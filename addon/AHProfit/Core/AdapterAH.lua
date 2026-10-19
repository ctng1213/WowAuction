-- Core/AdapterAH.lua: the auction house half of the Adapter (CLAUDE.md, SPEC §5): the panel's
-- window, searches and the Sell tab, and reading the replicate scan. Split from Core/Adapter.lua
-- for size; it adds to the same AHProfit.Adapter table, so callers see one Adapter.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

-- ------------------------------------------------------------------ auction house panel (SPEC §4.3)

--- The auction house window, to dock the AHProfit panel to.
--- UNVERIFIED: Forever has the modern AuctionHouseFrame (no QueryAuctionItems, verified 2026-10-18).
---@return table|nil
function Adapter.AuctionFrame()
  return _G.AuctionHouseFrame or _G.AuctionFrame
end

--- Search the AH for an item name, as typing it in the AH's search box and pressing Search does.
--- Called from a click, so it runs on a hardware event.
--- UNVERIFIED: Forever's AuctionHouseFrame.SearchBar mixin (SetSearchText, StartSearch), as on retail.
---@param name string
---@return boolean  true when a search was started
function Adapter.SearchAH(name)
  local frame = _G.AuctionHouseFrame
  local bar = frame and frame.SearchBar
  if bar and bar.SetSearchText and bar.StartSearch then
    bar:SetSearchText(name)
    bar:StartSearch()
    return true
  end
  return false
end

--- The browse results of the last AH search: each item's cheapest unit price and units listed.
--- UNVERIFIED: C_AuctionHouse.GetBrowseResults rows { itemKey = { itemID }, minPrice, totalQuantity }.
---@return table[]  { itemId, minPrice, units }; empty when the call is missing
function Adapter.ReadBrowseResults()
  local out = {}
  if not (C_AuctionHouse and C_AuctionHouse.GetBrowseResults) then return out end
  for _, r in ipairs(C_AuctionHouse.GetBrowseResults() or {}) do
    if r.itemKey and r.itemKey.itemID and r.minPrice then
      out[#out + 1] = { itemId = r.itemKey.itemID, minPrice = r.minPrice, units = r.totalQuantity or 0 }
    end
  end
  return out
end

-- ------------------------------------------------------------------ the Sell tab (SPEC §4.3, A29)

local lastSell = nil   -- the sell frame FillSell last filled, for RefillSellPrice

--- The Sell tab frame that takes an item: the commodities one or the single-item one.
--- UNVERIFIED: Forever's AuctionHouseFrame has retail's CommoditiesSellFrame and ItemSellFrame, and
--- C_AuctionHouse.GetItemCommodityStatus answers Enum.ItemCommodityStatus.Commodity for stackables.
---@param frame table     AuctionHouseFrame
---@param location table  ItemLocation
---@return table|nil
local function SellFrame(frame, location)
  local status = C_AuctionHouse.GetItemCommodityStatus and C_AuctionHouse.GetItemCommodityStatus(location)
  local commodity = Enum and Enum.ItemCommodityStatus and status == Enum.ItemCommodityStatus.Commodity
  return commodity and frame.CommoditiesSellFrame or frame.ItemSellFrame
end

--- Choose the auction duration on a sell frame (A56).
--- UNVERIFIED: the sell frame's DurationDropDown:SetDuration(index), index 1/2/3 = 2/8/24 h, as on retail.
---@param sell table    The sell frame
---@param hours number  2, 8 or 24
---@return boolean      true when set
local function ApplyDuration(sell, hours)
  local index = ({ [2] = 1, [8] = 2, [24] = 3 })[hours]
  local drop = sell.DurationDropDown
  if not (index and drop and drop.SetDuration) then return false end
  return (pcall(drop.SetDuration, drop, index))
end

--- Set a sell frame's unit price.
--- UNVERIFIED: the sell frame's SetPrice, else its PriceInput:SetAmount (retail's money input).
---@param sell table
---@param price number  Copper
---@return boolean      true when one of them was there
local function ApplyPrice(sell, price)
  if sell.SetPrice then sell:SetPrice(price) return true end
  if sell.PriceInput and sell.PriceInput.SetAmount then sell.PriceInput:SetAmount(price) return true end
  return false
end

--- Set a sell frame's quantity.
--- UNVERIFIED: the sell frame's SetQuantity, else its QuantityInput:SetQuantity.
---@param sell table
---@param units number
local function ApplyQuantity(sell, units)
  if sell.SetQuantity then sell:SetQuantity(units)
  elseif sell.QuantityInput and sell.QuantityInput.SetQuantity then sell.QuantityInput:SetQuantity(units) end
end

--- Put a bag item on the AH's Sell tab with a quantity and unit price, for you to check and post with
--- the AH's own Post button: nothing is posted here. Called from a click.
--- UNVERIFIED: AuctionHouseFrame:SetPostItem(itemLocation), ItemLocation:CreateFromBagAndSlot and
--- C_AuctionHouse.IsSellItemValid / GetAvailablePostCount, as on retail.
---@param bag number
---@param slot number
---@param qty number    Units wanted
---@param price number  Unit price, copper
---@param hours number|nil  Auction duration, hours (A56); nil leaves the AH's own
---@return number|nil   Units put up (at most what can be posted); nil when the Sell tab can't take it
---@return string|nil   Why not: "unavailable" (no Sell tab API) or "invalid" (can't be sold on the AH)
---@return boolean|nil  Whether the duration was set (nil when none was asked for)
function Adapter.FillSell(bag, slot, qty, price, hours)
  local frame = _G.AuctionHouseFrame
  if not (frame and frame.SetPostItem and ItemLocation and ItemLocation.CreateFromBagAndSlot and C_AuctionHouse) then
    return nil, "unavailable"
  end
  local location = ItemLocation:CreateFromBagAndSlot(bag, slot)
  if C_AuctionHouse.IsSellItemValid and not C_AuctionHouse.IsSellItemValid(location) then return nil, "invalid" end
  frame:SetPostItem(location)
  local sell = SellFrame(frame, location)
  if not sell then return nil, "unavailable" end
  local available = C_AuctionHouse.GetAvailablePostCount and C_AuctionHouse.GetAvailablePostCount(location) or qty
  local units = math.max(1, math.min(qty, available))
  ApplyQuantity(sell, units)
  ApplyPrice(sell, price)
  lastSell = sell
  return units, nil, hours ~= nil and ApplyDuration(sell, hours)
end

--- Put the suggested price back after the AH fills in its own from a search (A29).
---@param price number  Copper
---@return boolean      true when a filled sell frame took it
function Adapter.RefillSellPrice(price)
  return lastSell ~= nil and ApplyPrice(lastSell, price)
end

-- ------------------------------------------------------------------ your own auctions (SPEC §2.2.1, A41)

--- Ask the server for your own auctions; the answer is OWNED_AUCTIONS_UPDATED.
--- UNVERIFIED: C_AuctionHouse.QueryOwnedAuctions(sorts), as on retail.
---@return boolean  true when the call went out without an error
function Adapter.QueryOwnedAuctions()
  local AH = _G.C_AuctionHouse
  if not (AH and AH.QueryOwnedAuctions) then return false end
  return (pcall(AH.QueryOwnedAuctions, {}))
end

--- Your active auctions, as the last query answered.
--- UNVERIFIED: C_AuctionHouse.GetNumOwnedAuctions and GetOwnedAuctionInfo(i), 1-based, returning
--- { itemLink, itemKey = { itemID }, status (0 active, 1 sold), quantity, timeLeftSeconds, buyoutAmount
--- (per unit for a commodity; an item auction is one unit) }, as on retail.
---@return table[]|nil  { key, price (copper each), qty, leftSeconds } per active auction; nil without the API
function Adapter.ReadOwnedAuctions()
  local AH = _G.C_AuctionHouse
  if not (AH and AH.GetNumOwnedAuctions and AH.GetOwnedAuctionInfo) then return nil end
  local out = {}
  for i = 1, AH.GetNumOwnedAuctions() or 0 do
    local info = AH.GetOwnedAuctionInfo(i)
    if info and info.status == 0 and info.buyoutAmount then
      local key = (info.itemLink and Adapter.ItemKey(info.itemLink)) or (info.itemKey and info.itemKey.itemID)
      if key then
        out[#out + 1] = { key = key, price = info.buyoutAmount, qty = info.quantity or 1, leftSeconds = info.timeLeftSeconds or 0 }
      end
    end
  end
  return out
end

-- ------------------------------------------------------------------ purchases (SPEC §4.3, A39)

local purchasesHooked, postsHooked = false, false
-- The AH's duration 1/2/3 is 2/8/24 h, the engine's durations (verified on Forever 2026-10-18; SPEC §6 item 4).
local DURATION_HOURS = { 2, 8, 24 }

--- Watch AH posts without changing them (A44).
--- UNVERIFIED: C_AuctionHouse.PostCommodity(itemLocation, duration, quantity, unitPrice) and
--- PostItem(itemLocation, duration, quantity, bid, buyout), and ItemLocation:GetBagAndSlot(), as on retail.
---@param fn function  (bag, slot, quantity, unit price, duration hours) after each post call
---@return boolean     true when hooked
function Adapter.HookPosts(fn)
  local AH = _G.C_AuctionHouse
  if postsHooked or not (AH and hooksecurefunc) then return postsHooked end
  --- The bag and slot an item location points at, or nils.
  ---@param location table
  ---@return number|nil, number|nil
  local function Where(location)
    if type(location) == "table" and location.GetBagAndSlot then return location:GetBagAndSlot() end
    return nil, nil
  end
  if AH.PostCommodity then
    hooksecurefunc(AH, "PostCommodity", function(location, duration, quantity, unitPrice)
      local bag, slot = Where(location)
      fn(bag, slot, quantity, unitPrice, DURATION_HOURS[duration] or 24)
    end)
    postsHooked = true
  end
  if AH.PostItem then
    hooksecurefunc(AH, "PostItem", function(location, duration, quantity, _, buyout)
      local bag, slot = Where(location)
      if buyout then fn(bag, slot, quantity, math.floor(buyout / math.max(1, quantity or 1)), DURATION_HOURS[duration] or 24) end
    end)
    postsHooked = true
  end
  return postsHooked
end

--- Watch AH purchases without changing them: a commodity purchase confirmed, or an item auction bid on at
--- its buyout. Hooked once; a bid below the buyout is not a purchase.
--- UNVERIFIED: C_AuctionHouse.ConfirmCommoditiesPurchase(itemID, quantity), PlaceBid(auctionID, bidAmount) and
--- GetAuctionInfoByID(auctionID) -> { itemKey = { itemID }, itemLink, quantity, buyoutAmount }, as on retail.
---@param onCommodity function  (itemId, quantity) when a commodity purchase is confirmed (it may still fail)
---@param onItem function       (key, quantity, unit price, auctionId) when an item auction is bought out
---@return boolean              true when at least one hook is in place
function Adapter.HookPurchases(onCommodity, onItem)
  local AH = _G.C_AuctionHouse
  if purchasesHooked or not (AH and hooksecurefunc) then return purchasesHooked end
  if AH.ConfirmCommoditiesPurchase then
    hooksecurefunc(AH, "ConfirmCommoditiesPurchase", function(itemId, quantity) onCommodity(itemId, quantity) end)
    purchasesHooked = true
  end
  if AH.PlaceBid and AH.GetAuctionInfoByID then
    hooksecurefunc(AH, "PlaceBid", function(auctionId, bid)
      local info = AH.GetAuctionInfoByID(auctionId)
      if info and info.itemKey and info.buyoutAmount and bid and bid >= info.buyoutAmount then
        local key = (info.itemLink and Adapter.ItemKey(info.itemLink)) or info.itemKey.itemID
        local qty = info.quantity or 1
        onItem(key, qty, math.floor(info.buyoutAmount / math.max(1, qty)), auctionId)
      end
    end)
    purchasesHooked = true
  end
  return purchasesHooked
end

local quotesHooked = false

--- Watch the start of a commodity purchase, whose price quote (COMMODITY_PRICE_UPDATED) follows (A45).
--- UNVERIFIED: C_AuctionHouse.StartCommoditiesPurchase(itemID, quantity), as on retail.
---@param fn function  (itemId) when a purchase is started
---@return boolean     true when hooked
function Adapter.HookQuotes(fn)
  local AH = _G.C_AuctionHouse
  if quotesHooked or not (AH and hooksecurefunc and AH.StartCommoditiesPurchase) then return quotesHooked end
  hooksecurefunc(AH, "StartCommoditiesPurchase", function(itemId) fn(itemId) end)
  quotesHooked = true
  return true
end

-- ------------------------------------------------------------------ replicate scan (SPEC §4.2)

--- A value the client marks secret can't be read; treat it as missing.
---@param value any  A value returned by a WoW API
---@return any       The value; nil when it is secret
local function Plain(value)
  if issecretvalue and issecretvalue(value) then return nil end
  return value
end

--- Whether this client can replicate the whole auction house.
---@return boolean  true when C_AuctionHouse has ReplicateItems and its readers
function Adapter.HasReplicate()
  local AH = C_AuctionHouse
  return type(AH) == "table" and type(AH.ReplicateItems) == "function"
    and type(AH.GetNumReplicateItems) == "function" and type(AH.GetReplicateItemInfo) == "function"
end

--- Ask the server for the whole auction house; the answer is REPLICATE_ITEM_LIST_UPDATE.
---@return boolean  true when the call went out without an error
function Adapter.RequestReplicate()
  return (pcall(C_AuctionHouse.ReplicateItems))
end

--- Number of auctions in the last replicate answer.
---@return number  0 when there is no answer yet (or it can't be read)
function Adapter.NumReplicateItems()
  local n = Plain(C_AuctionHouse.GetNumReplicateItems())
  return type(n) == "number" and math.floor(n) or 0
end

--- The numeric market fields of GetReplicateItemInfo, by position.
--- UNVERIFIED (Forever 1.60.1): name, texture, count, quality, usable, level, levelType,
--- minBid, minIncrement, buyoutPrice, bidAmount, highBidder, bidderFull, owner, ownerFull,
--- saleStatus, itemId (AuctionHouseFYI reads the same positions).
---@return number|nil count, number|nil buyout (whole stack, 0 = none), number|nil itemId
local function PickInfo(_, _, count, _, _, _, _, _, _, buyout, _, _, _, _, _, _, itemId)
  return Plain(count), Plain(buyout), Plain(itemId)
end

local WEAPON_CLASS, ARMOR_CLASS = 2, 4   -- item class ids; only these can roll a random suffix

--- Item key for an auction whose link isn't available yet (the item isn't cached): its item id,
--- or -itemId for a weapon or armor (or an item whose class is unknown), which could carry a
--- random suffix we can't see (SPEC §2.2.1, §4.2, A13).
--- UNVERIFIED: GetItemInfoInstant (or C_Item.GetItemInfoInstant) answers without the item cache;
--- its 6th return is the class id.
---@param itemId number  The auction's item id
---@return number        key
local function KeyWithoutLink(itemId)
  local instant = GetItemInfoInstant or (C_Item and C_Item.GetItemInfoInstant)
  local classId = instant and select(6, instant(itemId))
  -- Unknown class (no API, or no answer) counts as possibly suffixed: never priced as its base item.
  if type(classId) == "number" and classId ~= WEAPON_CLASS and classId ~= ARMOR_CLASS then return itemId end
  return -itemId
end

--- The item key of an auction: from its link (cached per scan), else from its item id.
---@param link string|nil   GetReplicateItemLink result
---@param itemId number|nil The auction's item id
---@param keyCache table    link -> key (false = not an item link)
---@return number|nil       key; nil when neither gives one
---@return boolean          true when keyed by item id because the link was missing
local function AuctionKey(link, itemId, keyCache)
  if type(link) == "string" then
    local key = keyCache[link]
    if key == nil then
      key = Adapter.ItemKey(link) or false
      keyCache[link] = key
    end
    return key or nil, false
  end
  if type(itemId) == "number" and itemId >= 1 then return KeyWithoutLink(itemId), true end
  return nil, false
end

--- One auction of the replicate answer, as an order-book entry (SPEC §4.2 Store).
--- Left out, with the reason: "pet" (battle pet), "count" (no readable stack size),
--- "buyout" (bid only), "band" (time left not 1-4; volume needs it, §3.3.1) and
--- "link" (neither a usable link nor an item id). Secret values read as missing.
---@param i number         0-based auction index
---@param keyCache table   link -> item key (false = not an item) for this scan; links repeat a lot,
---                        so this saves parsing the same link thousands of times
---@return number|nil key, number|string unitPrice (copper, floor(buyout / count)) or the reason it was
---        left out, number|string|nil stack or the raw time left (as text, for "band"), number|nil band (1-4),
---        boolean|nil true when keyed by item id because the link was missing
function Adapter.ReadReplicateItem(i, keyCache)
  local AH = C_AuctionHouse
  if AH.GetReplicateItemBattlePetInfo then
    local creature = AH.GetReplicateItemBattlePetInfo(i)
    if (issecretvalue and issecretvalue(creature)) or (creature ~= nil and creature ~= 0) then return nil, "pet" end
  end
  local count, buyout, itemId = PickInfo(AH.GetReplicateItemInfo(i))
  if type(count) ~= "number" or count < 1 then return nil, "count" end
  if type(buyout) ~= "number" or buyout < 1 then return nil, "buyout" end
  -- Verified 2026-10-17 (A12): Forever returns 1 short .. 4 very long, as Classic's
  -- GetAuctionItemTimeLeft did; not retail's 0..3 (no 0 in 41,062 auctions).
  local left = AH.GetReplicateItemTimeLeft and Plain(AH.GetReplicateItemTimeLeft(i))
  if left ~= 1 and left ~= 2 and left ~= 3 and left ~= 4 then return nil, "band", tostring(left) end
  local key, byId = AuctionKey(Plain(AH.GetReplicateItemLink(i)), itemId, keyCache)
  if not key then return nil, "link" end
  return key, math.floor(buyout / count), count, left, byId
end
