# Plan: prices from what sold (the sold-price fix and the confirmed floor)

Written for: the user (approving §1b) and the Opus session that runs this batch. Drafted 2026-11-09, after the market simulator's first decision report (`docs/plans/market-simulator.md` §1.37–§1.47).

**Why.** The plan prices posts from **asking prices**: an hourly close is the lowest ask in that hour's book (LESSONS 2026-11-07), and the post-price cap is `max(med7, p90_30)` of those closes (SPEC §3.4, `engine/prices.post_price`). An ask is not a sale. A lone 15 g listing left up for 12 hours makes 12 closes of 15 g, and the plan posts against it (SM10). The simulator's misses trace back to this:

| Row | What went wrong | Why asks cause it |
|---|---|---|
| SD5 (demand drop) | Silk posted at 139 c and 130 c with a 99% predicted chance against a 105 c floor; nothing sold | the cap follows asks that no longer sell |
| SD9 seed 2 (gathering) | Elemental Fire posted at 938–989 c against others' 890 c tenth percentile; 3 posts expired | as above |
| SD11 (decoys) | a craft funded on 0.2–0.3 sales a day of an item nobody bought | thin-item sales read from asks that come and go |
| SD29 (a held item's dip) | the exit didn't fire in time | the dip in asks isn't confirmed by sales |

## 1. What is decided (do not re-litigate)

From `docs/plans/market-simulator.md` §1.13 and §1.32 and `docs/plans/blizzard-cloud.md` §1 deferred item 3 (the user, 2026-11-07 and 2026-11-08):

1. **Keep the price each sold auction left at.** Publish a **units-weighted sold-price median beside the ask medians** first, and compare the two before anything switches.
2. **Then switch** the post-price cap and the selling windows to the sold price, **for items with enough sales**. Asks stay the fallback. The switch is a spec departure that the user approves at that step.
3. **Confirmed floor**: the post price stops undercutting a lone cheap listing. It prices off the cheapest price that has at least N units or K auctions within a few % of it. N, K and the band come from real data. The rule is measured in the simulator first, in a lone-cheap-listing world beside SM10/SM11, and built with the switch.
4. **SM10 and SM11 are the test pair**, reshaped with the user on 2026-11-09 (§1c).

## 1c. Decided while planning (the user, 2026-11-09)

1. **SM10 and SM11 are rewritten to the real failure.** The old SM10 (2 g posts that sell before any snapshot sees them, under a 15 g ask) was implausible: if cheap supply were always gone whenever anyone looked, demand would be beating supply and buyers would reach the 15 g. The real case (Scouting Bracers, LESSONS 2026-11-07) is time-weighting: an hourly close is the cheapest ask *each hour*, so a high ask that sits unsold fills most hours, while the cheap listings that do sell fill few. A sold price counts only what was bought. The pair now tests both directions:
   - **SM10, the high ask doesn't sell.** A thin gear item sells about once a day at about 2 g; each 2 g listing is seen in a snapshot or two before it sells. A 15 g listing sits 30 h and expires unsold. Today most closes read 15 g and the post price lands near 15 g; the sold-price median reads about 2 g.
   - **SM11, demand catches up.** The same item while demand rises: the cheap listings run out and buyers take the 15 g listing and later ones near it. The sold-price median rises to about 15 g as they sell, and the post price follows, within 5% of the true sold price.
   - Sales of listings no snapshot saw are a smaller, separate risk (§7), not SM10's subject.
2. **D1 A**: during the comparison the sold price is in the summary document and `tools/sold_price_report.py` only; no screen changes until the switch.
3. **D2 A**: the switch (SP2) is decided at the 2026-11-21 real-data review.
4. **Decided while building SP1a (2026-11-09).** (a) The sold prices live in a new module, `engine/lifetime_prices.py`, not `engine/volume.py`, which was at the 1,000-line limit; the new listings' positions (BZ29) moved there with their test. (b) An ending's price is its last sighting's (`volume.lifetimes` refreshes it each sighting; Blizzard's unit buyout is not known to change, so this matches the first sighting today), and an ending with no price contributes nothing. (c) The analysis reads only the last 7 days of `sold_prices_d1`, not the 90-day volume window. (d) The cloud publishes schemas 13–16 side by side, one more document per run. **Open: `tools/bench.py --summary` was not run** (no current summary on the build machine); it is owed before or right after the deploy, against the newest summary.
5. **Built in SP1b (2026-11-09).** (a) `tools/sold_price_report.py` reads one published summary (`--document`), which carries both medians, not the raw snapshots §3 sketched. (b) The sold-price rows live in `tools/sim/sold_prices.py` (SM10 and SM11 moved out of `inference.py`). (c) **Results, seeds 1 and 2:** SM10, med7 15 g and the post price 15 g − 1 c against a true 2 g, `sold_med7` 2 g (pass); SM11, `sold_med7` 15 g a day after the rise (pass). (d) **SM18's finding:** the simulated sellers price off the cheapest auction (fitted, simulator plan §1.27), so a lone 60 c dump that keeps coming back drags the market from 1 s to about 70 c; while the dump shows, today's undercut posts at 59 c and a confirmed floor at 69 c, 1 c under what sells, for every N and K tried. So the confirmed floor follows the crowd, not the item's old worth; whether real sellers follow a dump this way is a question for the real data. (e) In SM11's first world buyers came twice as often after the rise and took listings before any snapshot saw them, so they had no sold price: the unseen-sales risk (§7) in its strongest form. The row now keeps the buyers' rate.

## 1d. The 2026-11-21 review (the summary built 2026-11-21 08:25 UTC, with the 11-18 and 11-19 ones)

**What the real data showed** (`tools/sold_price_report.py`; 47,635 items with a sold median, 41,120 with a med7 too):

1. **Asks overstate, almost never understate.** med7 is within ±10% of `sold_med7` for 43% of items, 20% or more above for 44%, 20% or more below for 2%. By sold units in 7 days the median med7 ÷ sold is 1.01 (1–3), 1.11 (3–5), 1.22 (5–10), 1.30 (10–20), 1.27 (20–50), 1.06 (50–200), 1.00 (200+). p90_30 ÷ sold runs 1.6–3.0 in every band, and today's ceiling max(med7, p90_30) a median 2.74× for items with 10+ sales.
2. **The sold median is the steadier number.** Against the summary three days earlier, 69–83% of sold medians moved under 20% (by band, thin to busy) against 61–79% of med7s.
3. **Both known biases push the sold median up**: gear cancels count as sales (SPEC §2.4), and the fastest sales are unseen (median unseen share 21% on busy commodities).
4. **A crowd of asks does not confirm a price on gear.** With N in 2–50 units or K in 2–5 auctions within 2–10%, a floor that skips a lone cheap gear listing lands farther from the sold median in about two thirds of the cases it changes. For lone listings 20%+ under a level of 3+ auctions, the cheapest price one to three days later was back at or below the dump 44–48% of the time and the later sold median was nearer the dump 74–76% of the time. 24,194 of 38,342 books hold one auction. SM18's finding (§1c item 5d) holds on real data.
5. **A floor by hours of sales** (the cheapest price at which the units at or below it reach 1 hour of `sold_per_day`) raises 19.5% of commodity posts by a median 4% and 0.8% of gear posts, leaving at most an hour of sales ahead.
6. **The sell chance ignores the price level.** `sellprob.simulate` gives buyers `sold_per_day ÷ 24` units an hour whatever the price; price only decides who is ahead. With nothing ahead (a flip's relist) Tome of Arcane Brilliance reads 1.00 at 13,399 c and at 240,000 c, 14× its sold median.

**What the simulator showed** (SM20, `tools/sim/price_rules.py`; seeds 1–3; earned per post ÷ the worth; a prototype also ran a buyers' spread of 0.15, a threshold of 10, a sold p75, 7/14/30-day sold buckets weighted 4:2:1 and both floors):

7. Today's rule posts gear at 1.3–1.6× its worth and sells 16–45% of posts; a sold level posts at 1.0–1.1× and sells 60–90%. Gear at 5+ sales a week, steady: today 0.42, sold 7d 0.82, sold half-life 0.88.
8. **A blend of asks does not fix it** (0.55): the asks are the problem, whatever their weights.
9. **Threshold 5, not 10**: they differ only at 5–10 sales a week, where 10 leaves today's rule in place (0.25 against 0.63 in the prototype).
10. **The median, not p75**: p75 earns less while prices are steady (0.49–0.89 against 0.63–0.91) and more only after a rise.
11. **A sold level is too low for some days after a rise** (0.55–0.59 on gear, 0.58–0.64 on the commodity against today's 0.74). Weights halving every 2 days lose least and recover fastest after a fall (0.46 against 0.40 for the 7-day median; 0.54 against 0.30–0.32 at 14 a week in the prototype); 4:2:1 buckets react no faster than the plain 7-day median.
12. **Gear at about 2 sales a week stays overpriced under every rule** (0.12–0.18). Open.
13. **Neither floor shows above the noise** (about +0.02 on a steady commodity; the 1-hour floor 0.91 against 0.97 after a fall). Runs of the same rule differ by up to 0.08.

**Decided by the user (2026-11-21):**

14. **One recency-weighted price level, made by the analysis job, backs every recommendation**: the post price, the best sell and the best buy. The tooltip keeps showing the 7, 14 and 30-day medians.
15. **The level's weighting: try a 2-day half-life** over sold prices.
16. **No confirmed floor in SP2** (item 4, item 13). This withdraws §1 item 3 for now.
17. **The sell-chance fix (item 6) is part of SP2.**

18. **The level is confirmed** (the user, 2026-11-21): the half-life sold median over 30 days at 5+ sold units, else the 4:2:1 blend of the 7, 14 and 30-day ask medians. **The single level is confirmed on the condition that it unifies every price recommendation, buying and selling, the best sell and the best buy included.**

19. **Decided 2026-11-21 (the user), after the design was proposed:** (a) the forecast's bounds and the buy-for-later floor come from what sold, only for items with 20+ sold units in the 30 days; under that, no premium above the level and the floor stays `p10_30`; the high bound is never under the level. (b) The hour-of-week pattern applies in full to every window, best sell and best buy, inside those bounds; it is expected to become a strong signal as the season goes on. (c) **The post-now ceiling uses the pattern for the current hours too** (`level × how` now), so posting now and a window share one formula. (d) No on-screen display of the level in SP2. The SP2 design below predates (a)–(c) and is updated at the spec merge.

20. **Decided 2026-11-21 (the user): the level is pattern-neutral, and sold prices are stored per hour.** A 2-day weighting on raw prices would read the week's cycle as a rise (a Sunday level is mostly weekend sales) and a partial newest day as a fall (a 9 am level has only the night's sales). So each sold price is divided by the pattern of its own hour of the week before the half-life weighting, and a forecast multiplies the hour's pattern back. That needs sold prices per hour (they arrive per gap already; `sold_prices_d1` only sums them), kept 30 days: a new hourly table, a bench for the 600 MB budget, and a rebuild from raw for the past days. A world with a weekly and a daily price cycle is simulated first (SP2a), adjusted against unadjusted.
21. **Measured 2026-11-21 on the real summary: the price pattern has no hour of day yet.** `how` is the weighted median close per hour-of-week slot ÷ med30, a slot with under 3 weeks taking its weekday's value, smoothed over 5 hours. The market has data since 2026-11-06, so the median busy commodity has 11 of 168 slots at 3 weeks: its pattern is flat within each day and differs only by weekday (median Thu 0.935 to Sun 1.021). The hour-of-day shape arrives as slots reach 3 weeks, from about 2026-11-27. Whether the pattern should come from sold prices by hour, not asks, is decided from real data once the hourly sold prices have several weeks. **The cloud's hourly mode reads no database** (SPEC §2.4), so an hourly refresh of the level cannot come from it as built; where it runs is SP2b's design question.

22. **Measured 2026-11-21 on all the raw (354 snapshots, 2026-11-06 to 11-21; `tools/hourly_prices_report.py`, then each hour against its centred 25 hours to take the day's drift out; 838 commodities with 100+ usable hours; local time taken as UTC−5):** (a) **sales follow the clock**: 5.0–5.6% of a day's units sell in each hour from 18:00 to 03:00 against 2.8–3.0% from 06:00 to 11:00, about 1.9×. (b) **The typical commodity's sold price does not move by hour** (the median item's ratio is 1.000 in every hour, and so is its cheapest ask). (c) **A minority has a daily cycle, in the direction the user expects**: averaged over items the sold price is lowest around 01:00–04:00 (1.01) and highest around 15:00–17:00 (1.045); afternoon against night is 5%+ dearer for 24% of commodities (33% of trade goods, 20% of consumables) and 10%+ dearer for 14–15%, and 5%+ cheaper for 6–7%. (d) Asks do not show it, so a pattern built from asks misses it and one built from hourly sold prices would catch it for those items. (e) The economy is two weeks old and its busiest consumables are levelling goods (fish, minor elixirs); raid consumables do not trade in volume yet, so the raid-time effect cannot be in this data. (f) A weekday effect cannot be told from the fortnight's overall price drift yet; the ask pattern's weekday values (item 21) may be that drift.
23. **For the simulator** (the user, 2026-11-21: take the real data's lessons into it): the sellers' fits are from the first 31 hours and are refitted from these 15 days (`tools/sim/fit.py`); buyers get the measured daily rhythm of item 22(a); a share of items gets a daily price cycle as item 22(c); the rows whose numbers move are reported. A project agent, `market-sim` (`.claude/agents/market-sim.md`, drafted for the user's review), owns running the simulator and reporting the distance to a goal.

24. **Approved** (the user, 2026-11-21): the design as items 14 to 23 leave it. Where the section "SP2 as proposed" in §3 differs from items 19 to 23 (bounds from what sold only at 20+ units; the post-now ceiling carries the current hour's pattern; the level is pattern-neutral; sold prices are stored per hour), the items win, and the spec merge writes them. The `market-sim` agent (`.claude/agents/market-sim.md`) gives the design one independent analysis before that merge.

25. **The independent analysis** (2026-11-21; an Opus agent on the `market-sim` instructions, its own code over the summary, the hourly record and a full pass of the raw; its SM20 numbers match item 7 and 8). It reproduced items 1, 7, 8 and 22(b), and found:
    - (a) **Dividing by today's `how` wrecks the level.** `how` is a slot's close ÷ med30, so a price that crashed on a Wednesday reads as "Wednesdays are cheap". Its 168-slot mean is 5%+ off 1 for 51% of commodities and 62% of gear. Dividing sold prices by it moves the 2-day level 5%+ for 57% of 774 busy commodities and 2×+ for 17 (Bloodbelly Fish 269 c to 16,667 c; Shellfish 90 c to 756 c).
    - (b) **Item 22(c) overstated the daily cycle.** Hours shuffled within each day already give 6–8% of items "5%+ dearer"; between two halves of the days the items' swings correlate at 0.22 and 13% are 5%+ dearer in both. About 13% of commodities have an afternoon premium of about 4%.
    - (c) **Item 22(a)'s 1.9× is the peak hour against the lowest hour**; evening against morning as blocks is about 1.6×.
    - (d) **No weekday effect yet** (week 1 against week 2 per item: 0.02), and busy commodities fell a median 17% a week.
    - (e) **The overstatement of item 1 is gear's**: commodities read 1.00–1.04 in every band, and on gear with under 3 sold units the sold median is often the same lone listing as med7 (63% at 0–1 units).
    - (f) **Sold prices predict the next 72 hours' sold median better than the ask blend in every band**, gear at 1–5 units in 30 days included (median error 0.22 against 0.35, the blend 24% high). On gear the half-life hardly matters (1, 2 or 4 days within 0.01); on commodities 1 day wins in every band (0.16 against 0.22 at 5–10 units).
    - (g) **In SM20 (prototype variants)** a 1-day half-life earns 0.71 against 0.46 on gear after a drop and 0.74 against 0.64 on the commodity after a rise, and 0.86 against 0.88 when steady (inside the noise); a threshold of 10 is worse than 5 on every seed (0.77 against 0.88); a threshold of 3 equals 5 when steady.
    - (h) **About 10% of sold units come from endings that may be expiries**, priced about 1.2× the clean median (p90 2.9–4.1×): the top of a sold curve is inflated. At a 2-day weighting gear with 20–50 units has about 19 effective units, at 5–10 about 5.
    - (i) **SM20's worlds flatter the sold level on gear and cannot show its commodity case**: gear med7 ÷ sold is 1.23–1.92 there against 1.22–1.30 real; the commodity's p90_30 ÷ sold is 1.02–1.15 against 1.89 real.
26. **Decided by the user (2026-11-21), after item 25:**
    - (a) **The pattern is rebuilt before it is used on the level** (option A). An hour's observation is its close ÷ the item's median close over the week centred on it (3.5 days each side), not ÷ med30, so a move of the level is not a pattern and a full week cancels its own rhythm; the newest 3.5 days give no observation yet. The slots, the weeks' weighting, the 3-week rule, the weekday fallback and the smoothing stay. The pattern stays on asks (item 21).
    - (b) **A 1-day half-life**, checked again once the market has 30 days of data (from 2026-12-06). The level looks back 30 days; at 1 day, sales older than about 4 days hardly count.
    - (c) **The threshold is 3 sold units** in the 30 days, not 5.
    - (d) **Commodities keep the sold level**: one level for everything (item 18).
    - The user's worked example, kept for the scenarios: an item sliding 2 c a day, mornings 5% cheap, evenings 5% dear, Thursday evening 10% dear; at Thursday 20:00 the level is 100 c under the rebuilt pattern at 1 day, 102 c at 2 days, and 107 c if the pattern is not removed.
27. **For the simulator, from item 25** (added to item 23): a pattern world (the buyers' rhythm, about 13% of items with a 4% afternoon premium, an item that crashes 90% over 3 days) with an hourly fair-price factor that does not change demand; a falling regime (2–3% a day) beside SM20's steady, rise and drop; a commodity shaped like the real one; SM20's gear asks retuned to the real ratios; the expiry-ambiguous share reported from the truth.

**Still open:** the three questions under "Open" in §3's SP2 design; whether the hourly run can refresh the level; the buy side and the chance fix, not yet simulated; the unexplained step in gear's posting on 2026-11-12 (the share at the cheapest price fell from about 75% to 55% overnight).

## 1b. Decided at planning (the user, 2026-11-09: D1 A, D2 A)

**D1. Where the sold price shows during the comparison week.**
- **A. In the cloud summary document and a report tool only** (`tools/sold_price_report.py`: sold median against med7 per item, with units). No app or addon screen changes until the switch.
  - Pro: no UI churn; nothing the plan does changes; no mockup needed.
  - Con: you see the comparison only by running the report (I'd run it and summarize).
- **B. Also a line in the dashboard's item detail** ("Sold median 7d: 2s 10c from 340 units").
  - Pro: visible on any item while browsing.
  - Con: a mockup round and a new displayed field for a number that may be replaced a week later.
- **Recommend A.** The comparison is a decision input, not a feature; B can follow with the switch if the number proves useful.

**D2. When to decide the switch.** The comparison needs about a week of sold prices from the deployed cloud job.
- **A. At the 2026-11-21 real-data review** that's already planned (steady-deal thresholds, M33b and M34 constants). About 10 days of sold prices by then.
  - Pro: one review session; N, K and the "enough sales" threshold come from the same data.
  - Con: SP2 waits about 12 days.
- **B. As soon as 7 days exist (about 2026-11-17)**, a separate review.
  - Pro: four days sooner.
  - Con: two reviews; fewer days of data.
- **Recommend A.**

## 2. Working rules

- CLAUDE.md's batch rules: one worktree and PR per milestone, `tools/verify.ps1` (here: its steps run by hand on macOS), scenario rows before code, docs in the same commit.
- `ahsync/engine/` stays pure. `engine/prices.py` is ported (`Engine/Prices.lua`): SP2's changes land in both in the same change (the `engine-parity` agent reviews).
- Engine constants are never tuned on simulated data. The simulator measures; N, K, the band and the "enough sales" threshold come from real data.
- Desktop-built markets (AHF, AHledger) have no auction ids, so no sold prices. They keep the ask medians unchanged (a non-goal, §4).

## 3. Design

### SP1: record and publish (now; data only, nothing the plan does changes)

- **What a sold price is.** Each auction that left early and was not a commodity cancel (BZ40) contributes its unit price (`Auction.price` at its last sighting), weighted by the units counted sold: `qty × (1 − expired)` (`volume.Ending.expired`, BZ44). A partial fill contributes its units at its auction's price. A matched possible relist is still counted sold, as `sold_per_day` counts it (BZ27).
- **Not counted:** sales of listings never seen (`unseen_per_day`, BZ45); they have no price. The report shows the units this leaves out per item (§7).
- **Stored** per item and day in a new table `sold_prices_d1(market, key, day, src, prices TEXT)`, `"price:units,..."` like `new_listings_d1` (schema 30, migration 30). It's written at ingest beside `lifetimes_d1`, replaced per day, and pruned with it.
- **Published** in the intelligence (schema 16): `sold_med7` (copper, the units-weighted median of the last 7 days' sold prices; None without any), and `sold_units7` (the units behind it). The weighted median's rule for an exact half split follows SPEC §3.3's even-count rule, stated in the spec merge. A schema-15 reader ignores the fields (the contract's `ADDED_IN`).
- **Report:** `tools/sold_price_report.py --raw <raw/market folder> --catalog <document.json.gz>` lists per item: sold_med7, sold_units7, med7, p90_30, the post price today and the ratio between them, sorted by how far they disagree.
- **Simulator:** SM10 and SM11 are rebuilt as §1c describes and report sold_med7 and today's post price against the true sold price, beside the close. A new row, **SM18**, a lone cheap listing under a real price level, reports where today's post price lands and where a confirmed floor at a few N/K values would (measured only).

### SP2: switch and confirmed floor (after the 2026-11-21 review; the user approves the departure then)

Sketched now so SP1 records what it needs; specified at its own spec merge once the data is in:

- The post-price cap becomes `max(sold_med7, …)` for items with at least the threshold of sold units, else today's ask cap.
- The selling windows switch the same way.
- The confirmed floor replaces `curOthers − 1` with the cheapest confirmed price − 1.
- It changes `engine/prices.py` and `Engine/Prices.lua` together, the whole-plan snapshot moves (reviewed), and SD5, SD9, SD11 and SD29 are rerun.

### SP2 as approved on 2026-11-21 (§1d items 14 to 27; replaces the sketch above)

**Objective.** One price level per item, made by the analysis job, that every price recommendation reads: the post price, the best sell, the best buy, flips, and the craft, disenchant and gathering values that pass through them. The sell chance learns the price level.

**Sold prices by hour** (new table `sold_prices_h1`, kept 30 days). Each gap's sold prices are stored under the clock hour of the snapshot that found the auctions gone, in the row format of `sold_prices_d1`. `sold_prices_d1`, `sold_med7` and `sold_units7` stay as they are.

**The pattern** (`how`, rebuilt; §1d item 26(a)). An hourly close gives its slot one observation: the close ÷ the median of the item's hourly closes in the 168 hours centred on it (84 before, the hour itself and 83 after). It needs that whole week to lie inside the item's history and at least 84 closes in it. Everything after that is as today: the weighted median over the weeks, three weeks for a slot, the weekday fallback, the smoothing. It is not normalised: the centred median makes its mean about 1, and SP2a measures it.

**The level** (published per item; summary schema 17):

- With at least 3 sold units in the last 30 days: each stored price is divided by the pattern of its own hour of the week (rounded half up to copper), each unit weighted `0.5 ^ (age in hours ÷ 24)`, age being the document's hour less the sale's; the level is the units-weighted median of those (SPEC §3.3's rule, over the weighted units). `level_source` is `"sold"`, `level_units` the unweighted units.
- Otherwise the floor of the 4:2:1 mean of med7, med14 and med30 over the ones known; `level_source` `"asks"`. None when there is neither.
- With at least 20 sold units: `sold_curve`, the same weighted prices' 0, 10, 25, 50, 75, 90 and 100th percentiles (the lowest price whose cumulative weighted units reach the share). The low bound is its 10th, the high bound the larger of its 90th and the level.
- Under 20 units, and for an ask level: the low bound is `p10_30` (the level when that is unknown or above it), the high bound the level.
- med7, med14, med30, p10_30, p90_30 and `sold_med7` stay published: the tooltip's price history and the dashboard read them, and the suspect-price check (FM5, FM31) stays on asks.

**What reads it:**

| Today | Becomes |
|---|---|
| A window's forecast `level × how × trend`, level med7 else med30, clamped `[p10_30, max(med7, p90_30)]`, the level itself on thin data (`contract.price_level`, `plan.slot_price`, TT22, TT23) | the same formula on the new level, clamped to `[low bound, high bound]` |
| Post price `min(curOthers − 1, max(med7, p90_30))`, med7 alone on thin data (`prices.post_price`, `relist_price`; `Prices.lua`; `analysis.market`; `Options.Reprice`, `Reselect.RepriceSale`) | `min(curOthers − 1, ceiling)`, the ceiling being the forecast of the current hour (`level × how` now, clamped to the bounds); without a competitor, the ceiling. PP2 and PP4 are replaced by PP5 |
| Buy for later when `cur < p10_30` (`candidates._flip_first`, `_flip_opportunity`) | `cur <` the low bound |
| Buy windows' `sell_net`, a later buy's expected price, flips' sale price and the exported flip offers | unchanged formulas, fed the level and the bounds |
| Sell value, hence disenchant, craft product and holdings values | follow the post price; no rule of their own |

The addon reprices after a scan, so it needs the pattern of the current hour: the market file gains the level, the two bounds and the 168-slot pattern per item (packed as `supply_how` is), and `EXPORT_VERSION` rises. `/ahp perf` checks the addon's memory.

**The sell chance.** In the queue simulation (SPEC §3.3.2) the hourly demand is multiplied by a price acceptance `a(p)`: 1 at or under the level; above it, the share of the sold curve's units at or above `p` ÷ 0.5, read by linear interpolation between the curve's points, 0 at its 100th and above. The curve is scaled by the hour's pattern as the level is. An item without a curve keeps `a = 1`: its recommended prices never exceed its level. **Simulated before it is built** (SM21).

**Hourly.** The daily run computes the level for every item. The hourly run refreshes it only if a run with the history fits its time and the 600 MB budget (`tools/bench.py --summary`); the hourly mode reads no database today, so this is a bench and a design step of SP2b, and the level stays daily if it fails.

**Non-goals.** No confirmed floor. No new display of the level or its source (a mockup round of its own). No change to how sales are counted, to `sales_how`, or to the windows' shape. No pattern built from sold prices (decided from real data once the hourly sold prices have several weeks). Thin data's "post now only" rule (PF30) stays on the count of closes. Gear at about 2 sales a week is helped by the threshold of 3 but not solved.

**Constraints.** `engine/prices.py` and `Engine/Prices.lua` change together (the `engine-parity` agent reviews); a schema-16 reader ignores the new fields (`ADDED_IN`) and a schema-16 document is priced from an ask level built from its medians; the addon is redeployed with the app; the whole-plan snapshot moves and its diff is reviewed; engine branch coverage stays 100%; the cloud bench stays under 600 MB with the hourly table.

**Acceptance.**
- A flip's resale price never exceeds the high bound: the 2026-11-18 items (Tome of Arcane Brilliance, Writ: Sniper Scope, Black Diamond) read their sold levels, not p90_30, on the real summary.
- On the real summary no busy commodity's level differs 2× from its unadjusted level (Bloodbelly Fish and Shellfish read their recent sold prices).
- SM23's crashed item: the level within ±10% of its worth two days after the crash; the afternoon-premium items' level within ±3% of their worth at any hour.
- SM10 and SM11's post prices land within ±5% of the true sold price; SM20's sold half-life rule, at 1 day and 3 units, and the built level give the same prices.
- SD5, SD9, SD11 and SD29 are rerun and reported.
- A desktop-built market (no auction ids) prices from the ask level, with the rebuilt pattern.

**Open, each measured in SP2a and taken to the user if it would change a rule.**
1. The bounds' top: whether the curve should leave out expiry-ambiguous endings, or use a lower percentile, given §1d item 25(h) (SM24).
2. Whether a slot's pattern should be shrunk towards 1 until it repeats (item 25(b): most single-fortnight cycles do not replicate).
3. The half-life at 30 days of data (item 26(b)).

**Milestones.**
- **SP2-0, spec merge** (this change): SPEC §2.4, §3.3, §3.3.2, §3.3.4, §3.4, §5.0 and §8.10; SCENARIOS rows and pending IDs; ARCHITECTURE. Fable: meaning-changing documents.
- **SP2s, the simulator** (§1d items 23 and 27): the sellers refitted, the pattern world, the falling regime, the real-shaped commodity; the rows that move are reported. Opus: new worlds.
- **SP2a, measure the unmeasured.** SM21 (the acceptance), SM22 (the buy side), SM23 (the pattern and the level), SM24 (the bounds). A result that changes a rule goes to the user before SP2c. Opus: new worlds.
- **SP2b, the pattern, the hourly table and the level.** `engine/profile` (W17), migration and `sold_prices_h1` (BZ68), `engine/lifetime_prices` (BZ69, BZ70), `analysis/market` and the contract (schema 17, BZ71), the cloud's bench and a rebuild from raw. Opus: a contract with several writers.
- **SP2c, everything reads it.** The table above in Python and Lua (PP5, TT51, R35), the export, the snapshot, the SD reruns. Opus: byte identity and the ported twin.
- **SP2d, the chance.** `engine/sellprob` (P28) and what the plan's simulations pass it. Opus: an engine algorithm.
- **SP2e, deploy and check.** The cloud deploy is the user's; then the acceptance check on the real summary.

## 4. Non-goals

- Sold prices for desktop-built markets (no auction ids).
- Any change to how sales are counted (`sold_per_day` stays as it is).
- The recent-activity multiplier (§1.10 of the simulator plan) and momentum (M34b): their own milestones.
- An addon or tooltip display of the sold price (D1 B is dashboard only, if chosen).

## 5. Scenarios

Written into [SCENARIOS.md](../SCENARIOS.md) at the spec merge (SP0), not repeated here: **BZ52** (the sold prices a gap keeps), **BZ53** (`sold_med7`, the units-weighted median, with its exact-half rule), **BZ54** (summary schema 16 both ways; nothing reads the fields), **SM10** and **SM11** rewritten (§1c), and **SM18** (a lone cheap listing; a confirmed floor's N and K grid, measured). BZ52–BZ54 and SM18 are in `tests/pending_scenarios.txt`. `sold_prices_d1` is not added to the AI assistant's query allowlist (SPEC §3.8.5) in SP1.

## 6. Milestones

- **SP0, spec merge.** SPEC §2.4 (sold prices kept), §2.4's document fields (schema 16), §3.3 (the weighted median's rule), §8.10 (SM18); SCENARIOS rows, SM10 and SM11 rewritten there and in `docs/plans/market-simulator.md` §3; pending IDs; ARCHITECTURE (the new table and contract fields). Opus: meaning-changing docs.
- **SP1a, record and publish. Built 2026-11-09** (the cloud bench owed, §1c item 4). `engine/volume.py` (the sold prices per gap), migration 30, `history/writes.py` and `reads.py`, `collect/observations.py`, `analysis/market.py` and the contract (schema 16), the cloud's ingest and prune; BZ52–BZ54; `tools/bench.py --summary` (the cloud budget is 600 MB). Opus: the contract and migration have several writers.
- **SP1b, the report and the simulator rows. Built 2026-11-09** (§1c item 5). `tools/sold_price_report.py`, SM10/SM11 updates, SM18. Sonnet for the report (it only draws numbers from a documented format), Opus for SM18's world.
- **SP1c, deploy.** Deploying the cloud job is the user's (`cdk deploy`). The comparison week starts then.
- **SP2** after the review: its own spec merge, approval and milestones (§3, "SP2 as approved").

## 7. Risks

- **Unseen sales**: a listing bought before any snapshot saw it has no price, so the fastest sellers count least. The report shows the units left out per item; if they are a large share for busy items, SP2's "enough sales" threshold must account for it.
- **Cloud memory**: one more per-item daily row. `tools/bench.py --summary` checks the 600 MB budget.
- **Gear with suffixes**: sold prices are per item key, like everything else, so a suffix splits them. That's the same as today's asks.

## 8. Acceptance (SP1)

- BZ52–BZ54 pass in pytest; engine branch coverage stays 100%; the whole-plan snapshot stays byte-identical (nothing the plan reads changes).
- The bench stays under 600 MB.
- `tools/sold_price_report.py` runs on recorded raw and prints the comparison.
- SM10, SM11 and SM18 report their numbers.
