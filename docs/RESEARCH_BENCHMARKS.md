# Phase 4 research execution benchmarks

Measurements use the audited Liquid 50 development data on Windows with 12 physical cores, 16 logical CPUs visible to the process, and an enabled CPython GIL. Arrow and Polars both reported 16 library threads. Each table identifies the workload, preparation and report mode. OS page-cache state was uncontrolled. A fresh application cache means no committed prepared artifacts for that selection. A warm application cache reuses a published prepared artifact.

```bash
uv run python -m simulator.benchmark --quick --out bench-data/phase4-baseline.json
uv run python -m examples.research_benchmark --tier profile --out bench-data/profile
uv run python -m examples.research_benchmark --tier grid --out bench-data/grid
uv run python -m examples.research_benchmark --tier larger --out bench-data/larger
uv run python -m examples.research_benchmark --tier diagnostics --out bench-data/diagnostics
uv run python -m examples.research_benchmark --tier full --full-cache --out bench-data/full
```

The benchmark writes each completed measurement to JSON locally. The full-cache tier needs about 18.2 GB of free space for its prepared input. It also runs a direct source pass. Use `--cache-only` to repeat prepared-cache construction and replay separately.

## Baseline and bottlenecks

- The legacy one-day engine profile on June 10, 2025 handled 570,214 observed bars and 864,000 scheduled symbol-time cells. Its lightweight native loop took 0.0114 s, while the whole direct replay took 1.383 s. EMA preparation in a separate pass took 0.240 s. Four independent replays took 4.484 s sequentially and 3.048 s with four threads. Investigation shifted to sharing preparation because every replay still scanned its own data.
- On June 10, 2026, projecting the Parquet fields took 0.692 s for 863,255 observed bars. Legacy aligned and sorted Arrow paths took 1.183 and 1.352 s respectively. The small BTC in-memory ZSTD codec test reached 229 MB/s; it isolates decompression from Arrow conversion and market preparation.
- In the interpreter calibration, 512 Python integer tasks took 0.546, 0.557, 0.504 and 0.524 s at one, two, four and eight threads and used about one core. Dispatching 512 no-op tasks took 1.1 ms sequentially and 14.6 ms at four threads. This supports interpreter and scheduling contention as reasons for the previous small EMA threading result. It does not directly time GIL waits.
- Hashing 1 GiB of repeated in-memory buffers took 1.455 s at one thread and 0.184 s at eight. Copying the same volume took 0.863 s at one thread and 0.297 s at eight. This is a process-level copy calibration. Hardware storage throughput and cache locality vary by tier.
- The first 1,000-candidate implementation spent 11.82 of 21.99 s finalizing one fsynced report per candidate. Consolidated atomic wave output and reuse of feed setup target this overhead. Actual trading simulations and lightweight zero-notional infrastructure runs are tracked separately.

## Batch scaling

Report rows for worker counts 1, 2, 4, 8, 12 and 16 come from the day grid. Counts use successfully completed reports per full measured wall second. Candidate-cells count scheduled symbol-time cells supplied to each successful native advance or diagnostic evaluation. Observed bars are the source cells with traded or confirmed-empty coverage, counted per input scan. Strategy-bar rates use per-candidate scheduled cells; the underlying source may be shared. Worker stage seconds sum over concurrent workers. Wall seconds include finalization. The sampler captures RSS at 25 ms intervals, including native code and mapped page residency. OS process I/O counters can omit some memory-mapped reads.

Initial matched active workload on June 10, 2026, using the same ten configurations and summary metrics. Both runs recorded 924 fills.

| Workload | Wall s | Experiments/s | Peak RSS |
|---|---:|---:|---:|
| Sequential, independent source scans | 22.283 | 0.449 | 0.54 GB |
| Shared block-major preparation, four workers | 2.584 | 3.87 | 0.51 GB |

The shared run is 8.62× faster end to end. The high-turnover comparison produced identical fill counts with both modes. Summary took 1.393 s and standard logging took 4.791 s for two wave candidates with the same prepared data and features. Native worker time was approximately 0.31 s summed across both candidates in each run.

The final grid used consolidated wave reports. The one-trial zero-notional run took 3.156 s with a fresh application cache and 0.520 s with a warm one.

| Workers | 10 warm trials (s) | 100 warm trials (s) | 100 trials/s | Mean cores, 100 trials |
|---:|---:|---:|---:|---:|
| 1 | 1.005 | 2.080 | 48.1 | 1.10 |
| 2 | 0.633 | 1.537 | 65.1 | 1.55 |
| 4 | 0.644 | 1.142 | 87.6 | 1.82 |
| 8 | 0.639 | 1.047 | 95.5 | 2.37 |
| 12 | — | 1.158 | 86.4 | 2.19 |
| 16 | — | 1.139 | 87.8 | 2.25 |

For 100 trials, eight workers achieved 1.99× speedup and 24.8% parallel efficiency relative to one worker. Twelve physical and 16 logical workers were slower for this slice. Four workers completed 1,000 warm zero-notional trials in **8.539 s** at **117.1 trials/s** and **0.484 GB** sampled peak RSS. The workload advanced 864 million scheduled candidate-cells at 101.2 million per wall second. Wave finalization fell from 11.82 to 0.578 s compared with the first report implementation.

The final matched active trial batch took **19.521 s** with sequential independent reads and **2.374 s** with sharing and four workers, an **8.22×** improvement. Both produced 924 fills. In the final high-turnover comparison, both summary and standard produced 585,678 fills. Summary used 1.525 s and 131 KB of reports. Standard used 5.227 s and 99.5 MB of artifacts. Those runs preserve the same risk, fee and fill logic. The final grid and campaign ran while a separate full-development benchmark was active, so the numbers represent shared-machine execution.

| Longer sample | Trials | Scheduled cells per trial | Observed bars per source pass | Wall s | Fills |
|---|---:|---:|---:|---:|---:|
| June 10–16, 2026, zero notional | 1 | 6,048,000 | 6,040,148 | 6.257 | 0 |
| June 10–16, 2026, active EMA | 10 | 6,048,000 | 6,040,148 | 23.505 | 1,592,288 |
| June 10–16, 2026, zero-notional wave | 100 | 6,048,000 | 6,040,148 | 10.402 | 0 |
| June 2026, zero notional | 1 | 25,920,000 | 21,856,579 | 18.833 | 0 |
| June 2026, active EMA | 10 | 25,920,000 | 21,856,579 | 87.386 | 5,642,112 |

The shipped path shares one preparation pass per wave, with independent portfolio states. Independent Python process workers remain available in the generic optimizer. The benchmark compares independent decoding with the block-major path, and measures whole-wave threading on identical data. A dedicated C++ thread pool, separate symbol-major cache and OS shared-memory allocations were not benchmarked.

## Diagnostic workload

One hundred predeclared candidates combined trailing momentum z-scores and aggressive-volume imbalance into scores. Each candidate computed rank IC, matched forward quantiles, threshold events, score turnover and monthly coverage. The one-worker run took **197.918 s** and the four-worker run took **100.726 s**, or **0.505 and 0.993 experiments/s** respectively. These are descriptive diagnostics, with 86.4 million scheduled candidate-cells per pass. Both used one continuous market and label stream for the compatible wave. The benchmark overlapped the full-development run.

The threaded trace revealed that legacy averaging and variance warning suppression used process-global warning-filter contexts. Pearson IC now computes the same centered-product and norm ratio directly with paired masks, so sparse rows return NaN without changing global warning filters. An independent paired NumPy reference checks the threaded path. The diagnostic table records the observed run before this warning-path adjustment.

## Full development

The direct, single-trial infrastructure pass took **385.654 s** for **551,232,000 scheduled cells** and **438,350,668 observed bars**. Parquet decoding and slicing accounted for 82.721 s, dense alignment and mark preparation for 286.069 s, and the native loop and binding for 9.803 s. Peak RSS was 2.09 GB. These stages explain why parallelizing portfolio advancement alone cannot meet the full-period interactive target. The earlier 219.303 s historical run used a different benchmark session. It provides context alongside the measured direct pass.

The first prepared-cache attempt was interrupted before publication. The completed retry streamed writes with bounded source memory and mapped read windows. It used the same development range and zero-notional settings.

| Full-development path | Wall s | Peak RSS | Observed bars/s | Committed cache |
|---|---:|---:|---:|---:|
| Direct Parquet and marks | 385.654 | 2.09 GB | 1.14M | None |
| Build prepared cache and replay | 283.520 | 2.04 GB | 1.55M | 18.19 GB |
| Warm prepared cache replay | 30.161 | 0.86 GB | 14.53M | Reused |

Both prepared replays traversed the same 2,692 blocks and matched terminal accounting. The warm replay did zero Parquet and mark preparation. Its native/binding stage took 24.189 s and included faults when the engine first touched mapped pages. The warm lightweight replay met the 20–45 s target. The operating system page cache was uncontrolled, and the direct and prepared measurements used separate sessions. Native reports from the measured run and the raw timing JSON are retained beneath `bench-data/phase4-full-bounded`.

## Durable acceptance

The two-process campaign recorded eight interrupted attempts, then completed 100 screening and eight native jobs. All registered outcomes were retained. Screens used 20 unique EMA nodes across 100 candidates. Screening results and native reports agreed with their sequential comparisons, and the audit replay agreed with the required summary fields. The release regression suite passed 88 tests, including the original ingestion, SDK and portfolio accounting tests.
