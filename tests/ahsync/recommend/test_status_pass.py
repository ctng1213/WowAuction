"""Tests for ahsync/recommend/status_pass.py: the hourly status pass (SPEC §3.1.1, §3.4.0, §3.4.1)."""
from __future__ import annotations

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.history.reads import HistoryReads
from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence
from ahsync.recommend.plan import week_start_ms
from ahsync.recommend.status_pass import status_pass
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
TUE, WED = 1, 2
LINEN = 2589
CFG = Config(markets=(M,))
NOW = MONDAY + TUE * DAY + 23 * HOUR + 30 * 60_000          # Tue 23:30: the Tue 20-23 window has just ended


def window(day: int) -> IntelWindow:
    """A 20:00-23:00 window peaking at 20:00."""
    return IntelWindow(day * 24 + 20, 3, day * 24 + 20, 1.0, 7)


@pytest.fixture
def db(tmp_path):
    """Amaya, published Linen intelligence with Tue, Wed and Thu windows, and three open actions."""
    database = Database.open(tmp_path / "s.db", FakeClock().now_ms(), None)
    linen = ItemIntel(LINEN, 45, 20, HOUR, 49, 49, 40, 60, 0.0, 400.0, 0.0, 100.0, "ahf", {"ahf": 7}, 49, 20, 0.9,
                      False, 46.0, 80, 40, None, (), sell_windows=(window(TUE), window(WED), window(3)))
    with database.transaction():
        database.conn.execute("INSERT INTO characters(id, market, gold) VALUES ('Amaya-G', ?, 100000)", (M,))
        database.conn.execute("INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_buy, vendor_sell, stack) "
                              "VALUES (?, ?, 'Linen Cloth', 1, 5, 7, NULL, 10, 20)", (LINEN, LINEN))
        database.stage_intel(MarketIntelligence(M, NOW - DAY, NOW - DAY, 1, {LINEN: linen}))
    database.publish_refresh([M])                              # publish first: it replaces pending actions
    with database.transaction():
        week = week_start_ms(NOW)
        rows = [("buy1", "buy", NOW - 25 * HOUR, None, None, None),                              # 25 h old
                ("post1", "post", MONDAY + TUE * DAY + 12 * HOUR, MONDAY + TUE * DAY + 20 * HOUR,
                 MONDAY + TUE * DAY + 23 * HOUR, 800.0),                                          # the missed Tue peak
                ("post2", "post", NOW - HOUR, MONDAY + 3 * DAY + 20 * HOUR, MONDAY + 3 * DAY + 23 * HOUR, 700.0)]   # late
        for action_id, kind, slot, start, end, anchor in rows:
            database.conn.execute(
                "INSERT INTO actions(action_id, run_at, slot_ms, char_id, kind, key, qty, price, expected_net, status, "
                "done_qty, window_start_ms, window_end_ms, anchor_net, anchor_window_ms, week_start_ms) "
                "VALUES (?, ?, ?, 'Amaya-G', ?, ?, 20, 49, ?, 'pending', 0, ?, ?, ?, ?, ?)",
                (action_id, NOW - DAY, slot, kind, LINEN, anchor or 0, start, end, anchor, start, week))
    yield database
    database.close()


def statuses(db: Database) -> dict[str, tuple]:
    """action id -> (kind, status, replaces_id, anchor_net, window start)."""
    return {r[0]: r[1:] for r in db.conn.execute(
        "SELECT action_id, kind, status, replaces_id, anchor_net, window_start_ms FROM actions")}


def test_the_status_pass_marks_late_and_missed_and_moves_a_missed_peak(db, monkeypatch):
    for name in ("market_history", "hourly_closes", "daily_closes", "volume_rows"):     # no history reads (IU10)
        monkeypatch.setattr(HistoryReads, name, lambda *a, **k: pytest.fail("the status pass read history"))
    counts = status_pass(db, CFG, NOW)
    s = statuses(db)
    assert (s["buy1"][1], s["post1"][1], s["post2"][1]) == ("missed", "missed", "late")
    kind, status, replaces, anchor, start = s["post1:r"]
    assert (kind, status, replaces, anchor, start) == ("post", "pending", "post1", 800.0, MONDAY + WED * DAY + 20 * HOUR)
    assert counts == {"late": 1, "missed": 2, "replaced": 1}
    assert status_pass(db, CFG, NOW) == {"late": 0, "missed": 0, "replaced": 0}      # nothing left to change


def test_a_missed_post_without_intelligence_or_a_window_is_just_missed(db):
    with db.transaction():
        db.conn.execute("DELETE FROM intel")
    counts = status_pass(db, CFG, NOW)
    assert counts["replaced"] == 0 and statuses(db)["post1"][1] == "missed"