-- Capture/Mail.lua: an `auction_mail` event for each sold, expired, cancelled or won mail in the
-- mailbox, once per mail (SPEC §2.2.1 events, A48). AHSync resolves your posts with them.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Mail = {}
AHProfit.Capture.Mail = Mail

Mail.KEEP_DAYS = 30            -- UNVERIFIED: how long the client keeps an auction mail
Mail.SEEN_DAYS = 31            -- how long a mail's fingerprint is remembered after it was sent
local DAY_MS = 86400000

--- An item's key from its name, from the names seen in your bags (itemNames).
---@param db table
---@param name string|nil
---@return number|nil  The smallest key with that name; nil when none
function Mail.KeyByName(db, name)
  local best
  for key, text in pairs(db.itemNames or {}) do
    local item = text:match("^%d+,(.+)$")
    if item == name and (not best or key < best) then best = key end
  end
  return best
end

--- One mail's fingerprint, stable across mailbox visits.
---@param m table        From Adapter.ReadAuctionMail
---@param sentAt number  ms
---@return string        "outcome:key:qty:price:sent minute"
function Mail.Fingerprint(m, sentAt)
  return table.concat({ m.outcome, m.key, m.qty, m.price or "", math.floor(sentAt / 60000) * 60000 }, ":")
end

--- Add an event for every auction mail not seen before, and forget fingerprints past SEEN_DAYS.
---@param db table
---@param mails table[]  From Adapter.ReadAuctionMail
---@param nowMs number
---@return number        Events added
function Mail.Record(db, mails, nowMs)
  db.mailSeen = db.mailSeen or {}
  for fp, untilMs in pairs(db.mailSeen) do
    if untilMs <= nowMs then db.mailSeen[fp] = nil end
  end
  local added = 0
  for _, m in ipairs(mails) do
    local sentAt = nowMs - math.floor((Mail.KEEP_DAYS - m.daysLeft) * DAY_MS)
    local fp = Mail.Fingerprint(m, sentAt)
    if not db.mailSeen[fp] then
      db.mailSeen[fp] = sentAt + Mail.SEEN_DAYS * DAY_MS
      DB.AppendEvent(db, { type = "auction_mail", char = Characters.CurrentId(), at = nowMs, outcome = m.outcome,
                           key = m.key, qty = m.qty, price = m.price, sentAt = sentAt })
      added = added + 1
    end
  end
  return added
end

--- Read the mailbox's auction mail into events.
local function CaptureLive()
  local db = DB.Get()
  if not db then return end
  Mail.Record(db, Adapter.ReadAuctionMail(function(name) return Mail.KeyByName(db, name) end), Adapter.NowMs())
end

--- MAIL_INBOX_UPDATE: the inbox loaded or changed; read it once it settles (debounced).
local function OnInbox()
  Events.Debounce("mail.auctions", CaptureLive)
end

--- MAIL_SHOW / MAIL_CLOSED: listen to the inbox only while the mailbox is open.
---@param event string
local function OnMailFrame(event)
  if event == "MAIL_SHOW" then Events.Register("MAIL_INBOX_UPDATE", OnInbox)
  else Events.Unregister("MAIL_INBOX_UPDATE", OnInbox) end
end

Events.Register("MAIL_SHOW", OnMailFrame)
Events.Register("MAIL_CLOSED", OnMailFrame)
