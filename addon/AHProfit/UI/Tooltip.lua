-- UI/Tooltip.lua: the AHProfit block under an item's tooltip (SPEC §4.3, A21, UI8). It reads
-- the market file (and a newer in-game scan's price) and ViewModel.Tooltip makes the lines;
-- this file only finds the item and draws them. One cache lookup per hover (SPEC §5.1).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Market = AHProfit.Market
local Live = AHProfit.Live
local VM = AHProfit.ViewModel
local Disenchant = AHProfit.Engine.Disenchant

local Tooltip = {}
AHProfit.Tooltip = Tooltip

Tooltip.TITLE = "AHProfit"
local DE_KINDS = { [2] = "weapon", [4] = "armor" }

--- Your best Enchanting skill across the characters AHProfit has seen.
---@return number|nil
local function BestEnchanting()
  local best
  for _, skills in pairs((DB.Get() or {}).professions or {}) do
    local e = skills.Enchanting
    if e and (not best or e.skill > best) then best = e.skill end
  end
  return best
end

--- An item's disenchant value per item, when one of your characters can disenchant it.
---@param key number
---@return boolean, number|nil  can disenchant, expected value (float copper)
local function DisenchantValue(key)
  local quality, ilvl, classId = Adapter.ItemDetails(VM.BaseItemId(key))
  local kind = DE_KINDS[classId]
  if not (quality and ilvl and kind) then return false, nil end
  local skill, outcomes = Disenchant.OutcomesFor(Disenchant.Table(), quality, ilvl, kind)
  if not skill or not Disenchant.CanDisenchant(skill, BestEnchanting()) then return false, nil end
  local values = {}
  for i, o in ipairs(outcomes) do
    local mat = Market.Item(o[1])
    values[i] = mat and mat.sellValue or 0
  end
  return true, Disenchant.ExpectedValue(outcomes, values)
end

--- The tooltip lines for an item, or nil without market data (A21).
---@param key number
---@param nowMs number
---@return table[]|nil  ViewModel.Tooltip lines
function Tooltip.Lines(key, nowMs)
  local item = Market.Item(key)
  if not item then return nil end
  local generatedAt, newest = Market.Times()
  local cur, ageMs = item.cur, item.curAgeMin and (nowMs - generatedAt + item.curAgeMin * 60000)
  local levels = Live.Item(key, newest)
  if levels then
    cur, ageMs = levels[1] and levels[1].price, nowMs - AHProfit.Scan.Replicate.LastBook().at
  end
  local window = item.sellWindows[1]
  local canDE, deValue = DisenchantValue(key)
  return VM.Tooltip({
    cur = cur, curAgeText = VM.SeenAgo(ageMs), med30 = item.med30, deValue = deValue, canDisenchant = canDE,
    bestWindow = window and VM.WindowLabel(window, Adapter.LocalOffsetMs(nowMs)),
    bestPrice = window and item.med30 and item.med30 * window.how, chance = item.chance,
  })
end

--- Add the block to a tooltip that shows an item; failures are swallowed (never break a tooltip).
---@param tooltip table
---@param link string
function Tooltip.OnItem(tooltip, link)
  local ok, lines = pcall(function()
    local key = Adapter.ItemKey(link)
    return key and Tooltip.Lines(key, Adapter.NowMs())
  end)
  if ok and lines then Adapter.AddTooltipLines(tooltip, Tooltip.TITLE, lines) end
end

Adapter.HookItemTooltip(Tooltip.OnItem)
