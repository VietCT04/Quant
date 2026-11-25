from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from marketdata.model import sha256, write_json
from marketdata.reader import MarketDataset, as_millis

from .feed import MarketDataFeed, order_rules
from .native import Engine, Settings
from .reporting import Logs, performance, record
from .strategies import NAMES, Precomputed, Signals

HOLDOUT = "2026-08-25"


def backtest(feed, settings, strategy, out):
    return _backtest(feed, settings, strategy, out)


def _backtest(feed, settings, strategy, out, *, release=None):
    if feed.end > as_millis(HOLDOUT) and release is None:
        raise ValueError(f"Strategy evaluation must end at or before {HOLDOUT} UTC")
    if (feed.step, feed.lag) != (settings.interval, settings.lag) or settings.logging != 1:
        raise ValueError("Backtests require logging and the feed's interval and availability lag")
    t = perf_counter()
    signal_seconds = native_seconds = 0
    out = Path(out)
    log = Logs(out, feed.symbols)
    complete = False
    try:
        with Engine(settings, order_rules(feed.dataset, feed.symbols), strategy.native_target) as engine:
            for starts, values, status in feed:
                now = perf_counter()
                if isinstance(strategy, Precomputed):
                    strategy.apply_at(starts, values)
                else:
                    strategy.apply(values, status)
                signal_seconds += perf_counter() - now
                pending = feed.funding_through(starts[-1] + settings.interval + settings.lag)
                now = perf_counter()
                engine.run(starts, values, status, pending)
                native_seconds += perf_counter() - now
                orders = engine.orders()
                for key, batch in (("orders", orders), ("fills", orders[orders["filled"] != 0]),
                                   ("equity", engine.curve()), ("funding", engine.cashflows()),
                                   ("targets", engine.targets())):
                    log.append(key, batch)
            final, positions = record(engine.equity()), engine.positions()
            log.append("positions", positions)
        complete = True
    finally:
        log.close(complete)
    summary = performance(out, settings, final, positions, feed)
    summary["settings"] = asdict(settings)
    snapshot_paths = {
        "universe": feed.dataset.meta / "universes" / "liquid50.json",
        "coverage": feed.dataset.meta / "coverage.parquet",
        "auxiliary_inventory": feed.aux_root / "inventory.json",
        "native_core_source": Path(__file__).with_name("core.cpp"),
    }
    summary["input_sha256"] = {key: sha256(path) if path.is_file() else None for key, path in snapshot_paths.items()}
    summary["mark_staleness_ms"] = feed.mark_staleness_ms
    if release is not None:
        summary["holdout_release"] = release
    summary["runtime"] = {"elapsed_seconds": perf_counter() - t, "signal_seconds": signal_seconds, "native_seconds": native_seconds}
    write_json(out / "report.json", summary)
    return summary


def main():
    p = argparse.ArgumentParser(description="Run a deterministic Liquid 50 development backtest")
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--start", default="2026-06-10")
    p.add_argument("--end", default="2026-06-17")
    p.add_argument("--symbols", nargs="+")
    p.add_argument("--strategy", default="buy_hold", choices=NAMES)
    p.add_argument("--notional", type=float, default=1000)
    p.add_argument("--seed", type=int, default=41)
    p.add_argument("--fee-bps", type=float, default=5)
    p.add_argument("--slippage-bps", type=float, default=1)
    p.add_argument("--spread-bps", type=float, default=2)
    p.add_argument("--impact-bps", type=float, default=0)
    p.add_argument("--latency-ms", type=int, default=50)
    p.add_argument("--mark-staleness-ms", type=int, default=60000)
    p.add_argument("--rebalance-ticks", type=int, default=720)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    d = MarketDataset(a.data / "binance/um/bars_5s", universe="liquid50")
    feed = MarketDataFeed(d, a.symbols, a.start, a.end, mark_staleness_ms=a.mark_staleness_ms)
    settings = Settings(fee_bps=a.fee_bps, spread_bps=a.spread_bps, slippage_bps=a.slippage_bps, impact_bps=a.impact_bps,
                        rebalance_ticks=a.rebalance_ticks, latency=a.latency_ms)
    strategy = Signals(a.strategy, feed.symbols, a.notional, settings.rebalance_ticks, a.seed)
    summary = backtest(feed, settings, strategy, a.out)
    write_json(a.out / "experiment.json", {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()})
    print(json.dumps({k: summary[k] for k in ("net_pnl", "total_return", "maximum_drawdown", "orders", "fills", "runtime")}, indent=2))


if __name__ == "__main__":
    main()
