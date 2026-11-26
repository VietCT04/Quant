"""Artifact-backed data contracts for the local Backtest Explorer."""

from dataclasses import asdict
from functools import cached_property, lru_cache
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import polars as pl

from simulator.feed import TRADED, EMPTY, MarketDataFeed

from . import __version__, control_handlers, control_store, data, experiment, features, strategy
from ._batch import FIELDS
from .explorer_ledger import accounting_at, execution, reconstruct, sample_marks
from .validation import _check

TIMEFRAMES = {"5s": 5, "15s": 15, "30s": 30, "1m": 60, "5m": 300,
              "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
TABLES = {"fills": "fills", "orders": "orders", "rejected": "orders",
          "targets": "targets", "funding": "funding"}
ARTIFACTS = ("fills", "orders", "targets", "funding", "equity", "positions")
CLOCK_FIELDS = ("equity", "risk_drawdown", "gross", "net", "realized", "unrealized",
                "fees", "funding", "uncertain_positions")
MAX_CANDLES = 2048
MAX_LEDGER_ROWS = 200_000
MAX_FEATURE_CELLS = 12_000_000
BOUND_FIELDS = ("start_ms", "end_ms", "universe", "dataset_fingerprint", "parameters",
                "sdk_version", "simulator_version", "evaluation", "seed", "features",
                "costs", "capabilities", "strategy")
COLORS = ("#efc276", "#91aafa", "#79d5c1", "#eb9ac3")


def _mismatch(message):
    raise ValueError(f"Exact replay is incompatible. {message}")


def verify_provenance(record, *, features_only=False):
    """Conservatively compare trusted execution source to the clean recorded revision."""
    spec = record["specification"]
    if "strategy" in spec:
        if spec.get("sdk_version") != __version__ or spec.get("simulator_version") != hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "simulator" / "core.cpp").read_bytes()
        ).hexdigest():
            _mismatch("The recorded SDK or simulator version differs.")
        try:
            settings = control_handlers.effective_settings(spec, spec["parameters"], True)
            strategy.resolve_frozen(spec, settings)
        except ValueError as error:
            _mismatch(str(error))
        return
    provenance = (record.get("outcome") or {}).get("execution", {})
    git = provenance.get("git") or {}
    commit = git.get("commit") or ""
    if not re.fullmatch(r"[a-f0-9]{40,64}", commit) or git.get("dirty") is not False:
        _mismatch("The original execution has no clean, verifiable code revision.")
    if provenance.get("handler") not in {"native", "audit"} or provenance.get("handler_version") != 1:
        _mismatch("The original trusted handler version is unavailable.")
    root = Path(__file__).resolve().parents[1]
    core = root / "simulator" / "core.cpp"
    if spec.get("sdk_version") != __version__ or spec.get("simulator_version") != hashlib.sha256(core.read_bytes()).hexdigest():
        _mismatch("The recorded SDK or simulator version differs.")
    scopes = ["quant", "simulator", "marketdata", "uv.lock",
              ":(exclude)quant/control_service.py", ":(exclude)quant/control_store.py",
              ":(exclude)quant/control_cli.py", ":(exclude,glob)quant/explorer*.py"]
    if features_only:
        # Legacy display projects close, then evaluates only the archived SDK graph.
        # Batch scheduling and extended columns are outside this calculation.
        scopes.extend([":(exclude)quant/_batch.py", ":(exclude)quant/_screen.py",
                       ":(exclude)quant/control_handlers.py", ":(exclude)quant/research.py",
                       ":(exclude)quant/strategy.py", ":(exclude)quant/__init__.py",
                       ":(exclude)simulator/feed.py"])
    command = ["git", "-C", str(root), "-c", f"safe.directory={root.as_posix()}"]
    try:
        delta = subprocess.check_output([*command, "diff", "--name-only", commit, "--", *scopes], stderr=subprocess.STDOUT)
        untracked = subprocess.check_output([*command, "ls-files", "--others", "--exclude-standard", "--", *scopes])
    except (OSError, subprocess.CalledProcessError):
        _mismatch("The recorded execution sources could not be compared.")
    if delta or untracked:
        _mismatch("Trusted strategy, dependency, data-reader, or simulator sources have changed.")
    if not (record.get("outcome") or {}).get("metrics", {}).get("settings"):
        _mismatch("The original effective execution settings are unavailable.")


def _settings(record):
    spec = record["specification"]
    effective = asdict(control_handlers.effective_settings(spec, spec["parameters"], "strategy" in spec))
    original = record["outcome"]["metrics"]["settings"]
    differences = set(original) ^ set(effective)
    differences |= {key for key in original.keys() & effective.keys()
                    if key not in {"logging", "snapshot_ticks"} and original[key] != effective[key]}
    if differences:
        _mismatch("The original effective settings differ: " + ", ".join(sorted(differences)))


def _window(spec, query, step):
    lo = int(query.get("start", spec["start_ms"]))
    hi = int(query.get("end", spec["end_ms"]))
    if lo < spec["start_ms"] or hi > spec["end_ms"] or lo >= hi:
        raise ValueError("Choose a nonempty window inside the recorded development range")
    aligned_lo = max(spec["start_ms"], lo // step * step)
    aligned_hi = min(spec["end_ms"], -(-hi // step) * step)
    if -(-(aligned_hi - aligned_lo) // step) > MAX_CANDLES:
        raise ValueError(f"Use a coarser timeframe or select at most {MAX_CANDLES} candles")
    return aligned_lo, aligned_hi


def _symbol(spec, query):
    symbols = spec["universe"]
    symbol = query.get("symbol", symbols[0])
    if symbol not in symbols:
        raise ValueError("Symbol is outside the candidate's recorded universe")
    return symbol, symbols.index(symbol)


def _scan(path, lo=None, hi=None, symbol=None, columns=None):
    scan = pl.scan_parquet(path)
    if lo is not None:
        scan = scan.filter(pl.col("ms").is_between(lo, hi, closed="left"))
    if symbol is not None:
        scan = scan.filter(pl.col("symbol") == symbol)
    return scan.select(columns) if columns else scan


def _compact(positions):
    return [{key: value for key, value in item.items() if key not in {"timeline", "terminal_entry"}}
            for item in positions]


def _bounded_page(query):
    offset, limit = int(query.get("offset", 0)), int(query.get("limit", 50))
    if offset < 0 or not 1 <= limit <= 500:
        raise ValueError("Offset must be nonnegative and page size must be in [1,500]")
    return offset, limit


def _extrema(scan, fields, lo, hi, bucket_count=360):
    """Select first, last, and timestamped extrema per metric in each UTC bin."""
    width = max(1, -(-(hi - lo) // bucket_count))
    bucket = ((pl.col("ms") - lo) // width).alias("bucket")
    operation = []
    for field in fields:
        operation.extend([
            pl.col("ms").get(pl.col(field).arg_min()).alias(f"{field}_min"),
            pl.col("ms").get(pl.col(field).arg_max()).alias(f"{field}_max"),
        ])
    indexes = scan.group_by(bucket).agg(
        pl.col("ms").min().alias("first"), pl.col("ms").max().alias("last"), *operation,
    ).collect(engine="streaming")
    times = {value for row in indexes.drop("bucket").iter_rows() for value in row if value is not None}
    if not times:
        return {}
    packed = scan.filter(pl.col("ms").is_in(sorted(times))).collect(engine="streaming")
    by_ms = {row["ms"]: row for row in packed.iter_rows(named=True)}
    answer = {}
    for field in fields:
        keys = ["first", "last", f"{field}_min", f"{field}_max"]
        stamps = sorted({row[key] for row in indexes.iter_rows(named=True) for key in keys if row[key] is not None})
        answer[field] = [[int(stamp), by_ms[stamp][field]] for stamp in stamps]
    return answer


class Explorer:
    def __init__(self, registry, root, source):
        self.path, self.root, self.source = registry, root, source

    @cached_property
    def dataset(self):
        return data.open(self.source)

    def record(self, job):
        record = experiment.reproduce(self.path, job)
        if record["status"] != "completed" or record["specification"].get("job_kind") not in {"native", "audit"}:
            raise ValueError("Open a completed native simulation candidate")
        spec = record["specification"]
        _check(spec["start_ms"], spec["end_ms"])
        return record

    def selection(self, record):
        spec = record["specification"]
        chosen = data.select(
            self.dataset, start=spec["start_ms"], end=spec["end_ms"], symbols=spec["universe"],
        )
        if chosen.symbols != tuple(spec["universe"]) or data.fingerprint(chosen) != spec["dataset_fingerprint"]:
            _mismatch("The current dataset fingerprint or symbol order differs.")
        return chosen

    def _paths(self, record):
        root = (self.root / "artifacts").resolve()
        paths = {}
        for stored in record["outcome"]["artifacts"]:
            path = Path(stored).resolve()
            if path.is_relative_to(root) and path.is_file() and path.suffix == ".parquet" and path.stem in (*ARTIFACTS, "strategy_trace"):
                paths[path.stem] = path
        return paths

    def origin_for_job(self, job):
        with control_store.transaction(self.path) as db:
            row = db.execute("""
                SELECT h.seq, json_extract(h.definition,'$.audit_of') origin
                FROM control_hypotheses h JOIN control_trials t ON t.hypothesis=h.seq WHERE t.job=?
            """, (job,)).fetchone()
        return (control_store.identifier(row[0]), row["origin"]) if row else (None, None)

    def validate_replay(self, origin, replay=None):
        original = self.record(origin)
        verify_provenance(original)
        _settings(original)
        self.selection(original)
        if replay is not None and any(original["specification"].get(key) != replay.get(key) for key in BOUND_FIELDS):
            _mismatch("The queued replay specification differs from the original candidate.")
        return original

    def replay(self, key, origin):
        original = self.validate_replay(origin)
        owner, _ = self.origin_for_job(origin)
        if owner != key:
            raise KeyError(origin)
        if self._paths(original).keys() >= set(ARTIFACTS):
            raise ValueError("This candidate already has detailed execution artifacts")
        previous = self._latest_replay(origin)
        if previous and (previous["status"] in {"queued", "running"}
                         or self._detailed(original, previous) is not None):
            return {"campaign_id": None, "submission_ids": [previous["hypothesis_id"]]}
        definition = dict(control_store.detail(self.path, key)["definition"])
        definition.pop("key", None)
        spec = original["specification"]
        candidate = {"kind": "audit", "parameters": spec["parameters"]}
        if "strategy" in spec:
            candidate["strategy"] = {key: spec["strategy"][key] for key in ("id", "version")}
        definition.update({
            "name": f"{definition['name']} / Audit", "stage": "audit", "depends_on": [],
            "audit_of": origin, "replay_contract": 1, "candidates": [candidate],
        })
        return control_store.submit(self.path, definition, control_handlers.validate)

    def check_replay_output(self, original, result, *, annotate=True):
        """Require exact accounting agreement before exposing the detailed artifact."""
        baseline, replay = original["outcome"]["metrics"], result.get("metrics")
        if replay is None:
            return result
        fields = ("end", "risk", "execution_costs", "orders", "fills", "daily_marked_equity",
                  "per_symbol", "symbols", "bar_range_ms")
        if any(baseline.get(field) != replay.get(field) for field in fields):
            _mismatch("Detailed replay failed accounting reconciliation against the original report.")
        if annotate:
            result["execution"] |= {"exact_replay_of": original["id"], "original_revision": original["outcome"]["execution"].get("git", {}).get("commit")}
        return result

    def _latest_replay(self, job):
        with control_store.transaction(self.path) as db:
            row = db.execute("""
                SELECT h.seq, e.id, e.status, json_extract(e.outcome,'$.failure') failure
                FROM control_hypotheses h JOIN control_trials t ON t.hypothesis=h.seq
                JOIN experiments e ON e.id=t.job
                WHERE json_extract(h.definition,'$.audit_of')=?
                ORDER BY coalesce(json_extract(h.definition,'$.replay_contract'),0) DESC,h.seq DESC LIMIT 1
            """, (job,)).fetchone()
        if not row:
            return None
        return {"hypothesis_id": control_store.identifier(row["seq"]), "job": row["id"],
                "status": row["status"], "failure": json.loads(row["failure"]) if row["failure"] else None}

    def meta(self, record):
        spec, job = record["specification"], record["id"]
        replay = self._latest_replay(job)
        detailed = self._detailed(record, replay)
        compatibility = None
        if not detailed:
            try:
                self.validate_replay(job)
            except ValueError as error:
                compatibility = str(error)
        return {"job": job, "hypothesis_id": self.origin_for_job(job)[0], "symbols": spec["universe"],
                "start": spec["start_ms"], "end": spec["end_ms"], "timeframes": TIMEFRAMES,
                "detailed_job": detailed["id"] if detailed else None, "replay": replay,
                "strategy": {key: spec["strategy"][key] for key in ("id", "version")} if "strategy" in spec else None,
                "compatibility": compatibility, "ledger_tables": list(TABLES),
                "funding_warnings": record["outcome"]["metrics"].get("funding_warnings", [])}

    def _detailed(self, record, replay):
        if self._paths(record).keys() >= set(ARTIFACTS):
            return record
        if replay and replay["status"] == "completed":
            candidate = self.record(replay["job"])
            execution_info = candidate["outcome"].get("execution", {})
            if not set(ARTIFACTS) <= self._paths(candidate).keys():
                return None
            if execution_info.get("exact_replay_of") == record["id"]:
                return candidate
            # Older audit artifacts can be certified without rewriting their recorded provenance.
            original_execution = record["outcome"].get("execution", {})
            if (
                execution_info.get("git") == original_execution.get("git")
                and execution_info.get("handler") == "audit"
                and execution_info.get("handler_version") == original_execution.get("handler_version")
                and (execution_info.get("git") or {}).get("dirty") is False
            ):
                try:
                    if any(record["specification"].get(key) != candidate["specification"].get(key) for key in BOUND_FIELDS):
                        return None
                    self.check_replay_output(record, candidate["outcome"], annotate=False)
                except ValueError:
                    return None
                return candidate
        return None

    def candles(self, record, query):
        symbol, _ = _symbol(record["specification"], query)
        size = TIMEFRAMES[query.get("tf", "15m")] * 1000
        lo, hi = _window(record["specification"], query, size)
        selection = self.selection(record)
        return self._candles(selection, symbol, lo, hi, size, record["specification"]["dataset_fingerprint"])

    @lru_cache(maxsize=48)
    def _candles(self, selection, symbol, lo, hi, size, fingerprint):
        del fingerprint
        columns = ("open", "high", "low", "close", "volume")
        source = data.select(selection.dataset, symbols=[symbol], start=lo, end=hi, fields=columns)
        blocks = data.iter_blocks(source)
        if size != source.dataset.interval_ms:
            blocks = data.resample(blocks, size)
        rows = []
        for block in blocks:
            for index, (t, status) in enumerate(zip(block.timestamps_ms, block.status[:, 0])):
                row = {"t": int(t), "status": int(status), "available_ms": int(block.available_at_ms[index])}
                if status == TRADED:
                    row.update({field: float(block.values[field][index, 0]) for field in columns})
                elif status == EMPTY:
                    row["volume"] = 0
                rows.append(row)
        return {"start": lo, "end": hi, "step": size, "rows": rows}

    def _records(self, path, symbol):
        frame = _scan(path, symbol=symbol).head(MAX_LEDGER_ROWS + 1).collect(engine="streaming")
        if len(frame) > MAX_LEDGER_ROWS:
            raise ValueError(f"Full symbol lifecycle reconstruction exceeds the {MAX_LEDGER_ROWS:,}-record limit")
        return frame.to_dicts()

    @lru_cache(maxsize=2)
    def _ledger(self, origin, detailed, symbol, fingerprint):
        del fingerprint
        record = self.record(origin)
        replay = self.record(detailed)
        spec = record["specification"]
        paths = self._paths(replay)
        _, index = _symbol(spec, {"symbol": symbol})
        report = replay["outcome"]["metrics"]
        terminal = report["per_symbol"][index]
        settings = report["settings"]
        fills = [execution(row, settings["interval"], settings["lag"]) for row in self._records(paths["fills"], index)]
        funding = self._records(paths["funding"], index)
        positions, ledger, totals = reconstruct(symbol, fills, funding, terminal, spec["end_ms"] + settings["lag"])
        selection = self.selection(record)
        if positions:
            feed = MarketDataFeed(selection.dataset, [symbol], selection.start, selection.end)
            feed.read_threads = False
            sample_marks(feed, ledger, positions, fallback=settings["trade_mark_fallback"])
        return positions, ledger, funding, totals

    def _exact_ledger(self, original, detailed, symbol):
        return self._ledger(original["id"], detailed["id"], symbol, original["specification"]["dataset_fingerprint"])

    def positions(self, original, detailed, query):
        spec = original["specification"]
        symbol, _ = _symbol(spec, query)
        rows, _, _, totals = self._exact_ledger(original, detailed, symbol)
        key = query.get("position")
        if key is not None:
            item = next((row for row in rows if row["id"] == key), None)
            if item is None:
                raise KeyError(key)
            offset, limit = _bounded_page(query)
            timeline = [dict(row) for row in item["timeline"][offset:offset + limit]]
            self.rationale(detailed, [row for row in timeline if "decision" in row], _symbol(spec, query)[1])
            return {"position": _compact([item])[0], "rows": timeline,
                    "total": len(item["timeline"])}
        lo = int(query.get("start", spec["start_ms"]))
        hi = int(query.get("end", spec["end_ms"] + 1000))
        side, state = query.get("side"), query.get("state")
        rows = [row for row in rows if (not side or row["side"] == side)
                and (not state or row["state"] == state) and row["entry_ms"] < hi
                and (row["exit_ms"] is None or row["exit_ms"] >= lo)]
        sort = query.get("sort", "entry_ms")
        fields = set(rows[0]) - {"timeline"} if rows else set()
        if sort in fields:
            known = [row for row in rows if row[sort] is not None]
            rows = sorted(known, key=lambda row: row[sort], reverse=query.get("desc") == "1") + [
                row for row in rows if row[sort] is None
            ]
        if query.get("format") == "csv":
            if len(rows) > 50_000:
                raise ValueError("Filter the export to at most 50,000 positions")
            return _csv(rows)
        offset, limit = _bounded_page(query)
        return {"rows": _compact(rows[offset:offset + limit]), "total": len(rows), "reconciled": totals}

    def events(self, original, detailed, resource, query):
        spec = original["specification"]
        symbol, index = _symbol(spec, query)
        lo = int(query.get("start", spec["start_ms"]))
        hi = int(query.get("end", spec["end_ms"] + 1000))
        if lo < spec["start_ms"] or hi > spec["end_ms"] + 1000 or lo >= hi:
            raise ValueError("Execution range is outside the candidate's booked clocks")
        settings = detailed["outcome"]["metrics"]["settings"]
        file = self._paths(detailed)[TABLES[resource]]
        scan = _scan(file, lo, hi, index)
        if resource == "rejected":
            scan = scan.filter(pl.col("filled") == 0)
        if resource in {"fills", "orders", "rejected"}:
            typ = query.get("type")
            if typ == "partial":
                scan = scan.filter(pl.col("reason") == 1)
            elif typ:
                delta, after = pl.col("filled"), pl.col("after")
                before = after - delta
                predicates = {
                    "rejected": delta == 0, "reversal": (delta != 0) & (before * after < 0),
                    "long_entry": (delta != 0) & (before.abs() <= 1e-11) & (after > 0),
                    "short_entry": (delta != 0) & (before.abs() <= 1e-11) & (after < 0),
                    "long_exit": (delta != 0) & (before > 0) & (after.abs() <= 1e-11),
                    "short_exit": (delta != 0) & (before < 0) & (after.abs() <= 1e-11),
                    "scale_in": (delta != 0) & (before * after > 0) & (pl.col("closed") == 0),
                    "scale_out": (delta != 0) & (before * after > 0) & (pl.col("closed") > 0),
                }
                if typ not in predicates:
                    raise ValueError("Unknown execution type")
                scan = scan.filter(predicates[typ])
            side, reason = query.get("side"), query.get("reason")
            if side:
                scan = scan.filter(pl.col("requested") > 0 if side == "buy" else pl.col("requested") < 0)
            if reason:
                scan = scan.filter(pl.col("reason") == int(reason))
        elif query.get("side"):
            field = pl.col("qty" if resource == "funding" else "notional")
            scan = scan.filter(field > 0 if query["side"] == "long" else field < 0)
        if query.get("near") is not None:
            near = int(query["near"])
            backwards = query.get("direction") == "previous"
            scan = scan.filter(pl.col("ms") < near if backwards else pl.col("ms") > near)
            scan = scan.sort("ms", descending=backwards).head(1)
        offset, limit = _bounded_page(query)
        total = int(scan.select(pl.len()).collect(engine="streaming").item())
        rows = scan.slice(offset, limit).collect(engine="streaming").to_dicts()
        if resource in {"fills", "orders", "rejected"}:
            rows = self.rationale(detailed, [execution(row, settings["interval"], settings["lag"]) for row in rows], index)
        return {"rows": [row | {"symbol_name": symbol} for row in rows], "total": total}

    def series(self, original, detailed, query):
        spec = original["specification"]
        symbol, index = _symbol(spec, query)
        size = TIMEFRAMES[query.get("tf", "15m")] * 1000
        lo, hi = _window(spec, query, size)
        paths = self._paths(detailed)
        settings = detailed["outcome"]["metrics"]["settings"]
        clock_lo, clock_hi = lo + settings["lag"] + 1, hi + settings["lag"] + 1
        scans = _scan(paths["equity"], clock_lo, clock_hi, columns=["ms", *CLOCK_FIELDS])
        portfolio = _extrema(scans, CLOCK_FIELDS, clock_lo, clock_hi)
        if (hi - lo) // settings["interval"] > 3_000_000:
            return {"portfolio": portfolio, "symbol": {}, "symbol_error": "Narrow the window to view marked symbol series."}
        _, ledger, funding, _ = self._exact_ledger(original, detailed, symbol)
        return {
            "portfolio": portfolio,
            "symbol": self._symbol_series(original, detailed, symbol, index, lo, hi, size, ledger, funding),
        }

    def _symbol_series(self, original, detailed, symbol, index, lo, hi, size, ledger, funding):
        paths = self._paths(detailed)
        selection = self.selection(original)
        settings = detailed["outcome"]["metrics"]["settings"]
        step, lag = settings["interval"], settings["lag"]
        targets = _scan(paths["targets"], hi=hi, symbol=index, columns=["ms", "notional"]).filter(
            pl.col("ms") <= hi + lag
        ).head(MAX_LEDGER_ROWS + 1).collect(engine="streaming")
        if len(targets) > MAX_LEDGER_ROWS:
            raise ValueError("Target series exceeds the per-symbol display budget")
        target_times, amounts = targets["ms"].to_numpy(), targets["notional"].to_numpy()
        feed = MarketDataFeed(selection.dataset, [symbol], lo, hi)
        feed.read_threads = False
        samples = {}
        for times, market, flags in feed.blocks(shared=True):
            marks = market[:, 0, 3]
            if settings["trade_mark_fallback"]:
                marks = np.where(np.isfinite(marks), marks, np.where(flags[:, 0] == TRADED, market[:, 0, 0], np.nan))
            # The last observable sample in each display bin.
            for idx in np.flatnonzero((times + step) % size == 0):
                samples[int(times[idx] // size * size)] = (int(times[idx] + step + lag), float(marks[idx]))
        stamps = sorted(samples)
        clocks = [samples[t][0] for t in stamps]
        values = {field: [] for field in ("target", "qty", "entry", "notional", "realized", "unrealized", "fees", "funding", "marked_pnl")}
        for stamp, (qty, entry, realized, fees, cash) in zip(stamps, accounting_at(ledger, funding, clocks)):
            clock, mark = samples[stamp]
            idx = np.searchsorted(target_times, clock, side="right") - 1
            valid = np.isfinite(mark) and mark > 0
            unrealized = qty * (mark - entry) if qty and valid else (None if qty else 0)
            row = (float(amounts[idx]) if idx >= 0 else None, qty, entry,
                   qty * mark if qty and valid else (None if qty else 0), realized, unrealized,
                   fees, cash, None if unrealized is None else realized + unrealized - fees + cash)
            for field, value in zip(values, row):
                values[field].append([stamp, value])
        start_clock, end_clock = lo + lag, hi + lag
        state_times = [row["ms"] for row in ledger]
        left, right = np.searchsorted(state_times, [start_clock, end_clock], side="right")
        if right - left <= 5000:
            seed = ledger[left - 1] if left else {"after": 0, "entry": 0}
            state = [(start_clock, seed), *((row["ms"], row) for row in ledger[left:right])]
            if state[-1][0] < end_clock:
                state.append((end_clock, state[-1][1]))
            values["qty_steps"] = [[int(stamp), row["after"]] for stamp, row in state]
            values["entry_steps"] = [[int(stamp), row["entry"] if row["after"] else None] for stamp, row in state]
        left, right = np.searchsorted(target_times, [start_clock, end_clock], side="right")
        if right - left <= 5000:
            targets = [[start_clock, float(amounts[left - 1]) if left else None]]
            targets.extend([[int(stamp), float(value)] for stamp, value in zip(target_times[left:right], amounts[left:right])])
            if targets[-1][0] < end_clock:
                targets.append([end_clock, targets[-1][1]])
            values["target_steps"] = targets
        return values

    def trace_series(self, original, detailed, query):
        spec = original["specification"]
        frozen = spec["strategy"]
        _, index = _symbol(spec, query)
        size = TIMEFRAMES[query.get("tf", "15m")] * 1000
        lo, hi = _window(spec, query, size)
        path = self._paths(detailed).get("strategy_trace")
        if path is None:
            return {"features": [], "series": {}}
        return _trace_window(path, index, lo, hi, size, json.dumps(frozen, sort_keys=True), detailed["id"], spec["dataset_fingerprint"])

    def rationale(self, detailed, rows, index):
        frozen = detailed["specification"].get("strategy", {})
        plots = [plot for plot in frozen.get("plots", []) if plot["labels"]]
        path = self._paths(detailed).get("strategy_trace")
        if not (plots and path and rows):
            return rows
        by_clock = {
            row["available_ms"]: row for row in _scan(path, symbol=index,
                columns=["ms", "available_ms", *[p["name"] for p in plots], *(
                    ["target"] if any(p["name"] == "target" for p in frozen["plots"]) else []
                )]).filter(pl.col("available_ms").is_in(sorted({row["decision"] for row in rows})))
                .collect(engine="streaming").iter_rows(named=True)
        }
        for row in rows:
            observation = by_clock.get(row["decision"])
            if observation is None or observation.get("target") is None or not np.isclose(observation["target"], row["target"]):
                continue
            labels = []
            for plot in plots:
                value = observation[plot["name"]]
                if value is not None:
                    labels.append(str(plot["labels"].get(str(int(value)), value)))
            if labels:
                row["decision_rationale"] = " / ".join(labels)
        return rows

    def feature_series(self, record, query):
        origin = record["outcome"]["execution"].get("exact_replay_of")
        if origin:
            record = self.record(origin)
        verify_provenance(record, features_only=True)
        selection = self.selection(record)
        spec = record["specification"]
        _, index = _symbol(spec, query)
        size = TIMEFRAMES[query.get("tf", "15m")] * 1000
        lo, hi = _window(spec, query, size)
        return self._features(record["id"], spec["dataset_fingerprint"], index, lo, hi, size)

    @lru_cache(maxsize=16)
    def _features(self, job, fingerprint, index, lo, hi, size):
        record = self.record(job)
        selection = self.selection(record)
        feed = MarketDataFeed(selection.dataset, selection.symbols, selection.start, hi)
        feed.read_threads = False
        if (hi - selection.start) // feed.step * len(feed.symbols) > MAX_FEATURE_CELLS:
            raise ValueError("Exact feature warm-up exceeds the on-demand replay cell budget")
        graph = control_handlers._expressions(record["specification"]["parameters"])

        def project():
            for starts, market, status in feed.blocks(shared=True):
                yield data.Block(starts, feed.symbols, starts + feed.step + feed.lag, status,
                                 {name: market[:, :, col] for name, (col, _) in FIELDS.items()},
                                 feed.step, {name: unit for name, (_, unit) in FIELDS.items()}, ("native_feature_projection",))

        series = {name: [] for name in (*graph, "raw_signal")}
        for block in features.evaluate(project(), graph):
            indices = np.flatnonzero((block.timestamps_ms >= lo) & ((block.timestamps_ms + feed.step) % size == 0))
            difference = control_handlers._score(block.values, False)
            for i in indices:
                stamp = int(block.timestamps_ms[i])
                bucket = stamp // size * size
                for name, value in [(name, block.values[name][i, index]) for name in graph] + [("raw_signal", difference[i, index])]:
                    series[name].append([bucket, float(value) if np.isfinite(value) else None, stamp,
                                         int(block.available_at_ms[i])])

        def feature_spec(node):
            if isinstance(node, str):
                return node
            return {"function": node.name, "parameters": json.loads(node.parameters_json),
                    "inputs": [feature_spec(item) for item in node.inputs], "units": node.units}

        descriptors = []
        for i, (label, node) in enumerate(graph.items()):
            definition = feature_spec(node)
            params = definition["parameters"]
            descriptors.append(definition | {
                "name": label, "label": f"{label.title()} {node.name.rsplit('.', 1)[-1].upper()}" + (f" {params['span']}" if "span" in params else ""),
                "display": "line", "pane": "price" if node.units == "USDT/base" else "signal", "color": COLORS[i % len(COLORS)],
            })
        descriptors.append({"name": "raw_signal", "label": "Fast − slow", "function": "difference",
                            "parameters": {"thresholds": [0]}, "units": graph["fast"].units,
                            "display": "line", "pane": "signal", "color": COLORS[2]})
        provenance = {"job": job, "fingerprint": fingerprint, "warmup_start": selection.start,
                      "native_interval": feed.step, "timestamp_columns": ["display_bucket", "value", "source_bar_start", "available_at"],
                      "sampling": "Last native observation in each complete displayed bucket"}
        return {"features": [item | {"provenance": provenance} for item in descriptors], "series": series}

    def request(self, job, resource, query):
        record = self.record(job)
        if resource == "meta":
            return self.meta(record)
        if resource == "candles":
            return self.candles(record, query)
        if resource == "features" and "strategy" not in record["specification"]:
            return self.feature_series(record, query)
        detailed = self._detailed(record, self._latest_replay(job))
        if resource == "features" and "strategy" in record["specification"]:
            if detailed is None:
                raise ValueError("Generate detailed replay to display recorded strategy observations.")
            return self.trace_series(record, detailed, query)
        if detailed is None:
            raise ValueError("Detailed execution data is not available. Generate detailed replay.")
        if resource == "positions":
            return self.positions(record, detailed, query)
        if resource == "series":
            return self.series(record, detailed, query)
        if resource in TABLES:
            return self.events(record, detailed, resource, query)
        raise KeyError(resource)


@lru_cache(maxsize=24)
def _trace_window(path, index, lo, hi, size, frozen_json, job, fingerprint):
    frozen = json.loads(frozen_json)
    plots = frozen["plots"]
    if not plots:
        return {"features": [], "series": {}}
    graph = frozen["graph"]
    scan = _scan(path, lo, hi, index, ["ms", "available_ms", *[plot["name"] for plot in plots]])
    aggregation = []
    for index, plot in enumerate(plots):
        name = plot["name"]
        def sample(column):
            value = pl.col(column)
            return value.last() if name in graph else value.filter(pl.col(name).is_not_null()).last()
        aggregation.extend([sample(name).alias(name), sample("ms").alias(f"__{index}_ms"),
                            sample("available_ms").alias(f"__{index}_available")])
    rows = scan.group_by((pl.col("ms") // size * size).alias("bucket")).agg(*aggregation).sort("bucket")
    result = rows.collect(engine="streaming").to_dicts()
    provenance = {"job": job, "fingerprint": fingerprint, "implementation_hash": frozen["implementation_hash"],
                  "timestamp_columns": ["display_bucket", "value", "source_bar_start", "available_at"],
                  "sampling": "Last recorded value in each display bucket. Non-feature series may be sparse."}
    descriptors = [
        {"function": "recorded_strategy_output", **graph.get(plot["name"], {}), **plot,
         "parameters": graph.get(plot["name"], {}).get("parameters", {}) | {"thresholds": plot["thresholds"]},
         "provenance": provenance}
        for plot in plots
    ]
    return {"features": descriptors, "series": {
        plot["name"]: [[row["bucket"], row[plot["name"]], row[f"__{index}_ms"],
                         row[f"__{index}_available"]] for row in result]
        for index, plot in enumerate(plots)
    }}


def _csv(rows):
    stream = StringIO()
    if rows:
        compact = _compact(rows)
        writer = csv.DictWriter(stream, fieldnames=list(compact[0]))
        writer.writeheader()
        writer.writerows(compact)
    return stream.getvalue()
