import json

from quant.strategy import prepare


def test_turnover_baseline_uses_only_the_preceding_week_and_optional_yesterday():
    strategy = {"id": "fixed_clock_turnover_handoff", "version": "1.2.0"}
    _, regular, _, _ = prepare(strategy, {}, rebalance_ticks=1)
    _, strict, _, _ = prepare(strategy, {"anchor": "conservative"}, rebalance_ticks=1)
    assert regular.feature_periods["same_clock_quote"] == 720
    assert regular.warmup_bars == 170 * 720
    assert json.loads(regular.features["same_clock_quote"].parameters_json)["periods"] == 168
    strict_inputs = strict.features["same_clock_quote"].inputs
    assert {json.loads(node.parameters_json)["periods"] for node in strict_inputs} == {24, 168}
