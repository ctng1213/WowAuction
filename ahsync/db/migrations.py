"""The database's schema version and the steps that bring an older database up to it (SPEC §3.2 "Format version")."""
from __future__ import annotations

SCHEMA_VERSION = 30
# version -> SQL that upgrades version-1 to version. A new database gets schema.sql, which is always the
# latest version; a change to schema.sql adds a step here and bumps SCHEMA_VERSION (test_sqlite checks
# that a migrated database has exactly schema.sql's tables and indexes).
MIGRATIONS: dict[int, str] = {
    # 2: market intelligence replaces item_stats (AR2); hourly book-pair sales rates (V27). item_stats only
    # held refresh output, which the next full refresh rebuilds as intelligence.
    2: """
        CREATE TABLE IF NOT EXISTS intel(
            market TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, body TEXT NOT NULL, built INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS intel_new(
            market TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, body TEXT NOT NULL, built INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sales_h1(
            market TEXT NOT NULL, key INTEGER NOT NULL, t INTEGER NOT NULL, sold_per_hour REAL NOT NULL,
            PRIMARY KEY(market, key, t)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS sales_h1_market_t ON sales_h1(market, t);
        DROP TABLE IF EXISTS item_stats;
        DROP TABLE IF EXISTS item_stats_new
    """,
    # 3: each post's peak hour, so the plan's notes and the addon can say "for the 18:00 peak" (D37, D38).
    3: """
        ALTER TABLE actions ADD COLUMN peak_ms INTEGER;
        ALTER TABLE plan_new ADD COLUMN peak_ms INTEGER
    """,
    # 4: items each character keeps for itself, read from the addon (S14, SPEC §3.4.3).
    4: """
        CREATE TABLE IF NOT EXISTS reserves(
            char_id TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY(char_id, key)
        ) WITHOUT ROWID
    """,
    # 5: where a planned buy buys, so the addon and the dashboard can say "from a vendor" (D41).
    5: """
        ALTER TABLE actions ADD COLUMN source TEXT;
        ALTER TABLE plan_new ADD COLUMN source TEXT
    """,
    # 6: which post each auction mail resolved, so a mail resolves one post only (SPEC §3.4.1, T4, T5).
    6: """
        ALTER TABLE auction_mail ADD COLUMN post_id TEXT
    """,
    # 7: gold taken from sold mail, and the profit it realized (SPEC §2.2.1 payout, T17).
    7: """
        CREATE TABLE IF NOT EXISTS payouts(
            event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER, money INTEGER,
            at INTEGER NOT NULL, mail_id TEXT, realized REAL
        )
    """,
    # 8: disenchants and what they gave (SPEC §2.2.1 disenchant, T18).
    8: """
        CREATE TABLE IF NOT EXISTS disenchants(
            event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, key INTEGER NOT NULL, results TEXT NOT NULL,
            at INTEGER NOT NULL, action_id TEXT
        )
    """,
    # 9: the deposit a sold mail's invoice reports, so a payout realizes without its post (T17).
    9: """
        ALTER TABLE payouts ADD COLUMN deposit INTEGER
    """,
    # 10: the method version of each estimated volume row, so a new method recomputes them (V24).
    10: """
        ALTER TABLE volume_d1 ADD COLUMN method INTEGER
    """,
    # 11: each planned post's auction duration, for the addon's Sell tab (EX9, A56).
    11: """
        ALTER TABLE actions ADD COLUMN duration_h INTEGER;
        ALTER TABLE plan_new ADD COLUMN duration_h INTEGER
    """,
    # 12: untracked changes, for History (D51).
    12: """
        CREATE TABLE IF NOT EXISTS untracked(
            char_id TEXT NOT NULL, key INTEGER NOT NULL, at INTEGER NOT NULL, delta INTEGER NOT NULL,
            PRIMARY KEY(char_id, key, at)
        )
    """,
    # 13: reserves carry when and where they were set, so the newest change wins both ways (S15).
    13: """
        ALTER TABLE reserves ADD COLUMN set_at INTEGER;
        ALTER TABLE reserves ADD COLUMN source TEXT
    """,
    # 15: each own-auction group's soonest end, from the snapshot's ends (S16, D53).
    15: """
        ALTER TABLE own_auctions ADD COLUMN end_ms INTEGER
    """,
    # 16: how each volume source observed a day (V29-V32); the calibration table was never written: id
    # calibration is computed from the stored rows (V16).
    16: """
        CREATE TABLE IF NOT EXISTS volume_cover(
            market TEXT NOT NULL, day INTEGER NOT NULL, src TEXT NOT NULL, kind TEXT NOT NULL, covered TEXT,
            PRIMARY KEY(market, day, src)
        ) WITHOUT ROWID;
        DROP TABLE IF EXISTS calibration
    """,
    # 17: book rows carry the scan's integer id, not its text id (V38): about a third smaller with hourly books.
    17: """
        CREATE TABLE scans_v17(
            sid INTEGER PRIMARY KEY, scan_id TEXT NOT NULL UNIQUE, market TEXT NOT NULL, src TEXT NOT NULL, char_id TEXT,
            at INTEGER NOT NULL, kind TEXT NOT NULL, coverage REAL NOT NULL DEFAULT 1.0
        );
        INSERT INTO scans_v17(scan_id, market, src, char_id, at, kind, coverage)
            SELECT scan_id, market, src, char_id, at, kind, coverage FROM scans ORDER BY at, scan_id;
        CREATE TABLE book_entries_v17(
            sid INTEGER NOT NULL, key INTEGER NOT NULL, price INTEGER NOT NULL, stack INTEGER NOT NULL,
            band INTEGER NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY(sid, key, price, stack, band)
        ) WITHOUT ROWID;
        INSERT INTO book_entries_v17(sid, key, price, stack, band, count)
            SELECT s.sid, b.key, b.price, b.stack, b.band, b.count FROM book_entries b JOIN scans_v17 s ON s.scan_id = b.scan_id;
        DROP TABLE book_entries;
        DROP TABLE scans;
        ALTER TABLE scans_v17 RENAME TO scans;
        ALTER TABLE book_entries_v17 RENAME TO book_entries;
        CREATE INDEX IF NOT EXISTS scans_at ON scans(at)
    """,
    # 18: a mail action's recipient, so the sender's row and the crafter's line can name each other (C22, T21);
    # sent mail is confirmed by the client like posts and purchases (A75).
    18: """
        ALTER TABLE actions ADD COLUMN to_char TEXT;
        ALTER TABLE plan_new ADD COLUMN to_char TEXT;
        ALTER TABLE mail_sent ADD COLUMN confirmed INTEGER NOT NULL DEFAULT 1
    """,
    # 19: the expected net of the action an event matched, copied when it matches, so History can set it against
    # the realized profit (T23, D68).
    19: """
        ALTER TABLE posts ADD COLUMN expected_net REAL;
        ALTER TABLE purchases ADD COLUMN expected_net REAL;
        ALTER TABLE crafts ADD COLUMN expected_net REAL;
        ALTER TABLE disenchants ADD COLUMN expected_net REAL
    """,
    # 20: the portfolio optimizer's alternatives, unfunded candidates with their reason, staged and swapped
    # with the plan (SPEC §3.2 planned tables, §3.4D, M9).
    20: """
        CREATE TABLE IF NOT EXISTS alternatives(
            market TEXT NOT NULL, cand_id TEXT NOT NULL, char_id TEXT NOT NULL, list TEXT NOT NULL, reason TEXT NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(market, cand_id));
        CREATE TABLE IF NOT EXISTS alternatives_new(
            market TEXT NOT NULL, cand_id TEXT NOT NULL, char_id TEXT NOT NULL, list TEXT NOT NULL, reason TEXT NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(market, cand_id))
    """,
    # 21: what the addon files carry of the portfolio: the exported candidates and the portfolio snapshot with its
    # positions, staged and swapped with the plan (SPEC §3.5 `K`, `portfolio`, `positions`, M11).
    21: """
        CREATE TABLE IF NOT EXISTS export_candidates(
            market TEXT NOT NULL, pos INTEGER NOT NULL, cand_id TEXT NOT NULL, status TEXT NOT NULL, adjusted REAL NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(market, pos)
        );
        CREATE TABLE IF NOT EXISTS export_candidates_new(
            market TEXT NOT NULL, pos INTEGER NOT NULL, cand_id TEXT NOT NULL, status TEXT NOT NULL, adjusted REAL NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(market, pos)
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshot(market TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS portfolio_snapshot_new(market TEXT PRIMARY KEY, data TEXT NOT NULL)
    """,
    # 22: positions and their lots (SPEC §3.2, §3.4D.9, M14), and the funded candidate each plan action came from,
    # so a matched purchase or craft knows the opportunity it opens a position for.
    22: """
        ALTER TABLE actions ADD COLUMN cand_id TEXT;
        ALTER TABLE plan_new ADD COLUMN cand_id TEXT;
        CREATE TABLE IF NOT EXISTS positions(
            position_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, market TEXT, cand_id TEXT, strategy TEXT,
            key INTEGER NOT NULL, state TEXT NOT NULL, opened_at INTEGER NOT NULL, units INTEGER NOT NULL,
            units_left INTEGER NOT NULL, cost_total INTEGER NOT NULL, cost_left INTEGER NOT NULL, expected_net_open REAL,
            expected_net_now REAL, target_window_ms INTEGER, latest_window_ms INTEGER, next_action TEXT,
            realized REAL NOT NULL DEFAULT 0, closed_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS position_lots(
            position_id TEXT NOT NULL, event_id TEXT NOT NULL, units INTEGER NOT NULL, unit_cost INTEGER NOT NULL,
            at INTEGER NOT NULL, PRIMARY KEY(position_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS position_lots_event ON position_lots(event_id)
    """,
    # 23: Gathering (SPEC §3.2, §3.4E, M17): gather events, the confirmed farming sessions derived from them with
    # their items, and the manual rate overrides set from the dashboard.
    23: """
        CREATE TABLE IF NOT EXISTS gather_events(
            event_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, at INTEGER NOT NULL, source TEXT NOT NULL,
            login_ms INTEGER NOT NULL, loot TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS gather_events_char_at ON gather_events(char_id, at);
        CREATE TABLE IF NOT EXISTS farming_sessions(
            session_id TEXT PRIMARY KEY, char_id TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
            units INTEGER NOT NULL, ended TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS farming_sessions_char ON farming_sessions(char_id, end_ms);
        CREATE TABLE IF NOT EXISTS farming_session_items(
            session_id TEXT NOT NULL, key INTEGER NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY(session_id, key)
        );
        CREATE TABLE IF NOT EXISTS manual_rates(
            char_id TEXT NOT NULL, key INTEGER NOT NULL, items_per_hour INTEGER NOT NULL, set_at INTEGER NOT NULL,
            PRIMARY KEY(char_id, key)
        )
    """,
    # 24: AHledger and data coverage (SPEC §2.3, §3.2, M19): its price table's side rows, each remote source's state,
    # and the hours each source observed completely, filled from what is still stored: the complete local scans
    # (30 days) and the hours of AHF hour closes. Older local hours are not recoverable and count for nobody.
    24: """
        CREATE TABLE IF NOT EXISTS ahledger_rows(
            market TEXT NOT NULL, key INTEGER NOT NULL, updated INTEGER NOT NULL, median INTEGER, m7 INTEGER,
            m30 INTEGER, lo INTEGER, hi INTEGER, per_day_x10 INTEGER, PRIMARY KEY(market, key)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS source_state(
            market TEXT NOT NULL, src TEXT NOT NULL, etag TEXT, updated_ms INTEGER, fetched_ms INTEGER,
            checkpoint TEXT, PRIMARY KEY(market, src)
        );
        CREATE TABLE IF NOT EXISTS observed_hours(
            market TEXT NOT NULL, src TEXT NOT NULL, hour INTEGER NOT NULL, PRIMARY KEY(market, src, hour)
        ) WITHOUT ROWID;
        INSERT OR IGNORE INTO observed_hours(market, src, hour)
            SELECT DISTINCT market, 'local', at / 3600000 FROM scans
            WHERE src = 'local' AND kind = 'full' AND coverage >= 1;
        INSERT OR IGNORE INTO observed_hours(market, src, hour)
            SELECT DISTINCT market, 'ahf', t / 3600000 FROM candles_h1 WHERE src = 'ahf'
    """,
    # 14: sell records, stored for the hourly status pass (M8).
    14: """
        CREATE TABLE IF NOT EXISTS sell_records(
            market TEXT NOT NULL, key INTEGER NOT NULL, sold INTEGER NOT NULL, expired INTEGER NOT NULL, streak INTEGER NOT NULL,
            lowest_expiries INTEGER NOT NULL, undercut_last INTEGER NOT NULL, not_selling INTEGER NOT NULL,
            PRIMARY KEY(market, key)
        )
    """,
    # 25: listings that ended per item and day, and new listings by position, from auction lifetimes (SPEC §2.4, M33a).
    25: """
        CREATE TABLE IF NOT EXISTS new_listings_d1(
            market TEXT NOT NULL, key INTEGER NOT NULL, day INTEGER NOT NULL, src TEXT NOT NULL, positions TEXT NOT NULL,
            PRIMARY KEY(market, key, day, src)
        ) WITHOUT ROWID;;
        CREATE TABLE IF NOT EXISTS lifetimes_d1(
            market TEXT NOT NULL, key INTEGER NOT NULL, day INTEGER NOT NULL, src TEXT NOT NULL,
            ended REAL NOT NULL, expired REAL NOT NULL, matched REAL NOT NULL,
            PRIMARY KEY(market, key, day, src)
        ) WITHOUT ROWID;
    """,
    # 26: 'game' when the vendor price came from the addon's itemNames, for the item detail (SPEC §2.4, A92, BZ25).
    26: "ALTER TABLE items ADD COLUMN vendor_src TEXT",
    # 27: commodity cancels and the lowest-price endings per item and day, from auction lifetimes (BZ40, BZ41, M33b).
    # Old rows read 0 until a daily run or a rebuild recomputes their day. A table rebuild, not ADD COLUMN, so it runs
    # on a table that already has them.
    27: """
        CREATE TABLE lifetimes_d1_new(
            market TEXT NOT NULL, key INTEGER NOT NULL, day INTEGER NOT NULL, src TEXT NOT NULL,
            ended REAL NOT NULL, expired REAL NOT NULL, matched REAL NOT NULL,
            cancels REAL NOT NULL DEFAULT 0, cancelled REAL NOT NULL DEFAULT 0, t1_ended REAL NOT NULL DEFAULT 0,
            t1_left REAL NOT NULL DEFAULT 0, commodity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(market, key, day, src)
        ) WITHOUT ROWID;;
        INSERT INTO lifetimes_d1_new(market, key, day, src, ended, expired, matched)
            SELECT market, key, day, src, ended, expired, matched FROM lifetimes_d1;;
        DROP TABLE lifetimes_d1;;
        ALTER TABLE lifetimes_d1_new RENAME TO lifetimes_d1
    """,
    # 28: of an id day's sold, the units estimated sold before first sight, for the item detail (BZ45, V44).
    28: """
        ALTER TABLE volume_d1 ADD COLUMN unseen REAL
    """,
    # 29: a post counts the units its mail sold and returned; a commodity sells in parts (T25), so a post has
    # several mails and History looks them up by post (the indexes, T26). Posts stuck open
    # because no mail equalled their stack resolve at the next tracking pass from their unused mail.
    29: """
        ALTER TABLE posts ADD COLUMN sold_units INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE posts ADD COLUMN returned_units INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS auction_mail_post ON auction_mail(post_id);
        CREATE INDEX IF NOT EXISTS payouts_mail ON payouts(mail_id)
    """,
    # 30: the units each item sold per day by the price they sold at, from auction lifetimes (SPEC §2.4, BZ52). Empty
    # until a daily run or a rebuild recomputes the days.
    30: """
        CREATE TABLE IF NOT EXISTS sold_prices_d1(
            market TEXT NOT NULL, key INTEGER NOT NULL, day INTEGER NOT NULL, src TEXT NOT NULL, prices TEXT NOT NULL,
            PRIMARY KEY(market, key, day, src)
        ) WITHOUT ROWID
    """,
}
