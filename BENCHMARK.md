# Binance USD-M pipeline benchmark

Benchmarked on Windows with 16 logical CPUs, 23.7 GiB of physical RAM and an NTFS workspace drive. Timings include downloads, checksums, ZIP decoding, aggregation, kline comparisons, and Parquet publication. Peak RSS was sampled every 0.5 seconds. Peak temporary bytes are the peak ZIP reservation, excluding existing files outside the benchmark run.

| Sample | ZIP payload | Aggregate records | Bars | Parquet | Wall time | Peak RSS | Peak ZIP reservation |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-11-20 BTC, ETH, LINK | 83.10 MB | 6,474,261 | 51,840 | 3.89 MB | 4.11 s | 582 MB | 83 MB |
| October 2026 BTC, LINK, XAU | 627.76 MB | 47,545,897 | 1,605,996 | 107.29 MB | 40.02 s | 1,230 MB | 605 MB |

Daily end-to-end throughput was 1.58 million aggregate records/s at 20.23 MB/s of archive payload. Monthly throughput was 1.19 million records/s at 15.69 MB/s. Benchmark payload-to-Parquet ratios were 21.36× and 5.85×. The monthly download count includes localized daily archive checks for the detected gaps. All six partitions passed structural validation and were qualified due to documented reference discrepancies.

A local cProfile pass over the BTC daily ZIP took 1.41 seconds. Arrow CSV ingestion and ZIP decoding dominated the measured stack, with about 0.36 seconds spent in zlib decompression and 0.18 seconds in the Polars bucket aggregation. [Profile output](bench-data/verified/profile-btc-daily.txt)

## Discrepancies

On October 27, 2026, BTCUSDT, LINKUSDT and XAUUSDT each had 27 consecutive positive-volume 1-minute candles without matching aggregate trades. The monthly and daily archive evidence agreed on the missing run. The published dataset leaves 00:02–00:31 UTC unavailable, including the two potentially partial minutes at its boundaries. Remaining days passed daily consistency checks and retained their logged minute-level differences.

For LINKUSDT aggregate 559878230, the official individual-trade ZIP timestamps ranged from 1795147319962 to 1795147320009 ms for the published ID span, with total quantity 36.62. Official klines split those fills at the minute boundary. The 5-second export assigns the aggregate to its recorded timestamp 1795147319962 ms. The daily bar reconstruction matched kline base-volume totals for all three symbols. Minute counts and constituent-fill totals can differ between the official data products.

## Full run estimate

The frozen 50 symbols imply up to 345,133,440 5-second slots after listing dates. Extrapolating recent archive sizes per symbol gives approximately 96.6 GB of network payload. Measured Parquet size per bar gives an upper-slot storage estimate of 25.9 GB, or 33.8 GB with headroom. Archive throughput implies approximately 80 minutes at the strongest observed benchmark rate. Historical activity, fallback checks, and network or CPU contention can extend the run. The drive had approximately 94 GB free at preflight, enough for the conservative forecast and the configured temporary budget.

Measurements, probe URLs and checksums are in `data/metadata/benchmark_report.json` and the per-benchmark manifests under `bench-data/verified`.

## Full acquisition

The 50-symbol run published 338,443,780 bars in 20,269,763,600 bytes across the requested UTC window. This includes November 24, 2026 for all 50 symbols after the official daily files appeared. The largest acquisition pass ran for 65.1 minutes, transferred 95.7 GB, and reached 3.6 GB peak sampled RSS. The final retry took 142.8 seconds and transferred 843 MB.

The SOLUSDT July 2025 monthly ZIP contains conflicting records for the same aggregate trade ID. Daily ZIPs recovered 30 days. The July 16 daily ZIP contains the conflict too, so the day remains unpublished. Four DRAMUSDT days from June 3 through 6, 2026 failed independent volume checks. For June 3, the checksum-verified individual-trade archive and the 1-minute candles both give 588,356.07 base and 40,435,410.192 USDT quote volume. The aggregate archive gives 587,846.22 base and 40,399,936.8603 USDT quote volume. The pipeline preserves those days as unavailable and records other quarantined intervals in the coverage report. [SOLUSDT July 16 aggregate ZIP](https://data.binance.vision/data/futures/um/daily/aggTrades/SOLUSDT/SOLUSDT-aggTrades-2025-07-16.zip) · [DRAMUSDT June 3 individual-trade ZIP](https://data.binance.vision/data/futures/um/daily/trades/DRAMUSDT/DRAMUSDT-trades-2026-06-03.zip)
