"""Tests for ahsync/engine/rules_view.py: the Rules page (SPEC §3.4.2, D50)."""
from __future__ import annotations

from ahsync.engine.plan_views import ItemLook
from ahsync.engine.rules_view import rules_view
from ahsync.engine.sell_record import Record

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_760_000_000_000                        # Thu 9 Oct 08:53 UTC
MON = NOW - 3 * DAY
LEATHER, BOLT, MEDIUM, STONE, LIGHT, RULED = 4234, 2996, 4304, 2836, 2318, 2589
STUCK = Record(0, 3, 3, 3, False, True)


def looks() -> dict[int, ItemLook]:
    """Names, with Heavy Leather and the ruled Linen flagged not selling."""
    return {LEATHER: ItemLook("Heavy Leather", 1, record=STUCK), BOLT: ItemLook("Bolt of Linen Cloth", 1),
            MEDIUM: ItemLook("Medium Leather", 1), STONE: ItemLook("Coarse Stone", 1), LIGHT: ItemLook("Light Leather", 1),
            RULED: ItemLook("Linen Cloth", 1, record=STUCK)}


RULES = [(BOLT, "keep", MON, "dashboard"), (MEDIUM, "keep", MON, "addon"), (STONE, "no_buy", MON, "addon"),
         (LIGHT, "ignore", MON + HOUR, "dashboard"), (RULED, "no_buy", MON, "dashboard")]
OUTCOMES = [(LEATHER, "p1", 50, MON - DAY, MON, "expired"), (LEATHER, "p0", 50, MON - 2 * DAY, MON - DAY, "expired"),
            (BOLT, "p2", 90, MON + HOUR, MON + 2 * HOUR, "sold"), (BOLT, "p3", 90, MON - DAY, MON - HOUR, "expired")]
LOTS = [("Brugan-G", LEATHER, 10), ("Amaya-G", BOLT, 2), ("Brugan-G", BOLT, 3)]


def test_D50_decisions_kept_items_and_the_blacklist():
    page = rules_view(RULES, looks(), OUTCOMES, LOTS, NOW, 0)
    assert page["decisions"] == [{"key": str(LEATHER), "item": "Heavy Leather", "quality": 1,
                                  "record": "Your record (30 d): 0 of 3 sold · expired 3× while cheapest · price −14% · qty 1/8",
                                  "last_expired": "Mon 08:53", "owned": "10 (Brugan)"}]
    assert [(r["item"], r["kept"], r["since"], r["status"], r["status_class"]) for r in page["kept"]] == [
        ("Bolt of Linen Cloth", "Mon 08:53", "1 sold · 0 expired", "selling again", "pos"),
        ("Medium Leather", "Mon 08:53", "0 sold · 0 expired", "watching: an expiry while cheapest asks again", "amber")]
    assert [(r["item"], r["rule"], r["effect"], r["set"], r["from"]) for r in page["blacklist"]] == [
        ("Coarse Stone", "Don't buy or craft", "owned stock still sold", "Mon 08:53", "addon"),
        ("Light Leather", "Ignore completely", "no recommendations at all", "Mon 09:53", "dashboard"),
        ("Linen Cloth", "Don't buy or craft", "owned stock still sold", "Mon 08:53", "dashboard")]


def test_owned_by_several_characters_and_a_flag_with_no_expiry_on_record():
    page = rules_view([], {BOLT: ItemLook("Bolt of Linen Cloth", 1, record=STUCK)}, [], LOTS, NOW, 0)
    assert (page["decisions"][0]["owned"], page["decisions"][0]["last_expired"]) == ("5 (Amaya 2, Brugan 3)", "—")
    assert rules_view([], {LIGHT: ItemLook("Light Leather", 1, record=STUCK)}, [], [], NOW, 0)["decisions"][0]["owned"] == "0"


def test_a_rule_the_page_does_not_know_is_left_out():
    page = rules_view([(STONE, "someday", MON, "addon")], looks(), [], [], NOW, 0)
    assert (page["kept"], page["blacklist"]) == ([], [])
