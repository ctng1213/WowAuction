-- Specs for addon/AHProfit/UI/CooldownsTab.lua: the Cooldowns tab in the real window (SPEC §3.4F.8): the character
-- buttons, the bar, the rows' sub-lines and the per-cell hovers, on tests/support/lua/cooldowns_world.lua.

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local CW = require("cooldowns_world")

--- Log in as Amaya with the world (and optionally more characters), and open the Cooldowns tab.
---@param people table[]|nil  CW.People()-shaped
---@param noMarket boolean|nil No market file
---@return table              The window's frames
local function Open(people, noMarket)
  Stubs.Reset()
  _G.AHProfitMarket = nil
  if not noMarket then CW.InstallMarket(UIWorld, CW.NOW) end
  UIWorld.Plan({})
  Stubs.Login()
  CW.Load(Stubs, people)
  Stubs.Slash("/ahp")
  AHProfit.MainWindow.ShowTab("cooldowns")
  return AHProfit.MainWindow.Frames()
end

--- The shown rows' item names.
---@param ui table
---@return string[]
local function Items(ui)
  local out = {}
  for _, row in ipairs(ui.rows) do
    if row:IsShown() and row.cdLine then out[#out + 1] = row.cells.cooldowns.item:GetText() end
  end
  return out
end

describe("CooldownsTab", function()
  it("CD22 the tab opens on you: the character row, the bar, rows by price with their sub-lines, and the footer", function()
    local ui = Open()
    assert_eq({ ui.cd.buttons[1]:GetText(), ui.cd.buttons[6]:GetText(), ui.cd.buttons[7]:IsShown() },
              { "Amaya · you", "Tessa", false })
    assert_eq(ui.cd.bar:GetText(), "Amaya · Alchemy 280 · Herbalism: no cooldown recipes · live")
    local first = ui.rows[1]
    assert_eq({ first.cells.cooldowns.item:GetText(), first.cd.subs.item:GetText(), first.cd.subs.cooldown:GetText(),
                first.cells.cooldowns.ready:GetText() }, { "Arcanite Bar", "Transmute: Arcanite", "shared", "ready" })
    assert_eq(Items(ui)[2], "Essence of Water")
    assert_eq(ui.footer:GetText():find("Also ready now (stored): Sorvin, 1 transmute (Iron to Gold)", 1, true) ~= nil, true)
  end)

  it("CD23 more than 6 characters wrap to a second line of buttons, and the list stays above the footer", function()
    local people = CW.People()
    people[#people + 1] = { name = "Zed", skills = { Alchemy = 250 } }
    people[#people + 1] = { name = "Yara", skills = { Tailoring = 260 } }
    local ui = Open(people)
    ui.frame:SetSize(560, 360)
    AHProfit.MainWindow.Render()
    local all = require("layout").Resolve(UIParent)
    local list, foot = all[ui.list], all[ui.footer]
    assert_eq(list.y + list.h <= foot.y or foot.y + foot.h <= list.y, true, "list and footer don't overlap")
    local rects = require("layout").Resolve(UIParent)
    local first, seventh = rects[ui.cd.buttons[1]], rects[ui.cd.buttons[7]]
    assert_eq({ ui.cd.buttons[7]:GetText(), ui.cd.buttons[8]:GetText(), seventh.x == first.x, seventh.y ~= first.y },
              { "Yara", "Zed", true, true })
  end)

  it("CD24 clicking another character shows their stored data; reopening starts on you again", function()
    local ui = Open()
    ui.cd.buttons[6]:Click()                                                          -- Tessa
    assert_eq(ui.cd.bar:GetText(), "Tessa · Alchemy 300 · data from Wed 09:15")
    assert_eq({ ui.rows[1].cells.cooldowns.item:GetText(), ui.rows[1].cells.cooldowns.ready:GetText() },
              { "Arcanite Bar", "1 d 2 h" })
    assert_eq(ui.footer:GetText():find("Also ready now", 1, true), nil)              -- the logged-in view only
    Stubs.Slash("/ahp")
    Stubs.Slash("/ahp")
    assert_eq(ui.cd.bar:GetText():sub(1, 5), "Amaya")
  end)

  it("CD24 with no market file the rows still show, every money column reads —, and the footer says to run AHSync", function()
    local ui = Open(nil, true)
    local cells = ui.rows[1].cells.cooldowns
    assert_eq({ cells.price:GetText(), cells.mats:GetText(), cells.net:GetText() }, { "—", "—", "—" })
    assert_eq(ui.footer:GetText(), "No market data yet. Run AHSync, then /reload.")    -- the window's own line
  end)

  it("CD26 hovering the price shows the market tooltip; Mats and Net / use show their own text", function()
    local ui = Open()
    local row = ui.rows[1]
    row.cd.hits.skill:GetScript("OnEnter")(row.cd.hits.skill)                      -- nothing of its own: the item
    assert_eq(Stubs.tooltip.hyperlink, "item:12360")
    row.cd.hits.skill:GetScript("OnLeave")(row.cd.hits.skill)
    row.cd.hits.price:GetScript("OnEnter")(row.cd.hits.price)
    assert_eq({ Stubs.tooltip.shown, Stubs.tooltip.hyperlink, row.hover:IsShown() }, { true, "item:12360", true })
    row.cd.hits.price:GetScript("OnLeave")(row.cd.hits.price)
    assert_eq({ Stubs.tooltip.shown, row.hover:IsShown() }, { false, false })
    row.cd.hits.mats:GetScript("OnEnter")(row.cd.hits.mats)
    assert_eq({ Stubs.tooltip.lines[1], Stubs.tooltip.lines[2] },
              { "Arcanite Bar", "Thorium Bar: need 1 · Amaya has 1 (bags) · buy 0" })
    row.cd.hits.mats:GetScript("OnLeave")(row.cd.hits.mats)
    row.cd.hits.net:GetScript("OnEnter")(row.cd.hits.net)
    assert_eq(Stubs.tooltip.lines[2], "from the " .. select(2, AHProfit.ViewModel.Clock(CW.NOW, AHProfit.Adapter.LocalOffsetMs(CW.NOW)))
                                      .. " export")
  end)

  it("CD27 a character with no cooldown profession lists the others and opens nobody", function()
    local people = CW.People()
    people[1].skills = { Herbalism = 290, Skinning = 200 }                           -- Amaya: no cooldown profession
    local ui = Open(people)
    assert_eq({ ui.cd.buttons[1]:GetText(), ui.cd.bar:GetText(), ui.empty:GetText(), ui.rows[1]:IsShown() },
              { "Brugan", "Amaya's professions (Herbalism, Skinning) have no cooldown recipes. Click a character above.", "",
                false })                                                          -- said once, in the bar
  end)

  it("CD29 the addon's catalog version against AHSync's export is a footer line", function()
    Stubs.Reset()
    CW.InstallMarket(UIWorld, CW.NOW, 2)
    UIWorld.Plan({})
    Stubs.Login()
    CW.Load(Stubs)
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("cooldowns")
    local footer = AHProfit.MainWindow.Frames().footer:GetText()
    assert_eq(footer:find("cooldown catalog v1 in the addon, v2 in AHSync's export: update AHProfit", 1, true) ~= nil, true)
  end)

  it("a cast of a catalog spell redraws the open tab once combat allows", function()
    local ui = Open()
    Stubs.EnterCombat()
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 17187)          -- Amaya uses Arcanite in combat
    Stubs.Advance(0.5)
    assert_eq(ui.rows[1].cells.cooldowns.ready:GetText(), "ready")                  -- no redraw in combat
    Stubs.LeaveCombat()
    Stubs.Advance(0.5)
    assert_eq(ui.rows[1].cells.cooldowns.ready:GetText(), "2 d")
  end)
end)
