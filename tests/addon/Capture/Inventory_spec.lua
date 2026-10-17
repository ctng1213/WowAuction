-- Specs for addon/AHProfit/Capture/Inventory.lua.

local Stubs = require("wow_stubs")

describe("Inventory", function()
  it("encodes slots summed per key and bound flag, ascending", function()
    Stubs.Reset()
    Stubs.Login()
    local encoded = AHProfit.Capture.Inventory.Encode({
      { key = 6948, qty = 1, bound = true }, { key = 2589, qty = 15, bound = false },
      { key = 2589, qty = 5, bound = false }, { key = 2589, qty = 1, bound = true },
    })
    assert_eq(encoded, "2589,20,0;2589,1,1;6948,1,1")
    assert_eq(AHProfit.Capture.Inventory.Encode({}), "")
  end)

  it("login snapshots the bags; bank and mail stay nil until visited", function()
    Stubs.Reset()
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2840, count = 6 } } } }
    Stubs.Login()
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"], { at = 1760000000000, bags = "2840,6,0" })
  end)

  it("a burst of bag updates captures once after the debounce", function()
    Stubs.Reset()
    Stubs.Login()
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bags, "")
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2840, count = 6 } } } }
    for bag = 0, 4 do Stubs.FireEvent("BAG_UPDATE", bag) end
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    assert_eq(Stubs.PendingTimers(), 1)
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bags, "2840,6,0")
  end)

  it("bag changes in combat are captured when combat ends", function()
    Stubs.Reset()
    Stubs.Login()
    Stubs.EnterCombat()
    Stubs.world.containers = { [0] = { size = 1, slots = { [1] = { id = 2589, count = 3 } } } }
    Stubs.FireEvent("BAG_UPDATE", 0)
    Stubs.Advance(5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bags, "")
    Stubs.LeaveCombat()
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bags, "2589,3,0")
  end)

  it("the bank is read when opened and kept after it closes", function()
    Stubs.Reset()
    Stubs.world.containers = { [-1] = { size = 2, slots = { [1] = { id = 4306, count = 10 } } } }
    Stubs.Login()
    Stubs.FireEvent("BANKFRAME_OPENED")
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bank, "4306,10,0")
    Stubs.world.containers[-1].slots[2] = { id = 4306, count = 5 }
    Stubs.FireEvent("PLAYERBANKSLOTS_CHANGED", 2)
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bank, "4306,15,0")
    Stubs.FireEvent("BANKFRAME_CLOSED")
    assert_eq(Stubs.IsRegistered("PLAYERBANKSLOTS_CHANGED"), false)
    Stubs.FireEvent("BAG_UPDATE", 0)
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bank, "4306,15,0")
  end)

  it("bag updates while the bank is open also refresh the bank", function()
    Stubs.Reset()
    Stubs.Login()
    Stubs.FireEvent("BANKFRAME_OPENED")
    Stubs.world.containers = { [5] = { size = 1, slots = { [1] = { id = 2589, count = 7 } } } }
    Stubs.FireEvent("BAG_UPDATE", 5)
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].bank, "2589,7,0")
  end)

  it("the mailbox is read on inbox updates while open, and stamps the snapshot time", function()
    Stubs.Reset()
    Stubs.Login()
    Stubs.world.inbox = { { { 2592, 20 } } }
    Stubs.Advance(60)
    Stubs.FireEvent("MAIL_SHOW")
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].mail, nil)
    Stubs.FireEvent("MAIL_INBOX_UPDATE")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].mail, "2592,20,0")
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].at, 1760000060000)
    Stubs.FireEvent("MAIL_CLOSED")
    assert_eq(Stubs.IsRegistered("MAIL_INBOX_UPDATE"), false)
  end)

  it("an inbox update that comes due after the mailbox closed writes nothing", function()
    Stubs.Reset()
    Stubs.Login()
    Stubs.world.inbox = { { { 2592, 20 } } }
    Stubs.FireEvent("MAIL_SHOW")
    Stubs.FireEvent("MAIL_INBOX_UPDATE")
    Stubs.FireEvent("MAIL_CLOSED")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.inventory["Amaya-Grommash"].mail, nil)
  end)
end)
