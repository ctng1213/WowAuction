-- UI/InventoryView.lua: the Inventory and Reservations tabs' rows (SPEC §4.3, A22, A26), split from
-- UI/ViewModel.lua for size. It adds to the same AHProfit.ViewModel table; frame code only draws.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel

-- ------------------------------------------------------------------ Inventory tab (SPEC §4.3, A22)

ViewModel.PLACES = { "bags", "bank", "mail" }

--- What the plan (AHSync's or the addon's own) does with an item, as the Inventory note.
---@param a table|nil       The item's first sale action, or nil
---@param craftedInto string|nil  The product a craft uses it for
---@param offsetMs number
---@return string
local function PlanText(a, craftedInto, offsetMs)
  if craftedInto then return "For crafting " .. craftedInto end
  if not a then return "Not planned" end
  local who = a.own and "" or " (AHSync)"
  if a.kind == "post" then
    if a.own then return "Post now at " .. ViewModel.Money(a.price) end
    local day, hour = ViewModel.Clock(a.at, offsetMs)
    return "Post " .. day .. " " .. hour .. " at " .. ViewModel.Money(a.price) .. who
  end
  if a.kind == "vendor" then return "Vendor at " .. ViewModel.Money(a.price) .. " each" .. who end
  if a.kind == "de" then return "Disenchant" .. who end
  return "Hold for a later week" .. who
end

--- An Inventory note with what you keep in front of the plan for the rest (A25).
---@param kept number|nil  The reserve (-1: all)
---@param plan string      The plan's note
---@return string
function ViewModel.KeptNote(kept, plan)
  if kept == nil then return plan end
  if kept == -1 then return "Kept for yourself" end
  return "Keeping " .. kept .. " for yourself · " .. plan
end

--- One of your auctions as an Inventory row (A50): its worth is what it brings if it sells.
---@param h table      { key, qty, price, endsAt }
---@param look table
---@param nowMs number
---@return table       Row of text
local function ListedRow(h, look, nowMs)
  local worth = h.price and h.price * h.qty * (1 - AHProfit.Engine.Fees.AH_CUT) or nil
  local note = (h.price and ("listed at " .. ViewModel.Money(h.price) .. " each · ") or "") .. ViewModel.EndsIn(h.endsAt, nowMs)
  return { key = h.key, item = look.name, quality = look.quality, where = "AH", qty = "×" .. h.qty, kind = "inventory",
           worth = worth and ViewModel.Money(worth) or "—", chance = "—", volume = "—", note = note,
           sortWorth = worth and ViewModel.RoundHalfUp(worth) or -1, netColor = "muted", riskAmber = false }
end

--- The Inventory tab: what this character holds, best worth first (A22). Worth is what its plan
--- expects (the action's net per unit), else its sell value; bound items have none.
---@param holdings table[]  { where, key, qty, bound }
---@param actions table[]   This character's plan actions and the addon's own options
---@param lookOf function   key -> look
---@param itemOf function   key -> market item or nil
---@param offsetMs number
---@param reserves table|nil key -> units you keep (A25)
---@param nowMs number|nil  For when your auctions end (A50)
---@return table[]          Rows { item, quality, where, qty, worth, chance, dot, volume, note, sortWorth }
function ViewModel.Inventory(holdings, actions, lookOf, itemOf, offsetMs, reserves, nowMs)
  local sale, craftedInto = {}, {}
  local sorted = {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if (a.kind == "post" or a.kind == "vendor" or a.kind == "de" or a.kind == "hold") and not a.parent
      and sale[a.key] == nil then
      sale[a.key] = a
    end
    for key in pairs(a.uses or {}) do craftedInto[key] = craftedInto[key] or lookOf(a.key).name end
  end
  local rows, stock = {}, {}
  for _, h in ipairs(holdings) do
    if h.where == "AH" then rows[#rows + 1] = ListedRow(h, lookOf(h.key), nowMs or 0) else stock[#stock + 1] = h end
  end
  for _, h in ipairs(stock) do
    local look, a, item = lookOf(h.key), sale[h.key], itemOf(h.key)
    local each
    if not h.bound and not craftedInto[h.key] then
      if a and a.net and a.qty > 0 then each = a.net / a.qty else each = item and item.sellValue end
    end
    local worth = each and each * h.qty
    local chance = (not h.bound and a and a.kind == "post") and a.chance or nil
    rows[#rows + 1] = {
      key = h.key, item = look.name, quality = look.quality, where = h.where, qty = "×" .. h.qty, kind = "inventory",
      worth = worth and ViewModel.Money(worth) or "—", chance = chance and (chance .. "%") or "—",
      dot = chance and ViewModel.Level(look.conf) or nil,
      volume = (look.soldPerDay and not h.bound) and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
      note = h.bound and "Bound · not planned" or ViewModel.KeptNote((reserves or {})[h.key], PlanText(a, craftedInto[h.key], offsetMs)),
      sortWorth = worth and ViewModel.RoundHalfUp(worth) or -1, netColor = "muted", riskAmber = false,
    }
  end
  table.sort(rows, function(x, y)
    if x.sortWorth ~= y.sortWorth then return x.sortWorth > y.sortWorth end
    if x.item ~= y.item then return x.item < y.item end
    return x.where < y.where
  end)
  return rows
end

--- The footer under Inventory.
---@param rows table[]  From ViewModel.Inventory
---@return string       e.g. "5 items · worth 10s 74c"
function ViewModel.InventoryFooter(rows)
  local total = 0
  for _, r in ipairs(rows) do if r.sortWorth > 0 then total = total + r.sortWorth end end
  return #rows .. (#rows == 1 and " item" or " items") .. " · worth " .. ViewModel.Money(total)
end

-- ------------------------------------------------------------------ Reservations tab (A26)

--- The Reservations tab: every item this character keeps, held or not, by name (A26).
---@param reserves table    key -> units kept (-1: all), from Reserves.Get
---@param holdings table[]  { where, key, qty, bound }, every place
---@param lookOf function   key -> look
---@return table[]          Rows { key, item, quality, keeping, held, free }
function ViewModel.Reservations(reserves, holdings, lookOf)
  local held = {}
  for _, h in ipairs(holdings) do
    if not h.bound then held[h.key] = (held[h.key] or 0) + h.qty end
  end
  local rows = {}
  for key, kept in pairs(reserves) do
    local look, units = lookOf(key), held[key] or 0
    local free = kept == -1 and 0 or math.max(0, units - kept)
    rows[#rows + 1] = { key = key, item = look.name, quality = look.quality, kind = "reservation",
                        keeping = kept == -1 and "All" or tostring(kept), held = "×" .. units,
                        free = free > 0 and ("×" .. free) or "—", netColor = "muted", riskAmber = false }
  end
  table.sort(rows, function(x, y)
    if x.item ~= y.item then return x.item < y.item end
    return x.key < y.key
  end)
  return rows
end

--- The footer under Reservations.
---@param rows table[]  From ViewModel.Reservations
---@return string       e.g. "3 items kept · click a row to change it"
function ViewModel.ReservationsFooter(rows)
  return #rows .. (#rows == 1 and " item" or " items") .. " kept · click a row to change it"
end
