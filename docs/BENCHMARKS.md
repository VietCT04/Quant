# Phase 3 SDK benchmarks

Measured locally on Windows 11 with 16 logical CPUs against the audited Liquid 50 Parquet. The workload reads close and volume, calculates independent recursive EMA(60) and EMA(240), and counts finite differences. Bars are scheduled time-symbol cells, including unavailable and pre-listing cells. Source time includes projection, decoding, coverage and dense alignment. Feature time measures the two causal EMA streams. Neither run evaluates a strategy or reads the reserved period.

```bash
uv run python -m examples.sdk_benchmark --out bench-data/sdk-performance.json
uv run python -m examples.sdk_cache_benchmark --out bench-data/sdk-cache-new
uv run python -m examples.research_sdk all
```

| Development slice (UTC) | Scheduled cells | Valid features | First read + feature (s) | First wall (s) | Repeat wall (s) | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT, Jan 1, 2025 | 17,280 | 17,280 | 0.125 + 0.002 | 0.128 | 0.095 | 350 MB |
| Liquid 50, Jun 10, 2026 | 864,000 | 863,255 | 0.891 + 0.160 | 1.055 | 1.078 | 499 MB |
| Liquid 50, Jun 2026 | 25,920,000 | 21,856,579 | 5.657 + 4.252 | 9.994 | 10.030 | 1.24 GB |
| Liquid 50, full development | 551,232,000 | 438,350,668 | 117.988 + 89.794 | 209.749 | — | 1.53 GB |

The first implementation spent 303.60 s on EMA and 428.01 s end to end over full development. Profiling by stage motivated replacing per-block Polars series construction with a SciPy recursive filter and bounded state. The optimized full run reached 6.14M scheduled cells per feature second and 2.63M cells/s end to end. Feature time decreased 3.38×, and wall time decreased 2.04×. Decoding and alignment account for the largest remaining share on the full run. Counts were identical. Independent numerical and chunk tests cover the optimized recurrence. [SciPy linear filter definition](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.lfilter.html)

Filesystem page cache was uncontrolled. First and repeated scans above describe execution order, and no OS-cold or repeated full-development timing was measured. RSS is process resident memory sampled every 100 ms, including Python and library allocations.

## Reusable caches and parallel trials

A separate Liquid 50 day used a rolling 240-bar z-score and bounded on-disk decoded and feature caches. A fresh SDK-cache pass took 2.126 s. The warm SDK-cache pass took 0.368 s with five decoded and five feature hits and identical 840,840 valid scores. Files occupied 20.74 MB. The measured speedup was 5.78× in this workload. The OS page cache remained uncontrolled.

Sixteen independent EMA replays on a shared decoded block took 0.715, 0.743 and 0.790 s with one, two and four threads respectively. Counts matched. Parallelism did not accelerate this small workload. The benchmark scripts retain raw machine-readable results locally.

The end-to-end acceptance suite also ran all ten research examples on one development hour with five symbols. The native streaming target example wrote its equity, orders, fills, funding, target and positions Parquet tables and report. The native simulator's standalone core and larger replay benchmarks are in [BENCHMARK.md](../BENCHMARK.md).

The regression suite passed 80 tests, including the 25 prior data and simulator tests. Coverage.py reported 86% combined statement and branch coverage for `quant` using `coverage run --branch --source quant -m pytest -q`. Windows execution was verified. The suite emitted two statsmodels return-format deprecation notices and the KPSS test's expected p-value table-bound warning.
