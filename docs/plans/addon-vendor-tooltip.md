# Plan: the addon after v0.1.3's in-game test (M40, M32c part 2)

Written for: the Opus session that runs this batch in Claude Code on this repo. The user's decisions were taken in conversation on 2026-11-07, after testing v0.1.3 in game; the spec merge (SPEC §4.1 market file, §4.3 tooltip and Flip, SCENARIOS TT30, TT32, A28, A59, EX14, EX21) and the updated mockup are in the same commit as this plan. Nothing in §1 is open for redesign.

## 1. What is decided (do not re-litigate)

1. **Sales Rate** (TT32): "55 sold/day"; "14246 sold/day · 2 days of data" (the days part muted) while the item has sales data on fewer than 7 of the last 7 days; "— sold/day" when unknown. "Days of supply" goes (the user: not actionable). The days are `sum(days_by_source)` of the 7-day volume estimate, a day counting when any source covered part of it.
2. **The market file gains `salesDays`** (EX21), the item string's last field, so `EXPORT_VERSION` becomes 5 (EX14). The app installs its bundled addon at every start, so the two move together; an older file decodes it as nil, and Sales Rate then shows no mark.
3. **Inventory** (TT30): "Total: 29 · Bags: 4 · Bank: 1 · Mail: 24", places in the order Bags, Bank, Mail, AH, a place with none left out; "Total: 3 · Bags: 3" when all in one place; no line when none held.
4. **Flip rows** (A28, A59): Sell 46 → 64 px and Profit 64 → 76 px, from the Item column, so values show whole. The note says how far the When time is: the own idea's note becomes "addon's own idea · buy at or below 44c · sell Sat 18:00, in 2 days" ("one stack" stays in the footer); a plan flip's note gains "· in 2 days" after its post time. Rule (`ViewModel.InTime`): whole days floored ("in 1 day", "in 6 days"); under 24 h "in N h" floored; under 1 h "within the hour".
5. **M32c part 2** (A92, BZ25) as designed in `docs/plans/blizzard-cloud.md` §1 (the M32b split): `itemNames` rows "quality,name,sellPrice,classId", "" for a value the game did not give, 0 kept; one shared parser reading from the right; the importer fills local `items` (the summary's catalog still wins where both have a row, §1 2026-11-07); the item detail says "vendor price from the game" or "vendor price unknown". Learned in game 2026-11-07: the tooltip's "Sell Price" is for the hovered stack, so the unit price comes only from `C_Item.GetItemInfo`'s 11th return, which `Adapter.ItemInfo` already reads on the live client.
6. **Price history stays as built.** Market Inventory against its 24 h average is a later idea, not this batch.

Decisions made during execution are appended here.

7. **Decided by the user 2026-11-07, during M40c: Flip cells drop copper from 1g** (UI25). At the approved 64 and 76 px, "15g 12s 1c" and "+6g 55s 1c" still cut off (about 6.6 px a character at the window font, a coin counting two, measured from the user's screenshot), so Buy, Sell and Profit round to the silver from 1g; under 1g they keep their copper. The user's concern, buy and sell reading alike, cannot arise: the 20% flip margin keeps them at least 26s apart at 1g and up. Buy was included: it has the same problem.

## 2. Working rules

CLAUDE.md's. One branch (`ctn-RCP-000-addon-vendor-tooltip`), one commit per milestone, each verified with the full suite, the Lua runner, the snapshot and `tools/visual.py`; one PR at the end. A UI change updates the goldens in its commit, the diff reviewed against `docs/mockups/addon.html`.

## 3. Milestones

- **M40a, spec merge** (this commit). Opus: judgement on wording and numbers.
- **M40b, tooltip** (EX21, EX14, TT30, TT32): `export/market_lua.py` adds `salesDays`, `EXPORT_VERSION` 5 on both sides; `Core/Market.lua` decodes it; `UI/TooltipView.lua` draws Sales Rate and Inventory. Opus: a contract change on both sides.
- **M40c, Flip rows** (A28, A59): `MainWindow.COLUMNS.flip` widths, `ViewModel.InTime`, the two notes, goldens. Opus, small.
- **M32c part 2** (A92, BZ25): the Adapter function, the capture and its fill on `GET_ITEM_INFO_RECEIVED`, the shared parser and its three readers, the importer, the item detail line. Opus: an addon contract change; `wow-adapter` reviews the diff.
- **Wrap-up**: the PR, then the user's in-game check (deploy, /reload, `tools/savedvars_report.py`).
