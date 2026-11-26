# Five-second order research throughput

## Matched workload

Liquid 50, audited five-second bars, UTC 2026-08-14 through 2026-08-21, fixed four-candidate shock portfolio wave, explicit decisions every five seconds. Each run processed 6,048,323 observed market bars and 24,264,200 scheduled candidate cells, including pre-warm-up. Observed bars are counted once across the entire wave. All runs use the same V2 fees, funding, quantities, causality rules and final portfolio reporting. This is a throughput check with market data. The P&L is not a promotion result.

Windows, 12 physical cores and 16 logical CPUs, 12 configured CPU slots, four candidate workers, and a 2 GiB memory budget. Native compilation occurred before timed warm runs. The production figures below use wall time inside the normal scheduler's batch execution, including data, shared features, portfolio execution and final reporting. Receipt time is measured separately.

| Production run | Batch wall time | Shared observed bars/s |
| --- | ---: | ---: |
| Previous build of prepared market cache | 6.711 s | 0.90M |
| Previous warm prepared market cache A | 2.378 s | 2.54M |
| Previous warm prepared market cache B | 2.348 s | 2.58M |
| Optimized direct Parquet (batch 16) | 3.469 s | 1.74M |
| Optimized warm prepared market cache A (batch 17) | 1.141 s | 5.30M |
| Optimized warm prepared market cache B (batch 18) | 1.067 s | 5.67M |

The optimized direct and warm runs reproduced all four complete reports from the previous warm batch exactly. The optimized submissions returned receipts in 0.42–0.52 s. Evidence is saved under `bench-data/order-throughput/v4-queue/queue.json` and `bench-data/order-throughput/queue-check/queue.json`. A separate command-line warm run reached 4.50M bars/s with a 513 MiB peak working set. The earlier direct-Parquet run processed the same range at 1.32M bars/s. OS cache state was uncontrolled, and a fresh cache build was measured only in the previous implementation.

An earlier matched one-day run at the same five-second cadence took 81.38 s before optimization and 1.28 s in the native direct-Parquet iteration, with identical reports. The first native port reduced the four-hour candidate advancement stage from 8.28 s to 0.046 s before the remaining data and reporting work. On the longer window, compiled decisions with the reference SDK graph matched the fused feature path exactly. The patient strategy version also matched its Python reference on audited data.

The production six-month replay matched every metric and diagnostic field in the four original 30-second results. It processed 153,591,611 shared observed bars directly from Parquet in 86.17 s (1.78M bars/s), compared with 2,029.97 s (75.7k bars/s) in the archived batch. Other local activity and OS cache state were uncontrolled. Exact evidence and both batch profiles are saved under bench-data/order-throughput/queue-check/full.json and the V2 registry.

A 100-configuration five-second benchmark completed 16.21M candidate cells in 8.83 s across two waves with a 438 MiB peak working set. Many-candidate work is dominated by portfolio and reporting effort compared with the four-candidate wave. An independent 71-fill production probe attached its reference Python replay after portfolio metrics and execution diagnostics reconciled. The 117 Python tests passed, including raw fill equality on synthetic records in cached and uncached modes.


## Changes and safeguards

- Registered shock versions use a checksum-gated compiled decision loop. Candidate-local state and next-open execution stay with the existing native ledger. The public Python callback remains available.
- Compatible shock waves share a causal incremental graph and warm-up masks. A bounded prefetch stage shards features by symbol on spare CPU slots while candidate portfolios execute the preceding block. Timing counters for market reads, features and simulator steps therefore overlap. Audit replay continues to use the Python graph and order decisions and must reconcile with the original metrics before Explorer attachment. The compiled feature regression checks gaps, chunk boundaries, time prefixes, and serial versus parallel output.
- Projected scans use a single manifest lookup, bounded decoding and market read-ahead, and an ordered block layout. Reader parallelism uses spare slots from the CPU budget. Prepared market caches now support explicit order-mode projections and include the historical warm-up interval in the cache identity. Cache hits reuse stored funding and coverage. Small ledger reports group Parquet data once per simulation instead of scanning it for every symbol.
- Compiled summary runs keep small fill and funding ledgers in reserved memory and spill larger ones to the existing Parquet format. Both use the same lifecycle reconstruction. Detailed runs retain the existing artifacts. General Python order callbacks continue to use their prepared slices and native execution addresses. The measured 4M target applies to the compiled shock workload on a warm prepared cache. Other strategies, larger waves and uncached source reads have different costs.

Detailed old artifacts remain readable in Explorer. As before, changed runtime provenance makes an exact replay of an old summary explicitly incompatible with the current runtime. Running the recorded code revision remains the reproduction path for those summaries.

## Reproduce

Submit the selected order tranche through the Control Center with protocol evaluation.prepared_cache set to true. Run it against a compatible prepared cache and keep the CPU budget high enough for concurrent candidate and read workers.

For the batch entry point, use a separate output path for every invocation and reuse the prepared cache path.

```sh
uv run python -m examples.benchmark_order_throughput --source data/binance/um/bars_5s --end 2026-08-21 --out bench-data/throughput-build --cache bench-data/throughput-cache --workers 4 --cpu-limit 12 --cadence 1 --block-steps 8192
uv run python -m examples.benchmark_order_throughput --source data/binance/um/bars_5s --end 2026-08-21 --out bench-data/throughput-warm --cache bench-data/throughput-cache --workers 4 --cpu-limit 12 --cadence 1 --block-steps 8192
```

Set QUANT_ORDER_ENGINE=python to use the generic strategy callback. Set QUANT_SHOCK_FEATURES=python to use the reference SDK features with compiled order decisions. Exclude first-time native compilation when comparing steady-state throughput.
