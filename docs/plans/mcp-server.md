# Plan: ask an AI assistant about your AHSync data (MCP server, MC0–MC3)

Written for: the user, who approves it, and the Opus session that runs the batch. **Status: draft for review, 2026-11-08. Nothing here is approved yet.** Tasks are tagged **Opus** (judgement) or **Sonnet** (routine), each with a one-line reason.

**What this batch is.** A local, read-only [MCP](https://modelcontextprotocol.io) server that runs inside AHSync, started and stopped by a button on the Status page. An assistant that speaks MCP (Claude Desktop, Claude Code) connects to it on your computer and uses it to read your data. The assistant does the thinking: it ranks, compares, spots trends and looks things up on the web. The server gives it complete, current, well-described data. It never changes anything, and it listens only on your own computer (127.0.0.1), only while you have it enabled.

## 0. The questions it must answer (the user, 2026-11-08)

The principle (the user): **give the assistant the data, don't pre-chew the answers.** No new engine views or ranking fields are built for it.

| Question | What the assistant does | What the server must give it |
|---|---|---|
| "What fish are the most profitable to get right now?", for my characters and their skill | Finds every fish the market has prices for and ranks them by price, sales and supply. It checks each fish's Fishing requirement on Wowhead, then keeps the ones the character can catch at their Fishing skill. | Every item with its current market numbers, filterable by name, class and subclass: `market_stats` in SQL (§3). Your characters and their profession skills: `characters` and `professions`. |
| "Find me recent trends in item prices I can take advantage of" | Compares current prices with the 7 and 30 day medians and the trend, checks hourly history where it needs more, and weighs sales per day so thin items don't mislead. | The same `market_stats` rows, plus the hourly and daily price, supply and sales history in SQL. The Flip page (`get_page`) adds the plan's own buy-now, sell-at-the-peak trades. |
| "Where are all the places to farm this item?" | Looks the item up on Wowhead or anywhere else it knows, with its own web tools. | The item's id, so the lookup is exact. Nothing else: the Gathering page's catalog can't track sources comprehensively, and the assistant already knows where to look. |

When it starts (MCP's `initialize` result), the server gives the assistant **context, not instructions**: only facts it can't know.
- The game is WoW: Forever, a Classic-era realm.
- Item ids are Classic's. A random-suffix item's key is its item id + 2147483648 × the suffix (SPEC §2.1).
- Money is integer copper, and times are UTC Unix milliseconds.
- The numbers come from the last refresh; `list_markets` says when that was.

How the assistant answers is left to it.

## 1. Proposed decisions (for the user to approve or change)

1. **Read-only.** No tool skips an action, sets a rule, changes a setting or starts a refresh. Writes keep going through the dashboard's token-checked POSTs (SPEC §3.5 Security). Letting an assistant act on your plan needs its own design, later.
2. **The server runs inside AHSync; Claude reaches it through a bridge** (the user's option B, 2026-11-08). Pressing **Enable** starts it inside the app, on its own local port. The port is `127.0.0.1` only (config `mcp_port`, default 8766, separate from the dashboard's) and exists only while the server runs.
   - Claude Desktop and Claude Code both launch a small bridge that ships with AHSync, `ahsync-mcp`. It speaks MCP to Claude over stdin and stdout, forwards each message to the app's local port, and passes back the answer.
   - The bridge reads the server's access key from AHSync's own data folder, so no key appears in Claude's config or on screen.
   - The app's port accepts only requests that carry the key, a `Host` of `127.0.0.1`/`localhost`, and no browser `Origin`. That makes it as safe as the dashboard against a website probing it (SEC scenarios).
   - The server never goes online itself.
3. **Claude first; ChatGPT not in v1.** Claude Desktop and Claude Code run local stdio servers directly. ChatGPT accepts only remote HTTPS MCP servers, in Developer Mode, so your data would have to go through a tunnel or hosted relay, and plan eligibility is unclear. That needs its own decision and security review.
4. **Data first; the engine's answers alongside.** SQL over your data, with the current market numbers as a table (`market_stats`), is the main tool. The dashboard's pages are there too (`get_page`, `item`). When the assistant uses them, its numbers match the dashboard's exactly.
5. **Standard library only.** AHSync's core takes no third-party packages (CLAUDE.md), and the MCP subset needed is small: JSON-RPC 2.0, one message per line, with `initialize`, `tools/list`, `tools/call` and `ping`. A new edge package, `ahsync/mcp/`, holds it.
6. **It works while AHSync runs, and only when enabled.** The server reads the last published refresh through its own read-only connection to the database, on its own thread, so a slow query never holds up a refresh or the dashboard.
   - With AHSync closed, the bridge tells Claude "AHSync isn't running: start it, then ask again".
   - With access disabled, the bridge tells Claude where to turn it on.
7. **An Enable / Disable button on the Status page, and pressing Enable is your agreement to the memory it uses** (the user, 2026-11-08). The button is off by default.
   - **Enable** starts the server and builds `market_stats` in AHSync's memory; the card then shows the server running and how much memory it holds.
   - **Disable** stops the server, closes its port and frees that memory at once. Claude's next request gets "AI assistant access is off: turn it on in AHSync's Status page".
   - The choice is remembered: an enabled server starts again with AHSync, and a disabled one stays off.
   - Both presses go through the dashboard's token-checked POST, with their own SEC scenarios.
   - The same card shows the Claude setup and the privacy note.
8. **Nothing is off limits** (the user, 2026-11-08). The access is read-only, so the assistant may read everything the allowlist in §4 names.
9. **ChatGPT is a follow-up batch** (the user, 2026-11-08). This batch is Claude only.

## 2. Working rules

- Scenarios first: SCENARIOS.md gets a new `MCP` family plus SEC rows for the SQL tool. Their IDs go into `tests/pending_scenarios.txt` at the spec merge.
- Every response is bounded (rows and bytes). Every response is valid JSON-RPC, even when the database is missing, locked, or from an older schema.
- `/security-review` and an independent review run before MC2 merges: queries written by a model reach the database, a trust boundary.
- ARCHITECTURE.md gains the component and its data flow at the spec merge; the milestones mark it built.
- Nothing the app shows changes: the whole-plan snapshot and every golden stay byte-identical.

## 3. The tools

| Tool | Input | Output | Notes |
|---|---|---|---|
| `list_markets` | none | each market: name, newest refresh time, its price sources, item count | Freshness for every answer. |
| `describe_data` | none | every table the SQL tool can read: columns, units (copper, UTC ms, hours), what a row means, and example queries | The assistant's map of the data, so it writes correct SQL. |
| `query` | `sql` (one `SELECT` or `WITH … SELECT`) | columns and rows; at most 500 rows and 256 KB, with a note when cut | The main tool. See §4. |
| `get_page` | `page` (portfolio, today, week, on_ah, disenchant, craft, flip, gathering, inventory, history, characters, status), `market`, optional `character`, `sort` (one of the page's sort keys), `limit` (default 25, at most 200), `search` | the page's summary fields, its column help texts, and the top `limit` rows | The dashboard's answers, from the `views` table. The full Disenchant page is 438 KB, hence the limit. |
| `item` | `key` or `name`, `market` | the dashboard's item detail: market lines, Disenchant Value, best windows, selling answers, activity; heatmaps only when asked | The engine's `item_detail` from the published intelligence, a lookup with no history read (SPEC §3.1.2). To verify in MC1. |

**`market_stats`, the one new piece of data plumbing.** Each market's current numbers live in the database as one JSON document per market (the `intel` table). SQL can't filter or rank inside it, and the cloud market's has about 16,000 items, too many to return whole. The server flattens it into a read-only table, one row per market and item, with:
- the item: name, quality, item level, class, subclass;
- prices: current price and its age, the 7-day and 30-day medians, the 30-day 10th and 90th percentiles;
- the 7-day trend in % per day;
- sales, expiries and new listings per day, with their source;
- units on the AH now;
- the chance to sell and the confidence;
- the vendor price.

The table is rebuilt in memory only when a newer refresh is published.

**Storage and memory.**
- **No duplicate storage:** `market_stats` lives only in AHSync's memory, only while the server is enabled. Nothing is written to the database or to disk.
- **Rebuilt after each refresh:** the table is rebuilt when a refresh publishes, swapped in whole, with the old one freed.
- **Disabled means none:** with the server disabled, AHSync holds none of it.
- **Measured on the user's database (2026-11-08):**
  - the market's published intelligence is 1.2 MB compressed;
  - reading it takes about 0.4 s and peaks around 42 MB while parsing;
  - the flattened table keeps only the fields above, so it holds a fraction of that.
- MC2 measures the app's memory with the server enabled against disabled, both at rest and across a refresh, and adds the difference to `tools/bench.py` as a budget. The card shows the figure, so the agreement you give is to a known amount. With it, "cheap against its 30-day median with at least 2 sales a day" or "the fish my character can sell" is one query. No new engine code is needed: the fields are the published intelligence, as built.

## 4. Security (SQL tool)

- **Read-only three ways:**
  - the database is opened with `mode=ro`;
  - `PRAGMA query_only` is set;
  - a `sqlite3` authorizer allows only reads of allowlisted tables and functions, and denies everything else (`ATTACH`, `PRAGMA`, writes, `load_extension`).
- **Allowlist:** `market_stats`, the history tables (hourly and daily prices, volume, sales, scans), your trades (posts, sales, payouts, purchases, crafts, mail, disenchants), gathering, items, characters, professions, recipes and inventory. Not `meta`, `sync_state`, `fetch_failures`, `status` or the staging tables.
- **Bounded:**
  - a progress handler stops a query after 2 s;
  - rows and bytes are capped;
  - one statement per call.
- **No secrets:** the API key and Blizzard credentials live in the environment and `~/.ahsync`, never in the database. MC2 adds a test that no allowlisted column can hold one.
- **Privacy:** whatever the assistant reads goes to its provider, including your characters, gold and trade history. The setup text says so plainly before you connect.
- **Prompt injection:** item names come from outside sources and are data. Every tool is read-only, so text in the data can't make the assistant change anything in AHSync.

## 5. Connecting an assistant

- **Claude Desktop:** an entry under `mcpServers` in its config file, with `command` set to the AHSync MCP executable. **Claude Code:** `claude mcp add`. AHSync shows the exact snippet with your install's path, ready to copy.
- **Web search:** the assistant needs web access for the Wowhead cross-checks. That's Claude Desktop's web search setting, or Claude Code's own web tools.
- **The bridge:** `ahsync-mcp.exe`, a small console program next to the app in the Windows installer. The app itself is windowed, with no console, so it can't talk over stdin and stdout. On macOS the bridge is the app's binary started with `--mcp`, which needs checking in MC3.
- **Bridge behaviour:** the bridge is a thin relay with no data of its own. It starts in milliseconds and exits when Claude closes it.
- **ChatGPT (not in v1):** only remote HTTPS MCP servers, added in Developer Mode. For a local server OpenAI offers a separate tunnel client, or a tunnel such as ngrok works. Sources differ on which plans may add custom servers, so check OpenAI's current help article before planning.

## 6. Milestones

### MC0: the spec merge (Opus: the contract and the security rules)
- Merge §1, §3 and §4 into SPEC (a new §3.x, plus §5's layout).
- Add the MCP and SEC scenarios and list them in `pending_scenarios.txt`.
- Draw the component in ARCHITECTURE.md.

### MC1: the server, the bridge and the dashboard's answers (Opus: the protocol, the threading and the port's protections)
- `ahsync/mcp/` holds the JSON-RPC handling, the `initialize` context (§0), `list_markets`, `get_page` and `item`.
- The app's local endpoint lives on its own thread, started and stopped by the app, and checks the key, `Host` and `Origin`.
- The bridge relays between stdin and stdout and the endpoint; run it from source with `python -m ahsync.mcp`.
- Tests:
  - the bridge with a fake endpoint, and the endpoint with fake requests;
  - `initialize`, `tools/list`, each tool and bad input;
  - a missing database, and an empty one (no refresh yet);
  - AHSync not running, and access disabled;
  - SEC: a missing or wrong key, a foreign `Host`, a browser `Origin`.

### MC2: the data tools (Opus: a trust boundary)
- `query` with the authorizer, the allowlist, the limits and the timeout.
- `market_stats`, flattened from the published intelligence and rebuilt only on a newer refresh.
- `describe_data`, with each table's units and example queries. Sonnet writes the table descriptions once the allowlist is fixed: routine work.
- SEC scenarios: a write, `ATTACH`, `PRAGMA`, a denied table, two statements, a runaway query, a huge result.
- `/security-review` and the independent review before merge.

### MC3: packaging and setup (Opus: the build and the setting's write path; Sonnet: the copy text)
- Add the console helper on Windows and `--mcp` on macOS, in `tools/build.py` and CI.
- The Status page gets an "AI assistant (Claude)" card (mockup `docs/mockups/ai-assistant.html`, to be approved first):
  - the Enable / Disable button (default off, `POST` with token, SEC scenarios);
  - the server's state: off, starting, running (with its port and memory), or failed (for example, the port in use);
  - the copyable Claude Desktop and Claude Code setup, and the privacy note.
- An enabled server starts again with the app; a disabled one stays off (MCP scenarios).
- Manual check: Claude Desktop answers the §0 questions from a real database.

## 7. Open questions for the user

None left. The user answered on 2026-11-08:
- the card goes on the Status page;
- ChatGPT is a follow-up batch;
- nothing is off limits while the access is read-only;
- `market_stats` lives in memory only;
- the server runs inside AHSync, started and stopped by an Enable / Disable button whose press is the agreement to its memory (decisions 2, 6, 7).

## 8. Acceptance (the whole batch)

- With AHSync running and the server enabled, Claude Desktop, configured with the copied snippet and with web search on, answers the user's three questions:
  - the most profitable fish right now that a given character can catch, with Fishing requirements checked on Wowhead;
  - recent price trends to take advantage of, from `market_stats` and the history;
  - where to farm a named item.
- Each answer says which refresh its numbers come from.
- With the server disabled, Claude is told where to enable it; with AHSync closed, Claude is told to start it. Disable frees the memory, measured against the budget.
- No tool can change the database (the SEC scenarios pass), no response exceeds its bounds, and the port is open only on 127.0.0.1, only while enabled, and only to requests with the key.
- `tools/verify.ps1` passes. The whole-plan snapshot and every golden are unchanged.
