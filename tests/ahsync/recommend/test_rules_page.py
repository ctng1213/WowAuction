"""Tests for ahsync/recommend/rules_page.py: the Rules payload from the database (SPEC §3.4.2, D50)."""
from __future__ import annotations

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.history.reads import ItemInfo
from ahsync.recommend.rules_page import rules_page
from tests.support.fake_clock import FakeClock

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_760_000_000_000
M = "forever.pvp.horde.us"
LEATHER, STONE = 4234, 2836
ITEMS = {LEATHER: ItemInfo(LEATHER, "Heavy Leather", 1, 30, 7, None, 20, 20), STONE: ItemInfo(STONE, "Coarse Stone", 1, 20, 7, None, 5, 20)}


def test_D50_the_page_reads_rules_records_outcomes_and_stock(tmp_path):
    db = Database.open(tmp_path / "r.db", FakeClock().now_ms(), None)
    try:
        with db.transaction():
            db.conn.execute("INSERT INTO characters(id, market, gold) VALUES ('Brugan-G', ?, 1000)", (M,))
            db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Brugan-G', 'bags', ?, 10, 0)",
                            (LEATHER,))
            db.conn.executemany(
                "INSERT INTO posts(post_id, char_id, key, stack, price, at, outcome, resolved_at, confirmed) "
                "VALUES (?, 'Brugan-G', ?, 1, 50, ?, 'expired', ?, 1)",
                [(f"p{i}", LEATHER, NOW - (i + 2) * DAY, NOW - (i + 1) * DAY) for i in range(3)])
            db.conn.execute("INSERT INTO item_rules(key, rule, set_at, source) VALUES (?, 'no_buy', ?, 'addon'), "
                            "(?, 'none', ?, 'dashboard')", (STONE, NOW - DAY, 999, NOW))
        page = rules_page(db, ["Brugan-G"], M, {}, ITEMS, NOW, 0, Config(markets=(M,)))
        assert [(r["item"], r["owned"]) for r in page["decisions"]] == [("Heavy Leather", "10 (Brugan)")]
        assert [(r["item"], r["from"]) for r in page["blacklist"]] == [("Coarse Stone", "addon")]   # a cleared rule is gone
        assert page["kept"] == []
        assert db.live_rule_rows() == [(STONE, "no_buy", NOW - DAY, "addon")]
    finally:
        db.close()
