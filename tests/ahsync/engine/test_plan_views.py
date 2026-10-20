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
    # The −20c action is a disenchant: a vendor sale is no Week row since D64.
    rows = [act("a", "post", 830, FRI + 16 * HOUR), act("b", "post", 11000, FRI + 17 * HOUR),
            act("c", "de", -20, FRI + 18 * HOUR, char_id="Brugan-G")]
    view = plan_views.week_view(rows, NOW, 0, LOOKS, GOLD)
    assert view["days"][0]["total"] == "+1g 18s 10c" == view["total"]
    assert view["categories"] == {"Disenchant": "−20c", "Sell owned": "+1g 18s 30c"}
    assert view["characters"] == {"Amaya": "+1g 18s 30c", "Brugan": "−20c"}
    buy = act("e", "buy", 625, FRI + 18 * HOUR, in_chain=True)                # a buy to disenchant (SPEC §3.4D.3)
    de = act("f", "de", 0.0, FRI + 18 * HOUR + MINUTE, parent_id="e", in_chain=True)      # a step: no net
    assert plan_views.week_view([buy, de], NOW, 0, LOOKS, GOLD)["categories"] == {"Disenchant": "+6s 25c"}


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
    cells = {r["id"]: (r["note"], r["lines"]) for r in rows}
    assert cells == {"f": ("", ["then: post Sat 10:00, for the 18:00 peak"]),
                     "k": ("", ["then: post Sat 11:00, for the 19:00 peak"]), "w": ("for the 20:00 peak", [])}


def test_D39_another_characters_mat_buy_says_what_it_is_for_and_a_hold_is_no_row():
    looks = {**LOOKS, 5000: ItemLook("Basic Campfire Kit", 1)}
    wood = act("w", "buy", 0, NOW, in_chain=True, key=BAR, qty=7, price=38, char_id="Brugan-G")
    kit = act("k", "craft", 732, NOW + MINUTE, parent_id="w", in_chain=True, key=5000, qty=7, price=None)
    eggs = act("e", "hold", 0.0, NOW, qty=2, price=None)
    week = plan_views.week_view([wood, kit, eggs], NOW, 0, looks, GOLD)
    rows = {r["id"]: r for r in week["days"][0]["rows"]}
    assert (rows["w"]["net"], rows["w"]["note"]) == ("—", "for crafting Basic Campfire Kit")
    assert set(rows) == {"w", "k"} and week["total"] == "+7s 32c"


def test_D41_a_vendor_buy_says_so_in_its_line_or_its_note():
    looks = {**LOOKS, 4470: ItemLook("Simple Wood", 1), 5000: ItemLook("Basic Campfire Kit", 1)}
    wood = act("w", "buy", 0, NOW, in_chain=True, key=4470, qty=7, price=38, vendor=True)
    bars = act("b", "buy", 0, NOW + MINUTE // 2, in_chain=True, parent_id="w", key=BAR, qty=2, price=1000)
    kit = act("k", "craft", 732, NOW + MINUTE, parent_id="w", in_chain=True, key=5000, qty=7, price=None)
    rows = {r["id"]: r for r in plan_views.week_view([wood, bars, kit], NOW, 0, looks, GOLD)["days"][0]["rows"]}
    assert list(rows) == ["k"] and rows["k"]["lines"] == ["buy 7 Simple Wood from a vendor · 2s 66c",
                                                          "buy 2 Copper Bar on the AH · 20s"]
    theirs = replace(wood, char_id="Brugan-G")                     # another character's step stays a row (D39)
    rows = {r["id"]: r for r in plan_views.week_view([theirs, bars, kit], NOW, 0, looks, GOLD)["days"][0]["rows"]}
    assert rows["w"]["note"] == "from a vendor · for crafting Basic Campfire Kit"


def test_T7_a_sale_below_what_you_paid_says_so_and_is_still_planned():
    looks = {**LOOKS, BAR: replace(LOOKS[BAR], paid_each=40)}
    post = act("p", "post", 650, NOW, 0.9, key=BAR, qty=20, price=35)
    rows = {r["id"]: r for r in plan_views.week_view([post], NOW, 0, looks, GOLD)["days"][0]["rows"]}
    assert rows["p"]["note"] == "below what you paid (40c)"
    above = {r["id"]: r for r in plan_views.week_view([replace(post, price=45)], NOW, 0, looks, GOLD)["days"][0]["rows"]}
    assert above["p"]["note"] == ""


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
    assert nets == {"craft": ("+18s", "80%")}                      # its buy is a line under it (D62)
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
    """Random actions over the week: sales, holds, flips and craft chains, some done."""
    out = []
    for n in range(rng.randint(1, 12)):
        at = NOW + rng.randint(-14, 160) * HOUR
        if rng.random() < 0.4:
            out.append(act(f"s{n}", rng.choice(["post", "vendor", "de", "hold"]), rng.randint(-50, 5000), at,
                           rng.random()))
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
        kinds = {a.action_id: a.kind for a in actions}
        assert not [r for r in rows + open_rows if kinds[r["id"]] in ("hold", "vendor")], (SEED_VW1, i)   # D64
        folds = plan_views.collapse(actions, actions)[2]
        assert not [r for r in rows + open_rows if r["id"] in folds], (SEED_VW1, i)    # a folded step is no row (D62)


def test_D42_the_record_line_shows_the_record_and_works_p_back_from_p_prime():
    from ahsync.engine.sell_record import Record
    stuck = Record(0, 3, 3, 3, False, True)
    assert plan_views.record_line(stuck, 4 * 0.8 / 7) == \
        "Your record (30 d): 0 of 3 sold · expired 3× while cheapest · P 80% → 46% · price −14% · qty 1/8"
    assert plan_views.record_line(Record(2, 0, 0, 0, False, False)) == "Your record (30 d): 2 of 2 sold"
    assert plan_views.record_line(Record(0, 5, 5, 5, False, True)).endswith("qty 1/32")
    looks = {**LOOKS, BAR: replace(LOOKS[BAR], record=stuck)}
    post = act("p", "post", 650, NOW, 4 * 0.8 / 7, key=BAR, qty=5, price=47)
    rows = {r["id"]: r for r in plan_views.week_view([post], NOW, 0, looks, GOLD)["days"][0]["rows"]}
    assert rows["p"]["record"].startswith("Your record (30 d): 0 of 3 sold")
    linen = act("l", "post", 650, NOW, 0.9, key=LINEN, qty=5, price=47)
    assert plan_views.week_view([linen], NOW, 0, looks, GOLD)["days"][0]["rows"][0]["record"] == ""


def test_D43_needs_a_decision_lists_the_items_flagged_not_selling():
    from ahsync.engine.sell_record import Record
    looks = {**LOOKS, BAR: replace(LOOKS[BAR], record=Record(0, 3, 3, 3, False, True)),
             LINEN: replace(LOOKS[LINEN], record=Record(1, 1, 1, 1, False, False))}
    today = plan_views.today_view([], NOW, 0, 0.0, looks, GOLD)
    assert [(d["key"], d["item"]) for d in today["decisions"]] == [(str(BAR), "Copper Bar")]
    assert today["decisions"][0]["record"].startswith("Your record (30 d): 0 of 3 sold")


def test_D58_rows_with_volume_unknown_are_shown_with_the_marker_never_hidden():
    looks = {**LOOKS, BAR: ItemLook("Copper Bar", 1, 30, None, None, None, volume_unknown=True)}
    rows = [act("a", "post", 500, NOW, 0.8), act("b", "post", 2000, NOW, 0.3),
            act("c", "post", 1000, NOW, 0.3, key=BAR)]
    for step in (0.25, 0.5, 0.75):
        view = plan_views.today_view(rows, NOW, 0, step, looks, GOLD)
        assert [r["id"] for r in view["rows"] if r["item"] == "Copper Bar"] == ["c"]
    view = plan_views.today_view(rows, NOW, 0, 0.5, looks, GOLD)
    assert [(r["net"], r["chance"], r["chance_note"]) for r in view["rows"]] == \
        [("+10s", "30%", "volume unknown"), ("+5s", "80%", "")]
    assert (view["hidden"], view["total"]) == (1, "+15s")


def test_R24_a_flip_relisted_now_says_so_on_the_flip_tab():
    buy = act("f", "buy", 657, NOW, in_chain=True, qty=20, price=10, expected=857)
    post = act("g", "post", 657, NOW, 0.9, parent_id="f", in_chain=True, qty=20, price=49)
    [row] = plan_views.flip_view([buy, post], 0, LOOKS, GOLD)["rows"]
    assert (row["sell_each"], row["sell_slot"], row["why"]) == ("49c", "Fri 14:30", "relist now, under the listings left")


# ---------------------------------------------------------------- one row per chain, mail between characters (D62-D66)

MON = MONDAY + 10 * HOUR + 38 * MINUTE              # Mon 10:38 UTC
BOWL, VIAL, SMALLFISH, ROD = 6370, 3371, 6303, 6217
CHAIN_LOOKS = {**LOOKS, BOWL: ItemLook("Fish Bowl", 1, 80, 30.0, "ahf", 40), VIAL: ItemLook("Empty Vial", 1),
               SMALLFISH: ItemLook("Raw Brilliant Smallfish", 1), ROD: ItemLook("Copper Rod", 1, 70, 6.0, "ahf", 6)}


def fish_bowl_chain() -> list[ViewAction]:
    """Amaya's Fish Bowl chain from the screenshots: 76 vials from a vendor at 20c, 76 smallfish on the AH at 75c,
    76 crafts at 10:38 carrying +12g 4s 48c, posted Fri 11:00 for the 19:00 peak."""
    head = dict(char_id="Amaya-G", in_chain=True)
    return [act("v", "buy", 0, MON - 2 * MINUTE, key=VIAL, qty=76, price=20, vendor=True, **head),
            act("f", "buy", 0, MON - MINUTE, parent_id="v", key=SMALLFISH, qty=76, price=75, **head),
            act("k", "craft", 120448, MON, parent_id="v", key=BOWL, qty=76, price=None, **head),
            act("p", "post", 120448, MONDAY + 4 * DAY + 11 * HOUR, 0.9, parent_id="v", key=BOWL, qty=76, price=2500,
                peak_ms=MONDAY + 4 * DAY + 19 * HOUR, deposit=1570, **head)]


def test_D62_today_shows_a_craft_chain_as_one_row_with_its_buys_as_lines():
    view = plan_views.today_view(fish_bowl_chain(), MON, 0, 0.5, CHAIN_LOOKS, GOLD)
    [row] = view["rows"]                                           # no BUY rows for the vial or the fish: 1 action
    assert (row["action"], row["item"], row["qty"], row["each"], row["total"], row["net"]) == \
        ("craft", "Fish Bowl", 76, "95c", "72s 20c", "+12g 4s 48c")      # 15s 20c + 57s = 72s 20c; ÷ 76 = 95c
    assert row["lines"] == ["buy 76 Empty Vial from a vendor · 15s 20c",
                            "buy 76 Raw Brilliant Smallfish on the AH · 57s",
                            "then: post Fri 11:00, for the 19:00 peak"]
    assert (row["note"], row["chance"], row["risk"], view["total"]) == ("", "90%", "2.3%", "+12g 4s 48c")   # 7220/314000
    [post] = plan_views.week_view(fish_bowl_chain(), MON, 0, CHAIN_LOOKS, GOLD)["days"][4]["rows"]
    assert (post["each"], post["total"], post["risk"]) == ("25s", "19g", "0.5%")   # a post risks its deposit, 15s 70c
    owned = [a for a in fish_bowl_chain() if a.kind != "buy"]      # every mat owned: Each and Total "—"
    [row] = plan_views.today_view(owned, MON, 0, 0.5, CHAIN_LOOKS, GOLD)["rows"]
    assert (row["each"], row["total"], row["risk"], row["lines"]) == \
        ("—", "—", "0.0%", ["then: post Fri 11:00, for the 19:00 peak"])


def test_D63_week_shows_the_chain_as_one_card_and_its_post_on_its_day():
    week = plan_views.week_view(fish_bowl_chain(), MON, 0, CHAIN_LOOKS, GOLD)
    [craft] = week["days"][0]["rows"]
    [post] = week["days"][4]["rows"]
    assert (craft["id"], craft["lines"][-1], craft["total"]) == ("k", "then: post Fri 11:00, for the 19:00 peak",
                                                                  "72s 20c")
    assert (post["action"], post["net"], post["note"], post["lines"]) == \
        ("post", "0c", "for the 19:00 peak · profit counted on the Mon 10:38 craft", [])
    assert week["total"] == "+12g 4s 48c" and [len(d["rows"]) for d in week["days"]] == [1, 0, 0, 0, 1, 0, 0]


def test_D64_holds_and_vendor_sales_are_no_rows_and_count_toward_no_total():
    sales = [act("h1", "hold", 0.0, MON, qty=2, price=None), act("h2", "hold", 0.0, MON, key=BAR, qty=3, price=None),
             act("x1", "vendor", 40, MON, qty=4, price=10), act("x2", "vendor", 25, MON, key=BAR, qty=5, price=5),
             act("x3", "vendor", 12, MON, key=VIAL, qty=3, price=4, char_id="Brugan-G"),
             act("w", "post", 500, MON, 0.9)]
    plan = sales + fish_bowl_chain()
    today = plan_views.today_view(plan, MON, 0, 0.5, CHAIN_LOOKS, GOLD)
    assert [r["id"] for r in today["rows"]] == ["k", "w"] and today["total"] == "+12g 9s 48c"   # 2 actions, no 77c
    week = plan_views.week_view(plan, MON, 0, CHAIN_LOOKS, GOLD)
    assert [r["id"] for r in week["days"][0]["rows"]] == ["k", "w"] and week["total"] == "+12g 9s 48c"
    assert week["characters"] == {"Amaya": "+12g 9s 48c"} and "Sell owned" in week["categories"]


def test_D65_week_cards_carry_the_characters_name():
    week = plan_views.week_view([act("w", "post", 500, MON, 0.9, char_id="Monstrscondm-Grommash")], MON, 0, LOOKS, GOLD)
    assert week["days"][0]["rows"][0]["character"] == "Monstrscondm"


def copper_rod_chain(mail_status: str = "pending", done_at: int | None = None) -> list[ViewAction]:
    """Amaya crafts 7 Copper Rods; Brugan mails her 6 of his Copper Bars a minute before (C22)."""
    mail = act("m", "mail", 0.0, MON - MINUTE, key=BAR, qty=6, price=None, char_id="Brugan-G", in_chain=True,
               to_char="Amaya-G", status=mail_status, done_at=done_at)
    craft = act("k", "craft", 9000, MON, parent_id="m", key=ROD, qty=7, price=None, in_chain=True)
    post = act("p", "post", 9000, MON + 5 * HOUR, 0.8, parent_id="m", key=ROD, qty=7, price=4100, in_chain=True,
               peak_ms=MON + 13 * HOUR)
    return [mail, craft, post]


def test_D66_mail_is_the_senders_row_and_a_line_under_the_crafters_row():
    rows = {r["id"]: r for r in plan_views.today_view(copper_rod_chain(), MON, 0, 0.0, CHAIN_LOOKS, GOLD)["rows"]}
    mail, craft = rows["m"], rows["k"]
    assert (mail["action"], mail["character"], mail["qty"], mail["item"], mail["net"], mail["chance"], mail["risk"]) == \
        ("mail", "Brugan", 6, "Copper Bar", "—", "—", "0.0%")
    assert mail["note"] == "to Amaya · for crafting Copper Rod"
    assert craft["character"] == "Amaya" and craft["lines"] == ["6 Copper Bar from Brugan · not mailed yet",
                                                                 "then: post Mon 15:38, for the 23:38 peak"]
    mailed = copper_rod_chain("done", MON + 2 * MINUTE)            # the mail_sent event matched it (T21)
    today = {r["id"]: r for r in plan_views.today_view(mailed, MON, 0, 0.0, CHAIN_LOOKS, GOLD)["rows"]}
    assert "m" not in today and today["k"]["lines"][0] == "6 Copper Bar from Brugan · mailed Mon 10:40"
    week = plan_views.week_view(mailed, MON, 0, CHAIN_LOOKS, GOLD)
    assert [(r["id"], r["status"]) for r in week["days"][0]["rows"]] == [("m", "done"), ("k", "pending"), ("p", "pending")]
    partly = {r["id"]: r for r in plan_views.today_view(copper_rod_chain("partial", MON + 2 * MINUTE), MON, 0, 0.0,
                                                        CHAIN_LOOKS, GOLD)["rows"]}
    assert partly["k"]["lines"][0] == "6 Copper Bar from Brugan · partly mailed Mon 10:40"
    skipped = {r["id"]: r for r in plan_views.week_view(copper_rod_chain("skipped"), MON, 0, CHAIN_LOOKS, GOLD)
               ["days"][0]["rows"]}
    assert skipped["k"]["lines"][0] == "6 Copper Bar from Brugan · skipped"
    held = [a for a in copper_rod_chain() if a.kind != "mail"]     # Amaya's snapshot holds the bars: no mail, no line
    [craft] = [r for r in plan_views.today_view(held, MON, 0, 0.0, CHAIN_LOOKS, GOLD)["rows"] if r["id"] == "k"]
    assert craft["lines"] == ["then: post Mon 15:38, for the 23:38 peak"]
