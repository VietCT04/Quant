"""Benchmark prepared data, native portfolio waves, and interpreter/native parallelism."""

import argparse
from pathlib import Path
from threading import Event, Thread
from time import perf_counter

import numpy as np
import psutil

import quant as q
from marketdata.model import write_json
from simulator.native import Settings, library


def _candidate(index, mode, symbols):
    config = Settings(rebalance_ticks=1 if mode == "turnover" else 120)
    if mode in {"zero", "active"}:
        return q.simulator.Candidate(
            f"p{index}",
            config=config,
            allocation=tuple([0.0 if mode == "zero" else 100.0 + index] * len(symbols)),
        )
    span = 8 + index % 16
    fast = q.features.expression("quant.ts.ema", "close", span=span, units="USDT/base")
    slow = q.features.expression(
        "quant.ts.ema", "close", span=80 + (index // 8) % 8, units="USDT/base"
    )

    def decisions(block):
        direction = np.sign(block.values["fast"] - block.values["slow"])
        if mode == "turnover":
            step = (block.timestamps_ms // block.interval_ms) % 6
            direction *= np.where(step[:, None] < 3, 1, -1)
        return (100.0 + index % 11) * direction

    return q.simulator.Candidate(
        f"p{index}", decisions, {"fast": fast, "slow": slow}, config
    )


def _measure(operation):
    process = psutil.Process()

    def cpu():
        times = process.cpu_times()
        return times.user + times.system

    before = process.io_counters()
    peek = [0, 0]
    halt = Event()

    def sample():
        while not halt.wait(0.025):
            processes = [process, *process.children(recursive=True)]
            rss = sum(p.memory_info().rss for p in processes if p.is_running())
            peek[0] = max(peek[0], rss)
            peek[1] = max(peek[1], process.num_threads())

    timer = Thread(target=sample, daemon=True)
    clock = perf_counter()
    c = cpu()
    timer.start()
    try:
        value = operation()
    finally:
        halt.set()
        timer.join()
    seconds = perf_counter() - clock
    after = process.io_counters()
    return value, dict(
        seconds=seconds,
        cpu_core_equivalents=(cpu() - c) / seconds,
        peak_rss_bytes=max(peek[0], process.memory_info().rss),
        peak_threads=max(peek[1], process.num_threads()),
        process_read_bytes=after.read_bytes - before.read_bytes,
        process_write_bytes=after.write_bytes - before.write_bytes,
    )


def _overhead(workers):
    from concurrent.futures import ThreadPoolExecutor
    from hashlib import sha256

    count = 512

    def gil_bound(_):
        total = 0
        for i in range(5000):
            total += i * i % 123
        return total

    buf = np.arange(2 << 20, dtype="f8")
    raw = memoryview(buf).cast("B")
    results = {}
    for name, f in (
        ("python_integer_work", gil_bound),
        ("noop", lambda _: None),
        ("sha256_16mib", lambda _: sha256(raw).digest()),
        ("memcopy_16mib", lambda _: buf.copy()[0]),
    ):
        samples = []
        trials = count if name in {"noop", "python_integer_work"} else 64
        for n in workers:
            with ThreadPoolExecutor(n) as pool:
                _, timing = _measure(
                    lambda: (
                        list(pool.map(f, range(trials)))
                        if n > 1
                        else list(map(f, range(trials)))
                    )
                )
            samples.append(
                timing
                | {
                    "workers": n,
                    "tasks": trials,
                    "bytes_processed": len(raw) * trials
                    if name in {"sha256_16mib", "memcopy_16mib"}
                    else 0,
                }
            )
        results[name] = samples
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--tier",
        choices=("grid", "larger", "full", "profile", "diagnostics"),
        default="grid",
    )
    p.add_argument("--full-cache", action="store_true")
    p.add_argument(
        "--cache-only",
        action="store_true",
        help="Skip the direct pass in the full tier",
    )
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    q.performance.sharpe([0.01, -0.01])
    library()
    hw = q.research.hardware()
    report = {
        "hardware": hw,
        "scope": "development only; filesystem page cache uncontrolled",
        "runs": [],
    }
    filename = args.out / "benchmark.json"
    max_workers = hw["usable_cpus"]
    workers = sorted({n for n in (1, 2, 4, 8, 12, 16, max_workers) if n <= max_workers})
    ds = q.data.open()
    day = ("2026-06-10", "2026-06-11")
    week = ("2026-06-10", "2026-06-17")
    month = ("2026-06-01", "2026-07-01")
    full = ("2024-11-25", "2026-08-25")

    def run(
        label, range_, jobs, n, style="zero", logging="benchmark", cache=None, batch=128
    ):
        d = q.data.select(ds, start=range_[0], end=range_[1], fields=("close",))
        items = [_candidate(i, style, d.symbols) for i in range(jobs)]
        output = args.out / f"{label}-{len(report['runs']):03}"
        values, timing = _measure(
            lambda: q.simulator.run_batch(
                d,
                items,
                out=output,
                workers=n,
                logging=logging,
                cache_directory=cache,
                batch_size=batch,
                disk_budget_gib=26,
            )
        )
        if values.failures:
            raise RuntimeError(values.failures)
        row = dict(
            name=label,
            range=range_,
            jobs=jobs,
            requested_workers=n,
            style=style,
            logging=logging,
            completed=len(values.results),
            experiments_per_second=len(values.results) / timing["seconds"],
            candidate_cells_per_second=values.profile["native_candidate_cells"]
            / timing["seconds"],
            report_bytes=sum(
                f.stat().st_size for f in output.rglob("*") if f.is_file()
            ),
            fills=sum(r.report["fills"] for r in values.results.values()),
            **timing,
            **values.profile,
        )
        report["runs"].append(row)
        write_json(filename, report)
        print(label, jobs, n, round(row["seconds"], 3), flush=True)

    if args.tier == "grid":
        cache = args.out / "day-cache"
        run("fresh-cache", day, 1, 1, cache=cache)
        for n in workers:
            for jobs in (1, 10, 100):
                if n <= jobs:
                    run("warm-grid", day, jobs, n, cache=cache)
        run("one-thousand", day, 1000, min(4, max_workers), cache=cache)
        run("trading-serial-decode", day, 10, 1, "active", "summary", batch=1)
        run("trading-shared-decode", day, 10, min(4, max_workers), "active", "summary")
        run("ema", day, 10, min(4, max_workers), "ema", "summary", cache=cache)
        run("turnover", day, 2, min(2, max_workers), "turnover", "summary", cache=cache)
        run(
            "turnover", day, 2, min(2, max_workers), "turnover", "standard", cache=cache
        )
    elif args.tier == "larger":
        for range_, name in [(week, "week"), (month, "month")]:
            run(name, range_, 1, 1)
            run(name, range_, 10, min(4, max_workers), "ema", "summary")
        run("week-large-wave", week, 100, min(4, max_workers), batch=100)
    elif args.tier == "full":
        if not args.cache_only:
            run("full-source", full, 1, 1)
        if args.full_cache or args.cache_only:
            cache = args.out / "full-cache"
            run("full-fresh-cache", full, 1, 1, cache=cache)
            run("full-warm-cache", full, 1, 1, cache=cache)
    elif args.tier == "diagnostics":
        d = q.data.select(
            ds, start=day[0], end=day[1], fields=("close", "buy_volume", "sell_volume")
        )
        candidates = []
        for i in range(100):
            momentum = q.features.expression(
                "quant.ts.log_returns", "close", periods=3 + i % 8, units="return"
            )
            zscore = q.features.expression(
                "quant.ts.rolling_zscore", momentum, window=12 + i % 12, units="ratio"
            )
            flow = q.features.expression(
                "quant.microstructure.volume_imbalance",
                "buy_volume",
                "sell_volume",
                units="ratio",
            )

            def score(values, factor=i):
                return values["momentum"] + factor / 100 * values["flow"]

            candidates.append(
                q.evaluate.ScreenCandidate(
                    f"d{i}", {"momentum": zscore, "flow": flow}, score
                )
            )
        for n in (1, min(4, max_workers)):
            result, timing = _measure(
                lambda: q.evaluate.screen(
                    d, candidates, workers=n, groups=3, event_threshold=2.0
                )
            )
            if result["failures"]:
                raise RuntimeError(result["failures"])
            report["runs"].append(
                timing
                | result["profile"]
                | {
                    "name": "rolling-rank-event",
                    "requested_workers": n,
                    "experiments_per_second": len(result["results"])
                    / timing["seconds"],
                    "completed": len(result["results"]),
                    "scheduled_labels_many_candidates": True,
                }
            )
            write_json(filename, report)
            print("diagnostics", n, round(timing["seconds"], 3), flush=True)
    else:
        from simulator.benchmark import codec_test, reader_pass

        report["microbenchmarks"] = _overhead(
            [n for n in (1, 2, 4, 8) if n <= max_workers]
        )
        report["codec"] = codec_test(ds)
        report["reader"] = [
            reader_pass(ds, None, *day, m)
            for m in ("arrow_parquet_projection", "aligned_arrow", "sorted_arrow")
        ]
        write_json(filename, report)


if __name__ == "__main__":
    main()
