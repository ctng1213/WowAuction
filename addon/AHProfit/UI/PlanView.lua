-- UI/PlanView.lua: the plan's Today rows (SPEC §4.3, D62-D66, A71-A74), split from UI/ViewModel.lua for size: the
-- Portfolio tab without a portfolio snapshot and the AH panel's Post list show them (the Today and Week tabs were
-- replaced by the Portfolio tab at M13). Lua twin of ahsync/engine/plan_chains.py and the Today part of
-- engine/plan_views.py: a chain is one row
-- (the action that carries its profit), its mat buys and intermediate crafts by the same character are lines under
-- it, mail another character sends for it is a line too, and the last line says when to sell; holds and vendor
-- sales are no rows (D64). It adds to the same AHProfit.ViewModel table; frame code only draws.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel

local HOUR_MS = 3600000
local LEFT_OUT = { hold = true, vendor = true }        -- never a Today or Week row, and in no total (D64)
local FOLDS = { buy = true, craft = true }             -- step kinds that fold into their chain's row

--- A character's name without its realm (plan_chains.short_name).
---@param charId string  "Name-Realm"
---@return string        "Name"
function ViewModel.ShortName(charId)
  return charId:match("^([^-]+)") or charId
end

--- A mat buy as a line under its chain's row, the Craft tab's wording (plan_chains.buy_line, A63).
---@param qty number     Units
---@param name string    The mat's name
---@param vendor boolean Bought from a vendor (else the AH)
---@param cost number    What the units cost together, copper
---@return string        "buy 76 Empty Vial from a vendor · 15s 20c"
function ViewModel.BuyLine(qty, name, vendor, cost)
  return "buy " .. qty .. " " .. name .. (vendor and " from a vendor · " or " on the AH · ") .. ViewModel.Money(cost)
end

--- An intermediate craft as a line under its chain's row (plan_chains.first_line).
---@param qty number   Units made
---@param name string  The intermediate's name
---@return string      "craft 21 Tin Bar first"
function ViewModel.FirstLine(qty, name)
  return "craft " .. qty .. " " .. name .. " first"
end

--- Every chain's actions, each list by time then id (plan_chains.by_chain).
---@param actions table[]  Marked actions
---@return table           chainId -> actions
function ViewModel.ByChain(actions)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.inChain then
      local id = ViewModel.ChainId(a)
      out[id] = out[id] or {}
      table.insert(out[id], a)
    end
  end
  return out
end

--- The action a chain's row is: its earliest profit step, else its last craft (plan_chains.carrier_of).
---@param members table[]|nil  The chain's actions by time
---@return table|nil
function ViewModel.CarrierOf(members)
  local last
  for _, a in ipairs(members or {}) do
    if ViewModel.Role(a) == "profit" then return a end
    if a.kind == "craft" then last = a end
  end
  return last
end

--- The steps that fold into their chain's row in a view (plan_chains.folded): a buy or intermediate craft with no
--- net whose carrier is a candidate of the same view and is done by the same character.
---@param candidates table[]  The actions the view would show
---@param chains table        From ByChain
---@return table              step id -> its carrier's id
function ViewModel.Folded(candidates, chains)
  local ids, out = {}, {}
  for _, a in ipairs(candidates) do ids[a.id] = true end
  for _, a in ipairs(candidates) do
    local carrier = a.inChain and FOLDS[a.kind] and ViewModel.Role(a) == "step"
                    and ViewModel.CarrierOf(chains[ViewModel.ChainId(a)]) or nil
    if carrier and ids[carrier.id] and carrier ~= a and carrier.char == a.char then out[a.id] = carrier.id end
  end
  return out
end

--- Incoming mail as a line under the crafter's row (plan_chains.mail_line, A74).
---@param m table        The mail action (its char is the sender)
---@param name string    The item's name
---@param offsetMs number
---@return string        "6 Copper Bar from Brugan · not mailed yet", "… · mailed Mon 10:40", "… · skipped"
local function MailLine(m, name, offsetMs)
  local head = m.qty .. " " .. name .. " from " .. ViewModel.ShortName(m.char)
  if m.status == "done" or m.status == "partial" then
    local day, hour = ViewModel.Clock(m.doneAt or m.at, offsetMs)
    return head .. " · " .. (m.status == "done" and "mailed " or "partly mailed ") .. day .. " " .. hour
  end
  return head .. " · " .. (m.status == "skipped" and "skipped" or "not mailed yet")
end

--- A chain row's last line: what happens to what it buys or makes (plan_chains.sale_line).
---@param carrier table
---@param members table[]  The chain's actions by time
---@param offsetMs number
---@return string|nil      "then: post Fri 11:00, for the 19:00 peak", "then: relist now", "then: post now",
---                        "then: disenchant it", or nil
local function SaleLine(carrier, members, offsetMs)
  local de = false
  for _, a in ipairs(members) do
    if a.kind == "post" then
      if not a.peak then return carrier.kind == "buy" and "then: relist now" or "then: post now" end
      local day, hour = ViewModel.Clock(a.at, offsetMs)
      local _, peak = ViewModel.Clock(a.peak, offsetMs)
      return "then: post " .. day .. " " .. hour .. ", for the " .. peak .. " peak"
    end
    de = de or a.kind == "de"
  end
  return de and "then: disenchant it" or nil
end

--- A chain row's lines: folded buys, folded intermediate crafts, incoming mail, then the sale (plan_chains.chain_lines).
---@param carrier table
---@param members table[]  The chain's actions by time
---@param folds table      From Folded
---@param lookOf function  key -> look
---@param offsetMs number
---@return string[]
function ViewModel.ChainLines(carrier, members, folds, lookOf, offsetMs)
  local lines = {}
  for _, kind in ipairs({ "buy", "craft" }) do
    for _, a in ipairs(members) do
      if folds[a.id] == carrier.id and a.kind == kind then
        lines[#lines + 1] = kind == "buy" and ViewModel.BuyLine(a.qty, lookOf(a.key).name, a.vendor, (a.price or 0) * a.qty)
                            or ViewModel.FirstLine(a.qty, lookOf(a.key).name)
      end
    end
  end
  for _, m in ipairs(members) do
    if m.kind == "mail" and m.to == carrier.char and m.char ~= carrier.char then
      lines[#lines + 1] = MailLine(m, lookOf(m.key).name, offsetMs)
    end
  end
  lines[#lines + 1] = SaleLine(carrier, members, offsetMs)
  return lines
end

--- A row's Each and Total and the gold it puts at risk (plan_chains.row_money): a post risks its deposit.
---@param a table              The row's action
---@param members table[]|nil  Its chain's actions when it carries the chain's row
---@param look table|nil       Its item's look: vendorSell (copper) for a post's deposit
---@return string, string, number  each, total, spend copper
function ViewModel.RowMoney(a, members, look)
  if a.kind == "craft" and members then
    local gold = 0
    for _, m in ipairs(members) do
      if m.kind == "buy" then gold = gold + (m.price or 0) * m.qty end
    end
    if gold <= 0 then return "—", "—", 0 end
    return ViewModel.Money(ViewModel.RoundHalfUp(gold / math.max(1, a.qty))), ViewModel.Money(gold), gold
  end
  local total = a.price and ViewModel.Money(a.price * a.qty) or "—"
  local spend = 0
  if a.kind == "buy" then
    spend = (a.price or 0) * a.qty
  elseif a.kind == "post" and a.duration and look then        -- SPEC §3.4 Risk; no vendor price: the 1c minimum (P27)
    spend = AHProfit.Engine.Fees.DepositCopper(look.vendorSell or 0, a.qty, a.duration)
  end
  return ViewModel.Money(a.price), total, spend
end

--- One row's display cells, shared by Today and Week.
---@param a table          The action
---@param cells table      { net, note, chance (percent or nil), lines, chain (members when it carries its chain) }
---@param look table       { name, quality, conf, soldPerDay, volumeUnknown } for its item
---@param totalGold number All your gold, copper
---@param offsetMs number
---@return table           Row of text and flags; notes = the chain's lines (D62)
local function Row(a, cells, look, totalGold, offsetMs)
  local each, total, spend = ViewModel.RowMoney(a, cells.chain, look)
  local risk, amber = ViewModel.Risk(spend, totalGold)
  local day, hour = ViewModel.Clock(a.at, offsetMs)
  local net, chance = cells.net, cells.chance
  return {
    id = a.id, kind = a.kind, action = ViewModel.ACTION_LABELS[a.kind] or a.kind, key = a.key, item = look.name,
    quality = look.quality, qty = "×" .. a.qty, each = each, total = total, notes = cells.lines,
    net = net == nil and "—" or ViewModel.SignedMoney(net),
    -- The action's own expected profit, even when a chain counts it elsewhere: the AH panel's Est. profit.
    postNet = ViewModel.SignedMoney(a.net or 0), postNetColor = (a.net or 0) < 0 and "neg" or "pos",
    netColor = (net or 0) > 0 and "pos" or ((net or 0) < 0 and "neg" or "muted"),
    chance = chance and (chance .. "%") or "—", dot = chance and ViewModel.Level(look.conf) or nil,
    confidence = chance and look.conf or nil,
    volume = (chance and look.soldPerDay) and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
    volumeUnknown = (chance and look.volumeUnknown) or nil,        -- "—" volume: the chance is a default (A68)
    risk = risk, riskAmber = amber, who = ViewModel.ShortName(a.char), day = day, time = hour,
    price = a.price, units = a.qty, postNetCopper = a.net or 0,   -- numbers, for the AH panel (A29, A31)
    durationH = a.duration,                                         -- the auction duration to set (A56)
    status = a.status, note = cells.note, netCopper = ViewModel.RoundHalfUp(net or 0),
  }
end

--- A view's rows: its candidates less holds, vendor sales, mail sent to this character and folded steps (D62-D64).
---@param candidates table[]  The actions the view would show
---@param actions table[]     Every marked action (for the chains)
---@return table[], table, table  The row actions, chainId -> members, step id -> carrier id
local function Collapse(candidates, actions)
  local shown, rows = {}, {}
  for _, a in ipairs(candidates) do
    if not LEFT_OUT[a.kind] and not a.incoming then shown[#shown + 1] = a end
  end
  local chains = ViewModel.ByChain(actions)
  local folds = ViewModel.Folded(shown, chains)
  for _, a in ipairs(shown) do
    if not folds[a.id] then rows[#rows + 1] = a end
  end
  return rows, chains, folds
end

--- A row's net, note, chance and, when it carries its chain, the chain's lines.
---@param a table      The row's action
---@param view table   { nets, chances, crafts, chains, folds, lookOf, offsetMs }
---@return table       Cells for Row
local function CellsOf(a, view)
  local counted = view.nets[a.id]
  local members = a.inChain and view.chains[ViewModel.ChainId(a)] or nil
  local carrier = members and ViewModel.CarrierOf(members)
  local note = a.ownNote or ViewModel.OwnCraftNote(a) or ViewModel.NoteOf(a, counted.note, view.crafts, view.offsetMs)
  local cells = { net = counted.net, note = note, chance = ViewModel.ChanceOf(a, view.chances), lines = {} }
  if carrier == a then
    cells.lines, cells.chain = ViewModel.ChainLines(a, members, view.folds, view.lookOf, view.offsetMs), members
  end
  return cells
end

--- The Today tab: open actions due within the next hour, best expected net first (D1, D2, D16, D62, D64, A71, A73).
--- A chain is one row, its steps lines under it; holds and vendor sales are no rows. Ties go to the higher chance,
--- then the item name, then the character. Rows whose chance is below minChance are hidden and counted; rows with
--- nothing to sell, and rows of items whose volume is unknown (their chance is a default, A68), never are.
---@param actions table[]  Plan actions, each with char (the character id); mail other characters send to this one
---                        carries incoming = true (A74): a line under its craft, never a row
---@param nowMs number
---@param offsetMs number  Local time minus UTC
---@param minChance number Percent, 0-100
---@param lookOf function  key -> { name, quality, conf, soldPerDay, volumeUnknown }
---@param totalGold number All your gold, copper
---@return table           { rows, hidden, total (signed money), count }
function ViewModel.Today(actions, nowMs, offsetMs, minChance, lookOf, totalGold)
  ViewModel.MarkChains(actions)
  local due = {}
  for _, a in ipairs(actions) do
    if (a.status == "pending" or a.status == "late") and a.at < nowMs + HOUR_MS then due[#due + 1] = a end
  end
  local shown, chains, folds = Collapse(due, actions)
  local earliest = nowMs
  for _, a in ipairs(shown) do earliest = math.min(earliest, a.at) end
  local view = { nets = ViewModel.Counted(shown, earliest, nowMs + HOUR_MS, offsetMs), chances = ViewModel.ChainChances(actions),
                 crafts = ViewModel.ChainCrafts(actions, lookOf), chains = chains, folds = folds, lookOf = lookOf,
                 offsetMs = offsetMs }
  local rows, hidden, total = {}, 0, 0
  for _, a in ipairs(shown) do
    local cells, look = CellsOf(a, view), lookOf(a.key)
    if cells.chance ~= nil and cells.chance < minChance and not look.volumeUnknown then
      hidden = hidden + 1
    else
      local row = Row(a, cells, look, totalGold, offsetMs)
      row.sortChance, row.sortChar = cells.chance or -1, a.char
      rows[#rows + 1] = row
      total = total + row.netCopper
    end
  end
  table.sort(rows, function(x, y)
    if x.netCopper ~= y.netCopper then return x.netCopper > y.netCopper end
    if x.sortChance ~= y.sortChance then return x.sortChance > y.sortChance end
    if x.item ~= y.item then return x.item < y.item end
    return x.sortChar < y.sortChar
  end)
  return { rows = rows, hidden = hidden, total = ViewModel.SignedMoney(total), count = #rows }
end

--- The footer under Today (SPEC §4.3 wireframe, A2).
---@param today table      From ViewModel.Today
---@param minChance number Percent
---@param gone number|nil  Planned buys a live scan showed are gone
---@return string          e.g. "7 actions · expected +1g 54s 15c · 1 hidden (chance < 50%) · 2 gone"
function ViewModel.TodayFooter(today, minChance, gone)
  local text = today.count .. (today.count == 1 and " action" or " actions") .. " · expected " .. today.total
  if today.hidden > 0 then text = text .. " · " .. today.hidden .. " hidden (chance < " .. minChance .. "%)" end
  if gone and gone > 0 then text = text .. " · " .. gone .. " gone" end
  return text
end
