# Plan: the redesigned item tooltip, flips that sell later, faster weekly patterns (M36–M38)

Written for: the Opus session that orchestrates this batch in Claude Code on this repo. Tasks tagged **Sonnet** may go
to a Sonnet subagent; tasks tagged **Opus** stay in the main session (CLAUDE.md "How work is run").

**What this batch is.** The user's first sessions on the live WoW: Forever client (2026-11-06) asked for a clearer item
tooltip, found that the addon and the plan suggest buying an auction and relisting it at once, and asked how the best
buying and selling times are worked out. Three milestones, each verified and committed on its own.

## 1. What is decided (do not re-litigate)

The user's decisions of 2026-11-06 and 07 (mockup `docs/mockups/addon.html` §3b, approved 2026-11-07):

1. **Tooltip order**: Do, Market Price, Price history, Market Inventory, Best Buy Price, Best Sell Price, Disenchant
   (gear only), Inventory. The Activity line, with its sales per day, is dropped.
2. **Market Price** is the lowest listing at the newest observation with its age ("now" at the AH), compared with the
   7-day median: "11% lower than 7d median" (red), "2% higher than 7d median" (green), "at 7d median" at 0%;
   `(price − med7) ÷ med7`, whole percent half up; no comparison without a 7-day median.
3. **Price history** is a title line with the shift word on its right and the medians on the line below, oldest
   first: "(30d) 55c · (14d) 52c · (7d) 45c". **rising** when med7 ≥ 1.10 × med30, **falling** when med7 ≤ 0.90 ×
   med30, else **steady**; a missing median reads "—" and then no word is shown.
4. **A 14-day median** is computed by the analysis (`prices.window_median(…, 14)`) and exported: the intelligence
   document gains `med14` (schema 11; the cloud publishes schemas 10 and 11 during the transition, so installs on
   v0.1.1 keep reading 10) and the market file's item string gains it as a new last field.
5. **Market Inventory**: units listed now ("433 listed"; a newer in-game scan's count replaces the file's).
   **Sales Rate** (added by the user 2026-11-07, mockup §3b, before v0.1.3): "55 sold/day · 7.9 days of supply", right
   after Market Inventory; days of supply = listed ÷ sold per day, one decimal under 10 days.
6. **Best Buy / Best Sell Price** are shown from the first data: the window's expected price (`slot_price`, as
   today), its day and time of day in words, and its strength. **early** (under 3 weeks at that hour of the week) is
   orange; **pattern** (3–5) and **established** (6+) plain. Times of day, local: Morning 06–12, Afternoon 12–18,
   Evening 18–23, Night 23–06; a night after midnight is named by the evening before (Tue 02:00 is "Monday Night"); a
   window is named by its best hour.
7. **Disenchant** (only when one of your characters can disenchant it): a title line with "N% lower/higher than AH
   price" on its right (red lower, green higher; none without a current price); then one line per possible mat:
   "80% · 1.5 Strange Dust @ 25c" with its share on the right, chance × average quantity × the mat's latest price;
   the average quantity is the middle of the range (1–2 is 1.5) and shows a decimal only when it has one; a mat
   without a price reads "no price" in amber and adds nothing; then an unlabelled line with the total. Latest prices
   are before the AH cut (the plan's DISENCHANT decision keeps using expected sale values).
8. **Inventory**: what you hold across your characters and where ("29 · bags 4, bank 1, mail 24"); no line when
   nothing is held.
9. **Long lines continue** on a value-only line underneath (as the Do line already does), and every value-only line is
   added with a one-space label (TT24, the live client's zero-height empty line). No line may wrap.
10. **A flip always sells at a later time** (the user, 2026-11-06): it buys now and posts in a selling window that
    starts later. A window already open counts as now and is excluded. No flip relists now: the plan's posting-now
    option for flips goes, items without a later window get no flip, and the addon's flip-now offers (A69, FN1) are
    removed. Its window-based offers (EX11, A59) stay.
11. **The weekly patterns' half-life is 2 weeks for now** (`pattern_half_life_weeks`, was 4), revisited around January
    2027 and returned to 4 once Forever's patterns settle. It is a shared analysis setting, so the cloud's document
    carries it.
12. **The best-time algorithm otherwise stays** (SPEC §3.3.4): hour-of-week ratios to the day's level, all weeks
    weighted by recency, windows by `sell_score`; fixed 7/14/30-day cut-offs were considered and rejected (one sample
    per slot per week).

**Already built before this batch, folded into M36**: the empty-label fix (TT24, on branch
`ctn-RCP-000-tooltip-empty-label`, uncommitted) and the "30d" label, which M36's layout replaces.

## 2. Working rules

CLAUDE.md applies. In particular: scenario first; the Lua tooltip view and its Python counterparts stay
formula-for-formula identical where ported (the delta rule, `slot_price`); the whole-plan snapshot moves only in M37
and M38, on purpose, with the diff reviewed; `tools/visual.py --update` for the tooltip golden (UI17) with the diff
reviewed against the mockup; the wow-adapter agent reviews any frame or Adapter change; engine-parity after M36's
export change.

## 3. Milestones

### M36: the redesigned tooltip

| # | Task | Who | Why |
|---|---|---|---|
| 1 | `analysis/market.py` computes `med14`; `intel/contract.py` schema 11 with `ADDED_IN[11] = item med14`; `ahcloud.publish` publishes (10, 11); tests (BZ37) | Opus | contract change with a transition |
| 2 | `export/market_lua.py` appends `med14` to the item string; the addon's `Market` decoder reads it (absent = nil); EX20 | Opus | cross-module contract |
| 3 | `UI/TooltipView.lua`: the new lines (TT25–TT31) and the day-part naming; `UI/Tooltip.lua` gathers med14, the mats' prices and holdings | Opus | judgement: layout rules and widths |
| 4 | Rewrite TT8–TT21's expected text and the tooltip specs to the new layout; `tools/visual.py --update` (UI17) | Opus | scenario rows whose text changes |
| 5 | SPEC §4.3 "Planned (M36)" becomes the built text; mockup §3 variants replaced by §3b | Sonnet | mechanical documentation marks |
| 6 | Deploy the cloud (`cdk diff`, the user's "deploy"), then release | Opus | outward-facing |

### M37: flips sell later

| # | Task | Who | Why |
|---|---|---|---|
| 1 | Scenario rows first: R24–R28 rewritten, R29 (no flip posts now), A69 and FN1 retired | Opus | meaning changes |
| 2 | `engine/flips.py`: no posting-now option; windows that start after now only | Opus | engine algorithm |
| 3 | Addon: remove `Live.FlipCandidates`'s flip-now rows, `Prices.FlipNow`, `FlipView`'s flip-now path; keep EX11/A59 offers | Opus | ported twin removed together |
| 4 | Whole-plan snapshot update, diff reviewed; invariants pass | Opus | byte-identity |

### M38: two-week half-life

| # | Task | Who | Why |
|---|---|---|---|
| 1 | `pattern_half_life_weeks` default 2 (config, docs/config.toml, SPEC §3.3.4); W-scenario numbers that depend on it recomputed | Opus | a formula's input moves numbers |
| 2 | Snapshot update, diff reviewed; cloud redeploy so the document carries 2 | Opus | byte-identity, outward-facing |

## 4. Decisions made during execution

- **M36: the whole-plan snapshot moves too** (not only in M37–M38): `w1.expected.json` embeds the exported
  `MarketIntelligence.lua`, whose item strings gain `med14` as their last field. Reviewed: the 12 exported items
  differ only by that field (each has a value), the checksum by the same 49 characters, nothing else.
- **M36: the market file's export version stays 4**: a trailing field is read as nil by an older file's decoder and
  ignored by an older addon (`Market.Decode` drops unknown fields), so neither side needs to refuse the other.
- **M36: TT25's first example fits on one line.** The mockup wrapped "40c (50 min) · 11% lower than 7d median" in a
  browser font; the addon's width estimate (SPEC §4.3: characters × the font size × 0.55, a coin counting two) gives
  302 px, under 320. The row now shows that, and a gold price ("2g 95s 67c (50 min) · …") for the continuation.

## 5. Progress

Branch `ctn-RCP-000-tooltip-and-flips`: the spec merge (1544b20), then M36 (the commit after it).

- **M36 built** (tasks 1–5): `med14` and schema 11 published beside 10; the market file's last field; the tooltip's new
  lines (`UI/TooltipView.lua`, `UI/Tooltip.lua`); TT8–TT21 and UI17 rewritten, TT25–TT31, EX20, BZ37 tested; the UI17
  golden and the whole-plan snapshot updated and reviewed; SPEC §4.3 and §2.4 built, mockup §3b marked built.
  Verified: Lua 484, visual 23, Python 1887, snapshot matches.
- **M36 task 6 open**: `cdk diff` shown to the user, deploy on their "deploy", then release v0.1.3 (v0.1.2 is out).
- **Next**: M37 (flips sell later), then M38 (two-week half-life).
