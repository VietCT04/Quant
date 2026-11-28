"""Reconcile public one-way fills and lifecycle reports without bridging archive gaps."""

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from math import isclose, isfinite


def near(a, b):
    return isclose(a, b, rel_tol=1e-7, abs_tol=1e-8)


def cost_near(a, b):
    return isclose(a, b, rel_tol=3e-6, abs_tol=5e-9)


@dataclass(frozen=True)
class Report:
    key: str
    sign: int
    start: int
    end: int
    closed: bool
    peak: float
    turnover: float
    average: float | None

    @classmethod
    def read(cls, key, record):
        closed = record["status"] == "All Closed" and bool(record.get("closed"))
        return cls(key, 1 if record["side"] == "Long" else -1, record["opened"],
                   record["closed"] if closed else record["updateTime"], closed,
                   record["maxOpenInterest"], record["closedVolume"], record.get("avgCost"))


def tag(row, kind, reports, certainty, evidence):
    report = reports[-1]
    row.update(type=kind, position=report.key, positions=[p.key for p in reports],
               direction="long" if report.sign > 0 else "short", action_certainty=certainty,
               action_evidence=evidence)


def anchor(row, before, after, certainty, evidence):
    row.update(before=before, after=after, quantity_certainty=certainty, quantity_evidence=evidence)


def costs(row, before, after, certainty, evidence):
    if before is not None:
        row.update(entry_before=before, entry_before_certainty=certainty)
    if after is not None:
        row.update(entry_after=after, entry_certainty=certainty, entry_evidence=evidence)


def action_for(report, leg):
    direction = "long" if report.sign > 0 else "short"
    return {"open": f"{direction}_entry", "add": "scale_in", "reduce": "scale_out",
            "close": f"{direction}_exit"}[leg]


def report_links(rows, reports, times):
    inside, heads, tails, boundary = (defaultdict(list) for _ in range(4))
    report_audit = Counter()
    for report in reports.values():
        left = bisect_left(times, report.start - 1)
        right = bisect_right(times, report.end + 1)
        if left == right:
            report_audit["no_orders_in_report"] += 1
            continue
        for i in range(bisect_right(times, report.start + 1),
                       bisect_left(times, report.end - 1) if report.closed else bisect_right(times, report.end)):
            inside[i].append(report)
        window_audit = []
        for time, sign, index in [(report.start, report.sign, heads)] + (
                [(report.end, -report.sign, tails)] if report.closed else []):
            matches = range(bisect_left(times, time - 1), bisect_right(times, time + 1))
            for i in matches:
                boundary[i].append(report)
            if len(matches) == 1 and rows[matches.start]["filled"] * sign > 0:
                index[matches.start].append(report)
            elif matches:
                window_audit.append("boundary_collision_or_wrong_side")
            else:
                window_audit.append("missing_boundary_fill")
        report_audit.update(set(window_audit))
    return inside, heads, tails, boundary, report_audit


def classify(rows, reports, by_index, links):
    inside, heads, tails, boundary, _ = links
    for i, row in enumerate(rows):
        row.update(action_certainty="unknown", action_evidence="No matching position report",
                   quantity_certainty="unknown", entry_certainty="unknown", entry_before_certainty="unknown")
        if row["reported_pnl"]:
            row["realizing_direction"] = "short" if row["filled"] > 0 else "long"
        leg = by_index.get(i, ())
        opening, closing, interior = heads[i], tails[i], inside[i]
        if len(opening) == len(closing) == 1 and not interior and opening[0].sign == -closing[0].sign:
            old, new = closing[0], opening[0]
            tag(row, "reversal", [old, new], "inferred", "Unique fill at opposite reported close and open")
            # Either independently reconciled leg identifies the other quantity of this single order.
            matches = [a for a in leg if (a[0], a[2]["action"]) in [(old.key, "close"), (new.key, "open")]]
            qty = abs(row["filled"])
            splits = [a[2]["size"] / row["price"] if a[0] == old.key else qty - a[2]["size"] / row["price"]
                      for a in matches]
            if splits and all(near(v, splits[0]) for v in splits):
                close_qty, open_qty = splits[0], qty - splits[0]
                if (close_qty > 1e-8 and open_qty > 1e-8 and
                        (close_qty <= old.peak or near(close_qty, old.peak)) and
                        (open_qty <= new.peak or near(open_qty, new.peak))):
                    tag(row, "reversal", [old, new], "confirmed", "Opposite reports and a reconciled reversal leg")
                    anchor(row, old.sign * close_qty, new.sign * open_qty, "confirmed", row["action_evidence"])
                    prior = next((entry_from_path(a[2], old, row["price"]) for a in matches if a[0] == old.key), None)
                    costs(row, prior, row["price"], "confirmed", row["action_evidence"])
            continue
        if len(leg) == 1:
            key, sign, action, price = leg[0]
            if near(action["size"], row["filled_notional"]) and (
                    len({p.key for p in boundary[i] + interior} - {key}) == 0):
                report = reports[key]
                after = sign * action["booked_notional"] / price if action["action"] != "close" else 0.
                before = after - row["filled"]
                if action["action"] == "open":
                    before = 0.
                evidence = "Signed fills, closed volume and reported peak reconciled"
                tag(row, action_for(report, action["action"]), [report], "confirmed", evidence)
                anchor(row, before, after, "confirmed", evidence)
                prior = entry_from_path(action, report, price)
                nxt = ((abs(before) * prior + row["filled_notional"]) / abs(after)
                       if prior is not None and action["action"] == "add" else
                       price if action["action"] == "open" else prior if after else None)
                costs(row, prior, nxt, "confirmed", evidence)
                continue
        if boundary[i]:
            if len({p.key for p in boundary[i] + interior}) == 1 and len(opening) + len(closing) == 1:
                report = (opening or closing)[0]
                if closing or row["reported_pnl"] == 0:
                    tag(row, action_for(report, "open" if opening else "close"), [report], "inferred",
                        "Unique side-consistent fill at the reported position boundary")
                    continue
            row["action_evidence"] = "Ambiguous position boundary or possible unpaired reversal"
        elif len(interior) == 1:
            report = interior[0]
            same_side = row["filled"] * report.sign > 0
            if not same_side or row["reported_pnl"] == 0:
                tag(row, "scale_in" if same_side else "scale_out", [report], "inferred",
                    "Signed fill within a single reported long or short lifecycle")
            else:
                row["action_evidence"] = "Realized P&L conflicts with reported position direction"
        elif interior:
            row["action_evidence"] = "Overlapping position reports"


def entry_from_path(action, report, price):
    distance = action["from_cost_bps"]
    return price / (1 + report.sign * distance / 10_000) if distance is not None else None


def covered_runs(rows, coverage):
    """Only certified order windows can link adjacent orders. Ties are unordered."""
    window = iter(coverage)
    lo, hi = next(window, (0, 0))
    run = []
    for i, row in enumerate(rows):
        time = row["ms"]
        while hi and time >= hi:
            if run:
                yield run, hi
                run = []
            lo, hi = next(window, (0, 0))
        tie = (i > 0 and rows[i-1]["ms"] == time or i+1 < len(rows) and rows[i+1]["ms"] == time)
        if lo <= time < hi and not tie:
            run.append(row)
        else:
            if run:
                yield run, time
                run = []
            row["inventory_issue"] = "Same-millisecond fill ordering unavailable" if tie else "Order coverage incomplete"
    if run:
        yield run, hi


def lifecycle_runs(run, through):
    segment = []
    key = None
    for i, row in enumerate(run):
        links = row["positions"]
        if segment and (not links or key != links[0]):
            yield key, segment, row["ms"]
            segment = []
        if not links:
            continue
        if not segment:
            key = links[0]
        if row["type"] == "reversal" and row.get("before") is None:
            if segment:
                yield key, segment, row["ms"]
            key, segment = links[-1], []
            continue
        segment.append(row)
        if row["type"] == "reversal":
            yield key, segment, row["ms"]
            key, segment = links[-1], [row]
        elif row["type"] in {"long_exit", "short_exit"}:
            yield key, segment, through if i + 1 == len(run) else run[i + 1]["ms"]
            segment = []
    if segment:
        yield key, segment, through


def pnl_entry(row, report):
    if row["type"] != "scale_out" or not row["reported_pnl"]:
        return None
    value = row["price"] - row["reported_pnl"] / (report.sign * abs(row["filled"]))
    return value if isfinite(value) and value > 0 else None


def snapshot_evidence(segment, snapshots, prefix, through):
    """A rise in reported lifetime peak identifies a new peak inside a covered interval."""
    times = [r["ms"] for r in segment]
    candidates, cost_checks = [], []
    for earlier, later in zip(snapshots, snapshots[1:]):
        if not (times[0] <= earlier.end < later.end < through):
            continue
        begin, end = bisect_right(times, earlier.end), bisect_right(times, later.end)
        if begin == end:
            continue
        reduced = sum(max(0., prefix[i] - prefix[i+1]) for i in range(begin, end))
        if later.peak > earlier.peak and not near(later.peak, earlier.peak) and near(
                reduced, later.turnover - earlier.turnover):
            candidates.append(later.peak - max(prefix[begin+1:end+1]))
    for state in snapshots:
        if times[0] <= state.end < through and state.average and not state.closed:
            cost_checks.append((bisect_right(times, state.end), state.average))
    return candidates, cost_checks


def path_quantities(segment, report, through, snapshots):
    """Solve a single continuous signed path from independently checked checkpoints."""
    prefix = [0.]
    hard, soft = [], []
    completed_open = False
    completed_close = False
    for i, row in enumerate(segment):
        before, after = row.get("before"), row.get("after")
        entering = i == 0 and row["ms"] <= report.start + 1 and row["type"] in {
            "long_entry", "short_entry", "reversal"}
        leaving = report.closed and i == len(segment) - 1 and row["ms"] >= report.end - 1 and row["type"] in {
            "long_exit", "short_exit", "reversal"}
        completed_open |= entering
        completed_close |= leaving
        if before is not None and not (entering and row["type"] == "reversal"):
            hard.append(report.sign * before - prefix[-1])
        if entering and row["type"] == "reversal":
            if after is None:
                return "Unallocated reversal"
            delta = report.sign * after
        elif leaving and row["type"] == "reversal":
            if before is None:
                return "Unallocated reversal"
            delta = -report.sign * before
        else:
            delta = report.sign * row["filled"]
        prefix.append(prefix[-1] + delta)
        if after is not None and not (leaving and row["type"] == "reversal"):
            hard.append(report.sign * after - prefix[-1])
        if entering:
            soft.append(0.)
        if leaving:
            soft.append(-prefix[-1])
    reductions = sum(a - b for a, b in zip(prefix, prefix[1:]) if b < a)
    whole = completed_open and (completed_close or (not report.closed and through > report.end))
    if (reductions > report.turnover and not near(reductions, report.turnover)) or (
            whole and not near(reductions, report.turnover)):
        return "Signed reductions conflict with reported closed volume"
    # Cost candidates may use checkpoint ratios only where independent observations agree with
    # a weighted-average path. Never assume totalPnl is interchangeable with gross P&L.
    observations = []
    for i, row in enumerate(segment):
        value = pnl_entry(row, report)
        if value is not None:
            observations.append((i, value))
    snapshot_anchors, snapshot_costs = snapshot_evidence(segment, snapshots, prefix, through)

    def fit(base):
        values = [base + delta for delta in prefix]
        lower = 0 if completed_open else None
        upper = len(values)-1 if completed_close else None
        if any(q <= 1e-8 for i, q in enumerate(values) if i not in (lower, upper)):
            return None
        if any(q < -1e-8 or q > report.peak and not near(q, report.peak) for q in values):
            return None
        if whole and not near(max(values), report.peak):
            return None
        return [0. if abs(q) <= 1e-8 else q for q in values]

    certainty = "inferred"
    evidence = "Signed fills from a uniquely matched reported position boundary"
    if hard:
        if not all(near(a, hard[0]) for a in hard + soft + snapshot_anchors):
            return "Conflicting quantity checkpoints"
        fitted = fit(hard[0])
        certainty = "confirmed"
        evidence = "Signed fills linked to a reconciled quantity checkpoint"
    elif snapshot_anchors:
        if not all(near(a, snapshot_anchors[0]) for a in snapshot_anchors + soft):
            return "Conflicting inventory peak checkpoints"
        fitted = fit(snapshot_anchors[0])
        certainty = "confirmed"
        evidence = "New published inventory peak and closed-volume change reconciled between snapshots"
    elif soft:
        if not all(near(a, soft[0]) for a in soft):
            return "Conflicting opening and closing boundaries"
        fitted = fit(soft[0])
    else:
        # Public orders and position reports give the observed quantity precision.
        # Recheck the rounded estimate against every cost checkpoint.
        quantities = [row["filled"] for row in segment] + [report.peak, report.turnover]
        places = max(0, *(-Decimal(str(value)).normalize().as_tuple().exponent for value in quantities))
        fitted = infer_from_cost(segment, prefix, sorted(observations + snapshot_costs), fit, places)
        if fitted is None:
            return ("Weighted-cost observations fail independent reconciliation" if len(observations + snapshot_costs) >= 3
                    else "No independent quantity checkpoint")
        evidence = "Consistent weighted-cost checkpoints and signed fills"
    if fitted is None:
        return "Signed quantities violate reported position direction or peak"
    for i, row in enumerate(segment):
        # Preserve a reversal's independent other leg.
        if row.get("after") is None:
            anchor(row, report.sign * fitted[i], report.sign * fitted[i+1], certainty, evidence)
    reconcile_costs(segment, fitted, sorted(observations + snapshot_costs))
    return None


def cost_path(segment, qty, start, value):
    """Weighted entry at each boundary, from a known entry at one boundary."""
    path = {start: value}
    for i in range(start, len(segment)):
        if qty[i+1] <= 0:
            break
        q = qty[i+1] - qty[i]
        path[i+1] = (path[i] * qty[i] + q * segment[i]["price"]) / qty[i+1] if q > 0 else path[i]
    for i in range(start-1, -1, -1):
        if qty[i] <= 0 or qty[i+1] <= 0:
            break
        q = qty[i+1] - qty[i]
        path[i] = (path[i+1] * qty[i+1] - q * segment[i]["price"]) / qty[i] if q > 0 else path[i+1]
    if any(not isfinite(c) or c <= 0 for c in path.values()):
        return {}
    return path


def infer_from_cost(segment, prefix, checks, fit, places):
    if len(checks) < 3:
        return None
    # Different fills in the same cost epoch corroborate entry prices, but only distinct
    # add-separated epochs give independent information about the starting quantity.
    epochs = [0]
    for a, b in zip(prefix, prefix[1:]):
        epochs.append(epochs[-1] + (b > a))
    by_epoch = {}
    for i, value in checks:
        reference = by_epoch.setdefault(epochs[i], value)
        # Both observations could be within tolerance of the same fitted cost.
        if not isclose(reference, value, rel_tol=6.1e-6, abs_tol=1.01e-8):
            return None
    if len(by_epoch) < 3:
        return None
    for (a, older), (b, newer) in zip(checks, checks[1:]):
        if cost_near(older, newer):
            continue
        adds = [i for i in range(a, b) if prefix[i+1] > prefix[i]]
        if not adds or adds[-1] - adds[0] + 1 != len(adds):
            continue
        add_size = prefix[adds[-1]+1] - prefix[adds[0]]
        cash = sum((prefix[i+1] - prefix[i]) * segment[i]["price"] for i in adds)
        if abs(newer - older) < max(newer, older) * 3e-4:
            continue  # Numerically ill-conditioned at public P&L precision.
        start = (cash - newer * add_size) / (newer - older) - prefix[adds[0]]
        fitted = fit(round(start, places))
        if fitted is None:
            continue
        predicted = cost_path(segment, fitted, a, older)
        if all(i in predicted and cost_near(predicted[i], observed) for i, observed in checks):
            return fitted
    return None


def reconcile_costs(segment, qty, checks):
    known = {}
    for i, row in enumerate(segment):
        if qty[i] and row.get("entry_before") is not None and not (i == 0 and row["type"] == "reversal"):
            known[i] = row["entry_before"]
        if qty[i+1] and row.get("entry_after") is not None:
            known[i+1] = row["entry_after"]
    if qty[0] == 0 and qty[1] > 0:
        known[1] = segment[0]["price"]
    evidence = "Weighted entry from a reconciled position boundary"
    if known:
        path = cost_path(segment, qty, *next(iter(known.items())))
        if any(i in path and not cost_near(path[i], value) for i, value in known.items()):
            return
    else:
        if len({i for i, _ in checks}) < 2:
            return
        path = cost_path(segment, qty, *checks[0])
        if not all(i in path and cost_near(path[i], value) for i, value in checks):
            return
        evidence = "Weighted entry corroborated by multiple public cost observations"
    for i, row in enumerate(segment):
        before = path.get(i) if qty[i] > 0 and row["entry_before_certainty"] == "unknown" else None
        after = path.get(i+1) if qty[i+1] > 0 and row["entry_certainty"] == "unknown" else None
        certainty = "confirmed" if known and row.get("quantity_certainty") == "confirmed" else "inferred"
        costs(row, before, after, certainty, evidence)


def reconstruct_public(rows, lookup, coverage, allocations, snapshots=None):
    times = [row["ms"] for row in rows]
    reports = {key: Report.read(key, record) for key, record in lookup.items()}
    links = report_links(rows, reports, times)
    classify(rows, reports, allocations, links)
    snapshots = snapshots or {}
    for run, right in covered_runs(rows, coverage):
        for key, segment, stop in lifecycle_runs(run, right):
            report = reports[key]
            history = {r.end: r for r in snapshots.get(key, ()) if r.start == report.start and r.sign == report.sign
                       and r.end < report.end}
            history[report.end] = report
            issue = path_quantities(segment, report, stop, [history[t] for t in sorted(history)])
            for row in segment:
                if row.get("after") is None:
                    row["inventory_issue"] = issue or "Inventory unavailable"
                elif row["type"] != "reversal" or key == row.get("position"):
                    ending = min(stop, report.end + 1)
                    if row["after"] == 0:
                        ending = stop
                    row["inventory_until"] = max(row["ms"] + 1, ending)
    for i, row in enumerate(rows):
        if "after" not in row:
            row.setdefault("inventory_issue", "No usable position link")
            continue
        row["provenance"] = "Reported execution; " + row["quantity_evidence"]
        end = row.get("inventory_until", row["ms"] + 1)
        if i + 1 < len(rows):
            end = min(end, rows[i+1]["ms"])
        row["inventory_until"] = end
        row["before_notional"] = row["before"] * row["price"]
        row["after_notional"] = row["after"] * row["price"]
        if row.get("entry_after") is not None:
            row["unrealized"] = row["after"] * (row["price"] - row["entry_after"])
        elif row["after"] == 0:
            row["unrealized"] = 0.
    return links[-1]
