"""Measure a six-month V2 wave and verify its recovery after an isolated worker interruption."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import aiohttp
import psutil

from examples.control_acceptance import wait_events
from examples.v2_research import campaign
from quant.control_cli import request
from research.v2.experiments import monitor


async def interrupt(port, identifier, cursor, process):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
        async with client.get(f"http://127.0.0.1:{port}/api/events?after={cursor}") as stream:
            stream.raise_for_status()
            await wait_events(stream, lambda d: any(
                row["id"] == identifier and row["state"] == "running" for row in d["hypotheses"]
            ))
            process.kill()
            await asyncio.to_thread(process.wait, 20)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--port", type=int, default=8792)
    args.add_argument("--out", type=Path, default=Path("data/research/v2/recovery"))
    options = args.parse_args()
    if (options.out / "worker" / "research.sqlite").exists():
        raise SystemExit("Choose a fresh recovery output path")
    options.out.mkdir(parents=True, exist_ok=True)
    local = [sys.executable, "-m", "quant", "service", "--home", str(options.out / "worker"),
             "--port", str(options.port)]
    port = options.port
    service = json.loads(subprocess.check_output([*local, "start"]))
    assert Path(service["database"]).parent == (options.out / "worker").resolve()
    request(port, "/service/settings", {"paused": True})
    try:
        payload = campaign(service["coverage"]["source"], ["BTCUSDT"], False)
        start = perf_counter()
        receipt = request(port, "/hypotheses", payload)
        delay = perf_counter() - start
        key, = receipt["submission_ids"]
        before = request(port, f"/hypotheses/{key}")
        assert before["state"] == "queued"
        cursor = request(port, "/snapshot")["cursor"]
        request(port, "/service/settings", {"paused": False})
        asyncio.run(interrupt(port, key, cursor, psutil.Process(service["pid"])))
        receipt2 = json.loads(subprocess.check_output([*local, "start"]))
        assert receipt2["pid"] != service["pid"]
        result = asyncio.run(monitor(port, [key], psutil.Process(receipt2["pid"])))
        detail = result["details"][key]
        assert detail["state"] == "completed" and len(detail["experiments"]) == 2
        assert len(request(port, "/inbox?unreviewed=true")) == 1
        for job in detail["experiments"]:
            assert job["status"] == "completed"
            assert len(job["attempts"]) == 2
            assert job["attempts"][0]["failure"]
            assert job["outcome"]["metrics"]["execution_model"] == "next_open_v2"
        result["submission_seconds"] = delay
        (options.out / "recovery.json").write_text(json.dumps(result, indent=2), encoding="utf8")
        print(json.dumps({k: v for k, v in result.items() if k != "details"}))
    finally:
        subprocess.run([*local, "stop"], check=False)


if __name__ == "__main__":
    main()
