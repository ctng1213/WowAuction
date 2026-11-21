--- The Cooldowns scenarios' world in the addon (SPEC §3.4F, docs/SCENARIOS.md "Cooldowns"): the six characters of
--- tests/support/cooldowns_world.py as AHProfitDB tables, with the money AHSync exported for them.

local CW = {}

CW.NOW = 1760000000000
CW.HOUR, CW.DAY = 3600000, 86400000
CW.STONE = 9149
CW.WED_0915 = 1759914900000                  -- Wed 2025-10-08 09:15 UTC: Tessa's last stored data

CW.NAMES = { [12360] = "Arcanite Bar", [12359] = "Thorium Bar", [12363] = "Arcane Crystal", [14342] = "Mooncloth",
  [14256] = "Felcloth", [15409] = "Refined Deeprock Salt", [8150] = "Deeprock Salt", [7080] = "Essence of Water",
  [7076] = "Essence of Earth", [12808] = "Essence of Undeath", [7082] = "Essence of Air", [6037] = "Truesilver Bar",
  [3860] = "Mithril Bar", [3577] = "Gold Bar", [3575] = "Iron Bar", [7077] = "Heart of Fire", [7068] = "Elemental Fire",
  [9149] = "Philosopher's Stone", [15846] = "Salt Shaker", [7078] = "Essence of Fire", [12803] = "Living Essence",
  [17202] = "Snowball", [159] = "Refreshing Spring Water", [17716] = "SnowMaster 9000" }
CW.PRICES = { [12360] = 450000, [12359] = 12000, [12363] = 280000, [14342] = 220000, [14256] = 35000, [15409] = 45000,
  [8150] = 1500, [7080] = 30000, [7076] = 24000, [12808] = 10000, [7082] = 28000, [6037] = 21000, [3860] = 6000,
  [3577] = 13000, [3575] = 4000, [7077] = 15000, [7068] = 12000 }
-- The CD strings AHSync exported for this world ("mats;net;perWeek", tests/support/cooldowns_world.py's base()).
CW.EXPORT = { [11479] = "4000;8350;58450", [11480] = "6000;13950;48825", [17187] = "292000;135500;474250",
  [17559] = "28000;;", [17560] = ";;", [17561] = "24000;4500;31500", [17562] = "30000;-3400;-23800",
  [17563] = "10000;18500;129500", [17564] = "30000;-20500;-143500", [17565] = ";;", [17566] = "24000;;",
  [18560] = "70000;139000;243250", [19566] = "1500;41250;96250", [21935] = ";;", [25146] = "15000;19200;" }

--- Encode key -> units as an inventory string.
---@param units table|nil
---@return string
local function Place(units)
  local keys, parts = {}, {}
  for key in pairs(units or {}) do keys[#keys + 1] = key end
  table.sort(keys)
  for i, key in ipairs(keys) do parts[i] = key .. "," .. units[key] .. ",0" end
  return table.concat(parts, ";")
end

--- One character's tables.
---@param c table  { name, skills = { profession = skill }, known = { spell }, bags, bank, ready = { spell = { readyAt, at } },
---                updated, class }
---@return table   { id, character, professions, known, inventory, cooldowns }
local function Character(c)
  local id = c.name .. "-Grommash"
  local professions = {}
  for name, skill in pairs(c.skills) do professions[name] = { skill = skill, max = 300 } end
  local known = {}
  table.sort(c.known or {})
  for i, spell in ipairs(c.known or {}) do known[i] = tostring(spell) end
  local spells, ready = {}, {}
  for spell in pairs(c.ready or {}) do spells[#spells + 1] = spell end
  table.sort(spells)
  for i, spell in ipairs(spells) do
    ready[i] = spell .. "," .. string.format("%.0f", c.ready[spell][1]) .. ",g," .. string.format("%.0f", c.ready[spell][2])
  end
  return { id = id, character = { name = c.name, realm = "Grommash", faction = "Horde", class = c.class or "MAGE",
                                  level = 60, updated = c.updated or CW.NOW },
           professions = professions, known = #known > 0 and table.concat(known, ";") or nil,
           inventory = { bags = Place(c.bags), bagsAt = CW.NOW, bank = c.bank and Place(c.bank) or nil,
                         bankAt = c.bank and CW.NOW or nil },
           cooldowns = #ready > 0 and table.concat(ready, ";") or nil }
end

--- The six characters (cooldowns_world.py), each as Character's input.
---@return table[]
function CW.People()
  local N, H = CW.NOW, CW.HOUR
  return {
    { name = "Amaya", skills = { Alchemy = 280, Herbalism = 290 }, known = { 17187, 17563, 17562, 11480, 11479 },
      bags = { [12359] = 1, [12363] = 1, [CW.STONE] = 1 }, bank = { [3860] = 1 }, ready = { [17187] = { N - H, N - 49 * H } } },
    { name = "Tessa", skills = { Alchemy = 300 }, known = { 17187, 17561, 17563, 25146 }, bags = { [CW.STONE] = 1, [7077] = 1 },
      ready = { [17187] = { N + 26 * H, N - 22 * H }, [25146] = { N - H, N - 2 * H } }, updated = CW.WED_0915 },
    { name = "Sorvin", skills = { Alchemy = 230 }, known = { 11479, 11480 }, bags = { [CW.STONE] = 1, [3575] = 1 },
      ready = { [11479] = { N - H, N - 25 * H } } },
    { name = "Brugan", skills = { Leatherworking = 262, Skinning = 260 }, bags = { [15846] = 1, [8150] = 1 },
      ready = { [19566] = { N + 28 * H, N - 44 * H } } },
    { name = "Mira", skills = { Tailoring = 260, Leatherworking = 255 }, known = { 18560 }, bags = { [14256] = 2 },
      ready = { [18560] = { N + 51 * H, N - 45 * H } } },
    { name = "Keldor", skills = { Tailoring = 245 } },
  }
end

--- An AHProfitDB holding these characters (default: the six).
---@param people table[]|nil  CW.People()-shaped, possibly changed
---@return table
function CW.DB(people)
  local db = { formatVersion = 1, characters = {}, professions = {}, knownRecipes = {}, inventory = {}, cooldowns = {},
               recipes = {}, ownAuctions = {}, scans = {}, events = {}, rules = {}, reserves = {}, reserveTimes = {}, mailSeen = {} }
  for _, c in ipairs(people or CW.People()) do
    local t = Character(c)
    db.characters[t.id], db.professions[t.id], db.knownRecipes[t.id] = t.character, t.professions, t.known
    db.inventory[t.id], db.cooldowns[t.id] = t.inventory, t.cooldowns
  end
  return db
end

--- The rows' context: the world's names and prices, the exported money, the market file written now.
---@param live boolean        The logged-in character's view
---@param noMarket boolean|nil  No market file at all (CD24)
---@return table              ViewModel.CooldownRows' ctx
function CW.Ctx(live, noMarket)
  local Market = { Decode = function(spell)
    local text = CW.EXPORT[spell]
    if not text then return nil end
    local mats, net, week = text:match("^([^;]*);([^;]*);([^;]*)$")
    return { mats = tonumber(mats), net = tonumber(net), perWeek = tonumber(week) }
  end }
  return {
    live = live, nowMs = CW.NOW, offsetMs = 0, fileMs = CW.NOW, exportClock = not noMarket and "14:32" or nil,
    lookOf = function(key) return { name = CW.NAMES[key] or ("item " .. key), quality = 1 } end,
    nameOf = function(key) return CW.NAMES[key] or ("item " .. key) end,
    itemOf = function(key)
      if noMarket or not CW.PRICES[key] then return nil end
      return { cur = CW.PRICES[key], curAgeMin = key == 7068 and 9 * 1440 or 0 }
    end,
    moneyOf = function(spell) if noMarket then return nil end return Market.Decode(spell) end,
  }
end

--- Install a MarketIntelligence.lua with the world's prices and its CD table (EX23), checksum included.
---@param UIWorld table      tests/support/lua/ui_world.lua
---@param generatedAt number ms
---@param version number|nil The exported catalog version (default 1)
function CW.InstallMarket(UIWorld, generatedAt, version)
  local items = {}
  for key, price in pairs(CW.PRICES) do items[key] = UIWorld.Item(price, 80, 5) end
  UIWorld.Market(items, generatedAt)
  local file = _G.AHProfitMarket
  local cd = { v = version or 1 }
  local count, length = file.checksum:match("^(%d+):(%d+)$")
  count, length = tonumber(count), tonumber(length)
  for spell, text in pairs(CW.EXPORT) do
    cd[spell] = text
    if text ~= "" then count, length = count + 1, length + #text end
  end
  file.markets[UIWorld.MARKET].CD = cd
  file.checksum = count .. ":" .. length
end

--- Put the world's characters into the live database after login, and name the world's items.
---@param Stubs table         wow_stubs
---@param people table[]|nil  CW.People()-shaped (default the six)
function CW.Load(Stubs, people)
  for key, name in pairs(CW.NAMES) do Stubs.ITEM_NAMES[key] = name end
  for field, value in pairs(CW.DB(people)) do
    if type(value) == "table" then _G.AHProfitDB[field] = value end
  end
end

return CW
