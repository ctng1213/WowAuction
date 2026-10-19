-- Specs for addon/AHProfit/Capture/Auctions.lua: your own auctions snapshot (SPEC §2.2.1, A41).

local Stubs = require("wow_stubs")

local NOW = 1760000000000

--- Log in with an AH that answers an owned-auctions query with these auctions.
---@param owned table[]  GetOwnedAuctionInfo results
---@return table         The fake C_AuctionHouse; .queries counts QueryOwnedAuctions calls
local function Boot(owned)
  Stubs.Reset()
  local house = { queries = 0 }
  house.QueryOwnedAuctions = function() house.queries = house.queries + 1 end
  house.GetNumOwnedAuctions = function() return #owned end
  house.GetOwnedAuctionInfo = function(i) return owned[i] end
  Stubs.Define("C_AuctionHouse", house)
  Stubs.Login()
  return house
end

describe("Auctions", function()
  it("A41 your active auctions are saved as rows and ends when the AH answers", function()
    local house = Boot({
      { itemLink = Stubs.ItemLink(4471), status = 0, quantity = 1, timeLeftSeconds = 11 * 3600, buyoutAmount = 80 },
      { itemLink = Stubs.ItemLink(2840), status = 0, quantity = 5, timeLeftSeconds = 1200, buyoutAmount = 950 },
      { itemLink = Stubs.ItemLink(2589), status = 1, quantity = 20, timeLeftSeconds = 0, buyoutAmount = 45 },   -- sold
    })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("AUCTION_HOUSE_AUCTION_CREATED")
    assert_eq(house.queries, 2)
    Stubs.FireEvent("OWNED_AUCTIONS_UPDATED")
    Stubs.Advance(2)
    local snap = AHProfitDB.ownAuctions["Amaya-Grommash"]
    local at = snap.at                                            -- read once the debounce fires
    assert_eq(at >= NOW, true)
    assert_eq(snap, { at = at, rows = "2840,950,5,1,1;4471,80,1,3,1",
                      ends = "2840,5," .. (at + 1200000) .. ",950;4471,1," .. (at + 11 * 3600000) .. ",80" })
  end)

  it("bands follow Classic's time left; ended auctions stop counting; no API stores nothing", function()
    local A = (Boot({}) and AHProfit.Capture.Auctions)
    assert_eq({ A.Band(1799), A.Band(1800), A.Band(7199), A.Band(43199), A.Band(43200) }, { 1, 2, 2, 3, 4 })
    local entry = { ends = "4471,1,5000,80;4471,2,9000;2840,5,3000,9" }        -- an older row has no price
    assert_eq(A.Listed(entry, 4000), { [4471] = { units = 3, endsAt = 5000 } })
    assert_eq(A.Active(entry, 4000), { { key = 4471, units = 1, endsAt = 5000, price = 80 }, { key = 4471, units = 2, endsAt = 9000 } })
    assert_eq(A.Listed(nil, 0), {})
    C_AuctionHouse.GetOwnedAuctionInfo = nil
    Stubs.FireEvent("OWNED_AUCTIONS_UPDATED")
    Stubs.Advance(2)
    assert_eq(AHProfitDB.ownAuctions["Amaya-Grommash"], nil)
  end)
end)
