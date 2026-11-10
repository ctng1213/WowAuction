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
- **SP1b, the report and the simulator rows.** `tools/sold_price_report.py`, SM10/SM11 updates, SM18. Sonnet for the report (it only draws numbers from a documented format), Opus for SM18's world.
- **SP1c, deploy.** Deploying the cloud job is the user's (`cdk deploy`). The comparison week starts then.
- **SP2** after the review: its own spec merge, approval and milestones.

## 7. Risks

- **Unseen sales**: a listing bought before any snapshot saw it has no price, so the fastest sellers count least. The report shows the units left out per item; if they are a large share for busy items, SP2's "enough sales" threshold must account for it.
- **Cloud memory**: one more per-item daily row. `tools/bench.py --summary` checks the 600 MB budget.
- **Gear with suffixes**: sold prices are per item key, like everything else, so a suffix splits them. That's the same as today's asks.

## 8. Acceptance (SP1)

- BZ52–BZ54 pass in pytest; engine branch coverage stays 100%; the whole-plan snapshot stays byte-identical (nothing the plan reads changes).
- The bench stays under 600 MB.
- `tools/sold_price_report.py` runs on recorded raw and prints the comparison.
- SM10, SM11 and SM18 report their numbers.
