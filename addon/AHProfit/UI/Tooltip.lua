-- UI/Tooltip.lua: the AHProfit block under an item's tooltip (SPEC §4.3 Tooltip, TT8-TT21, UI17). It gathers the
-- facts (the market file and a newer in-game scan's price, this character's plan actions and reservations, the live
-- reselection's unfunded rows, the addon's own option) and UI/TooltipView.lua makes the lines; this file only finds
-- the item and draws them, label left and value right. A few cache lookups per hover, never a reselection of its own
-- (SPEC §5.1: the live state is built once, the first time anything asks).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local Live = AHProfit.Live
local Options = AHProfit.Options
local Reserves = AHProfit.Reserves
local Inventory = AHProfit.Capture.Inventory
local Prices = AHProfit.Engine.Prices
local VM = AHProfit.ViewModel
local TV = AHProfit.TooltipView
local Disenchant = AHProfit.Engine.Disenchant

local Tooltip = {}
AHProfit.Tooltip = Tooltip

local DE_KINDS = { [2] = "weapon", [4] = "armor" }
-- The mockup's colours (docs/mockups/addon.html), as 0-1 RGB.
local RGB = { label = { 0.56, 0.83, 1 }, value = { 1, 1, 1 }, title = { 1, 0.82, 0 }, muted = { 0.56, 0.54, 0.49 },
              amber = { 1, 0.69, 0.18 }, pos = { 0.24, 0.86, 0.31 }, neg = { 1, 0.29, 0.24 }, post = { 1, 0.82, 0 },
              buy = { 0.37, 0.71, 1 }, craft = { 1, 0.6, 0.24 }, de = { 0.7, 0.48, 1 }, grey = { 0.67, 0.67, 0.67 },
              mail = { 0.56, 0.83, 1 } }
local FLAT = "Interface\\Buttons\\WHITE8X8"

--- A colour escape around a text.
---@param text string
---@param name string  An RGB key
---@return string
local function Paint(text, name)
  local c = RGB[name] or RGB.value
  return string.format("|cff%02x%02x%02x%s|r", c[1] * 255, c[2] * 255, c[3] * 255, text)
end

--- The age dot as an inline texture (WoW's fonts have no ● glyph).
---@param name string  "pos" | "amber"
---@return string
local function Dot(name)
  local c = RGB[name]
  return string.format(" |T%s:7:7:0:0:8:8:0:8:0:8:%d:%d:%d|t", FLAT, c[1] * 255, c[2] * 255, c[3] * 255)
end

--- An item's name, from the client's cache.
---@param key number
---@return string
local function NameOf(key)
  return (Adapter.ItemInfo(VM.BaseItemId(key))) or ("Item " .. key)
end

--- An item's disenchant value per item, when one of your characters can disenchant it, and its possible mats at
--- their latest prices for the tooltip's breakdown (TT29).
---@param key number
---@return boolean, number|nil, table[]|nil  can disenchant, expected value at the mats' sale values (float copper, the
---                                          Do line's), and { chance, min, max, name, price (the mat's cur or nil) }
local function DisenchantValue(key)
  local quality, ilvl, classId = Adapter.ItemDetails(VM.BaseItemId(key))
  local kind = DE_KINDS[classId]
  if not (quality and ilvl and kind) then return false, nil end
  local skill, outcomes = Disenchant.OutcomesFor(Disenchant.Table(), quality, ilvl, kind)
  if not skill or not Disenchant.CanDisenchant(skill, AHProfit.Options.BestSkill(DB.Get() or {}, "Enchanting")) then
    return false, nil
  end
  local values, mats = {}, {}
  for i, o in ipairs(outcomes) do
    local mat = Market.Item(o[1])
    values[i] = mat and mat.sellValue or 0
    mats[i] = { chance = o[2], min = o[3], max = o[4], name = NameOf(o[1]), price = mat and mat.cur or nil }
  end
  return true, Disenchant.ExpectedValue(outcomes, values), mats
end

--- A window's expected price, as the plan prices it (`plan.slot_price`: level × how with the trend to its best hour,
--- kept within the last 30 days' range; TT6, TT7, TT22, TT23, R21).
---@param item table
---@param w table
---@param nowMs number
---@return number|nil  Copper; nil without a level
local function WindowPrice(item, w, nowMs)
  local level = item.med7 or item.med30
  if not level then return nil end
  return Prices.SlotPrice(level, w.how, item.trend or 0, Prices.DaysUntilSlot(w.best, nowMs), nil, item.p10, item.med7,
                          item.p90, item.closes30)
end

--- The best sell and buy windows' facts for TooltipView.
---@param item table
---@param cur number|nil
---@param nowMs number
---@return table|nil, table|nil  sell, buy: { slot (the best hour), price, weeks, now }, the buy with cheaperNow
local function Windows(item, cur, nowMs)
  local sell, buy = nil, nil
  local s = item.sellWindows[1]
  local sp = s and WindowPrice(item, s, nowMs)
  if sp then sell = { slot = s.best, price = sp, weeks = s.weeks, now = TV.InWindow(s, nowMs) } end
  local b = item.buyWindows[1]
  local bp = b and WindowPrice(item, b, nowMs)
  if bp then
    local cheaper = cur ~= nil
    for _, w in ipairs(item.buyWindows) do
      local p = WindowPrice(item, w, nowMs)
      if not p or cur == nil or cur >= p then cheaper = false end
    end
    buy = { slot = b.best, price = bp, weeks = b.weeks, now = TV.InWindow(b, nowMs), cheaperNow = cheaper }
  end
  return sell, buy
end

--- The price now, its age text and the units listed: a newer in-game scan's book wins over the file's (A21).
---@param key number
---@param item table
---@param nowMs number
---@param atAH boolean  The AH window is open: a live price reads "now"
---@return number|nil, string, number  cur, age text, units listed
local function PriceNow(key, item, nowMs, atAH)
  local generatedAt, newest = Market.Times()
  local levels = Live.Item(key, newest)
  if levels then
    local units = 0
    for _, l in ipairs(levels) do units = units + l.units end
    local book = AHProfit.Scan.Replicate.LastBook()
    local age = (atAH or not book) and "now" or TV.AgeText(nowMs - book.at)
    return levels[1] and levels[1].price, age, units
  end
  local ageMs = item.curAgeMin and (nowMs - generatedAt + item.curAgeMin * 60000)
  return item.cur, TV.AgeText(ageMs), item.supply or item.curQty or 0
end

--- Whether the recipe that makes a product uses an item: the exported graph's recipes, else your captured ones.
---@param charId string
---@return function  (product, key) -> boolean
local function UsedBy(charId)
  return function(product, key)
    local graph = Market.Graph()
    for _, r in pairs(graph and graph.recipes or {}) do
      if r.product == product then
        for _, m in ipairs(r.reagents) do if m.key == key then return true end end
      end
    end
    for _, r in ipairs(Options.KnownRecipes(DB.Get() or {}, charId)) do
      if r.product == product then
        for _, m in ipairs(r.reagents) do if m[1] == key then return true end end
      end
    end
    return false
  end
end

--- The Shopping list's buy of an item for this character: its units, the lowest cap and what it is for (A65, A40).
---@param actions table[]  This character's plan actions
---@param key number
---@return table|nil       { qty, price, purpose }
local function ShoppingOf(actions, key)
  local out = nil
  for _, a in ipairs(actions) do
    if a.kind == "buy" and a.key == key and a.price and not a.vendor then
      out = out or { qty = 0, price = a.price }
      out.qty, out.price = out.qty + a.qty, math.min(out.price, a.price)
      if not out.purpose then
        local why = VM.BuyReason(a, actions)
        for _, b in ipairs(actions) do
          if why == "craft" and b.kind == "craft" and (b.parent or b.id) == (a.parent or a.id) then
            out.purpose = "for " .. NameOf(b.key)
          end
        end
        out.purpose = out.purpose or (why == "de" and "to disenchant") or (why == "flip" and "to resell") or nil
      end
    end
  end
  return out
end

--- The live reselection's unfunded row for an item, when the plan has a portfolio snapshot (SPEC §4.0).
---@param key number
---@return table|nil  { strategy, net, reason (the full status text) }
local function UnfundedOf(key)
  local Adapt = AHProfit.Adapt
  if not Adapt.Snapshot() then return nil end
  local result = Adapt.Current()
  for _, e in ipairs(result and result.excluded or {}) do
    if e.rc.c.key == key then
      return { strategy = e.rc.c.strategy, net = e.rc.net, reason = AHProfit.PortfolioView.Reason(e.rc, e.reason, Market.Item) }
    end
  end
  return nil
end

--- Units of an item this character holds in bags, bank and mail, and in its bags alone.
---@param db table
---@param charId string
---@param key number
---@return number, number
local function HeldOf(db, charId, key)
  local inventory, all, bags = (db.inventory or {})[charId] or {}, 0, 0
  for _, where in ipairs(VM.PLACES) do
    for _, row in ipairs(Inventory.Decode(inventory[where])) do
      if row.key == key and not row.bound then
        all = all + row.qty
        if where == "bags" then bags = bags + row.qty end
      end
    end
  end
  return all, bags
end

--- Units of an item held across your characters, by place: bags, bank, mail, then your active auctions (TT30).
---@param db table
---@param key number
---@param nowMs number
---@return table  { total, places = { { where, qty } } } with only the places that hold some
local function HoldingsOf(db, key, nowMs)
  local by = {}
  for _, places in pairs(db.inventory or {}) do
    for _, where in ipairs(VM.PLACES) do
      for _, row in ipairs(Inventory.Decode(places[where])) do
        if row.key == key then by[where] = (by[where] or 0) + row.qty end
      end
    end
  end
  for _, entry in pairs(db.ownAuctions or {}) do
    for _, a in ipairs(AHProfit.Capture.Auctions.Active(entry, nowMs)) do
      if a.key == key then by.AH = (by.AH or 0) + a.units end
    end
  end
  local out = { total = 0, places = {} }
  for _, where in ipairs({ "bags", "bank", "mail", "AH" }) do
    if by[where] then
      out.total = out.total + by[where]
      out.places[#out.places + 1] = { where, by[where] }
    end
  end
  return out
end

--- What to do with the item, for the Do line (SPEC §4.3).
---@param key number
---@param item table
---@param nowMs number
---@param facts table  { atAH, cur, deValue, sell, buy }
---@return table|nil   TooltipView.DoOf's
local function DoFor(key, item, nowMs, facts)
  local db, info = DB.Get() or {}, Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  local held, inBags = HeldOf(db, charId, key)
  local actions = Plan.CharacterActions(charId)
  local own = held > 0 and Options.SellOne(key, held, item, select(3, Adapter.ItemInfo(VM.BaseItemId(key))), nowMs, charId) or nil
  return TV.DoOf({ key = key, held = held, reserve = Reserves.Get(db, charId)[key], listing = facts.atAH and inBags == 0,
                   shopping = ShoppingOf(actions, key), actions = actions, allActions = Plan.AllActions,
                   unfunded = UnfundedOf(key), own = own, deValue = facts.deValue, usedBy = UsedBy(charId),
                   nameOf = NameOf, nowMs = nowMs, offsetMs = Adapter.LocalOffsetMs(nowMs),
                   sellNow = facts.sell and facts.sell.now, buyNow = facts.buy and facts.buy.cheaperNow })
end

--- The tooltip lines for an item (SPEC §4.3, TT8-TT21); nil without a market file.
---@param key number
---@param nowMs number
---@return table[]|nil  TooltipView.Lines
function Tooltip.Lines(key, nowMs)
  local offsetMs = Adapter.LocalOffsetMs(nowMs)
  local generatedAt = Market.Times()
  if not generatedAt then return nil end
  local age = VM.Age(generatedAt, nowMs)
  local item = Market.Item(key)
  if not item then return TV.Lines({ age = age, noData = true }) end
  local panel = AHProfit.AHPanel and AHProfit.AHPanel.Frames()
  local atAH = panel ~= nil and panel.frame:IsShown()
  local cur, ageText, listed = PriceNow(key, item, nowMs, atAH)
  local sell, buy = Windows(item, cur, nowMs)
  local canDE, deValue, mats = DisenchantValue(key)
  local facts = { atAH = atAH, deValue = deValue, sell = sell, buy = buy }
  return TV.Lines({ age = age, doing = DoFor(key, item, nowMs, facts), offsetMs = offsetMs, sell = sell, buy = buy,
    market = { cur = cur, ageText = ageText, med7 = item.med7, med14 = item.med14, med30 = item.med30, listed = listed,
              soldPerDay = item.soldPerDay, salesDays = item.salesDays },
    mats = canDE and mats or nil, holdings = HoldingsOf(DB.Get() or {}, key, nowMs) })
end

--- One line as drawn: label and value text with their colour escapes and coins.
---@param line table  A TooltipView line
---@return string, string, table, table  left, right, left RGB, right RGB
function Tooltip.Draw(line)
  local left, right = line.label, VM.WithCoins(line.value)
  if line.header then
    return Paint(left, "title"), Paint(line.value, line.amber and "amber" or "muted") .. Dot(line.amber and "amber" or "pos"),
           RGB.title, RGB.value
  end
  if line.isDo then left = Paint("Do: ", "label") .. Paint(line.verb, line.verbColor) .. Paint(line.rest, "value") end
  for _, span in ipairs(line.spans) do
    local text = VM.WithCoins(span[1])
    local at = right:find(text, 1, true)
    if at then right = right:sub(1, at - 1) .. Paint(text, span[2]) .. right:sub(at + #text) end
  end
  local valueRgb = (line.muted and RGB.muted) or (line.valueColor and RGB[line.valueColor]) or RGB.value
  return left, right, line.labelMuted and RGB.muted or RGB.label, valueRgb
end

--- Add the block to a tooltip that shows an item; failures are swallowed (never break a tooltip).
---@param tooltip table
---@param link string
function Tooltip.OnItem(tooltip, link)
  local ok, lines = pcall(function()
    local key = Adapter.ItemKey(link)
    return key and Tooltip.Lines(key, Adapter.NowMs())
  end)
  if not (ok and lines) then return end
  local drawn = {}
  for i, line in ipairs(lines) do drawn[i] = { Tooltip.Draw(line) } end
  Adapter.AddTooltipLines(tooltip, drawn)
end

Adapter.HookItemTooltip(Tooltip.OnItem)
