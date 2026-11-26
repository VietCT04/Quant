# Author and submit trusted strategies

Put local implementations in research_strategies and explicitly add their Strategy value to catalog.py. Restart the service and check the loaded versions with quant research strategies. HTTP selectors carry identifiers, versions and parameters. The implementation and trusted runtime hashes, feature graph, warm-up fingerprint, dataset fingerprint, seed and settings are frozen.

## V2 interface

- **prepare(parameters)** returns a Plan with named, registered expressions, optional decision cadence, pre-warm-up in scheduled five-second bars, and Plot descriptors. Set feature_periods by output alias for a higher timeframe. Each value is a number of five-second bars per UTC bucket. Use only features whose raw inputs can be aggregated by the SDK.
- **initialize(parameters, symbols, rng)** creates independent mutable state. Optional **warm(state, block)** observes historical blocks before trading. It has no order book.
- **orders(state, row, portfolio, orders)** sees one completed bar and the actual booked portfolio up through its close, including earlier outcomes in insertion order. Wallet, marked equity, available margin, average entries, signed quantities, ready symbols and order outcomes are observable. New requests can fill only on a later observation of the ledger.
- Call **orders.buy(symbol, notional=100)** or **orders.sell(symbol, quantity=0.002)**, **orders.close(symbol)**, **orders.reduce(symbol, fraction=0.5)** or **orders.close_all(portfolio.positions)**. Leave the collection empty to hold. Optional **orders.rebalance** explicitly submits a difference based on the observable price and booked quantity. Native margin checks remain authoritative.
- Optionally return named time-by-symbol observation arrays for declared plots. Recorded features and observations appear in detailed replays. Warm-up can leave individual symbols unready. Inspect the ready list and handle unavailable fields explicitly. Cadence controls when the callback runs, while the strategy controls whether it submits anything.
- **quant.trading.proportional_size** sizes from equity, with optional available-margin capping. **quant.trading.exit_trigger** tests stop, take-profit or trailing conditions against completed observable prices. Use booked entry time or stored state for holding limits and cooldowns. Any resulting exit is a market order for a subsequent open.

The existing version 1 targets and decide callbacks retain the legacy contract for explicit compatibility. In V2 use the order rebalance helper if the strategy actually needs it. The example slow_directional_persistence illustrates completed hourly features, explicit entries and exits, and a stable quantity between them.

## Submit and review

```sh
uv run quant service start --build-ui
uv run python -m examples.v2_research --out data/research/v2-first
uv run quant research inbox --unreviewed
uv run quant research results H-0001
```

The example defaults to all Liquid 50 symbols. For targeted research pass --symbols. Set stage and evaluation metadata in the data-only hypothesis envelope. The initial six-month and full-history experiments use separate submissions. Inspect diagnostic failures and recorded warnings even when the experiment completed. Create new strategy versions for behavioral changes. Use the [simulator contract](SIMULATOR_V2.md) and [research protocol](RESEARCH_PROTOCOL.md).
