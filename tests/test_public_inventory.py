import pytest

from quant.public_inventory import Report, lifecycle_coverage, reconstruct_public


def fill(ms, qty, price, pnl=0):
    return dict(ms=ms, filled=qty, price=price, reported_pnl=pnl, filled_notional=abs(qty) * price,
                type="realized" if pnl else "unclassified", positions=[])


def summary(start, end, *, peak, turnover, side="Long", closed=False, average=None):
    return dict(opened=start, closed=end if closed else None, updateTime=end, side=side,
                status="All Closed" if closed else "Partially Closed",
                maxOpenInterest=peak, closedVolume=turnover, avgCost=average)


def test_forward_from_open_partial_history_does_not_bridge_gap():
    rows = [fill(10, 4, 100), fill(20, 2, 94), fill(28, -1, 99, 1), fill(45, -1, 101, 3)]
    reconstruct_public(rows, {"p": summary(10, 50, peak=6, turnover=2)}, [(0, 30), (40, 55)], {})
    assert [r["type"] for r in rows] == ["long_entry", "scale_in", "scale_out", "scale_out"]
    assert [r.get("after") for r in rows] == [4, 6, 5, None]
    assert rows[2]["inventory_until"] == 30
    assert rows[2]["entry_after"] == pytest.approx(98)
    assert rows[2]["unrealized"] == pytest.approx(5)
    assert rows[-1]["quantity_certainty"] == "unknown"


def test_short_reconstructed_backward_from_verified_closed_boundary():
    rows = [fill(20, -2, 100), fill(30, 1, 92, 5), fill(40, 4, 91, 24)]
    reconstruct_public(rows, {"p": summary(5, 40, peak=8, turnover=7, closed=True, side="Short")},
                       [(18, 50)], {})
    assert [row["after"] for row in rows] == [-5, -4, 0]
    assert [row["type"] for row in rows] == ["scale_in", "scale_out", "short_exit"]
    assert rows[-1]["quantity_certainty"] == "inferred"


def test_one_exact_reversal_leg_reconstructs_other_and_confirms_signed_path():
    rows = [fill(20, -8, 100, 1), fill(30, 1, 95, 5)]
    lookup = {"old": summary(1, 20, peak=7, turnover=7, closed=True),
              "new": summary(20, 40, peak=5, turnover=1, side="Short")}
    action = dict(action="close", size=300, from_cost_bps=None)
    reconstruct_public(rows, lookup, [(15, 35)], {0: [("old", 1, action, 100)]})
    assert rows[0]["type"] == "reversal"
    assert rows[0]["positions"] == ["old", "new"]
    assert [row["after"] for row in rows] == [-5, -4]
    assert rows[1]["before"] == -5 and rows[1]["entry_after"] == 100
    assert rows[0]["action_certainty"] == rows[1]["quantity_certainty"] == "confirmed"


def test_rising_peak_snapshot_anchors_inventory_only_with_reconciled_turnover():
    lookup = {"p": summary(1, 50, peak=15, turnover=11)}
    older = Report.read("p", summary(1, 20, peak=10, turnover=9))
    history = {"p": [older]}
    for turnover, expected in [(11, 15), (12, None)]:
        rows = [fill(16, 1, 100), fill(25, 6, 100), fill(35, -2, 100)]
        report = {"p": {**lookup["p"], "closedVolume": turnover}}
        reconstruct_public(rows, report, [(15, 60)], {}, history)
        assert rows[1].get("after") == expected
        if expected:
            assert [r["after"] for r in rows] == [9, 15, 13]


def test_cost_inference_requires_independent_third_epoch_and_agreement():
    first_cost = 100.
    second_cost = (9 * 100 + 5 * 90) / 14
    third_cost = (13 * second_cost + 6 * 104) / 19
    for perturb, solved in [(0., True), (.1, False)]:
        rows = [fill(10, -1, 109, 109 - first_cost), fill(20, 5, 90),
                fill(30, -1, 106, 106 - second_cost), fill(40, 6, 104),
                fill(50, -1, 110, 110 - third_cost + perturb)]
        reconstruct_public(rows, {"p": summary(1, 60, peak=20, turnover=10)}, [(5, 60)], {})
        if solved:
            assert [r["after"] for r in rows] == pytest.approx([9, 14, 13, 19, 18])
            assert rows[-1]["entry_after"] == pytest.approx(third_cost)
            assert rows[-1]["quantity_certainty"] == "inferred"
        else:
            assert all("after" not in r for r in rows)


def test_report_conflicts_and_ambiguous_boundary_fills_stay_unclassified():
    rows = [fill(10, 1, 1), fill(10, 2, 1), fill(20, 1, 1, pnl=2), fill(30, -1, 1)]
    reconstruct_public(rows, {"p": summary(10, 35, peak=5, turnover=1)}, [(5, 40)], {})
    assert [r["action_certainty"] for r in rows] == ["unknown", "unknown", "unknown", "inferred"]
    assert all("after" not in r for r in rows)


def test_lifecycle_coverage_never_treats_first_trim_as_opening():
    report = {
        "expired": summary(100, 200, peak=20, turnover=5, closed=True),
        "pending": summary(220, 230, peak=9, turnover=0),
        "verified": summary(250, 270, peak=10, turnover=5, closed=True),
    }
    rows = [fill(190, -5, 3)]
    rows[0].update(id="first", positions=["expired"], type="scale_out")
    lives = [{"id": k, "state": "reported only"} for k in report]
    lifecycle_coverage(rows, lives, report, [(180, 210), (245, 280)], 150)
    assert [life["opening_status"] for life in lives] == [
        "outside_public_retention", "order_window_unverified", "missing_inside_verified_window"]
    assert all(life["completeness"] == "opening_missing" for life in lives)
    assert lives[0]["first_execution_id"] == "first"
    assert lives[0]["first_inventory_before"] is None
    assert lives[0]["opening_execution_id"] is None
    assert lives[0]["covered_order_ms"] == 21
