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
  "C_AuctionHouse.ReplicateItems", "C_AuctionHouse.GetNumReplicateItems", "C_AuctionHouse.GetReplicateItemInfo",
  "C_AuctionHouse.GetReplicateItemLink", "C_AuctionHouse.GetReplicateItemTimeLeft",
  "C_AuctionHouse.GetReplicateItemBattlePetInfo", "debugprofilestop", "issecretvalue",
  -- the AH panel and the tooltip hook (M5, still to verify on Forever)
  "AuctionHouseFrame", "AuctionHouseFrame.SearchBar", "AuctionHouseFrame.SearchBar.StartSearch",
  "AuctionHouseFrame.SearchBar.SetSearchText", "C_AuctionHouse.GetBrowseResults", "C_AuctionHouse.SendBrowseQuery",
  "TooltipDataProcessor.AddTooltipPostCall", "GameTooltip.HookScript",
  -- filling the Sell tab from the panel (A29), still to verify on Forever
  "AuctionHouseFrame.SetPostItem", "ItemLocation.CreateFromBagAndSlot", "C_AuctionHouse.GetItemCommodityStatus",
  "C_AuctionHouse.GetAvailablePostCount", "C_AuctionHouse.IsSellItemValid",
  "AuctionHouseFrame.CommoditiesSellFrame.SetPrice", "AuctionHouseFrame.CommoditiesSellFrame.SetQuantity",
  "AuctionHouseFrame.CommoditiesSellFrame.PriceInput.SetAmount", "AuctionHouseFrame.CommoditiesSellFrame.QuantityInput.SetQuantity",
  "AuctionHouseFrame.ItemSellFrame.SetPrice", "AuctionHouseFrame.ItemSellFrame.SetQuantity",
  "AuctionHouseFrame.ItemSellFrame.PriceInput.SetAmount", "AuctionHouseFrame.ItemSellFrame.QuantityInput.SetQuantity",
  -- watching buyouts for the Shopping list (A39)
  "C_AuctionHouse.ConfirmCommoditiesPurchase", "C_AuctionHouse.PlaceBid", "C_AuctionHouse.GetAuctionInfoByID",
  -- your own auctions (A41)
  "C_AuctionHouse.QueryOwnedAuctions", "C_AuctionHouse.GetNumOwnedAuctions", "C_AuctionHouse.GetOwnedAuctionInfo",
  -- post, purchase and craft events (A44-A47)
  "C_AuctionHouse.PostCommodity", "C_AuctionHouse.PostItem", "C_AuctionHouse.StartCommoditiesPurchase",
  "ItemLocation.GetBagAndSlot", "BuyMerchantItem", "C_MerchantFrame.GetItemInfo",
  -- auction mail (A48)
  "GetInboxHeaderInfo", "GetInboxInvoiceInfo", "AUCTION_EXPIRED_MAIL_SUBJECT", "AUCTION_REMOVED_MAIL_SUBJECT",
  "AUCTION_WON_MAIL_SUBJECT", "TakeInboxMoney", "AutoLootMailItem",
}

-- Namespaces whose function lists we need to write the modern readers (SPEC §6).
Diagnostics.NAMESPACES = { "C_TradeSkillUI", "C_MerchantFrame", "C_AuctionHouse", "C_Container", "C_Item" }

local lastMerchant = nil   -- DescribeMerchant() text from the last merchant this session
local lastTradeSkill = nil -- DescribeTradeSkill() text from the last trade skill window this session

--- Build the diagnostics table from the current session.
---@return table  { at, client, apis, errors, skillLines, merchant, tradeSkill, namespaces, professions, setupError, scan } (SPEC §2.2.1)
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
    tradeSkill = lastTradeSkill,
    namespaces = Adapter.DescribeNamespaces(Diagnostics.NAMESPACES),
    professions = Adapter.DescribeProfessions(),
    setupError = DB.SetupError(),
    scan = AHProfit.Scan.Replicate.Describe(),
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

--- Remember the open trade skill window's raw state for the next save.
local function RecordTradeSkill()
  lastTradeSkill = Adapter.DescribeTradeSkill()
end

--- TRADE_SKILL_SHOW: read the modern window once it has loaded (debounced).
local function OnTradeSkill()
  if not Adapter.HasCraftWindow() then Events.Debounce("diagnostics.trade", RecordTradeSkill) end
end

--- PLAYER_LOGOUT (also on /reload): write diagnostics into the live database.
local function OnLogout()
  local db = DB.Get()
  if db then db.diagnostics = Diagnostics.Build() end
end

Events.Register("MERCHANT_SHOW", OnMerchantShow)
Events.Register("TRADE_SKILL_SHOW", OnTradeSkill)
Events.Register("PLAYER_LOGOUT", OnLogout)
