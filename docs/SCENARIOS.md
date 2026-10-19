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
| V9 | your auction mail on a day: Linen sold in a mail of 2 and a mail of 1, and 1 expired; then read again | `mail` volume for that day: sold 3, expired 1, cover kind `own`; reading again leaves it so; a day without mail has no `mail` row | your own sales are stored as a source |
| V10 | AHF quantity 120 → 80 within 2h, no books | listing turnover 40 recorded; sales per day **unavailable**; chance = default 25%, "volume unknown" | totals alone never become sales |
| V11 | AHF volume for a whole day and your 2 h book estimate on the same day (both group diffs) | AHF used: it observed more of the day | within a kind, more of the day observed wins |
| V12 | 50c ×20 **medium** ×2 → ×0, 2 h apart | expired 40, sold 0 | medium (30 min–2 h) auctions have all run out after 2 h |
| V13 | 50c ×20 long ×3 → ×0, **5 h** apart | expiring 3 × (5−2)/(8−2) = 1.5 → expired 30, sold 30 | long auctions expire in proportion to the gap |
| V14 | no key: book estimates only, 40 sold/day for 7 days | sold/day 40, source "estimated" | free path works on its own |
| V15 | AHF volume 50/day on days 1–20, Premium lapses, estimates 40/day on days 21–30 | 30-day sold/day = (20 × 50 + 10 × 40) ÷ 30 ≈ 46.7; source "20 days AHF, 10 days estimated" | per-day best source |
| V16 | 10 days with both id diffs (50 sold) and whole-day book estimates (40); then 10 book-only days at 40 | factor 50 ÷ 40 = 1.25 from the id days; the book-only days count as 50, even in a window with no id day | id diffs calibrate the group estimates |
| V17 | id diffs and a book estimate on one day | the id diffs are used for that day; the estimate is still stored | estimates never stop |
| V18 | a day with a book estimate of 40 and your mail with 3 sold, and no day with id diffs | no factor: the day counts 40 (mail sees only your auctions, so it never calibrates the market estimate) | mail is a floor, not a calibration |
| V19 | two complete local scans 2 h apart on the same day: Linen 50c ×20 long ×5 → ×3 (long auctions cannot have run out in 2 h) | `books` volume for that day: sold 40; reading the same files again leaves it at 40 | scans feed volume, idempotently |
| V20 | a complete scan at 10:00 and a partial scan (coverage 0.6) at 12:00 | no `books` volume from that pair | partial books never feed volume |
| V22 | sales 50/day, 93 units listed now; another item with sales unavailable; a third with sales 0 | supply/volume 1.86 days; "—"; "—" | the ratio is never made up |
| V23 | median daily supply over 30 days of observations 80, 100, 120 units, and 10 days without any observation | median 100; the empty days are left out, not counted as 0; a day's value is the median of its observations (90 and 110 on one day count as 100); with no current supply the liquidity cap uses it (`supply_level`) | missing data is not zero |
| V24 | `VOLUME_METHOD_VERSION` goes from 1 to 2 with 20 days of stored books, and 5 older days of `books` rows whose scans are pruned | the next full refresh recomputes those 20 days' `books` volume and its cover, stamps them version 2, and leaves AHF and mail rows unchanged; the 5 days keep their version-1 rows (a whole day each, as before) and Status says "5 days of sales estimates are from an older method"; a refresh with nothing stale changes nothing and clears that line | estimates can be recomputed |
| V25 | Linen 50c ×20 long ×5 at 10:00 → ×3 at 11:00 → ×2 at 14:00 | `sales_h1` 10:00: 40 per hour (40 sold ÷ 1 h); no row from the 11:00 → 14:00 pair (3 h apart) | hourly rates need books at most 2 h apart |
| V26 | Linen ×20 long ×5 at 10:00 → ×4 at 10:30 → ×2 at 11:00 | both pairs start in the 10:00 hour: (20 + 40) sold ÷ (0.5 + 0.5) h = 60 per hour | several pairs in one hour pool their sold and gap |
| V28 | an AHF book with bands 0 ×7, 2 ×1, 3 ×2, 4 ×3 and no band 1 (AuctionHouseFYI's addon uploads Forever's 1–4 time left + 1, and very long as 0) | read as very long ×7, short ×1, medium ×2, long ×3; a book that has band 1 is read as WoW's codes, with 0 as very long | volume needs the true bands |
| V27 | an AHF Premium book at 10:00, a partial local scan (coverage 0.6) at 10:20, a complete local scan at 11:00; the same files are read again | one pair, AHF 10:00 → local 11:00; the partial scan is skipped; reading again leaves `sales_h1` unchanged | either source pairs; partial books never do; idempotent |
| V29 | an AHF book at 18:00, your complete scan at 18:40 and an AHF book at 19:00: Linen 50c ×20 long ×5 → ×4 → ×3 | the day's `books` volume: 40 sold from both pairs; its cover: kind `groups`, 3600 s observed in hour 18 and none elsewhere; storing the AHF books after your scan gives the same | books of every source pair for daily volume |
| V30 | two complete scans 10:00 → 12:00 show Linen 40 sold; the sales pattern is flat | sold/day 40 ÷ (2/24) = 480, not 40; that day's volume confidence 0.3 + 0.5 × 2/24 ≈ 0.34 | a partly observed day is scaled to a day, not taken as one |
| V31 | the same 2 h sample on a Friday, whose sales pattern puts 30% of the day's sales in 10:00–12:00 | sold/day 40 ÷ 0.3 ≈ 133 | weighted by the hourly pattern, so a busy-hour sample isn't spread flat over the night |
| V32 | a day with your mail (3 sold), a whole-day book estimate (40) and a source "blizzard" whose cover is kind `ids` (50); another day with two group sources, one observing 2 h and one 6 h; a day with only listing turnover | the first day uses "blizzard" (50: no code names it, its kind does); the second uses the 6 h one; the third has no sales source | sources rank by method and coverage, not by provider |
| V33 | two snapshots with auction ids, 1 h apart: Linen id 1 (×20, long) gone; Linen id 3 (×5, short) gone; Linen id 4 (×10) new; Copper Ore id 2 (a commodity) 200 → 150 units | Linen sold 20 (a long auction can't run out in 1 h), expired 5 (a short one surely has), listed 10; Copper Ore sold 50 (a partial buy, measured exactly), nothing new | auction ids tell partial buys and expiries apart |
| V34 | a whole-day book estimate of 0 Linen sold, and your mail says you sold 6 that day | the day counts 6 | the market never sold less than you did |
| V35 | 7 days that only your mail observed, 3 Linen sold each day | sold/day 3 from `mail`; the volume confidence component 0.4 | your mail is the last-resort source, trusted least |
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
| S12 | a Horde character on "Classic Beta PvP", a realm not in `[realms]`; the config syncs only forever.pvp.horde.us; then a second Horde market forever.normal.horde.us is added | the character maps to forever.pvp.horde.us, the only Horde market you sync, and its scans are stored; with two Horde markets it has no market and Status asks for `[realms]`; `[realms]` "Classic Beta PvP" = "normal" always wins |
| S14 | SavedVariables say Amaya keeps 10 Linen and all her Silk (`reserves = { ["Amaya-Grommash"] = "2589,10;4306,-1" }`); a newer save keeps only 5 Linen; then an older save (an earlier `updated`) still lists the Silk | AHSync stores (Amaya, 2589, 10) and (Amaya, 4306, all); the newer save replaces them with (Amaya, 2589, 5), and Linen and Silk are marked dirty; the older save changes nothing. A malformed reserve string is set aside with a problem and leaves her stored reserves alone |
| S15 | Amaya's stored reserves: Linen 5 from the dashboard at T + 1 h, Bars 3 from an older addon (no time); a file with `reserveTimes` keeping Linen 10 at T (older), clearing Silk at T + 2 h, and keeping all Wool at T + 2 h; then a file from an older addon (no `reserveTimes`) keeping Copper Ore 4 | after the first: Linen 5 (dashboard, newer), Silk cleared (qty 0 at T + 2 h), Wool all, Bars 3 kept; after the second: Copper Ore 4 and Bars gone (the older addon's set is replaced), Linen 5 and Wool unchanged |
| S16 | an own-auctions snapshot at T with `ends` "6217,1,T+5h,4100;6217,1,T+7h,4100;2589,20,T+20h,40" (two rods in band 3, the Linen in band 4) | the rod group's soonest end is T + 5 h, the Linen's T + 20 h; a snapshot without `ends` (an older addon) stores none |
| S13 | SavedVariables name the bound Hearthstone (6948) and Linen Cloth (2589); AHF's item list has Linen but not the Hearthstone; later AHF lists the Hearthstone too | the Hearthstone is stored as "Hearthstone", quality 1, so Inventory shows its name instead of 6948; Linen keeps AHF's row; once AHF lists the Hearthstone, AHF's row replaces the addon's |

**Failure modes** (§5.2). Faults are injected deterministically by the fakes:

| # | Scenario | Then |
|---|---|---|
| FM1 | 503 on hour 40, five times in a row | backoff 1, 2, 4, 8, 16 s on the fake clock; run ends; `sync_state` = hour 39; the next run starts at 40 |
| FM2 | hour 41's file cut off at 60% | not committed; logged; refetched next run |
| FM3 | the same hour bad 3 runs in a row | Status "bad file: hour 41"; later hours still sync |
| FM4 | the snapshot has a renamed field | snapshot disabled this run; last good `current` kept; Status "AHF format changed: snapshot" |
| FM5 | Linen `cur` 1 c while `p10_30` is 30 c (1 c is below 10% of it, 3 c); likewise 400 c over a `p90_30` of 35 c | flagged suspect (`cur_suspect`): no flip, no AH mat buy from its book and no disenchant buy; a second hour whose close is also out of range (two in the last 24 h) clears the flag |
| FM31 | 30 days of Linen closes around 45 c with one hour at 1 c, then the same with two 1 c hours 3 h apart | the lone 1 c hour is left out of med7, med30, trend and the weekly pattern (the medians stay 45 c); the two close together confirm each other and stay in; p10 and p90 use every close |
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
| FM18 | the plan's `ingestedUntil` for Amaya is older than her confirmed buy of 20 Linen, her confirmed post of 5 Copper Bar (plan: post 10) and a craft of Copper Rod; an unconfirmed buy attempt; an older buy AHSync already read | the Linen buy is gone (done), the bar post shows 5 left, the rod craft is gone; the attempt and the older buy change nothing; Today's and Week's footers start "provisional: 3 done in game since AHSync's plan · "; after /reload the same (the events are saved) until AHSync writes a newer plan |
| FM19 | AH closed at 40% of a full scan | saved as partial (coverage 40%); excluded from volume diffs |
| FM20 | buy hook fires, then "not enough gold" | an attempt only; it expires after 1 h; no cost basis change |
| FM21 | crash before logout; at the next login 20 Linen are missing from bags | recorded as an untracked change; no invented sale or cost |
| FM22 | SavedVariables at its cap with 13 un-ingested full scans | the oldest scan is dropped; all events kept; warning shown |
| FM23 | migration from SavedVariables v1 to v2 fails halfway | v1 data untouched (the migration runs on a copy, and the untouched table is what WoW saves); the error is printed in chat once and the window's footer reads "not recording: migration 1 -> 2 failed: …"; nothing deleted: capture is off and the login prune does not run |
| FM24 | the AH is closed and opened again after 1 of 3 rows was searched | this visit's searches are forgotten: rows no scan or search has priced show "checking", and the next search counts "1 of 3" again |
| FM25 | the key is expired: `/files` returns 401 with the key and 200 without it | the run continues on the free path; Status "API key rejected"; no traceback, no key in the log |
| FM26 | a 10-day gap with Premium; h1c returns 403 from the first h1c hour | h1c disabled after that request; no further h1c requests; those hours reported lost (or day-only where d1c covers them); public h1 hours still sync |
| FM27 | AHF renames a field in the hour candle files | "AHF format changed: h1" on Status; the hour loop stops; `last_hour` unchanged; no "bad file" rows; after the parser is fixed, the next run fetches those hours |
| FM28 | a client opens a dashboard connection and sends only half a request line | other requests are still answered at once; the stalled connection is dropped after 10 s; stopping AHSync doesn't wait for it |
| FM29 | the stored Craft page is corrupt (not valid JSON) | `GET /api/views/craft` answers `503 {"error": "bad_view"}` and logs it; the connection is not dropped; the next full refresh replaces it |
| FM30 | at the AH, the client has no `AuctionHouseFrame:SetPostItem` (or the item can't be sold on the AH) and Amaya clicks a Post row | the status says "Sell tab unavailable" (or "Copper Rod can't be sold on the AH"); no Lua error, nothing is posted |

**Dashboard security** (`web/server`, §3.5):

| # | Scenario | Then |
|---|---|---|
| SEC1 | `POST /api/skip` with the right token and `Origin: http://127.0.0.1:8765` | accepted; the action is skipped |
| SEC2 | the same request with `Origin: https://evil.example` | `403`; nothing changed; logged without the token |
| SEC3 | the same request with no `X-AHSync-Token` | `403`; nothing changed |
| SEC4 | any request with `Host: evil.example:8765` (DNS rebinding) | `403`; no data in the response |
| SEC5 | `GET /api/skip?id=…` | `405`; nothing changed |
| SEC6 | every response in the suite | no `Access-Control-Allow-Origin` header; the API key never appears |
| SEC8 | `POST /api/rule {"key": 2318, "rule": "keep"}` with the right token and Origin; then the same with `"rule": "maybe"`, then with a wrong Origin | the first stores the rule as `keep` from the dashboard (newer than any older rule), re-plans and rewrites `PortfolioPlan.lua`, answering 200 {"set": true}; the second is 400; the third 403 with nothing stored |
| SEC9 | `POST /api/reserve {"char": "Amaya-Grommash", "key": 2589, "qty": 5}` with the right token and Origin; then qty −2; then a character AHSync doesn't know; then a wrong Origin | the first stores Linen 5 for Amaya from the dashboard at now, re-plans and rewrites `PortfolioPlan.lua`, 200 {"set": true}; the second and third are 400; the fourth 403 with nothing stored |
| SEC10 | `GET /api/items?q=linen` without a token; then `POST /api/items` | the GET answers the search and changes nothing (a read, like the pages); the POST is 404 (not a write path) |
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
| R6 | you own 200 Linen; others list 93; Saturday is the peak | plan spreads posts over windows, each within its supply cap, Saturday preferred |
| R7 | the DE value of an owned green beats its AH sale | plan says disenchant, with the character who can |
| R8 | buy at 1 s: A resells at 3 s but `P` 0.4; B resells at 1 s 50 c with `P` 0.95 (no deposit, nothing back if unsold) | A: 0.4 × 3 s × 0.95 − 1 s = 14 c (14%): left out; B: 0.95 × 1 s 50 c × 0.95 − 1 s = 35 c (35%): listed. The chance to sell counts, not the price spread |
| R9 | budget 50 g | plan buys stay ≤ 50 g |
| R10 | a soulbound item in your bags | not in the sell plan |
| R11 | Main and Alt are both Blacksmiths; only Alt knows the recipe | the craft row names Alt; Main is never suggested |
| R12 | the lowest listing (50 c) is your own; the next is 55 c; `med7` 60 c | post price 54 c: you don't undercut yourself |
| R13 | a flip at 10 s with `P` = 0.5, posting at 30 s, vendor sell 1 s, deposit 1 s | E = 0.5 × 28 s 50 c + 0.5 × (1 s − 1 s) = 14 s 25 c; profit 4 s 25 c; still listed only if ≥ 20% margin (it is: 42.5%) |
| R14 | you own 200 Linen in stacks of 20; others list 93 now, flat supply pattern; `max_supply_share` 0.5 | each window takes max(1 stack = 20, floor(0.5 × 93) = 46) = 46 units, so 2 stacks (40); the rest is spread over later windows, then held | you never become most of the market |
| R17 | others list 93 Linen now, at an hour whose `supply_how` is 1.0; a window whose hours average `supply_how` 1.5 | that window's cap is floor(0.5 × 93 × 1.5) = 69 units: a fuller hour allows more |
| R18 | a green (gear) you own 3 of; others list 0, then 3; `max_supply_share_gear` 1.0, `min_gear_units` 2 | a window takes 2 when nothing else is listed, and 3 when others list 3 (you may double the market); materials keep the 0.5 share | one or two pieces always go up, even on an empty market |
| R20 | Amaya holds 20 Linen and keeps 10; she holds 7 Silk and keeps all of it; Brugan holds 4 Linen and keeps none | the plan's lots are Amaya 10 Linen and Brugan 4 Linen, with no Silk lot; craft mats on hand count 14 Linen and no Silk. Reserves are per character: Amaya's never touch Brugan's stock | kept units are never sold, disenchanted, vendored or crafted with |
| R21 | Small Venom Sac: cheapest 90 c (7 listed), med7 1 s 3 c, med30 20 s 94 c (old 20 s listings sat unsold for weeks), 58 sold a day, a flat weekly pattern | planned posts and flips price from the price level, med7: a window post is 1 s 3 c, not 20 s 94 c, and buying at 90 c to sell at 1 s 3 c misses the 20% flip margin, so there is no flip. With no med7 the level is med30 | a forecast starts from what the item sells for now, not a month-old level |
| R22 | a vendor sells Simple Wood at 38 c in unlimited supply; the AH lists 20 at 30 c and 5 at 60 c; you own 9 whose own sell plan values them at 60 c; Basic Campfire Kit takes 1 wood | the AH is no source for the wood: the craft uses your 9 at 38 c each (an owned unit is worth no more than buying another from the vendor), then buys the rest from the vendor at 38 c, never the 30 c listings; the wood is never planned as a flip | a vendor-sold item's AH price is ignored for buying |
| R23 | you hold 20 Simple Wood (a vendor sells it at 38 c; a merchant buys it at 9 c; the AH lists it at 60 c) and 5 Coarse Thread (vendor-sold, a merchant pays nothing); you know a recipe whose product a vendor sells | the wood is planned as a vendor sale at 9 c, never a post or a hold; the thread gets no action; the vendor-sold product is never crafted | posting what anyone can buy from a vendor is a waste of time |
| R19 | all your gold is 30 g, so `max_risk_pct` 25 allows 7 g 50 s per buy; Silk (stack 20) is listed as 10 at 40 s and 20 at 41 s; the flip would take both | it buys only the 10 at 40 s (4 g): adding the 20 would make 12 g 20 s. One stack is the default, but a partial stack is fine | a buy never puts too much of your gold at risk |
| R15 | three disenchantable greens each yield 2.5 Strange Dust; others list 2 dust, so its cap is max(1 stack of 1, floor(0.5 × 2) = 1) = 1; the buys are profitable | only the best-profit green is recommended (its 2.5 dust already exceeds the cap; the others would add more unsellable dust) |
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
| D7 | the same, but you own 6 bars | craft each "—", note "uses 6 owned Copper Bar" (on the Craft page, the line "use 6 owned Copper Bar", D44); profit still counts their hold value |
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
| D24 | a Craft row uses Amaya's bags captured 3 days ago and Brugan's bank from 2 days ago | the row says "based on Amaya's bags from 3 d ago, Brugan's bank from 2 d ago"; a row on today's snapshot says nothing; a snapshot exactly 24 h old says nothing |
| D25 | Inventory: Amaya holds 20 Linen in bags and 10 in the bank; the plan posts all 30 Sat 10:00 at 55 c for the 18:00 peak, expected 1,567.5 c (30 × 55 c × 0.95, `P` = 1) | both rows say "Post Sat 10:00 at 55c (18:00 peak)" with chance 100%; worth 10 s 45 c (20/30) and 5 s 23 c (10/30 = 522.5, half up); Worth tile 15 s 68 c |
| D26 | Brugan holds 12 Copper Bar; the plan posts 6 now at 12 s (expected 6 × 12 s × 0.95 = 68 s 40 c) and a planned Copper Rod craft uses the other 6, whose hold value is 10 s each | plan "Post 6 now at 12s · 6 for crafting Copper Rod"; worth 68 s 40 c + 6 × 10 s = 1 g 28 s 40 c |
| D27 | a soulbound ring in Brugan's bags; 4 Broken Fang that you set to ignore | "Bound · not planned" and "Ignored · not planned", worth "—"; the Not planned tile says "2 items" |
| D28 | Amaya's Scouting Gloves; the plan has Brugan disenchant them, expected 6 s 25 c | Amaya's row says "Disenchant (Brugan)", worth 6 s 25 c, no chance |
| D29 | 20 Linen with a cost basis of 38 c each; 7 Wool Cloth with none | Linen: paid each 38 c, paid total 7 s 60 c; Wool: paid 0 c, "before tracking"; Paid tile 7 s 60 c |
| D30 | item detail: `how` 1.2 on Sat 20:00 UTC and 1.0 elsewhere, 4 weeks per slot except Mon 00:00 with none; local time is UTC + 2 h | the price heatmap's brightest cell (level 4) is local Sat 22:00, titled "Sat 22:00 ×1.20 · 4 weeks"; the 1.0 cells are level 0; local Mon 02:00 has no level ("no data") |
| D32 | Status: `bad_file` lines for hours 497640–497660 of forever.pvp.horde.us (Fri 9 Oct 00:00–20:00 UTC), each failed 4 times; local time is UTC | one alert "AHF files failing": "21 hours of forever.pvp.horde.us failed 4 times: Fri 9 Oct 00:00–21:00. …", not 21 alerts |
| D33 | Status at Sat 17 Oct 23:00: the newest AHF price is from Fri 9 Oct 21:14; the same market with a price from 21:30 | the first market's badge says "data 8 d old" and a warning "AHF data is old" names Fri 9 Oct 21:14; the second is "up to date" with no warning (old means over 6 h) |
| D34 | Status: an `unknown_realm` line for "Classic Beta PvP" (Horde), which happens only when you sync several Horde markets (S12); that character has no market | the alert says how to fix it: `[realms]` `"Classic Beta PvP" = "pvp"` in config.toml, then restart; the Characters card shows the character with "no market" |
| D35 | Status lines at levels info, error and warn | alerts are ordered error, warn, info (ties: title, then topic) |
| D36 | Status: `premium_until` 23 Oct at warn level (under 2 days left); no key set | the API card's badge is "Premium until 23 Oct", with "upload scans to keep it"; the key line says "not set · public data only" |
| D37 | Week: Fri 14:10 buy 20 Linen (net +2 s 10 c); its post Sat 10:00 is for the 18:00 peak | the post's note reads "for the 18:00 peak · profit counted on the Fri 14:10 buy", on the dashboard and in the addon |
| D40 | Inventory: Amaya holds 20 Linen, keeps 10 and the plan posts the other 10 now at 55 c; she keeps all 7 of her Silk | Linen's plan reads "Keeping 10 for yourself · Post 10 now at 55c"; Silk's reads "Kept for yourself" (filter `kept`), and its worth is still counted at its hold value |
| D41 | a craft chain buys 7 Simple Wood, which a vendor sells at 38 c, and 2 Copper Bar on the AH | the wood's buy is stored and exported with source vendor and its note says "from a vendor" ("from a vendor · for crafting Basic Campfire Kit"); the bars' buy has source ah and no such note; a flip's buy is always ah |
| D44 | the Craft page's Linen Belt (C19) also buys 2 Coarse Thread from a vendor (20 c) and 1 Wool Cloth on the AH (33 c), and a Bolt of Linen another character crafts first | the row has one grey line per mat, by source, like the addon (A34): "use 10 owned Linen Cloth · mail 6 from Cara", "buy 2 Coarse Thread from a vendor · 20c", "buy 1 Wool Cloth on the AH · 33c", "craft Bolt of Linen Cloth first (Brugan)"; Recipe known by shows Amaya |
| D45 | the Craft page's Basic Campfire Kit (C17: 100 crafts uncapped) while others list 1 kit; then you hold 1 kit; then you have 1 s of gold | 1 craft (the liquidity cap for the posting-now window, as in the addon, A36); with 1 held: no row (D46), counted as already held or listed; with 40 s of gold: no row either, since 20 s of bars is over 25% of it |
| D46 | the Craft page with 6 known recipes: Copper Rod pays; Charred Wolf Meat's mats cost more than it brings; Linen Bandage lacks mats nobody sells; Basic Campfire Kit is already held up to its cap; Heavy Linen Bandage would pay but its mats are over 25% of your gold | only Copper Rod is a row; the footer reads "1 recipe worth crafting · 4 not shown: 1 doesn't pay now · 1 lacks mats · 1 over your risk limit · 1 already held or listed" |
| D47 | History over Amaya's last 30 days: she posted 1 Copper Rod at 41 s for 24 h (deposit 20 c), it sold (its post resolved), she took 39 s 15 c (realized +19 s 15 c); she bought 20 Linen at 38 c matching a planned buy (done) and 6 of 10 Iron Ore (partial); a vendor buy of 4 Coarse Thread at 10 c; a craft of 1 Copper Rod; a disenchant that gave 2 Strange Dust; a mail of 5 Linen to Brugan; a planned Silk buy whose slot passed (missed) and one you skipped; an event 31 days old | one row each, newest first: "Posted" detail "24 h · deposit 20c"; "Sold" "post Mon 08:53 resolved" (the post's time); "Paid out" price 39s 15c, "gold taken from the mailbox · realized +19s 15c"; "Bought" plan "✓ done", "paid 7s 60c"; "partial 6/10"; "Bought (vendor)"; "Crafted"; "Disenchanted" "gave 2 Strange Dust"; "Mailed" "to Brugan"; "—" with plan "missed" ("buy slot passed, nothing matched") and "skipped"; times "Thu 07:53" within 6 days, else "1 Oct 08:53"; nothing older than 30 days |
| D48 | the same journal | tiles: Sold (30 d) "1 auctions", "38s 95c after the cut"; Expired (30 d) with the cancelled count; Deposits lost (the deposits of expired posts); Plan actions "N done" with missed and skipped; Realized profit "+19s 15c" from 1 payout |
| D49 | Characters: Amaya-Grommash (MAGE 38, Horde, forever.pvp.horde.us, 21 g, Tailoring 160/225, Enchanting 142/225, 37 recipes, seen 2 h ago) and Brugan-Grommash with a `[budgets]` override of 10 g; Cira on an unmapped realm with no class or professions | cards by id: "Amaya" "Mage · 38": Realm "Horde · Grommash (pvp)", Gold (budget) "21g", Known recipes "37", Last seen "2 h ago", bars Enchanting "142 / 225" 63%, Tailoring "160 / 225" 71%; Brugan's line reads Budget "10g (set in config.toml)"; Cira's Market "none · realm not in config.toml" and no bars; with no characters the page says how to add one |
| D50 | Rules: Heavy Leather is flagged not selling (0 of 3 sold, last expired Mon 08:53), Brugan holds 10; Bolt of Linen was kept on Mon 08:53 and has sold 1 since; Medium Leather kept with nothing sold since; Coarse Stone is "no_buy" from the addon and Light Leather "ignore" from the dashboard; an item flagged not selling that already has a rule | Waiting for a decision lists Heavy Leather only, with its record line, "Mon 08:53" and owned "10 (Brugan)", and Blacklist / Keep; Kept lists Bolt of Linen "1 sold · 0 expired" "selling again" and Medium Leather "watching: an expiry while cheapest asks again"; Blacklist lists Coarse Stone "Don't buy or craft" ("owned stock still sold", from addon) and Light Leather "Ignore completely" (from dashboard); every kept or blacklisted row has Undo, which posts rule "none" |
| D51 | Amaya's bags snapshot goes from 20 Linen, 5 Herb Baked Egg, 2 Copper Bar and 3 Silk to 32 Linen, 0 eggs, 0 bars, 1 Copper Rod and 1 Silk; between them she crafted the rod from 2 bars and posted 2 Silk | untracked: Linen +12 ("+12: looted, traded or found · cost 0 · no recorded event") and eggs −5 ("−5: used, sold to a vendor or destroyed · …"); the rod and bars are explained by the craft; Silk is left out (posted); the same file read again adds nothing; the first snapshot of a character has nothing to compare with |
| D52 | Inventory: Amaya holds 20 Linen and keeps none | the row's Keep control offers Keep all, Keep a number, Sell normally; picking Keep a number with 5 posts {char: "Amaya-Grommash", key: 2589, qty: 5} to `/api/reserve`; the row's char_id carries the full character id |
| D53 | On the AH: 2 Copper Rods at 41 s ending in 5 h; the rod's book has 12 units at 38 c–40 c below you and 3 at 45 c; Linen at 40 c with nothing cheaper and no end time known (band 3) | the rod row says "ends in 5 h" and "undercut by 12 at 38c–40c", its chance is the queue model's for 2 units at 41 s over a 5 h window; the Linen row says "2 – 8 h" and "cheapest"; an auction whose end has passed says "ending" with a 0% chance |
| D54 | the item list has Linen Cloth, Bolt of Linen Cloth, Heavy Linen Bandage, Wool Cloth and an unknown-suffix Linen key; you type "linen" in the Blacklist card's search | `GET /api/items?q=linen` answers Linen Cloth first (it starts with the text), then Bolt of Linen Cloth and Heavy Linen Bandage (by name); never the unknown-suffix key; "%" or "_" match only themselves; under 2 characters answers nothing; picking "Ignore completely" on a result posts {key, rule: "ignore"} to `/api/rule` |
| D55 | Inventory: you click a row's Keep dropdown, a button or the item link | the dropdown stays open and the row is not selected (a click on the row anywhere else still selects it and shows its detail) |
| D56 | the dashboard's Craft page with the plan's chains: Bronze Bar (buy 22 Copper Ore and 22 Tin Ore, craft Copper Bars and Tin Bars first, post Sun 23:56 for the 19:00 peak) and Tin Bar (buy 40 Tin Ore, post Fri 11:00); Amaya also knows Copper Rod, which the plan doesn't craft | rows: the planned chains first, best net first, source "Plan", with the same lines as the addon (A63); then the Copper Rod as the page's own idea when it pays; no own row for Bronze Bar or Tin Bar; the footer starts "2 planned · " |
| D42 | Heavy Leather: your record is 0 of 3 sold, all 3 expired while cheapest (streak 3); its post now shows P' 46% | the row's record line reads "Your record (30 d): 0 of 3 sold · expired 3× while cheapest · P 80% → 46% · price −14% · qty 1/8" (P from P' = (k·P + s) ÷ (k + s + x) with k 4: 0.457 × 7 ÷ 4 = 0.8; price 1 − 0.95³ = 14%; qty 0.5³ = 1/8); an item with no record shows none; a clean record of 2 sold shows only "Your record (30 d): 2 of 2 sold" and its P |
| D43 | Heavy Leather is flagged not selling and Amaya still holds 5 | Today's Needs a decision section lists it with its record line and Blacklist (don't buy or craft / ignore) and Keep buttons; an item that isn't flagged is not listed |
| D39 | Week: a craft chain buys 7 Simple Wood for a Basic Campfire Kit craft; 2 Small Egg are held for a later week | the wood shows net "—" and "for crafting Basic Campfire Kit"; the eggs show net "—" (a hold sells nothing now) and "hold for a later week"; neither changes a total; the same in the addon |
| D38 | Today at Fri 14:10: that buy, a craft whose post is Sat 11:00 for the 19:00 peak, and a post of owned Wool now for the 20:00 peak | the buy's note reads "Flip: post Sat 10:00, for the 18:00 peak", the craft's "Then: post Sat 11:00, for the 19:00 peak", the Wool post's "for the 20:00 peak"; the addon shows the same lines under its rows |
| D31 | selling windows Sat 18:00–22:00 UTC (7 weeks) and Sun 20:00–23:00 UTC (2 weeks); local time is UTC − 1 h | Inventory's Best selling windows "Sat 17–21 · Sun 19–22 (early pattern)"; item detail lists "Sat 17:00–21:00" established · 7 weeks and "Sun 19:00–22:00" early pattern · 2 weeks, each with its price uplift |

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
| C19 | Linen Belt uses 10 owned Linen Cloth; Amaya and Brugan know it; Amaya holds 4 Linen, Brugan 3 and Cara 6 | the crafter is Amaya (she holds the most of what it uses; ties go to the first crafter by id); the other 6 come by mail from Cara, then Brugan, most first: "mail 6 from Cara" |
| C17 | Basic Campfire Kit takes 1 Simple Wood, which a vendor sells at 38 c and the AH doesn't list; each kit is worth 94 c; you own none | Source Vendor; 100 crafts (the backstop for a flat value); mat gold 100 × 38 c = 38 s; profit 100 × (94 − 38) = 56 s. The same in Python and Lua (shared scenario) |
| C18 | the same, but you own 4 Simple Wood worth 30 c each to hold | the owned wood goes first (30 c < 38 c): Source Mixed; mat gold 96 × 38 c = 36 s 48 c; profit 94 s − 4 × 30 c − 36 s 48 c = 56 s 32 c (shared scenario) |
| C16 | you own 6 bars; posting now is worth 9 s 50 c each, their Sat peak 11 s each; the AH has no bars | hold value 11 s (the sell plan's best option); profit per craft 38 s − 22 s = 16 s, not 19 s; Source Owned |

**Crafting chains.** Runed Copper Rod (Enchanting, Amaya) needs 1 Copper Rod + 1 Strange Dust + 1 Lesser Magic Essence. Copper Rod (Blacksmithing, Brugan) needs 2 Copper Bar:

| # | Scenario | Then |
|---|---|---|
| C9 | AH: Copper Rod 41 s, Copper Bar 10 s; you own none | Copper Rod costs 20 s crafted vs 41 s bought → crafted; Source "AH + crafted"; chain: buy 2 bars → Brugan crafts → mail to Amaya → Amaya crafts |
| C10 | same, but an AH Copper Rod costs 15 s | buy the rod (15 s < 20 s); no chain; no mail |
| C11 | Amaya also knew Blacksmithing | no mail step; Amaya does both crafts |
| C12 | a chain 4 steps deep; `max_craft_depth` 3 | the 4th level is not crafted; that reagent comes from owned / AH / vendor or the recipe is not craftable |
| C13 | recipe A uses B and recipe B uses A | the loop is not followed; each is sourced without the other |
| C14 | Brugan crafted the Copper Rod but the mail step was missed | the rod is owned stock on Brugan; the next run re-plans only the final craft (Amaya buys the dust and crafts the Runed Copper Rod), crafting no second rod; the plan has no mail action for owned stock (your choice, 2026-10-18): the Craft pages say "use 1 owned Copper Rod · mail 1 from Brugan" (D44) |
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
| M8 | a missed Linen post whose replacement window prices it at 49 c; Linen's stored record has a streak of 1 (expired once while cheapest) | the replacement is priced as the plan would: 49 × 0.95 = 46.55, rounded down to 46 c (as `repriced` does), and its P' blends the record; without a stored record it is 49 c; a bigger cut that drops it below 90% of the original peak makes it a hold (M2); the status pass still reads no history |
| M7 | Tue 18:00, the post time (12:00) passed, but the window (20:00–23:00) hasn't started | "post now (late)"; not missed; chance over 18:00–22:59 |

**Refresh model** (`refresh.refresh_all`, `refresh.update_items`, `sellprob.from_curve`):

| # | Scenario | Then |
|---|---|---|
| IU1 | you buy 20 Linen Cloth (SavedVariables changes) | the re-plan reads no history and rebuilds no intelligence; the plan is re-planned whole (budgets and windows are shared, §3.1.1), so Linen's rows change and every other item whose inputs didn't change keeps the same rows |
| IU2 | a new hourly AHF file arrives | stored; market intelligence, plan and the addon files unchanged; "prices newer than numbers" flag set: every page response carries `prices_newer: true` (an AHF price observed after the published intelligence was built), and the dashboard's pill reads "numbers from … · prices newer than numbers · Refresh now"; false before any newer price and before the first refresh |
| IU3 | `daily_refresh_time` 06:00 passes while AHSync runs | one full refresh on the next timer tick; none again until the next day |
| IU4 | "Refresh now" pressed twice within a second | one full refresh (the second request is merged in) |
| IU5 | the curve row for the current hour has P(2 ahead) = 0.90 and P(5 ahead) = 0.78; 3 units are now ahead | P = 0.90 − 0.12 × (1 ÷ 3) = 0.86, in both Python and Lua |
| IU6 | after the last full refresh, an in-game scan lists Silk Cloth as one stack of 12 at 90 c and two of 12 at 95 c, where the intelligence had 1 s; Linen is not in the scan | before the re-plan, Silk's `cur` becomes 90 c with `curQty` 12, its book and current supply (36) come from the scan, its price age is the scan's; Linen and the stored intelligence's build time are unchanged; the plan and the Craft page then use 90 c; AHF prices that arrived meanwhile are not applied (IU2) |
| IU7 | idle for 10 minutes, no file change | no engine call; one SavedVariables stat every 5 s |
| IU8 | refresh at 06:00; it is now 20:00; the 06:00 row gives P(0 ahead) = 0.40 and the 20:00 row 0.84 (evening buyers) | P = 0.84: the current hour's row, in both Python and Lua |
| IU11 | the simulation gives P = 0.853 for a curve point | exported as 0.86 (2% steps: floor(0.853 × 50 + 0.5) = 43 → 43 ÷ 50), stored as the character `string.char(35 + 43)` = "N"; Python and Lua both read 0.86 |
| IU9 | refresh at 06:00 Fri; it is 08:00 Sat with no refresh since | the 08:00 row from Fri is reused; the age badge is amber (over 24 h) |
| IU10 | a pending buy's slot passes 24 h while AHSync idles with no file change | the :00 status pass marks it missed; history reads = 0; no simulation; `PortfolioPlan.lua` rewritten |
| IU13 | Amaya buys 7 Simple Wood and crafts 7 Basic Campfire Kits, then /reloads while AHSync runs | within a few seconds the dashboard's Today, Week, Inventory and Craft pages and `PortfolioPlan.lua` follow: the chain's buy and craft show done, the kits' post is planned from the kits she now holds, and the wood is no longer bought. No history is read and the market intelligence is not rebuilt (the re-plan uses the published one); when it fails, the previous plan and pages stay live |
| IU12 | a refresh exports a shortlist item (7 weeks of hour-of-week data, top 3 sell and buy windows) and a non-shortlist item | both carry their top windows; the shortlist item has the 24 × 8 curve and the other the 24 × 3; neither string holds a 168-slot `how`, `sales_how` or `supply_how` (SPEC §3.5: the addon shows no week heatmap) |

**Confidence percentage** (`confidence.score`):

| # | Scenario | Then |
|---|---|---|
| CF1 | n = 60, 7 days of id-diff volume, book 1 h old, pattern 7 weeks, tier n = 30 (planned post) | every component 1.0 → 100% |
| CF2 | n = 15, whole-day book estimates, book 12 h old, pattern 0 weeks, no tier history (planned post) | 0.25×0.25 + 0.30×0.8 + 0.15×0.5 + 0 + 0 = 0.3775 ÷ 1.00 → 38% (low) |
| CF3 | the CF2 inputs when posting now (no pattern component) | 0.3775 ÷ 0.85 = 0.444 → 44% (low) |
| CF4 | 4 days of id-diff volume + 3 whole days of book estimates, other components 1.0, posting now | volume (4 × 1.0 + 3 × 0.8) ÷ 7 = 0.914; conf = (0.25 + 0.30 × 0.914 + 0.15 + 0.15) ÷ 0.85 = 0.970 → 97% (high) |

**Tracking** (`engine/tracking`):

| # | Scenario | Then |
|---|---|---|
| T1 | pending action: buy 20 Linen at ≤ 45 c; you buy 20 at 40 c | action done; cost basis 20 × 40 c = 8 s; its sell action stays planned |
| T2 | the same action; you buy 10 | partial, done_qty 10 |
| T3 | the same action; you buy 20 at 50 c | no match (above the planned price); purchase recorded, cost basis updated |
| T4 | two pending posts of 20 Linen (Mon, Tue); one "expired" mail for 20 Linen | Monday's post resolves as expired, and the mail resolves no other post |
| T5 | a sold invoice for 20 Linen at 48 c; open posts of 20 at 50 c (Mon) and 20 at 48 c (Tue) | Tuesday's post resolves as sold: a sale must match the price, an expiry only the stack |
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
| T17 | Amaya posted 1 Copper Rod at 10 s (deposit 20 c; its mats cost 4 s); a sold mail arrives (sold event), then she takes its 9 s 70 c (10 s less the 5% cut, plus the 20 c deposit back) from the mailbox (payout event) | posted, sold and paid out are three journal events; the payout links to the sold mail and through it to the post; realized profit is 0 after the sold mail and 5 s 50 c after the payout (9 s 70 c − 4 s cost − 20 c deposit); reading the payout again changes nothing; a payout whose sold mail was never captured still realizes, without a deposit; a payout that carries its invoice's deposit uses that one, even when its post was never captured (a 1 s sale taken as 1 s 95 c with the invoice's 1 s deposit realizes 95 c, not 1 s 95 c) |
| T18 | you bought green Bracers at 30 s, then disenchant them into 3 Strange Dust (10940) and 1 Lesser Magic Essence (10938), and hold the mats | a `disenchant` event { key Bracers, results "10938,1;10940,3" } (by key); the Bracers leave the cost basis and their 30 s goes to the mats by units, 7 s 50 c each: the essence 7 s 50 c, the dust 22 s 50 c; an item disenchanted beyond what was tracked passes on 0 |
| T20 | the T18 disenchant, with Strange Dust selling at 1 s and Lesser Magic Essence at 7 s now | the Bracers' 30 s splits by value: the dust 3 × 1 s = 3 s and the essence 7 s of 10 s, so the dust holds 9 s (3 s each) and the essence 21 s; when a result has no price the split is by units (T18) |
| T19 | Brugan's plan has DE Bracers today; he disenchants one | the disenchant matches the DE action like a craft (within 24 h of its slot) → `done` |

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
| AR8 | your full scan stored 1 s listings of key −2644 (a random-suffix item whose suffix wasn't known, A13) next to the Copper Rod's | the intelligence has the Copper Rod and no −2644, so no flip, buy, post or page row ever uses it (SPEC §2.2.1: stored, never valued) |
| EX1 | the shortlist of a market with Brugan (12 Copper Bar in bags, knows Copper Rod: 2 bars each), a pending post of Linen, a Disenchant row for Scouting Gloves, Silk Cloth in a partial scan 2 days ago, Wool Cloth in a full scan, and Iron Ore in a partial scan 9 days ago | {Copper Bar, Copper Rod, Linen, Scouting Gloves, Silk Cloth}: what you hold, your recipes' products and reagents, open plan actions, desktop list rows and targeted (partial) scans of the last 7 days; full scans cover the whole AH and would shortlist everything |
| EX2 | `MarketIntelligence.lua` for one market with Linen (45 c, 48 sold/day AHF, a Sat 18:00 window), an item with no price that is on the shortlist, and an item with no price that isn't | Linen's string is `45,20,60,49,49,40,60,4,480,480,93,86,72,45,20,42,0,900;138:4:139:1120:7;` with its curve in `C`; the shortlisted item is exported with empty fields; the other is left out; the header holds schemaVersion 1, generatedAt, the market's observationsUntil and curve hour, each character's market and checksum "3:<total length>" (two item strings and one curve) |
| EX3 | `PortfolioPlan.lua` for Amaya: a pending post of 20 Linen at 55 c, expected net 1,045.5 c, and a missed buy; rules Linen `no_buy` set at T and Silk cleared (`none`) at T + 1 h; Amaya ingested until T + 2 h, Brugan never | Amaya's actions hold only the post (planned chance 92.5%, for the peak at <peak>), `"<id>;post;2589;20;55;<at>;1046;;<window end>;pending;93;<peak>"`; rules `[2589] = "no_buy,T"` and `[4306] = "none,T+1h"` (a clear travels too); `ingestedUntil` names Amaya only; `basedOnUntil` = T + 2 h; checksum over every action and rule string |
| EX4 | the addon loads a `PortfolioPlan.lua` whose schema version differs, then one whose checksum fails | nothing is pruned from SavedVariables and no plan row is shown; a status line says why; no Lua error |
| EX7 | `PortfolioPlan.lua` with a vendor buy and an AH buy | the vendor buy's string ends in ";v", the AH buy's in ";" (13 fields); the addon reads `vendor = true` for the first and false for the second, and an older 12-field string reads as not vendor |
| EX10 | an item with 93 listed now and a median daily supply of 70; one never seen listed now with a median daily supply of 40 | each item's numbers end in a 19th field, its typical daily supply: ",70" and ",40" (the second's supply field is empty); an item with neither ends in "," |
| EX11 | a market where Linen could flip (level 49 c, its Sat 18:00 window 1.25 ×, 2 days ahead, no trend; posting-now chance 0.9; reference stack 20; a merchant pays 10 c), and Silk, whose peak can't beat its cheapest listing | the market's entry has `F = { [2589] = "53,61,138" }`: expected 53 c a unit (0.9 × 61 × 0.95 + 0.1 × (10 − deposit ÷ 20)), the window price 61 c (floor(49 × 1.25 + 0.5)) and the best slot 138; Silk has none; a vendor-sold, suspect or unexported item has none; F is outside the checksum |
| EX12 | reserves: Amaya keeps all Linen (set in game at T) and 5 Copper Bar (set on the dashboard at T + 1 h), and cleared Silk on the dashboard at T + 2 h | `PortfolioPlan.lua` has `reserves = { ["Amaya-Grommash"] = "2589,-1,T;2840,5,T+1h;4306,0,T+2h" }` (by key), outside the checksum; a reserve from an older addon (no time) is not exported |
| EX13 | a shortlisted rod whose supply pattern is 1.0 everywhere except 2.0 in the 8 hours after 20:00, refreshed at 18:00 with an 8 h window | the market's `S[6217]` is 24 characters; the row for 18:00 averages 2 hours at 1.0 and 6 at 2.0 → 1.75 → "F" (35 + 35); the row for 20:00 is 2.0 ÷ 2.0 → 1.0 → "7" (35 + 20); an item with no pattern, or not on the shortlist, has none; S is outside the checksum |
| EX9 | the plan posts 20 Linen with a 24 h auction and 5 Copper Bars with an 8 h one; a buy | the action strings end in ";24" and ";8" (a 14th field, the duration in hours); the buy's is empty; an older 13-field string decodes with no duration |
| EX8 | Heavy Leather is flagged not selling with a record of 0 of 3 sold | `PortfolioPlan.lua` has `records = { [2318] = "1;Your record (30 d): 0 of 3 sold · expired 3× while cheapest · price −14% · qty 1/8" }` ("1" not selling, "0" not), outside the checksum, so an older addon still reads the file; an item with no record has no entry |
| EX6 | AHSync has seen a vendor sell Simple Wood (vendor price 38 c) and Coarse Thread (10 c); Linen Cloth is not vendor-sold | `MarketIntelligence.lua` has `vendor = { [2320] = 10, [4470] = 38 }`, sorted by key, and nothing for Linen; the addon's `Market.VendorPrice(4470)` is 38 and `Market.VendorPrice(2589)` is nil; a file without the table reads as no vendor prices |
| EX5 | the market file: Linen priced an hour ago; Wool last priced 8 days ago (old AHF data no scan has seen since); then Wool on the shortlist | Wool is left out of `MarketIntelligence.lua` (a week-old price misleads, and 3,857 such items pushed the file past its 2 MB budget); on the shortlist it is exported |

**Addon** (Lua, `tests/addon/...`): every scenario for a ported module (`fees`, `sellprob`, `disenchant`, `craft`; see SPEC §5) must pass on the Lua port with identical numbers. Volume (V*) and tracking (T*) scenarios run in Python only. Plus:

| # | Scenario | Then |
|---|---|---|
| A1 | `Capture/*` writes a SavedVariables file with characters, recipes, inventory and events | `ingest/savedvars` parses it back to the same records (round-trip) |
| A2 | at the AH, a full scan shows a planned flip's item cheapest at 45 c while the plan buys at or below 38 c; another flip's item is no longer listed | both buy rows are dropped from Today and the footer says "2 gone"; the rest re-sort |
| A3 | a `MarketIntelligence.lua` with 20k items | loads in Lua 5.1 without hitting the constant limit |
| A4 | `PortfolioPlan.lua` with `ingestedUntil` = T for Amaya and nothing for Brugan, read at login | Amaya's scans, posts, purchases, crafts and mail at or before T are removed from SavedVariables, later ones kept (unconfirmed attempts younger than 1 h stay); Brugan's rows are all kept, since his file may not have been read |
| A5 | an engine error is raised during a re-rank | caught at the UI boundary and shown as a status line; no Lua error popup; the list keeps its last good state |
| A6 | the AH opens and another addon's `ReplicateItems` answers with 3 auctions (Linen 45c ×20 long, Linen 45c ×20 long, Copper Bar 1s ×5 short) | one `full` scan, coverage 1, rows `2589,45,20,3,2;2840,100,5,1,1`; AHProfit made no ReplicateItems call |
| A7 | the AH opens and no scan result arrives within 30 s | AHProfit calls `ReplicateItems` once; opening the AH again 5 min later doesn't call it again (20-min rule) |
| A8 | a 10,000-auction result | read over several frames, no step over the 4 ms budget (counted by the stub clock); entering combat pauses it and leaving combat finishes it |
| A10 | AHProfit's own `ReplicateItems` call gets no answer; the AH is opened again 20 min and then 40 min after that call | no call at 20 min (an unanswered call doubles the wait to 40 min); one call at 40 min |
| A11 | a replicate answer with a battle pet, an auction without a buyout, one with time left 0 (not a Forever value) and one with none, next to one Linen 45c ×20 | the scan holds only the Linen row; the four are counted as skipped in the diagnostics, and the time-left histogram shows `0:1,3:1,nil:1` |
| A12 | Forever's time left is 1 short … 4 very long (verified 2026-10-17: no 0 in 41,062 auctions, and 19.5% read 4); an auction of Linen with time left 4 | band 4 (very long), as written |
| A13 | two auctions without an item link: Linen Cloth (trade goods) and Scouting Gloves (armor, could roll a suffix) | Linen is keyed 2589 by its item id; the gloves are keyed −6585 (some suffix variant, left out of valuation, §2.2.1) |
| A14 | your bags hold a soulbound Hearthstone (white link), 7 Broken Fang (grey link) and a Copper Bar whose link has no name | `itemNames` gets `[6948] = "1,Hearthstone"` and `[3300] = "0,Broken Fang"`, read from the links with no item-cache lookup; the bar gets no name; a Forever link "|cnIQ2:…[Aboriginal Gloves of Frost Protection]" reads as quality 2 (Forever tags the quality instead of colouring it) |
| A15 | no `PortfolioPlan.lua`; Amaya's bags hold 20 Linen; the intelligence says Linen posts now at 45 c, sell value 42 c each, chance 90% | Today shows the addon's own row: POST ×20 at 45 c, net +8 s 40 c (20 × 42 c), chance 90%, note "post now · AHSync has no plan for it" |
| A16 | the plan has a post of Linen for Amaya; her bags also hold 6 Copper Bar the plan doesn't mention | Linen shows only the plan's row; Copper Bar gets the addon's own row |
| A17 | 7 Broken Fang: the merchant pays 12 c each, posting is worth 9 c each | the addon's row is VENDOR ×7 at 12 c, net +84 c, no chance |
| A18 | a bound Hearthstone, and a Silk Cloth with no market data, in the bags | no row for either |
| A20 | the same 6 bars feed the 3 Copper Rod crafts | the bars get no sell row of their own: a mat is used once |
| A21 | hovering Linen Cloth in the bags: market file cur 45 c seen 30 min before it was written 2 h ago, med30 49 c, top window Sat 18:00–22:00 UTC (7 weeks, ×1.12), chance 90%; local time UTC; no enchanter | the tooltip adds "AHProfit" and five lines: Current AH price "45c (2 h ago)", Historical price "49c (30d, +9%)" green, Best time to sell "Sat 18:00–22:00", Price at best time "55c (+22%)" (49 c × 1.12 = 54.88), Chance to sell "90%"; after a live scan shows 40 c the first line reads "40c (now)" and the deltas follow it; an item without market data adds nothing |
| A22 | the addon's Inventory tab for Amaya: 20 Linen in the bags (own sell row: post now at 45 c, 42 c each), 6 Copper Bar a planned craft of 3 Copper Rod uses, a bound Hearthstone, 4 Wool Cloth in the bank AHSync plans to post Sat 10:00 at 30 c for 1 s 14 c, and 2 Silk Cloth in the mail with no option (sell value 60 c) | rows best worth first: Linen "8s 40c" with "Post now at 45c"; Silk "1s 20c" "Not planned"; Wool "1s 14c" "Post Sat 10:00 at 30c (AHSync)"; Copper Bar "—" "For crafting Copper Rod"; Hearthstone "—" "Bound · not planned"; each with its place (bags, bank, mail) |
| A23 | the addon's Disenchant tab with no enchanter on the account: the market file lists Scouting Gloves (green armor, ilvl 18, Enchanting 1) at 8 s; Strange Dust is worth 6 s and Greater Magic Essence 10 s each | the tab is there anyway and shows the gloves: ilvl 18, skill 1, buy 8 s, EV 14 s 25 c (0.75 × 2.5 × 6 s + 0.2 × 1.5 × 10 s), profit +6 s 25 c, note "1.9 Strange Dust · 0.3 Greater Magic Essence · no enchanter yet"; with Amaya at Enchanting 150 the note drops the warning, and an item needing 225 says "needs Enchanting 225 (best 150)"; unprofitable items are left out |
| A24 | at the AH, a search for Linen Cloth returns min 38 c × 20 (no full scan this visit); the plan buys 20 Linen at ≤ 45 c | the Linen row turns ✓ "38c ≤ 45c"; clicking a row searches for it and the status says "searched Linen Cloth · 1 of 3"; Full scan asks for a ReplicateItems scan unless one was asked for in the last 20 min (it says how long to wait) |
| A25 | Amaya carries 13 Refreshing Spring Water that Post suggests selling; she right-clicks it and keeps at least 10; later she keeps all; then she picks "Sell normally" | with 10 kept, Post and Today suggest 3 (an AHSync plan post of 13 is trimmed to 3, its net scaled to 3/13) and Inventory says "Keeping 10 for yourself"; with all kept, no row suggests selling or crafting with it and Inventory says "Kept for yourself"; after Sell normally all 13 are suggested again. The reserve is saved per character in `AHProfitDB.reserves` |
| A26 | Amaya keeps all 7 of her Silk, at least 10 of her 13 Refreshing Spring Water, and 5 Linen she no longer holds; she opens the Reservations tab, clicks the water and picks "Sell normally" | the tab lists every reserve by name: Linen Cloth keeping 5, held ×0, free —; Refreshing Spring Water keeping 10, held ×13, free ×3; Silk Cloth keeping All, held ×7, free —; footer "3 items kept · click a row to change it". A left or right click opens the keep menu; after Sell normally the water is gone and the footer says "2 items kept". With nothing kept the tab says how to keep something |
| A27 | Amaya knows Copper Rod (2 Copper Bar each; the rod posts at 40 s with `P` = 1; a bar is worth 9 s 50 c to hold) and Linen Belt, which loses money; her bags hold 4 bars and the AH lists 2 bars at 10 s | the Craft tab shows one row: Copper Rod, source Mixed, ×3, mats 20s, profit +56s (3 × 38 s − 4 × 9 s 50 c − 20 s, C3), chance 100%, note lines "use 4 owned Copper Bar" and "buy 2 Copper Bar on the AH · 20s"; the footer says "1 of 2 known recipes pay now". Each recipe is costed on its own against what she carries (less what she keeps, A25), so a row shows the best that recipe can do; Today combines them. With no recipes seen it says to open each profession window once |
| A28 | AHSync's plan has two flips for Amaya: buy 20 Linen at or below 40 c (chain profit +2s 10c) and post them Sat 10:00 at 55 c for the 18:00 peak (chance 88%); buy 5 Silk at or below 70 c (+40c) and post Sun 11:00 at 1 s; plus a mat buy for a craft. All her gold is 1 g. A live scan then shows Silk at 80 c, nothing cheaper | the Flip tab lists Linen: ×20, buy 40c, sell 55c, when Sat 18:00, profit +2s 10c, chance 88%, risk 8.0%, note "post Sat 10:00, for the 18:00 peak · on the AH now from 38c" when the scan has Linen at 38 c; the craft's mat buy is not a flip; Silk is dropped as gone and the footer says "1 flip · capital needed 8s · 1 gone". Flips come from AHSync's plan, which sizes them with its queue simulation; the addon re-checks them against the live AH and never simulates (SPEC §4.0) |
| A29 | at the AH, Post suggests 5 Copper Rod at 41 s; Amaya's bags hold 3 rods in bag 0 slot 2 and 4 more elsewhere; she clicks the row | the AH's Sell tab gets the rod from bag 0 slot 2 (`AuctionHouseFrame:SetPostItem`), quantity 5 (capped at `GetAvailablePostCount`, 7 here) and unit price 41 s; the status says "Sell tab: ×5 at 41s each · press Post"; nothing is posted until she presses the AH's own Post button. When the AH then fills in its own price from a search (within 10 s), 41 s is put back once. A row for an item not in her bags says "no Copper Rod in your bags" |
| A30 | AHSync's plan crafts 7 Basic Campfire Kits from 7 Simple Wood and then posts the kits; Amaya holds no kits yet, but holds 20 Linen that Post suggests | at the AH the Post list shows Linen only: a post for an item not in her bags is a later step of a chain, so it waits until she has crafted the kits. The Craft tab shows the kit with Source Vendor, ×7 and the note "buy 7 Simple Wood from a vendor · 2s 66c": a vendor sells the wood at 38 c (EX6), and 7 crafts is the kit's liquidity cap (14 others listed × 0.5) |
| A31 | at the AH, Post lists her 1 Basic Campfire Kit and 20 Linen (plan: post 20 at 55 c, net +10 s); she posts the kit, then 15 Linen | once the bags are read again (debounced, about 1 s), the panel and the open window redraw: the kit's row is gone, and Linen shows ×5 with Est. profit +2s 50c (10 s × 5 ÷ 20). The panel never lists more than you hold, sharing your units across rows of the same item in order |
| A32 | the plan buys 7 Simple Wood from a vendor at 38 c and 20 Linen on the AH at 45 c | the AH panel's Shopping list has Linen only ("0 of 1" checked): it lists what to buy on the AH, and the wood is not bought there. Today's wood row says "from a vendor" |
| A33 | the Craft tab shows Basic Campfire Kit with "buy 9 Simple Wood from a vendor"; the AH lists wood at 30 c; Amaya buys 9 wood from the vendor, and the wood's sell value is 60 c | once the bags are read again the tab redraws and the kit's row reads Source Owned, Mats "—", note "use 9 owned Simple Wood": owned wood counts at 38 c, no more than the vendor's, so it goes before buying more; the 30 c AH listings are never used |
| A34 | the Craft tab's Linen Belt uses 10 owned Linen Cloth, buys 2 Coarse Thread from a vendor (20 c) and 1 Wool Cloth on the AH (33 c) | the row has three grey lines under it, one per mat, by source: "use 10 owned Linen Cloth", "buy 2 Coarse Thread from a vendor · 20c", "buy 1 Wool Cloth on the AH · 33c" |
| A35 | AHSync has no plan; Amaya carries 20 Simple Wood (vendor-sold at 38 c, a merchant pays 9 c, the market file says post at 55 c) and 20 Linen | Today's own rows: Linen posts; the wood is a vendor sale at 9 c, never a post; the AH panel's Post list has Linen only; a known recipe whose product a vendor sells is not on the Craft tab |
| A36 | the Craft tab suggests ×1 Basic Campfire Kit (its liquidity cap is 1 unit); Amaya crafts it | once the bags are read again, the kit's row shows a green check and "made" under Source, ×1, no mats, profit or risk, and the note "×1 in your bags · post it at the AH"; the footer says "0 of 1 known recipes pay now · 1 made". The kits you hold count against the cap: holding 1 of a 3-unit cap leaves 2 crafts to suggest. Today's own crafts follow the same cap |
| A37 | the Craft tab shows Basic Campfire Kit as made (×1 in the bags); AHSync's plan posts the kit Sun 09:00 for the 17:00 peak; then a second case where the plan holds it, and a third with no plan (the addon would post it now at 80 c) | the made row's note says "×1 in your bags · post Sun 09:00, for the 17:00 peak", then "×1 in your bags · hold for a later week", then "×1 in your bags · post it now at 80c" |
| A38 | at the AH, Amaya clicks Loose Chain Cloak and the search returns no cloak; then she searches a random-suffix green whose key is not its item id, and the result lists its item id at 1 s | the cloak's row turns ✗ "gone" instead of staying "checking"; the green's row is priced from the result for its item id |
| A39 | Shopping wants ×9 Rough Weightstone; Amaya buys out 4 (a commodity purchase of 4), then bids on another auction | the row shows ×5 once the purchase is confirmed, and drops off when all 9 are bought; a bid changes nothing; a failed purchase is not counted |
| A40 | the plan buys Copper Bar for a Copper Rod craft, Silk to flip, and the AH panel adds a green to disenchant | Shopping's For column says CRAFT, FLIP and DE, each in its action colour, with a header tooltip that explains them |
| A41 | at the AH Amaya has 2 auctions up: 1 Basic Campfire Kit at 80 c with 11 h left, and 5 Copper Bar at 9 s 50 c with 20 min left; a sold auction is in the list too | `ownAuctions["Amaya-Grommash"]` = { at = now, rows = "2840,950,5,1,1;4471,80,1,3,1", ends = "2840,5,<now + 20 min>,950;4471,1,<now + 11 h>,80" }: active auctions only, rows "key,unit price,stack,band,count" with Classic's bands (1 < 30 min, 2 < 2 h, 3 < 12 h, 4 longer), ends "key,units,end ms,unit price". It is read when the AH opens and again after a post or a cancel (`OWNED_AUCTIONS_UPDATED`); AHSync reads the rows and ignores `ends` |
| A42 | the Craft tab's Basic Campfire Kit (cap 1) is made; Amaya posts it, so her bags hold none and her auctions hold 1 ending in 11 h; later the auction's end passes | the row stays "✓ made ×1" with the note "×1 on the AH · ends in 11 h" instead of suggesting another craft; once the end has passed the auction no longer counts, and the row suggests crafting again. Today's own crafts count your auctions the same way |
| A43 | at the AH Amaya presses Full scan; the server answers with 47,667 auctions; another time it doesn't answer for 60 s; another time AuctionHouseFYI's scan arrives first | the panel shows a progress bar under the status line: "full scan: waiting for the server…" (empty bar), then "full scan: reading 23,400 of 47,667" (the bar fills with the auctions read, up to 85%), "full scan: saving" (the rest), then "full scan done · 47,667 auctions" with the bar gone. It updates every 10 steps, not every frame. With no answer after 60 s: "no answer: another addon may have scanned recently". A scan another addon asked for shows the same progress |
| A44 | Amaya posts 20 Linen Cloth from bag 0 slot 1 at 55 c each for 24 h (a commodity, duration 3); the AH confirms it; later she posts a Scouting Gloves (an item) at 9 s for 2 h and the AH never confirms | a `post` event { key 2589, stack 20, price 55, durationH 24, deposit 3 × 20 × 15% = 9 c, confirmed false } is added at once and turns `confirmed = true` on `AUCTION_HOUSE_AUCTION_CREATED`; the gloves' post stays `confirmed = false`. The item comes from the bag snapshot taken before the post, since the slot is empty when the hook runs |
| A45 | at the AH Amaya asks for 4 Rough Weightstone (the AH quotes 10 c each), confirms, and the purchase succeeds; another time a purchase of 3 fails; then she buys out a Linen Belt auction of 1 at 2 s (a bid below its buyout another time) | `purchase` events: { key 3239, qty 4, price 10, source ah, confirmed true } once `COMMODITY_PURCHASE_SUCCEEDED` fires; the failed one stays `confirmed = false`; the buyout { key 4240, qty 1, price 200, source ah } is confirmed on `AUCTION_HOUSE_PURCHASE_COMPLETED`; the bid adds nothing. Shopping's quantity falls by the confirmed units (A39) |
| A46 | at a merchant Amaya buys 9 Simple Wood (sold in stacks of 1 at 38 c) | a `purchase` event { key 4470, qty 9, price 38, source vendor, confirmed true } |
| A47 | Amaya crafts 2 Basic Campfire Kits (two successful casts of spell 6413, a recipe she knows); she also casts a spell that isn't a recipe | two `craft` events { spell 6413, count 1 }; nothing for the other spell |
| A48 | Amaya opens her mailbox: a sold invoice for 20 Linen Cloth at 9 s 60 c (48 c each), an "Auction expired: Copper Bar" mail with 5 bars, an "Auction cancelled: Silk Cloth" mail with 5 Silk, an "Auction won: Linen Belt" mail (bought at 2 s) and a letter from a friend; she closes and reopens it | one `auction_mail` event each: sold { key 2589, qty 20, price 48 }, expired { key 2840, qty 5 }, cancelled { key 4306, qty 5 }, won { key 4240, qty 1, price 200 }, each with `sentAt` (from the mail's days left; auction mail is kept 30 days); nothing for the letter; reopening adds nothing, because each mail is remembered in `mailSeen` until 31 days after it was sent. A sold item is named from `itemNames` (it was in her bags before she posted it) |
| A49 | AHSync's plan posts 1 Tiger's Eye now and 20 Linen Sat; Amaya holds 15 Linen in her bags and 5 in her bank and has just posted her only Tiger's Eye; the plan also posts 7 Basic Campfire Kits after a craft she hasn't done | once her bags are read again Today drops the Tiger's Eye post, and the Linen post stays at 20 (bank units count); the kits' post stays, because a chain's later step is for units still to be made. A post, vendor sale or disenchant with no parent is never shown for more than the character holds above what it keeps (A25), with its net scaled |
| A50 | Amaya has 1 Basic Campfire Kit up at 80 c ending in 11 h and 2 Peacebloom at 48 c each ending in 7 h | the Inventory tab lists them with Where "AH": the kit ×1, worth 76c (80 c × 0.95 if it sells), note "listed at 80c each · ends in 11 h"; an auction past its end isn't listed |
| A51 | a full scan finishes while the AH panel and the window are open | saving the scan takes its own frame and the redraws it causes (the panel, the window) the next one, so neither adds to the other; the diagnostics' scan line gains `notifyMs`, the redraws' time (measured in game: a 136 ms save step that included them) |
| A54 | Amaya opens a sold mail for 1 Copper Rod (invoice 10 s, deposit 20 c; 9 s 70 c on the mail) and takes the money, then the take fires again before the mail goes; then she takes the money of a letter | one `payout` event { key 6217, qty 1, money 970, deposit 20, sentAt }; the second take and the letter add none (T17); the diagnostics trace each take: "take 1 money=970 invoice=seller item=Copper Rod -> payout", then "-> seen before", then "-> not a sold mail" |
| A55 | Brugan casts Disenchant (13262) on the Bracers (their bag slot locked while it casts); the loot window shows 3 Strange Dust, 1 Lesser Magic Essence and some coin | one `disenchant` event { key Bracers, results "10938,1;10940,3" } (coin left out); an interrupted Disenchant, or a loot window after any other cast, adds none; the diagnostics trace "de start 4570", "de stopped", "de start 4570", "de loot 2 -> disenchant" |
| A56 | Amaya clicks the Post row for Linen, which the plan posts for 24 h; then an own "post now" row; then a client without the duration control | the Sell tab gets item, quantity, price and duration 24 h (the AH's third duration); the own row gets 8 h (the shortest duration covering the 8 h posting-now window); without the control the status line ends "· set the duration to 24 h" |
| A57 | the Craft tab's Copper Rod: nobody lists rods now (no supply), but the market file says rods typically have 6 listed; then neither is known | the cap uses the typical 6: max(1 stack, floor(0.5 × 6)) = 3 rods; with neither, no cap (as before) |
| A58 | the Craft tab's Copper Rod needs bars; the bars' 30-day range is p10 30 c, p90 40 c; a live scan lists 2 bars at 1 c, then 10 at 35 c; another time AHSync's file already has bars at 1 c as a confirmed price (cur 1 c, not suspect) | the 1 c bars are no mat source (1 c < 3 c) and the 35 c ones are; with the confirmed price the 1 c bars are used. The market file's items end in a 20th field, "1" when AHSync hasn't confirmed cur, else "0" |
| A59 | no plan file; the market file offers a Linen flip (53 c expected a unit, sells 61 c Sat 18:00, a stack of 20); Linen is on the AH at 40 c, then a scan shows 46 c; Silk is offered but has a "no_buy" rule; AHSync's plan already flips Wool | Flip lists Linen as the addon's own: BUY ×20 at 40c, sell 61c Sat 18:00, profit +2s 60c ((53 − 40) × 20), note "addon's own idea · one stack · buy at or below 44c" (floor(53 ÷ 1.2)); at 46 c it's gone (over 44 c); Silk never shows; Wool shows once, as AHSync's flip |
| A60 | Amaya kept 10 Linen in game at T; the plan file says the dashboard set Linen to 5 at T + 1 h, cleared Silk at T + 1 h (she keeps Silk since T + 2 h) and keeps all Wool at T + 1 h | at login her reserves are Linen 5 (newer), Silk kept (hers is newer), Wool all; `reserveTimes` has Linen and Wool at T + 1 h; setting a reserve in game records its time, clearing one too |
| A61 | an auction with 7 h 59 min left, then 8 h, then 2 h | bands 3, 4 and 3: the long band runs to 8 h, as the spec and the dashboard say, not Classic's 12 h |
| A62 | the Craft tab's rod: others list 6 now; its S row says 2.0 for this hour (supply doubles over the window) | the cap uses 6 × 2.0 = 12 listed over the window: max(1, floor(0.5 × 12)) = 6 crafts, where 6 alone gives 3; an item without S, or no time given, uses the supply as it is |
| A63 | AHSync's plan for Amaya: buy 100 Raw Longjaw Mud Snapper (47 c each) → craft 100 Longjaw Mud Snapper → post them Sat 09:00 for the 18:00 peak (net +97 s 91 c, chance 99%); she also knows Copper Bar, which the plan doesn't craft | the Craft tab lists the planned craft first: source "Plan", ×100, mats 47 s, profit +97s 91c, 99%, lines "buy 100 Raw Longjaw Mud Snapper on the AH · 47s" and "post Sat 09:00, for the 18:00 peak"; then her own option for Copper Bar; never an own option for the planned recipe; the footer reads "1 planned · 1 of 2 known recipes pay now". A chain that buys 22 Copper Ore and 22 Tin Ore, crafts Copper Bars and Tin Bars, then 22 Bronze Bars to post is one row, Bronze Bar, with both buys, then "craft 22 Copper Bar first" and "craft 22 Tin Bar first", then when to sell; planned rows are best profit first |
| A64 | Amaya has 4 g 55 s; an own craft option would buy 15 g of mats | its mats stay within 25% of her gold (1 g 13 s 75 c): fewer crafts, or none |
| A65 | the plan buys 40 Tin Ore (≤ 2s 79c) for Tin Bars and 22 Tin Ore (≤ 2s 75c) for Bronze Bars | Shopping has one Tin Ore row: ×62 at most 2s 75c each (the lower cap), For CRAFT; the Craft tab's two chains list 40 and 22 |
| A66 | the plan flips 5 Silk Cloth (buy ≤ 50c) and 4 Linen Cloth (≤ 10c); a scan shows Silk only at 60c and Linen at 9c | the Flip tab lists both: Linen as usual, Silk greyed below it with "gone: nothing on the AH at or below 50c now"; capital counts Linen only (40c); the footer reads "1 flip · capital needed 40c · 1 gone"; Shopping marks Silk ✗ gone too |
| A52 | `PortfolioPlan.lua` has records: Heavy Leather not selling ("1;Your record (30 d): 0 of 3 sold · …") and Linen with a clean record ("0;Your record (30 d): 2 of 2 sold"); Today has a Linen post and a Heavy Leather post | each row has its record line as a grey line under its note; below the rows a "Needs a decision" header and a DECIDE row for Heavy Leather, with its record line and "right-click: Blacklist or Keep"; Linen isn't listed there. The same record lines show on Week |
| A53 | Amaya right-clicks the Heavy Leather decision row and picks "Don't buy or craft it"; another time "Keep selling it (start fresh)" | `AHProfitDB.rules[2318]` = { rule "no_buy" (or "keep"), setAt now }, which AHSync reads (§3.4.2); the decision row goes at once |
| A19 | Amaya carries 20 Linen; the market file (refreshed 3 h ago) says post at 45 c, sell value 42 c; a full in-game scan now shows the cheapest Linen at 40 c, nothing below; med7 49 c; the curve says 80% at 0 ahead this hour | the addon's own row re-prices from the scan: post at 39 c (40 − 1, below med7), chance 80%, deposit 3 c (20 × 3 c vendor × 5% for the 8 h auction), net 0.8 × 20 × 39 × 0.95 + 0.2 × (20 × 3 − 3) = 604.2 c, shown +6 s 4 c; note "post now at the live price · AHSync has no plan for it" |
| PP1 | post price from the cheapest other auction and the 7-day median: 55 c and 60 c; 70 c and 60 c; none and 60 c; 55 c and none; neither | 54 c (undercut by 1 c); 60 c (the median is lower); 60 c; 54 c; no price. The same in Python and Lua (shared scenario) |
| A9 | no `PortfolioPlan.lua`; Brugan knows Copper Rod (2 Copper Bar each); the intelligence says Copper Rod posts at 40 s with `P` = 1 and Copper Bar is worth 9 s 50 c to hold; his bags hold 6 bars; the AH has no bars | the addon itself offers CRAFT ×3 Copper Rod, net +57 s (3 × (38 s − 19 s), the desktop's C1), chance 100%, note "from your own mats · AHSync has no plan for it"; with the bars bought on the AH at 10 s instead it says "mats from the AH: 60s" and nets +54 s (C2) |

## 2. Visual tests (UI1–UI14)

Layers and tooling: SPEC §8.4.

| # | Scenario (fixture addon files) | Then |
|---|---|---|
| UI1 | Today tab, 12 mixed actions | 12 rows sorted by expected net; each and total both shown; net values coloured; footer totals correct |
| UI2 | 40 actions | 12 visible, scroll bar shown, nothing drawn outside the list |
| UI3 | item "Scouting Gloves of the Monkey" in a narrow column | truncated with "…"; no overflow |
| UI4 | no `MarketIntelligence.lua` | empty state "Run AHSync, then /reload"; no rows |
| UI5 | data 30 h old | age badge amber |
| UI6 | an item flagged "not selling", its row right-clicked | the row has a red DECIDE badge and its record line; the menu (keep for yourself, then "Don't buy or craft it", "Ignore it completely", "Keep selling it (start fresh)", Cancel) fits inside its frame |
| UI7 | AH panel: 5 targets, 2 found, 1 gone, 2 checking | ✓ / ✗ / … states (ready-check icons: WoW's fonts have no ✓ ✗ glyphs); "3 of 5"; labelled columns, each header with a tooltip: Shopping "Item · Price · Max · Qty" (a found row "38c" under Price, "45c" under Max), Post "Item · List price · Qty · Est. profit · Chance" (a post of 5 Copper Rod at 41 s with net +63 s 6 c shows "41s", "×5", "+63s 6c", "58%"); panel 300 px wide, docked to the AH frame's right edge |
| UI8 | tooltip for a disenchantable item | header + 6 label/value lines in the spec order; deltas vs current 8s: historical 11s → +38%, disenchant 14s 25c → +78%, best 12s 30c → +54%, none wider than 320 px; without an enchanter, the Disenchant value line is left out |
| UI9 | window at its minimum size, 560 × 360 | layout checks pass; no overlaps |
| UI10 | Today with 1 row below the "Min chance" filter and 1 "not selling" item | sorted by net; footer "1 hidden"; Needs a decision section at the bottom |
| UI11 | Craft tab with one recipe fully covered by owned mats, in the window at its minimum size | its Mats cell shows "—" and its note "use 6 owned Copper Bar"; all seven tabs fit left of the Min chance box |
| UI14 | a note under a row, on Craft, Week and Today | the note starts exactly where that tab's Item column starts: 3 px in on every tab whose first column is Item, after the action badge on Today, after the time and badge on Week |
| UI13 | Flip tab with one flip, in the window at its minimum size | columns Item · Qty · Buy · Sell · When · Profit · Chance · Risk; all seven tabs fit left of the Min chance box; layout checks pass |
| UI12 | Reservations tab with 3 reserves, in the window at its minimum size | five tabs fit left of the Min chance box (hidden on this tab); columns Item · Keeping · Held · Free; layout checks pass |

## 3. Views and tooltip (D16–D22, TT1–TT5)

D19–D22 are listed with the display scenarios in section 1.

| # | Scenario | Then |
|---|---|---|
| D16 | Today rows with net 10 s, 5 s, 5 s (P' 0.8 and 0.9) | order 10 s, then the 5 s with P' 0.9, then the other 5 s; footer total 20 s |
| D17 | a Week day with actions +8 s 30 c, +1 g 10 s and −20 c | day total +1 g 18 s 10 c; the category and character totals add up to the week total |
| D18 | On the AH rows from T9–T11: a Copper Rod you crafted (cost 10 s) up at 41 s with a 9 c deposit, and 5 Linen you looted up at 45 c each (no deposit captured) | rows: the rod listed 41s, cost 10s, deposit 9c, profit if sold +28s 95c (41 × 0.95 − 10); the Linen cost 0c "before tracking", deposit "—", profit +2s 14c; the listed, cost, profit and deposit tiles equal the sums of their columns (43s 25c, 10s, +31s 9c, 9c); position "cheapest" at or below the cheapest listing, else "undercut: <cheapest>" |
| TT1 | current 8 s, med30 11 s, DE 14 s 25 c, best 12 s 30 c | deltas +38%, +78%, +54% |
| TT2 | current 10 s, historical 9 s | −10%, shown red |
| TT3 | no current price | values shown, no deltas |
| TT4 | no character can disenchant it | no Disenchant value line |
| TT5 | delta exactly +12.5% | +13% (half up) |
| TT6 | Linen: level 49 c, the best window's hour is 1.12 × the level and 2 days ahead, trend +2%/day; then +9%/day | Price at best time 57 c = floor(49 × 1.12 × (1 + 0.02 × 2) + 0.5), as the plan prices it (`plan.slot_price`, `Prices.SlotPrice`); with +9%/day the trend is clamped to 5%: 60 c |

## 4. Calibration (K1–K3)

Rules: SPEC §8.8.

| # | Scenario | Then |
|---|---|---|
| K1 | 10 posts predicted 0.8, 8 sold | bucket 80–100%: predicted 80%, actual 80%; Brier = (8 × 0.04 + 2 × 0.64) ÷ 10 = 0.16 |
| K2 | 25 posts predicted 0.5, 7 sold | bucket 40–60% actual 28%; warning raised (off by 22 points, ≥ 20 posts) |
| K3 | 5 posts predicted 0.5, 0 sold | no warning (fewer than 20 posts) |
| K5 | a post matched to its plan action, which planned P' 0.62; it sells | the post's `p_at_post` is 0.62 (the action's `conf`), so it counts in the 60–80% bucket as sold; a post that matched no action has no `p_at_post` and is left out |
| K6 | Status with K1's and K2's posts | a "Sell-probability calibration" section: "Predicted vs actual (last 90 days, 35 resolved posts)", "Brier score 0.22 · always guessing 50% would score 0.25" ((1.6 + 6.25) ÷ 35), rows 40–60% (25 posts, predicted 50%, sold 28%, −22 pts, off) and 80–100% (10, 80%, 80%, 0 pts); the warning "Posts predicted 40–60% actually sold 28% (25 posts). Chances in that range are too optimistic."; with no resolved posts the section says it appears once posts are tracked |
| K4 | 10 item-days with both id-diff and book volume in the last 30 days: book ÷ id ratios 0.8 ×4, 1.0 ×4, 1.5 ×2 (a book day observed for 2 h counts 12 × its sold, V30) | Status, on the market's card: "Your scans vs blizzard (30 d)" "median 1.0 · 8 of 10 item-days (80%) within ±25%"; a source with no overlap shows nothing; your mail is never a comparison (it sees only your auctions); with no id-diff source at all the card says "no confirmed sales to compare with yet"; days with a confirmed 0 sold are left out (no ratio) |

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
| VI2 | Id diffs: `sold + expired` = the units that left (gone auctions plus partial buys), `listed` = the units of new ids, all ≥ 0, and `expired` ≤ the units of gone auctions |
| VI3 | Coverage scaling: sold/day ≥ 0; observing twice the hours and twice the sales gives the same rate; whole days give the plain mean; a book-only week's volume score is in (0, 0.8] |
| CI1 | Craft: the chosen source for a reagent costs no more than any other available source |
| CI2 | Craft: each added craft has a positive marginal profit; the next one would not |
| CI3 | Chains: profit is counted once, so the chain's total equals the final product's profit |
| PL1 | Plan: buys never exceed the budget; no action is before `now_ms`; every craft has a follow-up within 7 days; every post is at peak − `post_lead_hours` (or now) |
| PL2 | Plan: every unit you hold is planned exactly once; a planned post is never above what you hold, and below 1 stack only for a character's last remainder (a lot of 47 in stacks of 20 posts 20, 20 and 7) |
| PL3 | Missed peaks: a replacement's `E` is always ≥ 90% of its original anchor (never of an earlier replacement), its P' ≥ `min_sell_prob`, and it lies in the same plan week. Otherwise the action is a hold |
| PL4 | Plan: for every item and sell window, the units planned across all characters (holds, flips, crafts' output, disenchant mats) never exceed that window's liquidity cap, and no character's plan claims capacity another already used |
| VW1 | Views: every total equals the sum of its rows; filters only hide rows, never change their numbers; each chain's profit appears in exactly one row of any view (§3.4 expected net) |
| LP1 | Python and Lua give identical rounded results on every generated case for the ported modules |
| FI1 | A crash injected at any commit point of catch-up, ingest or refresh, followed by a restart, gives the same final database rows and addon file bytes as an uninterrupted run |
| FI2 | Ingesting any SavedVariables file twice, or in any order with earlier files, gives the same database as ingesting each once in order |
