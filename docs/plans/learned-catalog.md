# Plan: the learned gathering catalog

Written for: the Opus session that runs this batch in Claude Code on this repo. The user's decisions were taken in conversation on 2026-11-08. It runs in parallel with `docs/plans/market-simulator.md`; the two meet only in SD35–SD38, which run in the simulator once both have landed. Nothing in §1 is open for redesign; §2 lists what the mockup still has to settle.

**Why.** The dashboard's Gathering page ranks only the 150 items of `ahsync/data/gathering_catalog.json`, a hand-made Classic-era list. SPEC §3.4E E.1 says "an item the catalog doesn't know is never a row, even if you looted it"; such loot shows only as Status's "gathered but not placed" (GA34). Forever adds and changes items, so the list misses exactly what is new. The addon already captures every farming loot window (M17, `Capture/Gather.lua`) and AHSync already groups it into sessions and rates (E.4, E.5): the data is there, the list just never grows from it.

## 1. What is decided (do not re-litigate)

1. **The Gathering page lists what you farm, not only the Classic-era catalog.** An item you have looted becomes a row, marked **learned**, beside the curated ones. This reverses E.1's "unknown sources are never farmable" and §9's "no drop rates, spawn maps or locations" for learned items (the user, 2026-11-08). The curated file stays as it is, with its skills and notes.
2. **When an item earns a row** (the user's rule A, 2026-11-08): only loot inside **confirmed farming sessions** counts (E.4: a run with no 15-minute pause and no logout that reaches 20 units in total); an unconfirmed candidate's loot never counts. Within that, the item's units **add up across every confirmed session**, with no time limit: it earns a row once its total reaches **20 units**, or once it has appeared in **2 confirmed sessions** (so a rare drop qualifies). Example: Linen Cloth 7 + 9 + 6 across three sessions makes 22, a row after the third.
3. **Once earned, a row stays.** The list is what you *could* farm; the rate shows its sessions and their age ("3 sessions, last 24 days ago"), as the rate card does, so a stale rate reads as stale.
4. **A learned row's data comes from what is built**: its category from the gather events' sources (E.3: mining, herbalism, skinning, fishing, monster), its rate from your sessions (E.5), its price and chance from the market's intelligence (E.6). No new engine formula for gold per hour.
5. **Zone and monster are captured.** Each gather event gains the zone and, for corpse loot, the creature, so a row can say where you farmed it ("Duskwood · Shadowcat · 42/h"). An addon data change: `wow-adapter` is consulted before and reviews after; every new client call goes through `Core/Adapter.lua` and is marked `-- UNVERIFIED:` until seen in game. Events captured before this change have neither field and read as "zone unknown".
6. **Dashboard only.** No in-game Farming tab in this batch.
7. **SD35 joins this batch** (from the simulator plan): farmed stock (cost 0) of an item also worth flipping claims a window's capacity before the flip does (SPEC §3.4 generation order, PL4); the simulator reports what that order costs against an oracle. It informs, it changes nothing by itself.

Decisions made during execution are appended here.

## 2. To settle at the mockup (the user approves)

- **Skill and eligibility of a learned row.** Proposal: the Skill cell shows the lowest skill at which any character looted it as an upper bound ("≤ 140", hover "you had Skinning 140 when you looted it"); the row is eligible for a character with that profession at or above it; a monster-only row is eligible for everyone, as curated monster rows are.
- **Items that can't be sold.** Proposal: a learned item the market has never listed (a soulbound quest drop) is left out; poor-quality vendor trash goes in a collapsed "vendor only" group rather than being hidden, since grinding grey items can still pay.
- **Where the zone shows**: a column, the detail, or both; how several zones of one item are ordered (by units per hour, ties by name).
- **How "learned" reads** next to "(observed)" (GA33), which stays for curated items.

## 3. Working rules

CLAUDE.md's. One branch per milestone, one commit each, verified with `tools/verify.ps1`; the dashboard change gets a headless render of the real dashboard (no console errors, no `undefined` or `NaN`) and follows `docs/mockups/dashboard.html`. The addon change runs the Lua runner and `/ahp perf` budget rules (no per-frame work; the capture stays one event per loot window).

## 4. Milestones

- **LC0, mockup.** The Gathering section of `docs/mockups/dashboard.html` with learned rows, the zone, the vendor-only group, the skill cell. Opus. The user approves.
- **LC1, spec merge.** SPEC §3.4E (E.1's rule replaced for learned items, E.3's new fields, E.7's detail), §2.2.1 `gather` fields, §9; SCENARIOS rows (GA35+, A96+, D106+, an FM row for an event without the new fields) and their pending IDs; ARCHITECTURE if a table changes. Opus: meaning-changing documentation.
- **LC2, the learned catalog.** `engine/gathering.py` (learned rows from the confirmed sessions' totals, rule §1.2; pure), `state/gathering.py` (the per-item totals over stored sessions), `recommend/gathering_page.py`, `engine/gathering_view.py`; GA34's Status line shrinks to what is still unplaced. Opus: an eligibility and precedence rule.
- **LC3, zone and monster capture** (after the `wow-adapter` consult). `Core/Adapter.lua` (the zone and the loot source's creature), `Capture/Gather.lua`, `state/importer.py` and the stored event, `tools/savedvars_report.py` printing how many events carry them. Opus: a SavedVariables contract change; `wow-adapter` reviews the diff.
- **LC4, where to farm.** The rows' and the detail's zone and creature (`engine/gathering_view.py`, `engine/gathering_history.py`), the dashboard drawing it. Opus for the view; Sonnet for the dashboard JavaScript that only draws the payload.
- **LC5, simulator scenarios** (after the simulator's MS3): SD35 (found stock against a flip of the same item), SD36 (a Forever item not in the curated file earns a row after its second confirmed session, with the right rate and gold per hour), SD37 (the filter: grey trash, a soulbound drop, one stray loot outside a session stay off the main list), SD38 (a learned monster drop ranks above a curated item when its gold per hour really is higher). Opus.
- **Wrap-up**: the user's in-game check (deploy, a farming session, /reload, `tools/savedvars_report.py`: events with a zone and a creature).
