"""Submit V2 trend follow-ups and optionally profile the detached worker using SSE."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import psutil

import quant as q
from examples.v2_research import campaign
from quant.control_cli import request
from research.phase5.review import wait_for_outcomes


def payload(source, promote=None):
    if promote:
        spec = promote["specification"]
        initial = campaign(source, spec["universe"], True)
        hypothesis, = initial["hypotheses"]
        strategy = spec["strategy"]
        hypothesis["candidates"] = [{"kind": "native", "parameters": spec["parameters"],
                                     "strategy": {key: strategy[key] for key in ("id", "version")}}]
        protocol = hypothesis["protocol"]
        protocol["costs"] = spec["costs"]
        protocol["seed"] = spec["seed"]
        protocol["evaluation"]["screen_job"] = promote["id"]
        return initial
    initial = campaign(source, None, False)
    initial["campaign"] = "V2 Liquid 50 / directional persistence"
    hypothesis, = initial["hypotheses"]
    hypothesis["definition"] += " Use fixed 150 USDT per opening order across Liquid 50."
    template = hypothesis["candidates"][0]
    hypothesis["candidates"] = [
        template | {"parameters": {"entry": threshold, "lookback_hours": hours, "allow_short": short, "size": 150}}
        for hours, threshold in ((48, .025), (96, .045)) for short in (False, True)
    ]
    return initial


async def monitor(port, ids, process):
    event = asyncio.create_task(wait_for_outcomes(port, set(ids)))
    peak = process.memory_info().rss
    while not event.done():
        await asyncio.wait({event}, timeout=.1)
        peak = max(peak, process.memory_info().rss)
    await event
    waves = {}
    details = {}
    for key in ids:
        details[key] = request(port, f"/hypotheses/{key}/results")
        for job in details[key]["experiments"]:
            batch = (job["outcome"] or {}).get("execution", {}).get("batch_id")
            if batch and batch not in waves:
                waves[batch] = request(port, f"/batches/{batch}")["profile"]
    return {"peak_rss_bytes": peak, "waves": waves, "details": details}


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--port", type=int, default=8791)
    args.add_argument("--out", required=True, type=Path)
    args.add_argument("--profile", action="store_true")
    args.add_argument("--promote", nargs=2, metavar=("HYPOTHESIS", "EXPERIMENT"))
    settings = args.parse_args()
    port, out = settings.port, settings.out
    service = request(port, "/service")
    promoted = None
    if settings.promote:
        hypothesis, job = settings.promote
        promoted = next(item for item in request(port, f"/hypotheses/{hypothesis}/results")["experiments"]
                        if item["id"] == job and item["status"] == "completed")
    data = payload(service["coverage"]["source"], promoted)
    out.mkdir(parents=True, exist_ok=True)
    (out / "specification.json").write_text(json.dumps(data, indent=2), encoding="utf8")
    begin = perf_counter()
    r = request(port, "/hypotheses", data)
    r["submission_seconds"] = perf_counter() - begin
    (out / "receipt.json").write_text(json.dumps(r, indent=2), encoding="utf8")
    print(json.dumps(r), flush=True)
    if settings.profile:
        report = asyncio.run(monitor(port, r["submission_ids"], psutil.Process(service["pid"])))
        (out / "profile.json").write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({k: v for k, v in report.items() if k != "details"}))


if __name__ == "__main__":
    main()
