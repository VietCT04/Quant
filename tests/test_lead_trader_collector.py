from contextlib import closing
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

from research.lead_trader import collector, collector_store as store, rate_limit as rate


NOW = datetime(2026, 11, 28, 6, 30, tzinfo=UTC).timestamp()
SETTINGS = collector.DEFAULT_SETTINGS


def fill(stamp, qty=5):
    return {"symbol": "BTCUSDT", "orderTime": stamp, "orderUpdateTime": stamp,
            "side": "BUY", "type": "MARKET", "executedQty": qty, "avgPrice": 100., "totalPnl": 0.}


def window(db, lo, hi, mode="backfill"):
    with db:
        store.task(db, "order", lo, hi, NOW, mode)
    return db.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1").fetchone()


def message(rows, total, cursor=None, headers=None):
    return lambda endpoint, request: (200, headers or {},
        {"code": "000000", "data": {"list": rows, "total": total, "indexValue": cursor}}, None)


def test_order_checkpoint_resume_and_deduplication(tmp_path):
    lo = int(NOW * 1000) - 75_000
    mid = lo + 20_000
    hi = lo + 60_000
    with closing(store.connect(tmp_path)) as db:
        job = window(db, lo, hi)
        collector.execute(db, job, {}, SETTINGS, message([fill(mid)], 2, str(mid)), clock=lambda: NOW)
        assert store.coverage(db) == []
    with closing(store.connect(tmp_path)) as db:
        job = store.next_task(db, NOW)
        body = collector.request_body(job)
        assert body["pageNumber"] == 1 and body["indexValue"] == str(mid)
        collector.execute(db, job, {}, SETTINGS, message([fill(lo + 1)], 2), clock=lambda: NOW + 60)
        assert store.coverage(db) == [[lo, hi]]
        assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 2


def test_shared_budget_accounts_for_unfinished_request_and_all_remote_windows(tmp_path):
    with closing(store.connect(tmp_path)) as db:
        state = {}
        headers = {"ratelimit": '"per-ip";r=1;t=120, "portfolio";r=50;t=600',
                   "ratelimit-policy": '"per-ip";q=100;w=120, "portfolio";q=100;w=600',
                   "x-mbx-used-weight-1m": "115"}
        settings = SETTINGS | {"weights": {"x-mbx-used-weight-1m": 120}}
        rate.feedback(state, 200, headers, {"code": "000000"}, NOW, settings)
        with db:
            db.executemany("INSERT INTO requests(started,endpoint,input) VALUES(?,?,?)",
                           [(NOW - i, ("order-history" if i % 2 else "position-history"), "{}") for i in range(4)])
        next_time, local, remote, reasons = rate.budget(db, state, NOW, settings)
        assert next_time == NOW + 122
        assert "per-ip" in reasons and "x-mbx-used-weight-1m" in reasons
        assert [policy["used"] for policy in local] == [4, 4, 4]
        assert remote["portfolio"]["remaining"] == 50


def test_throttle_persists_retry_after_and_never_advances(tmp_path):
    with closing(store.connect(tmp_path)) as db:
        job = window(db, 0, 90_000)
        result = collector.execute(db, job, {}, SETTINGS,
            lambda endpoint, body: (429, {"retry-after": "720"}, {"code": "-1003"}, None), clock=lambda: NOW)
        assert result == "throttled"
    with closing(store.connect(tmp_path)) as db:
        limiter = store.get(db, "limiter")
        assert limiter["cooldown"] == NOW + 723
        assert store.next_task(db, NOW)["page"] == 1
        assert rate.budget(db, limiter, NOW + 10, SETTINGS)[0] == NOW + 723
        assert rate.feedback(limiter, 200, {}, {"code": "90801003"}, NOW + 725, SETTINGS) == "throttled"
        assert limiter["cooldown"] > NOW + 725 + 1800
        assert rate.feedback(limiter, 418, {}, {}, NOW + 725, SETTINGS) == "throttled"
        assert limiter["cooldown"] >= NOW + 725 + 86_400
        assert rate.feedback(limiter, 403, {}, {}, NOW, SETTINGS) == "restricted"
        assert limiter["paused"]


def test_capped_or_omitted_rows_never_certify_coverage(tmp_path):
    with closing(store.connect(tmp_path)) as db:
        capped = window(db, 0, 120_000)
        collector.execute(db, capped, {}, SETTINGS, message([fill(119_999)], 6000), clock=lambda: NOW)
        assert db.execute("SELECT state FROM tasks WHERE id=?", (capped["id"],)).fetchone()[0] == "split"
        assert store.coverage(db) == []
        first = store.next_task(db, NOW)
        with db:
            store.save_records(db, "order", [fill(first["lo"] + 3)])
        collector.execute(db, first, {}, SETTINGS, message([], 0), clock=lambda: NOW)
        assert store.coverage(db) == []
        assert "omitted" in db.execute("SELECT failure FROM tasks WHERE id=?", (first["id"],)).fetchone()[0]


def test_bootstrap_retention_and_recent_priority(tmp_path):
    with closing(store.connect(tmp_path)) as db:
        with db:
            store.schedule(db, NOW, SETTINGS)
        assert store.next_task(db, NOW)["mode"] == "live"
        cursor = store.get(db, "scheduled_until")
        later = NOW + store.RETENTION + store.DAY
        with db:
            store.schedule(db, later, SETTINGS)
        assert store.get(db, "scheduled_until") > cursor
        expired = db.execute("SELECT count(*) FROM tasks WHERE state='expired'").fetchone()[0]
        assert expired > 0
        assert db.execute("SELECT count(*) FROM tasks WHERE mode='live' AND state='pending'").fetchone()[0] == 2


def test_public_transport_preserves_headers_and_body_on_http_throttle(monkeypatch):
    headers = Message()
    headers["Retry-After"] = "480"
    headers["RateLimit"] = '"ip";r=0;t=480'
    headers["RateLimit"] = '"portfolio";r=10;t=1200'
    headers["Set-Cookie"] = "session=private"

    def blocked(request, timeout):
        assert request.get_method() == "POST"
        assert request.full_url.endswith("/order-history")
        raise HTTPError(request.full_url, 429, "Too Many Requests", headers,
                        BytesIO(b'{"code":"90801003","message":"limit reached"}'))

    monkeypatch.setattr(collector, "urlopen", blocked)
    http, observed, payload, error = collector.public_read("order-history", {"portfolioId": collector.PORTFOLIO})
    assert http == 429 and error is None
    assert payload["code"] == "90801003" and observed["retry-after"] == "480"
    assert '"portfolio";r=10' in observed["ratelimit"]
    assert "set-cookie" not in observed
