-- AHSync database, schema version 1 (SPEC §3.2). Applied by store/db.py on start.
-- Money: integer copper. Time: UTC Unix ms unless the column says hour/day.
-- Every statement is idempotent (IF NOT EXISTS), so re-applying is harmless.

CREATE TABLE IF NOT EXISTS meta(
    name  TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS items(
    key INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, name TEXT, quality INTEGER, ilvl INTEGER,
    class INTEGER, subclass INTEGER, vendor_buy INTEGER, vendor_sell INTEGER, stack INTEGER, updated INTEGER
);

-- src: 'ahf' | 'local'. Prices may be NULL (nothing listed), never 0 for "none".
CREATE TABLE IF NOT EXISTS candles_h1(
    market TEXT NOT NULL, key INTEGER NOT NULL, t INTEGER NOT NULL,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER, qty INTEGER, src TEXT NOT NULL,
    PRIMARY KEY(market, key, t, src)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS candles_h1_market_t ON candles_h1(market, t);

CREATE TABLE IF NOT EXISTS candles_d1(
    market TEXT NOT NULL, key INTEGER NOT NULL, t INTEGER NOT NULL,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER, qty INTEGER, src TEXT NOT NULL,
    PRIMARY KEY(market, key, t, src)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS candles_d1_market_t ON candles_d1(market, t);

-- src: 'ahf' | 'books' | 'mail' | 'qty_drop'; conf: 'high' | 'medium' | 'low'. day = floor(ms/86400000).
CREATE TABLE IF NOT EXISTS volume_d1(
    market TEXT NOT NULL, key INTEGER NOT NULL, day INTEGER NOT NULL,
    listed REAL, sold REAL, expired REAL, src TEXT NOT NULL, conf TEXT,
    PRIMARY KEY(market, key, day, src)
);

-- Book-pair sales rate per item and hour start t (SPEC §3.3 sales_how), recomputed whole per hour at ingest.
CREATE TABLE IF NOT EXISTS sales_h1(
    market TEXT NOT NULL, key INTEGER NOT NULL, t INTEGER NOT NULL, sold_per_hour REAL NOT NULL,
    PRIMARY KEY(market, key, t)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS sales_h1_market_t ON sales_h1(market, t);

CREATE TABLE IF NOT EXISTS calibration(
    market TEXT NOT NULL, key INTEGER NOT NULL, source TEXT NOT NULL,   -- source: 'ahf' | 'mail'
    factor REAL, samples INTEGER, updated INTEGER,
    PRIMARY KEY(market, key, source)
);

CREATE TABLE IF NOT EXISTS current(
    market TEXT NOT NULL, key INTEGER NOT NULL, price INTEGER, qty INTEGER, at INTEGER NOT NULL, src TEXT NOT NULL,
    PRIMARY KEY(market, key, src)
);

-- src: 'local' (your scan) | 'ahf' (Premium books); char_id NULL for ahf. kind: 'full' | 'partial'.
-- Only kind = 'full' with coverage 1 feeds volume diffs (§5.2).
CREATE TABLE IF NOT EXISTS scans(
    scan_id TEXT PRIMARY KEY, market TEXT NOT NULL, src TEXT NOT NULL, char_id TEXT,
    at INTEGER NOT NULL, kind TEXT NOT NULL, coverage REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS scans_at ON scans(at);

CREATE TABLE IF NOT EXISTS book_entries(
    scan_id TEXT NOT NULL, key INTEGER NOT NULL, price INTEGER NOT NULL, stack INTEGER NOT NULL,
    band INTEGER NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(scan_id, key, price, stack, band)
) WITHOUT ROWID;

-- Event tables carry the addon's unique event_id (charId:timeMs:seq); ingest upserts on it (§5.2).
-- SPEC GAP: §3.2 keys auction_mail, purchases and crafts on (char_id, key|spell_id, at[, outcome]), but times
-- are whole seconds, so two sold mails or two buys of one item in the same second would collide. The event id
-- is their primary key instead, with the §3.2 natural key kept as an index.
CREATE TABLE IF NOT EXISTS auction_mail(
    event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER, price INTEGER,
    outcome TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS auction_mail_char_key_at ON auction_mail(char_id, key, at, outcome);

CREATE TABLE IF NOT EXISTS posts(
    post_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, market TEXT, key INTEGER NOT NULL, stack INTEGER,
    price INTEGER, cost_each REAL, p_at_post REAL, duration_h INTEGER, deposit INTEGER, at INTEGER NOT NULL,
    outcome TEXT, resolved_at INTEGER, action_id TEXT, event_id TEXT UNIQUE, confirmed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchases(
    event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER, price INTEGER, source TEXT,
    at INTEGER NOT NULL, action_id TEXT, confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS purchases_char_key_at ON purchases(char_id, key, at);

CREATE TABLE IF NOT EXISTS crafts(
    event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, spell_id INTEGER NOT NULL, count INTEGER, cost_each REAL,
    at INTEGER NOT NULL, action_id TEXT
);
CREATE INDEX IF NOT EXISTS crafts_char_spell_at ON crafts(char_id, spell_id, at);

-- Columns per §3.2 (mail_sent), following §2.2.1.
CREATE TABLE IF NOT EXISTS mail_sent(
    event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, to_char TEXT NOT NULL, key INTEGER NOT NULL,
    qty INTEGER, at INTEGER NOT NULL, action_id TEXT
);

-- SPEC GAP: §3.2 has no table for skip events (§2.2.1). Each is kept here, so an action that doesn't exist yet
-- (or is re-planned later) can still be marked skipped when it appears; ingest marks an existing one at once.
CREATE TABLE IF NOT EXISTS skips(
    event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, action_id TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS skips_action ON skips(action_id);

CREATE TABLE IF NOT EXISTS cost_basis(
    key INTEGER PRIMARY KEY, qty INTEGER NOT NULL DEFAULT 0, total_cost INTEGER NOT NULL DEFAULT 0,
    untracked_qty INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS actions(
    action_id TEXT PRIMARY KEY, run_at INTEGER, slot_ms INTEGER, char_id TEXT, kind TEXT, key INTEGER,
    qty INTEGER, price INTEGER, expected_net REAL, conf REAL, status TEXT, done_qty INTEGER, done_at INTEGER,
    parent_id TEXT, window_start_ms INTEGER, window_end_ms INTEGER, anchor_net REAL, anchor_window_ms INTEGER,
    replaces_id TEXT, week_start_ms INTEGER, peak_ms INTEGER, source TEXT   -- source: buys, 'vendor' | 'ah' (D41)
);

-- Staging twin of actions for the atomic swap after a full refresh (§3.1.1, §5.2).
CREATE TABLE IF NOT EXISTS plan_new(
    action_id TEXT PRIMARY KEY, run_at INTEGER, slot_ms INTEGER, char_id TEXT, kind TEXT, key INTEGER,
    qty INTEGER, price INTEGER, expected_net REAL, conf REAL, status TEXT, done_qty INTEGER, done_at INTEGER,
    parent_id TEXT, window_start_ms INTEGER, window_end_ms INTEGER, anchor_net REAL, anchor_window_ms INTEGER,
    replaces_id TEXT, week_start_ms INTEGER, peak_ms INTEGER, source TEXT
);

-- rule: 'no_buy' | 'ignore' | 'keep' | 'none' (tombstone); source: 'dashboard' | 'addon'.
CREATE TABLE IF NOT EXISTS item_rules(
    key INTEGER PRIMARY KEY, rule TEXT NOT NULL, set_at INTEGER NOT NULL, source TEXT
);

-- Items a character keeps for itself (SPEC §3.4.3); qty -1 keeps every unit. Set in game only.
CREATE TABLE IF NOT EXISTS reserves(
    char_id TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY(char_id, key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS characters(
    id TEXT PRIMARY KEY, name TEXT, realm TEXT, faction TEXT, market TEXT, class TEXT, level INTEGER,
    gold INTEGER, updated INTEGER
);

CREATE TABLE IF NOT EXISTS professions(
    char_id TEXT NOT NULL, profession TEXT NOT NULL, skill INTEGER, max_skill INTEGER,
    PRIMARY KEY(char_id, profession)
);

CREATE TABLE IF NOT EXISTS recipes(
    profession TEXT NOT NULL, spell_id INTEGER NOT NULL, product_key INTEGER, yield_min INTEGER,
    yield_max INTEGER, skill_req INTEGER, cooldown_s INTEGER,
    PRIMARY KEY(profession, spell_id)
);

CREATE TABLE IF NOT EXISTS reagents(
    profession TEXT NOT NULL, spell_id INTEGER NOT NULL, key INTEGER NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY(profession, spell_id, key)
);

CREATE TABLE IF NOT EXISTS known_recipes(
    char_id TEXT NOT NULL, spell_id INTEGER NOT NULL,
    PRIMARY KEY(char_id, spell_id)
);

-- SPEC GAP: §3.2 has no table for SavedVariables' vendorReagents (keys a merchant was seen selling, §3.4B).
CREATE TABLE IF NOT EXISTS vendor_reagents(
    key INTEGER PRIMARY KEY
);

-- location: 'bags' | 'bank' | 'mail'.
CREATE TABLE IF NOT EXISTS inventory(
    char_id TEXT NOT NULL, location TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER NOT NULL, bound INTEGER NOT NULL,
    PRIMARY KEY(char_id, location, key, bound)
);

-- SPEC GAP: §3.2 keys own_auctions on (char_id, key, price, stack); band is part of the key here, because the
-- same stack at the same price can be posted twice with different time left.
CREATE TABLE IF NOT EXISTS own_auctions(
    char_id TEXT NOT NULL, key INTEGER NOT NULL, price INTEGER NOT NULL, stack INTEGER NOT NULL, band INTEGER NOT NULL,
    count INTEGER, seen INTEGER,
    PRIMARY KEY(char_id, key, price, stack, band)
);

-- SPEC GAP: §3.2 doesn't say where each snapshot's capture time lives. One row per character and place
-- ('bags' | 'bank' | 'mail' | 'auctions'), so an older SavedVariables file never overwrites a newer snapshot,
-- even an empty one (§2.2.1).
CREATE TABLE IF NOT EXISTS snapshots(
    char_id TEXT NOT NULL, location TEXT NOT NULL, at INTEGER NOT NULL,
    PRIMARY KEY(char_id, location)
);

-- last_hour = floor(ms/3600000) of the newest hour handled; last_d1/last_volume = day numbers.
CREATE TABLE IF NOT EXISTS sync_state(
    market TEXT PRIMARY KEY, last_hour INTEGER, last_d1 INTEGER, last_volume INTEGER, live_since INTEGER,
    last_run INTEGER
);

-- Market intelligence (§5.0): one versioned JSON document per market, swapped in by a full refresh.
CREATE TABLE IF NOT EXISTS intel(
    market TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, body TEXT NOT NULL, built INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS intel_new(
    market TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, body TEXT NOT NULL, built INTEGER NOT NULL
);

-- Precomputed dashboard pages (SPEC §3.5): one JSON payload per page and market, swapped in by a full refresh.
CREATE TABLE IF NOT EXISTS views(
    page TEXT NOT NULL, market TEXT NOT NULL, body TEXT NOT NULL, built INTEGER NOT NULL,
    PRIMARY KEY(page, market)
);
CREATE TABLE IF NOT EXISTS views_new(
    page TEXT NOT NULL, market TEXT NOT NULL, body TEXT NOT NULL, built INTEGER NOT NULL,
    PRIMARY KEY(page, market)
);

-- Status page lines (§3.2): one per topic (e.g. 'lost:<market>', 'format:snapshot'); level 'info' | 'warn' | 'error'.
-- kind names the message; data holds its structured fields as JSON (ms ranges, counts, hours), rendered by views.
CREATE TABLE IF NOT EXISTS status(
    topic TEXT PRIMARY KEY, level TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, at INTEGER NOT NULL
);

-- An hour whose file failed (truncated, bad JSON, 404). Retried on later runs; 3 in a row → Status "bad file".
CREATE TABLE IF NOT EXISTS fetch_failures(
    market TEXT NOT NULL, hour INTEGER NOT NULL, failures INTEGER NOT NULL, last_error TEXT, last_at INTEGER,
    PRIMARY KEY(market, hour)
);
