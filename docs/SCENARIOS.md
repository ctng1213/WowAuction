# WowAuction — Scenarios

The named Given / When / Then scenarios for [SPEC.md](SPEC.md) §8. Each one reads in plain numbers a person can check by hand, and its ID is part of its test name.

**This file and the tests must agree** (SPEC §8.1). `tests/test_conventions.py` fails when:
- an ID listed here has no test;
- a test names an ID that isn't listed here;
- an ID appears twice.

Shared engine scenarios live in `tests/scenarios/*.json` (with an `id` field) and are the executable form. When a scenario's numbers change, change the JSON and this table in the same change.

Rules a scenario checks are defined in SPEC.md. When a scenario and the spec disagree, fix one of them. Never leave both.

## Contents

1. [Core scenarios](#1-core-scenarios): volume, sync, failure modes, dashboard security, pricing, sell probability, patterns, recommendations, own record, display, craft sourcing, chains, missed peaks, refresh, confidence, tracking, addon (A1–A5)
2. [Visual tests](#2-visual-tests-ui1ui11) (SPEC §8.4)
3. [Views and tooltip](#3-views-and-tooltip-d16d22-tt1tt5) (SPEC §8.5)
4. [Calibration](#4-calibration-k1k3) (SPEC §8.8)
5. [Invariants](#5-invariants-spec-87) (SPEC §8.7)

## 1. Core scenarios

**Sales volume** (`engine/volume`), book entries written as `[item, unit price, stack, band, count]`. Books are 2 h apart unless stated otherwise:

| # | Given (prev book → next book) | Then | Shows |
|---|---|---|---|
| V1 | Linen 50c ×20 long ×5 → ×3 | sold 40, expired 0 | plain sales: 2 auctions × 20 |
| V2 | Linen 50c ×20 **short** ×4 → ×0 | sold 0, expired 80 | gone auctions that were about to expire count as expired |
| V3 | 50c ×20: long ×3 + short ×1 → ×1 | gone 3, expired 1, sold 2 → 40 sold, 20 expired | expired is capped at the short count |
| V4 | 50c ×20 ×2 → ×5 | sold 0, listed 60 | new listings are not sales |
| V5 | 50c ×20 ×1 → 45c ×20 ×1 | sold 20, listed 20 | documented error: an undercut repost looks like a sale |
| V6 | 50c ×20 long ×2 → 50c ×20 medium ×2 | sold 0 | time left ticking down is not a sale (band is not in the group key) |
| V7 | two stack sizes, 50c ×20 ×2 → ×1 and 50c ×5 ×4 → ×2 | sold 20 + 10 = 30 | units = auctions × stack, summed per item |
| V8 | books 7h apart | no estimate | gap must be ≤ 6h |
| V9 | mail: 3 "sold", 1 "expired" for an item estimated 5 sold | calibration factor 0.6 recorded | ground truth from own sales |
| V10 | AHF quantity 120 → 80 within 2h, no books | listing turnover 40 recorded; sales per day **unavailable**; chance = default 25%, "volume unknown" | totals alone never become sales |
| V11 | Premium volume present + own estimate | Premium used, source = `ahf` | source priority |
| V12 | 50c ×20 **medium** ×2 → ×0, 2 h apart | expired 40, sold 0 | medium (30 min–2 h) auctions have all run out after 2 h |
| V13 | 50c ×20 long ×3 → ×0, **5 h** apart | expiring 3 × (5−2)/(8−2) = 1.5 → expired 30, sold 30 | long auctions expire in proportion to the gap |
| V14 | no key: book estimates only, 40 sold/day for 7 days | sold/day 40, source "estimated" | free path works on its own |
| V15 | AHF volume 50/day on days 1–20, Premium lapses, estimates 40/day on days 21–30 | 30-day sold/day = (20 × 50 + 10 × 40) ÷ 30 ≈ 46.7; source "20 days AHF, 10 days estimated" | per-day best source |
| V16 | days with both: AHF 50, estimate 40 (×10 days); then estimate-only days at 40 | factor 1.25; those estimate days count as 50 | AHF calibrates the estimates |
| V17 | AHF and estimate both present on a day | AHF used for that day; the estimate is still stored | estimates never stop |
| V18 | item has an AHF factor 1.25 and a mail factor 0.6 | estimate days use 1.25 only | one calibration, AHF preferred |
| V19 | two complete local scans 2 h apart on the same day: Linen 50c ×20 long ×5 → ×3 (long auctions cannot have run out in 2 h) | `books` volume for that day: sold 40; reading the same files again leaves it at 40 | scans feed volume, idempotently |
| V20 | a complete scan at 10:00 and a partial scan (coverage 0.6) at 12:00 | no `books` volume from that pair | partial books never feed volume |
| V22 | sales 50/day, 93 units listed now; another item with sales unavailable; a third with sales 0 | supply/volume 1.86 days; "—"; "—" | the ratio is never made up |
| V23 | median daily supply over 30 days of observations 80, 100, 120 units, and 10 days without any observation | median 100; the empty days are left out, not counted as 0 | missing data is not zero |
| V24 | `VOLUME_METHOD_VERSION` goes from 1 to 2 with 20 days of stored books | the next full refresh recomputes those days' `books` volume, stamps them version 2, and leaves AHF and mail rows unchanged | estimates can be recomputed |
| V25 | Linen 50c ×20 long ×5 at 10:00 → ×3 at 11:00 → ×2 at 14:00 | `sales_h1` 10:00: 40 per hour (40 sold ÷ 1 h); no row from the 11:00 → 14:00 pair (3 h apart) | hourly rates need books at most 2 h apart |
| V26 | Linen ×20 long ×5 at 10:00 → ×4 at 10:30 → ×2 at 11:00 | both pairs start in the 10:00 hour: (20 + 40) sold ÷ (0.5 + 0.5) h = 60 per hour | several pairs in one hour pool their sold and gap |
| V27 | an AHF Premium book at 10:00, a partial local scan (coverage 0.6) at 10:20, a complete local scan at 11:00; the same files are read again | one pair, AHF 10:00 → local 11:00; the partial scan is skipped; reading again leaves `sales_h1` unchanged | either source pairs; partial books never do; idempotent |
| V21 | a local scan at 14:20 lists Linen at 45c (×20, ×2) and 50c (×20, ×1) | `current` local: 45c, qty 40; `candles_h1` local for 14:00: close 45c, qty 60 | a scan becomes local prices |

**Sync and storage** (`sync/catchup`, `store/prune`):

| # | Scenario | Then |
|---|---|---|
| S1 | first run, no key | fetches the public d1 file + every h1 hour ≤ `sealed.h1` within 7 days, nothing beyond `sealed.h1` |
| S2 | crash after hour 37 of 112, restart | resumes at hour 38; no duplicate rows |
| S3 | last run 10 days ago, no key | hours 8–10 days old are reported lost (no public day candles cover them either); no request for them; status warning |
| S4 | same gap with Premium | days 8–10 come from `h1c` using `caps.h1[day]` |
| S5 | 429 with `Retry-After: 60` | waits 60 s on the fake clock, retries, succeeds |
| S6 | rows 89d23h and 90d01h old | prune keeps the first and deletes the second |
| S7 | AHF row and local scan for the same item | `cur` uses the newer one and keeps its `src` |
| S8 | API key in config | key never appears in logs, URLs or exceptions |
| S9 | an AHF close of 50c and a local close of 48c for the same item and hour | statistics see one close for that hour: 48c |
| S10 | SavedVariables being rewritten (modification time changed 1 s ago) | not parsed until it has been unchanged for 2 s |
| S11 | `/me` lost `depth`, `prices.live` and `historyDays 90` since the last run | no requests for Premium files; the snapshot replaces the live table; Status shows "Premium ended 23 Oct"; the run completes normally |

**Failure modes** (§5.2). Faults are injected deterministically by the fakes:

| # | Scenario | Then |
|---|---|---|
| FM1 | 503 on hour 40, five times in a row | backoff 1, 2, 4, 8, 16 s on the fake clock; run ends; `sync_state` = hour 39; the next run starts at 40 |
| FM2 | hour 41's file cut off at 60% | not committed; logged; refetched next run |
| FM3 | the same hour bad 3 runs in a row | Status "bad file: hour 41"; later hours still sync |
| FM4 | the snapshot has a renamed field | snapshot disabled this run; last good `current` kept; Status "AHF format changed: snapshot" |
| FM5 | Linen `cur` 1 c while `p10_30` is 30 c | flagged suspect; no buy recommendation from it; a second observation at 1 c clears the flag |
| FM6 | crash after committing hour 37 of 112 | restart resumes at 38; no duplicates (as S2) |
| FM7 | crash halfway through a full refresh | on restart, the staging tables are dropped; the old `item_stats` and plan are still served; the refresh re-runs |
| FM8 | one item's stats raise an error | that item is excluded and listed on Status; all others refreshed |
| FM9 | disk full on the 3rd write of a refresh | no partial tables; Status error; old numbers served |
| FM10 | `quick_check` fails on start | restored from the newest backup; hours after it are re-fetched where available; Status says what was restored |
| FM11 | SavedVariables ends mid-table | not ingested; `.bak` parsed instead; `ingestedUntil` moves no further than what was stored |
| FM12 | the same SavedVariables ingested twice | event ids dedupe; counts unchanged |
| FM13 | SavedVariables format version newer than AHSync | not ingested; "Update AHSync" |
| FM14 | `MarketIntelligence.lua` rename fails 3 times | the old file kept; Status warning |
| FM15 | second AHSync start | exits: "AHSync is already running" |
| FM16 | `config.toml` has `retention_days = "ninety"` | refuses to start: "config.toml line 4: retention_days must be a number" |
| FM17 | `MarketIntelligence.lua` schema version differs from the addon's | empty state plus "versions differ"; no Lua error |
| FM18 | `basedOnUntil` is older than your last buy | that buy's action shows done locally; plan labelled provisional |
| FM19 | AH closed at 40% of a full scan | saved as partial (coverage 40%); excluded from volume diffs |
| FM20 | buy hook fires, then "not enough gold" | an attempt only; it expires after 1 h; no cost basis change |
| FM21 | crash before logout; at the next login 20 Linen are missing from bags | recorded as an untracked change; no invented sale or cost |
| FM22 | SavedVariables at its cap with 5 un-ingested full scans | the oldest scan is dropped; all events kept; warning shown |
| FM23 | migration from SavedVariables v1 to v2 fails halfway | v1 data untouched; error shown; nothing deleted |
| FM24 | AH closed with 6 of 10 searches still queued | queue dropped; the 6 unsearched targets keep their last price and show "…", not "✗ gone"; the queue is rebuilt on the next AH open |
| FM25 | the key is expired: `/files` returns 401 with the key and 200 without it | the run continues on the free path; Status "API key rejected"; no traceback, no key in the log |
| FM26 | a 10-day gap with Premium; h1c returns 403 from the first h1c hour | h1c disabled after that request; no further h1c requests; those hours reported lost (or day-only where d1c covers them); public h1 hours still sync |
| FM27 | AHF renames a field in the hour candle files | "AHF format changed: h1" on Status; the hour loop stops; `last_hour` unchanged; no "bad file" rows; after the parser is fixed, the next run fetches those hours |
| FM28 | a client opens a dashboard connection and sends only half a request line | other requests are still answered at once; the stalled connection is dropped after 10 s; stopping AHSync doesn't wait for it |
| FM29 | the stored Craft page is corrupt (not valid JSON) | `GET /api/views/craft` answers `503 {"error": "bad_view"}` and logs it; the connection is not dropped; the next full refresh replaces it |

**Dashboard security** (`web/server`, §3.5):

| # | Scenario | Then |
|---|---|---|
| SEC1 | `POST /api/skip` with the right token and `Origin: http://127.0.0.1:8765` | accepted; the action is skipped |
| SEC2 | the same request with `Origin: https://evil.example` | `403`; nothing changed; logged without the token |
| SEC3 | the same request with no `X-AHSync-Token` | `403`; nothing changed |
| SEC4 | any request with `Host: evil.example:8765` (DNS rebinding) | `403`; no data in the response |
| SEC5 | `GET /api/skip?id=…` | `405`; nothing changed |
| SEC6 | every response in the suite | no `Access-Control-Allow-Origin` header; the API key never appears |
| SEC7 | AHSync restarts | a new token; a page loaded before the restart gets `403` on its next write and asks you to reload |

**Pricing and probability** (`engine/prices`, `profile`, `sellprob`, `fees`):

| # | Scenario | Then |
|---|---|---|
| P1 | hourly closes 1,2,3,…,9 g | `med7` = 5 g |
| P2 | only 2 hourly closes but 7 daily closes | median falls back to daily (fewer than 24 hourly closes) |
| P3 | Saturday 16:00–20:00 UTC prices consistently 20% above `med30` for 4 weeks (the slot and the 2 on each side, so smoothing keeps the full rise) | `how[Sat][18]` = 1.2, confidence high |
| P4 | the same pattern but only 1 week of data | weekday-only fallback, confidence low |
| P5 | sell 20 at faction AH for 1 g each, `P` = 1 | E = 20 g × 0.95 = 19 g; the deposit is refunded |
| P6 | sells 120/day (flat profile), post 20 now (8 h window), 10 units ahead (very long band), no new listings, no tier history | demand 40; the ahead units take 10; you get 30 → P = 1 |
| P7 | sells 2/week, you post 1 now, flat profile, nothing ahead | demand 2/7 ÷ 3 ≈ 0.095 → P ≈ 0.10 |
| P8 | no volume data | `P` = 0.25 (`default_sell_prob`), flagged `volume unknown` |
| P9 | as P5 but `P` = 0.5, vendor sell 5 s, deposit 15 s, no DE | E = 0.5 × 19 g + 0.5 × (20 × 5 s − 15 s) = 9 g 92 s 50 c |
| P10 | hourly closes 1, 2, 3, 4 g; then 1 c, 2 c | median 2 g 50 s; median 1 c (floor of 1.5) |
| P11 | time 345600000 ms (1970-01-05 00:00 UTC) | weekday 0 (Monday), hour 0 |

**Sell probability with full (Premium) data** (`sellprob.simulate`, `sellprob.blend`). Common inputs: `S` = 24/day with a flat profile (1 unit per hour), post now (8-hour window), no tier history unless stated:

| # | Scenario | Then |
|---|---|---|
| P12 | post 5; 6 units ahead in the **long** band (midpoint 5 h); no new listings | hours 0–4: buyers take 5 of the 6, and the last one expires; hours 5–7 give you 3 → P = 3/5 = 0.60. The old formula gave (8 − 6)/5 = 0.40, because it ignored that competitor expiring |
| P13 | the same 6 units in the **very long** band | hours 0–5 go to them; hours 6–7 give you 2 → P = 0.40, the same as the old formula |
| P14 | P12, plus `L × u` = 12/day (0.5 undercut units each hour) | hours 0–4: 0.5 to the undercutters and 0.5 to the old six each hour (3.5 then expire); hours 5–7: 0.5 to the undercutters and 0.5 to you → 1.5/5 → P = 0.30 |
| P15 | P12's queue (0.60) plus tier T1 history: 24 of 30 sold (0.80) | e = 0.5 → P = 0.5 × 0.60 + 0.5 × 0.80 = 0.70 |
| P16 | the same with only 15 auctions of history | e = 0.25 → P = 0.75 × 0.60 + 0.25 × 0.80 = 0.65 |
| P17 | S = 30/day, X = 10/day, 93 units on the AH | market sell-through 75%; 3.1 days of supply |
| P18 | no book; `cur` 50 c, `curQty` 8; you post at 49 c | nothing ahead (you are below `cur`) |
| P19 | no book; you post at 50 c | 8 units ahead, available all window |
| P20 | new listings in the last 7 days: 40% priced at or below the then-lowest price; you post at the lowest | `u = 0.4` |
| P21 | S = 24/day; profile: 06:00–09:00 at 1.0, 10:00–13:00 at 0.5, 14:00–17:00 at 2.0; post 10 at **06:00**, nothing ahead | demand 4 × 1.0 + 4 × 0.5 = 6 → P = 0.60 |
| P22 | the same item posted at **10:00** | demand 4 × 0.5 + 4 × 2.0 = 10 → P = 1.00. The same post is worth more at 10:00, because its window catches the 14:00 rush |
| P23 | Week plan: target window Sat 17:00–21:00, best hour 18:00 | post Sat 10:00; chance window Sat 10:00–20:59 (11 h); the auction is 24 h, the shortest seeded duration (2, 8, 24 h) covering it |
| P24 | a flip bought now, to sell in the Sat 17:00–21:00 window | its sale uses the Sat 10:00–20:59 window, not the next 8 hours |

**Weekly patterns** (`patterns`):

| # | Scenario | Then |
|---|---|---|
| W1 | flat demand; `how` = 1.15 on Tue 18–20, 1.12 on Wed 19–21, 1.00 elsewhere | threshold 0.9 × 1.15 = 1.035 → windows "Tue 18:00–21:00" (sum 3.45), then "Wed 19:00–22:00" (sum 3.36) |
| W2 | as W1, but `sales_how` is 0.8 on Tue 18–20 and 2.0 on Wed 19–21 (the busiest), about 1.0 elsewhere so the mean stays 1.0 | Tue scores 1.15 × 0.8 ÷ 2.0 = 0.46 and is dropped; the top window is Wed 19:00–22:00 (score 1.12): a high price with few buyers loses |
| W3 | Sun 23:00–Mon 01:00 all qualify | one window "Sun 23:00–Mon 02:00" (crosses midnight) |
| W4 | a slot has values 1.0 (this week), 1.0 (1 week ago), 1.2 (4 weeks ago); half-life 4 | weights 1, 0.84, 0.5; the weighted median is 1.0 |
| W5 | the top window has 2 weeks of data | labelled "early pattern"; confidence low |
| W6 | 7 weeks of data | "established"; confidence high |
| W7 | no slot data at all | no windows; "Best time to sell: —"; the plan posts now |
| W8 | 6 weeks of data; on the held-out latest week the weekly profile's mean absolute error is 20c and a flat price's is 50c; the same item with 3 weeks; with 2 weeks | strength 60 (100 × (1 − 20/50) × 1); 30 (× 3/6); insufficient evidence |
| W9 | `how` peaks Sat 20:00, `sales_how` peaks Tue 19:00, `sell_score` peaks Wed 19:00 | item detail shows all three: highest price Sat 20:00, most activity Tue 19:00, best risk-adjusted Wed 19:00 (the plan's) |
| W10 | `how` is 0.85 on Mon 03-05 (the cheapest), 0.93 on Thu 04, 1.0 elsewhere; threshold 0.9 | buy window "Mon 03:00–06:00" (≤ 0.85 ÷ 0.9 = 0.944); Thu 04:00 qualifies too (0.93) as a second window |
| W11 | a big premium (how 1.4 on Sat 18:00) seen in only one of 4 weeks, so the held-out week misses it | strength near 0 even though the premium is large: predictability, not profit |
| W12 | 4 weeks of hourly listed units: 100 everywhere except 200 on Tue 16:00–20:00; a second item with 2 weeks; a third with none | `supply_how` Tue 18:00 = 2.0 (overall median 100; the 5-slot smoothing sees only 200s), Tue 15:00 = (1 + 1 + 1 + 2 + 2) ÷ 5 = 1.4; the second item uses its weekday ratio; the third is flat 1.0 |
| W13 | a profile with ratios 1.2346, 0.0625 and 0.8 in its first three slots, 1.0 elsewhere; the same with 0 weeks everywhere | packed "1235,63,800,1000,…" (0.0625 × 1000 = 62.5 rounds half up to 63) and decoded back to 1.235, 0.063, 0.8; packing again gives the same text; with no weeks the profile is stored as "" and decodes to flat 1.0 |

**Recommendations** (`engine/disenchant`, `craft`, `plan`):

| # | Scenario | Then |
|---|---|---|
| R1 | green ilvl 25 armor at 8 s; DE table gives 2.5 Strange Dust avg; dust posts at 6 s and sells 200/day (`P` = 1) | EV = 2.5 × 6 s × 0.95 = 14 s 25 c; profit = 6 s 25 c; listed |
| R2 | the same item, but no character has the Enchanting skill | not listed |
| R3 | recipe: 2 Copper Bar (AH 10 s) → Copper Rod (posts at 40 s, `P` = 1) | profit = 40 s × 0.95 − 20 s = 18 s |
| R4 | the same recipe with Copper Bars in your bags | owned mats priced at their hold value (opportunity cost), not free |
| R5 | a vendor sells the reagent cheaper than the AH | the vendor price is used |
| R6 | you own 200 Linen; it sells 40/day; Saturday is the peak | plan spreads posts over days, ≤ the daily cap, Saturday preferred |
| R7 | the DE value of an owned green beats its AH sale | plan says disenchant, with the character who can |
| R8 | a flip candidate has a 25% margin but `P(sell)` is 0.1 | ranked below a 10% margin with `P` 0.9 |
| R9 | budget 50 g | plan buys stay ≤ 50 g |
| R10 | a soulbound item in your bags | not in the sell plan |
| R11 | Main and Alt are both Blacksmiths; only Alt knows the recipe | the craft row names Alt; Main is never suggested |
| R12 | the lowest listing (50 c) is your own; the next is 55 c; `med7` 60 c | post price 54 c: you don't undercut yourself |
| R13 | a flip at 10 s with `P` = 0.5, posting at 30 s, vendor sell 1 s, deposit 1 s | E = 0.5 × 28 s 50 c + 0.5 × (1 s − 1 s) = 14 s 25 c; profit 4 s 25 c; still listed only if ≥ 20% margin (it is: 42.5%) |
| R14 | you own 200 Linen in stacks of 20; it sells 40/day; 8 h window; `max_market_share` 0.5 | window cap = max(1 stack = 20, floor(40 × 8 ÷ 24 × 0.5) = 6) = 20 units; daily cap floor(40 × 0.5) = 20; the rest is spread over later windows |
| R15 | three disenchantable greens each yield 2.5 Strange Dust; dust sells 6/day, so its 8 h cap is 1 stack of 1 → max(1, floor(6 × 8 ÷ 24 × 0.5) = 1); the buys are profitable | only the best-profit green is recommended (its 2.5 dust already exceeds the cap; the others would add more unsellable dust) |
| R16 | Main and Alt could each flip 20 Linen, cap 20 per window | the plan buys 20 in total, for one character; never 20 each |

**Own sell record and rules** (`engine/record`, `sellprob`, `rules`), with `k` = 4 and `struggle_streak` = 3:

| # | Scenario | Then |
|---|---|---|
| O1 | model `P` = 0.8; your record: 0 sold, 3 expired | P' = (4 × 0.8 + 0) / (4 + 0 + 3) = 3.2 / 7 ≈ 0.457; the row shows "P 80% → 46%" |
| O2 | no posts of the item in 30 days | P' = P; no record line shown |
| O3 | streak 2; the plan would post 8 stacks | 8 × 0.5² = 2 stacks |
| O4 | streak 1; the plan would post 1 stack | still 1 stack (minimum) |
| O5 | streak 3 | flagged "not selling": not in flips or craft-to-sell; owned stock still planned; Blacklist offered |
| O6 | expired, expired, then sold | streak 0; quantity back to normal |
| O7 | 3 expiries, all 31 days ago | ignored: s = x = 0, streak 0 |
| O8 | rule `no_buy` on an item you also own | no buy, craft or DE-buy rows; owned stock still in the sell plan |
| O9 | rule `ignore` | in no recommendation; tooltip prices still shown |
| O10 | addon sets `no_buy` at T+2h; dashboard cleared it at T+1h | `no_buy` wins (newer) |
| O11 | your post at 50 c expired; a scan during its lifetime showed 47 c by someone else | cause `undercut`; streak unchanged; next post capped at `curOthers − 1` |
| O12 | 2 expiries in a row while cheapest; planned price 50 c | price 50 × 0.95² = 45.125 → 45 c; quantity × 0.5² = ¼ |
| O13 | as O12, but vendor sell 44 c and no DE | floor = ceil(44 / 0.95) = 47 c, so the price is 47 c, not 45 c |
| O14 | an expiry with no observations during the post | cause `unknown`; counted like `lowest` |
| O15 | streak 3, you choose Keep | s = x = streak = 0 from that moment; P' = P; quantity and price back to normal; no flag |
| O16 | after Keep, the next post expires while cheapest | flagged "not selling" again (`keep_retry_streak` = 1); Blacklist / Keep offered |
| O17 | after Keep, the next post is undercut and expires | not flagged (undercut doesn't count); next post capped at `curOthers − 1` |
| O18 | the addon set `no_buy` at T; the dashboard clears it at T+2h | stored as `none` at T+2h, which beats `no_buy`; `PortfolioPlan.lua` carries the `none`, and the addon drops its rule |

**Display, ordering and risk** (`engine/plan`, `rules`):

| # | Scenario | Then |
|---|---|---|
| D1 | Today rows with expected net 5 s (P' 0.9), 20 s (P' 0.3), 10 s (P' 0.6); `min_sell_prob` 0.5 | shown: 10 s, then 5 s; footer "1 hidden" |
| D2 | the same rows with the filter set to 0 | order 20 s, 10 s, 5 s |
| D3 | buy 20 at 25 s; your characters hold 50 g in total | each 25 s, total 5 g, risk 10.0% |
| D4 | post 10 with a 15 s deposit; 50 g in total | risk 0.3% |
| D5 | buy with risk 30% | risk shown amber (> 25%) |
| D6 | craft 3 × Copper Rod (2 Copper Bar each); you own 0 bars; AH 10 s each | craft each 20 s, total 60 s |
| D7 | the same, but you own 6 bars | craft each "—", note "uses 6 owned Copper Bar"; profit still counts their hold value |
| D8 | the same, but you own 4 bars | total 20 s (2 bars to buy); each shown as 6 s 67 c (20 s ÷ 3) |
| D9 | now Fri 14:00; a craft planned Thu 18:00 (day 7) | its post falls before the window ends (next Fri 14:00), or the craft is not planned |
| D10 | a craft for right now (B) | a linked post appears in Week within 7 days |
| D11 | it is Fri 14:30 | the Week starts Fri (today) and ends Thu; the 14:10 buy shows done |
| D12 | Linen's best slot is Sat 18:00 | the post action is Sat 10:00, labelled "for the 18:00 peak"; the duration covers 18:00 |
| D13 | it is Fri 14:30; the best window is Fri 18:00–21:00 with its best hour 19:00 | the post is planned for now (14:30), since 11:00 has passed; chance window Fri 14:00–20:59 (the current hour counts in full; it runs to the end of the selling window) |
| D14 | a craft whose product peaks Thu 19:00 | the post is Thu 11:00, so the craft is planned before 11:00 |
| D19 | Fri 14:10: buy 20 Linen at 38 c (net +2 s 10 c); its follow-up post is Sat 10:00 | the post shows "profit counted on the Fri 14:10 buy" and adds 0; the week total counts 2 s 10 c once |
| D20 | the same chain on Sat, when Fri has left the rolling week; post `E_post` 8 s 55 c (20 × 45 c × 0.95, `P` = 1); paid 7 s 60 c | the post carries its own net: +95 c |
| D21 | post 5 Linen you looted, at 45 c, `P` = 1 | net = 5 × 45 c × 0.95 − 0 = 213.75 c, shown +2 s 14 c |
| D22 | a craft with 2 mat buys and 1 mail step feeding it | the mat buys and the mail show no net ("for …"); the craft carries the chain's profit |
| D23 | a Craft row whose product sells 48/day (AHF) with 93 listed | columns Chance, Est. Sales/Day "48 AHF", Current Supply "93", Supply/Volume "1.9 days", in that order; each has a sort key |
| D24 | a Craft row uses Amaya's bags captured 3 days ago | the row says "based on Amaya's bags from 3 d ago"; a row on today's snapshot says nothing |

**Craft sourcing** (`engine/craft`, `plan`). Recipe: 2 Copper Bar → Copper Rod, which posts at 40 s with `P` = 1:

| # | Scenario | Then |
|---|---|---|
| C1 | you own 6 bars (hold value 9 s 50 c each: posting now is their best option); the AH has no bars (they last sold at 10 s) | Source Owned; 3 crafts; mat gold "—"; profit per craft 38 s − 19 s = 19 s |
| C2 | you own no bars; AH has 6 at 10 s | Source AH; 3 crafts; mat gold 20 s each, 60 s total; profit per craft 38 s − 20 s = 18 s |
| C3 | you own 4 bars; AH has 2 at 10 s | Source Mixed; 3 crafts; mat gold 20 s total; note "4 owned · 2 bought" |
| C4 | you own 4 bars whose hold value is 12 s (their Sat peak); the AH has 6 bars at 10 s | the AH is cheaper than your stock's hold value, so the first 3 crafts use the 6 AH bars; your bars are used only for further crafts that still pay (38 s > 2 × 12 s, so 2 more): 5 crafts, Source Mixed. At a hold value of 20 s (40 s for two, above the rod's 38 s) the bars stay in the sell plan: 3 crafts, Source AH |
| C5 | AH book: 2 bars at 10 s, 2 at 15 s, 2 at 25 s | craft 1 profit 18 s, craft 2: 38 − 30 = 8 s, craft 3: 38 − 50 = −12 s → 2 crafts |
| C6 | a mat has no AH listing, no vendor and none owned | not craftable; listed as "missing: 2 Copper Bar" only if one craft short |
| C7 | the Week plans an AH-sourced craft at Fri 20:30 | a linked "buy 8 Silk Cloth" action at or before 20:30; then the craft; then the post |
| C8 | the mat buy from C7 bought only 4 of 8 | the craft is re-planned for 1 craft instead of 2 |
| C16 | you own 6 bars; posting now is worth 9 s 50 c each, their Sat peak 11 s each; the AH has no bars | hold value 11 s (the sell plan's best option); profit per craft 38 s − 22 s = 16 s, not 19 s; Source Owned |

**Crafting chains.** Runed Copper Rod (Enchanting, Amaya) needs 1 Copper Rod + 1 Strange Dust + 1 Lesser Magic Essence. Copper Rod (Blacksmithing, Brugan) needs 2 Copper Bar:

| # | Scenario | Then |
|---|---|---|
| C9 | AH: Copper Rod 41 s, Copper Bar 10 s; you own none | Copper Rod costs 20 s crafted vs 41 s bought → crafted; Source "AH + crafted"; chain: buy 2 bars → Brugan crafts → mail to Amaya → Amaya crafts |
| C10 | same, but an AH Copper Rod costs 15 s | buy the rod (15 s < 20 s); no chain; no mail |
| C11 | Amaya also knew Blacksmithing | no mail step; Amaya does both crafts |
| C12 | a chain 4 steps deep; `max_craft_depth` 3 | the 4th level is not crafted; that reagent comes from owned / AH / vendor or the recipe is not craftable |
| C13 | recipe A uses B and recipe B uses A | the loop is not followed; each is sourced without the other |
| C14 | Brugan crafted the Copper Rod but the mail step was missed | the rod is owned stock on Brugan; the next run re-plans "mail to Amaya" and the final craft |
| C15 | the Copper Rod cost 20 s to craft; the Runed Copper Rod sells for 59 s, with 7 s Strange Dust and 10 s Essence | profit is counted once on the final product: 59 × 0.95 − (20 + 7 + 10) = 19 s 5 c |

**Missed peaks** (`plan.replan_missed`). Item windows: Tue, Wed and Thu 20:00–23:00. Original peak Tue with `E_orig` = 10 s. `min_sell_prob` 0.5, tolerance 10%, week starts Mon:

| # | Scenario | Then |
|---|---|---|
| M1 | Tue window ended, no post; Wed E = 9 s 50 c (95% of 10 s), P' 0.88 | moved to Wed: "Missed Tue peak → Wed 20:00–23:00 (−5%, 88% chance)"; post Wed 12:00 |
| M2 | Wed missed too; Thu E = 8 s (80% of the original) | hold: "Missed Tue and Wed; Thu is 20% below → hold until next week's best peak" |
| M3 | Tue 10 s, Wed 9 s 20 c (92%) missed, Thu 8 s 60 c (86% of Tue, though 93% of Wed) | hold: always compared with the original Tue, never with Wed |
| M4 | Wed is 96% of the original but P' 0.40 | Wed fails "likely to sell"; Thu is checked next |
| M5 | Wed passes on price and chance, but `E` is below the vendor value | fails "still worth it"; next window, or hold |
| M6 | Mon 00:00 of the next week, item on hold | fresh slate: the anchor is the new week's best window; the misses stay in History only |
| M7 | Tue 18:00, the post time (12:00) passed, but the window (20:00–23:00) hasn't started | "post now (late)"; not missed; chance over 18:00–22:59 |

**Refresh model** (`refresh.refresh_all`, `refresh.update_items`, `sellprob.from_curve`):

| # | Scenario | Then |
|---|---|---|
| IU1 | you buy 20 Linen Cloth (SavedVariables changes) | only Linen is dirty; its options and plan rows are recomputed; other items' rows are untouched; history reads = 0 (counted by a fake `db`) |
| IU2 | a new hourly AHF file arrives | stored; market intelligence, plan and the addon files unchanged; "prices newer than numbers" flag set |
| IU3 | `daily_refresh_time` 06:00 passes while AHSync runs | one full refresh on the next timer tick; none again until the next day |
| IU4 | "Refresh now" pressed twice within a second | one full refresh (the second request is merged in) |
| IU5 | the curve row for the current hour has P(2 ahead) = 0.90 and P(5 ahead) = 0.78; 3 units are now ahead | P = 0.90 − 0.12 × (1 ÷ 3) = 0.86, in both Python and Lua |
| IU6 | an in-game scan changes Silk Cloth's price | only Silk Cloth and the crafts that use it are recomputed |
| IU7 | idle for 10 minutes, no file change | no engine call; one SavedVariables stat every 5 s |
| IU8 | refresh at 06:00; it is now 20:00; the 06:00 row gives P(0 ahead) = 0.40 and the 20:00 row 0.84 (evening buyers) | P = 0.84: the current hour's row, in both Python and Lua |
| IU11 | the simulation gives P = 0.853 for a curve point | exported as 0.86 (2% steps: floor(0.853 × 50 + 0.5) = 43 → 43 ÷ 50), stored as the character `string.char(35 + 43)` = "N"; Python and Lua both read 0.86 |
| IU9 | refresh at 06:00 Fri; it is 08:00 Sat with no refresh since | the 08:00 row from Fri is reused; the age badge is amber (over 24 h) |
| IU10 | a pending buy's slot passes 24 h while AHSync idles with no file change | the :00 status pass marks it missed; history reads = 0; no simulation; `Data.lua` rewritten |
| IU12 | a refresh exports a shortlist item (7 weeks of hour-of-week data, top 3 sell and buy windows) and a non-shortlist item | both carry their top windows; the shortlist item has the 24 × 8 curve and the other the 24 × 3; neither string holds a 168-slot `how`, `sales_how` or `supply_how` (SPEC §3.5: the addon shows no week heatmap) |

**Confidence percentage** (`confidence.score`):

| # | Scenario | Then |
|---|---|---|
| CF1 | n = 60, 7 days of AHF volume, book 1 h old, pattern 7 weeks, tier n = 30 (planned post) | every component 1.0 → 100% |
| CF2 | n = 15, estimated volume, book 12 h old, pattern 0 weeks, no tier history (planned post) | 0.25×0.25 + 0.30×0.7 + 0.15×0.5 + 0 + 0 = 0.3475 ÷ 1.00 → 35% (low) |
| CF3 | the CF2 inputs when posting now (no pattern component) | 0.3475 ÷ 0.85 = 0.409 → 41% (low) |
| CF4 | 4 days of AHF volume + 3 days estimated, other components 1.0, posting now | volume (4 × 1.0 + 3 × 0.7) ÷ 7 = 0.871; conf = (0.25 + 0.30 × 0.871 + 0.15 + 0.15) ÷ 0.85 = 0.955 → 95% (high) |

**Tracking** (`engine/tracking`):

| # | Scenario | Then |
|---|---|---|
| T1 | pending action: buy 20 Linen at ≤ 45 c; you buy 20 at 40 c | action done; cost basis 20 × 40 c = 8 s; its sell action stays planned |
| T2 | the same action; you buy 10 | partial, done_qty 10 |
| T3 | the same action; you buy 20 at 50 c | no match (above the planned price); purchase recorded, cost basis updated |
| T4 | two pending posts of 20 Linen (Mon, Tue); one "expired" mail for 20 Linen | Monday's post resolves as expired; streak +1 |
| T5 | a sold invoice for 20 Linen at 48 c | the oldest pending post at 48 c ×20 resolves as sold; streak 0 |
| T6 | a buy action 25 h past its slot with no match | missed |
| T7 | you hold 20 Linen bought at 40 c; best option now is posting at 35 c | the plan says post at 35 c, flagged "below what you paid" |
| T8 | you bought through the default AH UI, not AHProfit | still captured by the hook and matched like any other buy |
| T9 | 10 Copper Bar found (cost 0) + 10 bought at 10 s | cost basis 100 s ÷ 20 = 5 s each |
| T10 | craft 1 Copper Rod from 2 of those bars, then post it at 41 s | craft cost 10 s; On the AH shows cost 10 s, profit if sold 41 × 0.95 − 10 = 28 s 95 c |
| T11 | post 5 Linen you looted | On the AH shows cost 0 |
| T12 | an item held before tracking started | cost 0, labelled "before tracking" |
| T13 | mail 10 bought Linen from Amaya to Brugan | cost basis unchanged (account-wide) |
| T14 | a post action for the Tue 20:00–23:00 window (post time 12:00); it is Tue 18:00 and nothing is posted | `late`: "post now (late)" |
| T15 | the same action at Wed 00:00, still not posted | `missed`; the missed-peak re-plan runs (M1) |
| T16 | the same action; you post at Tue 21:30 | matches the late action → `done` |
| T17 | a sold mail arrives (sold event), then you take the 9 s 50 c from the mailbox (payout event) | posted, sold and paid out are three journal events; realized profit counts once, at the payout |
| T18 | you disenchant a green into 3 Strange Dust | a `disenchant` event with the item and its results; the dust's cost basis is the green's |

**Architecture** (SPEC §5.0):

| # | Scenario | Then |
|---|---|---|
| AR1 | the same history in SQLite and in an in-memory `HistoryReader` | analysis gives byte-identical `MarketIntelligence` JSON; it never touches SQLite or character state |
| AR2 | `MarketIntelligence` with `schema_version` = current; one with a newer version | the first round-trips through JSON unchanged; the second is refused with "intelligence from a newer AHSync" |
| AR3 | history changes after intelligence was built, with no new analysis | recommendations still use the published intelligence (they never read history) |
| AR4 | every module under `analysis/` | imports only `engine/`, `history.reads`, `intel.contract` and the standard library (convention test) |
| AR5 | analysis of an item with 7 weeks of hourly data: 100c except 50c on Mon 04:00–05:00, 80 units listed every hour, 2 sales per hour except 8 on Tue 18:00–20:00, 48 sales a day | its `ItemIntel` holds `how`, `sales_how`, `supply_how` packed (W13; 168 each, `weeks` = 7 per slot, `supply_how` flat "1000,…", Mon 04:00 `how` 800); one sell window Tue 18:00, 3 h (smoothed sales 5.6 there, 4.4 beside it, under 0.9 × 5.6); one buy window Mon 03:00, 4 h at average `how` 0.8 (0.9 beside it, above 0.8 ÷ 0.9); seasonality 0 (smoothing spreads the dip, so the profile misses by 120c where flat misses by 100c); a 24 × 8 curve starting at the analysis hour; it round-trips through JSON unchanged |
| AR6 | the same intelligence (items A and B, both with 24 × 8 curves) exported with a shortlist of {A} | A's string has its 24 × 8 curve; B's has 24 × 3 equal to its intelligence's 0, 5 and 20 ahead columns; the intelligence itself is unchanged, and analysis output is the same with any shortlist |
| AR7 | analysis of a market with 50 items, then 500 items, against a `HistoryReader` that counts calls | the same number of reader calls both times (bulk per market, never per item) |

**Addon** (Lua, `tests/addon/...`): every scenario for a ported module (`fees`, `sellprob`, `disenchant`, `craft`; see SPEC §5) must pass on the Lua port with identical numbers. Volume (V*) and tracking (T*) scenarios run in Python only. Plus:

| # | Scenario | Then |
|---|---|---|
| A1 | `Capture/*` writes a SavedVariables file with characters, recipes, inventory and events | `ingest/savedvars` parses it back to the same records (round-trip) |
| A2 | at the AH, a search result raises a flip target's price above its profit line; another target is gone | the list re-orders; both targets are dropped |
| A3 | a `MarketIntelligence.lua` with 20k items | loads in Lua 5.1 without hitting the constant limit |
| A4 | `PortfolioPlan.lua` with `ingestedUntil` = T for a character | scans, posts, purchases, crafts and mail at or before T are removed from SavedVariables; later ones are kept |
| A5 | an engine error is raised during a re-rank | caught at the UI boundary and shown as a status line; no Lua error popup; the list keeps its last good state |
| A6 | the AH opens and another addon's `ReplicateItems` answers with 3 auctions (Linen 45c ×20 long, Linen 45c ×20 long, Copper Bar 1s ×5 short) | one `full` scan, coverage 1, rows `2589,45,20,3,2;2840,100,5,1,1`; AHProfit made no ReplicateItems call |
| A7 | the AH opens and no scan result arrives within 30 s | AHProfit calls `ReplicateItems` once; opening the AH again 5 min later doesn't call it again (20-min rule) |
| A8 | a 10,000-auction result | read over several frames, no step over the 4 ms budget (counted by the stub clock); entering combat pauses it and leaving combat finishes it |
| A9 | no `PortfolioPlan.lua`; the intelligence lists Copper Rod (sell value 38 s) and Copper Bar on the shortlist; your bags hold 6 bars; the AH shows bars at 10 s | the addon itself offers "Craft 3 Copper Rod from owned bars" with the same profit the desktop computes for the same inputs (19 s each, C1) |

## 2. Visual tests (UI1–UI11)

Layers and tooling: SPEC §8.4.

| # | Scenario (fixture addon files) | Then |
|---|---|---|
| UI1 | Today tab, 12 mixed actions | 12 rows sorted by expected net; each and total both shown; net values coloured; footer totals correct |
| UI2 | 40 actions | 12 visible, scroll bar shown, nothing drawn outside the list |
| UI3 | item "Scouting Gloves of the Monkey" in a narrow column | truncated with "…"; no overflow |
| UI4 | no `MarketIntelligence.lua` | empty state "Run AHSync, then /reload"; no rows |
| UI5 | data 30 h old | age badge amber |
| UI6 | an item flagged "not selling" | NOT SELLING label, record line, Blacklist and Keep buttons inside the row |
| UI7 | AH panel: 5 targets, 2 found, 1 gone, 2 checking | ✓ / ✗ / … states; "3 of 5"; panel docked to the AH frame's right edge |
| UI8 | tooltip for a disenchantable item | header + 6 label/value lines in the spec order; deltas vs current 8s: historical 11s → +38%, disenchant 14s 25c → +78%, best 12s 30c → +54%, none wider than 320 px; without an enchanter, the Disenchant value line is left out |
| UI9 | window at its minimum size, 560 × 360 | layout checks pass; no overlaps |
| UI10 | Today with 1 row below the "Min chance" filter and 1 "not selling" item | sorted by net; footer "1 hidden"; Needs a decision section at the bottom |
| UI11 | Craft tab with one recipe fully covered by owned mats | its each and total show "—" |

## 3. Views and tooltip (D16–D22, TT1–TT5)

D19–D22 are listed with the display scenarios in section 1.

| # | Scenario | Then |
|---|---|---|
| D16 | Today rows with net 10 s, 5 s, 5 s (P' 0.8 and 0.9) | order 10 s, then the 5 s with P' 0.9, then the other 5 s; footer total 20 s |
| D17 | a Week day with actions +8 s 30 c, +1 g 10 s and −20 c | day total +1 g 18 s 10 c; the category and character totals add up to the week total |
| D18 | On the AH rows from T9–T11 | the listed, cost, profit and deposit tiles equal the sums of their columns |
| TT1 | current 8 s, med30 11 s, DE 14 s 25 c, best 12 s 30 c | deltas +38%, +78%, +54% |
| TT2 | current 10 s, historical 9 s | −10%, shown red |
| TT3 | no current price | values shown, no deltas |
| TT4 | no character can disenchant it | no Disenchant value line |
| TT5 | delta exactly +12.5% | +13% (half up) |

## 4. Calibration (K1–K3)

Rules: SPEC §8.8.

| # | Scenario | Then |
|---|---|---|
| K1 | 10 posts predicted 0.8, 8 sold | bucket 80–100%: predicted 80%, actual 80%; Brier = (8 × 0.04 + 2 × 0.64) ÷ 10 = 0.16 |
| K2 | 25 posts predicted 0.5, 7 sold | bucket 40–60% actual 28%; warning raised (off by 22 points, ≥ 20 posts) |
| K3 | 5 posts predicted 0.5, 0 sold | no warning (fewer than 20 posts) |
| K4 | 10 days with both AHF and book volume: book ÷ AHF ratios 0.8 ×4, 1.0 ×4, 1.5 ×2 | Status: median ratio 1.0; 8 of 10 days (80%) within ±25% |

## 5. Invariants (SPEC §8.7)

How cases are generated, and how many: SPEC §8.7.

**Sell probability and expected value** (`sellprob`):

| # | Invariant |
|---|---|
| PI1 | `0 ≤ P ≤ 1`, and `0 ≤ P' ≤ 1` |
| PI2 | More sales per day, or a longer window, never lowers `P` |
| PI3 | More competing units at or below your price, or posting more units, never raises `P` |
| PI4 | Lowering your price never lowers `P` (fewer listings are ahead of you) |
| PI5 | With no own record, `P' = P`. With a record, `P'` lies between `P` and your own sell rate `s ÷ (s + x)`, and moves toward your rate as you post more |
| PI6 | With `P = 1`: `E_post = q × p × (1 − cut)`, so the deposit is never lost |
| PI7 | With `P = 0`: `E_post = q × fallback − deposit` |
| PI8 | When the sale is worth more than the fallback minus the lost deposit, a higher `P` never lowers `E_post` |
| PI9 | Expected profit never exceeds the profit if everything sells |
| PI10 | With a flat sales profile, no competitor expiring and `u = 0`, the simulation equals the old formula `clamp((S·W/24 − ahead) ÷ q, 0, 1)` |
| PI11 | More undercut arrivals (higher `L` or `u`) never raise `P_queue` |
| PI12 | A competitor that expires sooner (shorter band) never lowers `P_queue` |
| PI13 | A longer window (adding hours at the end) never lowers `P_queue` |
| PI14 | `P` lies between `P_queue` and `ST_tier`, and equals `P_queue` when there is no tier history |
| PI15 | Units sold in the simulation: yours plus competitors' never exceeds the total demand `S × h ÷ 24` |
| PI16 | Raising `sales_how` in any hour of the window never lowers `P_queue` |
| PI17 | Every `P` is computed over the window its context defines (posting now: the next 8 h; planned post: from the post time through the end of its selling window, §3.3.2); no calculation uses another window |

**Other formulas:**

| # | Invariant |
|---|---|
| VI1 | Volume: `sold + expired = gone`, all ≥ 0, and `expired ≤ expiring` |
| CI1 | Craft: the chosen source for a reagent costs no more than any other available source |
| CI2 | Craft: each added craft has a positive marginal profit; the next one would not |
| CI3 | Chains: profit is counted once, so the chain's total equals the final product's profit |
| PL1 | Plan: buys never exceed the budget; no action is before `now_ms`; every craft has a follow-up within 7 days; every post is at peak − `post_lead_hours` (or now) |
| PL2 | Plan: a planned post quantity is never below 1 stack and never above what you hold |
| PL3 | Missed peaks: a replacement's `E` is always ≥ 90% of its original anchor (never of an earlier replacement), its P' ≥ `min_sell_prob`, and it lies in the same plan week. Otherwise the action is a hold |
| PL4 | Plan: for every item and sell window, the units planned across all characters (holds, flips, crafts' output, disenchant mats) never exceed that window's liquidity cap, and no character's plan claims capacity another already used |
| VW1 | Views: every total equals the sum of its rows; filters only hide rows, never change their numbers; each chain's profit appears in exactly one row of any view (§3.4 expected net) |
| LP1 | Python and Lua give identical rounded results on every generated case for the ported modules |
| FI1 | A crash injected at any commit point of catch-up, ingest or refresh, followed by a restart, gives the same final database rows and addon file bytes as an uninterrupted run |
| FI2 | Ingesting any SavedVariables file twice, or in any order with earlier files, gives the same database as ingesting each once in order |
