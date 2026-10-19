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

end)
