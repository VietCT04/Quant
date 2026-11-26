"""Run the detached development campaign, interrupt one wave, and verify recovery."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import aiohttp
import psutil

from .control_campaign import campaign


async def wait_events(stream, predicate):
    frame = {}
    async for raw in stream.content:
        line = raw.decode().strip()
        if not line:
            if frame.get("event") == "update" and predicate(frame["data"]):
                return frame
            frame = {}
        elif not line.startswith(":"):
            name, value = line.split(":", 1)
            frame[name] = json.loads(value) if name == "data" else value.strip()
    raise RuntimeError("Event stream ended before expected lifecycle transition")


async def verify(args):
    args.out.mkdir(parents=True, exist_ok=True)
    spec = args.out / "hypotheses.json"
    if (args.out / "research.sqlite").exists():
        raise FileExistsError("Use a fresh acceptance output directory")
    spec.write_text(json.dumps(campaign(args.start, args.end), indent=2), encoding="utf-8")
    config = ["--home", str(args.out), "--port", str(args.port)]
    base = f"http://127.0.0.1:{args.port}"

    async def cli(group, *tail):
        cmd = [sys.executable, "-m", "quant", group, *config, *tail]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = await process.communicate()
        if process.returncode:
            raise RuntimeError((out + err).decode())
        return json.loads(out)

    started = await cli("service", "start")
    report = {"start": args.start, "end": args.end, "initial_pid": started["pid"]}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as client:
        async def get(path):
            async with client.get(base + "/api" + path) as response:
                response.raise_for_status()
                return await response.json()

        initial = await get("/snapshot")
        async with client.get(base + f"/api/events?after={initial['cursor']}") as stream:
            tick = perf_counter()
            accepted = await cli("research", "submit", str(spec))
            report["submit_cli_seconds"] = perf_counter() - tick
            after_submit = await get("/queue")
            report["after_submit"] = {item["id"]: item["state"] for item in after_submit}
            first = await wait_events(
                stream, lambda d: any(event["type"] == "job.started" for event in d["events"]),
            )
            assert any(item["state"] == "running" for item in after_submit)
            if args.interrupt:
                target = psutil.Process(started["pid"])
                target.kill()
                await asyncio.to_thread(target.wait, 10)
        if args.interrupt:
            recovered = await cli("service", "start")
            assert recovered["pid"] != started["pid"]
            report["recovered_pid"] = recovered["pid"]
        assert await cli("research", "submit", str(spec)) == accepted
        ids = set(accepted["submission_ids"])
        queued = await get("/queue")
        if not ids <= {item["id"] for item in queued if item["state"] == "completed"}:
            complete = {item["id"] for item in queued if item["state"] == "completed"}

            def collect(delta):
                complete.update(item["id"] for item in delta["hypotheses"] if item["state"] == "completed")
                return ids <= complete

            async with client.get(base + "/api/events", headers={"Last-Event-ID": first["id"]}) as stream:
                await wait_events(stream, collect)
        results = [await get(f"/hypotheses/{key}/results") for key in accepted["submission_ids"]]
        trials = [trial for item in results for trial in item["experiments"]]
        assert len(trials) == 108 and all(trial["status"] == "completed" for trial in trials)
        interrupted = sum(
            (attempt["failure"] or {}).get("type") == "InterruptedProcess"
            for trial in trials for attempt in trial["attempts"]
        )
        assert bool(interrupted) == args.interrupt
        wave_ids = {trial["outcome"]["execution"]["batch_id"] for trial in trials}
        waves = [await get(f"/batches/{batch_id}") for batch_id in sorted(wave_ids)]
        screen_wave, = [wave for wave in waves if len(wave["trials"]) == 100]
        profile = screen_wave["profile"]
        assert profile["candidate_cells"] == 100 * profile["market_cells"]
        assert profile["feature_nodes"] < 200 and profile["workers"] <= started["budgets"]["cpu_limit"]
        native = next(item for item in results if item["stage"] == "simulation")
        trial = native["experiments"][0]
        inbox = await cli("research", "inbox", "--unreviewed")
        assert len(inbox) == 6 and all(item["verdict"] is None for item in inbox)
        audit_id, = (await cli("research", "audit", native["id"], trial["id"]))["submission_ids"]
        async with client.get(base + f"/api/events?after={first['id']}") as stream:
            await wait_events(stream, lambda d: any(
                item["id"] == audit_id and item["state"] == "completed" for item in d["hypotheses"]
            ))
        audit, = (await get(f"/hypotheses/{audit_id}/results"))["experiments"]
        original, replay = trial["outcome"]["metrics"], audit["outcome"]["metrics"]
        for field in ("end", "risk", "net_pnl", "daily_marked_equity", "execution_costs"):
            assert original[field] == replay[field], field
        async with client.get(base + audit["artifact_urls"][0]) as response:
            assert response.status == 200 and await response.read()
        report.update({
            "submission": accepted, "trial_counts": {"screen": 100, "native": 8, "audit": 1},
            "interrupted_attempts": interrupted, "batch_profiles": [wave["profile"] for wave in waves],
            "audit_id": audit_id, "audit_summary_matches": True, "inbox_count_before_audit": len(inbox),
            "idempotent_submission": True, "source": "audited Liquid 50 development data",
        })
    (args.out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--start", default="2026-06-10")
    parser.add_argument("--end", default="2026-06-10T03:00:00Z")
    parser.add_argument("--interrupt", action="store_true")
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
