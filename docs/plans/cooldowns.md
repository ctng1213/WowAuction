# Cooldowns tab: plan (draft 3, 2026-11-20)

Status: **approved by the user 2026-11-20, with standing approval for CD0–CD4. CD0 (the spec merge) done.** Draft 3 folds in the independent (Fable) review, which recommended approval with changes, and the user's answers to it. The mockup is [docs/mockups/cooldowns.html](../mockups/cooldowns.html). After approval comes the spec merge (CD0): SPEC §3.4F, §4.3, §2.2.1, §3.5 and the CD scenarios. After that the milestones run one at a time.

## 0. The objective

This adds one research view for items made by profession **cooldowns** (transmutes, Mooncloth, Refined Deeprock Salt). For each item it shows what it is, the skill it needs, **how long the cooldown is**, what it sells for, what one use nets, whether each character is ready, and whether they can make it now.

- **Addon tab "Cooldowns"**: starts on the logged-in character and can switch to any other account character with a relevant profession. It lists *every* catalog recipe of that character's professions (learned or not, any skill), with Skill, Cooldown, Ready, AH price, Mats, Net / use and Can Make. Sorted by AH unit price, highest first.
- **Dashboard page "Cooldowns"**: every profession. Filters are Profession, Just my characters and My skill level only. It adds Per week, Sold / day and Who.

### Acceptance criteria

1. Every row shows the **cooldown duration** ("2 d", "3 d", "20 h") in its own column, in the addon and on the dashboard, and the column sorts.
2. The rows come only from the catalog, or from a recipe the game reported with a cooldown ("seen in game"). They never come from AH data.
3. Addon: all recipes of the character's professions are listed.
   - A skill below the requirement is amber with "you N" (or "has N" for another character), and Ready is "—".
   - Skill met but the recipe missing: Ready reads **Not learned** in orange. Skill met but the tool missing: **Needs item** in orange.
   - A ready time never captured reads **Not read** in orange (D9).
   - The dashboard's Who uses the same words: "<name> not learned", "<name> needs item", "<name> not read".
   - Hovering any of these names what's missing.
   - The default sort is AH unit price descending, ties by recipe name then spell id, and "—" prices go last.
4. **Shared cooldowns** (D10): recipes in one `group` share one ready time per character. Ready, Can Make and Who read the group's time. Using any recipe of the group locks all of them, for the period of the recipe used. The Cooldown cell says "shared". Counts ("Also ready now", "N Ready of M") count one use per group, not one per recipe.
5. Addon, other characters: **no recalculation** (D1). Their skill, known recipes, ready times, bags and bank are what the addon stored for them, and the money columns are what AHSync exported. Turning a stored ready time into "1 d 4 h" or "ready" against the clock is formatting. It runs when the tab is shown and on capture events, never in `OnUpdate`.
6. Addon: hovering the AH price (or the item name) shows the normal AHProfit market tooltip for the product (D2). Hovering Mats lists each mat in **units** (needed, held by this character in bags and bank, to buy). It shows no copper, so Lua multiplies nothing.
7. Can Make (D8): **Yes**, **Yes (bank)** when a needed mat or the tool is only in the bank (D11), or **No** with the first reason on hover.
8. Dashboard: the three filters behave as the mockup's table says. "My skill level only" implies "Just my characters". An empty result explains which filter emptied it. The view is built **at request time** with `now_ms` (from the staged rows and the stored ready times), so countdowns are current at every page load (review 5).
9. Net per use = yield × price after the AH cut − deposit − mats, using `engine/fees`. Per week = net × 7 ÷ cooldown days, **per character, never summed** (D4). For grouped recipes, per week assumes the group's every use goes to that recipe, so grouped rows are alternatives, not a total. Both are made in `engine/` and tested, and the addon shows the exported values.
10. A price older than 7 days (a shortlisted item with no recent listing) shows muted with its age ("45g · 9 d old") and still sorts by its value. "—" means no price was ever seen (review 9).
11. Status reports how many catalog rows loaded or were skipped, recipes seen in game that are not in the catalog, catalog items the market doesn't know, and a catalog version that differs between the addon and AHSync (review 7).

## 1. Decisions

- **Scope.** New tab in both the addon and the app. The addon is scoped to the character's professions but lists every cooldown recipe of them. The app covers all professions and has the three filters (user, 2026-11-20).
- **Sort.** AH unit price descending (user).
- **Cooldown duration** shown as its own column (user, 2026-11-20).
- **D1, addon characters.** Start on the logged-in character, with a row of buttons for the other account characters who have a profession with cooldown recipes.
  - For those characters the addon does **no recalculation**: it shows the stored SavedVariables data (professions, known recipes, ready times, bags and bank) and AHSync's exported numbers.
  - The logged-in character's data are the live captures, also only looked up.
  - Consequence: the addon computes no money at all. Net / use, Mats and Per week come from AHSync's export. Without AHSync they show "—", so no Lua port of the formula is needed.
  - Beyond 6 characters the button row wraps to a second line. A dropdown is deferred (review cut).
- **D2, price.** The current lowest unit price (`cur`, the tooltip's price). Hovering it shows the normal AHProfit market tooltip.
- **D3, planner.** The planner fix is wanted, but **deferred to a later batch**, after the planning algorithm is reworked following the 2026-11-21 sold-price review (user, 2026-11-20: the plans are not to be trusted until then).
  - When it comes, it is two milestones (the review's split).
  - **CD5a**: the catalog fills an unknown `cooldown_s`, giving one craft per crafter. Today `Adapter.lua` always sends nil, so the planner may plan up to `max_crafts` transmutes. This is a live bug.
  - **CD5b**: ready times and groups, which are new allocator logic.
  - Neither lands in this batch. `tools/gen_world.py`'s world has no cooldown recipe, so CD5a must either add one on purpose or state that the snapshot stays identical.
- **D4, per week.** Per character, never summed.
- **D5, the list on every machine** (user, 2026-11-20).
  - We curate the list, and it ships complete **inside the addon** from its first release, as the generated `addon/AHProfit/Data/Cooldowns.lua`, committed and released with the addon. It works the way `Data/Disenchant.lua` and `Data/BonusSuffixes.lua` do today.
  - The game never builds the list. A recipe a machine sees in game that the list lacks shows for that player at once ("seen in game"), and Status asks for it to be added. We then add it to the JSON and ship it to everyone with the next release.
  - The report tool that would print such rows (`tools/cooldowns_report.py`) is deferred until the first seen-in-game row exists (review cut).
- **D6, missing recipe or tool** (user, 2026-11-20).
  - Skill met, but the recipe isn't learned: "Not learned". Skill met, but the tool (Salt Shaker) isn't held: "Needs item". On the dashboard: "<name> not learned" and "<name> needs item". All in orange. The two can't both apply, because a catalog row is either a recipe or a tool use.
  - **The tool counts as held when it is in that character's bags or bank.** Another character's copy, or one in the mail, does not count.
  - The bank is readable only while open, so its contents are as of that character's last visit. A character who never opened the bank is judged on the bags alone.
  - "<name> Ready" otherwise.
  - In Who, entries sort Ready, then time left, then not learned, needs item or not read, then below the skill, with ties by name. Beyond 3 characters, Who shows "N Ready of M", the first 3 entries and "+K more" (hover for all).
- **D7, no Have column** (user, 2026-11-20). Posting what you hold is the Portfolio's job. The bag and bank capture is read only for the tool (D6), Can Make (D8) and the Mats hover.
- **D8, Can Make** (user, 2026-11-20). A Yes/No column in both views.
  - Addon, per character: **Yes** when the character can use the cooldown right now. That means the skill is met, the recipe is learned, the tool is held, the cooldown (or its group's) is ready, and the mats are in that character's own bags or bank.
  - Otherwise **No**, and the hover gives the first reason. A ready time never read is **No** ("cooldown not read"): a wrong Yes would send you to the bank for nothing.
  - Dashboard: Yes when any of your characters can, and the hover lists who.
  - It is a lookup and comparison, so D1 holds.
  - In a group, several rows can say Yes for one character, but only one can be used. The hover says "shared: one of these per cooldown".
- **D9, how a ready time is known** (user, 2026-11-20, from review 3 and 4). There are three sources, newest first, and the game's report wins over the addon's own record:
  1. **The game reports it.** For recipes: `C_TradeSkillUI.GetRecipeCooldown(spell)` when a profession window opens or updates. For tools: `C_Container.GetItemCooldown(tool)` at login, on bag updates and on `BAG_UPDATE_COOLDOWN`, which needs no profession window. Whether a banked tool's cooldown is readable is checked in game in CD2. Both calls are `-- UNVERIFIED:` (SPEC §6 item 7).
  2. **The addon saw the use.** On `UNIT_SPELLCAST_SUCCEEDED` for a catalog spell (a recipe, or the tool's Use spell), it records ready = cast time + the catalog's period, for the recipe or its group. This also clears "ready" right after a use (review 3), and it works even if Forever lacks the cooldown APIs.
  3. **Neither has happened yet**: **Not read** in orange. The hover reads "…'s cooldown hasn't been read yet. Open the profession window, carry the tool in your bags at login, or use it once." Can Make is No. It replaces "open Alchemy once", so recipes and tools share one unknown state.
  - Ready times are stored in **UTC ms** (the project's convention), not seconds.
- **D10, shared cooldowns** (user, 2026-11-20: "yes", mark the group). A catalog `group` (for example "transmute") shares one ready time per character. The tab marks it in the Cooldown cell ("2 d · shared"), and counts one ready use per group. Whether Classic or Forever transmutes actually share is confirmed while the catalog is built (CD1) and in game (CD2). The mockup shows them shared.
- **D11, bank** (user, 2026-11-20). When a needed mat or the tool is only in the bank, Can Make reads **Yes (bank)**.

## 2. How the cooldown list is derived

1. **Catalog (source of truth, curated by us, shipped complete)**: `ahsync/data/cooldown_catalog.json`.
   - Each row has `spell`, `name`, `profession`, `skill`, `cooldown_s`, optional `group`, `product`, `yield_min`/`yield_max`, `reagents`, optional `tool` (the Salt Shaker's item key), `url` and an optional `note`.
   - Inclusion rule: a profession spell that creates an item and has a non-zero recovery time (its own cooldown or its category's), plus tool items whose Use spell creates an item and has a cooldown.
   - Compiled by hand from Classic (1.12) spell pages, keeping each URL. The draft has 14 rows: Arcanite, Iron to Gold, Mithril to Truesilver, 8 elemental transmutes, Elemental Fire, Mooncloth and the Salt Shaker.
   - The `_comment` holds the version and the Forever caveat, as in `gathering_catalog.json`.
   - `tools/gen_cooldown_data.py` writes the addon's `Data/Cooldowns.lua` from it, with the same version. A test fails when the two disagree.
2. **The game tells us when each character is ready** (D9). A recipe the game reports a cooldown for that the catalog lacks becomes a "seen in game" row with unknown skill.
3. **The market only prices it.** Prices come from the intelligence. Every catalog product and mat goes on the export **shortlist**, so `MarketIntelligence.lua` carries their prices even when no listing is recent (`export/market_lua.wanted`). Such a price can be old (criterion 10).
4. **Cross-checks** go to Status: a catalog key missing from the market's items, and the addon's catalog version against the one AHSync exports. The period cross-check (`GetSpellBaseCooldown`) and the mats-against-schematic cross-check are cut from this batch (review).
5. **Skew between releases** (review 7): a spell in only one side's catalog shows in the addon with "—" money (no exported row). AHSync ignores stored ready times for a spell its catalog lacks. Status flags the version mismatch.

## 3. Working rules

- Scenario IDs `CD1…` go into SCENARIOS.md and `tests/pending_scenarios.txt` at CD0. Every new test is named after its scenario.
- `engine/cooldowns.py` is pure: catalog validation, eligibility, groups, net, per week, rows. `engine/cooldowns_view.py` handles filters, Who and sort, with a full tie-break: price desc, recipe name, spell id. Both take `now_ms`.
- The addon only looks up and compares. Its Cooldowns rows in `UI/ViewModel.lua` join the catalog, the stored captures and the exported numbers. Sorting, ready-time text, Can Make and group logic are tested there. There is no money arithmetic in Lua.
- The export adds a `CD` section to `MarketIntelligence.lua`: the catalog version, then one row per catalog spell with mats copper, net per use and per week. `EXPORT_VERSION` goes to 8 **on both sides in the same milestone (CD3)**, with a minimal Lua parser of `CD`, so `main` never holds one side bumped (review 6, FM34/FM35).
- `Data/Cooldowns.lua` is generated, never edited by hand.
- Consult `wow-adapter` before CD2 and CD4.

## 4. Milestones

| ID | Content | Who · why |
|---|---|---|
| **CD0** | Spec merge: SPEC §3.4F Cooldowns (catalog, derivation, groups, ready sources, columns, filters, states), §4.3 addon tab (D1 lookups only, D2 hover), §2.2.1 `cooldowns[charId]` SavedVariables field, §3.5 the `CD` export section, the §3.2 table, Status topics, §4.5 static data (D5). §6 item 7 notes what CD2 verifies. Scenarios CD1–CDn and pending IDs. ARCHITECTURE: catalog → engine → export and page → tab, drawn as planned | Opus · documentation that sets meaning |
| **CD1** | The catalog JSON (researched row by row, with URLs; groups confirmed). `engine/cooldowns.catalog()` validation with skipped-row Status (like GA2). `tools/gen_cooldown_data.py` → `Data/Cooldowns.lua`, the JSON-equals-Lua test, and the `.toc` entry | Opus: catalog content and validation. Sonnet: the generator · parser of a documented format |
| **CD2** | Capture (D9): Adapter reads of recipe time left and tool item cooldown, and the cast-success record. `cooldowns[charId] = "spell,readyAtMs;…"` (group-aware). Importer plus a `recipe_cooldowns` table (migration, idempotent upsert). FM scenarios: APIs missing (cast record only), never read ("Not read"), tool only in the bank. Checked in game with `tools/savedvars_report.py`, which answers whether transmutes share and whether a banked tool's cooldown reads | Opus · Adapter boundary, a new SavedVariables field, a migration |
| **CD3** | Dashboard and export: `engine/cooldowns.py` rows and `engine/cooldowns_view.py` (filters, sort, per week, Who, groups, at request time). `recommend/cooldowns_page.py`. The `CD` export section, the shortlist of catalog keys, and `EXPORT_VERSION` 8 on both sides with the minimal Lua `CD` parser. The web route and the page JS (draw only). The dashboard mockup page is updated. Headless render check | Opus: engine, view and export. Sonnet: the page JS · draws a payload |
| **CD4** | Addon tab: `MainWindow.TABS` gains Cooldowns (width check at minimum window size). ViewModel rows (lookups only). The character button row. Price hover opens the market tooltip (`UI/Tooltip.lua`'s item path). Mats hover in units. Visual goldens (`tools/visual.py --update`) reviewed against the mockup | Opus · UI logic, character data rules |
| *later* | CD5a and CD5b, the planner (D3), and `tools/cooldowns_report.py` (D5) | after the 2026-11-21 algorithm work, and once a seen-in-game row exists |

Each milestone runs in its own worktree, is verified with `tools/verify.ps1` (with `-Bench` for CD3, because the export grows), and is committed once. This batch doesn't touch the planner, so the whole-plan snapshot must stay byte-identical in every milestone.

## 5. Risks and unknowns

- **Forever's API** may lack `GetRecipeCooldown` or `GetItemCooldown`, or report them differently. The cast-success record (D9 source 2) keeps Ready working, with "Not read" until the first use.
- **Catalog accuracy**: Classic values may not match Forever (custom recipes, changed cooldowns). Mitigations: the "seen in game" rows, per-row notes, and the release path in D5.
- **Shared cooldowns**: still to be confirmed in CD1 and CD2. The `group` field and D10 cover both answers. If they don't share, the catalog simply has no group and every row keeps its own time.
- **Other characters' data ages** (D1): the tab shows when each character's data was captured ("data from Tue 18:02"). A ready time can be wrong if that character played on another machine since.
- **Thin markets**: Per week assumes every unit sells. Sold / day sits beside it, and D4 keeps it per character.
- **Addon tab width**: a seventh tab must still fit beside the Min chance box at minimum width. It is checked in CD4's goldens.
- **Export size**: 14 `CD` rows plus about 30 shortlisted items is negligible against 3 MB, but the bench confirms it.
- **The addon's own scan prices**: if the addon shows a fresher scanned price than the exported `cur`, AH price and the exported Net / use can disagree until the next AHSync export. CD4 checks which price the tooltip path uses, and says "net from the 14:32 export" on hover.

## 6. Not in this batch

The planner fix (D3: CD5a, CD5b), the seen-in-game report tool, the `GetSpellBaseCooldown` period cross-check, the mats cross-check, the character dropdown, ready reminders or alerts, automatically buying mats, automatic sharing of discoveries between machines, and per-zone or moonwell requirements beyond a catalog note.
