-- Specs for addon/AHProfit/UI/CraftView.lua: the Craft tab's rows (SPEC §4.3, A27, A34, A36).

local Stubs = require("wow_stubs")

local HOUR, DAY, MINUTE = 3600000, 86400000, 60000
local T = 1760000000000                      -- Thu 9 Oct 2025 08:53:20 UTC, the stubs' clock
local NOW = T - (8 * 60 + 53) * MINUTE - 20000 + 14 * HOUR + 30 * MINUTE   -- Thu 14:30 UTC
local GOLD = 314000
local LOOKS = { [2589] = { name = "Linen Cloth", quality = 1, conf = 86, soldPerDay = 48 },
                [2840] = { name = "Copper Bar", quality = 1, conf = 50, soldPerDay = 40.4 } }

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

--- A pending plan action of 20 Linen at 38c by Amaya, standing alone unless changed.
---@param id string
---@param kind string
---@param net number|nil
---@param at number
---@param chance number|nil  Percent
---@param more table|nil     Fields to change
---@return table
local function Act(id, kind, net, at, chance, more)
  local a = { id = id, kind = kind, char = "Amaya-Grommash", key = 2589, qty = 20, price = 38, at = at, net = net,
              chance = chance, status = "pending" }
  for k, v in pairs(more or {}) do a[k] = v end
  return a
end

describe("CraftView", function()
  it("craft rows: mats \"—\" when you own them all, risk from the mats bought, no chance without a price", function()
    local VM = Boot()
    local looks = { [6217] = { name = "Copper Rod", quality = 1, conf = 80, soldPerDay = 6.4 },
                    [2840] = { name = "Copper Bar", quality = 1 }, [2880] = { name = "Weak Flux", quality = 1 } }
    local lookOf = function(k) return looks[k] end
    local rows = VM.CraftRows({ { key = 6217, crafts = 3, matGold = 0, profit = 5700, p = 1, source = "Owned",
                                  ownedUsed = { [2840] = 6 }, bought = {} },
                                { key = 6217, crafts = 2, matGold = 4000, profit = 300, source = "Mixed",
                                  ownedUsed = { [2880] = 1 }, bought = { [2840] = 4 }, ahGold = { [2840] = 4000 } },
                                { key = 6217, crafts = 1, matGold = 300, profit = 10, source = "Mixed", ownedUsed = {},
                                  bought = { [2840] = 3 }, fromVendor = { [2840] = 1 }, vendorPrice = { [2840] = 100 },
                                  ahGold = { [2840] = 200 } } }, lookOf, 10000)
    assert_eq({ rows[1].mats, rows[1].profit, rows[1].chance, rows[1].dot, rows[1].volume, rows[1].risk, rows[1].notes },
              { "—", "+57s", "100%", "high", "6/day", "0.0%", { "use 6 owned Copper Bar" } })
    assert_eq({ rows[2].mats, rows[2].chance, rows[2].risk, rows[2].riskAmber, rows[2].notes },
              { "40s", "—", "40.0%", true, { "use 1 owned Weak Flux", "buy 4 Copper Bar on the AH · 40s" } })
    -- 3 bought: 1 from a vendor at 1s, 2 on the AH for 2s.
    assert_eq(rows[3].notes, { "buy 1 Copper Bar from a vendor · 1s", "buy 2 Copper Bar on the AH · 2s" })
    assert_eq(#VM.WithNotes(rows), 8)                                  -- 3 rows and 5 note lines (A34)
    assert_eq({ VM.CraftFooter(rows, 5), VM.CraftFooter({}, 1) }, { "3 of 5 known recipes pay now", "0 of 1 known recipe pay now" })
  end)

  it("A63 AHSync's planned crafts are rows of their own, with the chain's buys and when to sell", function()
    local VM = Boot()
    local looks = { [6303] = { name = "Raw Longjaw Mud Snapper", quality = 1 },
                    [4592] = { name = "Longjaw Mud Snapper", quality = 1, conf = 80, soldPerDay = 362 } }
    local SAT9, SAT18 = NOW + 2 * DAY - 5 * HOUR - 30 * MINUTE, NOW + 2 * DAY + 3 * HOUR + 30 * MINUTE
    local actions = {
      { id = "b", kind = "buy", key = 6303, qty = 100, price = 47, at = NOW, net = 0 },
      { id = "c", kind = "craft", key = 4592, qty = 100, at = NOW + HOUR, parent = "b", net = 0 },
      { id = "p", kind = "post", key = 4592, qty = 100, price = 120, at = SAT9, peak = SAT18, parent = "b", net = 9791, chance = 99 },
      { id = "x", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000 } }
    local rows = VM.PlannedCraftRows(actions, function(key) return looks[key] or { name = tostring(key), quality = 1 } end,
                                     GOLD, NOW, 0)
    assert_eq(#rows, 1)
    local r = rows[1]
    assert_eq({ r.item, r.source, r.crafts, r.mats, r.profit, r.chance, r.planned },
              { "Longjaw Mud Snapper", "Plan", "×100", VM.Money(4700), VM.SignedMoney(9791), "99%", true })
    assert_eq(r.notes, { "buy 100 Raw Longjaw Mud Snapper on the AH · " .. VM.Money(4700), "post Sat 09:00, for the 18:00 peak" })
    assert_eq(VM.CraftFooter({ r, { made = false }, { made = true } }, 3), "1 planned · 1 of 2 known recipes pay now · 1 made")
  end)

  it("D59 a planned chain's profit counts once on the Craft tab: the craft's, not the craft's plus its post's", function()
    local VM = Boot()
    local actions = {
      { id = "b", kind = "buy", key = 2840, qty = 2, price = 500, at = NOW, net = 0 },
      { id = "c", kind = "craft", key = 6217, qty = 1, at = NOW + MINUTE, parent = "b", net = 971 },
      { id = "p", kind = "post", key = 6217, qty = 1, price = 4099, at = NOW + 2 * MINUTE, parent = "b", net = 971,
        chance = 50 } }
    local rows = VM.PlannedCraftRows(actions, function(key) return { name = tostring(key), quality = 1 } end, GOLD, NOW, 0)
    assert_eq({ rows[1].profit, rows[1].netCopper }, { "+9s 71c", 971 })        -- never +19s 42c
  end)

  it("an own craft's mats stay within the budget it is given (A64's live cap, Recommend/Adapt_spec.lua)", function()
    Boot()
    local Options = AHProfit.Options
    local recipe = { spell = 1, product = 6217, yieldMin = 1, yieldMax = 1, reagents = { { 2840, 2 } } }
    local items = { [6217] = { postPrice = 4000, sellValue = 3800, chance = 1, supply = 100, refStack = 1 },
                    [2840] = { cur = 1000, curQty = 40, sellValue = 950 } }
    local ctx = { itemOf = function(k) return items[k] end, levelsOf = function() return nil end,
                  vendorOf = function() return 0 end, nowMs = NOW }
    local free = Options.CraftOptions({ recipe }, {}, ctx)[1]
    ctx.budget = 4500                                                     -- 45s: two crafts' bars
    local capped = Options.CraftOptions({ recipe }, {}, ctx)[1]
    assert_eq({ free.crafts > 2, capped.crafts, capped.matGold <= 4500 }, { true, 2, true })
  end)
  it("A63 a chain with intermediate crafts is one row for its final product, best profit first", function()
    local VM = Boot()
    local names = { [2770] = "Copper Ore", [2771] = "Tin Ore", [2840] = "Copper Bar", [3576] = "Tin Bar", [2841] = "Bronze Bar" }
    local lookOf = function(key) return { name = names[key] or tostring(key), quality = 1 } end
    local actions = {
      { id = "8", kind = "buy", key = 2770, qty = 22, price = 231, at = NOW, net = 0 },
      { id = "9", kind = "buy", key = 2771, qty = 22, price = 279, at = NOW + 1, parent = "8", net = 0 },
      { id = "10", kind = "craft", key = 2840, qty = 22, at = NOW + 2, parent = "8", net = 0 },
      { id = "11", kind = "craft", key = 3576, qty = 22, at = NOW + 3, parent = "8", net = 0 },
      { id = "12", kind = "craft", key = 2841, qty = 22, at = NOW + 4, parent = "8", net = 7799 },
      { id = "13", kind = "post", key = 2841, qty = 22, price = 455, at = NOW + 5, parent = "8", net = 0, chance = 100 },
      { id = "14", kind = "buy", key = 2771, qty = 40, price = 279, at = NOW, net = 0 },
      { id = "15", kind = "craft", key = 3576, qty = 40, at = NOW + 2, parent = "14", net = 5180 } }
    local rows = VM.PlannedCraftRows(actions, lookOf, GOLD, NOW, 0)
    assert_eq({ #rows, rows[1].item, rows[1].crafts, rows[2].item }, { 2, "Bronze Bar", "×22", "Tin Bar" })
    assert_eq(rows[1].notes, { "buy 22 Copper Ore on the AH · " .. VM.Money(5082), "buy 22 Tin Ore on the AH · " .. VM.Money(6138),
                               "craft 22 Copper Bar first", "craft 22 Tin Bar first", "post it now at " .. VM.Money(455) })
    assert_eq(rows[1].mats, VM.Money(11220))
  end)
end)
