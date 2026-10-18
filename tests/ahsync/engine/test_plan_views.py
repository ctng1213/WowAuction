"""Tests for ahsync/engine/plan_views.py: the Today, Week and Flip tabs (SPEC §3.4, §3.5), invariant VW1."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import plan_views
from ahsync.engine.plan_views import ItemLook, ViewAction
from ahsync.engine.views import signed_money
from tests.support.gen import cases

HOUR, DAY, MINUTE = 3_600_000, 86_400_000, 60_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
FRI = MONDAY + 4 * DAY
NOW = FRI + 14 * HOUR + 30 * MINUTE                 # Fri 14:30 UTC (offset 0)
LINEN, BAR = 2589, 2840
LOOKS = {LINEN: ItemLook("Linen Cloth", 1, 86, 48.0, "ahf", 93, 49, 72), BAR: ItemLook("Copper Bar", 1, 80, 40.0, "ahf", 150)}
GOLD = 314_000
SEED_VW1 = 4055


def act(action_id: str, kind: str, net: float, at: int, p: float | None = None, **changes) -> ViewAction:
    """A plan action of 20 Linen at 38c, pending, standing alone unless changed."""
    return replace(ViewAction(action_id, None, kind, "Amaya-G", LINEN, 20, 38, at, net, p, "pending"), **changes)


def test_D1_today_is_sorted_by_net_and_hides_rows_below_the_chance_filter():
    rows = [act("a", "post", 500, NOW, 0.9), act("b", "post", 2000, NOW, 0.3), act("c", "post", 1000, NOW, 0.6)]
    view = plan_views.today_view(rows, NOW, 0, 0.5, LOOKS, GOLD)
    assert ([r["net"] for r in view["rows"]], view["hidden"]) == (["+10s", "+5s"], 1)


def test_D2_with_the_filter_at_zero_every_row_shows():
    rows = [act("a", "post", 500, NOW, 0.9), act("b", "post", 2000, NOW, 0.3), act("c", "post", 1000, NOW, 0.6)]
    view = plan_views.today_view(rows, NOW, 0, 0.0, LOOKS, GOLD)
    assert [r["net"] for r in view["rows"]] == ["+20s", "+10s", "+5s"]


def test_D16_ties_go_to_the_higher_chance_and_the_footer_adds_up():
    rows = [act("a", "post", 1000, NOW, 0.7), act("b", "post", 500, NOW, 0.8), act("c", "post", 500, NOW, 0.9)]
    view = plan_views.today_view(rows, NOW, 0, 0.5, LOOKS, GOLD)
    assert [r["chance"] for r in view["rows"]] == ["70%", "90%", "80%"] and view["total"] == "+20s"


def test_D11_the_week_starts_today_and_shows_this_mornings_done_buy():
    done = act("d", "buy", 210, FRI + 14 * HOUR + 10 * MINUTE, status="done")
    view = plan_views.week_view([done], NOW, 0, LOOKS, GOLD)
    labels = [d["label"].split()[0] for d in view["days"]]
    assert labels == ["Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu"] and view["days"][0]["today"]
    [row] = view["days"][0]["rows"]
    assert (row["time"], row["status"]) == ("14:10", "done")


def test_D17_day_category_and_character_totals_add_up():
    rows = [act("a", "post", 830, FRI + 16 * HOUR), act("b", "post", 11000, FRI + 17 * HOUR),
            act("c", "vendor", -20, FRI + 18 * HOUR, char_id="Brugan-G")]
    view = plan_views.week_view(rows, NOW, 0, LOOKS, GOLD)
    assert view["days"][0]["total"] == "+1g 18s 10c" == view["total"]
    assert view["categories"] == {"Sell owned": "+1g 18s 10c"}
    assert view["characters"] == {"Amaya": "+1g 18s 30c", "Brugan": "−20c"}


def test_D19_a_chains_profit_counts_once_on_its_buy():
    buy = act("f", "buy", 210, FRI + 14 * HOUR + 10 * MINUTE, in_chain=True)
    post = act("g", "post", 95, FRI + DAY + 10 * HOUR, 1.0, parent_id="f", in_chain=True, price=45)
    view = plan_views.week_view([buy, post], NOW, 0, LOOKS, GOLD)
    [sat_post] = view["days"][1]["rows"]
    assert (sat_post["net"], sat_post["note"]) == ("0c", "profit counted on the Fri 14:10 buy")
    assert view["total"] == "+2s 10c"


def test_D37_a_chains_post_names_its_peak_and_where_its_profit_was_counted():
    buy = act("f", "buy", 210, FRI + 14 * HOUR + 10 * MINUTE, in_chain=True)
    post = act("g", "post", 95, FRI + DAY + 10 * HOUR, 1.0, parent_id="f", in_chain=True, price=45,
               peak_ms=FRI + DAY + 18 * HOUR)
    [sat_post] = plan_views.week_view([buy, post], NOW, 0, LOOKS, GOLD)["days"][1]["rows"]
    assert sat_post["note"] == "for the 18:00 peak · profit counted on the Fri 14:10 buy"


def test_D38_today_says_which_peak_each_post_is_for_and_when_a_chain_posts():
    at = FRI + 14 * HOUR + 10 * MINUTE
    buy = act("f", "buy", 210, at, in_chain=True)
    flip_post = act("g", "post", 95, FRI + DAY + 10 * HOUR, 1.0, parent_id="f", in_chain=True, peak_ms=FRI + DAY + 18 * HOUR)
    craft = act("k", "craft", 300, at, in_chain=True, key=BAR, qty=1, price=None)
    craft_post = act("q", "post", 300, FRI + DAY + 11 * HOUR, 0.8, parent_id="k", in_chain=True, key=BAR,
                     peak_ms=FRI + DAY + 19 * HOUR)
    wool = act("w", "post", 100, at, 0.9, peak_ms=FRI + 20 * HOUR)
    rows = plan_views.today_view([buy, flip_post, craft, craft_post, wool], at, 0, 0.0, LOOKS, GOLD)["rows"]
    notes = {r["id"]: r["note"] for r in rows}
    assert notes == {"f": "Flip: post Sat 10:00, for the 18:00 peak", "k": "Then: post Sat 11:00, for the 19:00 peak",
                     "w": "for the 20:00 peak"}


def test_D39_a_chains_mat_buy_says_what_it_is_for_and_a_hold_shows_no_net():
    looks = {**LOOKS, 5000: ItemLook("Basic Campfire Kit", 1)}
    wood = act("w", "buy", 0, NOW, in_chain=True, key=BAR, qty=7, price=38)
    kit = act("k", "craft", 732, NOW + MINUTE, parent_id="w", in_chain=True, key=5000, qty=7, price=None)
    eggs = act("e", "hold", 0.0, NOW, qty=2, price=None)
    week = plan_views.week_view([wood, kit, eggs], NOW, 0, looks, GOLD)
    rows = {r["id"]: r for r in week["days"][0]["rows"]}
    assert (rows["w"]["net"], rows["w"]["note"]) == ("—", "for crafting Basic Campfire Kit")
    assert (rows["e"]["net"], rows["e"]["note"]) == ("—", "hold for a later week")
    assert week["total"] == "+7s 32c"


def test_D20_once_the_buy_has_left_the_week_the_post_carries_its_own_net():
    buy = act("f", "buy", 210, FRI + 14 * HOUR + 10 * MINUTE, in_chain=True, status="done")
    post = act("g", "post", 95, FRI + DAY + 10 * HOUR, 1.0, parent_id="f", in_chain=True, price=45)
    view = plan_views.week_view([buy, post], FRI + DAY + 9 * HOUR, 0, LOOKS, GOLD)     # Sat: Fri has left
    assert view["days"][0]["rows"][0]["net"] == "+95c"


def test_D21_a_post_of_found_linen_nets_its_whole_sale():
    post = act("p", "post", 5 * 45 * 0.95, NOW, 1.0, qty=5, price=45)                  # 213.75c, paid nothing
    [row] = plan_views.today_view([post], NOW, 0, 0.5, LOOKS, GOLD)["rows"]
    assert row["net"] == "+2s 14c"


def test_steps_carry_no_net_and_crafts_show_their_products_chance():
    buy = act("m", "buy", 0, NOW, in_chain=True, key=BAR, qty=2, price=1000)
    craft = act("k", "craft", 1800, NOW + MINUTE, parent_id="m", in_chain=True, qty=1, price=None)
    post = act("q", "post", 1800, NOW + DAY, 0.8, parent_id="m", in_chain=True, peak_ms=NOW + DAY + 8 * HOUR)
    view = plan_views.today_view([buy, craft, post], NOW, 0, 0.5, LOOKS, GOLD)
    nets = {r["action"]: (r["net"], r["chance"]) for r in view["rows"]}
    assert nets == {"buy": ("—", "—"), "craft": ("+18s", "80%")}
    week = plan_views.week_view([buy, craft, post], NOW, 0, LOOKS, GOLD)
    assert week["categories"] == {"Crafts": "+18s"}
    assert week["days"][1]["rows"][0]["note"] == "for the 22:30 peak · profit counted on the Fri 14:31 craft"


def test_the_flip_tab_shows_cost_roi_and_the_peak():
    buy = act("f", "buy", 730, NOW, in_chain=True, qty=40, price=70, expected=3530)
    post = act("g", "post", 730, FRI + DAY + 11 * HOUR, 0.88, parent_id="f", in_chain=True, qty=40, price=60,
               peak_ms=FRI + DAY + 19 * HOUR)
    lone = act("h", "post", 100, NOW, 0.5)
    [row] = plan_views.flip_view([buy, post, lone], 0, LOOKS, GOLD)["rows"]
    assert (row["buy_total"], row["profit"], row["roi"], row["sell_slot"]) == ("28s", "+7s 30c", "26%", "Sat 19:00")
    assert (row["seasonality"], row["why"], row["chance"]) == ("72", "Sat peak +22% over its recent median", "88%")


def random_plan(rng):
    """Random actions over the week: sales, flips and craft chains, some done."""
    out = []
    for n in range(rng.randint(1, 12)):
        at = NOW + rng.randint(-14, 160) * HOUR
        if rng.random() < 0.4:
            out.append(act(f"s{n}", rng.choice(["post", "vendor", "de"]), rng.randint(-50, 5000), at, rng.random()))
            continue
        first, crafted = f"c{n}", rng.random() < 0.5
        # A flip's buy carries the profit; a craft chain's mat buy carries none (its craft does).
        out.append(act(first, "buy", 0 if crafted else rng.randint(1, 3000), at, in_chain=True))
        if crafted:
            out.append(act(f"k{n}", "craft", rng.randint(1, 3000), at + MINUTE, parent_id=first, in_chain=True))
        out.append(act(f"p{n}", "post", rng.randint(1, 3000), at + rng.randint(1, 72) * HOUR, rng.random(),
                       parent_id=first, in_chain=True))
    return out


def test_VW1_totals_add_up_and_each_chains_profit_counts_once():
    for i, actions in enumerate(cases(SEED_VW1, random_plan, 300)):
        view = plan_views.week_view(actions, NOW, 0, LOOKS, GOLD)
        sums = view["sums"]
        for day in view["days"]:                       # each day's total is the sum of its rows, and shows as such
            assert day["cents"] == sum(r["sort"]["net"] for r in day["rows"]), (SEED_VW1, i)
            assert day["total"] == signed_money(day["cents"]), (SEED_VW1, i)
        assert sum(d["cents"] for d in view["days"]) == sums["total"] == sum(sums["categories"].values()) \
            == sum(sums["characters"].values()), (SEED_VW1, i)
        assert view["total"] == signed_money(sums["total"]), (SEED_VW1, i)
        rows = [r for d in view["days"] for r in d["rows"]]
        by_chain: dict[str, int] = {}
        for a in actions:
            if a.in_chain and plan_views.role(a) in ("profit", "follow"):
                row = next((r for r in rows if r["id"] == a.action_id), None)
                if row and row["sort"]["net"]:
                    by_chain[plan_views.chain_id(a)] = by_chain.get(plan_views.chain_id(a), 0) + 1
        assert all(n == 1 for n in by_chain.values()), (SEED_VW1, i)
        open_rows = plan_views.today_view(actions, NOW, 0, 0.0, LOOKS, GOLD)["rows"]
        assert [r["sort"]["net"] for r in open_rows] == sorted((r["sort"]["net"] for r in open_rows), reverse=True)
