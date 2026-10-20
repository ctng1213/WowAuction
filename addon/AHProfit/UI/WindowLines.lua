-- UI/WindowLines.lua: what each tab of the /ahp window shows, gathered from the addon files, the live state and the
-- addon's own options into view-model lines (SPEC §4.3), split from UI/MainWindow.lua for size. MainWindow only draws
-- these lines. The Portfolio tab shows the live reselection (UI/PortfolioView.lua) when the plan has a portfolio
-- snapshot for this market; without one it shows the plan's actions due now and the addon's own options, as Today did
-- (the mockup's States: "own options only"), so the tab is never blank.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local VM = AHProfit.ViewModel
local Options = AHProfit.Options
local Live = AHProfit.Live
local Reserves = AHProfit.Reserves
local Inventory = AHProfit.Capture.Inventory

local WindowLines = {}
AHProfit.WindowLines = WindowLines

WindowLines.missingName = false     -- a name asked for isn't in the client's item cache yet (MainWindow redraws)

--- An item's look for the view model: saved name, cache name and market activity.
---@param key number
---@return table
function WindowLines.LookOf(key)
  local saved = (DB.Get() or {}).itemNames
  -- Asking for an uncached item makes the client fetch it; GET_ITEM_INFO_RECEIVED then redraws.
  -- UNVERIFIED: Forever fires GET_ITEM_INFO_RECEIVED when a requested item arrives, as Classic does.
  local name, quality, sellPrice = Adapter.ItemInfo(VM.BaseItemId(key))
  if not name and not (saved and saved[key]) then WindowLines.missingName = true end
  return VM.Look(key, Market.Item(key), saved and saved[key], name, quality, sellPrice)
end
local LookOf = WindowLines.LookOf

--- What a merchant pays for one of an item, from the item cache.
---@param key number
---@return number|nil
local function VendorOf(key)
  return select(3, Adapter.ItemInfo(VM.BaseItemId(key)))
end

--- An item's live listings from a scan newer than the market data, or nil (SPEC §4.0).
---@param key number
---@return table[]|nil
local function LevelsOf(key)
  local _, newest = Market.Times()
  return Live.Item(key, newest)
end

--- An item's market data, re-priced from live listings when a newer scan has them (A19).
---@param key number
---@return table|nil
local function LiveItem(key)
  local item, levels = Market.Item(key), LevelsOf(key)
  if not item or not levels then return item end
  return Options.Reprice(item, levels, Market.Curve(key), VendorOf(key) or 0, Adapter.NowMs())
end

--- What this character has up on the AH, by item, from its last own-auctions snapshot (A42).
---@param charId string
---@param nowMs number
---@return table  key -> { units, endsAt }
local function Listed(charId, nowMs)
  return AHProfit.Capture.Auctions.Listed(((DB.Get() or {}).ownAuctions or {})[charId], nowMs)
end

--- Everything this character holds unbound, every place together (A49).
---@param charId string
---@return table  key -> units in bags, bank and mail
local function HeldEverywhere(charId)
  local inventory, units = ((DB.Get() or {}).inventory or {})[charId] or {}, {}
  for _, where in ipairs(VM.PLACES) do
    for key, qty in pairs(Options.Units(Inventory.Decode(inventory[where]))) do units[key] = (units[key] or 0) + qty end
  end
  return units
end

--- This character's actions: AHSync's plan, without buys a live scan shows gone (A2), plus the
--- addon's own options where the plan is silent (SPEC §4.0): crafts first, then the rest of what
--- you carry, so a mat is never both crafted and sold (A20).
---@param charId string
---@param nowMs number
---@return table[], number  Actions, and the buys dropped as gone
local function CharacterActions(charId, nowMs)
  local db = DB.Get() or {}
  local reserves = Reserves.Get(db, charId)
  local held = Options.Units(Inventory.Decode(((db.inventory or {})[charId] or {}).bags))
  local actions, gone = Options.WithoutGoneBuys(Plan.CharacterActions(charId), LevelsOf)
  actions = Reserves.Trim(actions, reserves, HeldEverywhere(charId))   -- never more than you hold and don't keep (A25, A49)
  local planned, crafted, recipes = {}, {}, {}
  for _, a in ipairs(actions) do
    planned[a.key] = true
    if a.kind == "craft" then crafted[a.key] = true end
  end
  for _, r in ipairs(Options.KnownRecipes(db, charId)) do
    if not crafted[r.product] then recipes[#recipes + 1] = r end
  end
  local units = Reserves.Apply(held, reserves)
  local ctx = { itemOf = LiveItem, levelsOf = LevelsOf, vendorOf = VendorOf, vendorBuyOf = Market.VendorPrice,
                listed = Listed(charId, nowMs), nowMs = nowMs, charId = charId, planned = planned,
                budget = AHProfit.Adapt.Cap() }      -- the live cap (A64)
  local crafts, reserved = Options.Crafts(recipes, units, ctx)
  ctx.reserved = reserved
  for _, a in ipairs(crafts) do actions[#actions + 1] = a end
  for _, a in ipairs(Options.Sell(units, ctx)) do actions[#actions + 1] = a end
  return actions, gone
end

--- The actions plus the mail other characters' plans send to this one, flagged incoming: a line under the craft
--- that waits for it, never a row of this character's (C22, A74).
---@param actions table[]  This character's actions; the mail is appended
---@param charId string
---@return table[]         The same list
local function WithIncoming(actions, charId)
  for _, m in ipairs(Plan.IncomingMail(charId)) do
    m.incoming = true
    actions[#actions + 1] = m
  end
  return actions
end

--- This character's Today rows (the plan's actions due now and the addon's own options), for the AH panel's Post
--- list and the Portfolio tab without a snapshot, with the Min chance filter.
---@param charId string
---@param minChance number  Percent
---@return table[]  ViewModel.Today rows
function WindowLines.TodayRows(charId, minChance)
  local nowMs = Adapter.NowMs()
  local gold = VM.TotalGold((DB.Get() or {}).characters)
  return VM.Today(CharacterActions(charId, nowMs), nowMs, Adapter.LocalOffsetMs(nowMs), minChance, LookOf, gold).rows
end

--- Everything this character holds, place by place (bags, bank, mail).
---@param charId string
---@param nowMs number|nil  With it, your active auctions too, as where "AH" (A50)
---@return table[]  { where, key, qty, bound, price, endsAt }
local function Holdings(charId, nowMs)
  local db = DB.Get() or {}
  local inventory, out = (db.inventory or {})[charId] or {}, {}
  for _, where in ipairs(VM.PLACES) do
    for _, row in ipairs(Inventory.Decode(inventory[where])) do
      out[#out + 1] = { where = where, key = row.key, qty = row.qty, bound = row.bound }
    end
  end
  if nowMs then
    for _, a in ipairs(AHProfit.Capture.Auctions.Active((db.ownAuctions or {})[charId], nowMs)) do
      out[#out + 1] = { where = "AH", key = a.key, qty = a.units, bound = false, price = a.price, endsAt = a.endsAt }
    end
  end
  return out
end

--- Whether the addon may suggest its own flip of an item: no planned flip of it, and no rule or flag against it (A59).
---@param planned table  key -> true for items AHSync's plan flips
---@return function      key -> true to skip
local function FlipSkip(planned)
  local rules = (DB.Get() or {}).rules or {}
  return function(key)
    local inGame = type(rules[key]) == "table" and rules[key].rule or nil
    local rule = inGame or Plan.Rule(key)
    local record = Plan.Record(key)
    return planned[key] or rule == "no_buy" or rule == "ignore" or (record ~= nil and record.notSelling)
  end
end

--- The Flip tab: AHSync's flips, then the addon's own one-stack ideas for the rest (A28, A59).
---@param charId string
---@param nowMs number
---@param offset number  Local time minus UTC, ms
---@param gold number    All your gold, copper
---@return table  { lines, footer, empty }
local function FlipLines(charId, nowMs, offset, gold)
  local planned = Plan.CharacterActions(charId)
  local live = {}
  for _, a in ipairs(Options.WithoutGoneBuys(planned, LevelsOf)) do live[a.id] = true end
  local rows, gone = {}, {}
  for _, r in ipairs(VM.FlipRows(planned, LookOf, LevelsOf, gold, offset)) do
    if live[r.id] then rows[#rows + 1] = r else gone[#gone + 1] = VM.GoneFlip(r) end   -- listed, greyed (A66)
  end
  local footer = VM.FlipFooter(rows, VM.FlipRows(planned, LookOf, LevelsOf, gold, offset))
  for _, r in ipairs(gone) do rows[#rows + 1] = r end
  local flipped = {}
  for _, a in ipairs(planned) do if a.kind == "buy" then flipped[a.key] = true end end
  local _, newest = Market.Times()
  local own = VM.OwnFlipRows(Market.FlipOffers(), { itemOf = LiveItem, levelsOf = LevelsOf, lookOf = LookOf,
    skip = FlipSkip(flipped), nowMs = nowMs, offsetMs = offset, totalGold = gold, curveOf = Market.Curve,
    vendorOf = VendorOf, nowCandidates = Live.FlipCandidates(newest, VM.MIN_FLIP_MARGIN) })     -- A69
  for _, row in ipairs(own) do rows[#rows + 1] = row end
  if #own > 0 then footer = footer .. " · " .. #own .. (#own == 1 and " own idea" or " own ideas") .. " (one stack each)" end
  return { lines = VM.WithNotes(rows), footer = footer,
           empty = "No flips right now: none in AHSync's plan, and no market offer under its buy price." }
end

--- The Craft tab: AHSync's planned crafts, then the addon's own options for the other known recipes (A27, A63).
---@param charId string
---@param nowMs number
---@param offset number
---@param gold number
---@return table  { lines, footer, empty }
local function CraftLines(charId, nowMs, offset, gold)
  local db = DB.Get() or {}
  local recipes = Options.KnownRecipes(db, charId)
  local units = Reserves.Apply(Options.Units(Inventory.Decode(((db.inventory or {})[charId] or {}).bags)),
                               Reserves.Get(db, charId))
  local actions = CharacterActions(charId, nowMs)        -- what a made product is for (A37)
  local plannedRows = VM.PlannedCraftRows(Plan.CharacterActions(charId), LookOf, gold, nowMs, offset)   -- A63
  local plannedKeys, own = {}, {}
  for _, row in ipairs(plannedRows) do plannedKeys[row.key] = true end
  for _, r in ipairs(recipes) do if not plannedKeys[r.product] then own[#own + 1] = r end end
  local ctx = { itemOf = LiveItem, levelsOf = LevelsOf, vendorOf = VendorOf, vendorBuyOf = Market.VendorPrice,
                listed = Listed(charId, nowMs), nowMs = nowMs, budget = AHProfit.Adapt.Cap() }   -- the live cap (A64)
  local rows = VM.CraftRows(Options.CraftOptions(own, units, ctx), LookOf, gold, actions, nowMs, offset)
  for i = #plannedRows, 1, -1 do table.insert(rows, 1, plannedRows[i]) end
  local empty = #recipes == 0 and "No recipes seen on this character yet. Open each profession window once."
    or "None of your recipes pays right now."
  return { lines = VM.WithNotes(rows), footer = VM.CraftFooter(rows, #recipes), empty = empty }
end

--- Today's Needs a decision section: items AHSync flagged not selling you haven't decided on in game (A52, A53).
---@return table[]  ViewModel.Decisions' lines
local function Decisions()
  local decided = {}
  for key, r in pairs((DB.Get() or {}).rules or {}) do
    if type(r) == "table" and r.rule ~= "none" then decided[key] = true end
  end
  return VM.Decisions(Plan.NotSelling(), Plan.Record, LookOf, decided)
end

--- The Portfolio tab without a portfolio snapshot: the plan's actions due now and the addon's own options, best net
--- first, their record lines, then Needs a decision (A15, A52, A71-A74).
---@param charId string
---@param nowMs number
---@param offset number
---@param gold number
---@param minChance number
---@return table  { lines, footer, empty }
local function OwnLines(charId, nowMs, offset, gold, minChance)
  local actions, gone = CharacterActions(charId, nowMs)
  actions = WithIncoming(actions, charId)                          -- A74
  local today = VM.Today(actions, nowMs, offset, minChance, LookOf, gold)
  local lines = VM.WithNotes(VM.AddRecords(today.rows, Plan.Record))
  for _, line in ipairs(VM.WithNotes(Decisions())) do lines[#lines + 1] = line end   -- below the rows (A52)
  return { lines = lines, footer = VM.Provisional(Plan.Provisional(charId)) .. VM.TodayFooter(today, minChance, gone),
           empty = "Nothing to do in the next hour." }
end

--- The Portfolio tab with a snapshot: this character's live reselection (SPEC §4.3, A79, A84, UI16, UI19).
---@param charId string
---@param nowMs number
---@param offset number
---@param gold number
---@param minChance number
---@return table  { lines, footer, empty, top, right }
local function PortfolioLines(charId, nowMs, offset, gold, minChance)
  local Adapt, PV = AHProfit.Adapt, AHProfit.PortfolioView
  local snapshot = Adapt.Snapshot()
  local generatedAt = Market.Times()
  local tab = PV.Build(Adapt.Current(), snapshot, Plan.Positions(charId), {
    nowMs = nowMs, offsetMs = offset, nameOf = Adapt.NameOf, lookOf = LookOf, itemOf = Market.Item, totalGold = gold,
    liveGold = Adapter.Money(), charId = charId, skills = ((DB.Get() or {}).professions or {})[charId],
    dataStale = generatedAt ~= nil and nowMs - generatedAt > VM.STALE_MS, problem = Adapt.Problem() })
  local out = PV.Lines(tab, { minChance = minChance, recordOf = Plan.Record, decisions = Decisions(), snapshot = snapshot,
                              charName = VM.ShortName(charId) })
  return { lines = out.lines, footer = VM.Provisional(Plan.Provisional(charId)) .. out.footer, empty = "", top = tab.top,
           right = tab.footer.capacity, problem = tab.problem }
end

--- The lines the current tab shows, its footer and its empty-state text.
---@param tab string        The tab's key
---@param nowMs number
---@param gold number       All your gold, copper
---@param minChance number  Percent
---@return table            { lines, footer, empty, top (Portfolio's top line or nil), right (the footer's right side
---                         or nil), own (the Portfolio tab without a snapshot) }
function WindowLines.Lines(tab, nowMs, gold, minChance)
  local offset = Adapter.LocalOffsetMs(nowMs)
  local info = Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  if tab == "disenchant" then
    local best = Options.BestSkill(DB.Get() or {}, "Enchanting")
    local targets = Options.Disenchant(Market.Disenchantable(), { itemOf = LiveItem, levelsOf = LevelsOf })
    local rows = VM.DisenchantRows(targets, LookOf, best)
    return { lines = VM.WithNotes(rows), footer = VM.DisenchantFooter(rows, best),
             empty = "Nothing on the AH pays to disenchant right now." }
  end
  if tab == "craft" then return CraftLines(charId, nowMs, offset, gold) end
  if tab == "flip" then return FlipLines(charId, nowMs, offset, gold) end
  if tab == "reservations" then
    local rows = VM.Reservations(Reserves.Get(DB.Get() or {}, charId), Holdings(charId), LookOf)
    return { lines = rows, footer = VM.ReservationsFooter(rows),
             empty = "Nothing kept for yourself. Right-click an item on any tab and pick Keep." }
  end
  if tab == "inventory" then
    local actions = CharacterActions(charId, nowMs)
    local rows = VM.Inventory(Holdings(charId, nowMs), actions, LookOf, LiveItem, offset, Reserves.Get(DB.Get() or {}, charId),
                              nowMs)
    return { lines = VM.WithNotes(rows), footer = VM.InventoryFooter(rows), empty = "Nothing in your bags, bank or mail yet." }
  end
  if AHProfit.Adapt.Snapshot() then return PortfolioLines(charId, nowMs, offset, gold, minChance) end
  local own = OwnLines(charId, nowMs, offset, gold, minChance)
  own.own = true
  return own
end
