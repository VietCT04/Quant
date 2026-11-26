"""Loopback research service, shared sampler, and resumable event stream."""

import asyncio
from contextlib import closing, suppress
from functools import partial
import json
import logging
import os
import socket
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import quote

from aiohttp import web
import psutil

from marketdata.state import writer_lock

from . import control_handlers as handlers, control_store as store, experiment, requests, research
from .explorer import Explorer

LOG = logging.getLogger(__name__)
RECONCILE_SECONDS = 60
WEB_ROOT = Path(__file__).resolve().parents[1] / "control-ui" / "dist"


class Wave:
    """A trusted batch's live measurements and cooperative per-trial cancellation."""

    def __init__(self, service):
        self.service = service
        self.ids = frozenset()
        self.last_tick = 0.0
        self.started = monotonic()
        self.range = None
        self.cancel_version = -1
        self.cancelled_ids = frozenset()
        self.batch_id = None

    def start(self, jobs, selection):
        self.ids = frozenset(job["id"] for job in jobs)
        self.range = (selection.start, selection.end)
        self.progress({}, selection.start, force=True)

    def cancelled(self):
        version = self.service.cancel_version
        if self.cancel_version != version:
            with store.transaction(self.service.path) as db:
                flagged = db.execute("""
                    SELECT j.id FROM research_jobs j JOIN experiments e USING(id)
                    WHERE j.cancel_requested=1 AND e.status='running'
                """)
                self.cancelled_ids = frozenset(row[0] for row in flagged) & self.ids
            self.cancel_version = version
        return self.cancelled_ids

    def is_set(self):
        return bool(self.ids) and self.ids <= self.cancelled()

    def progress(self, profile, through, *, force=False):
        now = monotonic()
        if not force and now - self.last_tick < 0.5:
            return
        self.last_tick = now
        duration = max(now - self.started, 1e-6)
        self.service.publish_live({
            "phase": "processing" if profile else "preparing", "profile": dict(profile),
            "jobs": sorted(self.ids), "range": self.range, "through_ms": int(through),
            "bars_per_second": profile.get("observed_bars", 0) / duration if profile else None,
        })

    def finish(self, profile):
        duration = profile.get("wall_seconds", 0)
        record = dict(profile) | {
            "bars_per_second": profile.get("observed_bars", 0) / duration if duration else None,
        }
        self.batch_id = store.record_batch(self.service.path, sorted(self.ids), record)
        self.service.last_profile = record


class WakeProtocol(asyncio.DatagramProtocol):
    def __init__(self, service):
        self.service = service

    def datagram_received(self, data, address):
        self.service.wake.set()
        self.service.signal(durable=True)
        self.service.cancel_version += 1


class Service:
    def __init__(self, root, database, source, port, web_root):
        self.root, self.path, self.source, self.port = root, database, source, port
        self.web_root = web_root
        self.explorer = Explorer(database, root, source)
        self.wake, self.stop, self.ready = Event(), Event(), Event()
        self.settings_lock = Lock()
        self.hardware = research.hardware()
        self.settings = store.config(self.path)
        self.settings["cpu_limit"] = min(self.settings["cpu_limit"], self.hardware["usable_cpus"])
        self.started = monotonic()
        self.process = psutil.Process()
        self.process.cpu_percent()
        self.telemetry = {}
        self.live = {"phase": "starting"}
        self.last_profile = store.last_batch(self.path)
        self.cancel_version = 0
        self.worker_error = None
        self.journal_version = 0

    def signal(self, durable=False):
        if durable:
            self.journal_version += 1
        changed, self.changed = self.changed, asyncio.Event()
        changed.set()

    def publish_live(self, value):
        self.live = value
        self.loop.call_soon_threadsafe(self.signal)

    def changed_from_worker(self):
        self.ready.set()
        self.loop.call_soon_threadsafe(self.signal, True)

    def wait(self):
        self.wake.wait(RECONCILE_SECONDS)
        self.wake.clear()

    def dispatch(self):
        try:
            self.publish_live({"phase": "idle"})
            research.run(
                self.path, {kind: partial(self.execute, kind) for kind in handlers.KINDS},
                claim_size=128, stop=self.stop, wait=self.wait, batch_key=handlers.batch_key,
                paused=lambda: self.settings["paused"], on_change=self.changed_from_worker,
            )
        except Exception as error:
            LOG.exception("Dispatcher stopped")
            self.worker_error = str(error)
        finally:
            self.publish_live({"phase": "stopped"})

    def execute(self, kind, jobs):
        with self.settings_lock:
            budgets = self.settings | {"cpu_limit": min(self.settings["cpu_limit"], self.hardware["usable_cpus"])}
        self.publish_live({"phase": "preparing", "jobs": [job["id"] for job in jobs]})
        try:
            origins = {}
            if kind == "audit":
                for job in jobs:
                    _, origin = self.explorer.origin_for_job(job["id"])
                    if origin:
                        origins[job["id"]] = self.explorer.validate_replay(origin, job["specification"])
            results = handlers.execute(kind, jobs, source=self.source, root=self.root, budgets=budgets, control=Wave(self))
            for job, original in origins.items():
                if job in results:
                    self.explorer.check_replay_output(original, results[job])
            return results
        finally:
            self.publish_live({"phase": "idle"})

    def state(self):
        return {
            "pid": os.getpid(), "database": str(self.path), "ready": self.ready.is_set(), "running": True,
            "stopping": self.stop.is_set(), "worker_error": self.worker_error,
            "budgets": self.settings, "hardware": self.hardware,
            "worker": self.live, "last_batch": self.last_profile, "resources": self.telemetry, "coverage": self.coverage,
        }

    def update_settings(self, values):
        with self.settings_lock:
            if "cpu_limit" not in values and self.settings["cpu_limit"] > self.hardware["usable_cpus"]:
                values = values | {"cpu_limit": self.hardware["usable_cpus"]}
            self.settings = store.config(self.path, values, cpus=self.hardware["usable_cpus"])
            return self.settings

    def sample(self, inventory):
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self.root))
        if inventory:
            cache = self.root / "cache"
            total = 0
            for file in cache.rglob("*"):
                with suppress(OSError):
                    if file.is_file():
                        total += file.stat().st_size
            self.cache_bytes = total
        return {
            "sampled_at": experiment._now(), "cpu_percent": psutil.cpu_percent(interval=0.1),
            "process_cpu_percent": self.process.cpu_percent(), "ram_used_bytes": memory.total - memory.available,
            "ram_total_bytes": memory.total, "process_rss_bytes": self.process.memory_info().rss,
            "disk_used_bytes": disk.used, "disk_total_bytes": disk.total, "disk_free_bytes": disk.free,
            "uptime_seconds": int(monotonic() - self.started), "cache_bytes": self.cache_bytes,
        }

    async def sampler(self):
        self.cache_bytes = 0
        self.coverage = await asyncio.to_thread(self.load_coverage)
        index = 0
        while True:
            try:
                self.telemetry = await asyncio.to_thread(self.sample, index % 15 == 0)
            except (OSError, psutil.Error) as error:
                self.telemetry = {"error": str(error)}
                LOG.warning("Cannot sample resources: %s", error)
            reconcile = index % 30 == 0
            if reconcile:
                self.cancel_version += 1
            self.signal(durable=reconcile)
            index += 1
            await asyncio.sleep(2)

    def load_coverage(self):
        try:
            from . import data
            dataset = data.open(self.source)
            result = {"symbols": len(dataset._symbols(None)), "source": str(self.source), "interval_ms": dataset.interval_ms}
            report = dataset.meta / "universes" / "liquid50_report.json"
            if report.is_file():
                slots = json.loads(report.read_text(encoding="utf-8"))["coverage_slots"]
                planned = sum(slots.values())
                if planned:
                    result["planned_coverage"] = slots["covered"] / planned
            return result
        except (OSError, ValueError, KeyError) as error:
            return {"source": str(self.source), "error": str(error)}

    async def start(self, app):
        with store.transaction(self.path, write=True) as db:
            store.journal(db, "worker.started")
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Event()
        self.coverage = None
        transport, _ = await self.loop.create_datagram_endpoint(
            lambda: WakeProtocol(self), local_addr=("127.0.0.1", 0),
        )
        wake_file = Path(str(self.path) + ".wake")
        combined = wake_file.with_suffix(".wake.part")
        combined.write_text(str(transport.get_extra_info("sockname")[1]), encoding="ascii")
        combined.replace(wake_file)
        self.worker = Thread(target=self.dispatch, name="quant-dispatch")
        self.worker.start()
        sampler = asyncio.create_task(self.sampler())
        try:
            yield
        finally:
            self.stop.set()
            self.wake.set()
            self.signal()
            sampler.cancel()
            with suppress(asyncio.CancelledError):
                await sampler
            await asyncio.to_thread(self.worker.join)
            transport.close()
            wake_file.unlink(missing_ok=True)
            with store.transaction(self.path, write=True) as db:
                store.journal(db, "worker.stopped")

    async def stream(self, request):
        raw = request.headers.get("Last-Event-ID", request.query.get("after", "0"))
        try:
            cursor = int(raw)
        except ValueError as error:
            raise ValueError("Invalid event cursor") from error
        if not 0 <= cursor < 2**63:
            raise ValueError("Invalid event cursor")
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream;charset=utf-8", "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        async def send(event, payload, event_id=None):
            prefix = f"id: {event_id}\n" if event_id is not None else ""
            frame = f"{prefix}event: {event}\ndata: {_json(payload)}\n\n"
            await asyncio.wait_for(response.write(frame.encode()), timeout=15)

        last_live = None
        seen_version = None
        try:
            # A different or restored registry may have an older high water mark.
            high = await asyncio.to_thread(store.cursor, self.path)
            if cursor > high:
                await send("reconcile", {}, high)
                cursor = high
            while not self.stop.is_set():
                changed = self.changed
                version = self.journal_version
                if version != seen_version:
                    delta = await asyncio.to_thread(store.updates, self.path, cursor)
                    if delta["events"]:
                        cursor = delta["events"][-1]["id"]
                        await send("update", delta, cursor)
                    if len(delta["events"]) == 500:
                        continue
                    seen_version = version
                live = _json(self.state())
                if live != last_live:
                    await send("telemetry", self.state())
                    last_live = live
                try:
                    await asyncio.wait_for(changed.wait(), timeout=15)
                except TimeoutError:
                    await asyncio.wait_for(response.write(b": heartbeat\n\n"), timeout=15)
        except (ConnectionError, TimeoutError):
            pass
        return response


def _json(obj):
    return json.dumps(experiment._plain(obj), allow_nan=False, separators=(",", ":"))


async def _body(request):
    if not request.can_read_body:
        return {}
    value = await request.json()
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object")
    return value


def build_app(service):
    origins = {f"http://127.0.0.1:{service.port}", f"http://localhost:{service.port}"}

    @web.middleware
    async def local_only(request, handler):
        if request.host not in {origin.removeprefix("http://") for origin in origins}:
            raise web.HTTPForbidden()
        origin = request.headers.get("Origin")
        if origin is not None and origin not in origins:
            raise web.HTTPForbidden()
        if request.method not in {"GET", "HEAD"} and (
            request.headers.get("X-Quant-Client") != "control" or request.content_type != "application/json"
        ):
            raise web.HTTPForbidden()
        try:
            response = await handler(request)
        except KeyError as error:
            return web.json_response({"error": str(error)}, status=404)
        except PermissionError as error:
            return web.json_response({"error": str(error)}, status=403)
        except (TypeError, ValueError, OverflowError) as error:
            return web.json_response({"error": str(error)}, status=400)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    app = web.Application(client_max_size=8 << 20, middlewares=[local_only])
    app.cleanup_ctx.append(service.start)

    async def snapshot(request):
        result = await asyncio.to_thread(store.snapshot, service.path)
        result["service"], result["coverage"] = service.state(), service.coverage
        return web.json_response(result, dumps=_json)

    async def submit(request):
        payload = await _body(request)
        result = await asyncio.to_thread(store.submit, service.path, payload, handlers.validate)
        service.wake.set()
        service.signal(durable=True)
        return web.json_response(result, status=202, dumps=_json)

    async def queue(request):
        return web.json_response((await asyncio.to_thread(store.snapshot, service.path))["hypotheses"], dumps=_json)

    async def details(request):
        result = await asyncio.to_thread(
            store.detail, service.path, request.match_info["key"],
            include_results=request.path.endswith("/results"),
        )
        return web.json_response(result, dumps=_json)

    async def inbox(request):
        value = await asyncio.to_thread(store.inbox, service.path, unreviewed=request.query.get("unreviewed") == "true")
        return web.json_response(value, dumps=_json)

    async def action(request):
        payload = await _body(request)
        keys = [request.match_info["key"]] if "key" in request.match_info else payload["ids"]
        operation = request.match_info["operation"]
        result = await asyncio.to_thread(store.action, service.path, keys, operation, payload)
        if operation in {"cancel", "retry"}:
            service.cancel_version += 1
        service.wake.set()
        service.signal(durable=True)
        return web.json_response(result, dumps=_json)

    async def audit(request):
        key = request.match_info["key"]
        payload = await _body(request)
        result = await asyncio.to_thread(service.explorer.replay, key, payload["experiment_id"])
        service.wake.set()
        service.signal(durable=True)
        return web.json_response(result, status=202, dumps=_json)

    async def capabilities(request):
        if request.method == "POST":
            req = requests.Request(**await _body(request))
            value = await asyncio.to_thread(requests.submit, service.path, req)
            service.signal(durable=True)
            return web.json_response({"id": value}, status=201)
        value = await asyncio.to_thread(requests.search, service.path)
        return web.json_response(value, dumps=_json)

    async def capability_review(request):
        body = await _body(request)
        key = request.match_info["key"]
        await asyncio.to_thread(requests.transition, service.path, key, **body)
        service.signal(durable=True)
        return web.json_response({"id": key})

    async def resources(request):
        return web.json_response(service.state() | {"coverage": service.coverage}, dumps=_json)

    async def settings(request):
        value = await asyncio.to_thread(service.update_settings, await _body(request))
        service.wake.set()
        service.signal(durable=True)
        return web.json_response(value, dumps=_json)

    async def stop(request):
        service.stop.set()
        service.wake.set()
        service.signal(durable=True)
        return web.json_response({"stopping": True}, status=202)

    async def batch(request):
        value = await asyncio.to_thread(store.batch, service.path, int(request.match_info["id"]))
        return web.json_response(value, dumps=_json)

    async def artifact(request):
        key = request.match_info["job"]
        index = int(request.match_info["index"])
        record = await asyncio.to_thread(experiment.reproduce, service.path, key)
        paths = (record["outcome"] or {}).get("artifacts", [])
        if not 0 <= index < len(paths):
            raise web.HTTPNotFound()
        base = service.root / "artifacts"
        path = Path(paths[index]).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(path.name),
            "Content-Type": "application/octet-stream",
        })

    async def index(request):
        page = service.web_root / "index.html"
        if page.is_file():
            return web.FileResponse(page, headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
            })
        return web.Response(text="Build the control center with npm ci and npm run build in control-ui. The local API is running.", status=503)

    explorer_slots = asyncio.Semaphore(2)

    async def explore(request):
        async with explorer_slots:
            result = await asyncio.to_thread(
                service.explorer.request, request.match_info["job"], request.match_info["resource"], request.query,
            )
        if isinstance(result, str):
            return web.Response(text=result, content_type="text/csv", headers={
                "Content-Disposition": "attachment; filename=positions.csv",
            })
        return web.json_response(result, dumps=_json)

    app.add_routes([
        web.get("/", index), web.get("/api/snapshot", snapshot),
        web.get(r"/api/explorer/{job:[a-f0-9]{32}}/{resource}", explore),
        web.get("/api/queue", queue), web.post("/api/hypotheses", submit), web.post("/api/campaigns", submit),
        web.get("/api/hypotheses/{key}", details), web.get("/api/hypotheses/{key}/results", details),
        web.post("/api/hypotheses/{key}/audit", audit),
        web.post("/api/hypotheses/{key}/{operation}", action), web.post("/api/queue/{operation}", action),
        web.get("/api/inbox", inbox), web.get("/api/events", service.stream),
        web.get("/api/requests", capabilities), web.post("/api/requests", capabilities),
        web.post("/api/requests/{key}/review", capability_review),
        web.get("/api/resources", resources), web.get("/api/service", resources),
        web.get(r"/api/batches/{id:\d+}", batch),
        web.post("/api/service/settings", settings), web.post("/api/service/stop", stop),
        web.get(r"/api/artifacts/{job:[a-f0-9]{32}}/{index:\d+}", artifact),
    ])
    if (service.web_root / "assets").is_dir():
        app.router.add_static("/assets", service.web_root / "assets")
    return app


async def _serve(service):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as address:
        if os.name != "nt":
            address.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        address.bind(("127.0.0.1", service.port))
        runner = web.AppRunner(build_app(service), shutdown_timeout=4)
        await runner.setup()
        try:
            site = web.SockSite(runner, address)
            await site.start()
            await asyncio.to_thread(service.stop.wait)
        finally:
            await runner.cleanup()


def serve(root, database, source, port, web_root=WEB_ROOT):
    root.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    with writer_lock(database.parent / f".{database.name}.control"):
        store.initialize(database)
        asyncio.run(_serve(Service(root, database, source, port, web_root)))
