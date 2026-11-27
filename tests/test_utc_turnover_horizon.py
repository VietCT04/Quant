import json

from quant.strategy import prepare


def test_preceding_move_horizon_does_not_advance_information_clock():
    _, plan, _, _ = prepare({"id": "fixed_clock_turnover_handoff", "version": "1.7.0"},
                            {"prior_hours": 12}, rebalance_ticks=1)
    move = plan.features["prior_move"]
    assert move.name == "quant.ts.lag"
    assert json.loads(move.parameters_json)["periods"] == 1
    assert json.loads(move.inputs[0].parameters_json)["periods"] == 12
    assert plan.feature_periods["prior_move"] == 720
