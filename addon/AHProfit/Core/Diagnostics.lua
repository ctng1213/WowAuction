-- Core/Diagnostics.lua: saves what the addon saw so tools/savedvars_report.py can
-- explain a missing capture without Lua error popups (SPEC §2.2.1 diagnostics, §6).
-- Written once at PLAYER_LOGOUT, which also fires on /reload, just before WoW
-- saves SavedVariables. AHSync never ingests it; it is replaced each session.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB

local Diagnostics = {}
AHProfit.Diagnostics = Diagnostics

-- The APIs the addon depends on or might switch to (SPEC §6), dotted for namespaces.
Diagnostics.APIS = {
  "GetBuildInfo", "GetServerTime", "C_Timer.After",
  "GetNumSkillLines", "GetSkillLineInfo", "GetProfessions", "GetProfessionInfo",
  "GetTradeSkillLine", "GetNumTradeSkills", "GetTradeSkillInfo", "GetTradeSkillRecipeLink",
  "GetCraftDisplaySkillLine", "GetNumCrafts", "GetCraftInfo", "C_TradeSkillUI",
  "GetMerchantNumItems", "GetMerchantItemInfo", "GetMerchantItemLink", "C_MerchantFrame",
  "C_Container.GetContainerNumSlots", "C_Container.GetContainerItemInfo", "GetContainerItemInfo",
  "GetInboxNumItems", "GetInboxItem", "GetInboxItemLink",
  "C_AuctionHouse", "QueryAuctionItems", "CanSendAuctionQuery", "GetAuctionItemInfo",
}

local lastMerchant = nil   -- DescribeMerchant() text from the last merchant this session

--- Build the diagnostics table from the current session.
---@return table  { at, client, apis, errors, skillLines, merchant } as in SPEC §2.2.1
function Diagnostics.Build()
  local errors = {}
  for i, message in ipairs(Events.Errors()) do errors[i] = message end
  return {
    at = Adapter.NowMs(),
    client = Adapter.ClientBuild(),
    apis = Adapter.ProbeApis(Diagnostics.APIS),
    errors = errors,
    skillLines = Adapter.DescribeSkillLines(),
    merchant = lastMerchant,
    setupError = DB.SetupError(),
  }
end

--- Remember the open merchant's raw items for the next save.
local function RecordMerchant()
  lastMerchant = Adapter.DescribeMerchant()
end

--- MERCHANT_SHOW: read the items once they have loaded (debounced).
local function OnMerchantShow()
  Events.Debounce("diagnostics.merchant", RecordMerchant)
end

--- PLAYER_LOGOUT (also on /reload): write diagnostics into the live database.
local function OnLogout()
  local db = DB.Get()
  if db then db.diagnostics = Diagnostics.Build() end
end

Events.Register("MERCHANT_SHOW", OnMerchantShow)
Events.Register("PLAYER_LOGOUT", OnLogout)
