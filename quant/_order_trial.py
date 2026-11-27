"""Order scheduling and reporting within the existing block-major trial."""

from collections import Counter
import math
import os
from time import perf_counter

import numpy as np

from marketdata.model import DAY
from simulator.native import _OrderTape
from simulator.reporting import Logs, SummaryLogs, order_lifecycles, record
from .explorer_ledger import REASONS
from .strategy import Portfolio


# Version alone is insufficient for authorizing a compiled implementation.
SHOCK_IMPLEMENTATIONS = {
    "1.0.0": "4b079ad1b63ad60dbc4f80d65c6358e007e9b0dac8b39e97281db34824d24732",
    "2.0.0": "46aba4300284a5be8a0455dac2de09f5f357ac9de23c6a69e89f0c093d281545",
}
SHOCK_COLUMNS = ("close", "fast", "move", "old_fast", "prior_vol", "trend", "fair",
                 "aggression", "volume_current", "volume_prior", "prior_range_edge")


def observed_warmup(status, close, previous, warm):
    valid = (status <= 1) & np.isfinite(close)
    axis = np.arange(len(status), dtype="i8")[:, None]
    last_invalid = np.maximum.accumulate(np.where(valid, -previous - 1, axis), axis=0)
    runs = axis - last_invalid
    return valid & (runs >= warm), runs[-1].copy()


class OrderTrial:
    def __init__(self, trial, feed, *, ledger_limit=0):
        self.trial = trial
        self.start, self.end = feed.trading_start, feed.end
        self.symbols = feed.symbols
        self.indices = {symbol: i for i, symbol in enumerate(self.symbols)}
        self.pending = []
        self.outcomes = []
        self.next_id = 1
        self.counts = Counter(submitted=0, filled=0, rejected=0, terminal=0, liquidations=0)
        self.rejections = Counter()
        self.rejected_symbols = Counter()
        self.requested_notional = self.filled_notional = 0.0
        self.warmup = Counter()
        self.skipped = Counter()
        self.buffers = {}
        self.warm_seen = 0
        self.book_clock = None
        self.last_mark_valid = None
        self.audit = trial.logs is not None
        self.sealed = False
        runner = trial.candidate.decision
        self.compiled = (not self.audit and not trial.settings.latency
                         and os.getenv("QUANT_ORDER_ENGINE") != "python"
                         and hasattr(trial.engine.lib, "sim_run_program")
                         and runner.strategy.id == "shock_inventory_recovery"
                         and SHOCK_IMPLEMENTATIONS.get(runner.strategy.version) is not None
                         and SHOCK_IMPLEMENTATIONS[runner.strategy.version] == runner.receipt.get("implementation_hash"))
        ledger_type = SummaryLogs if ledger_limit and self.compiled else Logs
        self.ledger = trial.logs or ledger_type(trial.directory / ".summary-ledger", self.symbols,
                                               *((ledger_limit,) if ledger_type is SummaryLogs else ()))
        if self.compiled:
            trial.engine.configure_shock(runner.parameters, patient=runner.strategy.version == "2.0.0")

    def advance(self, starts, market, status, funding, block):
        trial = self.trial
        decision = trial.candidate.decision
        engine = trial.engine
        decision.annotations = {}
        first = int(np.searchsorted(starts, self.start))
        decision.warm_orders(block, first)
        self.warm_seen += first
        funding_cursor = 0
        signal_time = engine_time = output_time = 0.0
        begin = first
        warm = decision.plan.warmup_bars
        shared = getattr(block, "_order_readiness", None)
        shared_observed = getattr(block, "_observed_warmup", None)
        if shared is None:
            valid = shared_observed
            if valid is None:
                valid, decision.last_valid_run = observed_warmup(status, market[:, :, 0], decision.last_valid_run, warm)
            observed = valid[first:]
            qualified = None
        else:
            observed = shared[0][first:]
            qualified = shared[1][first:]
        for i, n in enumerate(np.count_nonzero(~observed, axis=0)):
            self.warmup[self.symbols[i]] += int(n)

        if not self.compiled:
            tape = _OrderTape(engine, starts, market, status, block.values["open"], funding)

        def run(stop):
            nonlocal begin, funding_cursor, engine_time, output_time
            started = perf_counter()
            end = starts[stop - 1] + trial.settings.interval
            cursor = int(tape.end_funding[stop-1])
            count = 0
            while count < len(self.pending) and self.pending[count][0] < end:
                count += 1
            requests = self.pending[:count]
            del self.pending[:count]
            tape.run(begin, stop, funding_cursor, cursor, requests)
            funding_cursor, begin = cursor, stop
            self.book_clock = int(end)
            engine_time += perf_counter() - started
            started = perf_counter()
            self.collect()
            output_time += perf_counter() - started

        cadence = decision.receipt["decision_ticks"]
        base = int((starts[0] - self.start) // block.interval_ms)
        offset = first + (-(base + first) % cadence)
        if qualified is None:
            readiness_step = 1 if self.compiled else cadence
            readiness_start = first if self.compiled else offset
            qualified = observed[readiness_start-first::readiness_step].copy()
            for name in decision.plan.features:
                qualified &= np.isfinite(block.values[name][readiness_start::readiness_step])
            ready_at_decision = qualified[offset-first::cadence] if self.compiled else qualified
        else:
            ready_at_decision = qualified[offset-first::cadence]
        counts = np.count_nonzero(~ready_at_decision, axis=0)
        for name, count in zip(self.symbols, counts):
            if count:
                self.skipped[name] += int(count)
        if first < len(starts):
            self.last_mark_valid = np.isfinite(market[-1, :, 3]) | (
                bool(trial.settings.trade_mark_fallback) & (status[-1] == 0) & np.isfinite(market[-1, :, 0])
            )
        if self.compiled and first < len(starts):
            clock = perf_counter()
            engine._run_program(starts[first:], market[first:], status[first:], block.values["open"][first:], funding,
                               [block.values[name][first:] if name in block.values else None for name in SHOCK_COLUMNS],
                               qualified, self.start, cadence)
            self.book_clock = int(starts[-1] + trial.settings.interval)
            engine_time = perf_counter() - clock
            clock = perf_counter()
            self.collect(cuts=starts[offset::cadence] + trial.settings.interval)
            self.flush()
            return 0, engine_time, perf_counter() - clock

        for row, ready in zip(range(offset, len(starts), cadence), ready_at_decision):
            run(row + 1)
            started = perf_counter()
            equity = engine.equity()
            positions = engine.positions()
            positions.flags.writeable = False
            portfolio = Portfolio(self.book_clock, positions, float(equity["equity"]),
                                  float(equity["wallet"]), float(equity["available"]), tuple(self.outcomes),
                                  trial.settings.leverage, tuple(s for s, ok in zip(self.symbols, ready) if ok))
            self.outcomes.clear()
            for order in decision.order_step(block, row, portfolio):
                self.pending.append((self.book_clock + trial.settings.latency, self.next_id,
                                     self.indices[order.symbol], order.kind, self.book_clock, order.size))
                self.next_id += 1
            signal_time += perf_counter() - started
        if begin < len(starts):
            run(len(starts))
        if first < len(starts):
            if first and decision.trace is not None:
                from .data import _slice
                # The decision frame is a proxy; the runner supplies the concrete projected block.
                block = _slice(decision._view(block), first, None)
                decision.annotations = {name: values[first:] for name, values in decision.annotations.items()}
            trial.candidate.decision._log(block, None, decision.annotations)
            self.flush()
        return signal_time, engine_time, output_time

    def collect(self, *, terminal=False, cuts=None):
        trial = self.trial
        orders = trial.engine.orders()

        if not self.audit and not terminal and not len(orders):
            trial._record_daily()
            self.buffer("fills", orders)
            self.buffer("funding", trial.engine.cashflows())
            return
        fills = orders[orders["filled"] != 0]
        submitted = orders[orders["id"] != 0]
        accepted = submitted[submitted["filled"] != 0]
        rejected = submitted[submitted["filled"] == 0]
        self.counts.update(submitted=len(submitted), filled=len(accepted), rejected=len(rejected),
                           terminal=int(np.count_nonzero(fills["reason"] == 21)),
                           liquidations=int(np.count_nonzero(fills["reason"] == 22)))
        if cuts is not None and len(submitted):
            groups = np.searchsorted(cuts, submitted["ms"], side="right")
            changes = np.flatnonzero(np.diff(groups)) + 1
            for piece in np.split(submitted, changes):
                if len(piece) == 1:
                    event = piece[0]
                    if not math.isnan(event["requested_notional"]):
                        self.requested_notional += float(event["requested_notional"])
                    if event["filled"]:
                        self.filled_notional += abs(float(event["filled"])) * float(event["price"])
                else:
                    self.requested_notional += float(np.nansum(piece["requested_notional"]))
                    good = piece[piece["filled"] != 0]
                    self.filled_notional += float(np.sum(np.abs(good["filled"]) * good["price"]))
        else:
            self.requested_notional += float(np.nansum(submitted["requested_notional"]))
            self.filled_notional += float(np.sum(np.abs(accepted["filled"]) * accepted["price"]))
        for item in rejected:
            self.rejections[str(int(item["reason"]))] += 1
            self.rejected_symbols[self.symbols[item["symbol"]]] += 1
        if not self.compiled:
            for item in orders:
                value = record(item)
                value.update(symbol=self.symbols[item["symbol"]], status="filled" if item["filled"] else "rejected",
                             reason_label=REASONS.get(item["reason"], "Unknown"))
                self.outcomes.append(value)
        curve = trial.engine.curve()
        if self.audit or terminal:
            for ms, equity in zip(curve["ms"], curve["equity"]):
                if terminal or ms % DAY == 0:
                    trial.daily[int((ms - 1) // DAY)] = float(equity)
        else:
            trial._record_daily()
        if self.audit:
            self.buffer("orders", orders)
            self.buffer("equity", curve)
            self.buffer("targets", trial.engine.targets())
        self.buffer("fills", fills)
        self.buffer("funding", trial.engine.cashflows())

    def buffer(self, name, rows):
        chunks = self.buffers.setdefault(name, [])
        if len(rows) or not chunks:
            chunks.append(rows)

    def flush(self):
        for name, chunks in self.buffers.items():
            self.ledger.append(name, np.concatenate(chunks) if len(chunks) > 1 else chunks[0])
        self.buffers.clear()

    def finish(self):
        trial = self.trial
        pre_close = record(trial.engine.equity())
        original = trial.engine.positions()
        trial.engine.terminal(self.end, self.pending)
        self.collect(terminal=True)
        self.flush()
        positions = trial.engine.positions()
        if self.audit:
            self.ledger.append("positions", positions)
        self.ledger.close(True)
        self.sealed = True
        positions_summary = (self.ledger.lifecycles(positions, self.end) if isinstance(self.ledger, SummaryLogs)
                             else order_lifecycles(self.ledger.folder, self.symbols, positions, self.end))
        warnings = {}
        if pre_close["uncertain_positions"]:
            warnings["stale_terminal_marks"] = [
                symbol for symbol, row, fresh in zip(self.symbols, original, self.last_mark_valid)
                if row["qty"] and not fresh
            ]
        report = {
            "execution_model": "next_open_v2", "pre_close": pre_close,
            "pre_close_positions": [record(row) | {"symbol": s} for s, row in zip(self.symbols, original) if row["qty"]],
            "position_lifecycles": positions_summary,
            "equity_at_close_usdt": float(trial.engine.equity()["equity"]),
            "completed_positions": positions_summary["count"], "profit_factor": positions_summary["profit_factor"],
            "win_rate": positions_summary["win_rate"],
            "execution_diagnostics": {
                **self.counts, "success_rate": self.counts["filled"] / self.counts["submitted"] if self.counts["submitted"] else None,
                "requested_notional": self.requested_notional, "filled_notional": self.filled_notional,
                "reasons": {REASONS[int(key)]: count for key, count in sorted(self.rejections.items())},
                "rejected_by_symbol": dict(self.rejected_symbols),
                "not_ready_or_unavailable_bars": dict(self.warmup),
                "not_ready_or_unavailable_decisions": dict(self.skipped),
                "warmup_requested_bars": trial.candidate.warmup_bars, "warmup_loaded_bars": self.warm_seen,
                "warnings": warnings,
            },
        }
        self.clean()
        return report

    def clean(self):
        if not self.audit and self.ledger.folder.exists():
            for path in self.ledger.folder.iterdir():
                if path.is_file() and path.name in {"fills.parquet", "funding.parquet", "fills.part", "funding.part"}:
                    path.unlink()
            self.ledger.folder.rmdir()

    def close(self):
        if not self.sealed:
            self.ledger.close(False)
            self.clean()
