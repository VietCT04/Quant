import numpy as np

from research_strategies.shock_lead_lag import STRATEGY, scores


def test_propagation_uses_delayed_response_and_signed_btc_shock():
    p = {name: field.default for name, field in STRATEGY.parameters.items()}
    valid = np.array([True, True, True, False])
    day = previous = np.zeros(4)
    upper = np.array([.03, .005, .028, -.03])
    score = scores(p, 0, upper, previous, day, valid)
    np.testing.assert_allclose(score[1], .025)
    assert np.isnan(score[[0, 2, 3]]).all()
    np.testing.assert_allclose(scores(p | {"side": -1}, 0, -upper, previous, -day, valid), score)
    assert np.isnan(scores(p, None, upper, previous, day, valid)).all()


def test_resilience_selection_requires_observed_broad_selloff_and_btc_turn():
    p = {name: field.default for name, field in STRATEGY.parameters.items()} | {"mode": "resilience"}
    valid = np.ones(20, dtype=bool)
    prior = np.full(20, -.04)
    prior[1] = -.015
    hour = np.full(20, .003)
    score = scores(p, 0, hour, prior, prior, valid)
    assert np.flatnonzero(np.isfinite(score)).tolist() == [1]
    hour[0] *= -1
    assert np.isnan(scores(p, 0, hour, prior, prior, valid)).all()
