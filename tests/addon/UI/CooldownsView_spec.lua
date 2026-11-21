-- Specs for addon/AHProfit/UI/CooldownsView.lua: the Cooldowns tab's rows, character row, bar and footer
-- (SPEC §3.4F.8), on the six characters of tests/support/lua/cooldowns_world.lua.

local Stubs = require("wow_stubs")
local CW = require("cooldowns_world")

local AMAYA, TESSA = "Amaya-Grommash", "Tessa-Grommash"

--- The addon loaded, and its ViewModel.
---@return table
local function VM()
  Stubs.Reset()
  Stubs.Login()
  return AHProfit.ViewModel
end

--- A row by spell.
---@param rows table[]
---@param spell number
---@return table
local function Row(rows, spell)
  for _, r in ipairs(rows) do if r.spell == spell then return r end end
  error("no row " .. spell)
end

--- The rows' spells in order.
---@param rows table[]
---@return number[]
local function Spells(rows)
  local out = {}
  for i, r in ipairs(rows) do out[i] = r.spell end
  return out
end

--- The world with one character changed.
---@param name string
---@param change function  function(person) changes it in place
---@return table           AHProfitDB
local function With(name, change)
  local people = CW.People()
  for _, p in ipairs(people) do if p.name == name then change(p) end end
  return CW.DB(people)
end

describe("CooldownsView", function()
  it("CD22 Amaya's own view lists every Alchemy row by AH price, with its bar", function()
    local vm = VM()
    local rows = vm.CooldownRows(CW.DB(), AMAYA, CW.Ctx(true))
    -- by price: Arcanite 45g; Water 3g (Earth, then Undeath, by recipe name); Air 2g 80s; Earth 2g 40s (Fire, Life);
    -- Truesilver 2g 10s; Gold 1g 30s; Elemental Fire 1g 20s; Undeath 1g; then the unpriced by recipe name
    assert_eq(Spells(rows), { 17187, 17561, 17563, 17562, 17560, 17565, 11480, 11479, 25146, 17564, 17559, 17566 })
    assert_eq((vm.CooldownBar(CW.DB(), AMAYA, { live = true, offsetMs = 0 })),
              "Amaya · Alchemy 280 · Herbalism: no cooldown recipes · live")
    local arcanite = Row(rows, 17187)
    assert_eq({ arcanite.item, arcanite.sub, arcanite.skill, arcanite.cooldown, arcanite.shared, arcanite.ready,
                arcanite.price, arcanite.mats, arcanite.net, arcanite.canMake },
              { "Arcanite Bar", "Transmute: Arcanite", "275", "2 d", "shared", "ready", "45g", "29g 20s", "13g 55s", "Yes" })
  end)

  it("CD9 the group's newest stored use locks every transmute; Elemental Fire has its own 10 minutes", function()
    local vm = VM()
    local tessa = vm.CooldownRows(CW.DB(), TESSA, CW.Ctx(false))
    for _, spell in ipairs({ 17187, 17561, 17563 }) do assert_eq(Row(tessa, spell).ready, "1 d 2 h") end
    assert_eq({ Row(tessa, 25146).ready, Row(tessa, 25146).cooldown, Row(tessa, 25146).shared }, { "ready", "10 m", nil })
    local amaya = vm.CooldownRows(CW.DB(), AMAYA, CW.Ctx(true))
    for _, spell in ipairs({ 17187, 17563, 17562, 11480, 11479 }) do assert_eq(Row(amaya, spell).ready, "ready") end
  end)

  it("CD10 Not learned, below skill, Needs item and Not read, each with its hover", function()
    local vm = VM()
    local amaya = vm.CooldownRows(CW.DB(), AMAYA, CW.Ctx(true))
    local earth = Row(amaya, 17561)
    assert_eq({ earth.ready, earth.readyColor, earth.readyTip }, { "Not learned", "orange", "Recipe: Transmute: Earth to Water" })
    local fire = Row(amaya, 25146)
    assert_eq({ fire.skill, fire.skillSub, fire.skillAmber, fire.ready }, { "300", "you 280", true, "—" })
    local salt = Row(vm.CooldownRows(CW.DB(), "Mira-Grommash", CW.Ctx(false)), 19566)
    assert_eq({ salt.ready, salt.readyColor, salt.readyTip }, { "Needs item", "orange", "Salt Shaker not in Mira's bags or bank" })
    local db = With("Sorvin", function(p) p.ready = nil end)
    local gold = Row(vm.CooldownRows(db, "Sorvin-Grommash", CW.Ctx(false)), 11479)
    assert_eq({ gold.ready, gold.readyColor, gold.readyTip, gold.canMake, gold.canMakeTip },
              { "Not read", "orange", "Open Alchemy once, or use it.", "No", "cooldown not read" })
    local noStone = With("Amaya", function(p) p.bags[CW.STONE] = nil end)
    local rows = vm.CooldownRows(noStone, AMAYA, CW.Ctx(true))
    assert_eq({ Row(rows, 17187).ready, Row(rows, 17187).readyTip }, { "Needs item", "Philosopher's Stone not in Amaya's bags or bank" })
    assert_eq(Row(rows, 17561).ready, "Not learned")                                -- not known and no Stone
    local sorvin = Row(vm.CooldownRows(CW.DB(), "Sorvin-Grommash", CW.Ctx(false)), 17187)
    assert_eq({ sorvin.skill, sorvin.skillSub }, { "275", "has 230" })            -- another character's skill
  end)

  it("CD11 a tool in the bank counts as held and says so; another character's copy doesn't count", function()
    local vm = VM()
    local db = With("Brugan", function(p) p.bags[15846] = nil; p.bank = { [15846] = 1 } end)
    local salt = Row(vm.CooldownRows(db, "Brugan-Grommash", CW.Ctx(false)), 19566)
    assert_eq({ salt.ready, salt.readyTip }, { "1 d 4 h", "Salt Shaker is in the bank" })
    assert_eq(Row(vm.CooldownRows(db, "Mira-Grommash", CW.Ctx(false)), 19566).ready, "Needs item")
  end)

  it("CD13 Can Make: Yes, Yes (bank), or No with its first reason", function()
    local vm = VM()
    local amaya = vm.CooldownRows(CW.DB(), AMAYA, CW.Ctx(true))
    local function can(spell) return { Row(amaya, spell).canMake, Row(amaya, spell).canMakeTip } end
    assert_eq(can(17187), { "Yes", "shared: one of these per cooldown" })                    -- CD14
    assert_eq(can(11480), { "Yes (bank)", "shared: one of these per cooldown" })             -- Mithril Bar in her bank
    assert_eq(can(17562), { "No", "missing 1 Essence of Water" })
    assert_eq(can(11479), { "No", "missing 1 Iron Bar" })                                     -- Sorvin's Iron Bar doesn't count
    assert_eq(can(17561), { "No", "not learned" })
    assert_eq(can(25146), { "No", "needs skill 300" })
    local tessa = vm.CooldownRows(CW.DB(), TESSA, CW.Ctx(false))
    assert_eq({ Row(tessa, 17187).canMake, Row(tessa, 17187).canMakeTip }, { "No", "on cooldown 1 d 2 h" })
    assert_eq({ Row(tessa, 25146).canMake, Row(tessa, 25146).canMakeTip }, { "Yes", nil })   -- Heart of Fire in her bags
  end)

  it("CD14 the footer counts each other character's ready group once", function()
    local vm = VM()
    local db = CW.DB()
    local characters = vm.CooldownCharacters(db, AMAYA, AHProfit.Capture.Cooldowns.Catalog(), AHProfit.Capture.Cooldowns.Spells())
    assert_eq(vm.CooldownFooter(db, characters, CW.Ctx(false)),
              "Also ready now (stored): Sorvin, 1 transmute (Iron to Gold) · Tessa, 1 (Elemental Fire)")
  end)

  it("CD23 the character row: the logged-in one first, then by name; a character without a cooldown profession is left out", function()
    local vm = VM()
    local people = CW.People()
    people[#people + 1] = { name = "Lyra", skills = { Herbalism = 200, Skinning = 180 } }
    local C = AHProfit.Capture.Cooldowns
    local list = vm.CooldownCharacters(CW.DB(people), AMAYA, C.Catalog(), C.Spells())
    local names = {}
    for i, c in ipairs(list) do names[i] = c.name .. (c.you and " · you" or "") end
    assert_eq(names, { "Amaya · you", "Brugan", "Keldor", "Mira", "Sorvin", "Tessa" })
  end)

  it("CD24 another character's stored data, and no market file at all", function()
    local vm = VM()
    assert_eq((vm.CooldownBar(CW.DB(), TESSA, { live = false, offsetMs = 0 })), "Tessa · Alchemy 300 · data from Wed 09:15")
    local rows = vm.CooldownRows(CW.DB(), TESSA, CW.Ctx(false, true))
    for _, r in ipairs(rows) do assert_eq({ r.price, r.mats, r.net }, { "—", "—", "—" }) end
    local recipes = {}
    for i, r in ipairs(rows) do recipes[i] = r.recipe end
    local sorted = {}
    for i, name in ipairs(recipes) do sorted[i] = name end
    table.sort(sorted)
    assert_eq(recipes, sorted)                                                       -- by recipe name
  end)

  it("CD25 two professions in one list, each sub-line naming its profession", function()
    local vm = VM()
    local rows = vm.CooldownRows(CW.DB(), "Mira-Grommash", CW.Ctx(false))
    assert_eq(Spells(rows), { 18560, 19566 })
    assert_eq({ rows[1].sub, rows[1].price, rows[2].sub, rows[2].price },
              { "Tailoring · Mooncloth", "22g", "Leatherworking · Salt Shaker (tool)", "4g 50s" })
  end)

  it("CD26 Mats in units only, and Net / use names its export", function()
    local vm = VM()
    local arcanite = Row(vm.CooldownRows(CW.DB(), AMAYA, CW.Ctx(true)), 17187)
    assert_eq(arcanite.matsTip, { "Thorium Bar: need 1 · Amaya has 1 (bags) · buy 0",
                                  "Arcane Crystal: need 1 · Amaya has 1 (bags) · buy 0" })
    assert_eq({ arcanite.netTip, arcanite.key }, { "from the 14:32 export", 12360 })
    local fire = Row(vm.CooldownRows(CW.DB(), TESSA, CW.Ctx(false)), 25146)
    assert_eq({ fire.price, fire.priceAge, fire.sub }, { "1g 20s", "9 d old", "Transmute: Elemental Fire · makes 3" })
    assert_eq(Row(vm.CooldownRows(CW.DB(), AMAYA, CW.Ctx(true)), 17562).netColor, "neg")
  end)

  it("CD27 a character with no cooldown profession says so and opens nobody", function()
    local vm = VM()
    local people = CW.People()
    people[#people + 1] = { name = "Lyra", skills = { Herbalism = 200, Skinning = 180, Cooking = 100 } }
    local text, none = vm.CooldownBar(CW.DB(people), "Lyra-Grommash", { live = true, offsetMs = 0 })
    assert_eq({ text, none }, { "Lyra's professions (Herbalism, Skinning) have no cooldown recipes. Click a character above.", true })
    assert_eq(vm.CooldownRows(CW.DB(people), "Lyra-Grommash", CW.Ctx(true)), {})
  end)

  it("CD29 a catalog version the export doesn't share is a status line", function()
    local vm = VM()
    assert_eq(vm.CooldownVersionLine(2, 1), "cooldown catalog v2 in the addon, v1 in AHSync's export: update AHSync")
    assert_eq(vm.CooldownVersionLine(1, 2), "cooldown catalog v1 in the addon, v2 in AHSync's export: update AHProfit")
    assert_eq({ vm.CooldownVersionLine(1, 1), vm.CooldownVersionLine(1, nil) }, { nil, nil })
  end)

  it("durations read as the tab shows them", function()
    local vm = VM()
    assert_eq({ vm.CooldownSpan(2 * CW.DAY), vm.CooldownSpan(26 * CW.HOUR), vm.CooldownSpan(370 * 60000),
                vm.CooldownSpan(600000), vm.CooldownSpan(1) }, { "2 d", "1 d 2 h", "6 h 10 m", "10 m", "1 m" })
  end)
end)
