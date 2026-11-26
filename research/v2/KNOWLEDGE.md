# V2 research knowledge

## Native execution acceptance

The BTCUSDT acceptance wave used two fixed configurations of slow_directional_persistence 1.0.0 over [2026-05-25, 2026-11-25) UTC. It used a 48-hour return, a 2.5% entry hurdle, a zero-return exit, and 1,000 USDT per order. The long-only candidate returned −0.76% net with Sharpe −0.63, 27 completed lifecycles and 54 filled orders. The long/short candidate returned −0.03% with Sharpe near zero, 55 lifecycles and 109 filled orders. Both had zero order rejections. These are narrow negative results for BTCUSDT and those configurations. About 408 of 4,416 hourly decision points were unavailable due to coverage and warm-up readiness.

The local V2 research registry is data/quant-control-v2/research.sqlite. H-0001 contains the two acceptance jobs. H-0002 is exact audit job d0a7c17b0767483cac292772428d575a, attached to experiment 22bbc3f0b7dc41d88a8118fef62ce991. The Explorer showed a distinct virtual terminal fill and reconciled it to the closed lifecycle and final equity. [Dashboard](http://127.0.0.1:8791/) · [Backtest Explorer](http://127.0.0.1:8791/#explorer?hypothesis=H-0001&candidate=22bbc3f0b7dc41d88a8118fef62ce991)

The queue acknowledged the submission in 127 ms. Its two-candidate wave took 10.02 s, including pre-warm-up and final reporting. A separate interruption and restart recovered both native jobs with their original IDs and retained the interrupted attempts. The recovered wave took 8.79 s with 353.4 MiB peak sampled worker RSS and a 114 ms paused submission. The Windows machine had 16 logical threads, a 4-worker ceiling and a 2 GiB framework memory budget. OS cache state was uncontrolled. Runtime and complete results are in data/research/v2 and data/research/v2/recovery/recovery.json. Browser artifacts are results.png and terminal-position.png under data/research/v2.

## Directional persistence on Liquid 50

The mechanism is continuation after a multi-day directional move. Orders open a fixed 150 USDT in a qualifying direction, close when the trailing return crosses through zero, and otherwise leave booked quantity unchanged. The initial six-month run is H-0003 in campaign C-6d7031d28d87d086. Orders used the V2 next open, default base costs and historical funding.

| Lookback / entry | Direction | Net ROI | Daily Sharpe | Max drawdown | Completed positions | Order success |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 48 h / 2.5% | Long | +17.26% | 1.32 | 12.97% | 2,225 | 98.21% |
| 48 h / 2.5% | Long and short | −18.61% | −0.84 | 35.34% | 4,268 | 98.04% |
| 96 h / 4.5% | Long | +25.80% | 1.92 | 13.21% | 992 | 98.26% |
| 96 h / 4.5% | Long and short | −12.57% | −0.61 | 25.08% | 2,023 | 98.38% |

The 48-hour long-only job ba541b88f8f844b481d3b4eaf742da34 earned 1,726 USDT net, with 81 empty-open rejections. Its three most profitable symbols contributed 1,585 USDT and September contributed 1,614 USDT. Only 24 symbols made money. The 96-hour long-only job e765d4db31214cd292f7306fb1559866 earned 2,580 USDT net, with 35 empty-open rejections. Thirty-three symbols made money and its top three contributed 1,682 USDT. August and September together contributed 2,740 USDT. There were no funding warnings or indicative equity flags for the promoted variants. Both were submitted unchanged for the full period. The six-month results remain distinct in the registry and reuse part of the full period.

Confidence is moderate that the six-month booked results are economically meaningful under the simulator. Confidence that the mechanism persists independently is low. The fixed Liquid 50 is selected using later November 2026 activity. Profit is concentrated in favorable months and smaller symbols in this universe. This long-only test can capture directional market exposure in addition to a timing effect. The tested bidirectional variants contradict a symmetric effect. Idealized zero-latency opens and no capacity limit reduce realism. The results justify earlier-history examination, with no real-capital use authorized.

The two compatible six-month waves took 142.68 s and 113.74 s with two active candidates each and peak worker RSS of 1.64 GiB across the complete campaign. The service accepted it in 201 ms. Full evidence and parameters are in data/research/v2/liquid50/profile.json.

## Context from the prior research phase

The pre-V2 five-minute screens and July target-based runs in [PH5-001](../phase5/KNOWLEDGE.md) remain intact. Their large losses arose under a high-turnover target strategy, participation slicing, repeated adjustments and a modeled 35% drawdown stop. The tested policies failed their stated objectives. Those results provide limited evidence about the broader mechanisms under deliberate market order entry, holding and exit. Turnover and realized execution remain priorities in follow-ups. Earlier and current results use different model versions and should keep their original labels.
