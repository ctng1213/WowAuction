"""Tests for ahsync/engine/history_view.py: the History page (SPEC §3.5, D47, D48)."""
from __future__ import annotations

from ahsync.engine.history_view import history_view, open_text, ratio_text, short_when
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.tracking import ChainFacts, PostResult, ProfitSums

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_760_000_000_000                       # Thu 9 Oct 08:53 UTC
ROD, LINEN, ORE, THREAD, DUST, CINCH, SILK, WOOL = 6217, 2589, 2772, 2320, 10940, 4239, 4306, 2592
NAMES = {ROD: "Copper Rod", LINEN: "Linen Cloth", ORE: "Iron Ore", THREAD: "Coarse Thread", DUST: "Strange Dust",
         CINCH: "Bandit Cinch", SILK: "Silk Cloth", WOOL: "Wool Cloth"}
LOOKS = {k: ItemLook(n, 1) for k, n in NAMES.items()}
POSTED = NOW - 3 * DAY                        # Mon 6 Oct 08:53
A = "Amaya-G"
JOURNAL = [   # (at, char, kind, key, units, price, plan, a, b, expected, facts), newest first as state.journal gives it
    (NOW - 1 * HOUR, A, "paid_out", ROD, 1, None, None, 3915, 1915.0, None, None),
    (NOW - 2 * HOUR, A, "sold", ROD, 1, 4100, None, POSTED, 20, None, None),
    (NOW - 3 * HOUR, A, "bought", LINEN, 20, 38, ("done", 20, 20), None, None, None, None),
    (NOW - 4 * HOUR, A, "bought", ORE, 6, 52, ("partial", 6, 10), None, None, None, None),
    (NOW - 5 * HOUR, A, "bought_vendor", THREAD, 4, 10, None, None, None, None, None),
    (NOW - 6 * HOUR, A, "crafted", ROD, 1, None, ("done", 1, 1), None, None, None, None),
    (NOW - 7 * HOUR, A, "disenchanted", CINCH, 1, None, None, f"{DUST},2", None, None, None),
    (NOW - 8 * HOUR, A, "mailed", LINEN, 5, None, None, "Brugan-G", None, None, None),
    (NOW - 9 * HOUR, A, "missed", SILK, 40, 28, ("missed", 0, 40), "buy", None, None, None),
    (NOW - 10 * HOUR, A, "skipped", SILK, 40, 28, ("skipped", 0, 40), "buy", None, None, None),
    (NOW - 2 * DAY, A, "expired", LINEN, 20, 45, None, None, 15, None, None),
    (NOW - 2 * DAY, A, "cancelled", LINEN, 20, 45, None, POSTED, 15, None, None),
    (POSTED, A, "posted", ROD, 1, 4100, None, 24, 20, None, None),
    (NOW - 8 * DAY, A, "won", CINCH, 1, 200, ("pending", 0, 1), None, None, None, None),
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
                     ["Realized profit", "+19s 15c", "1 payouts · after cost and deposit"],
                     ["Expected (resolved)", "0c", "0 resolved plan events"],
                     ["Realized", "0c", "the same events"],
                     ["Realized ÷ expected", "—", "how the plan's estimates held up"]]


def test_a_payout_before_its_profit_is_known_and_local_time():
    rows = history_view([(NOW, A, "paid_out", ROD, 1, None, None, 3915, None, None, None)], LOOKS, NAMES, NOW, 2 * HOUR)["rows"]
    assert (rows[0]["detail"], rows[0]["when"]) == ("gold taken from the mailbox", "Thu 10:53")
    assert short_when(NOW, NOW, 0) == "Thu 08:53"
    [row] = history_view([(NOW, A, "bought", ROD, 1, None, None, None, None, None, None)], LOOKS, NAMES, NOW, 0)["rows"]
    assert row["detail"] == ""


def test_D51_untracked_changes_say_what_they_likely_were():
    rows = history_view([(NOW, A, "untracked", LINEN, 12, None, None, 12, None, None, None),
                         (NOW, A, "untracked", ROD, 5, None, None, -5, None, None, None)], LOOKS, NAMES, NOW, 0)["rows"]
    assert [(r["event"], r["event_class"], r["qty"], r["price"], r["detail"]) for r in rows] == [
        ("Untracked", "amber", 12, "—", "+12: looted, traded or found · cost 0 · no recorded event"),
        ("Untracked", "amber", 5, "—", "−5: used, sold to a vendor or destroyed · no recorded event")]


# D68: posts against their plan; D69: a craft chain, a flip with nothing posted and a DE buy (newest first).
CHAIN_POSTS = (PostResult("sold", 300.0, 2, "c"), PostResult("sold", 350.0, 2, "c"), PostResult(None, None, 2, "c"))
PLANNED = [
    (NOW - 1 * HOUR, A, "posted", ROD, 1, 4100, ("done", 1, 1), 24, 3, 447.0, PostResult("sold", 410.0, 3)),
    (NOW - 2 * HOUR, A, "posted", LINEN, 20, 45, ("done", 20, 20), 24, 3, 120.0, PostResult("expired", None, 3)),
    (NOW - 3 * HOUR, A, "posted", SILK, 20, 60, ("done", 20, 20), 24, 5, 250.0, PostResult(None, None, 5)),
    (NOW - 4 * HOUR, A, "posted", WOOL, 20, 30, None, 24, 2, None, PostResult("sold", 80.0, 2)),
    (NOW - 5 * HOUR, A, "disenchanted", CINCH, 1, None, ("done", 1, 1), f"{DUST},2", None, 150.0, None),
    (NOW - 6 * HOUR, A, "bought_vendor", THREAD, 4, 10, None, None, None, None, None),
    (NOW - 7 * HOUR, A, "paid_out", ROD, 1, None, None, 4407, 410.0, None, None),
    (NOW - 8 * HOUR, A, "posted", ROD, 3, 4100, ("done", 3, 3), 24, 2, 300.0, CHAIN_POSTS[0]),    # the chain's posts
    (NOW - 9 * HOUR, A, "crafted", ROD, 7, None, ("done", 7, 7), None, None, 971.0, ChainFacts("c", CHAIN_POSTS)),
    (NOW - 10 * HOUR, A, "bought", LINEN, 20, 38, ("done", 20, 20), None, None, 210.0, ChainFacts("f", ())),
    (NOW - 11 * HOUR, A, "bought", CINCH, 1, 200, ("done", 1, 1), None, None, 500.0, ChainFacts("d", (), disenchants=True)),
]


def cells(row: dict) -> tuple:
    """A row's Expected and Realized cells."""
    return row["expected"], row["realized"], row["realized_class"], row["realized_note"]


def test_D68_posts_show_what_the_plan_expected_next_to_what_they_realized():
    rows = history_view(PLANNED, LOOKS, NAMES, NOW, 0)["rows"]
    assert [cells(r) for r in rows[:7]] == [
        ("+4s 47c", "+4s 10c", "amber", ""),                     # below what the plan expected
        ("+1s 20c", "−3c", "amber", "expired"),                  # the deposit lost
        ("+2s 50c", "—", "muted", "not sold yet"),
        ("—", "+80c", "", ""),                                   # matched no plan action
        ("+1s 50c", "—", "muted", "mats sold separately"),
        ("", "", "", ""), ("", "", "", "")]                      # a vendor buy and a payout get no cells
    better = (NOW, A, "posted", ROD, 1, 4100, None, 24, 3, 400.0, PostResult("sold", 410.0, 3))
    assert cells(history_view([better], LOOKS, NAMES, NOW, 0)["rows"][0])[2] == "pos"


def test_D69_a_craft_chain_shows_what_its_posts_made_so_far():
    rows = history_view(PLANNED, LOOKS, NAMES, NOW, 0)["rows"]
    assert [cells(r) for r in rows[8:]] == [
        ("+9s 71c", "+6s 50c", "amber", "2 of 3 posts paid"),
        ("+2s 10c", "—", "muted", "nothing sold yet"),
        ("+5s", "—", "muted", "mats sold separately")]


def test_D70_the_tiles_set_resolved_expected_against_realized():
    page = history_view(PLANNED, LOOKS, NAMES, NOW, 0)
    assert page["tiles"][5:] == [["Expected (resolved)", "+5s 67c", "2 resolved plan events"],
                                 ["Realized", "+4s 7c", "the same events"],
                                 ["Realized ÷ expected", "72%", "how the plan's estimates held up"]]   # 407 ÷ 567
    assert page["open"] == "1 post open · 2 chains in progress (counted once resolved)"
    assert history_view(JOURNAL, LOOKS, NAMES, NOW, 0)["open"] == "nothing open"
    assert open_text(ProfitSums(0.0, 0.0, 0, 4, 0)) == "4 posts open (counted once resolved)"
    assert open_text(ProfitSums(0.0, 0.0, 0, 0, 1)) == "1 chain in progress (counted once resolved)"
    assert ratio_text(ProfitSums(-100.0, 50.0, 1, 0, 0)) == "—"                 # nothing positive to compare with
    assert ratio_text(ProfitSums(400.0, -48.0, 2, 0, 0)) == "−12%"
