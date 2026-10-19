-- Capture/Posts.lua: a `post` event for every auction you post, confirmed when the AH creates it
-- (SPEC §2.2.1 events, A44). The hooks only observe the post calls.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters
local Fees = AHProfit.Engine.Fees

local Posts = {}
AHProfit.Capture.Posts = Posts

local slots = {}               -- "bag:slot" -> key, from before the last bag change (the post empties the slot)
local unconfirmed = {}         -- post events waiting for AUCTION_HOUSE_AUCTION_CREATED, oldest first

--- Remember which item is in each bag slot; called after every bag snapshot.
function Posts.RememberSlots()
  slots = Adapter.ReadBagSlots()
end

--- A post call: add its event, unconfirmed (A44).
---@param bag number|nil
---@param slot number|nil
---@param qty number       Units posted
---@param price number     Unit price, copper
---@param hours number     Auction duration
function Posts.OnPost(bag, slot, qty, price, hours)
  local db = DB.Get()
  local key = bag and slots[bag .. ":" .. slot]
  if not (db and key and qty and price) then return end
  local vendorSell = select(3, Adapter.ItemInfo(key < 0 and -key or key % 2147483648)) or 0
  unconfirmed[#unconfirmed + 1] = DB.AppendEvent(db, {
    type = "post", char = Characters.CurrentId(), at = Adapter.NowMs(), key = key, stack = qty, price = price,
    durationH = hours, deposit = Fees.DepositCopper(vendorSell, qty, hours), confirmed = false })
end

--- AUCTION_HOUSE_AUCTION_CREATED: the oldest unconfirmed post went up.
--- UNVERIFIED: the event's name on Forever, as on retail.
function Posts.OnCreated()
  local event = table.remove(unconfirmed, 1)
  if event then event.confirmed = true end
end

--- AUCTION_HOUSE_SHOW: hook the post calls once the AH exists.
local function OnShow()
  pcall(Adapter.HookPosts, Posts.OnPost)
  Posts.RememberSlots()
end

AHProfit.Capture.Inventory.OnChange(function(location) if location == "bags" then Posts.RememberSlots() end end)
Events.Register("AUCTION_HOUSE_SHOW", OnShow)
Events.Register("AUCTION_HOUSE_AUCTION_CREATED", Posts.OnCreated)
