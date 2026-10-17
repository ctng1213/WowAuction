-- Engine/Disenchant.lua: disenchant rows and expected values (SPEC §3.4A, §4.5).
-- Port of ahsync/engine/disenchant.py (outcomes_for, expected_value, can_disenchant);
-- keep them identical, including the order of floating-point operations (LP1).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Disenchant = {}
AHProfit.Engine.Disenchant = Disenchant

local decoded = nil   -- rows from Data/Disenchant.lua, decoded on first use

--- The disenchant table, decoded once from Data/Disenchant.lua (same rows and order as Python's DE_TABLE).
---@return table[]  { quality, kind, lo, hi, skill, outcomes = { { key, chance, min, max }, ... } } per row
function Disenchant.Table()
  if decoded then return decoded end
  decoded = {}
  local text = AHProfit.Data and AHProfit.Data.DISENCHANT or ""
  for row in text:gmatch("[^;]+") do
    local quality, kind, lo, hi, skill, mats = row:match("^(%d+)|(%a+)|(%d+)|(%d+)|(%d+)|(.*)$")
    local outcomes = {}
    for key, chance, qmin, qmax in mats:gmatch("(%d+):([%d.e%-]+):(%d+):(%d+)") do
      outcomes[#outcomes + 1] = { tonumber(key), tonumber(chance), tonumber(qmin), tonumber(qmax) }
    end
    decoded[#decoded + 1] = { tonumber(quality), kind, tonumber(lo), tonumber(hi), tonumber(skill), outcomes }
  end
  return decoded
end

--- The disenchant row for an item (SPEC §4.5).
---@param tbl table[]       Rows as from Disenchant.Table()
---@param quality number    Item quality (2 uncommon, 3 rare, ...)
---@param ilvl number       Item level
---@param kind string       "armor" or "weapon"
---@return number|nil skill    Enchanting skill needed; nil when it can't be disenchanted
---@return table|nil outcomes  { { key, chance, min, max }, ... }
function Disenchant.OutcomesFor(tbl, quality, ilvl, kind)
  for _, row in ipairs(tbl) do
    if row[1] == quality and row[2] == kind and row[3] <= ilvl and ilvl <= row[4] then
      return row[5], row[6]
    end
  end
  return nil, nil
end

--- EV = Σ P(mat) × E[qty] × sellValue(mat) (SPEC §3.4A).
---@param outcomes table[]     { { key, chance, min, max }, ... }
---@param sellValues number[]  Sell value per unit of each material, same order, float copper
---@return number              Expected value of one disenchant, float copper
function Disenchant.ExpectedValue(outcomes, sellValues)
  local total = 0.0
  for i, outcome in ipairs(outcomes) do
    total = total + outcome[2] * ((outcome[3] + outcome[4]) / 2) * sellValues[i]
  end
  return total
end

--- Whether one of your characters can disenchant it (SPEC §3.4A).
---@param skillRequired number   Enchanting skill the item needs
---@param bestSkill number|nil   Your highest Enchanting skill; nil when nobody has Enchanting
---@return boolean               bestSkill ≥ skillRequired
function Disenchant.CanDisenchant(skillRequired, bestSkill)
  return bestSkill ~= nil and bestSkill >= skillRequired
end
