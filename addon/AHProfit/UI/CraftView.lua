-- UI/CraftView.lua: the Craft tab's rows and footer (SPEC §4.3, A27, A30, A34, A36, A37, A42), split
-- from UI/ViewModel.lua for size. It adds to the same AHProfit.ViewModel table; frame code only draws.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel

-- ------------------------------------------------------------------ Craft tab (A27, UI11)

--- A craft row's note lines, one per mat and source (A34): what it uses of yours, then what it buys
--- from a vendor, then on the AH, each by item key.
---@param o table          From Options.CraftOptions
---@param lookOf function  key -> look
---@return string[]        e.g. { "use 10 owned Linen Cloth", "buy 2 Coarse Thread from a vendor · 20c" }
local function MatLines(o, lookOf)
  local fromVendor, vendorPrice, ahGold = o.fromVendor or {}, o.vendorPrice or {}, o.ahGold or {}
  local fromAH = {}
  for key, units in pairs(o.bought) do
    if units > (fromVendor[key] or 0) then fromAH[key] = units - (fromVendor[key] or 0) end
  end
  local lines = {}
  --- Add one line per key of a tally, in key order.
  ---@param units table      key -> units
  ---@param text function    (units text, name, key) -> line
  local function Add(units, text)
    local keys = {}
    for key in pairs(units) do keys[#keys + 1] = key end
    table.sort(keys)
    for _, key in ipairs(keys) do lines[#lines + 1] = text(ViewModel.RoundHalfUp(units[key]), lookOf(key).name, key) end
  end
  Add(o.ownedUsed, function(n, name) return "use " .. n .. " owned " .. name end)
  Add(fromVendor, function(n, name, key)
    return "buy " .. n .. " " .. name .. " from a vendor · " .. ViewModel.Money(fromVendor[key] * (vendorPrice[key] or 0))
  end)
  Add(fromAH, function(n, name, key) return "buy " .. n .. " " .. name .. " on the AH · " .. ViewModel.Money(ahGold[key]) end)
  return lines
end

--- What to do with a made product, from its first sale action (A37).
---@param a table|nil     The product's first post, hold, vendor or disenchant action, or nil
---@param nowMs number
---@param offsetMs number
---@return string         e.g. "post Sun 09:00, for the 17:00 peak", "post it now at 80c", "hold for a later week"
local function SaleNote(a, nowMs, offsetMs)
  if not a then return "post it at the AH" end
  if a.kind == "post" and a.peak and a.at > nowMs then
    local day, hour = ViewModel.Clock(a.at, offsetMs)
    local _, peak = ViewModel.Clock(a.peak, offsetMs)
    return "post " .. day .. " " .. hour .. ", for the " .. peak .. " peak"
  end
  if a.kind == "post" then return "post it now at " .. ViewModel.Money(a.price) end
  if a.kind == "hold" then return "hold for a later week" end
  if a.kind == "vendor" then return "sell it to a merchant at " .. ViewModel.Money(a.price) .. " each" end
  return "disenchant it"
end

--- A recipe whose product you already hold, in your bags or on the AH, up to its cap: a green check and
--- "made", with a note line for each (A36, A37, A42).
---@param o table     { key, made = units held, inBags, listed, endsAt }
---@param look table  The product's look
---@param sale string What to do with it, from SaleNote
---@param nowMs number
---@return table      Row of text
local function MadeRow(o, look, sale, nowMs)
  local notes, inBags = {}, o.inBags or o.made
  if inBags > 0 then notes[#notes + 1] = "×" .. inBags .. " in your bags · " .. sale end
  if (o.listed or 0) > 0 then notes[#notes + 1] = "×" .. o.listed .. " on the AH · " .. ViewModel.EndsIn(o.endsAt, nowMs) end
  return { key = o.key, item = look.name, quality = look.quality, kind = "craft", made = true,
           source = ViewModel.CHECK .. " made", crafts = "×" .. o.made, mats = "—", profit = "—", netColor = "muted",
           chance = "—", volume = look.soldPerDay and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
           risk = "—", riskAmber = false, notes = notes }
end

--- When an auction ends, from now (A42).
---@param endsAt number  ms
---@param nowMs number
---@return string        "ends in 11 h", "ends in 20 min"
function ViewModel.EndsIn(endsAt, nowMs)
  local minutes = math.floor((endsAt - nowMs) / 60000)
  if minutes < 60 then return "ends in " .. math.max(1, minutes) .. " min" end
  return "ends in " .. math.floor(minutes / 60) .. " h"
end

--- A recipe that pays now: its crafts, mats, profit and market cells (views.craft_row, D6-D8).
---@param o table          From Options.CraftOptions
---@param lookOf function  key -> look
---@param totalGold number All your gold, copper, for risk
---@return table           Row of text
local function CraftRow(o, lookOf, totalGold)
  local look = lookOf(o.key)
  local risk, amber = ViewModel.Risk(o.matGold, totalGold)
  local chance = o.p and ViewModel.RoundHalfUp(o.p * 100) or nil
  return {
    key = o.key, item = look.name, quality = look.quality, kind = "craft", source = o.source, crafts = "×" .. o.crafts,
    mats = o.matGold > 0 and ViewModel.Money(o.matGold) or "—", profit = ViewModel.SignedMoney(o.profit),
    netColor = "pos", chance = chance and (chance .. "%") or "—", dot = chance and ViewModel.Level(look.conf) or nil,
    volume = look.soldPerDay and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
    risk = risk, riskAmber = amber, notes = MatLines(o, lookOf),
  }
end

--- The Craft tab's rows (views.craft_row for one character, D6-D8).
---@param options table[]  Options.CraftOptions
---@param lookOf function  key -> look
---@param totalGold number All your gold, copper, for risk
---@param actions table[]|nil This character's actions: a made product's note says what they do with it (A37)
---@param nowMs number|nil
---@param offsetMs number|nil
---@return table[]         Rows of text, in the options' order
function ViewModel.CraftRows(options, lookOf, totalGold, actions, nowMs, offsetMs)
  local sales, sorted = {}, {}
  for i, a in ipairs(actions or {}) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if (a.kind == "post" or a.kind == "hold" or a.kind == "vendor" or a.kind == "de") and not sales[a.key] then
      sales[a.key] = a
    end
  end
  local rows = {}
  for i, o in ipairs(options) do
    rows[i] = o.made and MadeRow(o, lookOf(o.key), SaleNote(sales[o.key], nowMs or 0, offsetMs or 0), nowMs or 0)
              or CraftRow(o, lookOf, totalGold)
  end
  return rows
end

--- A chain's profit, counted once (SPEC §3.4 expected net, D59; planned_crafts.chain_net): the actions other than
--- posts that have a net (the final craft) carry it; its posts count their own net only when no such action does.
---@param chain table[]  Every action of the chain
---@return number        Copper (float)
function ViewModel.ChainNet(chain)
  local steps, posts, carried = 0, 0, false
  for _, a in ipairs(chain) do
    if a.kind == "post" then
      posts = posts + (a.net or 0)
    elseif a.net and a.net ~= 0 then
      steps, carried = steps + a.net, true
    end
  end
  return carried and steps or posts
end

--- One planned craft chain as a Craft tab row for its final product: every mat buy, the intermediate crafts
--- made first, the chain's profit and when to sell (A63).
---@param craft table      The chain's last craft (its final product)
---@param chain table[]    Every action of its chain, in time order
---@param lookOf function  key -> look
---@param totalGold number All your gold, copper, for risk
---@param nowMs number
---@param offsetMs number
---@return table           Row of text
local function PlannedRow(craft, chain, lookOf, totalGold, nowMs, offsetMs)
  local look, notes, mats, post = lookOf(craft.key), {}, 0, nil
  local net = ViewModel.ChainNet(chain)                                -- counted once (D59)
  for _, a in ipairs(chain) do
    if a.kind == "buy" then
      local cost = (a.price or 0) * a.qty
      mats = mats + cost
      notes[#notes + 1] = "buy " .. a.qty .. " " .. lookOf(a.key).name .. (a.vendor and " from a vendor · " or " on the AH · ")
        .. ViewModel.Money(cost)
    elseif a.kind ~= "craft" and a.key == craft.key and not post then
      post = a
    end
  end
  for _, a in ipairs(chain) do                                         -- intermediates, after the buys (A63)
    if a.kind == "craft" and a ~= craft then notes[#notes + 1] = "craft " .. a.qty .. " " .. lookOf(a.key).name .. " first" end
  end
  notes[#notes + 1] = SaleNote(post, nowMs, offsetMs)
  local risk, amber = ViewModel.Risk(mats, totalGold)
  local chance = post and post.chance
  return {
    key = craft.key, item = look.name, quality = look.quality, kind = "craft", source = "Plan", crafts = "×" .. craft.qty,
    mats = mats > 0 and ViewModel.Money(mats) or "—", profit = ViewModel.SignedMoney(net),
    netColor = net > 0 and "pos" or "neg", chance = chance and (chance .. "%") or "—",
    dot = chance and ViewModel.Level(look.conf) or nil,
    volume = look.soldPerDay and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
    risk = risk, riskAmber = amber, notes = notes, planned = true, netCopper = net,
  }
end

--- The crafts AHSync's plan has for this character, as Craft tab rows (A63), so the tab agrees with Shopping.
---@param actions table[]   The character's plan actions
---@param lookOf function   key -> look
---@param totalGold number  All your gold, copper
---@param nowMs number
---@param offsetMs number
---@return table[]          One row per chain with a craft, for its last craft; best profit first (ties: chain id)
function ViewModel.PlannedCraftRows(actions, lookOf, totalGold, nowMs, offsetMs)
  local sorted, chains, order, last = {}, {}, {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    local chain = a.parent or a.id
    if not chains[chain] then chains[chain], order[#order + 1] = {}, chain end
    table.insert(chains[chain], a)
    if a.kind == "craft" then last[chain] = a end
  end
  local rows = {}
  for _, chain in ipairs(order) do
    if last[chain] then
      local row = PlannedRow(last[chain], chains[chain], lookOf, totalGold, nowMs, offsetMs)
      row.chain = chain
      rows[#rows + 1] = row
    end
  end
  table.sort(rows, function(x, y)
    if x.netCopper ~= y.netCopper then return x.netCopper > y.netCopper end
    return x.chain < y.chain
  end)
  return rows
end

--- The footer under Craft.
---@param rows table[]  From ViewModel.CraftRows, planned rows first
---@param known number  Recipes the character knows that make an item
---@return string       e.g. "1 planned · 1 of 2 known recipes pay now", plus " · 1 made" (A36, A63)
function ViewModel.CraftFooter(rows, known)
  local made, planned = 0, 0
  for _, r in ipairs(rows) do
    if r.made then made = made + 1 end
    if r.planned then planned = planned + 1 end
  end
  local own = #rows - made - planned
  local text = own .. " of " .. (known - planned) .. ((known - planned) == 1 and " known recipe" or " known recipes") .. " pay now"
  if planned > 0 then text = planned .. " planned · " .. text end
  return made > 0 and (text .. " · " .. made .. " made") or text
end
