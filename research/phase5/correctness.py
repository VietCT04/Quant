"""Known and null five-second signals through the production screen engine."""

from unittest.mock import patch
from types import SimpleNamespace
import json

import numpy as np

import quant as q
from quant.data import Block, Selection, _slice
from simulator.feed import TRADED


def verify():
    rng = np.random.default_rng(551)
    known, null = rng.uniform(-1, 1, size=(2, 4096, 8))
    close = 100 * np.cumprod(1 + .001 * np.vstack([np.zeros((1, 8)), known[:-1]]), axis=0)
    start = 1780272000000
    times = start + 5000 * np.arange(len(known))
    symbols = tuple(f"S{i}" for i in range(8))
    values = {"close": close, "buy_volume": 1 + known, "sell_volume": 1 + null}
    block = Block(times, symbols, times + 5100, np.full(known.shape, TRADED, dtype="u1"),
                  values, 5000, {field: q.data.UNITS[field] for field in values})
    selection = Selection(SimpleNamespace(interval_ms=5000), start, int(times[-1]) + 5000,
                          symbols, tuple(values))

    def source(_, steps, *_args, **_kwargs):
        for first in range(0, len(times), steps):
            yield _slice(block, first, first + steps)

    def candidate(name, field):
        return q.evaluate.ScreenCandidate(name, {}, lambda b: b[field])

    labels = q.evaluate.research_values(q.evaluate.forward_returns(block, 1))
    np.testing.assert_allclose(labels[:-1], .001 * known[:-1], rtol=1e-9, atol=1e-14)
    assert np.isnan(labels[-1]).all()
    with patch("quant.data._iter_blocks", source):
        screens = [q.evaluate.screen(selection, [
            candidate("known", "buy_volume"), candidate("null", "sell_volume"),
        ], horizon=1, method="pearson", block_steps=steps, workers=1) for steps in (113, 1024)]
    first, second = [item["results"] for item in screens]
    for name in ("known", "null"):
        assert first[name]["ic_observations"] == 4095
        np.testing.assert_allclose(first[name]["mean_ic"], second[name]["mean_ic"], atol=1e-14)
    assert first["known"]["mean_ic"] > .9999
    assert abs(first["null"]["mean_ic"]) < .025
    np.testing.assert_allclose(q.validation.adjust_pvalues([.01, .02, .5]), [.03, .04, .5])
    return first


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
