"""Archive anonymous Binance portfolio-history responses with pagination metadata."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from urllib.request import Request, urlopen


BASE = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade/lead-portfolio"
PORTFOLIO = "5140488422016171264"


class RateLimited(RuntimeError):
    """Binance explicitly throttled requests."""


def fetch(endpoint, body):
    request = Request(f"{BASE}/{endpoint}", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    for delay in (2, 5, 10, None):
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
        if result["code"] == "000000":
            return result["data"]
        if result["code"] == "90801003":
            raise RateLimited(f"{endpoint}: Binance response {result['code']} {result.get('message')}")
        if result["code"] != "11012005" or delay is None:
            raise RuntimeError(f"{endpoint}: Binance response {result['code']} {result.get('message')}")
        sleep(delay)


def collect(target, endpoint, **filters):
    body = {"portfolioId": PORTFOLIO, "pageNumber": 1, "pageSize": 200, **filters}
    order_history = endpoint == "order-history"
    cursor = None
    first_total = None
    rows = 0
    pages = 0
    with target.open("x", encoding="utf-8") as output:
        while True:
            data = fetch(endpoint, body)
            output.write(json.dumps({"utc": datetime.now(UTC).isoformat(), "request": body, "data": data}) + "\n")
            output.flush()
            pages += 1
            first_total = data["total"] if first_total is None else first_total
            page = data["list"]
            rows += len(page)
            if not page or rows >= first_total:
                break
            if order_history:
                next_cursor = data["indexValue"]
                if next_cursor is None or (cursor is not None and int(next_cursor) >= int(cursor)):
                    raise RuntimeError(f"{endpoint}: cursor stopped advancing on page {body['pageNumber']}")
                cursor = next_cursor
                body["indexValue"] = cursor
            else:
                body["pageNumber"] += 1
            sleep(.55)
    print(target.name, "received", rows, "first total", first_total, "pages", pages, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("feed", choices=("orders", "positions"))
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    if args.feed == "positions":
        collect(args.folder / "position-history.jsonl", "position-history")
        return
    end = datetime.now(UTC)
    start = end - timedelta(days=7) + timedelta(minutes=10)
    while start < end:
        next_day = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        stop = min(next_day, end)
        target = args.folder / f"orders-{start.date()}.jsonl"
        if not target.exists():
            try:
                collect(target, "order-history", startTime=int(start.timestamp() * 1000),
                        endTime=int(stop.timestamp() * 1000) - 1)
            except RateLimited:
                raise
            except RuntimeError as error:
                print(target.name, error, flush=True)
        start = stop


if __name__ == "__main__":
    main()
