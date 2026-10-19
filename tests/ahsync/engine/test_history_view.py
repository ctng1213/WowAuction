"""Tests for ahsync/engine/history_view.py: the History page (SPEC §3.5, D47, D48)."""
from __future__ import annotations

from ahsync.engine.history_view import history_view, short_when
from ahsync.engine.plan_views import ItemLook

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_760_000_000_000                       # Thu 9 Oct 08:53 UTC
ROD, LINEN, ORE, THREAD, DUST, CINCH, SILK = 6217, 2589, 2772, 2320, 10940, 4239, 4306
NAMES = {ROD: "Copper Rod", LINEN: "Linen Cloth", ORE: "Iron Ore", THREAD: "Coarse Thread", DUST: "Strange Dust",
         CINCH: "Bandit Cinch", SILK: "Silk Cloth"}
LOOKS = {k: ItemLook(n, 1) for k, n in NAMES.items()}
POSTED = NOW - 3 * DAY                        # Mon 6 Oct 08:53
A = "Amaya-G"
JOURNAL = [   # (at, char, kind, key, units, price, plan, a, b), newest first as state.journal gives it
    (NOW - 1 * HOUR, A, "paid_out", ROD, 1, None, None, 3915, 1915.0),
    (NOW - 2 * HOUR, A, "sold", ROD, 1, 4100, None, POSTED, 20),
    (NOW - 3 * HOUR, A, "bought", LINEN, 20, 38, ("done", 20, 20), None, None),
    (NOW - 4 * HOUR, A, "bought", ORE, 6, 52, ("partial", 6, 10), None, None),
    (NOW - 5 * HOUR, A, "bought_vendor", THREAD, 4, 10, None, None, None),
    (NOW - 6 * HOUR, A, "crafted", ROD, 1, None, ("done", 1, 1), None, None),
    (NOW - 7 * HOUR, A, "disenchanted", CINCH, 1, None, None, f"{DUST},2", None),
    (NOW - 8 * HOUR, A, "mailed", LINEN, 5, None, None, "Brugan-G", None),
    (NOW - 9 * HOUR, A, "missed", SILK, 40, 28, ("missed", 0, 40), "buy", None),
    (NOW - 10 * HOUR, A, "skipped", SILK, 40, 28, ("skipped", 0, 40), "buy", None),
    (NOW - 2 * DAY, A, "expired", LINEN, 20, 45, None, None, 15),
    (NOW - 2 * DAY, A, "cancelled", LINEN, 20, 45, None, POSTED, 15),
    (POSTED, A, "posted", ROD, 1, 4100, None, 24, 20),
    (NOW - 8 * DAY, A, "won", CINCH, 1, 200, ("pending", 0, 1), None, None),
]


def test_D47_the_journal_lists_each_event_with_its_plan_action_and_detail():
    rows = history_view(JOURNAL, LOOKS, NAMES, NOW, 0)["rows"]
    cells = [(r["event"], r["item"], r["qty"], r["price"], r["plan"], r["detail"]) for r in rows]
    assert cells == [
        ("Paid out", "Copper Rod", 1, "39s 15c", "—", "gold taken from the mailbox · realized +19s 15c"),
        ("Sold", "Copper Rod", 1, "41s", "—", "post Mon 08:53 resolved"),
        ("Bought", "Linen Cloth", 20, "38c", "✓ done", "paid 7s 60c"),
        ("Bought", "Iron Ore", 6, "52c", "partial 6/10", "paid 3s 12c"),
        ("Bought (vendor)", "Coarse Thread", 4, "10c", "—", "paid 40c"),
        ("Crafted", "Copper Rod", 1, "—", "✓ done", ""),
        ("Disenchanted", "Bandit Cinch", 1, "—", "—", "gave 2 Strange Dust"),
        ("Mailed", "Linen Cloth", 5, "—", "—", "to Brugan"),
        ("—", "Silk Cloth", 40, "28c", "missed", "buy slot passed, nothing matched"),
        ("—", "Silk Cloth", 40, "28c", "skipped", "skipped by you"),
        ("Expired", "Linen Cloth", 20, "45c", "—", "its post wasn't captured"),
        ("Cancelled", "Linen Cloth", 20, "45c", "—", "post Mon 08:53 resolved"),
        ("Posted", "Copper Rod", 1, "41s", "—", "24 h · deposit 20c"),
        ("Won", "Bandit Cinch", 1, "2s", "pending", ""),
    ]
    assert (rows[0]["when"], rows[0]["character"], rows[0]["kind"], rows[0]["sort"]["age_days"]) == ("Thu 07:53", "Amaya",
                                                                                                    "paid_out", 0)
    assert (rows[-1]["when"], rows[-1]["sort"]["age_days"]) == ("1 Oct 08:53", 8)          # older than 6 days: its date
    assert (rows[1]["event_class"], rows[8]["plan_class"], rows[3]["plan_class"]) == ("pos", "neg", "amber")


def test_D48_the_tiles_total_the_last_30_days():
    tiles = history_view(JOURNAL, LOOKS, NAMES, NOW, 0)["tiles"]
    assert tiles == [["Sold (30 d)", "1 auctions", "38s 95c after the cut"],      # 41s × 0.95
                     ["Expired (30 d)", "1 auctions", "1 cancelled"],
                     ["Deposits lost", "15c", "30 days"],
                     ["Plan actions", "2 done", "1 missed · 1 skipped"],
                     ["Realized profit", "+19s 15c", "1 payouts · after cost and deposit"]]


def test_a_payout_before_its_profit_is_known_and_local_time():
    rows = history_view([(NOW, A, "paid_out", ROD, 1, None, None, 3915, None)], LOOKS, NAMES, NOW, 2 * HOUR)["rows"]
    assert (rows[0]["detail"], rows[0]["when"]) == ("gold taken from the mailbox", "Thu 10:53")
    assert short_when(NOW, NOW, 0) == "Thu 08:53"
    [row] = history_view([(NOW, A, "bought", ROD, 1, None, None, None, None)], LOOKS, NAMES, NOW, 0)["rows"]
    assert row["detail"] == ""


def test_D51_untracked_changes_say_what_they_likely_were():
    rows = history_view([(NOW, A, "untracked", LINEN, 12, None, None, 12, None),
                         (NOW, A, "untracked", ROD, 5, None, None, -5, None)], LOOKS, NAMES, NOW, 0)["rows"]
    assert [(r["event"], r["event_class"], r["qty"], r["price"], r["detail"]) for r in rows] == [
        ("Untracked", "amber", 12, "—", "+12: looted, traded or found · cost 0 · no recorded event"),
        ("Untracked", "amber", 5, "—", "−5: used, sold to a vendor or destroyed · no recorded event")]
