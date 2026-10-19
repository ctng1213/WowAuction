-- Specs for addon/AHProfit/Capture/Mail.lua: auction mail events (SPEC §2.2.1, A48).

local Stubs = require("wow_stubs")

local NOW = 1760000000000
local DAY = 86400000

--- Log in with a mailbox of auction mails and a letter.
local function Boot()
  Stubs.Reset()
  Stubs.ITEM_NAMES[4240] = "Linen Belt"
  Stubs.world.inbox = { {}, { { 2840, 5 } }, { { 4306, 5 } }, { { 4240, 1 } }, {} }
  local headers = { { "Auction successful: Linen Cloth", 29.5 }, { "Auction expired: Copper Bar", 29 },
                    { "Auction cancelled: Silk Cloth", 30 }, { "Auction won: Linen Belt", 28 }, { "hi", 3 } }
  local invoices = { [1] = { "seller", "Linen Cloth", "Buyer", 960, 0, 9, 48, 0, 1, 0, 20 },
                     [4] = { "buyer", "Linen Belt", "Seller", 200, 200, 0, 0, 0, 0, 0, 1 } }
  Stubs.Define("GetInboxHeaderInfo", function(i) local h = headers[i]; return nil, nil, "Sender", h[1], 0, 0, h[2] end)
  Stubs.Define("GetInboxInvoiceInfo", function(i) return unpack(invoices[i] or {}) end)
  Stubs.Define("AUCTION_EXPIRED_MAIL_SUBJECT", "Auction expired: %s")
  Stubs.Define("AUCTION_REMOVED_MAIL_SUBJECT", "Auction cancelled: %s")
  Stubs.Define("AUCTION_WON_MAIL_SUBJECT", "Auction won: %s")
  Stubs.Login()
  AHProfitDB.itemNames[2589] = "1,Linen Cloth"
  Stubs.FireEvent("MAIL_SHOW")
end

--- The auction mail events, as "outcome key qty price sentAt".
---@return string[]
local function Mails()
  local out = {}
  for _, e in ipairs(AHProfitDB.events) do
    if e.type == "auction_mail" then
      out[#out + 1] = table.concat({ e.outcome, e.key, e.qty, e.price or "-", e.sentAt }, " ")
    end
  end
  return out
end

describe("Mail", function()
  it("A48 each auction mail becomes one event; letters don't; reopening the mailbox adds nothing", function()
    Boot()
    Stubs.FireEvent("MAIL_INBOX_UPDATE")
    Stubs.Advance(2)
    local at = AHProfitDB.events[1].at
    assert_eq(Mails(), { "sold 2589 20 48 " .. (at - DAY / 2), "expired 2840 5 - " .. (at - DAY),
                         "cancelled 4306 5 - " .. at, "won 4240 1 200 " .. (at - 2 * DAY) })
    Stubs.FireEvent("MAIL_INBOX_UPDATE")
    Stubs.Advance(2)
    assert_eq(#Mails(), 4)
    Stubs.ITEM_NAMES[4240] = nil
  end)

  it("fingerprints are forgotten 31 days after the mail was sent; an unknown sold item is skipped", function()
    Boot()
    local Mail = AHProfit.Capture.Mail
    local db = { itemNames = {}, events = {} }
    assert_eq(Mail.Record(db, { { outcome = "expired", key = 1, qty = 1, daysLeft = 30 } }, NOW), 1)
    assert_eq(Mail.Record(db, { { outcome = "expired", key = 1, qty = 1, daysLeft = 30 } }, NOW), 0)
    assert_eq(Mail.Record(db, {}, NOW + 31 * DAY), 0)
    assert_eq(next(db.mailSeen), nil)
    assert_eq({ Mail.KeyByName({ itemNames = { [5] = "1,X", [3] = "2,X" } }, "X"), Mail.KeyByName({}, "Y") }, { 3, nil })
    AHProfitDB.itemNames[2589] = nil
    AHProfitDB.events = {}
    Stubs.FireEvent("MAIL_INBOX_UPDATE")
    Stubs.Advance(2)
    assert_eq(Mails()[1]:match("^sold") == nil, true)               -- the sold invoice can't be named
    Stubs.ITEM_NAMES[4240] = nil
  end)

  it("A54 taking a sold mail's money is one payout; a repeat take and a letter's money add none", function()
    Stubs.Reset()
    Stubs.world.inbox = { {}, {} }
    local money = { 970, 500 }
    Stubs.Define("GetInboxHeaderInfo", function(i) return nil, nil, "Sender", "Mail " .. i, money[i], 0, 29 end)
    Stubs.Define("GetInboxInvoiceInfo", function(i)
      if i == 1 then return "seller", "Copper Rod", "Buyer", 1000, 1000, 20, 50, 0, 0, 0, 1 end
    end)
    Stubs.Define("TakeInboxMoney", function() end)
    Stubs.Login()
    AHProfitDB.itemNames[6217] = "1,Copper Rod"
    TakeInboxMoney(1)
    TakeInboxMoney(1)                                              -- fired again before the mail went
    TakeInboxMoney(2)                                              -- a letter's gold isn't a payout
    local payouts = {}
    for _, e in ipairs(AHProfitDB.events) do
      if e.type == "payout" then payouts[#payouts + 1] = table.concat({ e.key, e.qty, e.money, e.deposit, e.at - e.sentAt }, " ") end
    end
    assert_eq(payouts, { "6217 1 970 20 " .. DAY })                   -- 29 of 30 days left: sent a day ago
    assert_eq(AHProfit.Events.Traces(), {
      "take 1 money=970 invoice=seller item=Copper Rod -> payout", "take 1 money=970 invoice=seller item=Copper Rod -> seen before",
      "take 2 money=500 invoice=nil item=nil -> not a sold mail" })
  end)

  it("A75 each item you mail to your character is a mail_sent event, confirmed on success, dropped on failure", function()
    Stubs.Reset()
    Stubs.world.player.name = "Brugan"
    Stubs.Login()
    --- The mail_sent events, as "to key qty confirmed".
    ---@return string[]
    local function Sent()
      local out = {}
      for _, e in ipairs(AHProfitDB.events) do
        if e.type == "mail_sent" then out[#out + 1] = table.concat({ e.to, e.key, e.qty, tostring(e.confirmed) }, " ") end
      end
      return out
    end
    Stubs.world.sendMail = { [1] = { id = 2589, count = 20 }, [3] = { id = 2840, count = 6 } }
    Stubs.FireEvent("MAIL_SEND_INFO_UPDATE")                       -- the two slots are filled
    SendMail("amaya", "mats", "")                                  -- the hook runs right after the call
    Stubs.world.sendMail = {}
    Stubs.FireEvent("MAIL_SEND_INFO_UPDATE")                       -- the client empties the slots
    assert_eq(Sent(), { "Amaya-Grommash 2589 20 false", "Amaya-Grommash 2840 6 false" })
    Stubs.FireEvent("MAIL_SEND_SUCCESS")
    assert_eq(Sent(), { "Amaya-Grommash 2589 20 true", "Amaya-Grommash 2840 6 true" })
    Stubs.world.sendMail = { [1] = { id = 2840, count = 4 } }
    Stubs.FireEvent("MAIL_SEND_INFO_UPDATE")
    SendMail("Amaya", "more", "")
    Stubs.FireEvent("MAIL_FAILED")                                 -- the send failed: its event goes
    assert_eq(#Sent(), 2)
    Stubs.world.sendMail = {}
    Stubs.FireEvent("MAIL_SEND_INFO_UPDATE")
    SendMail("Amaya", "a letter", "hi")                            -- nothing attached: nothing written
    SendMail("Cara-Stormrage", "", "")                             -- another realm's character: not one of yours here
    Stubs.FireEvent("MAIL_SEND_SUCCESS")                           -- nothing waits: changes nothing
    assert_eq({ #Sent(), #Stubs.world.sent }, { 2, 4 })
    assert_eq(AHProfit.Capture.Mail.RecipientId(nil, "Grommash"), nil)
    assert_eq(AHProfit.Events.Traces(), { "sent 2 to Amaya-Grommash", "sent 1 to Amaya-Grommash" })
  end)
end)
