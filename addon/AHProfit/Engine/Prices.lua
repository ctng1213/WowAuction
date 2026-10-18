-- Engine/Prices.lua: the post price (SPEC §3.4). Port of post_price in ahsync/engine/prices.py;
-- keep them identical (CLAUDE.md, shared scenario PP1).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Prices = {}
AHProfit.Engine.Prices = Prices

--- Post price p = min(curOthers − 1, med7) (SPEC §3.4).
---@param curOthers number|nil  Lowest competing buyout, copper
---@param med7 number|nil       7-day median, copper
---@return number|nil           Unit price; the available one when the other is nil; nil when both are
function Prices.PostPrice(curOthers, med7)
  local undercut = curOthers and curOthers - 1
  if undercut and med7 then return math.min(undercut, med7) end
  return undercut or med7
end
