"""Tests for ahsync/recommend/inventory_page.py: the Inventory payload and item details (SPEC §3.5)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine.plan_views import ViewAction
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel
from ahsync.recommend import inventory_page
from ahsync.state.reads import RecipeRow

HOUR, DAY = 3_600_000, 86_400_000
NOW = 345_600_000 + 3000 * 7 * DAY + 4 * DAY + 14 * HOUR
LINEN, BAR, ROD, FANG, RING = 2589, 2840, 6217, 3300, 1000
ITEMS = {LINEN: ItemInfo(LINEN, "Linen Cloth", 1, 5, 7, None, 10, 20), BAR: ItemInfo(BAR, "Copper Bar", 1, 10, 7, 0, 50, 20),
         ROD: ItemInfo(ROD, "Copper Rod", 1, 5, 7, None, 30, 1), FANG: ItemInfo(FANG, "Broken Fang", 0, 1, 15, None, 7, 20)}
LINEN_INTEL = ItemIntel(LINEN, 45, 20, HOUR, 49, 49, 40, 60, 0.4, 48.0, 0.0, 61.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                        False, 42.0, 86, 93, None, (), sell_windows=(IntelWindow(5 * 24 + 18, 4, 5 * 24 + 19, 1.12, 7),))
STATS = {LINEN: LINEN_INTEL, BAR: replace(LINEN_INTEL, key=BAR, sell_value=None, sell_windows=())}


class FakeState:
    """Brugan's bags and a Copper Rod recipe; Linen bought at 760c for 20."""

    def holdings(self, char_ids):
        """Inventory rows."""
        return [("Amaya-G", "bags", LINEN, 20, False), ("Brugan-G", "bags", BAR, 4, False),
                ("Brugan-G", "bags", FANG, 3, False), ("Brugan-G", "bags", RING, 1, True)]

    def cost_basis(self):
        """Cost basis."""
        return {LINEN: (20, 760)}

    def known_recipes(self, char_ids):
        """A Copper Rod recipe Brugan knows, and one with no product."""
        return [RecipeRow("Blacksmithing", 2663, ROD, 1, 1, None, ((BAR, 1),), ("Brugan-G",)),
                RecipeRow("Enchanting", 7421, None, 1, 1, None, ((10940, 1),), ("Amaya-G",))]

    def item_rules(self):
        """No rules."""
        return {}

    def reserves(self, char_ids):
        """Nothing kept."""
        return {}

    def transit(self, char_ids):
        """Nothing on its way unless a test sets `arriving`."""
        return getattr(self, "arriving", [])


def test_the_page_gathers_holdings_cost_basis_crafts_and_details():
    craft = ViewAction("c", None, "craft", "Brugan-G", ROD, 4, None, NOW, 120.0, None, "pending")
    page = inventory_page.inventory_page(FakeState(), ["Amaya-G", "Brugan-G"], [craft], STATS, ITEMS, 0)
    rows = {r["item"]: r for r in page["rows"]}
    assert (rows["Linen Cloth"]["paid_each"], rows["Linen Cloth"]["worth"], rows["Linen Cloth"]["windows"]) == \
        ("38c", "8s 40c", "Sat 18–22")                                         # 20 × 42c hold value
    assert (rows["Copper Bar"]["plan"], rows["Copper Bar"]["worth"]) == ("For crafting Copper Rod", "2s")   # vendor 50c
    assert rows["Broken Fang"]["worth"] == "21c" and rows["1000"]["plan"] == "Bound · not planned"
    assert sorted(page["details"]) == ["2589", "2840"]
    assert page["details"][str(LINEN)]["windows"][0]["when"] == "Sat 18:00–22:00"
    assert page["help"]["Worth"].startswith("What it is expected to bring")


def test_T22_mail_on_its_way_is_a_transit_row_of_the_recipient_saying_from_whom_and_when():
    from ahsync.engine.transit import Transit
    state = FakeState()
    monday = 345_600_000 + 3000 * 7 * 86_400_000                                  # a Monday 00:00 UTC
    state.arriving = [Transit("Amaya-G", "Brugan-G", ROD, 2, monday + 10 * HOUR),
                      Transit("Amaya-G", "Cara-G", ROD, 1, monday + 10 * HOUR + 40 * 60_000)]
    page = inventory_page.inventory_page(state, ["Amaya-G", "Brugan-G"], [], STATS, ITEMS, 0)
    [row] = [r for r in page["rows"] if r["where"] == "transit"]
    assert (row["character"], row["item"], row["qty"]) == ("Amaya", "Copper Rod", 3)
    assert row["plan"] == "On its way from Brugan and Cara, mailed Mon 10:40 · Not planned"
