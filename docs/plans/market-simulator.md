# Plan: the market simulator

Written for: the Opus session that runs this batch in Claude Code on this repo. The user's decisions were taken in conversation on 2026-11-08; the scenario list was widened by the `strategic-planner` (Fable) and checked against SPEC by Opus. Nothing in §1 is open for redesign. The spec merge (SPEC §8.10, SCENARIOS' simulator section, `tests/pending_scenarios.txt`) is this batch's first milestone, not this commit.

**Why.** Several milestones wait on 14 days of real data (about 2026-11-21): M33b's data-bound parts, M33c (K8) and M34. The simulator lets their code be built and tested before then, and, for the first time, checks what the analysis infers (sold, expired, cancelled, relisted) against a known truth, and what the plan decides against what would have made money. On 2026-11-21 only the constants are set from real data.

## 1. What is decided (do not re-litigate)

1. **No production change.** The simulator writes raw envelopes exactly as `ahcloud/collector.py` stores them (`raw_key`, `raw_envelope`: Blizzard's record shapes, commodities and regular auctions in separate bodies, Blizzard's band bounds `LONG` 2–12 h and `VERY_LONG` over 12 h) into a local store folder. A runner calls `ahcloud.hourly.run_hourly` per snapshot and `ahcloud.daily.run_daily` once per simulated day, in-process, with the catalog's item search answered by a fake client over `FakeTransport` (as `tests/support/cloud_world.py` does). `now_ms` is the newest snapshot's `at_ms`, so simulated time needs no clock change.
2. **Everything runs locally**: no AWS, no WoW, no network, no Blizzard credentials. X1 measures this machine's wall time and peak memory as the trend against Lambda's 15 min and 2 GB; a real Lambda run, if wanted, is asked first.
3. **Ground truth.** Every post, buy (units and price), cancel, expiry and relist, ours included, goes to a truth log beside the raw. Scorers compare against it.
4. **Three tiers plus stress.** Tier 1 (`SU`, unit sequences, exact numbers a human can check) and Tier 2a (`SM`, inference against truth within a stated tolerance) are pytest tests. Tier 2b (`SD`, decision quality) **starts as a report**, not a failing test; a stable one may become a test later, the user's call. Tier 3 (`SX`, stress) is run by hand or with `-Bench`, never in CI.
5. **Paper trading.** Synthetic characters (gold, bags, professions) follow the plan inside the simulated market: their buys take listings, their posts appear in later snapshots, competitors react. Their state reaches AHSync **through the real addon import**: `tests/support/savedvars_builder.py` is extended to write a day's posts, purchases, mail, inventory and loot as SavedVariables, and `state/importer.py` reads them, so the importer, cost basis, tracking and missed-peak logic run as in life. Direct database writes are kept only as a fallback for SX6 if import speed is the bottleneck.
6. **Benchmarks.** Each decision run is scored against an **oracle** (knows the future: the upper bound), **do nothing** (zero) and a **naive trader** (buys the cheapest listing, reposts at `med7`). Metrics: gold made, gold-days tied up, worst drawdown, stranded stock at the end, share of the oracle's profit; a ledger conservation check after every event (SU12).
7. **Determinism.** One `random.Random(seed)` per run, no other randomness; the same seed gives byte-identical raw, truth and report (SU9, SPEC §8.6). Failures print the seed.
8. **Fitting.** The model's parameters (listing price spread against the floor, arrivals per hour, stack sizes, duration mix, commodity cancel share, non-cheapest-first fills 0.2%) are fitted from the raw collected so far, then shaped per scenario. Constants of the engine are **never** tuned on simulated data: the simulator ranks strategies; SPEC's constants still come from real data (M33b step 1, M34 step 1).
9. **Momentum (SD1–SD4) starts as failing tests that document the gap.** M34 as specced makes the detection half pass (the regime label); the action half needs **M34b** (decision 14).
10. **The recent-activity multiplier** (`docs/plans/blizzard-cloud.md` §1, deferred item 1) is wanted. It weighs the last hours' observed endings against those expected for that hour of the week, acting only outside chance, so a busy item reacts in hours and a quiet hour that is normal for its slot does not trigger it. SD5 and SD7 are its tests; it is its own milestone with its spec merge.
11. **Reaction targets** (the pass bands of the decision report; provisional, revisited with real data):

    | Scenario | Target |
    |---|---|
    | SD5, a real drop of ≥ 40% in sold/day that lasts | no new buys within **12 h** for an item selling ≥ 100 a day, **36 h** at about 25 a day, **4 days** at about 10 a day (until then the 7-day average decides); units bought after the drop ≤ one day of the new rate; price cuts on held stock from day 3 |
    | SD7, one quiet day (≤ 50% dip) then normal | no selling below the plan; buying shrinks by at most 25% for one day |
    | SD28 / SD29, a held item's price drops ≥ 20% | the exit rule fires when the drop lasts 12 h; nothing changes for a one-snapshot dip (FM5) |
    | SD3, a spike lasting under 24 h | zero buys; if holding, sell into it |
    | SD1, a real rise | labelled within 5 days (M34's provisional ±1%/day band) |

12. **A solid steady deal** (SD9, SD10): **commodities** ≥ 100 sold a day; **crafted gear** ≥ 10 sold a day (per item key; crafted gear has fixed stats, so suffix keys don't split it); both ≥ 15% margin after the AH cut and deposits, ≥ 60% sell chance at the planned price, price and sales within ±15% of their 14-day level. **Revisit on real data on 2026-11-21.** Pass: the planted deal is in its category's top 5 (Gathering's gold per hour, or the plan's profit per day) within 2 days of data, its realized margin within 5 points of the planned one, and no SD11 decoy is funded.
13. **The sold-price fix** (`docs/plans/blizzard-cloud.md` §1 deferred item 3; LESSONS 2026-11-07 "an hourly close is a price the item trades at") is wanted, as the milestone after M33b: the price each sold auction left at is kept, a units-weighted sold-price median is published beside the ask medians for a week, then the post-price cap and the windows switch to it for items with enough sales, asks remaining the fallback. The switch is a spec departure approved by the user at that step. SM10/SM11 are its test pair.
14. **M34b, the regime feeds the plan** (added now as a placeholder, no numbers). Under "rising, demand holding" the 30-day cap of TT22 loosens by a bounded amount and a staged exit (sell part into the rise, hold the rest past 7 days) becomes plannable; under "falling" or "demand fading" buying shrinks. Its numbers come from the SD1–SD4 report; its spec merge is the user's approval.
15. **Out of scope.** SD25 (gold stranded on another character): dropped, the user does not see it as a problem. SD35 (farmed stock competing with flips for a window's capacity) moves to `docs/plans/learned-catalog.md`. SD36–SD38 belong to that batch too and run in this simulator once it lands.
16. **Deposits, checked in game by the user 2026-11-08.** A **cancel loses its deposit** (SPEC §3.4.1 had it back: the ledger, SU12, counts it lost; the engine's fix is its own change, LESSONS entry first). The deposit is **`max(1s, rate × vendor sell × units)`** with rates **5% (2 h), 20% (8 h), 60% (24 h)** (the user, 2026-11-08, from game references; consistent with the one reading: a Healing Potion, vendor 75c, took 1s at every duration, its rated deposits of 3c, 15c and 45c all under the minimum). `fees.DEPOSIT_BP` (1.25%, 5%, 15%, no minimum) is wrong and is fixed in its own change with its Lua twin. Confirmed by the user the same day: the minimum applies **per auction**, and the rated part is **floored** to the copper (Oiled Blunderbuss, vendor 11s 73c, 8 h: 20% = 234.6c, charged 2s 34c). The AH cut is taken as 5% (the user, 2026-11-08, from published game references; it is what `fees.AH_CUT` already uses).

Decisions made during execution are appended here.

17. **MS1's findings (2026-11-08; the user decides the engine's side).** The first steady market (3 commodities, 8 days, hourly refreshes) puts the engine's sold/day 2–11% under the truth, from two biases that partly cancel (LESSONS 2026-11-08): units bought before a listing's first snapshot are never seen (9–17% of units sold), and the overlap split credits a slice of true expiries as sales (3.8% of Linen's expired units). A further 2.5% for Linen sits between the lifetimes' count and the daily document's sold/day, still unexplained (MS2's triage). SM1's approved band (±5% of truth) would fail, so SM1 stays pending: whether sold/day should estimate the unseen sales, whether the split should weigh the expiry rate, or whether SM1 compares against the sales a snapshot can see, is the user's call. Expired share matches truth within 1.3 points.
18. **Rebuilds in the simulator publish to the run's own intel store** (as production's do), so they carry the latest document's hourly rings like a daily run; SM8 compares them byte for byte. A rebuild into an empty store differs only in those rings, by design.
19. **The runner runs the hourly mode only when asked** (`RunSettings.hourly`): the daily document is what SM rows read, and the hourly mode per snapshot is the slow part at scale. Background archetypes stay hand-set in MS1; fitting them from the collected raw (the user, 2026-11-08) is MS2's `tools/sim/fit.py`.
21. **Fix both biases now, before fitting (the user, 2026-11-08).** The gap is structural, so the fixes don't wait for fitted markets; the simulator proves them across market shapes, and fitting is a sanity check after. A milestone of its own, before the rest of MS2: (a) the overlap split weighs the item's own expiry rate (an auction vanishing in its expiry hour most likely expired when most of its listings expire); (b) sold/day estimates the sales before a listing's first snapshot from what is observed (how fast newly seen listings sell, how often they are first seen already bought down). Each gets its scenario row first, then the formula (both runtimes where ported), then SPEC; the whole-plan snapshot moves and is reviewed with the user. SM1 keeps its approved ±5% band as the acceptance test.
22. **The estimate is included in sold/day everywhere; only the dashboard's item detail says how much (the user, 2026-11-08).** The tooltip's Sales Rate keeps its one line ("55 sold/day", corrected); the item detail's existing source line (SPEC §3.3.1, "every sold/day shows its source mix") gains the estimated part, e.g. "sold/day 52 · 7 days of auction data · about 6 a day unseen (est.)". It is a displayed text, so it goes into `docs/mockups/dashboard.html` for approval in the fixes milestone, before code.
23. **The overlap split is fixed first, on its own (BZ44; Opus at execution, within decision 21).** Selling and expiring compete, weighed by the item's early-leave rate measured over the whole observed life in the same lifetimes pass (5 pseudo leaves toward the pooled rate). Across six simulated market shapes the net misfiling fell from +3–6% of units sold to +1–2%. A rate measured only in the last 3, 6 or 12 hours before the window was tried: about a point better for commodities, worse for gear, and unsteady between cutoffs, so not worth a constant. The remaining +1–2% comes from listings selling less often late in life. **Corrected at the independent review (same day):** a rate measured over the whole pass let snapshots after a day change that day's endings, which `id_days` never recomputes, and made an hour pooled across midnight depend on recompute order; the rate is now causal (the gaps that ended in the 24 h before each ending), the pass's lookback grew from 36 h to 66 h to hold that window, the cloud keeps 5 days of compact snapshots instead of 4, an item without exposure takes its pool's rate, `Ending.overlap` keeps the raw share for `tools/lifetimes_report.py`'s outcome and relist counts, and the pools count every pass snapshot's commodities. Two regression tests with a non-zero rate (both failed before). A rebuild over the 31 real snapshots: 173 s (171 s before), peak 1,047 MB (871 MB); the longer lookback's full cost shows only once a market has more than three days of raw, which SX1 measures. The unseen-sales estimator follows as its own research step (decision 21's (b)): the first candidate recovered 27–90% of the unseen units depending on the market shape, and the unseen share reached 35% in a busy market.
24. **The unseen-sales estimate is "A", the thin-supply flag is deferred (the user, 2026-11-08).** Three estimators were compared over six market shapes with real-shaped seller pricing (the collected raw: 42% of new commodity listings at the cheapest price, 19% up to 5% above, 38% above that, 1% below): A (a listing's first observed gap's sales, extended back over half its posting gap) took the error from −4% to −0.6% (steady), −9% to −4% (busy) and −3% to +2% (flooded); two demand-rate estimators overshot or fell back to the observed count; stack-size inference was dropped (first-seen quantities are mostly 1–5). No observable signal separated the shapes where sales go unseen (18%) from the rest, and those shapes may be an artifact of the seller model (the floor drifting above most buyers), so the flag waits for MS2's fitted markets. Built as BZ45 (`engine/unseen.py`, credited to the measuring gap so the BZ44 review's invariants hold; `ID_METHOD_VERSION` 8). **SM1 stays pending:** with MS1's own sellers (about 17% undercut the cheapest) sold/day is still 1–19% low over a week, from listings bought out before any snapshot sees them, which no snapshot estimator can count; whether real sellers undercut that often is what fitting must answer (the 1% seen undercutting is a floor: undercutters sell before they are seen). The item detail's "about N a day unseen (est.)" line is the next small milestone (a stored column, a summary field, the mockup). A rebuild over the 31 real snapshots: 185 s, peak 1,223 MB (1,047 MB with BZ44, 871 MB before); the analyzer has 2 GB.
20. **MS0 reached `main` through MS1's PR.** #55 was stacked on the plans branch and merged into it after #52 had merged, so its commit is carried in MS1's branch. Stacked PRs are avoided from here on.

## 2. Working rules

CLAUDE.md's, plus:

- **Where the code lives**: `tools/sim/` (standard library only), mirrored by `tests/tools/sim/`. One module per component: `market.py` (the model), `emulator.py` (snapshots and raw), `truth.py`, `runner.py` (the cloud job in-process), `trader.py` (our agent and the import), `scorers.py`, `fit.py`; `tools/sim_report.py` prints the inference and decision reports.
- **Scenario first**: every SU, SM, SD and SX row is in SCENARIOS before its code, its ID in `tests/pending_scenarios.txt` until built. Test names follow CLAUDE.md (`test_SU3_commodity_cancel_while_cheaper_stays`).
- **Tolerance tests** (Tier 2a) state their tolerance and the truth they are compared with in the row's `why`; SPEC §8.10 says when a tolerance is allowed (only against a simulated truth, never for an engine formula, which keeps exact scenarios).
- **Small worlds in pytest** (about 50 items, 3–14 days, under a few seconds each); full-size worlds only in SX.
- **A failing simulated scenario is a finding, not a bug, until the truth model is checked.** A gap between engine and truth is triaged: the simulator wrong, a known limit (pinned by a scenario), or an engine bug (CLAUDE.md's bug rule: reproducing scenario, fix, SPEC, LESSONS).
- **Spec contradictions found while planning** are fixed in the spec merge: SPEC §2.4 "sold/day … per item over 30 days" against §3.3.2 and §3.3.6 and the code (`analysis/market.py`: the 7-day volume window): the 7-day average is what is built; the blend cap (§3.3.2 "never more than half", §2.4's planned 0.9 at 300 listings) and the tier count (four in §3.3.2, five in §2.4) are marked as built against planned for M33b. SM5 takes both as parameters.

## 3. Scenarios

### Tier 1: unit sequences (`SU`, exact)

| ID | Sequence | Expected |
|---|---|---|
| SU1 | Commodity buy of 4 from 14 units at the cheapest price | 4 sold, the auction alive |
| SU2 | Cancel of the cheapest commodity auction | counts as sold (pins the known bias) |
| SU3 | Cancel while a strictly cheaper auction stays with its units unchanged | a cancel (BZ40) |
| SU4 | Buy 5 min before expiry | counts as expired (pins the known error) |
| SU5 | A 24 h auction across the 12 h line; `SHORT` at first sight | `LONG` at 12 h; treated as 2 h |
| SU6 | A gap over 6 h and a 3 h gap, both across an evening peak | over 6 h links nothing and leaves the day's coverage; 3 h spreads sales evenly (BZ30); the day total exact; the hourly error reported |
| SU7 | A record repeated field for field; one id with different content | counts once (BZ38); the snapshot refused (FM33) |
| SU8 | An auction across midnight | credited to the right day |
| SU9 | The same seed twice | byte-identical raw, truth and report |
| SU10 | Our post | appears in the next snapshot with its band and price |
| SU11 | We buy 20 of a 50-unit floor | the floor drops by 20; our cost walks the book |
| SU12 | Ledger conservation after every event: cash + inventory + listed + mailbox = start − AH cuts − deposits lost; each event moves exactly one pair of buckets (buy, post, sale, payout collected, expiry, cancel, mail, vendor) | exact |
| SU13 | 200 units → 150 → gone, `VERY_LONG` at first sight, 10 h old | sold 50 + 150, expired 0, the ending counted once |
| SU14 | BZ40's edges: the cheaper auction is new, was bought from, or has an equal price | all **sold**, never a cancel |
| SU15 | 10k fills including the measured 0.2% that are not cheapest-first | false-cancel rate ≤ 0.5%, reported |

### Tier 2a: inference against truth (`SM`)

| ID | Situation | Pass |
|---|---|---|
| SM1 | Steady commodity market | sold/day ±5%, expired share ±3 points |
| SM2 | Undercut war (bots cancel and repost 1c lower hourly), new listings planted at known undercut positions 0.90/0.98/1.00/1.10 | sold/day ±10%; the cancel share recovered; `u(p)` ±0.05 |
| SM3 | Gear cancels at 20% while commodities cancel at 10.8% | the known overstatement of gear sell-through, stated as a number for M33b |
| SM4 | True relist rates 0%, 15%, 40% | the excess-rate estimator recovers each |
| SM5 | Tier sell-through with a known P per tier; tier edges and blend cap as parameters | the blend moves from the model to the evidence as n grows |
| SM6 | A maintenance gap Tue 08–12 and a gap over Thursday's peak | sold/day ±15% on both days with `sales_how` weighting; a flat share shown to miss |
| SM7 | Thin, new and disappearing items | thin-data rules hold; "needs N more days" |
| SM8 | Rebuild against incremental, with a refused snapshot, a duplicated record and a skipped daily run | byte-identical document (FM72) |
| SM9 | The buyer model 25 points optimistic from week 3, then corrected | K8 warns within 2 weeks and clears within 2 weeks; Brier per week |
| SM10 | A gear item listed at 15 g for 30 h while its sales are 2 g posts that sell within the hour | the close against the sold price measured (fails by design today: closes are asks) |
| SM11 | SM10's closes, but the sales really are at 15 g | post price within 5% of the true sold price (keeps the sold-price fix honest) |
| SM12 | Raid nights planted, Tue/Thu 19–22 at 3× demand, over 4 weeks | windows found by week 3, peak ±1 h; the trust gate holds in week 1 |
| SM13 | SM12, then the peak moves to Saturday from week 5 | the top window's rank flips by week 7 (the 2-week half-life) |
| SM14 | A churning seller: 1,000 units nightly at 2×, all expire; real sales 20 a day | sold/day 20 ±10%; expired share ≈ 0.98; turnover never counted as sales |
| SM15 | A thin item with true P 0.9 in a class at 0.3 | blended ≈ 0.39, marked "class est." (fails by design: borrowing drags a good thin item down; the error is reported) |
| SM16 | Five planted regimes over 45 days with noise | the right label from day 30; a flat series mislabelled in ≤ 10% of seeds |
| SM17 | We sell 10 a day of an item that sells 1 a day | our share of sold/day reported (fails by design: the API names no seller) |

### Tier 2b: decision quality (`SD`, the report)

| Group | Scenarios |
|---|---|
| Trends | SD1 datamine rise with rising sales (no buy on the first, suspect snapshot); SD2 the same price path from a manipulator with flat sales; SD3 a one-day hype; SD4 a rise then a demand collapse; SD32 launch deflation −2%/day for 30 days (fails by design: over-paying measured); SD33 +2%/day (money left on the table) |
| Falling demand, exits | SD5 a sudden lasting drop; SD6 demand to zero (the "not selling" streak, then vendor or disenchant at the floor); SD7 a quiet day; SD8 demand moves from A to B; SD27 our post keeps expiring while cheapest (−5%, half quantity per streak, never below the floor; against the naive relister); SD28 a held item's lasting dip; SD29 the same dip for one hour |
| Steady deals | SD9 a monster drop among about 50 items; SD10 a craft (one commodity, one crafted gear); SD11 decoys (margin without sales, sales without margin, a margin from one outlier ask, a stale mat price); SD12 a mat crashes and a craft margin opens; SD13 the product drops 30% mid-chain; SD14 the weekly buy-Tuesday, sell-raid-night flip; SD23 we move both sides of a craft; SD24 five greens disenchanting into one dust; SD31 a brand-new item with a 300% apparent margin and no sales (nothing funded on any surface, EX11) |
| Our impact | SD15 we are a large share of supply (`max_supply_share` holds); SD16 bots undercut every post (the floor and the `curOthers − 1` cap; against the naive trader); SD17 our buy moves a thin floor; SD18 bot farmers crowd our route |
| Capital, timing, our mistakes | SD19 little gold, a steady deal against a slow high-margin item; SD20 a deposit trap; SD21 plan churn in a noisy steady market; SD22 we miss 30% of chain steps; SD26 8 g of sales left in the mailbox for 12 h; SD30 a controlled missed peak (Wednesday −5%, Thursday −18%); SD34 a mat halves at 10:00, baselines refresh at 09:00 next day |

Pairs that keep a feature honest: SD1/SD2, SM10/SM11, SD28/SD29, SD32/SD33, SM17/SD15, SU3/SU14, SD30/SD22.

### Tier 3: stress (`SX`, by hand)

| ID | Check |
|---|---|
| SX1 | A full-size house (85k auctions a snapshot, 12k items) over 14 and 30 days: daily and hourly runs' wall time and peak memory locally (projected about 18 min for 14 days from M33a's 72 s per 22 snapshots) |
| SX2 | The market file within 3 MB at 12k items |
| SX3 | A 90-day run: retention and pruning |
| SX4 | Deep: 50k auctions of one item, 10k prices, prices near 10¹⁰ copper (Lua 5.1 numbers), every auction `SHORT`. SX4b, wide: 50k thin random-suffix keys |
| SX5 | Dropped, duplicated and late S3 events in random order: the same final document |
| SX6 | The paper-trading loop at full size, within the full refresh's budget |
| SX7 | A seed sweep of SD1, SD5, SD9, SD10 over 50 seeds: the sign of (our profit − the baseline) the same in ≥ 90% of seeds, the oracle share's spread across seeds ≤ 0.2 |
| SX8 | Real-shaped refresh timing: two snapshots in one clock hour, a 3 h 10 min stall, an endpoint mismatch (BZ9) |
| SX9 | A schema bump mid-run with an older desktop install: both pointers served, no hourly outage |
| SX10 | A daylight-saving change on the Monday week rollover: 168 distinct hours, the missed-peak anchor reset once |

## 4. Milestones

- **MS0, spec merge.** SPEC §8.10 (simulated markets: what the tiers are, when a tolerance is allowed, that decision scores are a report), the contradiction fixes of §2, SCENARIOS' simulator section with every row of §3, the IDs in `tests/pending_scenarios.txt`, ARCHITECTURE's test-tooling note. Opus: meaning-changing documentation.
- **MS1, simulator core.** `tools/sim/market.py`, `emulator.py`, `truth.py`, `runner.py`; SU1–SU9, SU13–SU15, SM1, SM8. Opus: the model and the cloud job's in-process seam. Sonnet: the SU tests with the given numbers.
- **MS2, the M33b questions.** SM2–SM5, SM10, SM11 and `tools/sim/fit.py` against the collected raw; the inference report. Opus: the fitting and the triage of every gap. Due before 2026-11-21.
- **MS3, the paper-trading harness.** `tools/sim/trader.py` (characters, the `savedvars_builder` extension, the real import), `scorers.py` (benchmarks, ledger), `tools/sim_report.py`; SU10–SU12. Opus: the loop and the ledger invariant.
- **MS4, the first decision report.** SD31, SD5/SD7, SD9–SD11, SD28/SD29, SD32/SD33, SM17/SD15. Opus: the scenarios' worlds and reading the report. Its findings go to §1 and, where they are engine bugs, through CLAUDE.md's bug rule.
- **MS5, momentum and regimes.** SD1–SD4, SM16. Opus. The report is M34's and M34b's design input.
- **MS6, the rest.** SM6, SM7, SM9, SM12–SM15, the remaining SD rows, SX1–SX10. Opus for the worlds; Sonnet for SX tooling where it only measures.

Features these results drive, each its own milestone with its spec merge (the user's approval): the recent-activity multiplier (SD5, SD7), the sold-price fix (SM10, SM11), M34b (SD1–SD4).

## 5. Open items

- **2026-11-21, on real data**: the steady-deal thresholds (100 a day, 10 a day, 15%), the reaction targets of §1.11, and M33b and M34's constants as planned.
- **The user, in game**: the deposit formula (§1.16); the gathering capture (SPEC §6 item 18), which the learned catalog's LC3 needs.
