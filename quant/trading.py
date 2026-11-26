"""Optional order intents and sizing policies for the version 2 strategy callback."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Order:
    symbol: str
    kind: int
    size: float


class Orders:
    """An ordered collection of market intents. The native engine validates and sizes fills."""

    def __init__(self, symbols):
        self.symbols = tuple(symbols)
        self.items = []

    def _add(self, symbol, kind, size):
        if symbol not in self.symbols:
            raise ValueError(f"Order symbol {symbol!r} is outside the selected universe")
        self.items.append(Order(symbol, kind, float(size)))

    def _side(self, symbol, side, notional, quantity):
        if (notional is None) == (quantity is None):
            raise ValueError("Supply exactly one of notional or quantity")
        value = float(notional if quantity is None else quantity)
        self._add(symbol, 0 if quantity is None else 1, side * value if value > 0 else math.nan)

    def buy(self, symbol, *, notional=None, quantity=None):
        """Submit a buy with fixed requested quote notional or base quantity."""
        self._side(symbol, 1, notional, quantity)

    def sell(self, symbol, *, notional=None, quantity=None):
        """Submit a sell with fixed requested quote notional or base quantity."""
        self._side(symbol, -1, notional, quantity)

    def close(self, symbol):
        """Flatten the actual signed quantity at the execution event."""
        self._add(symbol, 2, 0)

    def reduce(self, symbol, *, fraction):
        """Reduce actual exposure by a fraction in (0, 1]."""
        self._add(symbol, 3, fraction)

    def close_all(self, positions):
        """Submit closes for the booked positions in the supplied universe order."""
        for symbol, quantity in zip(self.symbols, positions["qty"], strict=True):
            if quantity:
                self.close(symbol)

    def rebalance(self, symbol, *, target, observable_price, booked_quantity, tolerance=0):
        """Request an explicit approximate notional adjustment using an observable price."""
        change = target - observable_price * booked_quantity
        if abs(change) > tolerance:
            self._side(symbol, 1 if change > 0 else -1, abs(change), None)


def proportional_size(portfolio, fraction, *, margin_aware=False):
    """Size from current equity. Optionally cap the request to estimated free margin."""
    if not math.isfinite(fraction) or fraction < 0:
        raise ValueError("Sizing fraction must be finite and nonnegative")
    amount = portfolio.equity * fraction
    if margin_aware:
        amount = min(amount, portfolio.available_margin)
    return max(0.0, amount)


def exit_trigger(observed_close, entry, quantity, *, stop_loss=None, take_profit=None, trailing_reference=None,
                 trailing_stop=None):
    """Return a close reason from completed bar prices; the order executes at a future open."""
    if not quantity or not (entry > 0 and math.isfinite(observed_close)):
        return None
    side = 1 if quantity > 0 else -1
    pnl = side * (observed_close / entry - 1)
    if stop_loss is not None and pnl <= -stop_loss:
        return "stop_loss"
    if take_profit is not None and pnl >= take_profit:
        return "take_profit"
    if trailing_stop is not None and trailing_reference is not None and trailing_reference > 0:
        if side * (observed_close / trailing_reference - 1) <= -trailing_stop:
            return "trailing_stop"
    return None


__all__ = ["proportional_size", "exit_trigger"]
