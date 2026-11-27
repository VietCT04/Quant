import numpy as np

from research_strategies import adaptive_peer as strategy


def test_online_fit_waits_for_maturity_and_skips_discontinuous_prices():
    p = {"horizon": 3, "decay_days": 20}
    _, model = strategy.initialize(p, tuple(f"S{i}" for i in range(20)), None)
    matrix = np.tile(np.linspace(-1., 1., 20)[:, None], (1, len(strategy.FEATURES)))
    valid = np.ones(20, dtype=bool)
    initial = np.full(20, 100.)
    for i in range(3):
        strategy.update(p, model, i * strategy.STEP, matrix, valid, initial)
        assert model.updates == 0
        assert not model.cross.any()
    later = initial * (1 + .02 * matrix[:, 0])
    strategy.update(p, model, 3 * strategy.STEP, matrix, valid, later)
    assert model.updates == 1
    np.testing.assert_allclose(model.cross, matrix.T @ (.02 * matrix[:, 0]) / 20)
    bad = valid.copy()
    bad[:6] = False
    strategy.update(p, model, 4 * strategy.STEP, matrix, bad, later)
    strategy.update(p, model, 5 * strategy.STEP, matrix, valid, later)
    assert model.updates == 1
