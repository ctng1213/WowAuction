"""Tests for ahsync/recommend/plan_pages.py: the plan's Today, Week and Flip payloads (SPEC §3.5)."""
from __future__ import annotations

from ahsync.engine.plan import PlanAction
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend import plan_pages
from ahsync.recommend.plan import plan_rows

HOUR, DAY = 3_600_000, 86_400_000
NOW = 345_600_000 + 3000 * 7 * DAY + 4 * DAY + 14 * HOUR
M, LINEN = "forever.pvp.horde.us", 2589
ITEMS = {LINEN: ItemInfo(LINEN, "Linen Cloth", 1, 5, 7, None, 10, 20)}
STATS = {LINEN: ItemIntel(LINEN, 45, 20, HOUR, 49, 49, 40, 60, 0.0, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                          False, 42.0, 86, 93, None, ())}


def flip() -> list[PlanAction]:
    """A flip of 20 Linen: buy now at 38c, post Saturday."""
    return [PlanAction("buy", "Amaya-G", LINEN, 20, 38, NOW, None, None, None, None, 1050.0, 290.0, chain="flip:2589"),
            PlanAction("post", "Amaya-G", LINEN, 20, 55, NOW + DAY, NOW + 2 * DAY, NOW + 2 * DAY + 4 * HOUR,
                       NOW + 2 * DAY + HOUR, 0.9, 1050.0, 290.0, 24, "flip:2589")]


def test_new_plan_and_kept_history_become_view_actions():
    actions = flip()
    rows = plan_rows(actions, M, NOW)
    history = [("old", None, "post", "Amaya-G", LINEN, 5, 45, NOW - HOUR, 213.0, "done"),
               ("kid", "old", "post", "Amaya-G", LINEN, 5, 45, NOW - HOUR, None, "missed")]
    views = plan_pages.view_actions(actions, rows, history)
    assert [(v.action_id, v.status, v.in_chain) for v in views] == [
        ("old", "done", True), ("kid", "missed", True), (rows[0][0], "pending", True), (rows[1][0], "pending", True)]
    assert views[1].net == 0.0 and views[3].parent_id == rows[0][0] and views[3].peak_ms == NOW + 2 * DAY + HOUR
    assert (rows[0][-1], rows[1][-1]) == (None, 24)                  # EX9: the post carries its 24 h duration


def test_pages_today_by_filter_step_week_and_flip():
    actions = flip()
    views = plan_pages.view_actions(actions, plan_rows(actions, M, NOW), [])
    pages = plan_pages.plan_pages(views, STATS, ITEMS, 314_000, NOW, 0)
    assert sorted(pages) == ["flip", "today", "week"] and sorted(pages["today"]["by_min"]) == ["0", "25", "50", "75"]
    [buy] = pages["today"]["by_min"]["50"]["rows"]
    assert (buy["item"], buy["net"], buy["chance"]) == ("Linen Cloth", "+2s 90c", "90%")
    assert pages["week"]["total"] == "+2s 90c" and pages["flip"]["rows"][0]["roi"] == "38%"     # 290 ÷ 760
    unknown = plan_pages.item_looks({7}, {}, {})
    assert (unknown[7].name, unknown[7].conf) == ("7", None)

def test_an_unknown_suffix_key_is_named_after_its_base_item():
    looks = plan_pages.item_looks({-LINEN, LINEN, 99}, {}, ITEMS)
    assert [looks[k].name for k in (-LINEN, LINEN, 99)] == ["Linen Cloth (unknown suffix)", "Linen Cloth", "99"]
