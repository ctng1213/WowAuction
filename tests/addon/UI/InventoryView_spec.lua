-- Specs for addon/AHProfit/UI/InventoryView.lua: the Inventory and Reservations tabs' rows (SPEC §4.3, A22, A26).

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

describe("InventoryView", function()
  it("A22 the Inventory tab: what you hold, its worth and its plan, best worth first", function()
    local VM = Boot()
    LOOKS[6948], LOOKS[2592], LOOKS[4306], LOOKS[6217] = { name = "Hearthstone", quality = 1 },
      { name = "Wool Cloth", quality = 1 }, { name = "Silk Cloth", quality = 1 }, { name = "Copper Rod", quality = 1 }
    local holdings = { { where = "bags", key = 2589, qty = 20, bound = false }, { where = "bags", key = 2840, qty = 6, bound = false },
                       { where = "bags", key = 6948, qty = 1, bound = true }, { where = "bank", key = 2592, qty = 4, bound = false },
                       { where = "mail", key = 4306, qty = 2, bound = false } }
    local actions = { Act("own:2589", "post", 840, NOW, 90, { qty = 20, price = 45, own = true }),
                      Act("own:craft:2663", "craft", 5700, NOW, 100, { key = 6217, qty = 3, own = true, uses = { [2840] = 6 } }),
                      Act("w", "post", 114, NOW + DAY + 19 * HOUR + 30 * MINUTE, 80, { key = 2592, qty = 4, price = 30 }) }
    local items = { [4306] = { sellValue = 60 } }
    local rows = VM.Inventory(holdings, actions, Look, function(key) return items[key] end, 0)
    local got = {}
    for i, r in ipairs(rows) do got[i] = { r.item, r.where, r.worth, r.note } end
    assert_eq(got, { { "Linen Cloth", "bags", "8s 40c", "Post now at 45c" }, { "Silk Cloth", "mail", "1s 20c", "Not planned" },
                     { "Wool Cloth", "bank", "1s 14c", "Post Sat 10:00 at 30c (AHSync)" },
                     { "Copper Bar", "bags", "—", "For crafting Copper Rod" }, { "Hearthstone", "bags", "—", "Bound · not planned" } })
    assert_eq({ rows[1].chance, rows[1].volume, rows[5].volume }, { "90%", "48/day", "—" })
    local others = VM.Inventory({ { where = "bags", key = 3300, qty = 1, bound = false } },
      { Act("v", "vendor", 12, NOW, nil, { key = 3300, qty = 1, price = 12 }), Act("h", "hold", 0, NOW, nil, { key = 2592, qty = 1 }) },
      function() return { name = "x", quality = 0 } end, function() return nil end, 0)
    assert_eq(others[1].note, "Vendor at 12c each (AHSync)")
    LOOKS[6948], LOOKS[2592], LOOKS[4306], LOOKS[6217] = nil, nil, nil, nil
  end)

  it("counts held units across places, leaves bound ones out and ties on the key", function()
    local VM = Boot()
    local holdings = { { where = "bags", key = 7, qty = 4 }, { where = "bank", key = 7, qty = 3 },
                       { where = "bags", key = 8, qty = 1, bound = true } }
    local rows = VM.Reservations({ [7] = 2, [8] = -1, [9] = 1 }, holdings, function() return { name = "Same", quality = 1 } end)
    local out = {}
    for i, r in ipairs(rows) do out[i] = r.key .. " " .. r.keeping .. " " .. r.held .. " " .. r.free end
    assert_eq(out, { "7 2 ×7 ×5", "8 All ×0 —", "9 1 ×0 —" })
    assert_eq(VM.ReservationsFooter({ rows[1] }), "1 item kept · click a row to change it")
  end)
end)
