"""Measure identical recorded backtest configurations through the batch entry point."""

import argparse
import cProfile
import json
import os
from pathlib import Path
import pstats
import time

import psutil

import quant as q
from quant.strategy import Runner, prepare
from simulator.native import OrderSettings

p = argparse.ArgumentParser()
p.add_argument("--source", required=True)
p.add_argument("--out", type=Path, required=True)
p.add_argument("--start", default="2026-08-14")
p.add_argument("--end", default="2026-08-14T04:00:00Z")
p.add_argument("--workers", type=int, default=4)
p.add_argument("--cpu-limit", type=int)
p.add_argument("--memory-gib", type=float, default=2)
p.add_argument("--cadence", type=int, default=6)
p.add_argument("--cache", type=Path)
p.add_argument("--block-steps", type=int, default=4096)
p.add_argument("--profile", action="store_true")
p.add_argument("--version", choices=("1.0.0", "2.0.0"), default="1.0.0")
p.add_argument("--candidates", type=int, default=4)
p.add_argument("--logging", default="summary")
args = p.parse_args()
sel = q.data.select(q.data.open(args.source), start=args.start, end=args.end)
variants = [("velocity", 1), ("residual", 1), ("flow", 1), ("residual", -1)] if args.version == "1.0.0" else [
    ("velocity", 1), ("residual_flow", 1), ("residual_flow", -1), ("residual", -1)]
cases = []
for i in range(args.candidates):
    method, side = variants[i % len(variants)]
    marker = {"id": "shock_inventory_recovery", "version": args.version}
    resolved = prepare(marker, {"mechanism": method, "side": side, "cadence": args.cadence,
                               "dynamic": args.version == "2.0.0" and method == "residual_flow",
                               "depth": .009 + .0001 * (i // len(variants))}, rebalance_ticks=1)
    runner = Runner(resolved, sel.symbols, 716)
    cases.append(q.simulator.Candidate(f"candidate_{i}", runner, resolved[1].features,
        config=OrderSettings(), fields=runner.fields, feedback=True, warmup_bars=361))
args.out.mkdir(parents=True, exist_ok=True)
proc = psutil.Process(os.getpid())
cpu0 = sum(proc.cpu_times()[:2])
start = time.perf_counter()
profiler = cProfile.Profile()
if args.profile:
    profiler.enable()
result = q.simulator.run_batch(sel, cases, out=args.out/"runs", workers=args.workers, cpu_limit=args.cpu_limit or args.workers,
                              logging=args.logging, block_steps=args.block_steps, cache_directory=args.cache,
                              memory_budget_gib=args.memory_gib)
if args.profile:
    profiler.disable()
    with (args.out/"profile.txt").open("w",encoding="utf8") as file:
        pstats.Stats(profiler, stream=file).sort_stats("cumulative").print_stats(45)
elapsed = time.perf_counter() - start
runs = {key:item.report for key,item in result.results.items()}
assert not result.failures, result.failures
res = result.profile | {"elapsed":elapsed,"process_cpu_seconds":sum(proc.cpu_times()[:2])-cpu0,
    "peak_working_set":getattr(proc.memory_info(),"peak_wset",proc.memory_info().rss),
    "candidates":len(cases),"cadence_ticks":args.cadence,
    "shared_observed_bars_per_second":result.profile["observed_bars"]/elapsed,
    "strategy_version":args.version,"cache_requested":args.cache is not None,
    "cpu_limit":args.cpu_limit or args.workers}
(args.out/"benchmark.json").write_text(json.dumps(res,indent=2),encoding="utf8")
(args.out/"reports.json").write_text(json.dumps(runs,indent=2),encoding="utf8")
print(res,flush=True)
