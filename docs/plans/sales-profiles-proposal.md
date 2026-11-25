# Sales profiles: a proposal (2026-11-25, for decision)

Status: **approved 2026-11-25 (all four decisions in §8), nothing built yet.** It follows the sold-price plan's items 42 and 43 (`sold-price.md` §1d) and the
clean-week measurements behind them. The numbers here are from one week, 2026-11-16 to 11-23 UTC, with documents rebuilt
point-in-time by today's code; scratch scripts, not in the repo.

## 1. Why

The market has at least four kinds of item, and they sell differently enough that one rule rarely fits them all. Today
each rule picks its items through its own stand-in: the price acceptance (P28) and the demand spread (P31) gate on "has
a sold curve", the level on "sold or asks", other rules on the commodity flag or the sales rate. Those stand-ins drift
apart: "has a sold curve" was meant to mean busy gear and quietly took in 1,079 commodities, which the market-sim review
of P31 only caught by running the simulator (item 43(d)). And the measurements, the simulator's worlds and the engine's
rules each draw their own groups, so their numbers don't line up.

## 2. The four profiles

| Profile | Rule (proposed) | Items (document of 11-22) | Median sold a day |
|---|---|---|---|
| **Commodity** | Blizzard's commodities list (`commodity`) | 1,379 (1,079 with a sold curve) | 795 with a curve; 1.9 without |
| **Busy gear** | not a commodity, with a sold curve (20 weighted clean units, about 14 clean sales a day) | 1,893 | 34 |
| **Thin gear** | not a commodity, no curve, a sold level, 1+ sold a day | 23,019 | 2.0 |
| **Rare gear** | every other non-commodity: an ask level, or under 1 sold a day | 22,401 (920 at 1+ a day) | 0.44 |

"Gear" here means any non-commodity, which on this market is almost all gear.

## 3. What each profile is like

| | Commodity | Busy gear | Thin gear | Rare gear |
|---|---|---|---|---|
| Buying | any number of units, cheapest first (verified) | one unit an auction, cheapest first (89% of early leavers left no cheaper unit) | cheapest first (97%) | too few to tell |
| 8 h demand: CV against a pure count noise's | 0.72 against 0.07 | 0.69 against 0.32 | 1.44 against 1.37 | — |
| Overdispersion 1/k (0 = count noise only) | 0.45–0.51 | 0.33–0.36 | about 0.2 | — |
| New auctions: under the cheapest / at it / above / alone | not measured here | 2% / 56% / 42% / 0% | 20% / 47% / 13% / 20% | 14% / 27% / 3% / 56% |
| Durations 24 h / 8 h / 2 h | (simulator: 16% / 72% / 12%) | 21% / 63% / 16% | 18% / 69% / 13% | 15% / 70% / 15% |
| Gone by the next snapshot (sold or cancelled before a second look) | not measured here | 27% | 22% | 8% |
| The engine against the clean truth (24 h posts) | **not measurable yet**: a stack bought in part stays listed | over-reads: +0.05–0.09 tied or alone, +0.05–0.07 undercutting under the level, +0.18 at it (after P31) | **under-reads** under the level: −0.13 to −0.29; the read doesn't rise as the price falls, real sales do | under-reads: 0.14 read against 0.40–0.55 sold (the expected undercutters outnumber its sales) |

Demand variability is the engine's own hourly sold units (`sales_h1`, late-band vanishes counted in part) in the
week's 21 windows of 8 h. A commodity's variance includes lumpy purchases (several units a buyer), which is real
variability for a stack's chance. Busy gear's lowest decile of windows reads near zero, which may be hours the
snapshots missed rather than quiet hours; check that before fitting anything to it.

**What this says.** Commodities' demand varies as much as busy gear's. So the demand spread may well be right for them
too; the simulator showed a cost only because its buyers are pure count noise (CV 0.16 there against 0.72 real). That
can't be decided until the commodity truth exists (§6). Thin gear is close to count noise, so its under-read is not a
spread problem: at about half a sale per 8 h, a run at the mean demand already reads *above* what count noise would
give, so the cause must be elsewhere (its sales the snapshots never see, 22% gone before a second look, and a sold rate
that misses the buyers a cheaper post would find, item 30(e)).

## 4. Rules by profile

| Rule | Commodity | Busy gear | Thin gear | Rare gear |
|---|---|---|---|---|
| P28 price acceptance (above the level) | yes, with a curve | yes | no (`a = 1`; never posted above its level) | no (same) |
| P31 demand spread | no (unmeasured) | yes | no | no |
| Level from | sold | sold | sold | asks (mostly) |
| Open error and its candidate fix | measure the truth first | the rest of the over-read at the level: bursts of undercutting | the price response under the level (`a` above 1, the user's decision) | the expected undercutters (`L × u` against a sale every two days) |

## 5. The engine change (proposed)

- **One classification.** `engine.sales_profile(item facts) -> "commodity" | "busy" | "thin" | "rare"`, pure, beside
  the level; every rule that gates by item kind reads it (P28, P31 and the next rules), so a rule's scope is one word
  in SPEC, and SCENARIOS gets one row per profile where the rule differs.
- **Published.** The document carries `sales_profile` per item (a schema bump), so the dashboard, the addon's tooltip
  and the reports can group by it and say why an item reads as it does.
- **Hysteresis.** An item near a threshold flips (an item gaining its curve jumps by up to 0.15 with P31). Options:
  (a) none, the simplest; (b) a profile changes only after the new one holds for 3 consecutive daily builds, computed
  from history so the engine stays pure; (c) a band: enter busy at 20 clean units, leave under 15.
  **Measured** on the 7 point-in-time documents (39,316 items in all of them): 13,790 profile changes in 6 days, about
  2,400 a day, mostly thin ↔ rare (sales crossing 1 a day, which no rule treats differently yet); busy ↔ thin about
  280 a day, each moving the item's chance by up to 0.15 under P31; 3,087 items changed twice or more. A 3-build hold
  leaves 5,229 changes (−62%), 434 of them busy ↔ thin (−76%, about 70 a day), at the cost of a real change showing
  up to 2 days late.
- **No new numbers by itself.** Introduced with today's gates mapped one to one (P28: commodity with a curve or busy;
  P31: busy), it changes no displayed number and the whole-plan snapshot stays byte-identical. Rules move per profile
  afterwards, each with its own evidence.

## 6. The simulator, per profile

What the simulator plants today: gear shapes "busy" (30 buyers a day) and "listing-limited" (50 buyers, 16 listings),
a commodity (120 buyers), all with buyers as a fixed-rate count process; one gear price mix (8.5% under, 58.5% at);
measured durations, commodity positions and stacks. Improvements, most valuable first:

1. **Plant overdispersed demand per profile.** A buyer-rate multiplier `G ~ Gamma(k, 1/k)` redrawn every 8 h
   (`Item.demand_noise = k`; 0 keeps today's behaviour), with k from §3: busy gear about 3 (1/k 0.33), commodity about
   2 (1/k 0.45–0.51), thin gear none. This is the one change that lets the simulator judge P31 fairly: today it can
   show the spread's cost but not its benefit beyond count noise, and it decides the commodity question in a world
   with real variance.
2. **One world shape per profile, from §3's numbers.** Busy gear with 2% under / 56% at / 42% above; thin gear with
   20% under and 20% alone; a rare-gear shape (a sale every two days, 56% alone), which nothing in SM21 has today.
   Each with its profile's durations and its share gone before a second snapshot.
3. **A calibration table per profile.** The same table computed from the real raw and from each profile's simulated
   world: CV of 8 h demand, positions, durations, gone-before-a-second-look, and the clean-truth share sold at each
   depth. A world is trusted for a profile when its table matches the real one within noise; SM21 and SM22 report per
   profile, the same groups as the real-market reports.
4. **Check the real-market truth in the simulator.** The simulator knows every true sale, so it can run
   `sales_report` §7 on its own snapshots and compare the 24 h-only share and the every-band share with the truth.
   That checks the band rule; whether real 24 h sellers price differently from 8 h ones is a separate real-data check
   (§7).
5. **Keep SD5 and SD7 exercised** (the review): a margin that survives a slightly lower chance, or "no buys" reported
   apart from "not exercised", so a lost behaviour can't pass silently.

## 7. Measurements still needed

- **The commodity truth.** For the cheapest stack of a commodity (a 24 h post), the share of its units gone within
  8 h, partial fills included (a stack's quantity falling is visible between snapshots). Then the same tables as gear's,
  and P31 for commodities decided on data.
- **The 24 h selection check.** Whether 24 h posts sit at different prices or positions from 8 h ones; if they do, the
  clean truth needs reweighting.
- **Busy gear's zero windows.** Whether the near-zero 8 h windows are quiet hours or missing snapshots.

## 8. Decisions (the user, 2026-11-25)

1. **Adopted:** the four profiles as one classification (§5), introduced with no number moving.
2. **Adopted:** hysteresis option (b), a profile changes only after the new one holds for 3 consecutive daily builds.
   It goes on P31's busy-gear gate first, since that flicker is live today.
3. **Adopted:** simulator improvements 1–3 first (overdispersed demand, one world per profile, the calibration table).
4. **Adopted:** the commodity truth (§7) is measured before any commodity rule changes.

The order: the commodity truth and the selection check (measurement only), then the simulator's
overdispersion and profile worlds with the calibration table, then the classification in the engine (no number moves),
then per-profile rules one at a time, each with its evidence.
