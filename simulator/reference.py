"""Small independent FIFO-free, average-cost ledger for verification."""
from decimal import Decimal


def ledger(wallet, events, final_marks, leverage, maintenance_rate):
    D = lambda x: Decimal(str(x))
    wallet = D(wallet)
    gross_realized = fees = funding = D(0)
    positions = {}
    for kind, symbol, a, b, c in events:
        qty, entry = positions.get(symbol, (D(0), D(0)))
        if kind == "funding":
            payment = -qty * D(a) * D(b)
            funding += payment
            wallet += payment
            continue
        added, price, fee = map(D, (a, b, c))
        total = qty + added
        if qty == 0:
            entry = price
        elif qty * added > 0:
            entry = (qty * entry + added * price) / total
        else:
            closed = min(abs(qty), abs(added))
            earned = (price - entry) * closed * (1 if qty > 0 else -1)
            wallet += earned
            gross_realized += earned
            if total * qty < 0:
                entry = price
        positions[symbol] = total, entry if total else D(0)
        wallet -= fee
        fees += fee
    notionals = [q * D(final_marks[s]) for s, (q, _) in positions.items()]
    unrealized = sum((q * (D(final_marks[s]) - e) for s, (q, e) in positions.items()), D(0))
    gross = sum(map(abs, notionals), D(0))
    return {"wallet": wallet, "realized": gross_realized, "fees": fees, "funding": funding,
            "unrealized": unrealized, "equity": wallet + unrealized, "gross": gross,
            "initial": gross / D(leverage), "maintenance": gross * D(maintenance_rate), "positions": positions}


def simulate(start_times, bars, targets, *, interval, lag, latency, participation):
    """Single-contract reference for continuous quantity, zero costs and no exposure limits."""
    position = 0.0
    pending = None
    output = []
    for time, bar, target in zip(start_times, bars, targets, strict=True):
        price, volume, traded = bar
        observable = time + interval + lag
        if pending is not None and traded and time >= pending[0] + latency:
            decision, notional = pending
            diff = notional / price - position
            capacity = volume * participation
            filled = max(-capacity, min(diff, capacity))
            if filled:
                position += filled
                output.append((decision, time, observable, filled))
            if abs(filled - diff) < 1e-10:
                pending = None
        if target is not None:
            pending = observable, target
    return output
