# WowAuction — Spec (draft v0.4, 2026-10-16)

Two pieces:

1. **AHSync**: a desktop companion app and the **brain**.
   - Start it whenever you're ready to play. It works out where it left off and catches up on auctionhouse.fyi prices.
   - It keeps a local 90-day history, works out every recommendation, and shows them in a **local dashboard** (out of game).
   - It also writes the same results into the addon.
2. **AHProfit**: an in-game addon that acts as the **eyes and hands**.
   - It records your characters, professions, recipes, inventory and gold.
   - It runs its own auction house scans.
   - In game, it shows the recommendations, adds tooltip lines, and searches the auction house so you can buy with one click.

What it recommends:

1. buy and disenchant
2. craft and sell now
3. a weekly plan: buy or craft now and sell later, plus **when to sell what you already own** for the most profit

Names are placeholders.

---

## 1. Constraints that shape the design

| Fact | Consequence |
|---|---|
| A WoW addon runs in a Lua sandbox with **no network access**. It reads files only at login or `/reload`, and it writes its SavedVariables to disk only at logout, `/reload` or disconnect. | AHSync does all the HTTP work and the calculations. The two talk through files: the addon's SavedVariables go to AHSync, and generated `MarketIntelligence.lua` and `PortfolioPlan.lua` come back to the addon. |
| WoW: Forever has **no official Blizzard AH API**. auctionhouse.fyi (AHF) prices come from players scanning in game. | Data is only as fresh as someone's last scan. Every price keeps its observation time, and results show their age and a confidence level. |
| AHF CDN retention: public **hour candles 7 days**, public **day candles 7 days**. Premium adds 14 days of hours and 90 days of days. | We build our own 90-day hourly history. See §2.1. |
| Addons **cannot buy or post automatically**. Each action needs a hardware click. | We recommend and pre-fill searches. You click. |
| Forever client: folder `_classic_beta_` (may be renamed at the Nov 4 launch), `## Interface: 16001`, client build 1.60.x. | The WoW path is configurable, and every game-API call goes through a thin adapter. |

## 2. Data sources

### 2.1 auctionhouse.fyi API v1

- Base: `https://auctionhouse.fyi/api/v1`. CDN: `https://cdn.auctionhouse.fyi/` (from `GET /config`).
- Auth is optional: `Authorization: Bearer ak_…`. The key stays in local config and never goes into logs or URLs.
- Units: price is integer **copper per unit**, times are **Unix ms**, hour = `floor(ms/3600000)`, day = `floor(ms/86400000)`. A `null` price means nothing listed, never zero.
- Market id: `forever.<normal|pvp|rp>.<alliance|horde>.us`. Configurable as a list; the default is **`forever.pvp.horde.us`**.
- Item **key**: `item + 2147483648 × v`, where `v` encodes the random suffix. Base item id = `key mod 2147483648`.
- `429` comes with `Retry-After`, and we honour it. `403 {feature}` means our tier lacks it, so we skip that feature.

| Purpose | Call | Without key | With Premium |
|---|---|---|---|
| Capabilities | `GET /me` | `historyDays 7`, 60 requests/min | `historyDays 90`, + `prices.live`, `depth`, `books` |
| File index | `GET /ah/:market/files` → `sealed.h1`, `d1`, `caps.*` | ✔ | ✔ with capability URLs |
| Hour candles | CDN `candles/v1/h1/<m>/<hour>.json.gz` | last 7 days | + days 8–14 via `h1c` |
| Day candles | CDN `candles/v1/d1/<m>/<day>.json.gz` (7 days per file) | ✔ | `d1c` = 90 days in one file |
| Current prices | `GET /ah/:market/snapshot` (as of the top of the hour) | ✔ | live table `GET /ah/:market?since=` |
| Volume (listed/sold/expired per day) | `caps.volume` | ✗ (estimated, §3.3.1) | ✔ |
| Item metadata | `GET /items` (name, quality, ilvl, class/subclass, vendor buy/sell, stack, variants) | ✔ | ✔ |

Rules:

- **Only request files we were told exist** (hours ≤ `sealed.h1`). A guessed future file caches a 404 at the edge.
- **Building 90 days without Premium.** Public hour files cover 7 days, so running AHSync at least once every **6 days** misses nothing. Over time the local DB fills to the full 90 days. AHSync warns when the gap since the last run gets close to 7 days. Premium only speeds up the first fill and adds real volume data and live prices. Premium is also earned for free by uploading scans with the AHF addon (7 days per credited scan).
- **A gap of more than 7 days without Premium loses those hours for good.** Public day candles are also kept only 7 days, so nothing covers them. AHSync reports the lost range on the Status page.
- **Characters map to markets** as `forever.<ruleset>.<faction>.us`. The faction comes from the character. WoW reports the realm's name but not its ruleset, so the ruleset comes from the config: the realm's `[realms]` entry (§3.6) when there is one, otherwise the only market in `markets` for that faction. Only when you sync several markets for one faction (or none) does a realm need a `[realms]` entry; until then its characters have no market and Status says how to fix it (S12, D34). AHF has no neutral-AH market, so the neutral AH is not planned in v1 (§9).

Verified 2026-10-16, without a key: `/me`, `/files`, the snapshot, public h1 and d1 files, and `/items` all work.

**Verified 2026-10-16 with our key.** The key gives the Premium feature set through the **contributor** role, not a paid plan:

| | Without key | With our key (roles `free`, `contributor`) |
|---|---|---|
| `historyDays` | 7 | **90** |
| `apiPerMinute` | 60 | **600** |
| `feedMinutes` | 60 | 1 |
| Features | `prices.snapshot`, `candles.daily`, `candles.hourly` | adds `prices.live`, `candles.minute`, `books`, `depth`, `signals`, `live`, watchlist, alerts and boards, plus the API and upload features |
| `/files` caps | — | `d1`, `h1`, `m1`, `volume` (plus top-level `books` and `live`) |

- **Contributor access expires.** `/me` returns `contributor.until`, which was 2026-10-23 (7 days out), with `scans7d` = 4. Every credited scan uploaded with the AHF addon extends it.
- **So AHSync must re-check `/me` on every run** and degrade cleanly when Premium features disappear (§2.1 `403` rule).
- **The Status page shows the expiry**: "Premium via contributor until 23 Oct · upload scans to keep it". It warns 2 days before.
- **Minute candles (`m1`, `candles.minute`)** are available but not used in v1.

**Response formats** (recorded 2026-10-16 into `tests/fixtures/ahf/`; AHF publishes no API docs, so the URL patterns come from its site's `cdnrules` script):

| File | URL (relative to the CDN) | Rows |
|---|---|---|
| Hour candles | `candles/v1/h1/<m>/<hour>.json.gz`; Premium: `candles/v1/h1c/<m>/<caps.h1[day]>/<hour>.json.gz` (token per day, file per hour) | `items: [[key, [[t, open, high, low, close, qty], …]], …]` |
| Day candles | `candles/v1/d1/<m>/<day>.json.gz` (7 days); Premium: `caps.d1`, the `d1c` file (90 days) | same as hour candles, with day `t` |
| Snapshot | `snap/v1/<m>/latest.json` → `{hour, at, path}`; then `path` | `rows: [[key, price, qty, scanAt], …]` |
| Order book | `files.books[].url` (`books/v1/<m>/<at>-<token>.json.gz`), with `complete` | `entries: [[key, price, stack, band, count], …]`, with bands shifted on Forever (verified 2026-10-17): AuctionHouseFYI's addon reads Forever's time left as retail's 0–3 and uploads it + 1, but Forever returns 1–4, so short arrives as 2, medium as 3, long as 4 and very long as 0, and band 1 never appears. A book without band 1 is mapped back to WoW's codes; one with band 1 is read as WoW's codes, with 0 as very long, the conservative choice for §3.3.2 (V28); `bidEntries` has 8 fields |
| Volume | `caps.volume` | `items` (empty for this market so far, so the row format is still unknown) |
| Items | `GET /items` | `items: {id: {n, q, il, c, sc, sell, buy, st, …}}`, `variants: {key: {n, q, …}}` for suffix keys, plus `suffixes`, `bonusLists`, `bonusTrees` |

- A missing hour file is a 404, which is why only listed files are requested (§2.1 rules).
- Capability tokens are secrets: the recorder replaces them with `cap-…` placeholders.
- `/me` contains the account's id and email; the recorder replaces those too.

**The app works fully without Premium.** Each Premium feature only improves something that already has a free fallback:

| Premium feature | Used for | Without it |
|---|---|---|
| `volume` / `depth` | real daily listed / sold / expired, the main input to the sell probability | our own estimates (order-book diffs, mail, quantity drops; §3.3.1) |
| `historyDays 90` (`h1c`, `d1c`) | filling the 90-day history quickly, and recovering gaps up to 14 days | AHSync builds the history itself by running at least every 6 days (§2.1 rules) |
| `prices.live` | prices refreshed every 15 minutes | the hourly snapshot and your own in-game scans |
| `books` | AHF's full order books | your own scans' order books |
| `apiPerMinute 600` | faster catch-up | 60 per minute: the first sync is slower, but nothing is lost |

The choice is made per run from `/me`, and per item and day for volume (§3.3.1). Losing Premium therefore changes the quality of the estimates, never whether the app works. Scenarios S11 and V14–V17 cover it.

**Coverage warning (beta).** `forever.pvp.horde.us` had 8,929 items, all from **one scan about 7 days old**, with 1 contributor in 7 days. Its public h1 files were empty. Our own scans (§2.2) are therefore a first-class source.

### 2.2 The AHProfit addon's SavedVariables

AHSync reads `WTF/Account/*/SavedVariables/AHProfit.lua`. It watches the file and re-reads it whenever WoW rewrites it (on logout or `/reload`). The addon records:

| Data | Captured when |
|---|---|
| **Characters**: id `Name-Realm`, name, realm, faction, class, level, gold | login, money change |
| **Professions** and skill level per character | login, skill change |
| **Recipe definitions per profession** (account-wide): product, yield min/max, reagents with counts, vendor-sold reagents, skill needed, cooldown | profession window opened (once per profession is enough) |
| **Known recipes per character**: spell ids | profession window opened on that character |
| **Inventory per character**: bags, bank, mail, each with a **bound** flag | bag/bank/mail events |
| **Own active auctions**: item, unit price, stack, time-left band | AH events |
| **Own AH scans** (full or partial): scan time, kind, and the order book as rows `[key, unitPrice, stack, band, count]` | AH open + scan button / auto |
| **Posts**: character, item, stack, unit price, duration, deposit, time | a hook on the post call |
| **Purchases**: character, item, quantity, unit price, source (AH or vendor), time | AH: a hook on the buy call (`hooksecurefunc`, which only observes), confirmed by the "auction won" message and mail. Vendor: a hook on the merchant buy call |
| **Crafts**: character, recipe, count, time | spellcast success + bag change |
| **Mail to your own characters**: from, to, item, quantity, time (matches plan `mail` actions) | a hook on the send-mail call |
| **Auction mail**: sold / expired / cancelled / won, item, quantity, price (from the invoice), time | live system messages; mailbox opened |
| **Item rules** set in game (blacklist, §3.4.2) | a right-click on a row |
| **Reserves**: per character, items you keep for yourself, all or at least N units (§3.4.3) | a right-click on a row: Keep all, Keep at least…, Sell normally |

**You never have to report anything by hand.** The inventory snapshot (bags, bank, mail, own auctions) is the source of truth for *what* you own, so a missed event can't make the app think you own something you don't. Events only say *where* items came from and *what they cost*. Items from outside the AH (farmed, traded, mailed between characters) have no recorded cost and are valued at their opportunity cost. Auctions that sell or expire while you're offline are learned at your next mailbox visit.

The app merges own scans into the price tables as source `local`, next to AHF data. **One close per (item, hour):** when AHF and a local scan both have a value for the same item and hour, statistics use the local one. It is an exact observation, and it may be the very scan you uploaded to AHF, which would otherwise be counted twice.

Reading rules:

- AHSync re-reads the file only after its modification time has been unchanged for 2 seconds, so it never parses a half-written file.
- The file carries `formatVersion`, and every event carries a unique `event_id`, so ingest is idempotent. A file that won't parse is retried from WoW's `AHProfit.lua.bak` (§5.2).
- **Pruning handshake.** Order books, events and mail would make SavedVariables grow without limit, which slows login and logout. AHSync writes `ingestedUntil` into `PortfolioPlan.lua` (§3.5) as a **map from character id to time** (ms): for each character, the newest event or scan time stored for that character. On load, the addon deletes each character's scans, posts, purchases, crafts and mail rows at or before that character's value.
  - **Per character, because `PortfolioPlan.lua` is shared by every account** on the machine: one global time could pass events of another account whose file couldn't be read.
  - **Never past a row that was set aside.** When any of a character's rows (a scan or an event) was not stored, for example a scan from a realm missing in `[realms]` or a row the parser rejected, that character's value stops 1 s before the oldest such row. The addon then keeps it, and it is ingested once the problem is fixed. It keeps unconfirmed attempts younger than 1 h (§5.2), so a confirmation that arrives later still reaches AHSync, which upserts the event by its id.

#### 2.2.1 SavedVariables layout (format version 1)

The addon has one account-wide global, `AHProfitDB` (`## SavedVariables: AHProfitDB`). This layout is the contract between `Capture/*`, which writes it, and `ingest/savedvars.py`, which reads it. A1 round-trips it. A change to it bumps `formatVersion` and adds a migration on both sides (FM13, FM23).

Field rules:
- Money is integer copper; prices are per unit.
- Times are UTC Unix ms. WoW's clock (`GetServerTime()`, through the Adapter) counts whole seconds, so every time is seconds × 1000.
- `charId` is `Name-Realm`. The market comes from the character's faction and the config's `[realms]` table (§2.1), so it isn't stored.
- `key` is the AHF item key (§2.1). `Adapter.lua` computes it from the item link. A random suffix comes from the link's bonus ID: `v = 2 × suffix id`, with the suffix id looked up in AHF's bonus table (`Data/BonusSuffixes.lua`, §6 item 11). An item whose suffix can't be worked out (a bonus ID not in the table, or a Classic-style suffix field) is written as **`key = −itemId`**: some suffix variant of that item, with `v` unknown. AHSync stores such rows but leaves them out of every valuation and recommendation, so a suffixed green is never priced at its base item's price.
- **Unknown is left out, never 0.** A field the client can't tell us (a recipe's `skill` or `cooldown` on Classic APIs) is omitted, and AHSync reads it as unknown (None). `cooldown = 0` means "no cooldown".
- **A snapshot is written only when it is complete.** Bags, bank, mail and professions each replace the previous value, so a partial read would look like lost items. A location is skipped, and the old value kept, when an occupied slot has no item link yet (not cached), when the mailbox has loaded fewer mails than it holds, or when a collapsed Skills header hides profession lines.
- `band` is WoW's time-left code: 1 short, 2 medium, 3 long, 4 very long.
- **Bulk rows are compact strings:** rows are separated by `;` and fields by `,`, in the order listed. Everything else is a plain table.
- **Event ids** are `charId:timeMs:seq`. `seq` restarts at 0 each second for each character. Only one character is logged in at a time, so ids are unique.
- Profession names are the client's English names; only enUS clients are supported in v1.

```lua
AHProfitDB = {
  formatVersion = 1,
  addonVersion = "0.1.0",
  refreshRequestedAt = nil,                    -- ms; set by /ahp refresh (§3.1.1)
  characters = {
    ["Amaya-Grommash"] = { name = "Amaya", realm = "Grommash", faction = "Horde",
                           class = "MAGE", level = 60, gold = 1234567, updated = 1760000000000 },
  },
  professions = {                              -- per character
    ["Amaya-Grommash"] = { Enchanting = { skill = 225, max = 300 } },
  },
  recipes = {                                  -- account-wide; profession -> spell id -> definition
    Blacksmithing = {
      [2663] = { product = 2857, yieldMin = 1, yieldMax = 1, skill = 1, cooldown = 0,  -- cooldown in s
                 reagents = "2840,2" },        -- "key,count;key,count"; skill/cooldown omitted when unknown
    },
  },
  vendorReagents = "2320;2321",                -- keys a merchant was seen selling (§3.4B)
  knownRecipes = { ["Brugan-Grommash"] = "2662;2663" },        -- spell ids, ascending
  inventory = {                                -- per character; each location replaced whole, with its own time
    ["Amaya-Grommash"] = { bags = "2840,20,0;6948,1,1", bagsAt = 1760000000000,  -- "key,qty,bound(0/1)"
                           bank = nil, bankAt = nil,           -- nil until first visited; "" = visited, empty
                           mail = nil, mailAt = nil },
  },
  itemNames = { [6948] = "1,Hearthstone" },    -- key -> "quality,name" of every item seen in an inventory, read
                                               -- from its link; names items AHF's list leaves out, e.g. soulbound (S13)
  ownAuctions = {                              -- latest snapshot per character, replaced whole
    ["Amaya-Grommash"] = { at = 1760000000000, rows = "2857,4100,5,3,1",  -- "key,price,stack,band,count"
                           ends = "2857,5,1760039600000,4100" },  -- "key,units,end ms,price" per auction; addon only (A41)
  },
  scans = {                                    -- oldest first; at most 12 un-ingested full scans (§5.1)
    { id = "Amaya-Grommash:1760000000000:0", char = "Amaya-Grommash", at = 1760000000000,
      kind = "full", coverage = 1.0,           -- kind "full" | "partial"; coverage 0..1
      rows = "2589,38,20,3,4;2589,45,20,4,1" },  -- "key,price,stack,band,count"
  },
  events = {                                   -- oldest first; fields per type below
    { id = "Amaya-Grommash:1760000000000:1", type = "post", char = "Amaya-Grommash",
      at = 1760000000000, key = 2857, stack = 5, price = 4100, durationH = 24, deposit = 60,
      confirmed = true },
  },
  rules = { [2589] = { rule = "no_buy", setAt = 1760000000000 } },   -- set in game; "none" = cleared (§3.4.2)
  reserves = { ["Amaya-Grommash"] = "2589,10;4306,-1" },  -- "key,qty" kept for yourself; qty -1 = all (§3.4.3)
  mailSeen = { ["sold:2589:20:48:1760000000000"] = 1762678400000 },  -- auction mails already turned into events (A48)
}
```

| Event `type` | Fields besides `id`, `char`, `at` | Captured by |
|---|---|---|
| `post` | `key`, `stack`, `price`, `durationH`, `deposit`, `confirmed` | `Capture/Posts` |
| `purchase` | `key`, `qty`, `price`, `source` (`ah` / `vendor`), `confirmed` | `Capture/Purchases` |
| `craft` | `spell`, `count` | `Capture/Crafts` |
| `mail_sent` | `to` (charId), `key`, `qty` | `Capture/Mail` |
| `auction_mail` | `outcome` (`sold` / `expired` / `cancelled` / `won`), `key`, `qty`, `price` (sold and won only) | `Capture/Mail` |
| `skip` | `action` (action id from `PortfolioPlan.lua`) | the row menu's "Mark as skipped" |
| `payout` | `key`, `qty`, `money` (copper taken from the mail), `deposit` (the invoice's, copper; optional) | `Capture/Mail`: an auction's gold collected from the mailbox, so *posted*, *sold* and *paid out* are three separate events |
| `disenchant` | `key` (the item), `results` (`"key,qty;..."`) | `Capture/Crafts`: a disenchant and what it gave |

- **`diagnostics`** (written by `Core/Diagnostics.lua` at `PLAYER_LOGOUT`, which also fires on `/reload`): `{ at, client, apis, errors, skillLines, merchant, tradeSkill, namespaces, professions, setupError, scan, traces }`. It holds the client build, which of the APIs the addon depends on exist (`"name=type;…"`), the handler errors caught this session (at most 10), the raw Skills-tab rows, the last merchant's raw items, the last trade skill window's raw state, the function names inside the modern API namespaces (`"C_X=FnA FnB;…"`), the raw `GetProfessions` slots, any database setup error, and the last full-scan read (`"calls=..|n=..|skipped=..|pet=..|count=..|buyout=..|band=..|link=..|byId=..|left=1:..,2:..|steps=..|maxStepMs=..|readMs=..|sortMs=..|encodeMs=..|saveMs=..|notifyMs=..|kind=..|rows=..|dropped=.."`: skips per reason, auctions keyed by item id, the raw time-left histogram, the slowest step per phase, and the time the redraws after a complete scan took, which run in a frame of their own, A51, §4.2), and a trace of the session's last 10 capture decisions for the captures that hook a click (`traces`, `";"`-joined, oldest first: each money take with the mail's money, invoice and item and whether it became a payout, A54; each Disenchant's target, its loot and whether it became an event, A55). It exists so `tools/savedvars_report.py` can explain a missing capture without Lua error popups (§6). AHSync never ingests it, and each session replaces it.
- **Professions and known recipes follow the file.** The addon writes a character's professions whole and only ever adds known recipes. AHSync replaces that character's professions and known recipes with the file's when the file's `characters[id].updated` is at least the stored one; an older file changes nothing. So an unlearned profession disappears, and a relearned one starts again at its new skill.
- `confirmed` starts `false` when the hook fires and turns `true` when the action is confirmed (§5.2). The same row is updated in place.
- **How the addon captures them** (A44–A47; every call UNVERIFIED on Forever and probed in the diagnostics):
  - `post`: hooks that only observe `C_AuctionHouse.PostCommodity(location, duration, quantity, unitPrice)` and `PostItem(location, duration, quantity, bid, buyout)`. The item is read from the bag snapshot taken before the post (the slot is empty when the hook runs); `durationH` maps the duration 1/2/3 to 2/8/24 h, the engine's durations (verified on Forever 2026-10-18); the deposit is `Engine/Fees`' formula, as AHSync computes it. `AUCTION_HOUSE_AUCTION_CREATED` confirms the oldest unconfirmed post.
  - `purchase` on the AH: a commodity purchase uses the unit price the AH quoted (`COMMODITY_PRICE_UPDATED` after `StartCommoditiesPurchase`), is added on `ConfirmCommoditiesPurchase` and confirmed on `COMMODITY_PURCHASE_SUCCEEDED`; a buyout (`PlaceBid` at the buyout, read with `GetAuctionInfoByID`) is confirmed on `AUCTION_HOUSE_PURCHASE_COMPLETED`. A bid below the buyout is not a purchase.
  - `purchase` from a merchant: `BuyMerchantItem(index, quantity)`, at the merchant's price per unit; confirmed at once.
  - `craft`: `UNIT_SPELLCAST_SUCCEEDED` for the player with a spell among the character's known recipes; one event per cast, `count` 1.
  - `disenchant` (A55): `UNIT_SPELLCAST_START` of Disenchant (13262) for the player notes the item in the locked bag slot (the cast's target); `UNIT_SPELLCAST_SUCCEEDED` marks it cast and `INTERRUPTED` / `FAILED` drop it; the next `LOOT_OPENED` reads the loot (coin left out) into `results`, summed per item and ordered by key. AHSync passes the item's average cost to the results, split by units (T18), and matches the event to a planned DE action like a craft (T19). SPEC GAP: splitting by units, not by value, gives dust and essence the same cost per unit.
  - `auction_mail` (A48): read from the mailbox when it shows (`MAIL_INBOX_UPDATE`). A seller invoice (`GetInboxInvoiceInfo`) is `sold` at its sale price ÷ count, a buyer invoice or "Auction won: …" `won`, and the subjects "Auction expired: …" and "Auction cancelled: …" (the client's `AUCTION_EXPIRED_MAIL_SUBJECT` and `AUCTION_REMOVED_MAIL_SUBJECT`) `expired` and `cancelled`, keyed by the attachment's link, or, for a sold invoice (which has no attachment), by the item's name in `itemNames`. Each mail becomes one event: a fingerprint (outcome, item, quantity, price and its send time, from the days left of a 30-day auction mail) is kept in `mailSeen` until 31 days after it was sent. The event's `at` is when the mail was first seen, so the pruning handshake never drops it before AHSync reads it; `sentAt` carries the send time.
  - `payout` (A54, T17): a hook that only observes `TakeInboxMoney(index)` and `AutoLootMailItem(index)` reads the mail before the take lands (its header still shows the money): a seller invoice with money becomes one `payout` of that money, with the deposit the invoice reports, keyed like a sold mail, deduplicated by a `paid:` fingerprint in `mailSeen`. AHSync links each payout to the oldest sold mail of the same character, item and quantity not yet paid, and realizes its profit once: money − units × cost basis − the deposit (the invoice's; without one, the post's for those units), because the money includes the deposit coming back (§3.4.1).
- Untracked changes (FM21) are not events. AHSync derives them by comparing consecutive inventory snapshots with the events between them.

## 3. AHSync (companion app)

### 3.1 Lifecycle

```
start
 ├─ load config.toml; open SQLite data/ahsync.db
 ├─ GET /me → features; warn if > 6 days since last run (risk of missing hours)
 ├─ refresh item metadata if > 24h old
 ├─ read AHProfit SavedVariables (characters, recipes, inventory, own scans, events, item rules)
 ├─ match events to plan actions and posts to outcomes (§3.4.1)
 ├─ CATCH-UP per market
 │    last = sync_state.last_hour
 │    target = files.sealed.h1
 │    for each missing hour in (last, target]:
 │        ≤ 7 days old   → public h1
 │        7–14 days old  → h1c (if Premium)
 │        older          → d1c day candles if Premium; otherwise lost (§2.1)
 │    day candles: public d1 (7 days) or d1c (90 days); current: snapshot or live table
 │    volume: once a day if Premium
 │    commit and advance sync_state after each hour, so a crash resumes there
 ├─ prune > 90 days
 ├─ FULL REFRESH (§3.1.1) → market intelligence, recommendations, plan, dashboard data, the addon files
 ├─ serve dashboard at http://localhost:8765
 └─ STEADY STATE (mostly idle; see §3.1.1)
      every hour at :06 → store the new h1 hour + snapshot (+ AHF order book if Premium `books`); no recompute
      every 15 min (Premium) → store the live table; no recompute
      every hour at :00 → STATUS PASS (§3.1.1): late / missed transitions, missed-peak re-plans; no history reads
      SavedVariables changed → re-read → INCREMENTAL UPDATE of the changed items only (§3.1.1)
      first run after `daily_refresh_time`, or "Refresh now" → FULL REFRESH
      daily → d1/volume refresh, prune; weekly VACUUM
```

Every write is an idempotent upsert, so re-running any range is harmless.

#### 3.1.1 Refresh model: heavy work rarely, small updates often

All the heavy lifting happens in AHSync, and only in a **full refresh**. Everything else is a small, targeted update.

**Full refresh** (`engine.refresh_all`). It runs at start, at the first run after `daily_refresh_time` (default 06:00 local), on the dashboard's **Refresh now** button, or when the addon sets a refresh request (`/ahp refresh`, picked up on the next `/reload` or logout). This is the only time AHSync reads through history:
1. **Builds `item_stats`**, one materialized row per item and market with everything §3.3 defines: medians, percentiles, trend, `how`, `sales_how`, `sell_score`, best windows, volume per source, tier sell-through, undercut share, calibration and confidence components. It works item by item and streams the candle and book tables in key order, so memory stays flat.
2. **Precomputes each item's options:** sell values and hold values (§3.4), disenchant EV, the crafting options with their sourcing, flip candidates, and the sell-probability curve (below).
3. **Builds the recommendations, the plan, the views and the addon files** (`MarketIntelligence.lua`, `PortfolioPlan.lua`).

**Incremental update** (`app.refresh.replan_all`, IU13). It runs when SavedVariables changes (inventory, posts, purchases, crafts, mail, rules, reserves or a new scan), after the tracking pass and before `PortfolioPlan.lua` is written.
- It re-plans every market from its **published** intelligence and the new character state, and rebuilds the pages: the plan, Today, Week, Flip, Inventory, Craft and Disenchant. No history is read and the intelligence is not rebuilt, so it costs the recommendation half of a full refresh, not the analysis half.
- The plan is re-planned whole rather than item by item, because budgets and each selling window's capacity are shared across items (§3.4C.4); a per-item update would let two items spend the same gold.
- It is published like a full refresh (staged, then swapped in), keeping the live intelligence; a failure leaves the previous plan and pages live.
- A new in-game scan updates `cur`, `curQty`, the book and the current supply for the scanned items only (`analysis.market.with_scan`, IU6): the re-plan applies your scans made after the intelligence was built to the items they priced, in memory, for the plan and the pages; the stored intelligence stays as analysis built it.
- Hourly and live AHF data are **stored but not applied** until the next full refresh, so the numbers stay stable through the day. The exception is items you scanned in game, whose prices update immediately. The dashboard shows "prices newer than numbers · Refresh now" when newer data is waiting: each `/api/views` response carries `prices_newer`, true when the newest stored AHF price is newer than the published intelligence (IU2).

**Status pass** (`plan.status_pass`, run by `sync/scheduler`). Time passing changes the plan even when no data does, so AHSync runs this once an hour at :00. It runs on precomputed options only, with no history reads and no simulation:
- It moves `pending` actions to `late` or `missed` by the rules in §3.4.1.
- For each post that just became `missed`, it runs the missed-peak re-plan (§3.4.0), which only compares the item's precomputed windows.
- It re-runs the cheap budget allocation and rewrites the views and `PortfolioPlan.lua` if anything changed.

**Sell-probability curve.** The queue simulation (§3.3.2) runs **only in AHSync**, during a full refresh. The chance of a post made now depends on the hour you post it, because the window catches different buyers (P21, P22). So the curve is a grid:
- **Rows:** one for each of the 24 clock hours, starting at the refresh hour. Each row is `P` for a post made at the start of that hour over the posting-now window.
- **Columns:** 8 points of units ahead: 0, 1, 2, 5, 10, 20, 50, 100.
- It is computed at the current price tier, and it carries the inputs needed to blend it.
- Analysis computes the full 24 × 8 grid for **every** item and stores it in the intelligence (§5.0), because it is market-wide and doesn't know your lists. The export then trims it: items on the shortlist keep 24 × 8, and other items, which only need the tooltip, get the 24 × 3 subset (the 0, 5 and 20 ahead columns, unchanged), to stay within the `MarketIntelligence.lua` budget.
- **Encoding: one character per point.** Each `P` is quantized to 2% steps, `n = floor(P × 50 + 0.5)` (0–50), and written as `string.char(35 + n)`, row by row, in the item's string. Codes 35–85 contain no `"` and no `\`, so no escaping is needed. A 9k-item market then needs about 650 KB of curves, which leaves room in the 2 MB budget for everything else. **AHSync uses the quantized curve too**, not the raw simulation result, so Python and Lua read identical values (IU11).

In each column the units ahead are modelled as one block that stays the whole window, which is conservative: the curve can't know which future competitors expire when. Each value is stored in 2% steps (below).

When a price or the competition changes, AHSync and the addon both pick the row for the current hour and interpolate linearly between the units-ahead points, in O(1) time. More than 24 h after the refresh, they reuse the row for the same clock hour, and the age badge already warns that the data is old. The full simulation is never run live.

**Idle means idle.**
- Between events AHSync sleeps on timers. It checks the SavedVariables modification time every 5 s, which is a single file stat.
- The dashboard serves precomputed JSON and does no computation per request.

A full refresh builds into staging tables and swaps them in with one transaction, so a failure halfway leaves the previous numbers in place (§5.2).

#### 3.1.2 Performance budgets (AHSync)

| What | Budget |
|---|---|
| CPU when idle | ≈ 0% (timers and one file stat every 5 s) |
| Memory when idle | ≤ 150 MB |
| Memory during a full refresh | ≤ 400 MB peak (streaming, item by item) |
| Full refresh time | ≤ 120 s for one market of about 9k items with 90 days of hourly history (it runs once a day in the background; weekly patterns and the 24 × 8 curve for every item, §5.0) |
| Incremental update | ≤ 1 s (a whole re-plan from the published intelligence: 0.96 s measured on a real 15k-item market, 2026-10-18; applying a full in-game scan's 12k prices adds 0.13 s, IU6) |
| Dashboard request | ≤ 50 ms (precomputed JSON, paged at 100 rows) |
| `MarketIntelligence.lua` size | ≤ 2 MB (`PortfolioPlan.lua` ≤ 200 KB) |

Storage rules:
- SQLite runs in WAL mode.
- Every write batch is one transaction.
- Indexes on `(market, key, t)`; candle tables are `WITHOUT ROWID`.
- AHF files are streamed and decompressed one hour at a time, never all at once.
- `item_stats` is replaced atomically, so the dashboard never sees half a refresh.

`tools/bench.py` runs a full refresh on a generated 9k-item world with 90 days of hourly closes, listed units and sales rates for every item (the worst case), in a fresh process without memory tracing, and reports its time and the process peak memory against these budgets. The incremental update is not benchmarked yet.

### 3.2 Storage: SQLite

**Why SQLite rather than Postgres:**

- one user, one machine, one writer process
- a single file, built into Python, nothing to install or run
- backup = copy the file
- handles tens of millions of rows

All DB access lives in `store/` (`db.py`, plus modules split off it by responsibility, such as the SavedVariables ingest writes). Moving to Postgres would only mean replacing that package, if this ever becomes multi-user or hosted.

```
items(key PK, item_id, name, quality, ilvl, class, subclass, vendor_buy, vendor_sell, stack, updated)
candles_h1(market, key, t, open, high, low, close, qty, src,   PK(market,key,t,src))   WITHOUT ROWID
candles_d1(market, key, t, open, high, low, close, qty, src,   PK(market,key,t,src))   WITHOUT ROWID
volume_d1(market, key, day, listed, sold, expired, src, conf, PK(market,key,day,src))
sales_h1(market, key, t, sold_per_hour,                      PK(market,key,t))        -- book-pair sales rate per hour start t (§3.3 sales_how)
calibration(market, key, source, factor, samples, updated,   PK(market,key,source))   -- source: ahf | mail
current(market, key, price, qty, at, src,                    PK(market,key,src))
scans(scan_id PK, market, src, char_id, at, kind)            -- src: local (your scan) | ahf (Premium books); char_id null for ahf
book_entries(scan_id, key, price, stack, band, count,        PK(scan_id,key,price,stack,band))
auction_mail(event_id PK, char_id, key, qty, price, outcome, at)   -- index (char_id,key,at,outcome)
payouts(event_id PK, char_id, key, qty, money, at, mail_id, realized, deposit)   -- gold taken from a sold mail; realized copper, set once (T17)
disenchants(event_id PK, char_id, key, results, at, action_id)   -- results "key,qty;..." by key (T18)
posts(post_id PK, char_id, market, key, stack, price, cost_each, p_at_post, duration_h, deposit, at, outcome, resolved_at, action_id)
purchases(event_id PK, char_id, key, qty, price, source, at, action_id, confirmed)   -- index (char_id,key,at)
crafts(event_id PK, char_id, spell_id, count, cost_each, at, action_id)   -- index (char_id,spell_id,at)
cost_basis(key PK, qty, total_cost, untracked_qty)
actions(action_id PK, run_at, slot_ms, char_id, kind, key, qty, price, expected_net, conf, status, done_qty, done_at, parent_id,
        window_start_ms, window_end_ms, anchor_net, anchor_window_ms, replaces_id, week_start_ms, peak_ms,
        source)   -- buys: 'vendor' or 'ah' (D41)
item_rules(key PK, rule, set_at, source)
reserves(char_id, key, qty,  PK(char_id,key))   -- qty -1 = all (§3.4.3)
characters(id PK, name, realm, faction, market, class, level, gold, updated)
professions(char_id, profession, skill, max_skill,           PK(char_id,profession))
recipes(profession, spell_id, product_key, yield_min, yield_max, skill_req, cooldown_s,  PK(profession,spell_id))
reagents(profession, spell_id, key, count,                   PK(profession,spell_id,key))
known_recipes(char_id, spell_id,                             PK(char_id,spell_id))
inventory(char_id, location, key, qty, bound,                PK(char_id,location,key,bound))
own_auctions(char_id, key, price, stack, band, count, seen,  PK(char_id,key,price,stack,band))
snapshots(char_id, location, at,                             PK(char_id,location))   -- capture time of each place, incl. auctions
skips(event_id PK, char_id, action_id, at)                   -- "Mark as skipped" from the addon; applied when the action exists
vendor_reagents(key PK)                                      -- seen at a merchant (add-only)
sync_state(market PK, last_hour, last_d1, last_volume, live_since, last_run)
meta(name PK, value)                                         -- e.g. savedvars_ingested_until
mail_sent(event_id PK, char_id, to_char, key, qty, at, action_id)
status(topic PK, level, kind, data, at)                      -- Status page lines; data = JSON fields, rendered by views (§8.5)
fetch_failures(market, hour, failures, last_error, last_at,  PK(market,hour))   -- hour files to retry (§5.2)
```

- Candle and `current` `src` is `ahf` or `local`.
- Volume `src` is `ahf` (Premium depth), `books` (own order-book diffs) or `mail`, ranked in that order of trust; `qty_drop` rows hold listing turnover, never sales (§3.3.1). `conf` is `high`, `medium` or `low`.
- Scan `kind` is `full` or `partial`. Mail `outcome` is `sold`, `expired`, `cancelled` or `won`. Post `outcome` is null while pending, then `sold`, `expired` or `cancelled`.
- `location` is `bags`, `bank` or `mail`. Items on the AH live in `own_auctions`.
- Action `kind` is `buy`, `craft`, `mail`, `post`, `hold`, `de` or `vendor`. `status` is `pending`, `late`, `partial`, `done`, `missed` or `skipped`. A replacement post points to the missed one with `replaces_id` and carries the original `anchor_net` and `anchor_window_ms` (§3.4.0). `parent_id` links a sell action to the buy or craft that created the stock.
- `cost_basis` is **what you actually paid**, as an average per unit across all your characters. It is account-wide, so mailing an item to an alt keeps its cost. It is used for display (Inventory, On the AH, "below what you paid"). Planning uses opportunity cost instead (§3.4). The tracking pass rebuilds it each run (`engine/cost_basis`): confirmed purchases (AH and vendor) and crafts in time order, a craft taking its mats out at their average cost and adding its product at that cost (T10); then reconciled with what you hold now, every character and place including your auctions: units that left take their average cost with them, units held beyond what was bought or crafted count at 0 (T9). An item never bought or crafted gets no row and shows "before tracking" (T11, T12). A post or vendor sale planned below it says "below what you paid (40c)" and is still planned, because what you paid is spent (T7).
  - A purchase (AH or vendor, `purchases.source`) adds `qty × price`.
  - Items you found (looted, farmed, gifted) add their quantity at cost 0.
  - A craft adds its reagents' own cost basis, so a craft from found mats costs 0. The cost per product is stored in `crafts.cost_each`.
  - Selling, vendoring, disenchanting or using an item as a reagent removes units at the average cost. A post records the average at posting time in `posts.cost_each`.
  - Items you already held when tracking started count as cost 0, and `untracked_qty` marks them, so the UI can say "before tracking".
- `item_rules.rule` is `no_buy`, `ignore`, `keep` or `none` (§3.4.2). `source` is `dashboard` or `addon`. Clearing a rule writes `none` with a new `set_at` rather than deleting the row. That tombstone is what lets a clear beat an older rule from the other side. Tombstones older than 90 days are pruned.
- **Failure handling (§5.2):**
  - **Event ids.** Every event table (`posts`, `purchases`, `crafts`, `disenchants`, `auction_mail`, `payouts`, `mail_sent`, `skips`) is keyed by `event_id` (`charId:timeMs:seq`, unique), and ingest upserts on it. A natural key such as (character, item, time) would merge two identical buys in the same second.
  - **Snapshots.** `snapshots` holds each place's capture time, so an older file never overwrites a newer snapshot, not even an empty one.
  - **Skips** follow the 90-day retention. A skip whose action doesn't exist yet is applied when matching creates it (§3.4.1, M4).
  - **Attempts.** `purchases` and `posts` have `confirmed` (0/1); unconfirmed rows older than 1 h are ignored.
  - **Scan coverage.** `scans` has `coverage` (0–1); only `kind = full` with coverage 1 feeds volume diffs.
  - **Materialized stats.** `item_stats` (§3.1.1) is the per-item stats table that refresh writes. It has `item_stats_new` and `plan_new` as staging twins.
  - **Format version.** `meta` stores `schema_version`, and migrations run on start.
  - **Status lines.** `status` stores a topic, a level and **structured fields** (ms ranges, counts, hour numbers) as JSON, never pre-formatted text, so the dashboard can show them in local time (§3.3) through `views` (§8.5). A line is deleted when its condition clears (Premium back, the hour recovered, the endpoint working again). Log lines may carry English text; they never carry secrets.
  - **Placeholders until M3.** `item_stats` and `item_stats_new` hold `(market, key, stats JSON, updated)` until the engine defines their columns.

**Retention: 90 days**, matching AHF's longest window, for candles, volume, posts, purchases, crafts, mail and actions. `cost_basis` and `item_rules` are kept until they no longer apply. Order books (`scans`, `book_entries`, both sources) are kept **30 days**, which is what the undercut share and the price-tier sell-through need (§3.3.2). Hourly AHF books for about 9k items are the largest table; the size estimate below assumes them.

**Size estimate** for one market of about 9k items, at about 40–60 bytes per hour row:

| Scan frequency | 1 week | 90 days |
|---|---|---|
| Today (about 1 scan a week) | < 1 MB | about 10 MB |
| About 5 scans a day (likely post-launch) | about 20 MB | about 250 MB |
| Every item every hour (worst case) | about 90 MB | about 1.2 GB |
| Hourly AHF order books (Premium), kept 30 days | about 250 MB | about 1 GB at 30 days (about 5 price groups per item, about 30 bytes per row) |

Scale linearly per extra market.

### 3.3 Pricing primitives (per item, per market)

**Numeric rules** (Python and Lua must follow them exactly, or the two engines drift apart):

- Prices, costs, deposits and budgets are **integer copper**. Deposits are floored.
- Anything multiplied by a probability or a ratio (`P`, expected values, `how`-adjusted prices) is a **float in copper**. It is rounded half up, `floor(x + 0.5)`, only when shown or exported. Never use Python's `round()`, which rounds half to even.
- **Median** of an even count = floor of the mean of the two middle values.
- **Percentile** p = nearest rank: sort ascending and take the value at 1-based index `ceil(p × n)`.
- **Time** is UTC ms internally. The hour of week uses the UTC date: `weekday = (floor(ms/86400000) + 3) mod 7` with 0 = Monday (1970-01-01 was a Thursday), and `hour = floor(ms/3600000) mod 24`. Display uses the machine's local time. The ±2-hour smoothing absorbs daylight-saving shifts.

| Field | Meaning |
|---|---|
| `cur`, `curQty`, `curAge` | newest lowest buyout (AHF or local, whichever is newer), quantity, age; nil when nothing is listed |
| `curOthers` | lowest buyout that is not one of your own auctions (`own_auctions`). It uses the next price in the newest local book if you hold the lowest one; nil if unknown |
| `med7`, `med30` | median of hourly closes in the window (one per item and hour, §2.2). With fewer than 24 hourly closes, it uses the median of daily closes instead, or the few hourly closes when there are no daily ones; nil when there are neither |
| `p10_30`, `p90_30` | nearest-rank 10th and 90th percentiles of hourly closes over 30 days |
| `trend7` | least-squares slope of the daily medians over the last 7 days ÷ `med7` × 100, in % per day. It needs at least 4 days of data, otherwise it is 0 |
| `sold/day`, `sellThru` | **sales volume**; see §3.3.1 for its sources |
| `how[7×24]` | **hour-of-week profile**: median close per (weekday, hour) ÷ `med30`, smoothed as the mean of the slot and the 2 slots on each side. A slot with data from at least 3 different weeks is used with confidence high. With 1–2 weeks it falls back to the weekday-only ratio (median of that weekday ÷ `med30`), confidence low. With no data it is flat (1.0) |
| `sales_how[7×24]` | **hourly sales profile**: units sold in each hour-of-week slot ÷ the average hour. Its mean over the 168 slots is 1.0. It is measured from consecutive hourly order books (AHF `books` with Premium, or your scans), smoothed like `how`. **Hourly rate:** each pair of consecutive complete books of a market (either source) at most 2 h apart (`volume.SALES_PAIR_MAX_GAP_MS`; longer gaps blur the hour) gives `sold ÷ gap in hours` (§3.3.1 source 2), counted for the clock hour of the earlier book. An hour with several pairs takes their total sold ÷ their total gap. Rates are computed at ingest and stored per hour in `sales_h1`, so analysis never diffs books. It needs data from at least 3 weeks per slot (else the weekday-only value, else flat 1.0). It drives demand in the sell-probability simulation (§3.3.2) |
| `sell_score[7×24]` | **how good each hour of the week is for selling**: `how[slot] × sales_how[slot] ÷ max(sales_how)`, i.e. the price you get there × how busy buyers are relative to the busiest hour. A high price with no buyers scores low |
| `sell_windows` | the item's **best selling windows**, derived from `sell_score` (§3.3.4), e.g. "Tue 18:00–21:00, Wed 19:00–22:00" |
| `n`, `conf` | `n` = hourly closes in the last 30 days. `conf` = a **confidence percentage** (0–100%) for each result, defined in §3.3.5. Its level (high ≥ 75%, medium ≥ 45%, low below that) is only for the dot colour; rankings use the percentage itself |

#### 3.3.1 Sales volume

The free API gives only the **quantity listed**, not sales. Sales volume is the main input for the probability that something sells, so it comes from the best source available. Each value carries its source.

1. **AHF Premium `depth`** (best): daily `listed / sold / expired`, worked out by comparing consecutive complete order books.
2. **Own order-book diffs** (same method, our data): the addon stores the full order book from each full scan. It records each auction's item, unit price, stack size and time-left band; `ReplicateItems` gives this. For two scans at most 6 hours apart, per `(item, price, stack)` group:
   - Time left is **not** part of the group. Otherwise an auction whose band ticks from long to medium would count as gone plus new.
   - `prev` and `next` = auction counts in the two books; `g` = time between them.
   - Each time-left band covers `[lo, hi)`: short 0–30 min, medium 30 min–2 h, long 2–8 h, very long 8 h to the longest duration. These are the Classic bands and must be verified for Forever (§6). An auction is assumed to sit anywhere in its band with equal chance. So the expected number of the old book's auctions that ran out during the gap is `expiring = Σ count_band × clamp((g − lo) / (hi − lo), 0, 1)`.
   - `gone = max(0, prev − next)` and `new = max(0, next − prev)`
   - `expired = min(gone, expiring)` and `sold = gone − expired`. Both can be fractional.
   - Units = counts × stack. Item totals sum their groups.
   - Known errors: a cancelled auction counts as sold, and a repost at a new price counts as sold plus new.
   - Scanning at the start and end of a play session gives one sample.
3. **Your own sales**: "auction successful" and "auction expired" mail is real ground truth for your own items. It also calibrates source 2: `factor = mail-confirmed sold ÷ book-estimated sold` for your auctions of that item, stored in `calibration` and multiplied into book-based `sold/day`.
4. **Listing turnover, not sales.** `max(0, qty_prev − qty_next)` between consecutive AHF quantity observations a few hours apart counts units that left the AH: sold, expired or cancelled, which totals alone can't tell apart. It is stored as `observed_listing_turnover` (§3.3.6) and shown for information, but it is **never used as sales**. An item whose only data is quantities has sales volume **unavailable**, and its chance uses `default_sell_prob`, marked "volume unknown" (§3.3.2).

Order-book diffs (source 2) are an explicit estimate, not confirmed sales: without auction ids a cancellation can't be told from a sale, so they are labelled "est.", carry a lower confidence (§3.3.5), and are corrected by AHF and mail calibration (below).

**Choosing the source, per item and per day:**
- **All available sources are always computed and stored** in `volume_d1`, one row per source. The estimates (2–3) keep running even while Premium is active, so there is no gap when it lapses.
- `sold/day` uses, for each day, the best source that has a row for that day, in the order 1 → 3. A 30-day `sold/day` can therefore mix days of real AHF volume with days of estimates, for example when Premium lapsed halfway through the month.
- AHF volume already stored is history and stays valid after Premium lapses.
- **AHF volume calibrates the estimates.** On days where both AHF volume and an order-book estimate exist for an item, `factor = AHF sold ÷ estimated sold` is stored in `calibration`, the same table the mail calibration uses. It is applied to that item's estimates on days without AHF data. The more weeks of Premium you have had, the better the estimates are afterwards. With no overlap, the factor is 1. When an item has both an AHF factor and a mail factor, only the AHF one is applied: it covers the whole market, not just your own auctions. The two are never multiplied together.
- Every `sold/day` shows its source mix on the item detail and in the Status page, e.g. "sold/day 38 · 22 days AHF, 8 days estimated".

#### 3.3.2 Sell probability and expected value

`P` = the expected fraction of your `q` units that sell **within the post's sell window**. It depends on *when* you post, because demand changes through the day and the week.

**The sell window** is always made of whole clock hours (the current hour counts in full):

| Context | Window |
|---|---|
| Posting now: Today, Disenchant (selling the mats), Craft (craft now and sell), a sell value (§3.4) | the next `sell_window_hours` (8) from now |
| A planned post in the Week plan, including the sale half of a flip or a craft-to-sell | from the post time (`post_lead_hours` = 8 before the best hour of its target selling window, §3.3.4) through the end of that window. If the post time has already passed, it starts now. If the window has ended, the peak was missed (§3.4.0) |

- The auction duration is the shortest one that covers the window.
- Sales after the window are not counted, which is conservative: the auction may still sell later.

`P` combines two views:
- a **queue simulation** of what happens to your auction given today's competition;
- the **historical sell-through** of auctions priced like yours.

Both use the same formulas with or without Premium. Premium only supplies better inputs.

**Inputs** (per item and market):

| Input | Meaning | With Premium | Without |
|---|---|---|---|
| `S` | units sold per day (7-day average) | AHF `volume` | estimates (§3.3.1) |
| `X` | units expired per day | AHF `volume` | estimates |
| `L` | units newly listed per day | AHF `volume` | estimates |
| book | every competing auction: unit price, units, time-left band | AHF `books` (latest) | your newest scan, if under 24 h old |
| `u(p)` | **undercut share**: of the units newly listed over the last 7 days, the share priced at or below your price relative to the lowest price at the time they were listed | AHF book history | your scans' history, else `default_undercut_share` (0.5) |
| `ST_tier` | **sell-through by price tier**: of the auctions that disappeared in the last 30 days, the share that sold rather than expired, grouped by how their price compared with the cheapest competitor | AHF book history | your scans' history, else none |

**Market facts shown with every result** (informational):
- **Market sell-through** = `S ÷ (S + X)`, e.g. "72% of auctions sell".
- **Supply on the AH** = the total units listed now, `N`.
- **Days of supply** = `N ÷ S`, e.g. "3.1 days of supply".

**1. Queue simulation** (`sellprob.simulate`). It runs over the window's clock hours `k = 0 … W−1` (slot `t_k`) and is deterministic:
- **Demand.** In hour `k`, buyers take `S ÷ 24 × sales_how[t_k]` units from the cheapest listings available, oldest-cheapest first. At an equal price the book's order stands in for "oldest"; the order never depends on the time-left band, so a competitor expiring sooner can't reorder the queue (PI12). `sales_how` is the hourly sales profile (§3.3), so a window that covers the evening rush sees more buyers than one overnight.
- **Who is ahead of you.** Competing auctions priced at or below `p` are ahead. At an equal price they are also ahead of you, which is the conservative choice.
- **Competitors expire.** An auction with band midpoint `m` is available during hour `k` only while `k < m`. The midpoints are: short 0.25 h, medium 1.25 h, long 5 h, very long `(8 + longest duration) ÷ 2`. When an auction expires, its unsold units leave the queue and stop absorbing demand. The band bounds are the same as §3.3.1 and still need verifying (§6).
- **New undercutters.** At the start of each hour, `L × u(p) ÷ 24` new units appear priced below you. They are the cheapest listings from then on and stay up for the rest of the window.
- **Your sales.** Demand left over after everything ahead of you goes to you, until your `q` units are gone.
- **Result.** `P_queue = your units sold ÷ q`.

**2. Price-tier sell-through.** Your tier is `r = p ÷ curOthers`:

| Tier | Your price vs. the cheapest competitor |
|---|---|
| T1 | `r ≤ 1.00` (you are the cheapest) |
| T2 | `r ≤ 1.05` |
| T3 | `r ≤ 1.15` |
| T4 | above that |

`ST_tier = sold ÷ (sold + expired)` for that item's auctions in the tier, using the order-book diff method of §3.3.1. `n_tier` is how many auctions it is based on.

**3. Blend.**
- `e = 0.5 × min(1, n_tier ÷ 30)`, with `e = 0` when there is no tier history.
- `P = (1 − e) × P_queue + e × ST_tier`.
- The history never counts for more than half. The simulation sees today's competition, and the history catches what the simulation misses, such as buyers who skip an item at any price.

**4. Your own record** is applied after this: `P'` (§3.3.3).

**Fallbacks:**
- **No volume data at all** (`S` unknown): `P = default_sell_prob` (0.25), marked "volume unknown".
- **No usable book:** there are no band or price details. The simulation uses a single block of `curQty` units ahead of you, but only when `p ≥ cur`, and that block stays available for the whole window.
- **Confidence:** every `P` carries a `conf` percentage (§3.3.5), which drops as inputs weaken: estimated instead of real volume, an old or missing book, a default undercut share, a short pattern.

**Why the old one-line formula was not enough:** `clamp((S·W/24 − ahead) ÷ q)` assumed three things: demand is the same every hour, every competing listing absorbs buyers for the whole window, and no one undercuts you. The simulation is identical to it with a flat sales profile, nothing ahead expiring, and `u = 0`. That equivalence is tested (PI10), so the old formula is a special case, not a different answer.

**Explaining the number.** Every `P'` can be expanded on the dashboard (item detail, and hovering a Chance cell) into its parts, for example: "market sells 72% · 3.1 days of supply · 6 units ahead of you (all expire in 5 h) · ~6 undercuts expected · queue 95% · tier history 80% (30 auctions) → 88% · your record → 85%".

**Expected net of posting** `q` units at unit price `p`:

`E_post = P·q·p·(1 − cut) + (1 − P)·(q·fallback − deposit)`

- The deposit is **refunded when the auction sells** and lost only when it expires.
- Unsold units come back to you, worth `fallback` = max(vendor sell price, DE value per unit if one of your characters can disenchant it). Relisting is not modelled, to avoid recursion.

**Anything you pay for now** (a buy or a craft): `expected profit = E_post − cost`. So an unsold flip shows as a loss, not just as a smaller profit.

Items that sell only a few units a week get a low `P`, so they rank low however big their margin.

#### 3.3.3 Your own sell record

The market model can be wrong about an item, and your own posts are the best evidence of that. For each item and market, over the last `own_record_days` (30) days, across all your characters:

- `s` = your posts that sold, `x` = your posts that expired. Cancelled posts don't count. A post is one auction, so its outcome is either sold or expired.
- Each expiry gets a **cause**, worked out from the observations during the post's lifetime (local books and AHF `cur`):
  - `undercut`: something cheaper than your price was listed at some point, so price was the problem.
  - `lowest`: you were the cheapest in every observation, so demand was the problem.
  - `unknown`: there were no observations; it is treated as `lowest`.
- `streak` = `lowest` or `unknown` expiries in a row since your last sale of that item. Undercut expiries don't add to it.
- If you chose **Keep** for the item (§3.4.2), only posts after that choice count. The record starts fresh.

It changes recommendations in four ways:

1. **Probability.** `P` is blended with your record, with the model counting as `k = own_record_weight` (4) posts of evidence:
   `P' = (k·P + s) / (k + s + x)`.
   With no record, `P' = P`. `P'` replaces `P` everywhere in §3.3.2 and §3.4.
2. **Quantity.** The quantity planned for posting is multiplied by `0.5^streak`, rounded down to whole stacks, with a minimum of 1 stack. A sale resets `streak` to 0.
3. **Price.**
   - After a `lowest` or `unknown` expiry, the planned price is multiplied by `(1 − reprice_step)^streak` (5% per step) and floored to whole copper.
   - It never goes below `ceil(fallback / (1 − cut))`. Below that price, vendoring or disenchanting pays more, so the plan picks one of those instead.
   - After an `undercut` expiry, the next post's price is capped at `curOthers − 1` when you post, even if the plan's peak-slot price is higher.
4. **Priority.** When `streak ≥ struggle_streak` (3), the item is flagged **not selling**. After a Keep, the threshold is `keep_retry_streak` (1) instead, so a single further expiry while cheapest brings the question back.
   - It is left out of buy-to-flip and craft-to-sell, so no new stock is bought or made.
   - Stock you already own stays in the sell plan, where vendor or disenchant often wins once `P'` is low.
   - The dashboard and the addon offer **Blacklist** or **Keep** (§3.4.2).

**Built** (`engine/sell_record`, `recommend.plan.sell_records`): the records are computed from your resolved posts (§3.4.1 post outcomes) and the item's hourly closes at every full refresh and re-plan, and the plan applies all four effects. The undercut cap uses the item's current cheapest listing (yours has expired). SPEC GAP: the missed-peak re-plan of the hourly status pass doesn't use the record yet. The dashboard shows the record line on every Today and Week row of the item (D42; P before the blend is worked back from P') and a Needs a decision section on Today with Blacklist and Keep (D43), which `POST /api/rule` stores (SEC8). `PortfolioPlan.lua` carries each record line and not-selling flag as `records`, outside the checksum (EX8). The addon shows each record line under the item's Today and Week rows and lists flagged items in a Needs a decision section below Today's rows (A52, UI6, UI10); a right-click offers "Don't buy or craft it", "Ignore it completely" and "Keep selling it (start fresh)", saved as rules in `AHProfitDB.rules` (A53).

It is always visible. Every action row for an item with a record shows (the tooltip only reflects it in Chance to sell), for example, "Your record (30 d): 2 of 5 sold · expired 2× while cheapest · P 80% → 58% · price −10% · qty ¼". So you can see when and why your own history changed a recommendation. Old failures stop counting after 30 days, so an item can recover.

#### 3.3.4 Weekly patterns and best selling windows

The week's 168 hours are treated as **repeating slots**: every Tuesday 19:00 is the same slot. Each week of history adds one more observation to each slot. So the patterns sharpen over time and become a forecast for the coming week.

- **Recency weighting.** `how` and `sales_how` use a weighted median per slot. Each week's observation has weight `0.5 ^ (age_in_weeks ÷ pattern_half_life_weeks)` (4), so a pattern from last month counts half as much as last week's. A shift in when people buy, say after a patch, shows up within a few weeks.
  - Weighted median, deterministic: sort the values, then take the first value where the cumulative weight reaches half the total.
- **Best selling windows** (`patterns.sell_windows`):
  1. Take the slots whose `sell_score` is at least `window_threshold` (90%) of the item's best slot.
  2. Join adjacent qualifying slots into windows; a window can cross midnight.
  3. Rank the windows by the sum of their scores, and keep the top 3.
  4. Each window carries its average price ratio (`how`) and the number of weeks of data behind it.
- A week whose every slot qualifies (a flat score) has no best window: posting time doesn't matter, so the plan posts now.
- **Pattern strength**, from the weeks of data in the window's slots (the fewest weeks of any slot in the window): under 3 weeks is "early pattern", 3–5 is "pattern", 6 or more is "established". It feeds the window's confidence. With no pattern, the item has no windows and the plan uses posting now.
- **Where windows are used.** The Week plan's peak slot is the best slot inside the top window, and the post is placed 8 h before it (§3.4C). Windows are also shown on the tooltip ("Best time to sell"), on the item detail (heat map plus windows) and in Inventory.
- **Forecast.** The expected price in a window next week = `level × how × (1 + trend7/100 × days ahead)` (§3.4C), where the **price level** is `med7`, or `med30` when there is no `med7`. A month-old level misleads when a market has moved: old listings that sat unsold keep `med30` high while the item now sells far lower (R21). The chance to sell there is the simulation over that window (§3.3.2), using `sales_how` for demand. Both get more confident as the pattern strengthens.

#### 3.3.6 Market activity metrics

Numbers, not labels: the engine never classifies an item as "high" or "low" liquidity. For every priced item and market, the full refresh computes the fields below and stores them in `item_stats`. Each states its unit and window. A value the data can't support is **unavailable** (None, shown "—"), never a guess and never 0.

| Field | Meaning (unit, window) | Unavailable when |
|---|---|---|
| `estimated_sales_per_day` | `sold/day` (§3.3.1): units per day, averaged over the days of the last 7 that have a sales source | no day has AHF, book or mail volume |
| `estimated_sales_per_hour_of_week` | 168 values: `estimated_sales_per_day ÷ 24 × sales_how[slot]` (units per hour) | sales per day unavailable |
| `current_listed_supply` | units on the AH in the newest valid observation (local scan or AHF) | never seen |
| `median_daily_supply` | median of each day's listed units over the last 30 days (a day's value: the median of its observations; days without one are left out, V23). The liquidity cap uses it when the current supply is unknown (`intel.contract.supply_level`) | no observation in 30 days |
| `supply_to_volume_ratio` | `current_listed_supply ÷ estimated_sales_per_day`, in days of supply (§3.3.2 market facts). Not a time-to-sell guarantee | either input unavailable, or sales per day is 0 |
| `volume_confidence` | 0–1: the confidence component for volume (§3.3.5 "Volume source") | — (0 without a source) |
| `volume_observation_coverage` | 0–1: share of the last 7 days that have a sales source | — |
| `observed_listing_turnover` | units per day that left the AH between consecutive AHF quantity observations (§3.3.1 item 4). Informational only: never used as sales | fewer than two observations a few hours apart |
| `estimated_time_to_sell(q)` | optional: hours until the queue simulation (§3.3.2) has sold q units, posting now | sales per day unavailable, or q not sold within 48 h |

**Weekly distribution.** Each of the 168 hour-of-week slots (§3.3.4) carries, over the rolling 90 days and recency-weighted like `how`:

| Per slot | Meaning | Unavailable when |
|---|---|---|
| `how` | median normalized price (§3.3) | (flat 1.0, as §3.3) |
| `supply_how` | median listed units in that slot ÷ the overall median listed units | fewer than 3 weeks of observations (weekday fallback, as `how`) |
| `sales_how` | estimated sales relative to the average hour (§3.3) | (flat 1.0, as §3.3) |
| `price_spread` | (nearest-rank p90 − p10) ÷ median of the slot's closes | fewer than 3 closes |
| `weeks` | weeks of data behind the slot (its observation coverage) | — |

Missing observation periods never count as zero sales or zero supply; they are left out of medians and averages.

**Seasonality strength** (0–100): how *predictable* the weekly price pattern is, kept separate from how *profitable* it is.
- **Out-of-sample test.** Build `how` from all weeks but the most recent one, then predict that held-out week's hourly closes two ways: with the weekly profile (`med30 × how[slot]`) and with a flat price (`med30`).
- `strength = floor(100 × max(0, 1 − error_profile ÷ error_flat) × min(1, weeks ÷ 6) + 0.5)`, where error is the mean absolute error over the held-out hours with a close.
- **Insufficient evidence** (strength None, shown "—" with "insufficient evidence"): fewer than 3 weeks of data, fewer than 24 held-out closes, or a flat error of 0.
- Consistent patterns score high even when the premium is small; a big premium that doesn't repeat scores low.

**Buying windows.** The mirror of selling windows (§3.3.4): the slots whose price ratio is at most `min(how) ÷ window_threshold` (within about 11% of the cheapest slot), joined into windows that may cross midnight, ranked by lowest average `how`, top 3. Flips buy in them (§3.4C.2). With no pattern there are none, and a buy is planned for now.

**Price volatility and long windows.** `volatility_30` = standard deviation ÷ mean of the daily medians over 30 days; 7-, 30- and 90-day medians and nearest-rank percentiles. A close outside 10 × p90 or below 10% of p10 is a suspect price (§5.2), excluded from all statistics until a second observation confirms it.

**Three selling answers, kept apart** (item detail and Inventory): the hour with the **highest expected price** (max `how`), the hour with the **highest estimated activity** (max `sales_how`), and the **best risk-adjusted** window (max `sell_score`, §3.3.4), which is the one the plan uses.

**Method version.** `VOLUME_METHOD_VERSION` (an integer in `engine/volume.py`) is stored with every `volume_d1` row and every `item_stats` row. When it changes, the next full refresh recomputes `books` volume from the stored order books (kept 30 days, §3.2) and marks older rows as from an earlier method on the Status page; AHF and mail rows are kept as they are. **Built** (V24): `volume_d1.method` holds the version of an estimated row (NULL for confirmed sources and rows from before versions were stored, which count as version 1); `collect.local_scans.recompute_stale_volume` runs at the start of each market's full refresh.

#### 3.3.5 Confidence percentage

`conf` says how much the data behind a result can be trusted. It is the weighted mean of the components that apply to the result:

| Component | Score (0–1) | Weight |
|---|---|---|
| Price history | `min(1, n ÷ 60)`, hourly closes in the last 30 days | 0.25 |
| Volume source | the share of the 7 days by source: AHF real 1.0 · order-book estimate 0.7 · mail only 0.6 · listing turnover only 0 (not sales) · none 0 | 0.30 |
| Order book freshness | ≤ 2 h old 1.0 · ≤ 24 h old 0.5 · older or none 0.3 | 0.15 |
| Weekly pattern | `min(1, weeks of data ÷ 6)` for the window used. **Only for planned posts**; posting now leaves it out | 0.15 |
| Tier history | `min(1, n_tier ÷ 30)` | 0.15 |

- `conf = Σ (score × weight) ÷ Σ (weights of the components that apply)`, shown as a whole percent (rounded half up).
- Calibration (§8.8) also reports predicted versus actual sell rate by confidence band, so a too-optimistic confidence shows up.

### 3.4 Recommendation engine

**Market capacity is shared and counted once** (§3.3.6). Every quantity a recommendation proposes stays within what the market can absorb, across all your characters together:
- **Flips:** units bought ≤ the liquidity cap of the windows they'll sell in, minus units you already hold or have planned for that item.
- **Crafts:** the craft count already stops when an extra craft stops paying (the product's chance falls as more is posted); the planned output also counts against the product's cap.
- **Disenchant:** the **mats** from all disenchant buys together stay within each mat's cap for its sell windows; buys are taken best profit first until a mat's cap is reached.
- **Portfolio (§3.4C.4):** the allocation spends one shared capacity per item and window across characters, so two characters never plan to fill the same demand, and no more than `max_item_capital_share` (config, 0.25) of total gold goes into items whose supply/volume ratio is above `slow_market_days` (config, 7).
- Volume estimates are uncertain inputs: caps use the estimate as given, and a low `volume_confidence` lowers the result's confidence (§3.3.5), never inflates it.

All money is net of fees. Estimates come with a confidence level.

- **Fees** (`engine/fees`): the AH cut is 5% at the faction AH. The deposit is `floor(vendor_sell × q × rate(duration))`, and it is refunded on sale (§3.3.2). The durations are 2, 8 and 24 h, as in Classic Era (verified on Forever 2026-10-18). The deposit rates are seeded in `fees.py` with Classic Era values and must still be verified in game (§6). The neutral AH (15% cut) is not planned in v1.
- **Post price** `p` = `min(curOthers − 1, med7)` in copper. If one of them is nil, it uses the other. If both are nil, the item has no price: it is skipped and flagged "no price". `curOthers` leaves out your own auctions, so you never undercut yourself.
- **Sell value** per unit, for things you don't hold yet (DE results, a craft's mats you'd buy) = `E_post(q, p) / q`, using the posting-now window (the next 8 hours).
  - `q` is the item's **reference stack**: the most common stack size in the newest book, with ties going to the smaller one. It is 1 when there is no book.
  - It already includes `P`, so mats that don't sell are worth less.
- **Hold value** per unit, for things you already hold = the sell plan's expected value for all the units you hold (§3.4C.1, best option per lot) ÷ units held.
  - Posting now is one of the options the sell plan compares, so the hold value is never below the sell value.
  - It is higher when waiting for a peak pays more. That is the real opportunity cost of using the item.
  - The sell plan is computed first, treating all owned stock as for sale. Crafts then price owned mats at their hold value, and the allocation (§3.4C.4) decides which units are crafted and which are sold.
- **Reagent cost** for `q` units = the cheaper of vendor buy × `q` and buying from the AH. AH cost walks the newest local book from the cheapest listing up. Without a book, it is `cur × q`, flagged "depth unknown" when `q > curQty`. Mats you already own are priced at their **hold value**, because that's the opportunity cost. Otherwise a craft would look profitable even when selling the mats raw, now or at their peak, pays more.
- **Liquidity.** Your units planned for one sell window are capped at `max(1 stack, floor(max_supply_share × others' listed units in that window))`, with `max_supply_share` config (0.5): you are at most about a third of the window's listings, so you never set the price or invite undercuts. Others' listed units in the window = current supply × the mean `supply_how` over the window's hours ÷ `supply_how` at the current hour (§3.3.6), so a window where the AH is usually fuller allows more (R17). With no supply pattern it is the current supply; with supply unknown the cap is 1 stack. **Gear** (weapons and armor, item class 2 and 4) is more lenient, since each piece is its own listing and an empty market is exactly when to list: its cap is `max(min_gear_units, floor(max_supply_share_gear × others' listed units))`, with config `max_supply_share_gear` 1.0 (you may double the market) and `min_gear_units` 2 (one or two pieces always go up, R18). There is no separate sales limit: the queue simulation already lowers `P` as you post more than buyers take. A result that needs more units is capped, not hidden; its `P` already reflects what's left.
- **Money on every action** is shown as both **each** (per unit) and **total** (each × quantity):

  | Action | Each | Total |
  |---|---|---|
  | buy, DE (buy to disenchant) | AH price per unit | what you pay |
  | post | listing price per unit | listing value |
  | craft | **gold to buy the mats you're missing**, per craft, at the latest AH prices (or vendor, if cheaper) | × number of crafts |
  | vendor | vendor price per unit | what the vendor pays |

  A craft shows "—" only when you already own every mat it needs. When owned mats are used, the row adds a note, e.g. "uses 6 owned Copper Bar (hold value 57 s)". Profit still counts their hold value, as above.
- **Expected net** on an action is what doing it is expected to gain. **A chain's profit is counted once**, so day, week, category and character totals never count the same gold twice (VW1). A chain is a buy or craft, its linked follow-ups (`parent_id`), and any mat buys, intermediate crafts and mail steps that feed it.

  | Action | Expected net |
  |---|---|
  | buy to flip | `E_post` of its planned sale − buy cost |
  | DE buy | DE EV − buy cost |
  | craft (to sell or to DE) | `E_post` of the product (or its DE EV) − sourced mat cost, with owned mats at hold value |
  | mat buy, intermediate craft, mail | none ("for Bolt of Silk Cloth 20:30"); its cost is part of the final craft |
  | post, DE or vendor of stock you hold | `E_post` (or DE EV, or vendor price × qty) − what you paid (`cost_basis`; 0 for found items) |
  | hold | none |

  - **Which action carries it:** the chain's earliest action that the view shows, whether done or pending. The follow-up then shows "profit counted on the Fri 14:10 buy" and adds 0 to the totals.
  - **When the earliest action leaves the view** (the rolling week has moved past its day), the follow-up becomes a sale of stock you hold and carries its own net, as in the last table row.
- **Risk %** = gold the action spends ÷ the total gold across all your characters (sum of `characters.gold`) × 100, shown to one decimal place.
  - The gold spent is the total for a buy or DE buy, the mat gold for a craft, and the deposit for a post (the only gold a post can lose). It is 0 for a vendor sale.
  - Rows above `high_risk_pct` (25%) show their risk in amber.
  - **No buy risks more than `max_risk_pct` (config, 25%) of all your gold.** A flip, mat buy or buy-to-disenchant takes the cheapest auctions first and stops before the next one would cross it, so one stack is the default size but a partial stack is fine (R19). Posting stock you already own risks only its deposit.

**A. Disenchant.** For each disenchantable item on the AH (AHF snapshot or own scans), where at least one of your characters has the Enchanting skill to do it:

`EV = Σ P(mat)·E[qty]·sellValue(mat)` and `profit = EV − buy`. Here `P(mat)` is the drop chance from the DE table.

**B. Craft now.** For each recipe in `known_recipes` for any of your characters:

`profit = E_post(product, q = avgYield) − Σ reagentCost`

**Mat sourcing.** Every known recipe is evaluated, not only the ones your bags already cover. Each reagent unit comes from its cheapest source:
- **owned** (bags, bank or mail on any character), valued at its hold value;
- **AH**, walking the newest book from the cheapest listing up;
- **vendor**, for vendor-sold reagents;
- **crafted by you**: when one of your characters knows a recipe that makes this reagent. Its cost is that recipe's own sourced mat cost, worked out with these same four sources.

Owned mats are used first when they cost the same. If the AH price is below an owned mat's hold value (for example, it is worth more at its Saturday peak than the AH asks today), the plan buys from the AH and leaves your stock to the sell plan.

The result is shown as the row's **Source**. The base tells where the raw mats come from, and **+ crafted** is added when any reagent is made by you along the way:

| Source | Meaning | Example |
|---|---|---|
| Owned | every mat comes from your stock; no gold needed | Copper Rod from 6 Copper Bar in your bags |
| AH | every mat is bought (AH or vendor); you craft entirely from the market | Bolt of Silk Cloth from Silk Cloth bought on the AH |
| Mixed | your stock plus a top-up from the AH or vendor | Linen Belt: your Linen + bought Coarse Thread |
| … + crafted | one or more reagents are made by your characters first | Runed Copper Rod: Brugan crafts the Copper Rod from AH Copper Bar, mails it to Amaya, Amaya adds bought Strange Dust |

**Crafting chains** (the "crafted by you" source):
- **Profit is counted once.** An intermediate is valued at the cost of its own mats, so its profit lands on the final product. Leftovers from a random yield go to the sell plan.
- **Limits.** Chains are followed up to `max_craft_depth` (3) steps, and a recipe never feeds itself, so transmute loops are ignored. Every step checks that character's skill and cooldowns.
- **Which character.** Each step is made by a character who knows it. When the next step's crafter is someone else, the plan adds a **mail** action ("mail 1 Copper Rod Brugan → Amaya"). Mail between your own characters must be verified on Forever (§6).
- **The row note shows the chain**, e.g. "Copper Rod: crafted by Brugan from 2 Copper Bar (AH) → mailed to Amaya".

- **How many crafts.** Crafts are added one at a time. Each extra craft must still make a profit: AH mats get pricier as the book is walked, and the product's chance to sell drops as more is posted. Crafts stop at the first one that would lose money, or at the budget, the AH depth, or the cooldown.
- **Missing mats.** A recipe with a mat that has no AH listing, no vendor and no stock is not craftable now. It is listed only if it is one craft short, as "missing: 2 Iron Bar".
- This includes craft → disenchant.
- It shows how many crafts the mats you already own can cover, and which character crafts each one (only characters who know the recipe).
- Recipes with a cooldown (transmutes, Mooncloth) are limited to one craft per cooldown per character.

**Today list.** The actions to do now are sorted by **expected net, highest first**. Ties go to the higher `P'`, then the item name A–Z, then the character name A–Z, so the order is always the same (§8.6). Rows with `P'` below `min_sell_prob` (config 0.5) are hidden, and the footer says how many. You can change the threshold from a dropdown on the dashboard and in the addon. Items flagged "not selling" go in a separate **Needs a decision** section under the list.

**C. Weekly plan** (a rolling 7 days, hour resolution). It always starts at the current hour today and covers the next 7 × 24 hours, so the first column is always today. Earlier hours of today show what was done or missed.

1. **Sell plan for what you own** (all characters: bags, bank and mail; bound items are left out, and items already on the AH are shown as "listed" rather than planned again). For each item, it compares `E_post` for:
   - post now at `p`
   - post for the best slot in the next 7 days, at price `level × how[slot] × (1 + trend7/100 × days ahead)`, the level being `med7`, else `med30` (R21). `trend7` is clamped to ±5% per day. The auction duration is chosen so the listing covers the peak.
   - **Every post action is scheduled `post_lead_hours` (8) before the best hour of its target selling window**, not at it. That is the same 8 hours used for posting now. Its chance to sell is computed from the post through the end of the window (§3.3.2), and a window that ends with no post is a missed peak (§3.4.0). The row says which peak it is for, e.g. "Sat 10:00 · post · for the 18:00 peak". If that time has already passed, the post is planned for now.
   - disenchant (if a character can)
   - vendor
   - hold beyond 7 days (only flagged, never planned)

   It picks the option with the highest expected value. Large stacks are split across slots so that what you post for each window stays within the liquidity cap (§3.4).
   - **How it allocates** (`engine/plan.py`): each character's lot, in character order, is split into reference stacks; a character's last remainder may be smaller (PL2). Each stack goes to the post option with the highest expected value that still has room in its window. When no post this week pays more per unit than disenchanting or vendoring (no window has room left, or one more stack would mostly not sell), it is a **hold** while the AH price after the cut still pays more than either, so stock waits for later weeks instead of being dumped; otherwise it is disenchanted or vendored.
   - **A post's chance** is the queue simulation over its hours (§3.3.2), with `sales_how` as demand, today's book as the competition, and your own units already planned for the same window ahead of you. Your sell record is added once it is tracked (M6b, §3.3.3).
   - **The duration** is the shortest one that keeps the listing up through the window's best hour.
2. **Buy to flip**: items whose expected profit (`E_post` at an upcoming peak slot − buy cost) is at least `min_flip_margin` (20%) × buy cost. Typical sources are a weekday or hour pattern, or a dip below `p10_30`. They are ranked by expected profit × `conf` (as a fraction).
   - **How a flip is built** (`engine/flips.py`): it buys other sellers' auctions now, cheapest first, and posts each one for the selling window (not "now") where it is worth most, with the auctions it bought taken out of the competition. An auction is added only while it fits the spend limit, a window with room, and its own expected profit is at least `min_flip_margin` × its cost; prices only rise along the book, so the first one that fails ends the flip. An item without a selling window gets no flip.
   - **Who buys**: each item gets at most one flip, bought by the character with the most budget left (ties: character id), so two characters never fill the same demand (R16). The spend limit is that character's budget and `max_risk_pct` of all your gold (R9, R19). The flips' posts share the windows' capacity with your own stock (PL4).
   - The buy carries the chain's expected profit; each post also records its own net against what its units cost, for when the buy has left the view (§3.4 expected net).
3. **Craft to sell later**: buy mats on cheap slots, craft, and post at the product's peak slot. It uses all three sourcing paths (B).
   - **Mats to buy appear as their own buy actions, scheduled before the craft** and linked to it by `parent_id`, e.g. "20:15 buy 8 Silk Cloth → for Bolt of Silk Cloth". A mat buy can be on an earlier, cheaper slot.
   - **A crafting chain is a linked sequence**: buy raw mats → craft the intermediate → mail it (if the next crafter is another character) → craft the product → post.
   - If any step is missed or only partly done, the steps after it are re-planned for what you actually hold. An intermediate you already made counts as owned stock.
   - **Every planned craft has a follow-up for its product within 7 days after the craft**, linked by `parent_id`. It is a post, or a disenchant for craft → DE.
   - The follow-up also has to fall inside the plan window. A craft whose product has no worthwhile slot in time is not planned.
   - This applies to crafts listed for right now (B) as well. When the craft is done, the follow-up stays planned (§3.4.1).
   - **How chains are built** (`engine/craft_plan.py`): each recipe's output is valued at the product's best selling window in the 7 days (or posting now) that still has room in the shared capacity; output no window has room for is worth nothing, so the craft count stops where capacity ends. Chains are taken best expected profit × confidence first. A chain's mat gold comes from its crafter's budget and stays within `max_risk_pct` (§3.4 Risk); the mats it uses (owned or bought) are gone for later chains and for flips.
   - **Steps:** each intermediate's crafter buys that intermediate's mats (none when it is made from your own stock), the final crafter buys the rest, then the intermediate crafts and mail, then the craft, a minute apart from now; the post follows at the product's window. The craft carries the chain's expected profit; mat buys, intermediate crafts and mail carry none (D22).
4. **Capital and time limits**: a budget per character (default: the character's gold, overridable in config `[budgets]`) and an optional maximum number of actions per day (`max_actions_per_day`, 0 = no limit). The allocation also spends each owned unit only once, either on a craft or in the sell plan (§3.4 hold value).

Output is a calendar of **actions**: `when · character · action (buy/craft/post/DE/vendor) · item · qty · each · total · expected net · P' · risk % · confidence`. Totals are shown per day, per category and per character.

**Building and storing the plan** (`engine/week.py`, `recommend/plan.py`). Each full refresh plans each market's characters together, in this order, with one capacity ledger and one set of budgets: a first sell plan of everything you own gives each item's hold value; crafts use owned mats at that value; the sell plan covers the stock that is left; flips buy from the auctions the crafts left. Facts are built only for items the plan can use: your stock, your recipes' products and mats, and **flip candidates**, items whose best peak hour (med30 × its price ratio × the most a week of trend can add, after the cut) could beat the cheapest auction by `min_flip_margin`. Rule `ignore` also takes the item out of every craft as a mat. The plan is staged in `plan_new` and published with the intelligence and pages in one transaction; it replaces the market's characters' `pending` and `late` actions, while `done`, `partial`, `missed` and `skipped` ones stay as history (§3.4.1). A chain's later steps name its first action as their parent.

#### 3.4.0 Missed peaks: move, or hold for next week

An item usually has several peaks a week, for example raid nights on Tue, Wed and Thu. These are its best selling windows (§3.3.4). A planned post targets one of them, its **original peak**. Its expected net there, `E_orig`, is the **anchor**.

- **Late but not missed.** The post time (8 h before) has passed, but the window hasn't ended. The plan says "post now (late)", and the chance is computed over the rest of the window.
- **Missed.** The window ended with no matching post. The action becomes `missed` and is labelled with what was lost, e.g. "Missed Tue 20:00–23:00 (+12%)". Then the plan looks at the item's next windows **in the same plan week**, in time order, and takes the first one that passes all three checks:
  1. **Close to the original:** `E_candidate ≥ (1 − peak_tolerance) × E_orig` (`peak_tolerance` = 10%). It is always compared with the **original** anchor, never with an earlier replacement, so it can't slide down one window at a time.
  2. **Likely to sell:** `P'_candidate ≥ min_sell_prob`.
  3. **Still worth it:** `E_candidate` is above the item's best alternative (vendor, disenchant, or holding).
- **The result is shown either way:**
  - If a window passes, the post moves there and is labelled "Missed Tue peak → Wed 20:00–23:00 instead (−5% vs original, 88% chance)".
  - If none passes, the action becomes **hold**: "Missed Tue and Wed; Thu is 18% below → hold until next week's Tue 20:00 peak". No post is planned for the rest of this week.
- **A new week is a new slate.** The plan week runs from `week_start` (Mon 00:00 local). When a new week starts, held and missed items are planned from scratch: the anchor becomes that week's best window and earlier misses are forgotten. History keeps them.
- **Mechanics** (`engine/missed.py`, `recommend/status_pass.py`): the hourly status pass reads only stored actions, the published intelligence and character state, never history (IU10). A missed post gets one replacement action, a pending post or a hold, with `replaces_id` set to it and the same `anchor_net`, so a later miss is still compared with the first peak (M3). A post planned for "now" has no stored window: it is missed 24 h after its post time and not re-planned; the next full refresh plans its stock again.

#### 3.4.1 Tracking what you did

Captured events (§2.2) are matched to plan actions automatically, by the tracking pass (`engine/tracking`, `recommend/tracking_pass`), which runs after every SavedVariables read and before the hourly status pass. Only confirmed purchases and posts match; an attempt waits for its confirmation. A craft event matches by its recipe's product, and each auction mail resolves at most one post (`auction_mail.post_id`):

- **Matching.** An event matches the earliest `pending`, `late` or `partial` action with the same character, kind and item that is still open for matching:
  - a buy, craft or mail: its slot is within 24 h of the event;
  - a post: the event falls between 24 h before its post time and the end of its selling window.

  Further rules per kind:
  - A buy matches only at or below the planned price.
  - A post matches at any price, and records the price you used.
  - A craft matches by recipe.
- **Status.** Matching the full quantity sets `done`, and part of it sets `partial`. You can also mark an action `skipped`. Events that match nothing are still recorded, and they still update inventory and cost. **What "late" and "missed" mean depends on the kind**, and the hourly status pass (§3.1.1) applies them:

  | Kind | `late` | `missed` |
  |---|---|---|
  | buy, craft, mail, DE, vendor | — | still unmatched 24 h after its slot |
  | post | its post time has passed but its selling window hasn't ended ("post now (late)", §3.4.0) | its selling window ended with no matching post; the missed-peak re-plan runs (§3.4.0) |
- **Follow-up.** A done buy or craft for a flip or craft-to-sell keeps its sell action (via `parent_id`). Each engine run re-plans that sell action from fresh prices, and it appears in Today and Week as a reminder ("You bought 20 Linen Cloth on Tue for 40 c each; sell Sat 18:00"). The sell plan shows "below what you paid" when the best option no longer covers the cost. It still recommends that option, because what you paid is already spent.
- **Post outcomes.**
  - A sold invoice resolves the oldest pending post with the same character, item, stack and price.
  - An expired or cancelled mail has no price, so it resolves the oldest pending post with the same character, item and stack.
  - Each outcome updates your sell record (§3.3.3).
- **Stale state.** Every recommendation records the age of the character snapshot it used (§2.2.1 per-location times). One built on a snapshot older than 24 h says so, e.g. "based on Amaya's bags from 3 d ago" (D24): on the Craft page, for every place (bags, bank, mail) of a character that holds an owned mat the row uses. The addon uses its live state instead.
- **Re-planning.** Each engine run replaces `pending` and `late` actions. `done`, `partial`, `missed` and `skipped` actions are kept as history.

**Vendor-sold items** (R22, A33). An item a merchant has been seen selling in unlimited supply (`vendor_reagents`, §3.2)
is bought from the vendor only: the AH is not a source for it, even when a listing is cheaper (`craft.vendor_first`). An
owned unit of it is worth no more than buying another from the vendor, so its hold value is capped at the vendor price,
and it is never planned as a flip. **It is never posted either** (R23, A35): anyone can buy it from the vendor, so the sell plan only sells it to a merchant (when one pays) and plans no hold for it, and a recipe whose product a vendor sells is not crafted. The addon builds its craft market and its own sell rows the same way.

#### 3.4.2 Item rules (blacklist)

- `no_buy`: never recommend buying it (flip, disenchant buys) or crafting it. Stock you already own still gets a sell plan.
- `ignore`: leave it out of every recommendation, including the sell plan. Tooltip prices are still shown.
- `keep` (the **Keep** button on a "not selling" prompt): clears the flag and starts the item's sell record fresh from that moment, so `P'`, the quantity cut and the price cut all reset (§3.3.3). If it then expires again while cheapest (`keep_retry_streak`, default 1), the prompt comes back. Choosing Keep again resets it again.

You set or clear a rule from any dashboard row, from the **Rules** page, or by right-clicking a row in the addon. Rules set in the addon reach AHSync through SavedVariables, and rules set on the dashboard reach the addon through `PortfolioPlan.lua`. When both sides changed the same item, the newer `set_at` wins. **Clearing is a change too:** it is recorded as rule `none` with its own `set_at` (§3.2), so a clear on one side beats an older rule on the other instead of being undone by it.

#### 3.4.3 Reserves (keep for yourself)

Some items you hold are for your own use: food, potions, mats for your own gear. A **reserve** keeps them out of every suggestion without hiding the item. It is set per character, because what you keep belongs to the character that uses it, by right-clicking a row in the addon (window or AH panel): **Keep all**, **Keep at least…** (a number) or **Sell normally** (clears it).

- Kept units are never sold, disenchanted, vendored or used as craft mats, in the addon (A25) or the plan (R20). Only what the character holds above the reserve is planned; "keep all" plans none of it.
- The addon applies a reserve at once, trimming any AHSync action for the item to the free units and scaling its net. AHSync reads `AHProfitDB.reserves` and **follows the file** like professions: when the character's `updated` is at least the stored one, its reserves are replaced with the file's (S14). The next refresh plans with them.
- Inventory still lists kept items and counts their worth at hold value; the plan cell says "Kept for yourself" or "Keeping 10 for yourself · …" (D40).
- The addon's **Reservations** tab lists the character's reserves, held or not: item, how many it keeps ("All" or a number), how many it holds and how many are free to sell. Clicking a row (either button) opens the same menu, to change the number or pick Sell normally (A26).
- SPEC GAP: reserves are set only in game; the dashboard shows them but has no editor yet.

### 3.5 Outputs

1. **Dashboard** (out of game), at `http://localhost:8765`, served by Python's standard library and bound to `127.0.0.1` only. The look of every page is defined by the mockup [docs/mockups/dashboard.html](mockups/dashboard.html). It uses an always-dark WoW theme: stone panels, gold trim and serif gold headings, Blizzard-style red buttons and item-quality colours. Data tables stay in a plain sans-serif font for readability. Open it in a browser and click through the pages, or use a direct link such as `dashboard.html#Week`.
   - **Today**: summary tiles (expected today and this week, budget left, inventory worth), alerts (low coverage, stale data), the actions for right now sorted by expected net with the minimum-chance filter, and a Needs a decision section. Each row has a Skip button
   - **Week**: the rolling calendar from §3.4C (today first), one column per day with a day total, plus totals per category and per character. Posts show at their post time (8 h before the peak) with "for the HH:00 peak"
   - **On the AH**: every auction you have up right now, on all characters. Each row shows:
     - quantity, listed price each and total;
     - **what it cost you** each and total (what you paid, or what the mats cost you for a craft; 0 for items you found);
     - deposit, profit if it sells (total × (1 − cut) − cost);
     - time left, your position (cheapest, or undercut by whom and how much), and the chance it sells.
     
     Tiles at the top total the listed value, the cost, the expected profit and the deposits at stake.

     **Built** (`engine/on_ah_view`, `recommend/on_ah_page`, D18) from the own-auctions snapshots the addon captures: one row per group of identical auctions; the deposit comes from the post event that made it ("—" when that post wasn't captured); position compares with the item's current cheapest listing; the chance is the item's posting-now chance. SPEC GAP: the chance for the rest of the auction's own window, and who undercut you, are not worked out yet.
   - **Disenchant** / **Craft** / **Flip** tables, sortable and filterable, with each, total and risk %. Craft adds a Source column (Owned / AH / Mixed) and filter, and a per-row breakdown of owned versus bought mats **Each row lists its mats** one grey line each, by source, as in the addon (A34): owned (with "mail N from <character>" when they sit on another of your characters), from a vendor, on the AH with what they cost, and intermediates another character crafts first. The crafter is the one who knows the recipe and already holds the most of its owned mats (C19, D44). Crafts stop at the product's liquidity cap for the posting-now window, less what you hold or have listed, and the mats stay within `max_risk_pct` of your gold, as in the plan and the addon (D45). Only recipes worth at least one craft are rows; the footer counts the rest by reason: doesn't pay now, lacks mats, over your risk limit, already held or listed (D46).
   - **Inventory**: what you own, what you paid, what it's worth, and its plan. Selecting a row shows *why*: the 30-day price line and the hour-of-week heat map behind its sell slot
     - **Worth** is counted once per item across all its rows, then split by quantity: the expected value of its planned posts, disenchants and vendor sales, plus its other units (held for a later week, or used by a planned craft, "6 for crafting Copper Rod") at their hold value. A disenchant planned for another character shows who ("Disenchant (Brugan)"). Bound and ignored items say "not planned" and have no worth (D25–D28).
     - **Paid** is the account-wide cost basis per unit; with none it is 0, "before tracking" (D29).
   - **History**: your posts and their outcomes, purchases, crafts, plan actions (done, missed, skipped), and deposits lost (**built**, D47, D48: `state.journal`, `engine/history_view`, `recommend/history_page`; the last 30 days of posts, sold / expired / cancelled / won mail, AH and vendor purchases, crafts, disenchants, payouts with their realized profit, mail sent, and missed or skipped plan actions, each with the plan action it matched; tiles for sold after the cut, expired, deposits lost, plan actions and realized profit. SPEC GAP: untracked changes are not listed yet)
   - **Rules**: blacklisted items, and items flagged "not selling" waiting for a decision (**built**, D50: `engine/rules_view`, `recommend/rules_page`; Waiting for a decision with Blacklist and Keep, Kept on a fresh start with what sold since, and the Blacklist; Undo clears a rule through `POST /api/rule` with `none`. SPEC GAP: "+ Add item" needs an item search and is not built; blacklist from any row instead)
   - **Characters**: professions, skill and gold (**built**, D49: `engine/characters_view`; one card per character: class and level, faction, realm and ruleset, market, gold or the `[budgets]` override, known recipes, last seen, and a bar per profession)
   - **Status**: sync state, data age, missed-hours warnings, the API tier (with the contributor expiry date and a warning 2 days before), and **sell-probability calibration** (predicted against actual sell rate per bucket, plus the Brier score; §8.8)
     - **Alerts** turn every stored Status line into a sentence in local time, most serious first. The per-hour "bad file" lines become one alert per market (D32). One alert is derived, not stored: **AHF data is old** when a market's newest AHF price is more than 6 h old, because then prices, sales and windows rest on stale data and your own scans are the fresh source (D33). An unmapped realm says how to fix `[realms]` (D34).
     - **Cards**: one per market (last sync run, hours handled, newest AHF price and its age, items with prices, hourly history stored, your scans and AHF order books in 7 days), API (key set, Premium), Characters (each with its market, or "no market") and Storage (database size, retention).

   Item names link to auctionhouse.fyi.

   **Security.** Binding to `127.0.0.1` keeps other machines out, but not other web pages. Any site open in your browser can send requests to `localhost:8765`, and a DNS-rebinding site can even read the replies. So `web/server.py`:
   - **Checks `Host`.** It rejects any request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding).
   - **Never changes state on GET.** Skip, rules, Keep and Refresh now are `POST` only.
   - **Checks every write** in two ways, and rejects it with `403` and a log line (without the token) if either fails:
     - the `Origin` header is the dashboard's own origin;
     - an `X-AHSync-Token` header matches a random token generated at each start and embedded in the served page.
   - **Sends no CORS headers,** so other origins can't read responses.
   - **Leaves secrets out of its responses:** the API key and file paths outside `data/` never appear.

   **Chance to sell is shown wherever something gets sold**, always for that context's window (§3.3.2):
   - **Today** and **Week:** every post, and every craft or flip (the chance its product sells in its planned window). Mat buys show none.
   - **On the AH:** the chance your live auction sells in the rest of its window.
   - **Disenchant:** the chance the mats sell.
   - **Craft:** the chance the product sells.
   - **Flip:** the chance the resale sells in its peak window.
   - **Inventory:** the chance the item's planned option sells.
   - **Addon:** the tooltip, and the AH panel's post list.

   **Market activity columns.** On the dashboard, every table that shows a chance shows these columns, with these names and in this order: **Chance · Est. Sales/Day · Current Supply · Supply/Volume** (§3.3.6), each sortable and filterable. Flip adds **Seasonality Strength** and **Expected ROI** before them. The addon window is too narrow for four columns, so its tabs keep the compact pair **Chance · Volume** (§4.3), with supply and days of supply in the Volume cell's hover. Chance and confidence are different things, and both are shown:
   - **Chance** is how likely the item is to sell (`P'`, §3.3.2), e.g. "92% ●".
     - The **dot inside the cell is the confidence**: how much the data behind that chance can be trusted (§3.3.5). Green is ≥ 75%, amber ≥ 45%, grey below that.
     - Hovering the cell shows the exact confidence percentage, and the item detail breaks it down by component.
     - So "75% ○" reads as "probably sells, but it's a guess from thin data".
   - **Est. Sales/Day** is `estimated_sales_per_day` with its source, e.g. "48 AHF" or "31 est."; "—" when unavailable. **Current Supply** is units on the AH now. **Supply/Volume** is days of supply, e.g. "1.9 days"; "—" when unavailable. (The addon's Volume cell is "48/day".)
   - For Disenchant, these columns describe the **mats**, since that's what you sell.
   - `views.py` builds these cells in one place, and a convention test checks that every table using them has all of them in that order.
   - **Every column header has a hover tooltip** saying what the column means and its unit (for example "Supply/Volume: days of supply at the current sales rate; not a time-to-sell guarantee"). The texts live with the column definitions in `views.py`, one per column name, so the same column reads the same on every page.
   - **Item detail** adds 7 × 24 heatmaps of price (`how`), estimated sales (`sales_how`) and listed supply (`supply_how`), the three selling answers (§3.3.6), daily sales and supply over 30 days, and volume confidence and coverage.

   Hovering a Chance cell, or opening the item detail, shows the breakdown: market sell-through, days of supply, units ahead of you and when they expire, expected undercuts, queue result, tier history and your record.

   **Dashboard data (the contract between the full refresh and `web/`).** A full refresh writes one JSON payload per page and market into `views` (§3.2), swapped in atomically (§5.2). The server serves them as stored and computes nothing (§8.5).
   - `GET /` serves the page (`web/static/index.html`) with the per-run token embedded.
   - `GET /api/markets` returns `{"markets": [...], "default": "<id>"}` from the config.
   - `GET /api/views/<page>?market=<id>` returns `{"built": <ms>, "data": <payload>}`, or `404 {"error": "no_refresh_yet"}` before the first refresh. `<page>` is `disenchant`, `craft` (M3), `today`, `week`, `flip`, `inventory` (M4), `status`, and later `on_ah`, `history`, `rules`, `characters`. **`status` is the one page built on request** (`app/status_page.py`, texts by `engine/status_view.py`), because sync runs, SavedVariables reads and failures change it between refreshes; it reads only small tables (status lines, sync state, current prices, scans, characters) and the database file's size, and `built` is the request time. Its payload is `{"alerts": [{"level", "title", "text"}], "cards": [{"title", "badge", "badge_class", "lines": [[label, value, class]]}]}`.
   - Payload of `disenchant` and `craft`: `{"rows": [...], "hidden": <n>, "help": {header: tooltip}}`. Each row is built by `views.disenchant_row` / `views.craft_row`: every cell is already formatted text (money, percents, the market activity cells `chance` with its `dot`, `sales`, `supply` and `days`, risk with `risk_amber`), plus a `sort` object with one number per sortable column (−1 for an unavailable value). `help` is `views.COLUMN_HELP`, the header tooltips. The page sorts by `sort` values and filters by row fields (quality, profession, source, profit); it never parses or computes numbers.
   - Payloads of the plan pages (`recommend/plan_pages.py`, built by `engine/plan_views.py`): `today` is `{"by_min": {"0"|"25"|"50"|"75": {"rows", "hidden", "total"}}, "help"}`, one complete view per step of the page's "Min chance" dropdown, so the page only picks one and never filters or totals itself; `week` is `{"days": [{"label", "today", "total", "rows"}], "total", "categories", "characters", "sums", "help"}`; `flip` is `{"rows", "hidden", "help"}`; `inventory` (`recommend/inventory_page.py`, built by `engine/inventory_view.py` and `engine/item_detail.py`) is `{"rows", "tiles", "details", "help"}`, where `details` maps each held item's key to its item detail (windows, the three selling answers, market activity lines and the three 7 × 24 heatmaps, each cell with a level 0–4 or none for no data and its title), so selecting a row only looks one up. Days and times are in the machine's local time (the clock's offset). Week rows include the week's kept history, so done actions show ticked.
   - `POST /api/refresh` asks for a full refresh (merged with one already queued; IU4) and returns `202`. `POST /api/skip {"action_id": ...}` marks a plan action skipped (§3.4.1) and returns `200 {"skipped": true|false}` (false when the action isn't open) or `400` for a bad body. Writes follow the Security rules above.
   - A market not in the config is `404 unknown_market`; no `market` means the first configured one. A busy database is `503 database_busy`.
   - **Accepted risk:** another process or Windows user on the same machine can send the right `Host`, read the token from `GET /` and write. That is inherent to a loopback tool; the checks protect against web pages, not local processes.
2. **Two addon files** in `Interface/AddOns/AHProfit_Data/`, each written atomically (temp file + check + rename), each with a header holding its `schemaVersion`, `generatedAt` and a checksum of the body. The addon refuses a file whose version or checksum doesn't match and shows why (§5.2).
   - **`MarketIntelligence.lua`** (market data, built on the desktop by `export/` from the intelligence plus the shortlist, which comes from character state; the intelligence itself may be built in the cloud, §5.0): per item, compact strings (Lua constant limits) in two tiers.
     - **Tier 1, every eligible item:** current price and its age, med7/med30/p10/p90, seasonality strength, estimated sales per day, current supply, supply/volume, the top buying and selling windows, the sell-probability curve (§3.1.1), confidence and freshness, and `observationsUntil`.
     - **Tier 2, the shortlist only** (items on any desktop list, in your stock or recipes, or recently scanned): the full 24 × 8 sell-probability curve in place of the 24 × 3 one (§3.1.1). Each window keeps the price ratio and weeks of data it carries (§3.3.4).
     - **The hour-of-week profiles are never exported.** The 168-slot `how`, `sales_how` and `supply_how` stay in AHSync: the addon shows no week heatmap (heatmaps are dashboard-only), and its live decisions use only the windows and the curve, so it never reads or scans a 168-slot array.
     - The disenchant table and the bonus-suffix table are static, so they ship with the addon (`Data/Disenchant.lua`, `Data/BonusSuffixes.lua`) instead of being exported.
     - **Format** (`export/market_lua.py`, read by `Core/Market.lua`): `AHProfitMarket = { schemaVersion, generatedAt, checksum, charMarkets, markets = { [market] = { observationsUntil, curveHour, I, C } } }`. `I[key]` is one string per item, `"cur,curQty,curAgeMin,med7,med30,p10,p90,trend10,sold10,listed10,supply,conf,seasonality,postPrice,refStack,sellValue,volumeUnknown,chance1000;sellWindows;buyWindows"`: integers, an empty field meaning unavailable, ages in minutes before `generatedAt`, trend and per-day rates in tenths, `chance1000` the chance a reference stack posted now sells in thousandths, and each window `start:hours:best:how1000:weeks` joined by `/`. `C[key]` is the curve (§3.1.1). `D[key] = "skill,quality,ilvl,kind"` (kind `a` armor, `w` weapon) for exported items one could disenchant, from AHSync's item list and the disenchant table, so the addon can list targets without an enchanter (A23); the checksum counts them too. Items are filled 2,000 per generated function, so no function exceeds Lua 5.1's constant limits (A3). Every item with a price seen in the last 7 days is exported, plus the shortlist (EX2, EX5); older prices stay on the dashboard. `charMarkets` maps each character to its market; a character it doesn't name uses the only market of its faction (S12). `vendor = { [key] = copper }` holds the buy price of every item AHSync has seen a merchant sell in unlimited supply (`vendor_reagents` with a known vendor price), so the addon's crafts can buy mats from a vendor (EX6); it is not in the checksum, and a file without it reads as no vendor prices.
     - **The shortlist** (EX1): what you hold, your recipes' products and reagents, open plan actions, the rows of the desktop lists, and items in your *partial* (targeted) scans of the last 7 days. Full scans are left out because they cover the whole AH.
     - **The checksum** is `"<strings>:<their total length>"` over every `I` and `C` string: cheap to check at login, and a truncated or mixed-up file fails it. A file whose `schemaVersion` differs, or whose checksum fails, is refused with a status line, never a Lua error (FM17).
     - AHSync writes it after every published full refresh (`export/addon_files.py`); a write blocked 3 times leaves the old file and a Status warning (FM14), and a file over 2 MB is written with a warning.
   - **`PortfolioPlan.lua`** (account-specific): the desktop's plan with action ids so the addon can mark them done, your sell record per item (`s`, `x`, `streak`), the item rules, `basedOnUntil` (the newest event it includes), and `ingestedUntil` per character for the pruning handshake (§2.2), written only after the database commit.
     - **Format** (`export/plan_lua.py`, read by `Core/Plan.lua`): `AHProfitPlan = { schemaVersion, generatedAt, checksum, basedOnUntil, ingestedUntil = { [charId] = ms }, actions = { [charId] = { "id;kind;key;qty;price;atMs;net;parentId;windowEndMs;status;chance;peakMs", ... } }, rules = { [key] = "rule,setAtMs" } }`. Only open actions (pending, late) travel, in time order; `net` is copper, rounded half up, and `chance` the planned chance to sell in whole percent (from `actions.conf`, which holds each action's planned `P'`), and `peakMs` the hour a post is for (`actions.peak_ms`); cleared rules travel as `none` so a clear on the dashboard beats an older rule in game. `basedOnUntil` is the newest `ingestedUntil`. The checksum works like `MarketIntelligence.lua`'s, over every action and rule string (EX3).
     - It is rewritten after each commit that changes it: a published full refresh, a SavedVariables read, and the hourly status pass.
     - **At login**, once both addons have loaded, the addon checks it and prunes each character's committed rows (§2.2, A4). A plan of another schema version, or one that fails its checksum, prunes nothing and shows no plan rows, with a status line (EX4). No plan at all is normal: the addon's own recommendations still work.
     - SPEC GAP: the sell record per item (`s`, `x`, `streak`) joins it with tracking (M6b).
   - Raw history is never exported. Both files have a size budget: ≤ 2 MB and ≤ 200 KB.

   The addon reads them at login or `/reload`.

   **WoW only sees addon files that existed when the client started.** A file created later is ignored until the client restarts, even after `/reload`. So:
   - `tools/deploy.py` installs `AHProfit_Data.toc` and placeholder `MarketIntelligence.lua` and `PortfolioPlan.lua`, and never overwrites existing generated ones.
   - AHSync only ever replaces the files; it never deletes them.

### 3.6 Config (`config.toml`)

```
api_key = ""                                  # optional, discouraged: prefer env AUCTIONHOUSE_APIKEY or %USERPROFILE%\.ahsync\api_key
                                              # lookup order: env var, then key file, then this value
markets = ["forever.pvp.horde.us"]
wow_path = "C:/Program Files (x86)/World of Warcraft/_classic_beta_"
retention_days = 90
dashboard_port = 8765
live_refresh_minutes = 15
min_flip_margin = 0.20
default_sell_prob = 0.25                      # P when there is no volume data (§3.3.2)
default_undercut_share = 0.5                  # u(p) when there is no book history (§3.3.2)
own_record_days = 30                          # window for your sell record (§3.3.3)
own_record_weight = 4                         # how many posts of evidence the model counts as
struggle_streak = 3                           # expired posts in a row before "not selling"
reprice_step = 0.05                           # price cut per expiry in a row while cheapest (§3.3.3)
keep_retry_streak = 1                         # after Keep, expiries before asking again (§3.4.2)
min_sell_prob = 0.5                           # Today hides rows with P' below this (also a UI dropdown)
high_risk_pct = 25                            # risk % shown in amber above this (§3.4)
daily_refresh_time = "06:00"                  # full refresh once a day after this local time (§3.1.1)
post_lead_hours = 8                           # posts are planned this long before their peak slot (§3.4C)
sell_window_hours = 8                         # sell-probability window when posting now (§3.3.2)
pattern_half_life_weeks = 4                   # recency weighting of weekly patterns (§3.3.4)
window_threshold = 0.90                       # a slot joins a best-selling window at ≥ 90% of the best slot's score
peak_tolerance = 0.10                         # after a missed peak, a later window must be within 10% of the original (§3.4.0)
week_start = "Mon"                            # plan weeks start Mon 00:00 local; a new week is a fresh slate
max_craft_depth = 3                           # longest crafting chain followed when sourcing mats (§3.4B)
max_supply_share = 0.5                        # your units per window ≤ this share of others' listed units (§3.4 liquidity)
max_supply_share_gear = 1.0                   # the same for weapons and armor: you may double the market (§3.4 liquidity)
min_gear_units = 2                            # gear always gets this many pieces per window, even on an empty market
max_risk_pct = 25                             # no single buy spends more than this % of all your gold (§3.4 Risk)
max_actions_per_day = 0                       # 0 = no limit (§3.4C.4)
max_item_capital_share = 0.25                 # most of your gold in slow markets (§3.4 market capacity)
slow_market_days = 7                          # supply/volume above this many days is a slow market (§3.3.6)
launch_wow_after_catchup = false

[realms]                                      # realm -> ruleset; needed only when you sync several markets per faction (§2.1)
"RealmName" = "pvp"

[budgets]                                     # optional per-character budget in copper; default is the character's gold
# "Amaya-RealmName" = 150000
```

## 4. AHProfit (addon)

### 4.0 Split of work: app and addon (hybrid)

| Lives in AHSync (heavy, rarely) | Lives in the addon (light, as state changes) |
|---|---|
| all history reads, statistics, weekly patterns, the queue simulation, the plan (§3.1.1) | current listings from in-game scans and searches, your bags and gold right now |
| precomputed **options** per item: sell and hold values, DE EV, crafting options, flip candidates, sell-probability curves | **choosing between precomputed options** when state changes (a price moves, you buy, you post) |
| the dashboard | the AH window panel, tooltip, one-click searches |

**The addon never reads history and never simulates.** It tracks state, reads the market intelligence, and builds its own recommendations (§5.0 invariant 4):
- AHSync exports **market intelligence** in `MarketIntelligence.lua` (per-item baselines, curves, windows, volume and supply, the disenchant table) and, optionally, its cross-character plan in `PortfolioPlan.lua` (§3.5).
- **The addon's own recommendations** (`Recommend/Options.lua`): Disenchant, Craft, Flip and Sell options for the **shortlist** in the intelligence (tier 2 items plus what you hold and your recipes), from the intelligence plus live state (bags, gold, recipes, prices it just saw), using the ported formulas. They follow the same economic rules as the desktop's: shared scenarios check that identical inputs give identical profit numbers (LP1). With no `PortfolioPlan.lua`, or an old one, the addon still recommends; the plan only adds cross-character advice (transfers, which character acts).
- **Built so far** (A15–A18): sell options for what the logged-in character carries. Each unbound item in the bags with market data gets its best option, posting now (the intelligence's sell value and posting-now chance, `chance1000`) or a merchant (`GetItemInfo`'s sell price), shown on Today with the note "post now · AHSync has no plan for it". Items AHSync's plan already has an action for keep the plan's row.
  - **Live prices** (A2, A19): the scanner keeps the last complete full scan's order book in memory (`Scan/Replicate.lua`), and `Recommend/Live.lua` reads an item's listings from it when it is newer than the market file's newest observation. Your own sell rows are re-priced from it the way analysis prices them: post at `min(cheapest − 1, med7)` (`Engine/Prices.lua`, shared scenario PP1), `P` from the curve row for this hour at the units ahead, `E_post` with the merchant price as the fallback and the deposit for the posting-now window; the note says "post now at the live price". A planned buy with no units left at or below its price is dropped from Today, and the footer counts it ("2 gone"). A finished scan redraws the open window.
  - **Crafts** (A9, A20): for the logged-in character's known recipes that AHSync's plan doesn't already craft, `Engine/Craft.lua`'s `Option` (a port of `craft_option` without crafted intermediates, shared scenarios C1–C3) sources mats from the bags at their hold value and from the AH (the live book, else `cur × curQty`), and counts crafts while each still pays. The best-paying craft goes first and each uses what the ones before it left; the mats they use get no sell row. The product's output is valued with its posting-now chance for any quantity (the desktop simulates per quantity, so the two agree when the chance doesn't depend on it). The note says where the mats come from ("from your own mats", "mats from the AH: 60s", "your mats topped up from the AH: 20s"). Vendor-sold mats are bought at the vendor price from the market file's `vendor` table (EX6) when that is cheaper than the AH, as on the desktop (R5). The note says where the mats come from ("mats from a vendor: 2s 66c"). Crafts stop at the product's liquidity cap for the posting-now window, as the plan's do: `max(refStack, floor(max_supply_share × supply))` units, less the product you already hold, divided by the average yield, at least 1 while any units are left (`capacity.liquidity_cap`; A30, A36). When what you hold fills the cap, the Craft tab shows the recipe as made: a green check and "made", with a note to post it (A36). Your own auctions count like the units you hold, until their end passes, and the made note says "×1 on the AH · ends in 11 h" (A42). The addon reads them into `ownAuctions` when the AH opens and after a post or a cancel (A41). UNVERIFIED on Forever: `C_AuctionHouse.QueryOwnedAuctions`, `GetNumOwnedAuctions`, `GetOwnedAuctionInfo` (fields `itemLink`, `itemKey`, `status`, `quantity`, `timeLeftSeconds`, `buyoutAmount`), `OWNED_AUCTIONS_UPDATED`, `AUCTION_HOUSE_AUCTION_CREATED` and `AUCTION_CANCELED`; the diagnostics probe them. SPEC GAP: without supply data there is no cap, and the supply pattern over the window is not applied.
  - **Disenchant** (A23) and **Craft** (A27) tabs: every target or known recipe that pays, each on its own.
  - **Flip** (A28): the plan's flips, re-checked live. SPEC GAP: the addon makes no flips of its own, because sizing one needs the queue simulation, which only AHSync runs (§4.0); without `PortfolioPlan.lua` the tab says so.
- **When state changes in game**, the addon does only O(1) work per changed item:
  1. a new price → re-read `P` from the curve row for the current hour (§3.1.1); posting-time advice comes from the precomputed top windows, never from per-slot profiles;
  2. recompute that item's options with the small ported formulas (fees, `E_post`, DE EV, craft cost);
  3. re-pick the best option;
  4. re-sort only the visible list.

  That's how it "recommends new paths" live: for example, an item gets bought out → its flip row drops; a cheaper mat appears → the craft's cost and rank update.
- The addon runs the **same small formulas in Lua** against live prices. They are ported from the `ahsync/engine/` modules listed under `Engine/` in §5, follow the numeric rules in §3.3, and must give the same numbers as Python on the shared scenarios in `tests/scenarios/`.
- Engine errors in game are caught with `pcall` at the UI boundary and shown as a status line, not as a Lua error popup.

When you're at the AH:

- Opening the AH runs a quick targeted search for every item on the current lists (the addon can also run a full scan). If Forever throttles searches or requires a hardware click for each one (§6), this becomes a "next search" button that steps through the list.
- Each result updates that item's price at once, re-ranks the lists, and drops targets that are gone or no longer profitable.
- Buying an item removes it, spends from the budget, and adds the item to inventory, which feeds the sell plan.
- Buying or posting marks the matching plan action done right away (§3.4.1). This in-game mark is provisional; AHSync's matching in `engine/tracking` is the record, so the matcher is not ported to Lua. An expiry or sale seen in the mailbox updates your sell record for the rest of the session.
- AHSync stays the second trigger. After a `/reload`, the addon picks up fresh baselines and everything your session recorded.

### 4.1 Capture

Everything in §2.2. Recipe definitions are keyed by profession, so they are stored once per account. Which character knows which recipe is stored per character. A future option is a static recipe database for every profession (from wago.tools), so the app can suggest recipes worth learning.

### 4.2 Scan

**Full scans are shared, not requested** (verified 2026-10-16). Forever has `C_AuctionHouse.ReplicateItems`, which returns the whole auction house (about 95k auctions on our realm). The AuctionHouseFYI addon already calls it when the AH opens, at most every 20 minutes, backing off when the server throttles it (3 to 17 minutes). The results arrive as the `REPLICATE_ITEM_LIST_UPDATE` event, which any addon can read.
- **Listen.** While the AH window is open, AHProfit listens for `REPLICATE_ITEM_LIST_UPDATE` and reads every auction (`GetNumReplicateItems`, `GetReplicateItemInfo`, `GetReplicateItemLink`, `GetReplicateItemTimeLeft`). It costs no request of its own, so it never competes with AuctionHouseFYI for the throttle. Reading is read-only.
- **Fall back.** When no scan result has arrived 30 s after the AH opened, and AHProfit hasn't called `ReplicateItems` in the last 20 minutes this session, it calls it itself (same back-off: a call that gets no answer doubles the wait, up to 2 h; any answer resets it to 20 min; A7, A10).
- **Store.** Auctions are grouped as `key, unit price, stack, band → count` (unit price = `floor(buyout ÷ stack)`) and saved as one `scans` entry (§2.2.1) with kind `full` and coverage 1. Closing the AH, a disconnect or a `/reload` before the end saves it as `partial` with the coverage reached (`floor(read × 1000 ÷ total) ÷ 1000`, FM19); partial scans never feed volume (§3.3.1). At most 12 un-ingested full scans are kept, enough for a scan every hour through a 12-hour session without a `/reload`; the oldest is dropped first, and the addon's status line says "run AHSync to sync" (FM22). Partial scans don't count toward the 12.
- **Left out, and counted in the diagnostics** (A11): battle pets, auctions without a buyout, values the client marks secret, and a time left that isn't 1–4 (volume needs it). The diagnostics also hold a histogram of every raw time left.
- **Item key:** from the link (§2.2.1), parsed once per distinct link per scan. An auction whose link isn't available yet (the item isn't cached) is keyed by its item id, or `−itemId` when it is a weapon or armor, or its class is unknown, since only those can roll a random suffix (A13).
- **Reading** follows AuctionHouseFYI's reader (0-based indexes, owner and bidder fields never read), except for time left: Forever returns 1 short … 4 very long, as Classic did, not retail's 0–3 (verified 2026-10-17: no 0 in 41,062 auctions; A12). It runs in steps of at most 4 ms and pauses in combat (A8). Each step aims to stop at 3 ms: it stops when the next row, at twice the step's average row cost, would pass that, which leaves 1 ms for what averages can't foresee, such as a garbage-collector step (measured in game: 4.5 ms when aiming at 4). Reading, sorting the item keys, encoding (each step joins its own rows) and saving each take their own frames, so no phase adds to another's step. An update arriving within 60 s of the last scan's start, or while one is still being read, starts no second scan.
- **AHSync** turns each stored scan into source `local` data: `current` (each key's lowest unit price and the units at it) and `candles_h1` (that price as the hour's close, with the units listed as its quantity). For each pair of consecutive complete local scans of a market at most 6 h apart, the order-book diff (§3.3.1) gives `books` volume, summed per day and written whole for that day, so re-reading a file changes nothing.
- A scan of a search or page (the Browse list) is not captured in v1.

The scan's results also update the addon's prices for this session, which matters for disenchant and flip targets (M5).

### 4.3 Show

The look is defined by the mockup [docs/mockups/addon.html](mockups/addon.html) and the wireframes below. Open the mockup in a browser and click the tabs, or use a direct link such as `addon.html#Craft`. The visual tests (§8.4) check the real addon against it.

The window's tabs, each shown in the mockup:

| Tab | Shows |
|---|---|
| Today | actions for now, sorted by expected net (highest first), with the "Min chance" filter and a Needs a decision section (wireframe below) |
| Week | the rolling 7-day plan from today, grouped by day with day totals; done actions ticked; each craft preceded by its mat buys (and any intermediate crafts and mail steps) and followed by its post; posts timed 8 h before their peak |
| Disenchant | **always shown**, even with no enchanter on the account: an alt may be the one (A23). Item, ilvl, the Enchanting skill it needs, buy (the live scan's cheapest, else the file's), EV, profit, Chance · Volume (of the main mat), and a note with the expected mats; "no enchanter yet" when nobody on the account has Enchanting, "needs Enchanting 225 (best 150)" (greyed) when your best is too low. Only targets that pay. Live search state (✓ / ✗ / …) comes with the AH panel |
| Craft | Dashboard: product, who knows it, **source** (Owned / AH / Vendor / Mixed, plus "+ crafted" for chains; filterable), crafts, mat gold each and total ("—" when you own every mat), profit, Chance · Volume, risk, cooldown; a note lists which mats are owned and which are bought. The addon's tab is the character you're playing: product, Source, ×, Mats (gold for the mats to buy, "—" when you own them all), profit, Chance · Volume, risk, and a note of the mats used and bought. Each known recipe is costed on its own against what you carry less what you keep; only recipes that pay are listed (A27, UI11). Source is Owned, AH, Vendor or Mixed (C17, C18), and the note has one grey line per mat (A34): "use 4 owned Simple Wood", "buy 3 Coarse Thread from a vendor · 30c", "buy 2 Copper Bar on the AH · 20s" (A30, A34) |
| Flip | Dashboard: item, quantity, buy each and total, expected sell price, sell slot, profit, Chance · Volume, risk %. The addon's tab lists the flips AHSync's plan has for the character you're playing (a buy whose chain ends in a post): item, Qty, Buy (at or below), Sell, When (the post's peak), Profit (the chain's), Chance (its first post's), Risk, and a note with the post time and the live cheapest price. A live scan drops a flip that is gone or now costs more than its buy price, and the footer counts it (A28, UI13) |
| Inventory | what the character you're playing holds: item, where (bags, bank, mail), quantity, worth, Chance · Volume, and its plan as a note under the row (AHSync's, marked "(AHSync)", or the addon's own: "Post now at 45c", "For crafting Copper Rod", "Bound · not planned"), best worth first (A22). Paid joins it with tracking (M6b), and listed auctions with On the AH |
| Inventory (your auctions) | the character's active auctions as rows with Where "AH": units, worth if sold (price × units × 0.95), note "listed at 80c each · ends in 11 h", from the last own-auctions snapshot; one past its end is left out (A50) |
| Reservations | what the character keeps for itself (§3.4.3): item, Keeping (All or a number), Held, Free; click a row to change or clear it (A26) |

Right-clicking a row opens a menu: Search the AH, Mark as skipped, Blacklist: don't buy or craft, Blacklist: ignore completely, and Keep (start fresh) on a "not selling" item.

**Main window** (`/ahp`), default 640 × 440 px, minimum 560 × 360:

```
┌ AHProfit ── Grommash · Horde PvP ───────────────────────── data 2 h old ── [x] ┐
│ [Today] [Week] [Disenchant] [Craft] [Flip] [Inventory]      Min chance [50%▾] │
│───────────────────────────────────────────────────────────────────────────────│
│       Item              Qty  Each    Total   Net       Chance   Volume  Risk │
│ BUY   Mithril Spurs     ×1   3g      3g      +73s 42c   85% ◐    3/day  9.6% │
│ POST  Copper Rod        ×5   41s     2g 5s   +63s 6c    58% ◐    6/day  0.0% │
│       Your record: 2 of 5 sold · P 80% → 58% · price −10% · qty ¼            │
│ BUY   Silk Cloth        ×40  70c     28s     +7s 30c    88% ●   64/day  0.9% │
│ DE    Scouting Glov…    ×1   8s      8s      +6s 25c   100% ●  210/day  0.3% │
│ ...                                                                    ▲▼     │
│ Needs a decision                                                              │
│ POST  Heavy Leather   ×5   47c     2s 35c  NOT SELLING    [Blacklist ▾] [Keep] │
│───────────────────────────────────────────────────────────────────────────────│
│ 7 actions · expected +1g 54s 15c · 1 hidden (chance < 50%) · budget 31g 40s   │
└───────────────────────────────────────────────────────────────────────────────┘
```

The dot after each chance is its confidence. Hovering "85% ◐" shows "Confidence 63%".

- **The window is about the character you are playing.** It shows only that character's actions, so it has no Who column; the dashboard shows every character, with one.
- Each row shows: action, item (in its quality colour), quantity, **each** and **total** (§3.4), expected net (green when positive, red when negative), then the standard pair **Chance · Volume**, then **risk %** (measured against all your characters' gold).
- **The pair is identical on every addon tab.** (The dashboard shows the fuller market activity columns, §3.5.)
  - **Chance** is `P'` (§3.3.2) with the **confidence dot** inside the cell (● ≥ 75%, ◐ ≥ 45%, ○ below; §3.3.5). Hover shows the exact confidence %.
  - **Volume** is units sold per day. The dashboard also shows the source and the units on the AH.
  - Rows with nothing to sell (mat buys, mail, holds, vendor) show "—" in both.
- **Every column header shows a tooltip on hover** (`GameTooltip`) saying what the column means, with the same texts as the dashboard where the column is the same (§3.5). The texts live in `UI/ViewModel.lua`.
- Your sell record (§3.3.3) appears as a second line under the row, but only when it changed the recommendation.
- Long item names are cut off with "…". The list scrolls, and the window never grows past its size.
- Money uses gold, silver and copper coin icons.
- The window is movable and resizable, remembers its position, and closes with Escape.
- **Empty states:**
  - With no `MarketIntelligence.lua` yet: "Run AHSync, then /reload".
  - With nothing to do: "No actions right now".
  - Data more than 24 h old: the age badge turns amber.
- Disenchant and Craft numbers are re-checked live against the fresh in-game scan.

**AH side panel**, docked to the right of the auction house window, 300 px wide:

```
┌ AHProfit at the AH ──────────────┐
│ Shopping                 3 of 5  │
│ ✓ Linen Cloth   38c ≤ 45c   ×20  │
│ ✓ Scouting Gl.  8s ≤ 9s     ×1   │
│ ✗ Copper Bar    gone             │
│ … Iron Ore      checking         │
│ Post                             │
│ ○ Copper Rod    41s  ×5          │
│ [Next search]        [Full scan] │
│ 2 found · budget left 31g 40s    │
└──────────────────────────────────┘
```

Clicking a row searches for that item.

In the addon (`UI/AHPanel.lua`, rows from `ViewModel.AHPanel`; UI7, A24, FM24): it opens docked to the AH window's right edge on `AUCTION_HOUSE_SHOW` and hides on close. **Shopping** is the character's planned buys, then disenchant targets your best enchanter can do (bought at most at their EV), up to 10. A row is ✓ when the live data (the last full scan, or an AH search's result if newer) has its quantity at or below its price ("38c ≤ 45c"), ✗ "gone" when not, and … "checking" until one has been seen; the states are WoW's ready-check icons, since its fonts have no ✓ ✗ glyphs. "3 of 5" counts the rows checked. Each section has labelled columns whose headers explain themselves on hover: Shopping **Item · For · Price · Max · Qty**, For being why it is bought: CRAFT (a mat for a planned craft), FLIP (to resell later) or DE (to disenchant), from what follows the buy in its plan chain (A40) (Price is the cheapest listing seen now, or "gone" / "checking"; Max is the most worth paying each), Post **Item · List price · Qty · Est. profit · Chance** (Est. profit is the post's own expected net for all its units, even when a flip counts its profit on the buy). **Post** lists the character's Today posts (AHSync's posts due now and the addon's own "post now" options), with Today's Min chance filter, with price and chance. Shopping lists only what to buy on the AH: a vendor buy stays on Today, marked "from a vendor" (A32). AHSync's posts, vendor sales and disenchants of stock (no parent) are never shown for more than the character holds in bags, bank and mail above what it keeps, so a posted item leaves Today as soon as the bags are read again; a chain's later step stays (A49). Only items in your bags are listed in Post: a post for an item you don't hold yet is a later step of a chain (a craft or a flip) and waits until you have it (A30). Each row is capped at the units you hold (shared in order across rows of the same item), with its Est. profit scaled to match, and the panel and the window redraw whenever the bags have been read again, so a posted item drops off or shrinks at once (A31). **Clicking a Post row fills the AH's Sell tab** (A29): the item from your bags (the first unbound stack), the quantity (at most what you can post) and the unit price; you check it and press the AH's own Post button, so a wrong suggestion never lists by itself. When the AH fills in its own price from a search right after (within 10 s), the suggested price is put back once. Without the Sell tab's API the status line says so (FM30). Right-click keeps the item for yourself (§3.4.3). The auction duration is set too (A56): the plan's post carries it (EX9), an own "post now" row uses the shortest duration covering the posting-now window; without the AH's duration control the status line says which duration to pick. UNVERIFIED on Forever: `AuctionHouseFrame:SetPostItem`, `ItemLocation:CreateFromBagAndSlot`, the sell frames' price and quantity inputs, `C_AuctionHouse.GetItemCommodityStatus`, `GetAvailablePostCount`, `IsSellItemValid` and the search-result events; the diagnostics probe them. An empty Shopping list says why. A click searches through the AH window's search bar (a hardware event); the status line says what it searched and how far this visit has got ("searched Linen Cloth · 2 of 5"); a search whose results don't list the item marks it ✗ gone, and a result is matched to a random-suffix item by its item id (A38). A buyout or commodity purchase seen this session (hooks that only observe `C_AuctionHouse.ConfirmCommoditiesPurchase` and buyout-sized `PlaceBid`; a commodity purchase counts once `COMMODITY_PURCHASE_SUCCEEDED` fires and is dropped on `COMMODITY_PURCHASE_FAILED`) lowers that row's quantity, and a row whose quantity is bought drops off; a bid changes nothing (A39). The purchases are saved as events, so this holds after a /reload too, until AHSync's next plan includes them (FM18); **Full scan** asks for a ReplicateItems scan within the same 20-minute gap as the fallback and says how long to wait otherwise. A progress bar under the status line follows every scan, whoever asked for it: waiting for the server, reading ("reading 23,400 of 47,667", up to 85% of the bar), saving, then "full scan done · 47,667 auctions"; it updates every 10 steps, never per frame. An own request with no answer after 60 s says "no answer: another addon may have scanned recently" (A43). Search results (`AUCTION_HOUSE_BROWSE_RESULTS_UPDATED`, `C_AuctionHouse.GetBrowseResults`) update that item's live price for the session. Closing the AH drops the search queue (FM24). UNVERIFIED on Forever: the search bar's `SetSearchText`/`StartSearch`, the browse event and results; the diagnostics probe them.

**Tooltip**: a short block under the item's own lines. It uses label/value pairs (`AddDoubleLine`), one fact per line, and every price is per unit:

```
AHProfit
Current AH price        8s (2h ago)
Historical price        11s (30d, +38%)
Disenchant value        14s 25c (+78%)
Best time to sell       Sat 17:00–21:00
Price at best time      12s 30c (+54%)
Chance to sell          46%
```

| Line | Meaning | Source |
|---|---|---|
| Current AH price | the cheapest listing on the AH right now (`cur`), with how long ago it was seen (`curAge`) | §3.3 |
| Historical price | the median price over the last 30 days (`med30`), with its delta from the current AH price. It falls back to daily closes when hourly data is sparse | §3.3 |
| Disenchant value | expected mats × their sell value, with its delta from the current AH price. The line is left out when none of your characters can disenchant the item | §3.4A |
| Best time to sell | the top best-selling window, e.g. "Sat 17:00–21:00", and the pattern strength when it's not yet established | §3.3.4 |
| Price at best time | the expected price in that window (`level × how × trend`, level `med7` else `med30`), with its delta from the current AH price | §3.4C |
| Chance to sell | `P'` for posting one reference stack (§3.4) now (next 8 hours), including your own record | §3.3.2–3 |

**Delta** on the Historical price, Disenchant value and Price at best time lines = `(value − cur) ÷ cur × 100`, rounded half up to a whole percent and shown with a sign. Positive is green and negative is red. It is left out when there is no current price. A positive delta means the item is worth more than it costs right now: a higher historical price or price at best time means it's cheap now, and a higher disenchant value means buying to disenchant pays.

A line with no data shows "—", except Disenchant value, which is left out as described above.

**In the addon** (`UI/Tooltip.lua`, lines from `ViewModel.Tooltip`, the Lua twin of `views.tooltip`; TT1–TT5 are shared scenarios run in both, A21, UI8): the block reads the market file, and a newer in-game scan's cheapest price replaces `cur` ("40c (now)"), with the deltas following it. Best time to sell is the top selling window in local time, tagged "(early)" under 3 weeks of data and "(pattern)" under 6, so the longest line fits 320 px (UI8). Price at best time is `level × how` of that window (`med7`, else `med30`); SPEC GAP: the trend factor is not applied yet. The Disenchant line uses the ported disenchant table with the item's quality, level and class from the client and your best Enchanting skill. An item without market data adds nothing, and a failure never breaks the tooltip. The hook is `TooltipDataProcessor` where the client has it, else `GameTooltip`'s `OnTooltipSetItem` (UNVERIFIED on Forever).

**Structure.** `UI/ViewModel.lua` turns the addon files, the addon's own recommendations and the live state into plain tables of rows, labels, colours and formatted money. The frame code (`Window`, `AHPanel`, `Tooltip`) only draws those tables. So most UI behaviour is testable as plain Lua, without frames.

`/ahp demo` loads a fixture plan into the real window, so the UI can be checked in game before any real data exists.

Other commands:
- `/ahp perf` shows the addon's memory and timing against its budgets (§5.1).
- `/ahp refresh` asks AHSync for a full refresh at the next `/reload` or logout.

### 4.4 Act

Clicking a row runs an AH search for it, and the shopping and post lists show alongside. You still click to buy or post.

### 4.5 Static data

`Data/Disenchant.lua` holds quality × ilvl band × armor/weapon → material probabilities, plus the Enchanting skill per ilvl. It is seeded from Classic Era values and must be verified for Forever.

## 5. Architecture and tech

### 5.0 Components and boundaries

The system is built as separate components that talk only through versioned contracts, so that **collection and historical analysis can move to the cloud** (for example AWS Lambda, if a Blizzard API ever replaces thin community data) without changes to the addon or the recommendation side.

```text
 Providers (AuctionHouseFYI, own in-game scans, future Blizzard API)
        │
        ▼
 COLLECTOR ──► HISTORICAL STORE ──► HISTORICAL ANALYSIS ──► MARKET INTELLIGENCE (versioned)
 collect/        history/             analysis/                intel/
                                                                   │
                                   ┌───────────────────────────────┤
                                   ▼                               ▼
                       EXPORT (MarketIntelligence.lua)   DESKTOP RECOMMENDATIONS ◄── STATE IMPORTER ◄── SavedVariables
                       export/                           recommend/                  state/                (addon)
                                   │                               │
                                   ▼                               ▼
                       ADDON (state, own recommendations,  PORTFOLIO OPTIMIZER ──► DASHBOARD (web/)
                       transaction journal)                 portfolio/            └► PortfolioPlan.lua (optional advice)
```

| Component | Folder | Owns | Does not own |
|---|---|---|---|
| Collector | `collect/` | acquiring, validating and normalizing observations from interchangeable provider adapters; catch-up checkpoints; own scans (§4.2) | statistics, recommendations |
| Historical Store | `history/` | raw observations (candles, current, books, volume rows), retention | business logic |
| Historical Analysis | `analysis/` | market-wide statistics, weekly patterns, seasonality strength, volume estimates, supply, buy/sell windows (§3.3) | anything character-specific |
| Market Intelligence | `intel/` | the versioned analysis results and their store | live character state |
| Export | `export/` | `MarketIntelligence.lua` (the intelligence, trimmed to the shortlist) and `PortfolioPlan.lua` (§3.5) | analysis, recommendations |
| State Importer | `state/` | reading SavedVariables, idempotent import, character snapshots and the transaction journal, freshness per character | recommendations |
| Desktop Recommendations | `recommend/` | per-character Disenchant, Craft, Flip, Sell options from intelligence + state | collection, history |
| Portfolio Optimizer | `portfolio/` | the cross-character plan: capital, shared market capacity, transfers (§3.4C) | raw collection |
| Dashboard | `web/` | showing results; writes only through its secured endpoints (§3.5) | any calculation |
| Engine | `engine/` | the pure formulas every component above uses (§3.3, §3.4) | I/O of any kind |
| Lifecycle | `app/` | the scheduler and the order of a refresh: analysis → recommendations → publish | formulas |
| Storage | `db/` | the local SQLite implementation of every store (one file, `data/ahsync.db`) | business logic |

**Contracts.** Every arrow above is a typed, versioned contract with a schema version, owner, timestamp meaning (observation time, never import time), unique ids, an explicit "unavailable" value, validation rules and failure behaviour.

| Producer → consumer | Contract |
|---|---|
| Collector → Historical Store | normalized observations (`collect/provider.py`: `Observation`, `BookObservation`), with source, observation time and completeness |
| Historical Store → Analysis | `history.reads.HistoryReader` (a protocol: closes, current, books, volume, items), read **in bulk per market** (below) |
| Analysis → Intelligence | `intel.contract.MarketIntelligence` (`INTEL_SCHEMA_VERSION`, method versions, `built_ms`, `observations_until_ms`, one `ItemIntel` per item; contents below), JSON-serializable |
| Intelligence → Export / Recommendations | `intel.store.IntelReader` |
| State → Export | the shortlist: item keys on any desktop list, in stock or recipes, or recently scanned (`state.reads.StateReader`) |
| Addon → State Importer | SavedVariables (§2.2.1) |
| State Importer → Recommendations, Portfolio | `state.reads.StateReader` (characters with freshness, skills, recipes, stock, journal) |
| Recommendations → Portfolio → Dashboard | option and plan records; dashboard pages as in §3.5 |
| Export → Addon | `MarketIntelligence.lua`, `PortfolioPlan.lua` (§3.5) |

**Architectural invariants** (checked by AR1–AR4 and the convention test):
1. Collection is independent of analysis; analysis is independent of character state: `analysis/` imports only `engine/`, `history.reads` and `intel.contract`.
2. Analysis runs on any `HistoryReader`, not only SQLite (AR1), and its output depends on nothing else.
3. Export and recommendations never compute history statistics; they read intelligence.
4. The addon builds its own recommendations from intelligence plus live state, and works without the desktop running; the desktop's plan is optional advice.
5. Listing changes never become confirmed sales; seasonality strength, expected profit and sales volume stay separate numbers.
6. Every component is testable on its own with fakes.
7. **The intelligence is complete.** Every number derived from history that any consumer needs is in `MarketIntelligence`, so a consumer never needs a `HistoryReader` (AR5).

**Contents of the intelligence.** Per item (`ItemIntel`), everything market-wide that §3.3 computes from history:
- prices and their ages: `cur`, med7/med30, p10/p90, trend, the latest book;
- volume and supply (§3.3.1, §3.3.6): sales, expiries and listings per day with their sources, current supply, supply/volume, the confidence components;
- weekly patterns (§3.3.4, §3.3.6): `how`, `sales_how` and `supply_how` (168 slots each) with `weeks` per slot, and seasonality strength. **Each profile is stored packed**: 168 comma-separated integers in thousandths, `floor(ratio × 1000 + 0.5)` (§3.3 rounding), and its weeks as 168 comma-separated counts; an empty string means no data (flat 1.0, 0 weeks). Analysis computes the windows and the curve from the packed values, so anyone decoding them gets the same windows (as IU11 does for the curve). Packing keeps a 9k-item market's intelligence within the memory budget (§3.1.2);
- the top 3 selling windows and top 3 buying windows, each with its start slot, length, best slot, average `how` and weeks of data;
- the sell-probability curve: the full 24 × 8 grid, already quantized (§3.1.1), with the hour its first row starts at.

Analysis computes these the same way for every item. It never chooses by your lists, stock or recipes; that trimming belongs to the export.

**Bulk reads.** Analysis reads history **per market, not per item**: `HistoryReader` offers one read that streams every item's closes, books and volume rows for a market in key order. Against local SQLite this is one query per table, each walking the table's primary key `(market, key, …)` so its rows arrive in key order without a sort (a whole-market sort of the hourly tables costs about 10 s per 2M rows), merged by key with one item in memory at a time. Against a remote store it keeps a market to a few round trips instead of several per item (about 40k for 9k items) (AR7).

**Cloud boundary.** The Collector (remote providers), the Historical Store and the Analysis can run elsewhere, for example as a scheduled AWS Lambda, and publish `MarketIntelligence` as versioned JSON. The desktop then:
- fetches the newest intelligence and checks its version (AR2) and age; when it is missing or too new, it keeps the last good one and shows its age (§5.2);
- runs recommendations, the portfolio and the dashboard from it, with no history store;
- builds `MarketIntelligence.lua` itself, because the trimming needs the shortlist (character state) and the file must land in the local AddOns folder.

Reading SavedVariables and writing the addon files stay on the desktop. Own in-game scans would have to be uploaded to feed cloud analysis.

### 5.0.1 Tech

- **AHSync**: Python 3.12 (installed), standard library only (`urllib`, `gzip`, `json`, `sqlite3`, `tomllib`, `http.server`). The dashboard is one static HTML/JS page served with JSON endpoints. Started from `start.bat`, with an `.exe` via PyInstaller later.
- **AHProfit**: Lua 5.1 and WoW FrameXML, with no libraries in v1.

Layout. **`tests/` mirrors the source tree one-to-one**: every source module has a test file at the same relative path.

```
ahsync/
  config.py
  clock.py    Clock protocol + SystemClock (injected everywhere; tests use FakeClock)
  app/        scheduler.py (timers, daily refresh, file-change watch), refresh.py (analysis → recommendations → publish, §3.1.1)
  collect/    provider.py (provider adapter protocol, normalized observations), client.py (HTTP, retry, 429), ahf.py (AHF adapter),
              catchup.py, local_scans.py (own scans → local observations, §4.2)
  history/    reads.py (HistoryReader protocol + SQLite reads), writes.py, prune.py
  analysis/   market.py (history → MarketIntelligence, §3.3)
  intel/      contract.py (MarketIntelligence, ItemIntel, INTEL_SCHEMA_VERSION), store.py (IntelReader/IntelWriter + SQLite)
  state/      luadata.py (WoW SavedVariables Lua parser), savedvars.py (AHProfitDB → records, §2.2.1) with savedvars_fields.py (its field converters), importer.py (records → store),
              writes.py, reads.py (StateReader + SQLite)
  recommend/  pages.py (Disenchant and Craft options and pages), later flips and sells
  portfolio/  plan.py (cross-character plan, §3.4C; M4)
  db/         sqlite.py (the local Database: connection, migrations, backups, and every store's SQLite implementation), schema.sql
  engine/     fees.py, prices.py, profile.py (hour-of-week), volume.py, sellprob.py (incl. the P' blend), de_table.py (disenchant table, §4.5),
              record.py (own sell record + calibration), tracking.py (event → action matching), rules.py,
              patterns.py (weekly patterns, best selling windows), confidence.py (conf %), disenchant.py, craft.py,
              capacity.py (liquidity caps, shared market capacity, §3.4), plan.py, views.py (tab totals, sorting, filters, tooltip deltas)
  export/     market_lua.py (MarketIntelligence.lua), plan_lua.py (PortfolioPlan.lua), addon_files.py (atomic writes, the shortlist)
  web/        server.py, static/
addon/AHProfit/
  Core/       Adapter.lua (every game API call) and AdapterAH.lua (its auction house half: panel, Sell tab, replicate scan), Events.lua, DB.lua (AHProfitDB setup, migrations, pruning; §2.2.1), Diagnostics.lua (§2.2.1), Market.lua (reads MarketIntelligence.lua: checks, lazy decode), Plan.lua (reads PortfolioPlan.lua; prunes at login)
  Capture/    Characters.lua, Professions.lua, Inventory.lua, Auctions.lua (your own auctions, A41), Mail.lua, Posts.lua, Purchases.lua, Crafts.lua
  Scan/       Search.lua, Replicate.lua
  Engine/     Fees.lua, SellProb.lua, Disenchant.lua, Craft.lua, Prices.lua   (ports of ahsync/engine)
  Recommend/  Options.lua (the addon's own Disenchant / Craft / Flip / Sell options for the shortlist, §4.0), Live.lua (listings from the last in-game scan)
  UI/         ViewModel.lua, CraftView.lua (the Craft tab's rows), RowMenu.lua, MainWindow.lua, Tooltip.lua, AHPanel.lua, Demo.lua
  Data/       Disenchant.lua (generated from engine/de_table.py), BonusSuffixes.lua (generated: bonus ID → suffix id, §6 item 11)
addon/AHProfit_Data/   AHProfit_Data.toc, MarketIntelligence.lua, PortfolioPlan.lua (placeholders; AHSync generates the real ones)
tests/
  ahsync/...           test_<module>.py, same tree as ahsync/
  addon/...            <Module>_spec.lua, same tree as addon/AHProfit/
  fixtures/ahf/        trimmed recordings of real AHF responses (the templates)
  fixtures/worlds/     w1.json + w1.expected.json: the whole-plan snapshot (§8.6)
  properties/          generated invariant cases shared by Python and Lua (§8.7)
  scenarios/           named JSON scenarios shared by the Python and Lua tests (their Lua form goes to addon/generated/, not committed)
  support/             builders.py, fake_clock.py, fake_transport.py, gen.py (seeded case generator), lua/runner.lua, lua/wow_stubs.lua,
                       lua/layout.lua (anchor solver + layout checks)
  visual/              scenarios/*.lua (UI1…), golden/*.txt (frame-tree snapshots), golden/*.png (renders)
  test_conventions.py  docstrings, size limits, mirroring, scenario IDs (§8.1)
  pending_scenarios.txt  listed IDs not built yet (§8.1)
pyproject.toml         pytest and coverage settings only (no runtime dependencies)
requirements-dev.txt   pytest, coverage
tools/                 record_fixtures.py, gen_lua_fixtures.py, gen_properties.py (LP1 cases, §8.7), gen_bonus_suffixes.py, gen_disenchant_data.py, deploy.py, savedvars_report.py (what the addon captured; §6 checks), visual.py, bench.py (performance budgets, §3.1.2)
docs/SPEC.md, docs/SCENARIOS.md, docs/mockups/addon.html (in game), docs/mockups/dashboard.html (desktop)
CLAUDE.md
```

`tools/deploy.py` copies `addon/*` into the WoW AddOns folder.

### 5.1 Addon performance (WoW best practices)

The addon must never be noticeable in game: **no work while idle, a few milliseconds at most when something happens, nothing in combat.**

| Rule | How |
|---|---|
| **No `OnUpdate` polling** | Everything is event-driven. Delays use one-shot `C_Timer.After`, never a per-frame handler. |
| **Coalesce bursty events** | `BAG_UPDATE` and similar fire many times at once. Mark the state dirty, then handle it once on `BAG_UPDATE_DELAYED` or after a 0.5 s debounce timer. |
| **Register events only when needed** | AH events are registered on `AUCTION_HOUSE_SHOW` and unregistered on close; mail events likewise for the mailbox. |
| **Pause in combat** | On `PLAYER_REGEN_DISABLED`, queue all non-essential work (scans, re-ranks, UI refreshes) and run it on `PLAYER_REGEN_ENABLED`. Never touch frames in combat. |
| **Lazy UI** | Frames are created on the first `/ahp` or AH open, not at login. A hidden window does no updates. |
| **Pooled, virtual lists** | Only the visible rows exist (about 15 frames per list), reused while scrolling. Rows are never created per item. |
| **Lazy data decode** | `MarketIntelligence.lua` keeps items as compact strings keyed by item id. A row is decoded only when needed (tooltip, list) and kept in a small cache of about 500 entries. Login never builds 9k tables. |
| **Cheap tooltip** | One cache lookup plus a few `AddDoubleLine` calls. No string building beyond formatting the numbers. |
| **Chunked full scans** | A full scan's results (`ReplicateItems`, usually requested by AuctionHouseFYI, §4.2) are read in a coroutine with a **4 ms budget per frame**, stepped by one-shot timers, so it never stalls the game. Reading pauses in combat and resumes after it. AHProfit requests a scan itself only as the §4.2 fallback. |
| **No per-frame allocations** | Reuse tables, cache globals in locals, and avoid string concatenation in loops (already in CLAUDE.md). |
| **Small SavedVariables** | Order books are stored as compact strings. At most 12 un-ingested full scans are kept, about 0.4 MB each (the oldest is dropped first, with a status warning), and the pruning handshake (§2.2) removes ingested data. Target: ≤ 5 MB on disk. |
| **Respect AH throttles** | Searches go through one queue that obeys `CanSendAuctionQuery` or its Forever equivalent (§6). |

**Budgets**, measured in game with `/ahp perf`: memory from `GetAddOnMemoryUsage` and time from `debugprofilestop`.

| What | Budget |
|---|---|
| CPU while idle (no AH, no window) | 0 ms per frame (no handlers running) |
| Memory after login | ≤ 3 MB, plus the intelligence strings (≤ 2 MB) |
| Memory with the window and AH open | ≤ 10 MB |
| One state change (price, buy, post) | ≤ 2 ms total |
| Tooltip | ≤ 0.2 ms |
| Full scan processing | ≤ 4 ms per frame |
| Login / `/reload` added time | ≤ 100 ms |

### 5.2 Failure modes and recovery

Data moves through many hand-offs: AHF → AHSync → SQLite → `MarketIntelligence.lua` / `PortfolioPlan.lua` → WoW → SavedVariables → AHSync. Any of them can fail halfway. Six rules make every failure recoverable:

1. **Commit points, then resume.** Every step commits in small units (one hour file, one SavedVariables ingest, one refresh). A crash resumes from the last commit point; nothing restarts from zero.
2. **Idempotent writes.** Every write is an upsert on a natural key, so repeating a step is harmless. Every captured event has a unique id, `charId:timeMs:seq`, so reading the same SavedVariables twice never double-counts.
3. **Never publish half a result.**
   - A full refresh writes into staging tables (`item_stats_new`, `plan_new`, …) and swaps them in with one transaction.
   - The addon files are each written to a temp file, checked, then renamed.
   - On any failure, the previous complete version stays in place.
4. **Last good state survives.** When a step fails, the dashboard and the addon keep the last good numbers, show their age and say what failed: "Refresh failed at 06:02 (disk full) · showing yesterday's numbers".
5. **Never lose your events.**
   - The addon deletes an event from SavedVariables only after AHSync confirms it was **committed** (`ingestedUntil`, §2.2), and AHSync writes `ingestedUntil` only after the database commit.
   - Unconfirmed events are simply sent again next time.
6. **Validate at every boundary, and degrade instead of crashing.**
   - Each file has a format version and a check: AHF responses, SavedVariables, the market intelligence and the addon files.
   - One bad item is set aside with its error, and everything else carries on.
   - Errors go to `data/ahsync.log` (rotated, never containing the API key) and to the Status page.

**AHSync failures:**

| Failure | What happens | Recovery |
|---|---|---|
| Network timeout, 5xx, 429 | retry with backoff (1, 2, 4, 8, 16 s: the first try plus 5 retries), honouring `Retry-After` up to 300 s; a longer `Retry-After` skips the run | the next run resumes at `sync_state` |
| 401 (key rejected or expired) | that request is retried once without the key; if the public request works, the run continues on the free path; Status "API key rejected" | fix the key; nothing crashes (FM25) |
| Any other HTTP error on `/files` or `/me` | that market is skipped for this run; Status line | the next run retries |
| AHF file truncated or bad gzip/JSON | not committed; logged; the hour gets a `fetch_failures` row and `last_hour` moves on, so later hours still sync | each later run retries the `fetch_failures` hours first; 3 failures in a row → "bad file" on Status; an hour that ages out of every source while still failing is added to the "lost" report, never dropped silently |
| Premium file refused (`403`) mid-run | that endpoint is disabled for the run; its remaining hours are not requested and are reported as lost (or day-only if day candles cover them) | Status line; re-checked at the next `/me` (FM26) |
| Hour file with an unexpected shape | treated as an API change, not a bad file: the endpoint is disabled, the hour loop stops, and `last_hour` does not advance | Status "AHF format changed: h1"; nothing is lost while the parser is fixed (FM27) |
| Live table (`GET /ah/:market?since=`) | not recorded yet, so unsupported: skipped quietly (one log line, no Status error) | add a fixture and a parser when its format is known |
| AHF response with an unexpected shape (API change) | that endpoint is disabled for the run; the last good data is kept | Status: "AHF format changed: snapshot"; record a new fixture and fix the parser |
| Suspect price (below 10% of `p10_30` or above 10× `p90_30`) | stored but flagged (`ItemIntel.cur_suspect`, FM5): the item is never flipped, its AH book is no craft mat source and it is no disenchant buy | is ordinary again once confirmed by a second observation: two hourly closes in the last 24 h out of range. SPEC GAP: it still counts in the statistics, and the addon's own mat sourcing still sees it |
| Crash or power loss mid catch-up | SQLite rolls back the open transaction | resumes at the next missing hour (S2) |
| Crash mid full refresh | staging tables are dropped on start | the previous `item_stats` and plan stay live; the refresh re-runs |
| One item fails in the refresh (bad data, engine error) | that item is excluded and listed on Status with its error | the rest of the refresh completes |
| Disk full | writes stop; nothing is half-written | Status error; it resumes when space is free |
| Database corrupted | `PRAGMA quick_check` fails on start | restore from the newest of 3 daily backups (SQLite backup API, `data/backups/`); history since then is re-fetched where AHF still has it |
| SavedVariables half-written or unparsable | not ingested; the file has to be unchanged for 2 s first (§2.2) | falls back to WoW's `AHProfit.lua.bak`; if that fails too, it waits for the next write. Nothing is pruned, because `ingestedUntil` doesn't move |
| SavedVariables from a newer addon version | ignored, with a message | "Update AHSync" on Status |
| An addon file write blocked (file locked, antivirus) | retried 3 times over 10 s | the old file stays; Status warns |
| AHSync started twice | the second instance finds `data/ahsync.lock` held and exits with a message | — |
| A dashboard client stalls mid-request | requests run on their own daemon threads with a 10 s timeout, so others are answered and exit never waits for it | — (FM28) |
| A stored dashboard page can't be decoded | that request answers `503 bad_view` and is logged | the next full refresh replaces it (FM29) |
| Bad `config.toml` | refuses to start with the line and the reason | — |

**Addon failures (in game):**

| Failure | What happens | Recovery |
|---|---|---|
| `MarketIntelligence.lua` missing | empty state "Run AHSync, then /reload" | — |
| `PortfolioPlan.lua` missing or old | the addon's own recommendations still work; plan rows and cross-character advice are hidden | the next AHSync run writes it |
| An addon file from another schema version, or its check fails | treated as missing, plus "AHSync and AHProfit versions differ" | update either side |
| Plan older than your last session (`PortfolioPlan.lua` `basedOnUntil` < your newest event) | the plan is shown as **provisional**: actions already matched by your newer events are marked done locally (`Plan.DoneLocally`, FM18: events after the character's `ingestedUntil`; a confirmed purchase takes its units off the earliest buy of that item, a confirmed post its stack off the earliest post, a craft one craft off the earliest craft of its product; an action with nothing left goes; Today's and Week's footers say "provisional: N done in game since AHSync's plan") | the next AHSync run replaces it |
| Lua error in the engine or UI | caught with `pcall` at the UI boundary; shown as a status line | that list shows its last good state |
| Full scan interrupted (AH closed, disconnect, combat, reload) | saved as `partial` with its coverage %. Partial books are **never** used for volume diffs, which need complete books | the next full scan starts fresh |
| Search queue interrupted (AH closed) | the queue is dropped | rebuilt on the next AH open |
| A buy or post hook fires but the action fails (outbid, sold out, not enough gold, error) | recorded as an **attempt**. It counts only when confirmed: by the "won" message or mail for a buy, by the own-auctions list for a post. Unconfirmed attempts expire after 1 h | not matched, no cost basis change |
| WoW crashes before SavedVariables is written | that session's events are lost | at the next login, the inventory snapshot shows the true state; differences are recorded as "untracked changes" (cost 0, flagged), so nothing is invented. **Nothing asks you to act**: no prompt and no forced `/reload`. Recommendations carry on from the snapshot, with untracked items valued at their opportunity cost like found items (§2.2), so the next deal is found as usual |
| SavedVariables grows past its cap | the oldest un-ingested full scans are dropped first, never events | status warning: "Run AHSync to sync" |
| Addon upgraded with a new SavedVariables layout | migrated forward on load by version, step by step; the original is kept until the migration is saved | a failed migration leaves the old data untouched and shows an error |

**Tests:**
- Every row in the two tables above has a scenario (FM1–FM24, plus UI4 for a missing `MarketIntelligence.lua` and A5 for the Lua error boundary; see [SCENARIOS.md](SCENARIOS.md)), run against `FakeTransport`, a fake filesystem and `wow_stubs.lua`. Fault injection is deterministic: a fake fails "on the 3rd write" or "after 40% of the file", never at random.
- An invariant (FI1, §8.7) checks that a crash at any commit point, followed by a restart, gives the same final database and addon files as an uninterrupted run.

**Addon performance tests:**
- Lua tests count work, not time, so they are deterministic:
  - a price change must touch only its own item's row;
  - a hidden window must not redraw;
  - tooltips must hit the decode cache after the first lookup;
  - no `OnUpdate` script may be set (checked by `wow_stubs.lua`).
- `/ahp perf` reports the real numbers in game against the budgets.

## 6. To verify

1. ~~**API key tier**~~: done 2026-10-16 (§2.1). The key has Premium features through the contributor role, which expires unless scans are uploaded. The `books`, candle, snapshot and items formats were recorded on 2026-10-16 (§2.1 Response formats). Still to check: the `volume` row format, because the file was empty for our market. Re-run `tools/record_fixtures.py` once it has data.
2. **Forever game APIs.** Verified 2026-10-16 on client 1.60.1 70425 through the addon's diagnostics: **Forever uses the modern (retail-style) API, not Classic's.**
   - Present: `GetProfessions`/`GetProfessionInfo`, `C_TradeSkillUI`, `C_MerchantFrame`, `C_Container`, the `GetInbox*` mail functions, `C_AuctionHouse`.
   - Missing: `GetNumSkillLines`/`GetSkillLineInfo` (there is no Classic Skills tab), `GetTradeSkill*`, `GetCraft*`, `GetMerchantItemInfo`, the legacy `GetContainerItemInfo`, `QueryAuctionItems`/`CanSendAuctionQuery`/`GetAuctionItemInfo`. The event `TRADE_SKILL_UPDATE` is unknown.
   - Bags and bank capture work through `C_Container`. Professions and the merchant now use the modern calls (`Adapter.lua` picks them when present).
   - `C_TradeSkillUI` is the Dragonflight-era shape (`GetAllRecipeIDs`, `GetRecipeInfo`, `GetRecipeSchematic`, `GetBaseProfessionInfo`, `IsTradeSkillLinked`, …). Recipe ids are spell ids. The window lists unlearned recipes too, so only `learned` ones are marked known; linked, guild and NPC windows are not recorded. There is no Classic craft window (`CRAFT_SHOW` is unknown); Enchanting uses the trade skill UI.
   - `C_AuctionHouse` is the modern API, including `ReplicateItems`, `GetReplicateItemInfo`, `GetReplicateItemTimeLeft` and `GetTimeLeftBandInfo`, so the full scan of §4.2 is possible. M6 is written against it.
   - The recipe reader was checked in game on 2026-10-16: Cooking 132 and First Aid 32 recipes defined, the 6 learned ones marked known, a Herbalism window (3 Forever recipes, none learned) defined only, no errors.
   - Still to check: the tooltip hook (M5), and the AH scan and the post, buy and mail hooks (M6, M6b).
3. **Disenchant tables and skill brackets** on Forever.
4. **AH fees and auction durations**: deposit rate per duration and the 5% / 15% cut. The durations are verified: 2, 8 and 24 h (2026-10-18).
5. **Time-left bands**: the bounds of short / medium / long / very long (§3.3.1).
6. **AH search limits**: whether searches are throttled, and whether each one needs a hardware click (§4.0).
7. **Recipe cooldown API** for transmutes and similar recipes.
8. **Tracking hooks**: the post and buy functions to hook, the sold / expired / won system messages, the mail invoice API, and the send-mail hook. Also check whether mail between your own characters arrives instantly or after a delay (it changes how mail steps are spaced in a chain).
9. **wow-ui-sim spike** (§8.4, layer 3): can it load a Classic-style addon (`## Interface: 16001`) and render AHProfit's frames headless through Docker on Windows? It targets the retail client, and Classic support is not documented.
10. **Folder name after launch.**
11. ~~**Item key from a link**~~: done 2026-10-16.
   - Forever's links use the modern format: `item:id:enchant:gem1:gem2:gem3:gem4:suffixID:unique:level:spec:modifiersMask:context:numBonusIDs:bonusID…`. A random suffix is a **bonus ID**; the classic suffix field is empty.
   - AHF's `/items.bonusLists` maps a bonus ID to `[suffix id, stats]`, and AHF's key uses **`v = 2 × suffix id`** (true for all 523 variants in `/items`).
   - Checked on a real link: Aboriginal Gloves of Frost Protection carries bonus 12742 → suffix 14322 → v = 28644 → key 61512521627429, which is AHF's variant of that name.
   - The addon computes it in `Adapter.ItemKey` from `Data/BonusSuffixes.lua`, generated by `tools/gen_bonus_suffixes.py` (re-run it when AHF adds suffixes).

## 7. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M1 | AHSync sync: catch-up, SQLite, 90-day prune, works without a key | Kill it mid-sync, restart, and it resumes; data accumulates |
| M2 | Addon capture (characters, professions, recipes, inventory) + SavedVariables reader | The app lists your characters and recipes |
| M3 | Engine A+B + dashboard (Disenchant, Craft) | Ranked tables at localhost |
| M4 | Engine C: weekly plan + inventory sell plan | Calendar with expected earnings |
| M5 | `MarketIntelligence.lua` + `PortfolioPlan.lua` export, the addon's own recommendations (§4.0) + in-game window, tooltip, AH search + Lua port of the engine (§4.0) + visual tests (§8.4) | Same numbers in game as on the dashboard; lists re-rank live at the AH; UI1–UI11 pass layers 1–2 and match the mockup |
| M6 | In-game AH scanning (targeted + full, order books) as a `local` source + own-sales mail capture | Own scans feed prices and sales-volume estimates |
| M6b | Tracking: post, purchase and craft capture, action matching, cost basis, own sell record, blacklist (§3.3.3, §3.4.1–2) | A buy marks its action done with no typing; 3 expiries lower `P`, cut the quantity and offer Blacklist |
| M7 | Polish: `.exe`, auto-launch WoW | |

## 8. Testing

### 8.1 Principles

- **Every core rule has a named scenario.** Each scenario reads Given / When / Then in plain numbers a person can check by hand. The scenario name is the test name, and it starts with the scenario's ID (for example `P12`).
- **One list of scenarios: [SCENARIOS.md](SCENARIOS.md).** `tests/test_conventions.py` keeps it and the tests in step. It fails on a listed ID with no test, a test ID that isn't listed, or a duplicate ID.
  - **Not built yet:** IDs whose feature isn't built yet go in `tests/pending_scenarios.txt`, so the suite stays green while the project grows. The test also fails when a pending ID already has a test, so the list only shrinks, and when it names an ID that isn't listed.
  - **What counts as a test:** a Python test named `test_<ID>_…`, a Lua `it("<ID> …", …)` in a `*_spec.lua`, or an `id` in `tests/scenarios/*.json`.
- **Real data as the template, mocked scenarios on top.** `tools/record_fixtures.py` records real AHF responses (`/me`, `/files`, snapshot, h1, d1, `/items`) and trims them to a small set of real items into `tests/fixtures/ahf/`. Builders load that template and apply the changes a scenario needs ("Copper Bar sells 40/day", "no run for 10 days", "AH listing undercut to 45s").
- **No network, no real clock, no WoW in tests.** HTTP goes through an injected transport (`FakeTransport` serves fixture files and can return 429/404/503). Time comes from an injected `Clock` (`FakeClock`). Lua tests run outside the game against `wow_stubs.lua`.
- **One source of truth for the formulas.** Engine scenarios live in `tests/scenarios/*.json`. Python reads them directly. `tools/gen_lua_fixtures.py` converts them to Lua so the Lua engine port is checked against **the same expected numbers**.
- **Deterministic and fully covered.** Every number on every tab is produced by a tested pure function (§8.5). The engine is deterministic (§8.6), its formulas pass invariant tests (§8.7), and the sell probability is checked against real outcomes (§8.8).
- **Runners**: `pytest` and `coverage.py` (dev-only dependencies; the app itself stays standard-library only) and a small Lua 5.1 runner (`tests/support/lua/runner.lua`, run with a standalone `lua5.1`). `tools/test.ps1` runs both.

### 8.2 Scenario format

```json
{
  "name": "volume: undercut repost counts as one sale plus one new listing",
  "template": "pvp_horde_small",
  "given": { "books": [ {"at": "T0", "entries": [["Linen Cloth", "50c", 20, "long", 1]]},
                        {"at": "T0+2h", "entries": [["Linen Cloth", "45c", 20, "long", 1]]} ] },
  "when":  "estimate_volume",
  "then":  { "Linen Cloth": {"sold": 20, "expired": 0, "listed": 20} },
  "why":   "Known limitation: price changed, so the 50c auction looks gone and the 45c one looks new."
}
```

Builders accept item names, `"1g 20s 5c"` prices and `T0+2h` times, and convert them to keys, copper and ms. A failed test prints the scenario's `why`.

### 8.3 Required scenarios (minimum set)

The minimum set lives in **[SCENARIOS.md](SCENARIOS.md)**, grouped by module, with IDs V*, S*, FM*, SEC*, P*, W*, R*, O*, D*, C*, M*, IU*, CF*, T*, A* and UI*, TT*, K*, plus the invariants PI*, VI*, CI*, PL*, VW*, LP* and FI* (§8.7).
Spec sections refer to scenarios by ID. A convention test keeps that file and the tests in step (§8.1).

### 8.4 Visual tests (addon UI)

No tool renders a Classic-era addon exactly like the game outside it, so there are four layers. Each one catches what the layer before it can't.

1. **View-model tests** (plain Lua, always run). These check what each tab shows: which rows, in what order, with which labels, colours and formatted money. Most UI bugs are caught here.
2. **Layout snapshots** (our Lua runner, always run).
   - `wow_stubs.lua` frames record their size, anchors, text, font and visibility. `layout.lua` resolves the anchors into rectangles on a 1920 × 1080 screen at UI scale 1. Text width is estimated from the character count × the font's average glyph width.
   - Each scenario writes a **frame-tree snapshot** (indented text: name, rectangle, text, shown). It is compared with `tests/visual/golden/<scenario>.txt`.
   - **Automatic checks:** every shown frame lies inside its parent; text never overflows its box unless it is set to truncate; sibling rows don't overlap; buttons are at least 20 px tall.
   - `python tools/visual.py --update` rewrites the golden files. Review the diff like code.
   - `python tools/visual.py --html` draws the snapshot rectangles and text to `tests/visual/out/<scenario>.html`, for a quick look next to the mockup.
3. **Rendered screenshots** (optional, through wow-ui-sim in Docker). The same scenarios are rendered to PNG and compared with `tests/visual/golden/*.png` within a pixel tolerance. Pillow is a dev-only dependency for the comparison. This depends on the §6 spike. If wow-ui-sim can't load a Classic-style addon, this layer is dropped, and layer 2's HTML view and layer 4 cover it.
4. **In game** (manual, once per milestone): `/ahp demo`, then screenshot each tab and compare with the mockup.

Scenarios UI1–UI11: [SCENARIOS.md §2](SCENARIOS.md#2-visual-tests-ui1ui11).

### 8.5 Every number on every tab is tested

Each number a tab shows comes from a pure function in `ahsync/engine/`. The dashboard's `web/` and the addon's frame code only display it, and do no arithmetic of their own. Tab totals, sorting, filters and tooltip deltas live in `engine/views.py`, with the Lua twin in `UI/ViewModel.lua`.

| Tab / view | Engine function(s) | Scenarios |
|---|---|---|
| Today | `plan.today`, `views.today` (sort, filter, footer totals) | D1–D5, R8, O1–O5, D16, D19–D22 |
| Week | `plan.week`, `plan.replan_missed`, `plan.status_pass`, `views.week` (day, category and character totals) | R6, R9, R14, D9–D14, C7–C8, C14, D17, D19–D22, M1–M7, T14–T15 |
| On the AH | `tracking.cost_basis`, `views.on_ah` (tiles) | T9–T13, D18 |
| Disenchant | `disenchant.targets` | R1, R2 |
| Craft | `craft.options` (sourcing, chains, crafts count, hold value) | R3–R5, C1–C16, D6–D8 |
| Flip | `plan.flips` | R8, R13, D3, D5 |
| Inventory | `inventory_view.inventory_view` (worth, plan cell, tiles), `item_detail.item_detail` (heatmaps, windows, the three answers), `tracking.cost_basis` | R7, R10, T7, T12, D25–D31, W9 |
| History | `tracking.match` | T1–T8, T14–T16 |
| Rules | `record.sell_record`, `rules.apply` | O1–O18 |
| Tooltip | `views.tooltip` | TT1–TT5 |
| Status | `status_view.status_view` (alerts, cards), `sync.*`, `record.calibration` | S1–S10, D32–D36, K1–K3 |
| Weekly patterns, best windows | `patterns.sell_windows`, `patterns.weighted_median` | W1–W7 |
| Confidence % | `confidence.score` | CF1–CF4 |
| Refresh and incremental updates | `refresh.refresh_all`, `refresh.update_items`, `plan.status_pass`, `sellprob.from_curve` | IU1–IU11 |
| Failure handling | `sync.*`, `ingest.*`, `export.*`, `refresh.*`, addon `Core/*` | FM1–FM24, FI1–FI2 |
| Dashboard security (not engine; `web/server`) | request checks | SEC1–SEC7 |
| Sell probability, expected value | `sellprob.simulate`, `sellprob.blend`, `sellprob.expected` | P5–P9, P12–P24, PI1–PI17, K1–K3 |
| Volume | `volume.*` (incl. the per-day source choice and calibration) | V1–V18, S11, the invariants below |

`tests/test_conventions.py` fails if a view field has no scenario that checks it. Branch coverage of `ahsync/engine/` must be 100% (`coverage.py`, dev-only).

The view and tooltip scenarios (D16–D18, TT1–TT5) are in [SCENARIOS.md §3](SCENARIOS.md#3-views-and-tooltip-d16d22-tt1tt5).

### 8.6 Determinism

The engine must give **the same output for the same input, every time and on every machine**, in both languages.

- **No hidden inputs.** The current time is passed in as `now_ms`. There is no randomness. Config values are arguments.
- **Explicit order.** Every sort has a full tie-break that ends in a unique key, such as an item key or a character id. Nothing depends on dict, set or Lua `pairs` order. Lua code walks sorted key lists.
- **Exact numbers.** The numeric rules in §3.3 apply: integer copper, floats only for expected values, `floor(x + 0.5)` at output, and the defined median and percentile. Tests compare rounded output exactly, not "close enough".
- **Whole-plan snapshot.** A fixture world, `tests/fixtures/worlds/w1.json`, holds characters, inventory, recipes, prices, order books, history and `now_ms`. It is built from the mockups' example data. The test runs the whole engine on it and compares every tab's view model with `tests/fixtures/worlds/w1.expected.json`. Running it twice must give byte-identical output. Where the mockups and the engine disagree, the engine's tested numbers win and the mockup is updated.

### 8.7 Invariants: is the logic sound?

Single scenarios check one example. Invariants check that a formula behaves sensibly across many inputs.

- **How cases are made.** `tests/support/gen.py` makes cases from a fixed seed with `random.Random(seed)`. A failure prints the seed and the inputs, so it can be reproduced. The generated cases are saved to `tests/properties/*.json`, so the Lua port is checked against exactly the same cases.
- **Scale.** At least 1,000 cases per invariant.

The invariants (PI1–PI17, VI1, CI1–CI3, PL1–PL3, VW1, LP1, FI1–FI2) are listed in [SCENARIOS.md §5](SCENARIOS.md#5-invariants-spec-87), with the other test IDs, so the convention test (§8.1) sees one list.

### 8.8 Calibration: does the chance to sell match reality?

**Volume backtest.** Where both exist for the same item and day, book-estimated sales are compared with AHF volume and with your mail-confirmed sales: the Status page reports the median ratio and the share of days within ±25%, per source, over the last 30 days (K4). A ratio far from 1 shows up before it misleads the plan. **Built** (K4): every estimated source (`books`) is compared with every confirmed source in `volume_d1` (AHF today; any later one, such as a Blizzard API job, without changes), by `engine.volume.backtest`, on each market's Status card.

The invariants prove the formula is sensible. Only your real sales can show whether it is *right*. Every post therefore records the `P'` it was planned with (`posts.p_at_post`). `record.calibration` compares that with what actually happened:

- It groups resolved posts into buckets by predicted chance: `[0, 0.2)`, `[0.2, 0.4)`, `[0.4, 0.6)`, `[0.6, 0.8)` and `[0.8, 1]`. Each bucket includes its lower edge, and the last one also includes 1. It shows each bucket's predicted versus actual sell rate.
- It reports the **Brier score**, the mean of `(P' − outcome)²` where outcome is 1 for sold and 0 for expired. Lower is better. Always guessing 50% scores 0.25.
- The **Status** page shows both. A bucket that is off by more than 15 points, with at least 20 posts in it, raises a warning, e.g. "40–60% predictions actually sold 28%". That suggests `default_sell_prob`, `own_record_weight` or the volume source needs adjusting.

**Built** (K1–K3, K5, K6): `engine/sell_record.calibration` over the last 90 days of sold and expired posts with a `p_at_post`, which the tracking pass copies from the matched action's planned `P'` (`actions.conf`); a post that matched no action, such as one the addon suggested on its own, is left out. The Status page shows it below the cards (`engine/status_view.calibration_view`), with the bucket warnings as alerts in that section.

Scenarios K1–K4: [SCENARIOS.md §4](SCENARIOS.md#4-calibration-k1k3).

### 8.9 Definition of done for any feature

The scenarios for that feature are written first, they are listed in [SCENARIOS.md](SCENARIOS.md), and they pass in both languages where the feature exists in both.

## 9. Out of scope (v1)

Automated buying or posting (not allowed by Blizzard), cross-market arbitrage, planning for the neutral AH (AHF has no market for it), and uploading scans to AHF (the AHF addon already does that).
