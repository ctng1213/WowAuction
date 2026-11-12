-- Specs for addon/AHProfit/Capture/Posts.lua: post events (SPEC §2.2.1, A44).

local Stubs = require("wow_stubs")

--- An ItemLocation stub for a bag slot.
---@param bag number
---@param slot number
---@return table
local function At(bag, slot)
  return { GetBagAndSlot = function() return bag, slot end }
end

--- Log in carrying 20 Linen and a pair of gloves, with an AH whose post calls do nothing.
---@return table  The fake C_AuctionHouse
local function Boot()
  Stubs.Reset()
  Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 }, [2] = { id = 6585, count = 1 } } } }
  local house = { PostCommodity = function() end, PostItem = function() end }
  Stubs.Define("C_AuctionHouse", house)
  Stubs.Login()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return house
end

describe("Posts", function()
  it("A44 a post adds an unconfirmed event from the slot's item; the AH's confirmation confirms it", function()
    local house = Boot()
    house.PostCommodity(At(0, 1), 3, 20, 55)                 -- 24 h
    house.PostItem(At(0, 2), 1, 1, nil, 900)                  -- 2 h, never confirmed
    Stubs.FireEvent("AUCTION_HOUSE_AUCTION_CREATED")
    local linen, gloves = AHProfitDB.events[1], AHProfitDB.events[2]
    assert_eq({ linen.type, linen.key, linen.stack, linen.price, linen.durationH, linen.deposit, linen.confirmed },
              { "post", 2589, 20, 55, 24, 36, true })                 -- 3c vendor × 20 × 60% = 36c, over the 1c minimum
    assert_eq({ gloves.key, gloves.price, gloves.durationH, gloves.confirmed }, { 6585, 900, 2, false })
  end)

  it("a post from an unknown slot, or with no buyout, adds nothing", function()
    local house = Boot()
    house.PostCommodity(At(3, 9), 2, 5, 10)
    house.PostCommodity({}, 2, 5, 10)
    house.PostItem(At(0, 2), 1, 1, 500, nil)
    assert_eq(#AHProfitDB.events, 0)
  end)
end)
