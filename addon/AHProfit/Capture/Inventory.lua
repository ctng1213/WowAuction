-- Capture/Inventory.lua: bags, bank and mail snapshots per character, with a
-- bound flag, as compact strings "key,qty,bound" (SPEC §2.2, §2.2.1).
-- Bank and mail are only readable while open, so they keep their last
-- snapshot otherwise (nil until first visited).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Inventory = {}
AHProfit.Capture.Inventory = Inventory

local bankOpen = false
local mailOpen = false

--- Sum slots per (key, bound) and encode them, ascending by key then bound (SPEC §2.2.1).
---@param items table[]  { key, qty, bound } per slot or attachment
---@return string        e.g. "2840,20,0;6948,1,1"; "" when empty
function Inventory.Encode(items)
  local totals, rows = {}, {}
  for _, item in ipairs(items) do
    local bound = item.bound and 1 or 0
    local id = item.key * 2 + bound
    local row = totals[id]
    if row then
      row[2] = row[2] + item.qty
    else
      row = { item.key, item.qty, bound }
      totals[id] = row
      rows[#rows + 1] = row
    end
  end
  table.sort(rows, function(a, b) return a[1] < b[1] or (a[1] == b[1] and a[3] < b[3]) end)
  return DB.EncodeRows(rows)
end

-- Time field of each location (SPEC §2.2.1: each location has its own time).
local TIME_FIELD = { bags = "bagsAt", bank = "bankAt", mail = "mailAt" }

--- Replace one location of the character's inventory, with its own capture time.
--- Nothing is written when the read was incomplete (SPEC §2.2.1: complete snapshots only);
--- the next update event retries.
---@param db table          The database; db.inventory[charId] changes
---@param charId string     "Name-Realm"
---@param location string   "bags", "bank" or "mail"
---@param items table[]|nil { key, qty, bound } as read by the Adapter; nil when incomplete
---@return boolean          true when written
function Inventory.CaptureLocation(db, charId, location, items)
  if not items then return false end
  local entry = db.inventory[charId] or {}
  db.inventory[charId] = entry
  entry[location] = Inventory.Encode(items)
  entry[TIME_FIELD[location]] = Adapter.NowMs()
  return true
end

local OnItemInfo

--- Capture the bags (and the bank, if open) into the live database.
--- An incomplete bag read waits for item info to arrive and retries once.
local function CaptureBagsLive()
  local db = DB.Get()
  if not db then return end
  local charId = Characters.CurrentId()
  if not Inventory.CaptureLocation(db, charId, "bags", Adapter.ReadBags()) then
    -- UNVERIFIED: GET_ITEM_INFO_RECEIVED fires when an uncached item's info arrives.
    Events.Register("GET_ITEM_INFO_RECEIVED", OnItemInfo)
  end
  if bankOpen then Inventory.CaptureLocation(db, charId, "bank", Adapter.ReadBank()) end
end

--- GET_ITEM_INFO_RECEIVED after an incomplete bag read: stop listening and retry, debounced.
function OnItemInfo()
  Events.Unregister("GET_ITEM_INFO_RECEIVED", OnItemInfo)
  Events.Debounce("inventory.bags", CaptureBagsLive)
end

--- Capture the bank into the live database, if it is still open.
local function CaptureBankLive()
  local db = DB.Get()
  if db and bankOpen then
    Inventory.CaptureLocation(db, Characters.CurrentId(), "bank", Adapter.ReadBank())
  end
end

--- Capture the inbox into the live database, if the mailbox is still open.
local function CaptureMailLive()
  local db = DB.Get()
  if db and mailOpen then
    Inventory.CaptureLocation(db, Characters.CurrentId(), "mail", Adapter.ReadInbox())
  end
end

--- PLAYER_LOGIN: snapshot the bags right away.
local function OnLogin()
  CaptureBagsLive()
end

--- BAG_UPDATE and BAG_UPDATE_DELAYED: bursty, so capture once after the debounce (SPEC §5.1).
local function OnBagsChanged()
  Events.Debounce("inventory.bags", CaptureBagsLive)
end

--- PLAYERBANKSLOTS_CHANGED while the bank is open: debounced bank capture.
local function OnBankChanged()
  Events.Debounce("inventory.bank", CaptureBankLive)
end

--- MAIL_INBOX_UPDATE while the mailbox is open: debounced mail capture.
local function OnInboxChanged()
  Events.Debounce("inventory.mail", CaptureMailLive)
end

--- BANKFRAME_OPENED / BANKFRAME_CLOSED: read the bank while open, listen to it only then.
---@param event string  The event name
local function OnBankFrame(event)
  bankOpen = event == "BANKFRAME_OPENED"
  if bankOpen then
    Events.Register("PLAYERBANKSLOTS_CHANGED", OnBankChanged)
    Events.RunWhenSafe("inventory.bank", CaptureBankLive)
  else
    Events.Unregister("PLAYERBANKSLOTS_CHANGED", OnBankChanged)
  end
end

--- MAIL_SHOW / MAIL_CLOSED: listen to the inbox only while the mailbox is open.
--- The inbox loads after MAIL_SHOW, so capture waits for MAIL_INBOX_UPDATE.
---@param event string  The event name
local function OnMailFrame(event)
  mailOpen = event == "MAIL_SHOW"
  if mailOpen then
    Events.Register("MAIL_INBOX_UPDATE", OnInboxChanged)
  else
    Events.Unregister("MAIL_INBOX_UPDATE", OnInboxChanged)
  end
end

Events.Register("PLAYER_LOGIN", OnLogin)
Events.Register("BAG_UPDATE", OnBagsChanged)
Events.Register("BAG_UPDATE_DELAYED", OnBagsChanged)
Events.Register("BANKFRAME_OPENED", OnBankFrame)
Events.Register("BANKFRAME_CLOSED", OnBankFrame)
Events.Register("MAIL_SHOW", OnMailFrame)
Events.Register("MAIL_CLOSED", OnMailFrame)
