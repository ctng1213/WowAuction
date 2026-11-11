-- UI/MinimapView.lua: what the minimap button shows and where it sits (SPEC §4.3 Minimap button, UI20, UI21).
-- Pure: the frame code in UI/MinimapButton.lua only draws what these return.

local AHProfit = AHProfit

local MinimapView = {}
AHProfit.MinimapView = MinimapView

MinimapView.DEFAULT_ANGLE = 225      -- degrees, 0 = east, counter-clockwise: bottom left
MinimapView.RIM_GAP = 10             -- px from the minimap's edge to the button's centre
MinimapView.HINT = "Click: open AHProfit · Drag: move"
MinimapView.NO_PLAN = "no plan yet: run AHSync"
MinimapView.NOTHING = "nothing due now"
MinimapView.FAILED = "couldn't read the plan"

--- The button's offset from the minimap's centre at an angle on its rim (UI21).
---@param angle number   Degrees, 0 = east, counter-clockwise
---@param radius number  Distance from the minimap's centre, px
---@return number x      px, right is positive
---@return number y      px, up is positive
function MinimapView.Offset(angle, radius)
  local r = math.rad(angle)
  return math.cos(r) * radius, math.sin(r) * radius
end

--- The rim radius for a minimap of a given width (UI21: 140 px wide -> 80).
---@param width number  The minimap's width, px
---@return number       px
function MinimapView.Radius(width)
  return width / 2 + MinimapView.RIM_GAP
end

--- The whole-degree angle of a point seen from the minimap's centre, where a dragged button snaps to (UI21).
---@param dx number  The point's x minus the centre's, px
---@param dy number  The point's y minus the centre's, px
---@return number    Degrees in [0, 360), rounded to whole (floor(x + 0.5)); the default angle when the point is the centre
function MinimapView.AngleOf(dx, dy)
  if dx == 0 and dy == 0 then return MinimapView.DEFAULT_ANGLE end
  local degrees = math.floor(math.deg(math.atan2(dy, dx)) + 0.5)
  return degrees % 360
end

--- The saved angle, or the default when the setting is missing or not a number.
---@param setting table|nil  AHProfitDB.minimap
---@return number            Degrees
function MinimapView.SavedAngle(setting)
  local angle = type(setting) == "table" and setting.angle
  if type(angle) ~= "number" or angle ~= angle or angle == math.huge or angle == -math.huge then
    return MinimapView.DEFAULT_ANGLE                     -- saved state is outside input: NaN or inf never reaches SetPoint
  end
  return angle % 360
end

--- Seconds until the data turns amber (over 24 h old), for one timer that shows the dot then (SPEC §4.3).
---@param dataMs number|nil   The newest snapshot behind the data (Market.DataMs, UI26), ms; nil without a file
---@param nowMs number
---@param staleMs number       Age at which data is old, ms (ViewModel.STALE_MS)
---@return number|nil          Whole seconds, at least 1; nil when there is no file or it is already old
function MinimapView.SecondsUntilStale(dataMs, nowMs, staleMs)
  if dataMs == nil then return nil end
  local leftMs = dataMs + staleMs - nowMs
  if leftMs < 0 then return nil end
  return math.floor(leftMs / 1000) + 1
end

--- The "Now on <name>" value: the Today list's count and expected net (UI20).
---@param today table|nil    ViewModel.Today's result ({ count, total }); nil without a plan file
---@param failed boolean     Computing it raised an error (caught by the caller)
---@return string            "3 actions · +2g 10s", "nothing due now", "no plan yet: run AHSync" or "couldn't read the plan"
local function NowText(today, failed)
  if failed then return MinimapView.FAILED end
  if not today then return MinimapView.NO_PLAN end
  if today.count == 0 then return MinimapView.NOTHING end
  return today.count .. (today.count == 1 and " action" or " actions") .. " · " .. today.total
end

--- The tooltip and the dot (SPEC §4.3, UI20).
---@param input table  { age = ViewModel.Age's { text, amber }, marketProblem = string|nil, planStale = boolean
---                      (the plan file is unusable: another version or damaged), name = the character's name,
---                      today = ViewModel.Today's result or nil (no usable plan file), failed = boolean,
---                      inCombat = boolean (the Today list isn't computed in combat: no Now line) }
---@return table       { title = "AHProfit", age, ageAmber, lines = { { left, right } }, problem = string|nil
---                      (amber, replaces the lines), hint, dot = boolean }
function MinimapView.Tip(input)
  local tip = { title = "AHProfit", age = input.age.text, ageAmber = input.age.amber == true, lines = {},
                hint = MinimapView.HINT }
  if input.marketProblem then
    tip.problem = input.marketProblem
  elseif not input.inCombat then
    tip.lines[1] = { left = "Now on " .. input.name, right = NowText(input.today, input.failed) }
  end
  tip.dot = tip.ageAmber or input.marketProblem ~= nil or input.planStale == true
  return tip
end
