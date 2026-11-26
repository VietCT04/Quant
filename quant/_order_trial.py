"""Order scheduling and reporting within the existing block-major trial."""

from collections import Counter
from time import perf_counter

import numpy as np

from marketdata.model import DAY
from simulator.reporting import Logs, order_lifecycles, record
from .explorer_ledger import REASONS
from .strategy import Portfolio


class OrderTrial:
    def __init__(self, trial, feed):
        self.trial = trial
        self.start, self.end = feed.trading_start, feed.end
        self.symbols = feed.symbols
        self.indices = {symbol: i for i, symbol in enumerate(self.symbols)}
        self.pending = []
        self.outcomes = []
        self.next_id = 1
        self.counts = Counter()
        self.rejections = Counter()
        self.rejected_symbols = Counter()
        self.requested_notional = self.filled_notional = 0.0
        self.warmup = Counter()
        self.skipped = Counter()
        self.buffers = {}
        self.warm_seen = 0
        self.book_clock = None
        self.last_mark_valid = None
        self.ledger = trial.logs or Logs(trial.directory / ".summary-ledger", self.symbols)
        self.audit = trial.logs is not None
        self.sealed = False

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
        prev_run = decision.last_valid_run
        valid = (status <= 1) & np.isfinite(market[:, :, 0])
        axis = np.arange(len(starts), dtype="i8")[:, None]
        last_invalid = np.maximum.accumulate(np.where(valid, -prev_run - 1, axis), axis=0)
        runs = axis - last_invalid
        decision.last_valid_run = runs[-1]
        if warm:
            observed = (runs[first:] >= warm) & valid[first:]
        else:
            observed = valid[first:]
        for i, n in enumerate(np.count_nonzero(~observed, axis=0)):
            self.warmup[self.symbols[i]] += int(n)

        def run(stop):
            nonlocal begin, funding_cursor, engine_time, output_time
            started = perf_counter()
            end = starts[stop - 1] + trial.settings.interval
            cursor = np.searchsorted(funding["ms"], end, side="left")
            count = 0
            while count < len(self.pending) and self.pending[count][0] < end:
                count += 1
            requests = self.pending[:count]
            del self.pending[:count]
            engine.run_orders(starts[begin:stop], market[begin:stop], status[begin:stop],
                              block.values["open"][begin:stop], funding[funding_cursor:cursor], requests)
            funding_cursor, begin = cursor, stop
            self.book_clock = int(end)
            self.last_mark_valid = np.isfinite(market[stop - 1, :, 3]) | (
                bool(trial.settings.trade_mark_fallback) & (status[stop - 1] == 0)
                & np.isfinite(market[stop - 1, :, 0])
            )
            engine_time += perf_counter() - started
            started = perf_counter()
            self.collect()
            output_time += perf_counter() - started

        cadence = decision.receipt["decision_ticks"]
        base = int((starts[0] - self.start) // block.interval_ms)
        offset = first + (-(base + first) % cadence)
        for row in range(offset, len(starts), cadence):
            run(row + 1)
            started = perf_counter()
            equity = engine.equity()
            positions = engine.positions()
            positions.flags.writeable = False
            ready = observed[row - first].copy()
            for name in decision.plan.features:
                ready &= np.isfinite(block.values[name][row])
            for i in np.flatnonzero(~ready):
                self.skipped[self.symbols[i]] += 1
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
            if first:
                from .data import _slice
                # The decision frame is a proxy; the runner supplies the concrete projected block.
                block = _slice(decision._view(block), first, None)
                decision.annotations = {name: values[first:] for name, values in decision.annotations.items()}
            trial.candidate.decision._log(block, None, decision.annotations)
            self.flush()
        return signal_time, engine_time, output_time

    def collect(self, *, terminal=False):
        trial = self.trial
        orders = trial.engine.orders()
        fills = orders[orders["filled"] != 0]
        submitted = orders[orders["id"] != 0]
        accepted = submitted[submitted["filled"] != 0]
        rejected = submitted[submitted["filled"] == 0]
        self.counts.update(submitted=len(submitted), filled=len(accepted), rejected=len(rejected),
                           terminal=int(np.count_nonzero(fills["reason"] == 21)),
                           liquidations=int(np.count_nonzero(fills["reason"] == 22)))
        self.requested_notional += float(np.nansum(submitted["requested_notional"]))
        self.filled_notional += float(np.sum(np.abs(accepted["filled"]) * accepted["price"]))
        for item in rejected:
            self.rejections[str(int(item["reason"]))] += 1
            self.rejected_symbols[self.symbols[item["symbol"]]] += 1
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
        positions_summary = order_lifecycles(self.ledger.folder, self.symbols, positions, self.end)
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
        if not self.audit:
            for path in self.ledger.folder.iterdir():
                if path.is_file() and path.name in {"fills.parquet", "funding.parquet", "fills.part", "funding.part"}:
                    path.unlink()
            self.ledger.folder.rmdir()

    def close(self):
        if not self.sealed:
            self.ledger.close(False)
            self.clean()
