"""Phase 5 initial campaign. Causal five-second features with five-minute decisions."""

import numpy as np

import quant as q
from quant.strategy import Decision, Parameter, Plan, Plot, Strategy


FAMILIES = {
    "quiet_shock": ("Quiet price shock reversal", "Fade normalized price displacement of at least k when relative quote volume is below 1."),
    "flow_persistence": ("Aggressor-flow persistence", "Follow mean signed aggressive quote imbalance when its magnitude is at least 0.15k."),
    "absorption": ("Muted response to aggressive flow", "Fade mean aggressive quote imbalance of at least 0.15k when normalized price displacement is no more than 0.5 in magnitude."),
    "exhaustion": ("High-volume exhaustion", "Fade a displacement of at least 0.5 standard deviations when current quote volume is at least 1+k times its prior mean."),
    "range_break": ("Activity-confirmed range breakout", "Follow the normalized breakout beyond the prior high or low by at least 0.1k when relative quote volume exceeds 1."),
    "compression": ("Volatility compression breakout", "Follow normalized price displacement of at least k when trailing volatility divided by slow volatility is below 0.8."),
    "peer_reversal": ("Peer relative-value reversal", "Fade cross-sectionally standardized trailing return of at least k in magnitude when absolute BTC displacement is below 1."),
    "btc_lead": ("BTC-to-alt lagged response", "Follow lagged BTC displacement of at least k, weighted by each alt's relative trailing volatility. BTC shares the hedge allocation."),
    "large_print": ("Concentrated print reversal", "Fade a displacement of at least 0.5 standard deviations when the largest trade is at least 0.1k of current bar base volume."),
    "flow_divergence": ("Value versus count aggressor divergence", "Follow the difference of trailing signed quote imbalance and signed count imbalance when their signs oppose and the spread magnitude is at least 0.15k."),
}

WINDOWS = (6, 12, 24, 60)
LEVELS = (0.5, 1.0, 1.5)
TICKS = 60
FIELDS = ("close", "high", "low", "volume", "quote_volume", "buy_quote_volume",
          "sell_quote_volume", "buy_trade_count", "sell_trade_count", "max_trade_qty")


def prepare(p):
    n = p["window"]
    family = p["family"]
    e = q.features.expression
    change = e("quant.ts.pct_change", "close", units="fraction", periods=n)
    move = e("quant.ts.pct_change", "close", units="fraction", periods=1)
    sd = e("quant.ts.rolling_std", move, units="fraction", window=120)
    prior_sd = e("quant.ts.lag", sd, units="fraction", periods=1)
    relative_volume = e("quant.ts.lag", e("quant.ts.rolling_mean", "quote_volume",
                        units="USDT", window=n), units="USDT", periods=1)
    flow = e("quant.microstructure.quote_volume_imbalance", "buy_quote_volume",
             "sell_quote_volume", units="fraction")
    count = e("quant.microstructure.trade_count_imbalance", "buy_trade_count",
              "sell_trade_count", units="fraction")
    graph = {}
    if family in {"quiet_shock", "absorption", "exhaustion", "compression", "peer_reversal", "large_print"}:
        graph.update(trailing_return=change, slow_volatility=prior_sd)
    if family in {"quiet_shock", "exhaustion", "range_break"}:
        graph["prior_quote_volume"] = relative_volume
    if family in {"flow_persistence", "absorption", "flow_divergence"}:
        graph["quote_imbalance"] = e("quant.ts.rolling_mean", flow, units="fraction", window=n)
    if family == "flow_divergence":
        graph["count_imbalance"] = e("quant.ts.rolling_mean", count, units="fraction", window=n)
    if family == "range_break":
        for alias, source, operation in (("prior_high", "high", "max"), ("prior_low", "low", "min")):
            graph[alias] = e("quant.ts.lag", e(f"quant.ts.rolling_{operation}", source,
                             units="USDT/base", window=n), units="USDT/base", periods=1)
        graph["slow_volatility"] = prior_sd
    if family in {"compression", "btc_lead"}:
        graph["fast_volatility"] = e("quant.ts.rolling_std", move, units="fraction", window=n)
    if family == "btc_lead":
        graph.update(prior_return=e("quant.ts.lag", change, units="fraction", periods=1),
                     slow_volatility=prior_sd)
    plots = tuple(Plot(alias, alias.replace("_", " ").capitalize(), expr.units)
                  for alias, expr in graph.items())
    return Plan(graph, warmup_bars=122, decision_ticks=TICKS,
                plots=plots + (Plot("score", "Gated forecast", "score"),
                               Plot("target", "Requested net position", "USDT")))


def initialize(p, symbols, rng):
    return {**p, "btc_index": symbols.index("BTCUSDT")}


def signal(p, block):
    v = block.values
    family, k, n = p["family"], p["threshold"], p["window"]
    divide = q.math.safe_divide
    if "trailing_return" in v and "slow_volatility" in v:
        z = divide(v["trailing_return"], v["slow_volatility"] * np.sqrt(n))
    if "prior_quote_volume" in v:
        rv = divide(v["quote_volume"], v["prior_quote_volume"])
    if family == "quiet_shock":
        raw, gate, valid = -z, (abs(z) >= k) & (rv < 1), np.isfinite(rv)
    elif family == "flow_persistence":
        raw, valid = v["quote_imbalance"], True
        gate = abs(raw) >= .15 * k
    elif family == "absorption":
        raw, valid = -v["quote_imbalance"], np.isfinite(z)
        gate = (abs(raw) >= .15 * k) & (abs(z) <= .5)
    elif family == "exhaustion":
        raw, gate, valid = -z, (abs(z) >= .5) & (rv >= 1 + k), np.isfinite(rv)
    elif family == "range_break":
        close = v["close"]
        excess = np.maximum(close - v["prior_high"], 0) + np.minimum(close - v["prior_low"], 0)
        raw = divide(divide(excess, close), v["slow_volatility"])
        gate, valid = (abs(raw) >= .1 * k) & (rv > 1), np.isfinite(rv)
    elif family == "compression":
        ratio = divide(v["fast_volatility"], v["slow_volatility"])
        raw, gate, valid = z, (abs(z) >= k) & (ratio < .8), np.isfinite(ratio)
    elif family == "peer_reversal":
        btc = z[:, p["btc_index"], None]
        raw = -q.cs.zscore(v["trailing_return"], min_observations=4)
        # Identical peer returns have no relative direction.
        raw = np.where(np.isfinite(v["trailing_return"]) & np.isnan(raw)
                       & (np.isfinite(v["trailing_return"]).sum(axis=1, keepdims=True) >= 4), 0, raw)
        gate, valid = (abs(raw) >= k) & (abs(btc) < 1), np.isfinite(btc)
    elif family == "btc_lead":
        i = p["btc_index"]
        leader = divide(v["prior_return"][:, i:i + 1], v["slow_volatility"][:, i:i + 1] * np.sqrt(n))
        raw = leader * divide(v["fast_volatility"], v["slow_volatility"])
        raw[:, i] = np.where(np.isfinite(raw[:, i]), 0, np.nan)
        gate, valid = abs(leader) >= k, np.isfinite(leader)
    elif family == "large_print":
        share = divide(v["max_trade_qty"], v["volume"])
        raw, gate, valid = -z, (abs(z) >= .5) & (share >= .1 * k), np.isfinite(share)
    else:
        flow, count = v["quote_imbalance"], v["count_imbalance"]
        raw = flow - count
        gate, valid = (flow * count < 0) & (abs(raw) >= .15 * k), True
    return np.where(np.isfinite(raw) & valid & block.valid["close"], np.where(gate, raw, 0), np.nan)


def targets(p, block):
    score = signal(p, block)
    weights = q.cs.long_short_normalize(q.cs.demean(score, min_observations=4))
    return Decision(np.where(np.isfinite(weights), weights * p["gross"], 0), {"score": score})


def screen(p, block):
    score = signal(p, block)
    selected = (block.timestamps_ms % (TICKS * block.interval_ms)) == 0
    # Nondecision rows remain zero for the SDK coverage calculation and contribute no IC.
    return np.where(selected[:, None] | ~np.isfinite(score), score, 0.0)


STRATEGY = Strategy("phase5_mechanisms", "1.0.0", {
    "family": Parameter("string", choices=tuple(FAMILIES)),
    "window": Parameter("integer", choices=WINDOWS),
    "threshold": Parameter("number", choices=LEVELS),
    "gross": Parameter("number", 50_000, 100, 100_000),
}, FIELDS, prepare, initialize, signal=screen, targets=targets,
    capabilities=("quant.ts.pct_change", "quant.ts.lag", "quant.ts.rolling_mean",
                  "quant.ts.rolling_std", "quant.ts.rolling_max", "quant.ts.rolling_min",
                  "quant.microstructure.quote_volume_imbalance", "quant.microstructure.trade_count_imbalance"))
