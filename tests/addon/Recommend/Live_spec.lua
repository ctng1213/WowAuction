-- Specs for addon/AHProfit/Recommend/Live.lua: live listings from the last complete scan (SPEC §4.0).

local Stubs = require("wow_stubs")

describe("Live", function()
  it("turns a scan's book into cheapest-first levels and counts the units ahead", function()
    Stubs.Reset()
    Stubs.Login()
    local Live = AHProfit.Live
    local levels = Live.Levels({ [45] = { [20 * 5 + 3] = 2 }, [40] = { [5 * 5 + 1] = 1, [20 * 5 + 4] = 1 } })
    assert_eq(levels, { { price = 40, units = 25 }, { price = 45, units = 40 } })
    assert_eq({ Live.Ahead(levels, 39), Live.Ahead(levels, 40), Live.Ahead(levels, 50) }, { 0, 25, 65 })
    assert_eq(Live.Levels(nil), {})
  end)

  it("uses the scan only when it is newer than the market data", function()
    Stubs.Reset()
    Stubs.Login()
    local Live = AHProfit.Live
    assert_eq(Live.Item(2589, 0), nil)                       -- no scan this session
    AHProfit.Scan.Replicate.LastBook = function() return { at = 2000, book = { [2589] = { [40] = { [101] = 1 } } } } end
    assert_eq(Live.Item(2589, 1000), { { price = 40, units = 20 } })
    assert_eq({ Live.Item(2589, 2000), Live.Item(4306, nil) }, { nil, {} })
  end)

  it("A69 finds stacks dumped below the next price level, once per book", function()
    Stubs.Reset()
    Stubs.Login()
    local Live = AHProfit.Live
    assert_eq(Live.FlipCandidates(0, 0.2), {})                -- no scan this session
    local book = { [2589] = { [10] = { [20 * 5 + 4] = 1 }, [50] = { [20 * 5 + 4] = 5 } },   -- 49 ≥ 1.2 ÷ 0.95 × 10
                   [4306] = { [45] = { [20 * 5 + 4] = 1 }, [50] = { [20 * 5 + 4] = 5 } },   -- 49 < 56.8
                   [2840] = { [10] = { [5 * 5 + 1] = 2 } },                                -- one level only
                   [-6585] = { [10] = { [101] = 1 }, [90] = { [101] = 1 } } }               -- unknown suffix
    AHProfit.Scan.Replicate.LastBook = function() return { at = 2000, book = book } end
    local found = Live.FlipCandidates(1000, 0.2)
    assert_eq(found, { { key = 2589, cheapest = 10, second = 50, units = 20 } })
    book[2589][10] = nil
    assert_eq(Live.FlipCandidates(1000, 0.2), found)            -- the same book: worked out once
    assert_eq(Live.FlipCandidates(2000, 0.2), {})               -- the market file is as new as the scan
  end)
end)
