"""Tests for ahsync/engine/inventory_view.py: the Inventory tab (SPEC §3.5)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine.inventory_view import Holding, InventoryWorld, inventory_view
from ahsync.engine.patterns import Window
from ahsync.engine.plan_views import ItemLook, ViewAction

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
SAT = MONDAY + 5 * DAY
NOW = MONDAY + 4 * DAY + 14 * HOUR                  # Fri 14:00 UTC
LINEN, WOOL, BAR, ROD, GLOVES, RING, FANG, SILK = 2589, 2592, 2840, 6217, 2851, 1000, 3300, 4306
LOOKS = {LINEN: ItemLook("Linen Cloth", 1, 86, 48.0, "ahf", 93), WOOL: ItemLook("Wool Cloth", 1),
         SILK: ItemLook("Silk Cloth", 1),
         BAR: ItemLook("Copper Bar", 1, 80, 40.0, "ahf", 150), ROD: ItemLook("Copper Rod", 1),
         GLOVES: ItemLook("Scouting Gloves", 2), RING: ItemLook("Band of Thorns", 2), FANG: ItemLook("Broken Fang", 0)}


def world(*actions: ViewAction, **changes) -> InventoryWorld:
    """The plan and item facts; no windows, values, crafts or rules unless changed."""
    return replace(InventoryWorld(actions, LOOKS, {}, {}, {}, {}), **changes)


def action(action_id: str, kind: str, char_id: str, key: int, qty: int, price: int | None, expected: float,
           **changes) -> ViewAction:
    """A pending plan action posted now, standing alone."""
    return replace(ViewAction(action_id, None, kind, char_id, key, qty, price, NOW, expected, None, "pending",
                              expected=expected), **changes)


def test_D25_an_item_in_bags_and_bank_shares_one_plan_and_its_worth_splits_by_quantity():
    post = action("p", "post", "Amaya-G", LINEN, 30, 55, 1567.5, at_ms=SAT + 10 * HOUR, peak_ms=SAT + 18 * HOUR, p=1.0)
    view = inventory_view([Holding("Amaya-G", "bags", LINEN, 20, False), Holding("Amaya-G", "bank", LINEN, 10, False)],
                          world(post))
    assert [(r["where"], r["plan"], r["chance"], r["worth"]) for r in view["rows"]] == [
        ("bags", "Post Sat 10:00 at 55c (18:00 peak)", "100%", "10s 45c"),
        ("bank", "Post Sat 10:00 at 55c (18:00 peak)", "100%", "5s 23c")]
    assert view["tiles"][0][:2] == ["Worth", "15s 68c"]


def test_D26_units_a_planned_craft_uses_count_at_their_hold_value():
    post = action("p", "post", "Brugan-G", BAR, 6, 1200, 6840.0, p=1.0)
    craft = action("c", "craft", "Brugan-G", ROD, 6, None, 900.0)
    view = inventory_view([Holding("Brugan-G", "bags", BAR, 12, False)],
                          world(post, craft, values={BAR: 1000.0}, uses={BAR: "Copper Rod"}))
    [row] = view["rows"]
    assert (row["plan"], row["plan_kind"], row["worth"]) == ("Post 6 now at 12s · 6 for crafting Copper Rod", "post",
                                                            "1g 28s 40c")


def test_D27_bound_and_ignored_items_are_not_planned():
    view = inventory_view([Holding("Brugan-G", "bags", RING, 1, True), Holding("Amaya-G", "bags", FANG, 4, False)],
                          world(values={FANG: 21.0}, rules={FANG: "ignore"}))
    assert sorted((r["plan"], r["worth"]) for r in view["rows"]) == [("Bound · not planned", "—"),
                                                                      ("Ignored · not planned", "—")]
    assert view["tiles"][2] == ["Not planned", "2 items", "bound or ignored"]


def test_D28_a_disenchant_by_another_character_shows_on_the_owners_row():
    de = action("d", "de", "Brugan-G", GLOVES, 1, None, 625.0)
    [row] = inventory_view([Holding("Amaya-G", "bags", GLOVES, 1, False)], world(de))["rows"]
    assert (row["character"], row["plan"], row["worth"], row["chance"]) == ("Amaya", "Disenchant (Brugan)", "6s 25c", "—")


def test_D29_paid_comes_from_the_cost_basis_and_is_0_before_tracking():
    view = inventory_view([Holding("Amaya-G", "bags", LINEN, 20, False, 38), Holding("Amaya-G", "mail", WOOL, 7, False)],
                          world())
    rows = {r["item"]: r for r in view["rows"]}
    assert (rows["Linen Cloth"]["paid_each"], rows["Linen Cloth"]["paid_total"], rows["Linen Cloth"]["paid_note"]) == \
        ("38c", "7s 60c", "")
    assert (rows["Wool Cloth"]["paid_each"], rows["Wool Cloth"]["paid_note"]) == ("0c", "before tracking")
    assert view["tiles"][1][:2] == ["Paid", "7s 60c"] and rows["Linen Cloth"]["plan"] == "Not planned"


def test_D31_the_best_selling_windows_column_is_local_and_marks_an_early_pattern():
    windows = (Window(5 * 24 + 18, 4, 0.0, 1.12, 7, 5 * 24 + 19), Window(6 * 24 + 20, 3, 0.0, 1.09, 2, 6 * 24 + 20))
    [row] = inventory_view([Holding("Amaya-G", "bags", LINEN, 20, False)],
                           world(windows={LINEN: windows}, offset_ms=-HOUR))["rows"]
    assert row["windows"] == "Sat 17–21 · Sun 19–22 (early pattern)"


def test_D64_vendor_sales_and_holds_keep_their_inventory_plan_cell_crafts_and_chain_posts_too():
    # Holds and vendor sales are no Today or Week rows (D64), but Inventory still plans and values them.
    vendor = action("v", "vendor", "Amaya-G", FANG, 3, 7, 21.0)
    hold = action("h", "hold", "Amaya-G", FANG, 1, None, 0.0, at_ms=NOW + HOUR)
    chain_post = action("f", "post", "Amaya-G", LINEN, 20, 55, 1000.0, in_chain=True, p=0.9)
    view = inventory_view([Holding("Amaya-G", "bags", FANG, 4, False), Holding("Brugan-G", "bags", BAR, 5, False),
                           Holding("Amaya-G", "bags", LINEN, 2, False)],
                          world(vendor, hold, chain_post, values={FANG: 7.0, LINEN: 40.0}, uses={BAR: "Copper Rod"}))
    rows = {r["item"]: r for r in view["rows"]}
    assert (rows["Broken Fang"]["plan"], rows["Broken Fang"]["worth"]) == ("Vendor 3 · Hold for a later week 1", "28c")
    assert (rows["Copper Bar"]["plan"], rows["Copper Bar"]["plan_kind"], rows["Copper Bar"]["worth"]) == \
        ("For crafting Copper Rod", "craft", "—")
    # A flip's post sells what the flip buys, not this stock: Linen stays unplanned, worth its hold value.
    assert (rows["Linen Cloth"]["plan"], rows["Linen Cloth"]["worth"]) == ("Not planned", "80c")
    assert [r["item"] for r in view["rows"]] == ["Linen Cloth", "Broken Fang", "Copper Bar"]


def test_D40_kept_items_say_so_and_keep_their_worth():
    post = action("p", "post", "Amaya-G", LINEN, 10, 55, 522.5, p=1.0)
    view = inventory_view([Holding("Amaya-G", "bags", LINEN, 20, False), Holding("Amaya-G", "bags", SILK, 7, False)],
                          world(post, values={LINEN: 50.0, SILK: 70.0},
                                reserves={("Amaya-G", LINEN): 10, ("Amaya-G", SILK): -1}))
    rows = {r["item"]: r for r in view["rows"]}
    assert (rows["Linen Cloth"]["plan"], rows["Linen Cloth"]["plan_kind"]) ==         ("Keeping 10 for yourself · Post 10 now at 55c", "post")
    # Silk: 7 x 70c hold value, even though none of it is planned.
    assert (rows["Silk Cloth"]["plan"], rows["Silk Cloth"]["plan_kind"], rows["Silk Cloth"]["worth"]) ==         ("Kept for yourself", "kept", "4s 90c")


def test_a_partial_reserve_with_nothing_else_planned_still_says_what_is_kept():
    [row] = inventory_view([Holding("Amaya-G", "bags", LINEN, 4, False)],
                           world(reserves={("Amaya-G", LINEN): 10}))["rows"]
    assert (row["plan"], row["plan_kind"]) == ("Keeping 10 for yourself", "kept")


def test_D52_each_row_carries_its_full_character_id_for_the_keep_control():
    [row] = inventory_view([Holding("Amaya-G", "bags", GLOVES, 1, False)], world())["rows"]
    assert (row["character"], row["char_id"], row["key"]) == ("Amaya", "Amaya-G", str(GLOVES))
