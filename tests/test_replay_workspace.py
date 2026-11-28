from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from quant import data
from quant.replay_workspace import MARKET, ReplayWorkspace, SymbolTape, add_native_fields
from quant.validation import RESEARCH_START


def event(id, ms, before, after, position, kind, **extras):
    return {"id": id, "ms": ms, "price": 10., "side": "buy" if before < after else "sell",
            "filled": after - before, "filled_notional": abs(after - before) * 10,
            "before": before, "after": after, "position": position, "positions": [position],
            "type": kind, **extras}


def test_same_clock_position_step_and_true_replay_prefix():
    tape = SymbolTape([
        event("a", 100, 0, 2, "p1", "long_entry"),
        event("b", 100, 2, 3, "p1", "scale_in"),
        event("c", 100, 3, 1, "p1", "scale_out"),
        event("d", 105, 1, -1, "p2", "reversal"),
    ], [{"id": "p1", "side": "long", "net_pnl": 4, "exit_ms": 105}, {"id": "p2", "side": "short"}])
    assert tape.navigate({"directionStep": "next", "id": "a"})["row"]["id"] == "b"
    assert tape.navigate({"directionStep": "previous", "id": "c"})["row"]["id"] == "b"
    assert tape.navigate({"directionStep": "nextPosition", "id": "b"})["row"]["id"] == "d"
    assert tape.navigate({"directionStep": "previousPosition", "id": "d"})["row"]["id"] == "a"
    assert [r["id"] for r in tape.page({"clock": 100, "selected": "b"})["rows"]] == ["a", "b"]
    assert tape.page({"clock": 100, "selected": "b"})["rows"][0]["position_info"] == {"id": "p1", "side": "long"}
    assert tape.page({"action": "scale_out"})["total"] == 1
    assert tape.navigate({"id": "c", "directionStep": "select"})["row"]["adjustment_pct"] == pytest.approx(200 / 3)


def test_native_state_settles_funding_and_reversal_fee_once():
    tape = [
        {"ms": 1, "after": 3., "filled": 3., "entry": 10., "price": 10., "realized": 0., "fee": 3., "positions": ["p"]},
        {"ms": 2, "after": 2., "filled": -1., "entry": 10., "price": 12., "realized": 2., "fee": 1., "positions": ["p"]},
        {"ms": 3, "after": -1., "filled": -3., "entry": 12., "price": 12., "realized": 4., "fee": 3., "positions": ["p", "q"],
         "active_position": "q"},
    ]
    add_native_fields(tape, [{"ms": 3, "payment": -.5}])
    assert tape[1]["entry_before"] == 10. and tape[1]["breakeven"] == 11.
    assert tape[2]["before"] == 2. and tape[2]["after"] == -1.
    assert tape[2]["position"] == "q" and tape[2]["breakeven"] == 11.
    assert tape[-1]["gross_cumulative"] == 6. and tape[-1]["net_cumulative"] == -1.5


def test_true_replay_partial_bucket_ignores_future_intrabar_ohlcv(monkeypatch):
    base = RESEARCH_START
    stamp = base + 10_100
    x = object.__new__(ReplayWorkspace)
    x.market = SimpleNamespace(availability_lag_ms=100, _paths=lambda *_: ("fixture",))
    values = {
        "open": [10., 11., 99.], "high": [12., 13., 999.], "low": [9., 10., 1.],
        "close": [11., 12., 998.], "volume": [2., 3., 999.],
    }
    monkeypatch.setattr(data, "select", lambda _ds, **query: query)

    def block(query, **_):
        indices = np.arange((query["start"] - base) // 5000, (query["end"] - base) // 5000)
        times = base + indices * 5000
        return iter([data.Block(times, ("BTCUSDT",), times + 5100, np.zeros((len(times), 1), dtype="u1"),
            {key: np.array(values[key])[indices, None] for key in MARKET}, 5000,
            {key: data.UNITS[key] for key in MARKET}, ("fixture",))])

    monkeypatch.setattr(data, "iter_blocks", block)
    result = x.candles("BTCUSDT", base, base + 15_000, 15_000, stamp)["rows"][0]
    assert result == dict(t=base, status=0, available_ms=stamp, partial=True,
                          open=10., high=13., low=9., close=12., volume=5.)


def test_external_archive_retains_individual_fills_and_hides_unknown_inventory(tmp_path):
    archive = tmp_path / "archive.sqlite"
    with closing(sqlite3.connect(archive)) as db:
        db.executescript("""CREATE TABLE orders(id TEXT, ms INT, symbol TEXT, body TEXT);
            CREATE TABLE positions(symbol TEXT,body TEXT); CREATE TABLE tasks(kind TEXT, state TEXT,lo INT,hi INT);
            CREATE TABLE pages(id INT);""")
        for key, pnl in (("a", 0), ("b", 5)):
            body = dict(symbol="AUSDT", orderUpdateTime=100, orderTime=99, avgPrice=2.,
                        executedQty=3., side="SELL", totalPnl=pnl)
            db.execute("INSERT INTO orders VALUES (?,?,?,?)", (key, 100, "AUSDT", json.dumps(body)))
        db.commit()
    reader = ReplayWorkspace(SimpleNamespace(source=tmp_path / "bars_5s"), archive)
    with closing(reader.archive_db()) as db:
        assert db.execute("PRAGMA query_only").fetchone() == (1,)
    tape = reader.external_tape("AUSDT", reader.archive_version())
    assert [x["id"] for x in tape.rows] == ["a", "b"]
    assert [x["type"] for x in tape.rows] == ["unclassified", "realized"]
    assert all("before" not in r and "after" not in r for r in tape.rows)
    assert tape.page({"clock": 100, "selected": "a"})["total"] == 1
