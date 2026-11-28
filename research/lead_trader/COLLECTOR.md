# Binance lead portfolio collector

The collector archives the anonymous order and position history of portfolio `5140488422016171264`. It uses the public Binance frontend history endpoints and makes no account, trade or subscription requests. The independent market-data and alpha analysis are separate.

## Start and inspect

Run these commands from the workspace in the locked environment.

```powershell
.venv/Scripts/python.exe -m research.lead_trader.collector start
.venv/Scripts/python.exe -m research.lead_trader.collector status
.venv/Scripts/python.exe -m research.lead_trader.collector pause
.venv/Scripts/python.exe -m research.lead_trader.collector resume
.venv/Scripts/python.exe -m research.lead_trader.collector stop
```

`start` launches a detached worker and an operating-system file lock permits one worker per archive. Configure a Windows logon and five-minute recovery check with `research/lead_trader/install_collector.ps1`. The recovery check runs as the signed-in user and respects a persisted `stop`. `start` clears that flag. `pause` and `resume` preserve any exchange cooldown. For Linux or another supervisor, run `python -m research.lead_trader.collector serve` under supervision or call `ensure` on a schedule. The default archive is `data/research/lead-trader/collector/`. Use `--home <directory>` before the command for a separate local archive. Avoid running separate collectors on the same public request budget.

`status` prints JSON with record counts and first/last timestamps, task states, verified and missing order intervals, pending and expired work, the next task, request counts, recent errors, observed limit metadata, and the first time another request is allowed. Phase `waiting` includes safety delays. `export orders <new-file.jsonl>` and `export positions <new-file.jsonl>` export deduplicated records. The SQLite database also contains the archived responses, request metadata and pagination state. Keep the SQLite file and its live WAL together when taking backups. Use SQLite's backup API for a consistent snapshot while the collector runs.

Import supported raw files or directories before startup to preserve earlier evidence. This command is idempotent.

```powershell
.venv/Scripts/python.exe -m research.lead_trader.collector import binance-5140488422016171264-raw.json data/research/lead-trader/2026-11-28 data/research/lead-trader/refreshed-2026-11-28
```

## Rate limits and request evidence

Both history feeds share one serialized scheduler. It reserves request budget before sending, including attempts interrupted before a response. Any reported quota and the local rolling ceilings apply together. With the default 25% margin, the ceilings are 4 per minute, 67 per hour and 600 per day. These are conservative local settings. This frontend endpoint's exact public request weight and rate policy have no confirmed published values. Official limits for other Binance API routes are tracked independently of this frontend route. The limiter recognizes standard and common legacy rate-limit metadata, Binance used-weight headers, and `Retry-After` as a date or a duration. Set `configure --margin <fraction>` and, when confirmed for this route, `--weight-cap x-mbx-used-weight-1m=<cap>` and `--max-request-cost <weight>`. Published usage with unknown capacity remains visible with unknown remaining quota.

The fallback begins at one request per 12 seconds, which applies alongside all rolling ceilings. Failures slow requests and successful calls gradually relax the delay toward the base. HTTP 429, HTTP 418 and observed Binance throttle codes stop both feeds until the latest required reset. With no server-provided retry time, the first throttle uses a 30-minute fallback, repeated throttles extend it, and HTTP 418 uses a one-day fallback. An HTTP 401, 403 or 451 pauses the worker for access review. Requests and response bodies record HTTP/category, reported limits, reset information and error details. Cookies and authorization headers are excluded from the archive. [Binance general rate-limit reference](https://developers.binance.com/en/docs/products/derivatives-trading-portfolio-margin-pro/general-info)

## Coverage and recovery

- The prior captures exposed an order window of roughly seven days and an advertised 6,000-row cap on broad queries. The collector works in fixed-time slices, prioritizing new windows and gaps nearing the inferred retention boundary. It revisits new slices after publication lag, splits capped or inconsistent windows, and records intervals that expire before recovery. It uses the returned order cursor with the page number fixed at one. A page and its cursor checkpoint commit in the same transaction.
- A covered order interval requires stable advertised totals, unique observed records, monotone pagination, matching slice bounds and agreement with previously imported order identities. These checks establish agreement with the exposed feed. Binance does not supply order IDs on this feed, so indistinguishable same-time records or publication changes can leave unresolved intervals. Unverified intervals are visible separately from matched records.
- Position history interleaves recent page-one captures and deduplicated deep sweeps. It has a live offset-based list, which can move during pagination. A sweep with changing totals, duplicate keys or inconsistent chronology stays unverified. The next sweep starts from page one and preserves every earlier accepted row. Position IDs and sequence indexes identify lifecycles. Revisions remain available in raw pages.
- Recovery resumes from durable SQLite checkpoints and honors the latest persisted cooldown. It applies to records still exposed publicly. The archive preserves uncertainty about older missing orders, portfolio-private data and periods where Binance changes or withdraws its public feed.

## Production check

On 2026-11-28 UTC, the recovery task restarted an interrupted worker while preserving its original cooldown, all imported records and the pending work. After the cooldown ended at 06:16, the first five live order requests and first two position requests all returned HTTP 200 and Binance code `000000`. Both returned JSON, a server Date and CloudFront cache metadata. The responses exposed no used-weight, limit, remaining-quota, reset or Retry-After fields. The worker applied its local budgets and paused after the first four calls in the rolling minute.

By 06:17 UTC, the archive had grown from 9,619 to 9,817 deduplicated orders and from 6,003 to 6,132 position records. The deep position sweep advertised 14,125 rows and advanced its durable checkpoint to page two. Archive totals and covered or missing intervals continue to update in `status`.
