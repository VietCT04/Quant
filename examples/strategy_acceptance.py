"""Fixed development-only acceptance through the persistent strategy queue."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import aiohttp
import numpy as np
import polars as pl

from quant import data
from examples.control_acceptance import wait_events


def campaign(source):
    selection = data.select(data.open(source), start="2026-06-10", end="2026-06-10T00:30:00Z",
                            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    protocol = {
        "start": selection.start, "end": selection.end, "universe": selection.symbols,
        "dataset_fingerprint": data.fingerprint(selection), "seed": 2026,
        "evaluation": {"purpose": "Infrastructure verification. No alpha promotion.", "method": "pearson", "horizon": 6},
        "costs": {"rebalance_ticks": 1},
    }
    names = {
        "ema_crossover": [{"notional": 200}, {"notional": 300}, {"notional": 400}],
        "shock_reversal": [{"threshold": .00015}],
        "flow_imbalance": [{"notional": 500}, {"notional": 600}],
        "cross_sectional_momentum": [{}],
        "stateful_scaling": [{}],
    }
    hypotheses = []
    for kind in ("native", "screen"):
        for name, grid in names.items():
            hypotheses.append({
                "key": f"{name}-{kind}", "name": f"{name} / {kind}", "family": "Interface verification",
                "mechanism": f"Execute the predeclared {name} reference module.",
                "definition": f"Replay trusted {name} 1.0.0 with its frozen graph.",
                "prediction": "The registered configuration completes and its observations are reproducible.",
                "stage": "screening" if kind == "screen" else "simulation", "protocol": protocol,
                "candidates": [{"kind": kind, "strategy": {"id": name, "version": "1.0.0"}, "parameters": params}
                               for params in grid],
            })
    return {"campaign": "General strategy interface acceptance", "hypotheses": hypotheses}


async def verify(port, out):
    base = f"http://127.0.0.1:{port}/api"
    headers = {"X-Quant-Client": "control", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=180)) as client:
        async def get(path):
            async with client.get(base + path) as response:
                assert response.status == 200, await response.text()
                return await response.json()

        async def post(path, payload, status=202):
            async with client.post(base + path, json=payload) as response:
                assert response.status == status, await response.text()
                return await response.json()

        async def complete(stream, keys):
            snapshot = await get("/snapshot")
            finished = set()
            def receive(rows):
                for row in rows:
                    if row["id"] not in keys:
                        continue
                    if row["state"] in {"failed", "blocked", "cancelled"}:
                        raise AssertionError(row)
                    if row["state"] == "completed":
                        finished.add(row["id"])
                return finished >= keys
            if not receive(snapshot["hypotheses"]):
                await wait_events(stream, lambda delta: receive(delta["hypotheses"]))

        server = await get("/service")
        catalog = await get("/strategies")
        assert len(catalog) >= 5
        payload = campaign(server["coverage"]["source"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "campaign.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        cursor = (await get("/snapshot"))["cursor"]
        async with client.get(base + f"/events?after={cursor}") as stream:
            start = perf_counter()
            accepted = await post("/hypotheses", payload)
            submission_ms = (perf_counter() - start) * 1000
            assert await post("/hypotheses", payload) == accepted
            keys = accepted["submission_ids"]
            await complete(stream, set(keys))
        details = [await get(f"/hypotheses/{key}/results") for key in keys]
        runs = [trial for h in details for trial in h["experiments"]]
        assert len(runs) == 16
        batches = [await get(f"/batches/{batch}") for batch in sorted({
            trial["outcome"]["execution"]["batch_id"] for trial in runs
        })]
        assert len(batches) == 2
        profiles = [batch["profile"] for batch in batches]
        for batch in batches:
            profile = batch["profile"]
            cells = profile.get("scheduled_cells", profile.get("market_cells"))
            assert cells == 1440 and len(batch["trials"]) == 8
            assert profile.get("native_candidate_cells", profile.get("candidate_cells")) == cells * 8
            assert profile["feature_nodes"] < 12
        receipt = {}
        positions = {}
        cursor = (await get("/snapshot"))["cursor"]
        async with client.get(base + f"/events?after={cursor}") as stream:
            for h in details[:5]:
                original = h["experiments"][0]
                job = original["id"]
                replay = await post(f"/hypotheses/{h['id']}/audit", {"experiment_id": job})
                key, = replay["submission_ids"]
                receipt[job] = (h["id"], key)
            await complete(stream, {value[1] for value in receipt.values()})
        for job, (hypothesis, audit) in receipt.items():
            base_view = f"/explorer/{job}"
            meta = await get(base_view + "/meta")
            assert meta["detailed_job"] and meta["replay"]["hypothesis_id"] == audit
            trial, = (await get(f"/hypotheses/{audit}/results"))["experiments"]
            assert trial["outcome"]["execution"]["exact_replay_of"] == job
            assert trial["id"] == meta["detailed_job"]
            trace = next(Path(path) for path in trial["outcome"]["artifacts"] if Path(path).stem == "strategy_trace")
            active = next((row["symbol"] for row in trial["outcome"]["metrics"]["per_symbol"] if row["fills"]), None)
            assert active, (hypothesis, "The fixture produced no fills")
            result = await get(base_view + f"/features?tf=5s&symbol={active}")
            descriptors = result["features"]
            assert descriptors and all(p["provenance"]["implementation_hash"] == trial["specification"]["strategy"]["implementation_hash"] for p in descriptors)
            table = pl.read_parquet(trace).filter(pl.col("symbol") == meta["symbols"].index(active))
            for descriptor in descriptors:
                field = descriptor["name"]
                values = result["series"][field]
                np.testing.assert_allclose(
                    [np.nan if row[1] is None else row[1] for row in values],
                    table[field].to_numpy(), equal_nan=True,
                )
            ledger = await get(base_view + f"/positions?symbol={active}")
            fills = await get(base_view + f"/fills?symbol={active}")
            if meta["strategy"]["id"] in {"shock_reversal", "stateful_scaling"}:
                reason_rows = [(await get(base_view + f"/fills?symbol={symbol}&limit=500"))["rows"]
                               for symbol in meta["symbols"]]
            else:
                reason_rows = [fills["rows"]]
            positions[hypothesis] = {
                "audit": audit, "job": job, "strategy": meta["strategy"]["id"],
                "plots": list(result["series"]), "symbol": active, "positions": ledger["total"], "fills": fills["total"],
                "reasons": sorted({row["decision_rationale"] for records in reason_rows for row in records if "decision_rationale" in row}),
            }
        assert all(item["fills"] for item in positions.values())
        assert next(item for item in positions.values() if item["strategy"] == "shock_reversal")["reasons"]
        assert "Partial exit" in next(item for item in positions.values() if item["strategy"] == "stateful_scaling")["reasons"]
        inbox = await get("/inbox?unreviewed=true")
        assert set(keys) | {value[1] for value in receipt.values()} <= {item["id"] for item in inbox}
    evidence = {"submission_ms": submission_ms, "hypotheses": keys, "experiments": len(runs),
                "batch_profiles": profiles, "explorer": positions, "inbox": len(inbox)}
    (out / "acceptance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.port, args.out))


if __name__ == "__main__":
    main()
