"""Predeclared Phase 5 mechanism screen and development-only promotion windows."""

import argparse
import json
from pathlib import Path

import quant as q
from quant.control_cli import request
from research_strategies.phase5 import FAMILIES, LEVELS, WINDOWS


PORT = 8785
OUT = Path("data/research/phase5-001")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT")
WINDOW = {
    "A": ("2026-05-12", "2026-05-14"),
    "B": ("2026-06-09", "2026-06-11"),
    "validation": ("2026-07-01", "2026-07-22"),
    "confirmation": ("2026-08-01", "2026-08-15"),
}
BASE_COST = {"rebalance_ticks": 60, "initial_wallet": 100000, "leverage": 1,
             "fee_bps": 5, "spread_bps": 2, "slippage_bps": 1, "participation": .05}
STRESS_COST = BASE_COST | {"fee_bps": 7.5, "spread_bps": 4, "slippage_bps": 2, "impact_bps": 2}
SCREEN_RULE = {"min_ic": .01, "min_ic_observations": 35, "min_feature_coverage": .95,
               "max_per_family": 1, "max_families": 5,
               "rank": "Descending minimum fold IC, then family, lookback and threshold ascending"}
NET_RULE = {"hac_lags": 2, "holm_alpha": .05, "daily_bps_on_gross_floor": 1,
            "gross_usdt": 50000, "max_top_day_share_positive_pnl": .5}
PREDICTIONS = {
    "quiet_shock": "Quiet displacements overstate durable price changes.",
    "flow_persistence": "A sustained aggressor imbalance predicts additional returns in its direction.",
    "absorption": "A muted price reaction to aggressors predicts returns against their direction.",
    "exhaustion": "A high-activity price displacement predicts a partial retracement.",
    "range_break": "An activity-confirmed break of a recent price range persists.",
    "compression": "A price move out of a low-volatility regime persists.",
    "peer_reversal": "Extreme relative returns revert when BTC is not in a displacement regime.",
    "btc_lead": "Alts with higher relative realized volatility respond more to earlier BTC moves.",
    "large_print": "Price displacement caused by concentrated trade activity partially reverses.",
    "flow_divergence": "Aggressive quote value predicts returns when it disagrees with aggressive trade count.",
}


def protocol(source, fold, cost=None):
    start, end = WINDOW[fold]
    sel = q.data.select(q.data.open(source), start=start, end=end, symbols=SYMBOLS)
    cover = sel.dataset.coverage(sel.symbols, sel.start, sel.end).to_pylist()
    days = (sel.end - sel.start) / 86_400_000
    clipped = {symbol: sum(max(0, min(row["end_ms"], sel.end) - max(row["start_ms"], sel.start))
                         for row in cover if row["symbol"] == symbol and row["status"] == "covered")
               / (sel.end - sel.start) for symbol in SYMBOLS}
    if min(clipped.values()) < .98:
        raise ValueError(f"Insufficient audited coverage in {fold}: {clipped}")
    return {"start": sel.start, "end": sel.end, "universe": sel.symbols,
            "dataset_fingerprint": q.data.fingerprint(sel), "seed": 551,
            "evaluation": {"campaign_key": "phase5-001", "fold": fold, "days": days,
                           "horizon": 60, "method": "pearson", "audited_coverage": clipped,
                           "screen_rule": SCREEN_RULE, "net_rule": NET_RULE},
            "costs": cost or BASE_COST}


def candidate(family, window, threshold, kind):
    return {"kind": kind, "parameters": {"family": family, "window": window, "threshold": threshold,
                                        "gross": NET_RULE["gross_usdt"]},
            "strategy": {"id": "phase5_mechanisms", "version": "1.0.0"}}


def experiment(family, fold, kind, protocol_spec, candidates):
    name, definition = FAMILIES[family]
    return {"key": f"{family}-{fold}-{kind}", "name": f"{name} / {fold}",
            "family": family, "mechanism": PREDICTIONS[family], "prediction": PREDICTIONS[family],
            "definition": definition, "stage": "screening" if kind == "screen" else "validation",
            "protocol": protocol_spec, "candidates": candidates}


def build(source):
    screens = {fold: protocol(source, fold) for fold in ("A", "B")}
    plan = {"protocols": {fold: protocol(source, fold) for fold in WINDOW},
            "stress_cost": STRESS_COST}
    campaign = {"campaign": "Phase 5 / Initial mechanisms / PH5-001",
                "hypotheses": [experiment(family, fold, "screen", spec, [
                    candidate(family, window, threshold, "screen")
                    for window in WINDOWS for threshold in LEVELS])
                    for fold, spec in screens.items() for family in FAMILIES]}
    return campaign, plan


def store(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf8")


def read(name):
    return json.loads((OUT / f"{name}.json").read_text(encoding="utf8"))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", default=PORT, type=int)
    cli.add_argument("--submit", action="store_true")
    args = cli.parse_args()
    service = request(args.port, "/service")
    phase5 = [item for item in request(args.port, "/strategies") if item["id"] == "phase5_mechanisms"]
    if len(phase5) != 1:
        raise RuntimeError("Trusted strategy is missing from this service")
    campaign, plan = build(service["coverage"]["source"])
    store("initial-specification", campaign)
    store("frozen-evaluation", plan | {"strategy": phase5[0]})
    if args.submit:
        receipt = request(args.port, "/hypotheses", campaign)
        store("initial-receipt", receipt)
        print(json.dumps(receipt))
    else:
        print(json.dumps({key: item["evaluation"]["audited_coverage"] for key, item in plan["protocols"].items()}))


if __name__ == "__main__":
    main()
