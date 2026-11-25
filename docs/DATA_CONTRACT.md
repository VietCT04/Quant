# SDK data contract

The SDK projects the existing audited Binance USD-M futures Parquet. Frozen Liquid 50 membership uses late-2026 traded volume and carries survivorship and historical selection bias. A time-local cross-section includes only already listed, available symbols.

- `timestamps_ms` is ascending UTC epoch milliseconds at interval starts. Selections are half-open. Each field and validity mask has shape `[time, symbol]` in `symbols` order.
- Status is `0` traded, `1` confirmed empty, `2` unavailable, `3` quarantined, or `4` pre-listing. Unknown states carry NaN. The reader retains audited carried prices and zero flow for confirmed-empty intervals.
- `available_at_ms` is one clock per row. Native bars use start plus interval plus lag (100 ms by default). Higher resolutions use the latest input clock. Blocks carry field units and provenance.
- Prices and VWAP are USDT per base unit. Base quantities and USDT turnover are separate fields. SDK arrays use float64 and NaN. Parquet retains integer types for counts.
- `align` checks exact axes by default. Inner and outer modes explicitly join at the same interval and mark introduced cells unavailable.

## Resampling

UTC-anchored resolution must be a multiple of the source interval. Supported examples include 10s, 30s, 1m, 5m, 15m, 1h, 4h and 1d. Known buckets sum flow and counts. Open is the first traded open. High and low exclude carried prices where there are trades. Close is the last slot price. An entirely empty bucket uses the known carried price. VWAP requires projected quote turnover and base volume and is null at zero volume. `max_trade_qty` is the largest aggregate.

An input quarantine quarantines its entire bucket. Unavailable input or mixed pre-listing and post-listing makes the bucket missing. A whole pre-listing bucket stays pre-listing. Partial edge buckets are omitted.

## Auxiliary observations and identity

`load_funding` returns per-symbol settlement tables using their event timestamps. `load_marks` returns monthly tables using recorded availability. Missing post-listing files raise a capability error. With `asof_observations`, supply the clock column and a maximum age. An observation is available only at and after its recorded clock. The native feed retains its own settlement checks and stale mark warnings.

`fingerprint` digests selection configuration, the universe, coverage, auxiliary inventory and current SQLite partition receipts. `verify_contents=True` additionally hashes selected trade Parquet bytes. Treat receipt-audited partitions as immutable and re-audit out-of-band replacements.
