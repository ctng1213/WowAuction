# Plan: the Do line says what its money means (batch `do-line`)

Written for: the Opus session that runs this batch in Claude Code on this repo. The user's decisions were taken in conversation on 2026-11-13 over three rounds of `docs/mockups/do-line.html` (round 3 approved). The spec merge is SPEC §4.3 "The Do line's money" and "Coins everywhere", §2.2's `profitMode`, §3.5's `paid` field, and SCENARIOS TT45–TT50, UI29, UI30, EX22 (listed in `tests/pending_scenarios.txt`).

## 1. What is decided (do not re-litigate)

1. **Line 1 is the verb and when; line 2, indented, the quantity and price.** "Do: POST now" or "Do: POST Sat 10:00–18:00" (post time to the selling window's best hour, from `atMs` and `peakMs`); "20× at 18c", a post later adding "· +53% vs now". "for the 18:00 peak" and "then nothing" go; a chain's real next step stays on the line under.
2. **Quantities read "N×"** on POST, CRAFT and MAIL.
3. **Sells for** = qty × price × 0.95, with the chance after it, right of line 1. It is the only number on the line for stock you had (full mode).
4. **Paid** shows only when the plan's net takes something off for the units: `PlanAction.expected − net` of the action that carries the chain's net, exported per action as `paid`.
5. **Two modes, an addon setting, default full** (the user: "2 is easiest to follow, but I'd like to go back to 1 if needed"). full: Profit = Sells for − Paid, only when Paid shows. expected: the plan's net as "Expected Profit", for your own stock too. `/ahp profit [full|expected]`, stored account-wide in `AHProfitDB.profitMode` like `minimap` (UI23), read at draw time. The plan itself never changes with the mode.
6. **CRAFT, DISENCHANT, UNFUNDED** as SPEC §4.3 lists: a craft's Sells for from its chain's open posts; a bought disenchant shows Paid and Profit in both modes; an unfunded row keeps "Expected Profit" in both.
7. **Coin icons in every text slot**, applied where text is drawn; `WithCoins` matches only a number followed by g, s or c that ends the word. The gap found: tooltip left parts, one Main Window summary slot, the left texts of the window's and the minimap's hover lines.

Decisions made during execution are appended here.

8. **DL1: `paid` is an explicit `PlanAction` field, not `expected − net`.** A bought disenchant's own step and a hold carry net 0 by design (the chain's net sits on the buy), so `expected − net` would call the mats' whole value paid. Each constructor sets it where it takes the cost off; actions whose net is elsewhere carry 0.
9. **DL1 finding, acted on in DL3: a crafted product's post says "Mats", not "Paid".** Its `paid` is `expected − profit`, the mats valued as the craft plan values them, owned mats at their market value (w1: 12,133 c against a 7,440 c ore buy). That is what the craft gives up, not gold spent, so its line 2 reads "Mats 1g 21s 33c · Profit …", the CRAFT line's word.
10. **DL3: a craft's Mats is its `paid`, with no fallback.** Falling back to the gold its chain spends on buys (the old "mats 2s 48c") gave TT11's fixture "Profit +54s 52c" against a plan net of 9s 71c, since the owned bars cost nothing there. A file without `paid` shows no Mats and no profit.
11. **DL3: a right part moves under only when it does not fit** (TT31's rule, applied to line 2). The mockup drew a bought post later on three lines; by the 320 px estimate its full-mode line 2 fits, so it takes two, and its longer expected-mode text takes three.

## 2. Working rules

CLAUDE.md's. One branch (`ctn-RCP-000-do-line-wording`), one commit per milestone, each verified with `tools/verify.ps1` and `tools/visual.py`; one PR at the end. A UI change updates the goldens in its commit, the diff reviewed against `docs/mockups/do-line.html` and `docs/mockups/addon.html`. Rows TT8, TT9, TT11–TT13, TT15, TT21 and A21 describe today's Do texts: their expected texts are updated in the milestone that changes them (DL3), with the new IDs' tests.

## 3. Milestones

- **DL0, spec merge** (this commit): the mockup, SPEC, SCENARIOS, pending IDs, this plan. Opus.
- **DL1, the `paid` export** (EX22): schema 32 adds `actions.paid` and `plan_new.paid` (a migration, its test in the migrations suite); `plan_rows` stores `round_half_up(expected − net)` for every action (0 when the action carries no net); `plan_lua.action_text` writes it as the 16th field (empty for 0); `EXPORT_VERSION` 7 on both sides; `Core/Plan.lua` decodes it, an older string as 0. The snapshot moves (every action string gains a field): review that only the strings, checksums and version change. Opus: a contract change on both sides and a migration.
- **DL2, coins everywhere** (UI30): tighten `ViewModel.WithCoins` (Lua 5.1's `%f` frontier, tested); apply it in `Tooltip.Draw` to both parts, and at the remaining draw sites in `MainWindow.lua` and `MinimapButton.lua`. Sonnet-able on its own, but it touches every drawn string, so Opus reviews the diff and the goldens.
- **DL3, the Do line** (TT45–TT50, UI29): `TooltipView.DoParts`/`DoLines` produce line 1, line 2 and the follow-ups; the numbers (Sells for, Profit, the time frame) are made in `UI/ViewModel.lua` or `TooltipView` and tested there, never in frame code; `FromAction`/`PostDo` pass `paid`, `peakMs` and the chain's posts; `/ahp profit` in `MainWindow.OnSlash` next to `minimap`, its setting read through one accessor. Update the altered TT rows and A21, the UI17 golden and `docs/mockups/addon.html`'s tooltip variants. Opus: judgement on layout, widths and the altered rows.
- **Wrap-up**: SPEC's "planned" notes become "built" with module names, the pending IDs removed as their tests land, ARCHITECTURE only if a contract line names the action fields, the PR, then the user's in-game check (`tools/deploy.py --restart`, /reload, hover a post of each kind, `/ahp profit expected`).

## 4. Risks

- **Width.** "Do: POST Sat 10:00–18:00" with "Sells for 4g 27s 50c · 85%" is near 320 px at the small font; TT31's move-under rule handles the overflow, and the layout check (UI17) must pass for TT45–TT49's tooltips.
- **Paid for chains.** A disenchant's or craft's net sits on its chain's buy or post, not on the Do action itself; DL3 reads Paid from the action that carries the net and says so in a test.
