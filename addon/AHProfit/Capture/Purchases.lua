-- Capture/Purchases.lua: a `purchase` event for every AH or merchant purchase (SPEC §2.2.1 events,
-- A45, A46). The hooks only observe; a commodity purchase is confirmed when it succeeds, a buyout when
-- its purchase completes, a merchant purchase at once. Confirmed AH units also lower Shopping (A39).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters
local Live = AHProfit.Live

local Purchases = {}
AHProfit.Capture.Purchases = Purchases

local quote = {}               -- item id -> the unit price the AH last quoted for a commodity purchase
local quoting = nil            -- the item id of the commodity purchase being quoted
local pendingCommodity = {}    -- commodity purchase events waiting for success or failure
local pendingItems = {}        -- auction id -> buyout purchase event waiting for its completion
local listeners = {}

--- Call fn after a purchase is confirmed (the AH panel redraws).
---@param fn function
function Purchases.OnChange(fn)
  listeners[#listeners + 1] = fn
end

--- Tell the listeners.
local function Changed()
  for _, fn in ipairs(listeners) do fn() end
end

--- Add a purchase event.
---@param key number
---@param qty number
---@param price number      Unit price, copper
---@param source string     "ah" or "vendor"
---@param confirmed boolean
---@return table|nil        The event; nil without a database
local function Add(key, qty, price, source, confirmed)
  local db = DB.Get()
  if not db then return nil end
  return DB.AppendEvent(db, { type = "purchase", char = Characters.CurrentId(), at = Adapter.NowMs(), key = key,
                              qty = qty, price = price, source = source, confirmed = confirmed })
end

--- StartCommoditiesPurchase: the next price quote is for this item.
---@param itemId number
function Purchases.OnQuoteStart(itemId)
  quoting = itemId
end

--- COMMODITY_PRICE_UPDATED: the AH's unit price for the commodity being bought.
---@param _ string        The event
---@param unitPrice number
function Purchases.OnQuote(_, unitPrice)
  if quoting and unitPrice then quote[quoting] = unitPrice end
end

--- ConfirmCommoditiesPurchase: add the purchase, unconfirmed until it succeeds (A45).
---@param itemId number
---@param qty number
function Purchases.OnCommodity(itemId, qty)
  local levels = Live.Item(itemId, nil)
  local price = quote[itemId] or (levels and levels[1] and levels[1].price) or 0
  local event = Add(itemId, qty, price, "ah", false)
  if event then pendingCommodity[#pendingCommodity + 1] = event end
end

--- COMMODITY_PURCHASE_SUCCEEDED / _FAILED: settle the pending commodity purchases.
---@param event string
function Purchases.OnCommoditySettled(event)
  local succeeded = event == "COMMODITY_PURCHASE_SUCCEEDED"
  for _, e in ipairs(pendingCommodity) do
    if succeeded then e.confirmed = true end
  end
  pendingCommodity = {}
  Changed()
end

--- A buyout: add the purchase, unconfirmed until the AH completes it (A45).
---@param key number
---@param qty number
---@param price number      Unit price, copper
---@param auctionId number
function Purchases.OnBuyout(key, qty, price, auctionId)
  local event = Add(key, qty, price, "ah", false)
  if event then pendingItems[auctionId or 0] = event end
end

--- AUCTION_HOUSE_PURCHASE_COMPLETED: a buyout went through.
--- UNVERIFIED: the event's name and its auction id argument on Forever, as on retail.
---@param _ string
---@param auctionId number
function Purchases.OnBuyoutCompleted(_, auctionId)
  local event = pendingItems[auctionId or 0]
  pendingItems[auctionId or 0] = nil
  if not event then return end
  event.confirmed = true
  Changed()
end

--- A merchant purchase (A46): confirmed at once.
---@param key number
---@param qty number
---@param price number  Unit price, copper
function Purchases.OnMerchant(key, qty, price)
  Add(key, qty, price, "vendor", true)
end

--- AUCTION_HOUSE_SHOW: hook the purchase calls once the AH exists.
local function OnShow()
  pcall(Adapter.HookPurchases, Purchases.OnCommodity, Purchases.OnBuyout)
  pcall(Adapter.HookQuotes, Purchases.OnQuoteStart)
end

--- PLAYER_LOGIN: hook merchant purchases.
local function OnLogin()
  pcall(Adapter.HookMerchantBuys, Purchases.OnMerchant)
end

--- Forget pending purchases and quotes (tests).
function Purchases.Reset()
  quote, quoting, pendingCommodity, pendingItems = {}, nil, {}, {}
end

Events.Register("PLAYER_LOGIN", OnLogin)
Events.Register("AUCTION_HOUSE_SHOW", OnShow)
Events.Register("COMMODITY_PRICE_UPDATED", Purchases.OnQuote)
Events.Register("COMMODITY_PURCHASE_SUCCEEDED", Purchases.OnCommoditySettled)
Events.Register("COMMODITY_PURCHASE_FAILED", Purchases.OnCommoditySettled)
Events.Register("AUCTION_HOUSE_PURCHASE_COMPLETED", Purchases.OnBuyoutCompleted)
