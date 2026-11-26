"""Machine-readable client and detached local service lifecycle."""

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
CLIENT = build_opener(ProxyHandler({}))


def arguments(commands):
    for group in ("service", "research"):
        parser = commands.add_parser(group)
        parser.add_argument("--home", default=os.getenv("QUANT_CONTROL_HOME", "data/quant-control"))
        parser.add_argument("--database", default=os.getenv("QUANT_CONTROL_DATABASE"))
        parser.add_argument("--port", type=int, default=os.getenv("QUANT_CONTROL_PORT", "8765"))
        actions = parser.add_subparsers(dest="command", required=True)
        if group == "service":
            for action in ("start", "serve"):
                start = actions.add_parser(action)
                start.add_argument("--source", default="data/binance/um/bars_5s")
                if action == "start":
                    start.add_argument("--build-ui", action="store_true")
            actions.add_parser("status")
            actions.add_parser("stop")
            actions.add_parser("pause")
            actions.add_parser("resume")
            limits = actions.add_parser("budgets")
            limits.add_argument("--cpu-limit", type=int)
            limits.add_argument("--memory-gib", type=float)
            limits.add_argument("--disk-gib", type=float)
        else:
            actions.add_parser("submit").add_argument("specification")
            actions.add_parser("queue")
            actions.add_parser("strategies")
            actions.add_parser("inbox").add_argument("--unreviewed", action="store_true")
            for name in ("status", "cancel", "retry", "results", "acknowledge", "priority", "audit", "verdict"):
                command = actions.add_parser(name)
                command.add_argument("id")
                if name == "priority":
                    command.add_argument("priority", type=int)
                elif name == "acknowledge":
                    command.add_argument("--revision", type=int)
                elif name == "audit":
                    command.add_argument("experiment_id")
                elif name == "verdict":
                    command.add_argument("verdict", choices=["promising", "rejected", "inconclusive"])
                    command.add_argument("--note", required=True)


def request(port, path, payload=None, *, timeout=120):
    data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
    call = Request(
        f"http://127.0.0.1:{port}/api{path}", data=data,
        headers={"X-Quant-Client": "control", "Content-Type": "application/json"},
    )
    try:
        with CLIENT.open(call, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            message = json.load(error)["error"]
        except (ValueError, KeyError):
            message = str(error)
        raise RuntimeError(message) from error


def _locations(args):
    home = Path(args.home).resolve()
    database = Path(args.database).resolve() if args.database else home / "research.sqlite"
    if not 1024 <= args.port <= 65535:
        raise ValueError("Choose a local port from 1024 to 65535")
    return home, database


def _state(args, *, timeout=2):
    try:
        return request(args.port, "/service", timeout=timeout)
    except (URLError, TimeoutError):
        return None


def _check_database(state, database):
    if state is not None and Path(state["database"]).resolve() != database:
        raise RuntimeError("The port belongs to a service with a different registry")


def _service(args):
    home, database = _locations(args)
    if args.command == "serve":
        from .control_service import serve
        serve(home, database, Path(args.source).resolve(), args.port)
        return {"running": False, "database": str(database)}
    state = _state(args)
    _check_database(state, database)
    if args.command == "start":
        if args.build_ui:
            npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
            if npm is None:
                raise RuntimeError("Node.js with npm is required to build the control center")
            for arguments in (("ci",), ("run", "build")):
                subprocess.run([npm, *arguments], cwd=ROOT / "control-ui", stdout=sys.stderr, check=True)
        if state is not None:
            if state["stopping"] or state["worker_error"] or not state["ready"]:
                raise RuntimeError("The service on this port is stopping or unavailable")
            return state | {"url": f"http://127.0.0.1:{args.port}/", "already_running": True}
        home.mkdir(parents=True, exist_ok=True)
        log_path = home / "service.log"
        command = [
            sys.executable, "-m", "quant", "service", "--home", str(home), "--database", str(database),
            "--port", str(args.port), "serve", "--source", str(Path(args.source).resolve()),
        ]
        options = (
            {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )
        with log_path.open("ab") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        end = monotonic() + 30
        while monotonic() < end and process.poll() is None:
            state = _state(args, timeout=1)
            _check_database(state, database)
            if state and state["worker_error"]:
                raise RuntimeError(f"Dispatcher could not start. See {log_path}")
            if state and state["ready"]:
                return state | {"url": f"http://127.0.0.1:{args.port}/"}
            sleep(0.15)
        raise RuntimeError(f"Service failed to become ready. See {log_path}")
    if args.command == "status":
        return state or {"ready": False, "running": False, "database": str(database)}
    if args.command == "stop":
        return request(args.port, "/service/stop", {}) if state else {"running": False, "stopping": False}
    if args.command in {"pause", "resume"}:
        return request(args.port, "/service/settings", {"paused": args.command == "pause"})
    values = {"cpu_limit": args.cpu_limit, "memory_budget_gib": args.memory_gib, "disk_budget_gib": args.disk_gib}
    return request(args.port, "/service/settings", {k: v for k, v in values.items() if v is not None})


def _research(args):
    _, database = _locations(args)
    state = _state(args)
    _check_database(state, database)
    if state is None:
        raise RuntimeError("Start the local research service before using the research CLI")
    command = args.command
    if command == "submit":
        path = Path(args.specification)
        return request(args.port, "/hypotheses", json.loads(path.read_text(encoding="utf-8-sig")))
    if command == "strategies":
        return request(args.port, "/strategies")
    if command == "queue":
        return request(args.port, "/queue")
    if command == "inbox":
        return request(args.port, "/inbox" + ("?unreviewed=true" if args.unreviewed else ""))
    from .control_store import identifier, sequence

    base = f"/hypotheses/{identifier(sequence(args.id))}"
    if command in {"status", "results"}:
        return request(args.port, base if command == "status" else f"{base}/results")
    values = {}
    if command == "acknowledge":
        revision = args.revision if args.revision is not None else request(args.port, base)["revision"]
        values["revision"] = revision
    elif command == "priority":
        values["priority"] = args.priority
    elif command == "audit":
        values["experiment_id"] = args.experiment_id
    elif command == "verdict":
        values.update(verdict=args.verdict, note=args.note)
    return request(args.port, f"{base}/{command}", values)


def main(args):
    try:
        result = _service(args) if args.action == "service" else _research(args)
        print(json.dumps(result, separators=(",", ":"), ensure_ascii=True))
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"error": str(error)}, separators=(",", ":")))
        return 1
    return 0
