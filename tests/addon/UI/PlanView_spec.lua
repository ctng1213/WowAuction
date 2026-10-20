-- Specs for addon/AHProfit/UI/PlanView.lua: the plan's Today rows, one row per chain (SPEC §4.3, A71, A73, A74).
-- Expected values match ahsync/engine/plan_chains.py and engine/plan_views.py for the same inputs (D62-D66).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local HOUR, DAY, MINUTE = 3600000, 86400000, 60000
local T = 1760000000000                      -- Thu 9 Oct 2025 08:53:20 UTC, the stubs' clock
local NOW = T - (8 * 60 + 53) * MINUTE - 20000 + 14 * HOUR + 30 * MINUTE   -- Thu 14:30 UTC
local GOLD = 314000
local BOWL, VIAL, FISH, BAR, ROD, LINEN = 6370, 3371, 6303, 2840, 6217, 2589
local LOOKS = { [BOWL] = { name = "Fish Bowl", quality = 1, conf = 80, soldPerDay = 30, vendorSell = 138 },
                [VIAL] = { name = "Empty Vial", quality = 1 }, [FISH] = { name = "Raw Brilliant Smallfish", quality = 1 },
                [BAR] = { name = "Copper Bar", quality = 1 }, [ROD] = { name = "Copper Rod", quality = 1, conf = 70 },
                [LINEN] = { name = "Linen Cloth", quality = 1, conf = 86, soldPerDay = 48 } }

--- Boot the addon and return its view model.
---@return table
local function Boot()
  Stubs.Reset()
  Stubs.Login()
  return AHProfit.ViewModel
end

--- An item's look.
---@param key number
---@return table
local function Look(key) return LOOKS[key] end

--- A pending plan action by Amaya, changed by `more`.
---@param id string
---@param kind string
---@param net number|nil
---@param at number
---@param more table|nil
---@return table
local function Act(id, kind, net, at, more)
  local a = { id = id, kind = kind, char = "Amaya-Grommash", key = LINEN, qty = 20, price = 38, at = at, net = net,
              status = "pending" }
  for k, v in pairs(more or {}) do a[k] = v end
  return a
end

--- Amaya's Fish Bowl chain (D62): 76 vials from a vendor at 20c, 76 smallfish on the AH at 75c, 76 crafts carrying
--- +12g 4s 48c, posted Sun 11:00 for the 19:00 peak.
---@return table[]
local function FishBowl()
  return { Act("v", "buy", 0, NOW - 2 * MINUTE, { key = VIAL, qty = 76, price = 20, vendor = true }),
           Act("f", "buy", 0, NOW - MINUTE, { key = FISH, qty = 76, price = 75, parent = "v" }),
           Act("k", "craft", 120448, NOW, { key = BOWL, qty = 76, price = nil, parent = "v" }),
           Act("p", "post", 120448, NOW + 2 * DAY + 20 * HOUR + 30 * MINUTE, { key = BOWL, qty = 76, price = 2500,
               parent = "v", chance = 90, peak = NOW + 3 * DAY + 4 * HOUR + 30 * MINUTE, duration = 24 }) }
end

describe("PlanView", function()
  it("A71 Today shows a craft chain as one CRAFT row with its buys and its sale as lines", function()
    local VM = Boot()
    local today = VM.Today(FishBowl(), NOW, 0, 50, Look, GOLD)
    assert_eq(#today.rows, 1)
    local row = today.rows[1]
    assert_eq({ row.action, row.item, row.qty, row.each, row.total, row.net, row.chance, row.risk },
              { "CRAFT", "Fish Bowl", "×76", "95c", "72s 20c", "+12g 4s 48c", "90%", "2.3%" })
    assert_eq(row.notes, { "buy 76 Empty Vial from a vendor · 15s 20c", "buy 76 Raw Brilliant Smallfish on the AH · 57s",
                           "then: post Sun 11:00, for the 19:00 peak" })
    assert_eq(VM.TodayFooter(today, 50), "1 action · expected +12g 4s 48c")
    local owned = FishBowl()
    table.remove(owned, 2)
    table.remove(owned, 1)
    owned[1].parent, owned[2].parent = nil, "k"                    -- every mat owned: no buys, Each and Total "—"
    local mine = VM.Today(owned, NOW, 0, 50, Look, GOLD).rows[1]
    assert_eq({ mine.each, mine.total, mine.risk }, { "—", "—", "0.0%" })
    -- A post risks its deposit (SPEC §3.4 Risk): 76 × 138c × 15% for 24 h = 1573c of 314000c = 0.5%.
    local _, _, deposit = VM.RowMoney({ kind = "post", qty = 76, price = 2500, duration = 24 }, nil, { vendorSell = 138 })
    local _, _, unknown = VM.RowMoney({ kind = "post", qty = 1, price = 5 }, nil, nil)   -- no vendor price: 0
    assert_eq({ deposit, VM.Risk(deposit, GOLD), unknown }, { 1573, "0.5%", 0 })
  end)

  it("A73 holds and vendor sales are no rows on Today and count toward no total", function()
    local VM = Boot()
    local actions = FishBowl()
    for _, a in ipairs({ Act("h1", "hold", 0, NOW, { qty = 2, price = nil }), Act("h2", "hold", 0, NOW, { key = BAR }),
                         Act("x1", "vendor", 40, NOW, { price = 10 }), Act("x2", "vendor", 25, NOW, { key = BAR }),
                         Act("x3", "vendor", 12, NOW, { key = VIAL }), Act("w", "post", 500, NOW, { chance = 90 }) }) do
      actions[#actions + 1] = a
    end
    local today = VM.Today(actions, NOW, 0, 50, Look, GOLD)
    assert_eq({ today.count, today.total, today.rows[1].id, today.rows[2].id }, { 2, "+12g 9s 48c", "k", "w" })
  end)

  it("A74 the sender's Today shows the MAIL row; the crafter's craft row says the mail is not sent yet", function()
    Stubs.Reset()
    Stubs.world.player.name = "Brugan"
    UIWorld.Market({ [BAR] = UIWorld.Item(500, 60, 40), [ROD] = UIWorld.Item(4100, 70, 6) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "m", kind = "mail", key = BAR, qty = 6, at = T - MINUTE, net = 0, char = "Brugan-Grommash",
                     to = "Amaya-Grommash" },
                   { id = "k", kind = "craft", key = ROD, qty = 7, at = T, net = 9000, parent = "m" },
                   { id = "p", kind = "post", key = ROD, qty = 7, price = 4100, at = T + 5 * HOUR, net = 9000, parent = "m",
                     chance = 80, peak = T + 13 * HOUR } })
    Stubs.Login()
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    local cells = ui.rows[1].cells.today
    assert_eq({ cells.action:GetText(), cells.item:GetText(), cells.qty:GetText(), ui.rows[2].note:GetText() },
              { "MAIL", "Copper Bar", "×6", "to Amaya" })
    assert_eq(AHProfit.Plan.IncomingMail("Brugan-Grommash"), {})
    Stubs.world.player.name = "Amaya"
    AHProfit.MainWindow.Refresh()
    cells = ui.rows[1].cells.today
    assert_eq({ cells.action:GetText(), cells.item:GetText(), ui.rows[2].note:GetText() },
              { "CRAFT", "Copper Rod", "6 Copper Bar from Brugan · not mailed yet" })
    assert_eq(#AHProfit.Plan.IncomingMail("Amaya-Grommash"), 1)
  end)
end)
