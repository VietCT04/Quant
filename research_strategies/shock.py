"""Price shocks with an actual-position holding limit and flat cooldown."""

import numpy as np

import quant as q
from quant.strategy import Decision, Parameter, Plan, Plot, Strategy


def prepare(p):
    return Plan({"shock": q.features.expression("quant.ts.pct_change", "close", periods=1, units="fraction")},
                warmup_bars=1, decision_ticks=1, plots=(
        Plot("shock", "One-bar return", "fraction", thresholds=(-p["threshold"], p["threshold"])),
        Plot("reason", "Decision reason", "code", labels={1: "Fade shock", 2: "Maximum hold", 3: "Cooldown"}),
        Plot("target", "Requested target", "USDT", color="#91aafa"),
    ))


def initialize(p, symbols, rng):
    return dict(p), np.zeros(len(symbols), dtype=bool), np.zeros(len(symbols), dtype="i8"), np.zeros(len(symbols), dtype="i8")


def signal(state, block):
    return -block.values["shock"]


def decide(state, block, portfolio):
    p, was_held, opened, cooldown = state
    now = int(block.available_at_ms[0])
    held = portfolio.positions["qty"] != 0
    opened[held & ~was_held] = portfolio.clock_ms
    cooldown[~held & was_held] = now + p["cooldown_bars"] * block.interval_ms
    was_held[:] = held
    returns = block.values["shock"][0]
    flat = ~held
    enter = flat & (now >= cooldown) & (np.abs(returns) >= p["threshold"])
    exit_ = held & (now - opened >= p["hold_bars"] * block.interval_ms)
    target = np.where(exit_, 0, np.where(enter, -np.sign(returns) * p["notional"], np.nan))
    reason = np.where(exit_, 2., np.where(enter, 1., np.where(flat & (now < cooldown), 3., np.nan)))
    return Decision(target[None], {"reason": reason[None]})


STRATEGY = Strategy("shock_reversal", "1.0.0", {
    "threshold": Parameter("number", .0004, .00001, .1),
    "hold_bars": Parameter("integer", 12, 1, 17280),
    "cooldown_bars": Parameter("integer", 4, 0, 17280),
    "notional": Parameter("number", 300, 1, 1_000_000),
}, ("close",), prepare, initialize, signal=signal, decide=decide)
