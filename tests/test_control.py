"""Control center durability, dispatch, API event replay, and authorization boundaries."""

import asyncio
from contextlib import closing
import json
import socket
import subprocess
import sys

import aiohttp
import psutil
import pytest

from quant import data, evaluate, experiment
from quant.control_handlers import validate
from quant import control_store as store
from quant.validation import HOLDOUT_START
from test_research import market  # shared audited synthetic market fixture


def hypothesis(market, name, *, candidates=2, **extra):
    return {
        "name": name, "family": "fixture", "stage": "screening",
        "mechanism": "Fixture for research dispatch", "definition": "EMA(fast) - EMA(slow)",
        "prediction": "All scheduled trials finish",
        "protocol": {
            "start": market.start, "end": market.end, "universe": market.symbols,
            "dataset_fingerprint": data.fingerprint(market), "evaluation": {"horizon": 2},
        },
        "candidates": [
            {"kind": "screen", "parameters": {"fast": i + 1, "slow": 5}}
            for i in range(candidates)
        ],
        **extra,
    }


def test_submission_atomic_idempotent_and_holdout(market, tmp_path):
    db = tmp_path / "registry.sqlite"
    store.initialize(db)
    first = hypothesis(market, "Parent", key="first")
    second = hypothesis(market, "Child", key="second", depends_on=["first"])
    with pytest.raises(ValueError, match="Cyclic"):
        store.submit(db, {"hypotheses": [first | {"depends_on": ["second"]}, second]}, validate)
    assert store.snapshot(db)["hypotheses"] == []
    holdout = first | {"key": "holdout", "protocol": first["protocol"] | {"start": HOLDOUT_START, "end": HOLDOUT_START + 5000}}
    with pytest.raises(PermissionError):
        store.submit(db, {"hypotheses": [first, holdout]}, validate)
    assert store.snapshot(db)["cursor"] == 0
    payload = {"campaign": "Fixtures", "hypotheses": [second, first]}
    submitted = store.submit(db, payload, validate)
    assert store.submit(db, payload, validate) == submitted
    child, parent = submitted["submission_ids"]
    assert store.detail(db, child)["dependencies"] == [parent]
    assert store.detail(db, child)["state"] == "blocked"
    assert store.detail(db, parent)["total"] == 2
    assert len({event["id"] for event in store.updates(db, 0)["events"]}) == 6


async def _sse(stream, predicate):
    event = {}
    async with asyncio.timeout(20):
        async for raw in stream.content:
            line = raw.decode().strip()
            if not line:
                if predicate(event):
                    return event
                event = {}
            elif not line.startswith(":"):
                key, value = line.split(":", 1)
                event[key] = json.loads(value) if key == "data" else value.strip()
    raise AssertionError("Stream ended before the expected event")


@pytest.mark.asyncio
async def test_detached_service_wake_sse_reconnect_cancel_retry_inbox(market, tmp_path):
    with socket.socket() as check:
        check.bind(("127.0.0.1", 0))
        port = check.getsockname()[1]
    home = tmp_path / "control"
    cli = [sys.executable, "-m", "quant"]
    local = ["--home", str(home), "--port", str(port)]

    async def invoke(group, *args):
        result = await asyncio.to_thread(
            subprocess.run, [*cli, group, *local, *args],
            capture_output=True, text=True, timeout=35, check=True,
        )
        return json.loads(result.stdout)

    launch = await invoke("service", "start", "--source", str(market.dataset.path))
    process = psutil.Process(launch["pid"])
    headers = {"X-Quant-Client": "control", "Content-Type": "application/json"}
    base = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
            async def get(path):
                async with session.get(base + "/api" + path) as result:
                    assert result.status == 200, await result.text()
                    return await result.json()

            async def post(path, value, expected=200):
                async with session.post(base + "/api" + path, json=value) as result:
                    assert result.status == expected, await result.text()
                    return await result.json()

            snap = await get("/snapshot")
            assert snap["hypotheses"] == [] and launch["ready"]
            await post("/service/settings", {"paused": True, "cpu_limit": 1})
            with (home / "spec.json").open("w", encoding="utf-8") as spec:
                json.dump({"hypotheses": [hypothesis(market, "A"), hypothesis(market, "B")]}, spec)
            receipt = await invoke("research", "submit", str(home / "spec.json"))
            assert process.is_running()
            a, b = receipt["submission_ids"]
            await post(f"/hypotheses/{a}/cancel", {})
            await post(f"/hypotheses/{a}/retry", {})
            await post(f"/hypotheses/{b}/priority", {"priority": 4})
            assert (await get("/queue"))[0]["id"] == b
            assert (await get(f"/hypotheses/{a}"))["state"] == "queued"
            await post("/service/settings", {"paused": False})
            async with session.get(base + f"/api/events?after={snap['cursor']}") as stream:
                first = await _sse(stream, lambda frame: frame.get("event") == "update")
            assert any(event["type"] == "job.cancelled" for event in first["data"]["events"])
            recent = await get("/snapshot")
            if sum(item["state"] == "completed" for item in recent["hypotheses"]) < 2:
                async with session.get(base + "/api/events?after=0", headers={"Last-Event-ID": str(recent["cursor"])}) as stream:
                    delta = await _sse(stream, lambda frame: frame.get("event") == "update" and any(
                        h["state"] == "completed" for h in frame["data"]["hypotheses"]
                    ))
                assert int(delta["id"]) > recent["cursor"]
            results = await get(f"/hypotheses/{a}/results")
            batch_id = results["experiments"][0]["outcome"]["execution"]["batch_id"]
            batch = await get(f"/batches/{batch_id}")
            assert len(batch["trials"]) == 4
            assert batch["profile"]["workers"] == 1
            assert batch["profile"]["feature_nodes"] == 3
            assert batch["profile"]["candidate_cells"] == 4 * batch["profile"]["market_cells"]
            inbox = await get("/inbox?unreviewed=true")
            assert {item["id"] for item in inbox} == {a, b}
            await post(f"/hypotheses/{a}/acknowledge", {"revision": results["revision"]})
            assert [item["id"] for item in await get("/inbox?unreviewed=true")] == [b]
            await post(f"/hypotheses/{a}/verdict", {"verdict": "inconclusive", "note": "Infrastructure fixture"})
            assert {item["id"] for item in await get("/inbox?unreviewed=true")} == {a, b}
            await post(f"/hypotheses/{a}/acknowledge", {"revision": results["revision"]})
            assert {item["id"] for item in await get("/inbox?unreviewed=true")} == {a, b}

            cursor = (await get("/snapshot"))["cursor"]
            async with session.get(base + "/api/events?after=0", headers={"Last-Event-ID": str(cursor)}) as stream:
                await post(f"/hypotheses/{b}/priority", {"priority": 5})
                frame = await _sse(stream, lambda event: event.get("event") == "update")
                assert len(frame["data"]["events"]) == 1
                assert int(frame["id"]) > cursor
            async with session.get(base + "/api/events?after=2147483648") as stream:
                await _sse(stream, lambda event: event.get("event") == "reconcile")

            async with session.post(base + "/api/service/stop", json={}, headers={"Origin": "https://invalid.example"}) as forbidden:
                assert forbidden.status == 403
            await post("/service/stop", {}, 202)
        await asyncio.to_thread(process.wait, 10)
    finally:
        if process.is_running():
            process.kill()


def test_orphaned_attempt_recovers_without_losing_provenance(market, tmp_path):
    from quant import research

    db = tmp_path / "recovery.sqlite"
    store.initialize(db)
    key = store.submit(db, hypothesis(market, "Recover", candidates=1), validate)["submission_ids"][0]
    trial = store.detail(db, key, include_results=True)["experiments"][0]
    with closing(research._db(db)) as conn, conn:
        conn.execute("UPDATE experiments SET status='running' WHERE id=?", (trial["id"],))
        conn.execute("UPDATE research_jobs SET attempts=1 WHERE id=?", (trial["id"],))
        conn.execute("INSERT INTO research_attempts VALUES (?,?,?,NULL,NULL)", (trial["id"], 1, experiment._now()))
    research.run(db, {"screen": lambda jobs: {job["id"]: {"metrics": {"fixture": 1}} for job in jobs}})
    result = store.detail(db, key, include_results=True)
    assert result["state"] == "completed" and result["verdict"] is None
    rerun = result["experiments"][0]
    assert rerun["specification"] == trial["specification"]
    assert [row["failure"] for row in rerun["attempts"]] == [{"type": "InterruptedProcess"}, None]


def test_partial_screen_cancellation_leaves_shared_peer_unchanged(market):
    class Control:
        progressed = False

        def cancelled(self):
            return {"stop"} if self.progressed else set()

        def is_set(self):
            return False

        def progress(self, profile, through):
            self.progressed = True

    def score(values):
        return values["close"]

    stopped = evaluate.ScreenCandidate("stop", {}, score)
    kept = evaluate.ScreenCandidate("keep", {}, score)
    options = {"horizon": 2, "block_steps": 4, "workers": 1}
    shared = evaluate.screen(market, [stopped, kept], cancel=Control(), **options)
    expected = evaluate.screen(market, [kept], **options)
    assert shared["failures"] == {"stop": "cancelled"}
    assert shared["results"] == expected["results"]
