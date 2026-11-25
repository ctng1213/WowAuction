-- UI/CooldownsView.lua: the Cooldowns tab's rows, character row, bar and footer (SPEC §3.4F.8, CD22–CD29),
-- split from UI/ViewModel.lua for size. It adds to the same AHProfit.ViewModel table; frame code only draws.
-- Lookups and comparisons only (D1): the money is AHSync's exported CD row (Market.Cooldown), the states mirror
-- ahsync/engine/cooldowns.py standing and can_make, and nothing is recalculated for another character.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel

local MINUTE_MS, DAY_MS = 60000, 86400000
local OLD_PRICE_MS = 7 * DAY_MS          -- an AH price older than this shows muted with its age (SPEC §3.4F.8)
-- Secondary skills: never listed as "no cooldown recipes" on the bar.
local SECONDARY = { Cooking = true, ["First Aid"] = true, Fishing = true }

--- A time span as the tab shows it (duration_text): "2 d", "1 d 2 h", "6 h 10 m", "20 h", "10 m".
---@param ms number  The span, ms (whole minutes kept)
---@return string    At least "1 m"
function ViewModel.CooldownSpan(ms)
  local minutes = math.max(math.floor(ms / MINUTE_MS), 1)
  local days, hours, mins = math.floor(minutes / 1440), math.floor(minutes / 60) % 24, minutes % 60
  if days > 0 then return days .. " d" .. (hours > 0 and (" " .. hours .. " h") or "") end
  if hours > 0 then return hours .. " h" .. (mins > 0 and (" " .. mins .. " m") or "") end
  return mins .. " m"
end

--- The catalog's spells of a profession set, by spell id.
---@param catalog table   spell -> row (Capture/Cooldowns.Catalog)
---@param spells number[] Its spell ids, ascending
---@param skills table    profession -> { skill, max }
---@return number[]
local function SpellsOf(catalog, spells, skills)
  local out = {}
  for _, spell in ipairs(spells) do
    if skills[catalog[spell].profession] then out[#out + 1] = spell end
  end
  return out
end

--- The account's characters with a profession that has catalog rows: the logged-in one first, the rest by name.
---@param db table          AHProfitDB
---@param loggedIn string   The logged-in character's id
---@param catalog table     Capture/Cooldowns.Catalog()
---@param spells number[]   Capture/Cooldowns.Spells()
---@return table[]          { id, name, you } each (CD23)
function ViewModel.CooldownCharacters(db, loggedIn, catalog, spells)
  local you, others = nil, {}
  for id, row in pairs(db.characters or {}) do
    local skills = (db.professions or {})[id]
    if type(row) == "table" and type(skills) == "table" and #SpellsOf(catalog, spells, skills) > 0 then
      local entry = { id = id, name = row.name or id, you = id == loggedIn }
      if entry.you then you = entry else others[#others + 1] = entry end
    end
  end
  table.sort(others, function(a, b) if a.name ~= b.name then return a.name < b.name end return a.id < b.id end)
  if you then table.insert(others, 1, you) end
  return others
end

--- A character's bags and bank as key -> units, bound or not.
---@param db table
---@param charId string
---@return table, table  bags, bank
local function Held(db, charId)
  local place = (db.inventory or {})[charId] or {}
  local Inventory = AHProfit.Capture.Inventory
  local bags, bank = {}, {}
  for _, r in ipairs(Inventory.Decode(place.bags)) do bags[r.key] = (bags[r.key] or 0) + r.qty end
  for _, r in ipairs(Inventory.Decode(place.bank)) do bank[r.key] = (bank[r.key] or 0) + r.qty end
  return bags, bank
end

--- When a row can be used again: the newest observation of any member of its group (ready_at, CD12).
---@param members number[]  Spells sharing the cooldown
---@param ready table       spell -> { readyAt, source, at } (Capture/Cooldowns.Decode)
---@return number|nil       UTC ms; nil when nothing is stored
local function ReadyAt(members, ready)
  local best = nil
  for _, spell in ipairs(members) do
    local r = ready[spell]
    if r and (best == nil or r.at > best.at or (r.at == best.at and r.readyAt > best.readyAt)) then best = r end
  end
  return best and best.readyAt
end

--- The spells that share a row's cooldown, itself included, ascending.
---@param row table      A catalog row
---@param catalog table
---@param spells number[]
---@return number[]
local function Members(row, catalog, spells)
  if not row.group then return { row.spell } end
  local out = {}
  for _, spell in ipairs(spells) do if catalog[spell].group == row.group then out[#out + 1] = spell end end
  return out
end

--- A character's standing for a row: the first state of SPEC §3.4F.2 that applies (standing).
---@param row table  A catalog row
---@param who table  { skills, known (spell -> true), bags, bank, ready, members }
---@param nameOf function  key -> item name
---@return table     { state = "below"|"not_learned"|"needs_item"|"not_read"|"timed", readyAt, bank, missing }
local function Standing(row, who, nameOf)
  local skill = who.skills[row.profession].skill or 0
  if skill < row.skill then return { state = "below", skill = skill } end
  if not row.toolUse and not who.known[row.spell] then return { state = "not_learned", skill = skill } end
  if row.tool and not ((who.bags[row.tool] or 0) > 0 or (who.bank[row.tool] or 0) > 0) then
    return { state = "needs_item", skill = skill }
  end
  local readyAt = ReadyAt(who.members, who.ready)
  if not readyAt then return { state = "not_read", skill = skill } end
  local bank, missing = false, nil
  for _, mat in ipairs(row.reagents) do
    local inBags, inBank = who.bags[mat.key] or 0, who.bank[mat.key] or 0
    if inBags < mat.count then
      if inBags + inBank >= mat.count then bank = true
      elseif not missing then missing = (mat.count - inBags - inBank) .. " " .. nameOf(mat.key) end
    end
  end
  if row.tool and (who.bags[row.tool] or 0) == 0 then bank = true end
  return { state = "timed", skill = skill, readyAt = readyAt, bank = bank, missing = missing }
end

--- Whether a character can use a row right now (can_make, CD13).
---@param row table
---@param st table       From Standing
---@param nowMs number
---@param nameOf function
---@return string, string  "Yes" | "Yes (bank)" | "No", and the first reason for a No ("" otherwise)
local function CanMake(row, st, nowMs, nameOf)
  if st.state == "below" then return "No", "needs skill " .. row.skill end
  if st.state == "not_learned" then return "No", "not learned" end
  if st.state == "needs_item" then return "No", "needs " .. nameOf(row.tool) end
  if st.state == "not_read" then return "No", "cooldown not read" end
  if st.readyAt > nowMs then return "No", "on cooldown " .. ViewModel.CooldownSpan(st.readyAt - nowMs) end
  if st.missing then return "No", "missing " .. st.missing end
  return st.bank and "Yes (bank)" or "Yes", ""
end

--- The Ready cell: its text, colour and hover (SPEC §3.4F.8, CD9–CD11).
---@param row table
---@param st table
---@param ctx table   { nowMs, name (the character's), nameOf }
---@return string, string|nil, string|nil  text, colour ("orange" or nil), hover
local function ReadyCell(row, st, ctx)
  if st.state == "below" then return "—", nil, nil end
  if st.state == "not_learned" then return "Not learned", "orange", "Recipe: " .. row.name end
  local tool = row.tool and ctx.nameOf(row.tool)
  if st.state == "needs_item" then return "Needs item", "orange", tool .. " not in " .. ctx.name .. "'s bags or bank" end
  if st.state == "not_read" then
    return "Not read", "orange", row.toolUse
      and (tool .. "'s cooldown hasn't been read yet. Carry it in your bags at login, or use it once.")
      or ("Open " .. row.profession .. " once, or use it.")
  end
  local text = st.readyAt <= ctx.nowMs and "ready" or ViewModel.CooldownSpan(st.readyAt - ctx.nowMs)
  local inBank = row.tool and (ctx.bags[row.tool] or 0) == 0
  return text, nil, inBank and (tool .. " is in the bank") or nil
end

--- The Mats hover in units only (CD26): "Thorium Bar: need 1 · Amaya has 1 (bags) · buy 0".
---@param row table
---@param ctx table  { name, nameOf, bags, bank }
---@return string[]
local function MatsLines(row, ctx)
  local lines = {}
  for _, mat in ipairs(row.reagents) do
    local inBags, inBank = ctx.bags[mat.key] or 0, ctx.bank[mat.key] or 0
    local where = (inBags > 0 and inBank > 0) and " (bags + bank)" or (inBank > 0 and " (bank)" or (inBags > 0 and " (bags)" or ""))
    lines[#lines + 1] = ctx.nameOf(mat.key) .. ": need " .. mat.count .. " · " .. ctx.name .. " has " .. (inBags + inBank)
      .. where .. " · buy " .. math.max(mat.count - inBags - inBank, 0)
  end
  return lines
end

--- The sub-line under the item name: the profession when the character has two, the recipe, and "makes N".
---@param row table
---@param twoProfessions boolean
---@return string
local function SubLine(row, twoProfessions)
  local recipe = row.toolUse and (row.name .. " (tool)") or row.name
  local text = twoProfessions and (row.profession .. " · " .. recipe) or recipe
  if row.yieldMax > 1 then
    text = text .. " · makes " .. (row.yieldMin == row.yieldMax and row.yieldMax or (row.yieldMin .. "–" .. row.yieldMax))
  end
  return text
end

--- The Cooldown cell's hover for a shared group (SPEC §3.4F.8).
---@param row table
---@param members number[]
---@param catalog table
---@param spells number[]
---@return string|nil
local function GroupTip(row, members, catalog, spells)
  if not row.group then return nil end
  local own = {}
  for _, spell in ipairs(spells) do
    local other = catalog[spell]
    if other.profession == row.profession and not other.group then own[#own + 1] = other.name:gsub("^Transmute: ", "") end
  end
  return "One cooldown for all " .. #members .. " " .. row.group .. "s" .. (#own > 0 and ("; " .. table.concat(own, ", ")
    .. (#own == 1 and " has" or " have") .. " its own") or "") .. ". Using any of them locks all of them for the period of the one used."
end

--- The AH price cell: the exported lowest price, muted with its age past 7 days (D2).
---@param item table|nil   Market.Item of the product
---@param ctx table        { nowMs, fileMs (the market file's generatedAt) }
---@return string, string|nil, number|nil  text, the age ("9 d old") or nil, the price for sorting
local function PriceCell(item, ctx)
  local cur = item and item.cur
  if not cur then return "—", nil, nil end
  local ageMs = (item.curAgeMin or 0) * MINUTE_MS + math.max(0, ctx.nowMs - (ctx.fileMs or ctx.nowMs))
  return ViewModel.Money(cur), ageMs > OLD_PRICE_MS and (math.floor(ageMs / DAY_MS) .. " d old") or nil, cur
end

--- The Profit / day cell's hover (SPEC §3.4F.8): the Net / use it comes from, why a cooldown under a day has no
--- Profit / day, and which export the money is from.
---@param row table          A catalog row
---@param money table|nil    Market.Cooldown's
---@param clock string|nil   The export's time ("14:32"); nil without a market file
---@return string[]|nil      The hover's lines; nil without a Net / use
local function PerDayTip(row, money, clock)
  if not (money and money.net) then return nil end
  local lines = { "Net / use: " .. ViewModel.Money(money.net) }
  if row.cooldownS * 1000 < DAY_MS then lines[#lines + 1] = "not limited by the cooldown: limited by the mats" end
  if clock then lines[#lines + 1] = "from the " .. clock .. " export" end
  return lines
end

--- One row of the tab (CD9–CD13, CD26).
---@param row table   A catalog row
---@param who table   As Standing's, plus name
---@param ctx table   As ViewModel.CooldownRows' ctx, plus catalog, spells, twoProfessions
---@return table
local function Row(row, who, ctx)
  local st = Standing(row, who, ctx.nameOf)
  local cell = { nowMs = ctx.nowMs, name = who.name, nameOf = ctx.nameOf, bags = who.bags, bank = who.bank }
  local ready, readyColor, readyTip = ReadyCell(row, st, cell)
  local can, why = CanMake(row, st, ctx.nowMs, ctx.nameOf)
  local money = ctx.moneyOf(row.spell)
  local look = ctx.lookOf(row.product)
  local price, age, sortPrice = PriceCell(ctx.itemOf(row.product), ctx)
  local below = st.state == "below"
  return {
    id = "cd:" .. row.spell, key = row.product, spell = row.spell, recipe = row.name,
    item = look.name, quality = look.quality, sub = SubLine(row, ctx.twoProfessions),
    skill = tostring(row.skill), skillAmber = below,
    skillSub = below and ((ctx.live and "you " or "has ") .. st.skill) or nil,   -- under the skill, amber (D25)
    cooldown = ViewModel.CooldownSpan(row.cooldownS * 1000), shared = row.group and "shared" or nil,
    cooldownTip = GroupTip(row, who.members, ctx.catalog, ctx.spells),
    ready = ready, readyColor = readyColor, readyTip = readyTip,
    price = price, priceAge = age, sortPrice = sortPrice,
    mats = ViewModel.Money(money and money.mats), matsTip = MatsLines(row, cell),
    perDay = ViewModel.CellMoney(money and money.perDay), sortPerDay = money and money.perDay,   -- to the silver (UI25)
    perDayColor = money and money.perDay and money.perDay < 0 and "neg" or nil,
    perDayTip = PerDayTip(row, money, ctx.exportClock),
    canMake = can, canMakeColor = can ~= "No" and "pos" or nil,
    canMakeTip = can ~= "No" and row.group and "shared: one of these per cooldown" or (why ~= "" and why or nil),
  }
end

--- What the tab needs to know about one character, from what the addon stored (D1).
---@param db table
---@param charId string
---@return table  { name, skills, known, bags, bank, ready }
local function Who(db, charId)
  local row = (db.characters or {})[charId] or {}
  local known = {}
  for id in pairs(AHProfit.DB.DecodeIdSet((db.knownRecipes or {})[charId])) do known[id] = true end
  local ready = AHProfit.Capture.Cooldowns.Decode(type((db.cooldowns or {})[charId]) == "string" and db.cooldowns[charId] or nil)
  local bags, bank = Held(db, charId)
  return { name = row.name or charId, skills = (db.professions or {})[charId] or {}, known = known, bags = bags,
           bank = bank, ready = ready }
end

--- The Cooldowns tab's rows for one character (SPEC §3.4F.8, CD22, CD24, CD25): every catalog row of its
--- professions, learned or not, by the exported Profit / day highest first, then the rows without one by AH price
--- highest first, ties by recipe name, then spell; by recipe name without a market file (CD8, CD24).
---@param db table       AHProfitDB
---@param charId string  The character shown
---@param ctx table      { live (the logged-in one), nowMs, lookOf (key -> { name, quality }), nameOf (key -> name),
---                      itemOf (Market.Item), moneyOf (Market.Cooldown), fileMs, exportClock ("14:32") }
---@return table[]
function ViewModel.CooldownRows(db, charId, ctx)
  local Cooldowns = AHProfit.Capture.Cooldowns
  local catalog, spells = Cooldowns.Catalog(), Cooldowns.Spells()
  local who = Who(db, charId)
  local mine, professions = SpellsOf(catalog, spells, who.skills), {}
  for _, spell in ipairs(mine) do professions[catalog[spell].profession] = true end
  local count = 0
  for _ in pairs(professions) do count = count + 1 end
  local rowCtx = setmetatable({ catalog = catalog, spells = spells, twoProfessions = count > 1 }, { __index = ctx })
  local rows = {}
  for _, spell in ipairs(mine) do
    who.members = Members(catalog[spell], catalog, spells)
    rows[#rows + 1] = Row(catalog[spell], who, rowCtx)
  end
  table.sort(rows, function(a, b)
    if (a.sortPerDay ~= nil) ~= (b.sortPerDay ~= nil) then return a.sortPerDay ~= nil end
    if a.sortPerDay and a.sortPerDay ~= b.sortPerDay then return a.sortPerDay > b.sortPerDay end
    if (a.sortPrice ~= nil) ~= (b.sortPrice ~= nil) then return a.sortPrice ~= nil end
    if a.sortPrice and a.sortPrice ~= b.sortPrice then return a.sortPrice > b.sortPrice end
    if a.recipe ~= b.recipe then return a.recipe < b.recipe end
    return a.spell < b.spell
  end)
  return rows
end

--- The bar above the rows (CD22, CD24, CD25, CD27): the name, each cooldown profession's skill, the other
--- professions as "no cooldown recipes", and "live" or "data from Wed 09:15".
---@param db table
---@param charId string
---@param ctx table  { live, offsetMs }
---@return string, boolean  the text, and true when the character has no cooldown profession (CD27)
function ViewModel.CooldownBar(db, charId, ctx)
  local Cooldowns = AHProfit.Capture.Cooldowns
  local catalog, spells = Cooldowns.Catalog(), Cooldowns.Spells()
  local row = (db.characters or {})[charId] or {}
  local skills = (db.professions or {})[charId] or {}
  local has, names = {}, {}
  for _, spell in ipairs(spells) do has[catalog[spell].profession] = true end
  for name in pairs(skills) do names[#names + 1] = name end
  table.sort(names)
  local parts, others = { row.name or charId }, {}
  for _, name in ipairs(names) do
    if has[name] then parts[#parts + 1] = name .. " " .. (skills[name].skill or 0)
    elseif not SECONDARY[name] then others[#others + 1] = name end
  end
  if #parts == 1 then
    return (row.name or charId) .. "'s professions" .. (#others > 0 and (" (" .. table.concat(others, ", ") .. ")") or "")
      .. " have no cooldown recipes. Click a character above.", true
  end
  for _, name in ipairs(others) do parts[#parts + 1] = name .. ": no cooldown recipes" end
  if ctx.live then parts[#parts + 1] = "live"
  elseif row.updated then
    local day, hour = ViewModel.Clock(row.updated, ctx.offsetMs)
    parts[#parts + 1] = "data from " .. day .. " " .. hour
  end
  return table.concat(parts, " · "), false
end

--- The logged-in view's footer (CD14): the other characters with a stored ready cooldown they can use, each group
--- counted once, with up to 3 recipe names: "Also ready now (stored): Sorvin, 1 transmute (Iron to Gold)".
---@param db table
---@param characters table[]  ViewModel.CooldownCharacters
---@param ctx table           As ViewModel.CooldownRows' ctx
---@return string|nil         nil when nobody else is ready
function ViewModel.CooldownFooter(db, characters, ctx)
  local parts = {}
  for _, c in ipairs(characters) do
    if not c.you then
      local units, names, groups, onlyGroup = 0, {}, {}, nil
      for _, r in ipairs(ViewModel.CooldownRows(db, c.id, ctx)) do
        if r.canMake ~= "No" then
          local group = AHProfit.Capture.Cooldowns.Catalog()[r.spell].group
          if not group or not groups[group] then
            units = units + 1
            if group then groups[group] = true end
            onlyGroup = (units == 1 and group) or (onlyGroup == group and group) or false
          end
          if #names < 3 then names[#names + 1] = (r.recipe:gsub("^Transmute: ", "")) end
        end
      end
      if units > 0 then
        parts[#parts + 1] = c.name .. ", " .. units .. (onlyGroup and (" " .. onlyGroup) or "") .. " ("
          .. table.concat(names, ", ") .. ")"
      end
    end
  end
  if #parts == 0 then return nil end
  return "Also ready now (stored): " .. table.concat(parts, " · ")
end

--- The catalog version check (CD29): the addon's catalog against the one AHSync exported.
---@param addonVersion number|nil  AHProfit.Data.COOLDOWNS_VERSION
---@param exportVersion number|nil Market.CooldownVersion()
---@return string|nil              The status line, or nil when they agree or the export has none
function ViewModel.CooldownVersionLine(addonVersion, exportVersion)
  if not addonVersion or not exportVersion or addonVersion == exportVersion then return nil end
  return "cooldown catalog v" .. addonVersion .. " in the addon, v" .. exportVersion .. " in AHSync's export: update "
    .. (addonVersion > exportVersion and "AHSync" or "AHProfit")
end
