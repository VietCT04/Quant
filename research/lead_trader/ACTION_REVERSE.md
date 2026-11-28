# REVERSE | Crossing zero inventory

Scope and validation are in [How the trader adjusts open inventory](ACTION_FOCUS.md). A confirmed direct reversal has one reported execution with an independently reconciled outgoing closing quantity and opposite incoming opening quantity. The order is counted once.

## What the orders establish

The earlier position-history study found 5,341/5,707 near-simultaneous opposite-side opening matches (93.6%). That older result measures reported boundaries. The present order audit identified 807 reversal candidates. **678 have exact booked quantity splits**, including 564 with complete outgoing paths and 114 with incomplete outgoing paths. The remaining 129 are inferred and excluded from exact-quantity results. A pre-order five-second price mark was available for 239 of the 564 complete outgoing reversals, 25 of the 114 partial-path exact reversals and 21 of the 129 inferred records. The exported trajectory retains earlier linked orders and per-observation coverage flags.

| Pattern at reversal | Outgoing long / 275 | Outgoing short / 289 |
| --- | ---: | ---: |
| Previous action was a partial reduction | 183 (66.5%) | 184 (63.7%) |
| At least three consecutive reductions | 107 (38.9%) | 106 (36.7%) |
| Remaining quantity at most 25% of prior peak | 119 (43.3%) | 132 (45.7%) |
| Remaining quantity at least 75% of prior peak | 96 (34.9%) | 101 (34.9%) |
| No previous reduction and at least 75% of prior peak | 83 (30.2%) | 94 (32.5%) |

In the last group, closing-leg notional marked at the actual reported execution price was a median $13.55 outgoing long and $9.91 outgoing short. Eight longs and six shorts were at least $100. Only two longs and one short were at least $250. Abrupt flips of nearly intact inventory are common; abrupt flips of large dollar inventory are rare in this cohort. Median incoming quantity was **2.12×** closing quantity for long-to-short and **3.25×** for short-to-long. This separates actual opposite-side exposure from the notional consumed to close the old side.

## Would simply repeating the previous reduction cross zero?

Restrict to opposing executions that either trim or flip and follow a recorded trim within 15 minutes. Take the actual previous trim quantity as a proxy for a repeated order and compare it with the remaining outgoing quantity just before the next order.

| Would the previous trim clip consume the remainder? | Outgoing long reversals | Outgoing short reversals |
| --- | ---: | ---: |
| Yes | 65/115 (56.5%) | 72/138 (52.2%) |
| Would consume under half | 32/1,247 (2.57%) | 26/1,201 (2.16%) |
| Eligible baseline | 121/1,463 (8.27%) | 122/1,444 (8.45%) |

The crossing ratio helps identify when an opposing order is likely to flip the position. It does not imply the same clip was resubmitted. Among actual reversals with a recent trim, only 18/121 long and 16/122 short reversal orders matched the previous **whole** trim size within 10%. The median full reversal order was about twice the last trim. Many sequences combine a progressively smaller outgoing book with a substantial new allocation. Others flip an intact position. A model of independent BUY and SELL intents interacting with net exposure is consistent with part of the evidence. The tape cannot identify private intent, separate signal routing or common parent IDs.

## Price, inventory and actual timing

Median signed move from the prior fill was about −17 bp for outgoing longs and −26 bp for outgoing shorts with available marks. Median signed distance from weighted cost was −154 and −92 bp. These are exploratory distributions. Reversals also occurred with favorable displacement and without preceding cuts. Once an order occurred, low residual stock was informative. At or below 25% of prior peak, reversal represented 119/479 long events (24.8%) and 132/468 short events (28.2%). The full side baselines were 8.9% and 9.9%.

The low-stock reversal rate exceeded each symbol's own baseline in 27/29 long and 24/25 short symbols, with at least three qualifying observations per symbol. The rate exceeded the date baseline on 8/9 long and 9/9 short dates. Pooled exploratory 90% symbol-cluster intervals were 21.9–28.8% and 22.2–37.9%.

The next-minute probability moved less. In that low-stock group, 89/18,323 long minutes reversed (0.486%) versus 261/63,169 (0.413%) overall. For shorts, 96/21,901 (0.438%) versus 269/59,740 (0.450%) overall. This evidence explains the action selected at execution substantially better than the time when a reversal takes place.

## Next evidence to collect

A longer order archive would test the three-trim path, abrupt low-inventory flips and post-reversal ADD clusters with independent symbol-day overlap. Historical pre-trade book depth or parent identifiers, if ever published, could distinguish adaptive thresholds, split parent execution and independent opposing orders. Maintain the exact/partial/inferred tiers for these tests. New order histories improve path identification; position statements alone cannot fill missing orders.

[REVERSE heatmaps](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/action-atlas.html?action=reverse) · [Reversal quantity audit](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/reversal-audit.parquet) · [Prior market and inventory trajectory rows](/G:/Quant/data/research/lead-trader/action-focus-2026-11-29/reversal-neighbors.parquet)

## Complete positions in Explorer

- **Long, gradual reversal.** $14.18 in the closing leg, $14.18 of inventory before the order, and +4 bp from the most recent fill. 12 consecutive prior reductions; 9% of the earlier peak quantity remained. [TAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=TAUSDT&lifecycle=1125040827633602560%3A55&execution=ec9e5bdb4cac68d4480383cc29c4ea0a229105f97ee78c687d79dbe6bd0132f1)
- **Long, abrupt reversal.** $13.30 in the closing leg, $13.25 of inventory before the order, and -187 bp from the most recent fill. 0 consecutive prior reductions; 100% of the earlier peak quantity remained. [EVAAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=EVAAUSDT&lifecycle=1177304576108892160%3A7&execution=721dd08d0725f7d4c20bbe77cf404feb2dbcd086ed09ba578c5744a42b0eb73d)
- **Short, gradual reversal.** $13.84 in the closing leg, $13.87 of inventory before the order, and +9 bp from the most recent fill. 4 consecutive prior reductions; 9% of the earlier peak quantity remained. [ARIAUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=ARIAUSDT&lifecycle=1124904245505134592%3A54&execution=7b221fde2e5e1c93147ba225516d6c6e9972b2fdad86605c7cd24e1de2e7cc0c)
- **Short, abrupt reversal.** $9.03 in the closing leg, $9.16 of inventory before the order, and +227 bp from the most recent fill. 0 consecutive prior reductions; 100% of the earlier peak quantity remained. [IDOLUSDT in Explorer](http://127.0.0.1:8765/#explorer?source=binance&symbol=IDOLUSDT&lifecycle=1136496983082110976%3A47&execution=c8f5143596b6028644c2ccefdfe171d86b81432c043c976c38f9fa0376d269ff)
