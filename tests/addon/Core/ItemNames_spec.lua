-- Specs for addon/AHProfit/Core/ItemNames.lua: one itemNames row, read from both ends (SPEC §2.2.1; A92).

local Stubs = require("wow_stubs")

describe("ItemNames", function()
  it("A92 a row reads from both ends: names with commas, unknown numbers, and rows from before A92", function()
    Stubs.Reset()
    Stubs.Login()
    local N = AHProfit.ItemNames
    assert_eq({ N.Parse("1,Heavy Grinding Stone,50,7") }, { 1, "Heavy Grinding Stone", 50, 7 })
    assert_eq({ N.Parse("2,Sword, Long,120,2") }, { 2, "Sword, Long", 120, 2 })          -- a comma in the name
    assert_eq({ N.Parse("1,Wool Cloth,,7") }, { 1, "Wool Cloth", nil, 7 })
    assert_eq({ N.Parse("1,Hearthstone,0,") }, { 1, "Hearthstone", 0 })                    -- 0: can't be sold
    assert_eq({ N.Parse("0,Broken Fang") }, { 0, "Broken Fang" })                           -- before A92
    assert_eq({ N.Parse(nil) }, {})
    assert_eq({ N.Format(1, "Wool Cloth", nil, 7), N.Format(1, "Hearthstone", 0, nil) },
              { "1,Wool Cloth,,7", "1,Hearthstone,0," })
    assert_eq({ N.BaseId(3486), N.BaseId(-6585), N.BaseId(2147483648 * 3 + 14117) }, { 3486, 6585, 14117 })
  end)
end)
