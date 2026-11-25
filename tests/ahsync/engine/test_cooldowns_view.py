"""engine/cooldowns_view.py: the Cooldowns page (SPEC §3.4F.4-§3.4F.6; CD4-CD10, CD13, CD15-CD21)."""
from __future__ import annotations

from ahsync.engine.cooldowns_view import PriceFact, export_text, render
from tests.support import cooldowns_world as cw
from tests.support.cooldowns_world import HOUR, NOW, base, row

ARCANITE, EARTH_TO_WATER, UNDEATH_TO_WATER, WATER_TO_AIR = 17187, 17561, 17563, 17562
TRUESILVER, GOLD, FIRE, WATER_TO_UNDEATH, MOONCLOTH, SALT = 11480, 11479, 25146, 17564, 18560, 19566


def page(**kwargs) -> dict:
    """The world's page rendered now."""
    return render(base(**kwargs), NOW)


def who(payload: dict, spell: int) -> list[str]:
    """A row's Who lines."""
    return [line["text"] for line in row(payload, spell)["who"]["lines"]]


def test_CD4_arcanite_nets_13g_55s_a_use_and_6g_77s_50c_a_day():
    r = row(base(), ARCANITE)
    assert (r["mats"], r["net"], r["per_day"]) == (292000, 135500, 67750)
    cells = row(page(), ARCANITE)
    assert (cells["mats"]["text"], cells["net"]["text"], cells["per_day"]["text"]) == ("29g 20s", "13g 55s", "6g 77s 50c")
    assert export_text(r) == "292000;135500;67750"


def test_CD5_yield_three_a_four_day_and_a_three_day_cooldown_and_ten_minutes_give_no_profit_per_day():
    fire, moon, salt = (row(base(), s) for s in (FIRE, MOONCLOTH, SALT))
    assert (fire["net"], fire["per_day"]) == (19200, None)
    assert row(page(), FIRE)["per_day"] == {"text": "—", "neg": False,
                                            "hover": "not limited by the cooldown: limited by the mats"}
    assert row(page(), FIRE)["cooldown"]["text"] == "10 m"
    assert (moon["net"], moon["per_day"]) == (139000, 34750)
    assert (salt["net"], salt["per_day"]) == (41250, 13750)
    assert row(page(), SALT)["per_day"]["text"] == "1g 37s 50c"


def test_CD6_a_loss_is_red_a_mat_without_a_price_blanks_the_money_and_an_unpriced_product_sorts_last():
    undeath = row(page(), WATER_TO_UNDEATH)
    assert (undeath["net"], undeath["per_day"]["text"]) == ({"text": "−2g 5s", "neg": True}, "−2g 5s")
    assert undeath["per_day"]["neg"]
    no_water = base(price_facts=cw.prices({7080: PriceFact(None)}))
    assert [row(no_water, WATER_TO_UNDEATH)[k] for k in ("mats", "net", "per_day")] == [None, None, None]
    assert export_text(row(no_water, WATER_TO_UNDEATH)) == ";;"
    assert row(render(no_water, NOW), WATER_TO_AIR)["price"]["text"] == "2g 80s"
    assert base()["rows"][-1]["price"] is None


def test_CD7_a_price_nine_days_old_is_muted_with_its_age_and_still_sorts_by_its_value():
    fire = row(page(), FIRE)["price"]
    assert fire == {"text": "1g 20s", "muted": True, "sub": "9 d old"}
    by_price = sorted(page()["rows"], key=lambda r: -(r["sort"]["price"] or 0))   # the AH price header (stable)
    order = [r["spell"] for r in by_price]
    assert order.index(GOLD) < order.index(FIRE) < order.index(WATER_TO_UNDEATH)
    default = [r["spell"] for r in base()["rows"]]
    assert default.index(17565) < default.index(FIRE) < default.index(21935)   # after the Essence of Earth rows


def test_CD8_the_default_order_is_by_profit_per_day_then_ah_price_then_recipe_name_then_spell():
    names = [(r["item"], r["recipe"]) for r in base()["rows"]]
    assert names == [
        ("Arcanite Bar", "Transmute: Arcanite"), ("Mooncloth", "Mooncloth"),
        ("Essence of Water", "Transmute: Undeath to Water"), ("Refined Deeprock Salt", "Salt Shaker"),
        ("Gold Bar", "Transmute: Iron to Gold"), ("Truesilver Bar", "Transmute: Mithril to Truesilver"),
        ("Essence of Water", "Transmute: Earth to Water"), ("Essence of Air", "Transmute: Water to Air"),
        ("Essence of Undeath", "Transmute: Water to Undeath"),
        ("Essence of Earth", "Transmute: Fire to Earth"), ("Essence of Earth", "Transmute: Life to Earth"),
        ("Elemental Fire", "Transmute: Elemental Fire"),
        ("Snowball", "SnowMaster 9000"), ("Essence of Fire", "Transmute: Air to Fire"),
        ("Living Essence", "Transmute: Earth to Life")]
    assert [round(r["per_day"]) for r in base()["rows"][:9]] == [
        67750, 34750, 18500, 13750, 8350, 6975, 4500, -3400, -20500]


def standing(payload: dict, spell: int, name: str) -> dict:
    """One character's staged standing for a row."""
    return next(st for st in row(payload, spell)["standings"] if st["name"] == name)


def test_CD9_one_use_locks_the_whole_transmute_group_and_elemental_fire_keeps_its_own_timer():
    p = page()
    assert who(p, ARCANITE)[:2] == ["Amaya Ready", "Tessa 1 d 2 h"]
    assert who(p, UNDEATH_TO_WATER)[:2] == ["Amaya Ready", "Tessa 1 d 2 h"]
    assert who(p, EARTH_TO_WATER)[0] == "Tessa 1 d 2 h"
    assert who(p, FIRE)[0] == "Tessa Ready"
    assert who(p, GOLD)[:2] == ["Amaya Ready", "Sorvin Ready"]
    assert [r["cooldown"]["sub"] for r in p["rows"] if r["profession"] == "Alchemy" and r["spell"] != FIRE] == ["shared"] * 11
    assert row(p, FIRE)["cooldown"]["sub"] == ""
    assert row(p, ARCANITE)["cooldown"]["hover"].startswith("one cooldown for all 11 transmutes")


def test_CD10_below_skill_not_learned_needs_item_and_not_read():
    p = page()
    assert standing(base(), EARTH_TO_WATER, "Amaya")["state"] == "not_learned"
    assert "Amaya not learned" in who(p, EARTH_TO_WATER)
    assert "Amaya needs 300 (280)" in who(p, FIRE)
    assert "Mira needs item" in who(p, SALT)
    unread = cw.with_(cw.SORVIN, ready={})
    assert standing(base(holders=[cw.AMAYA, unread]), GOLD, "Sorvin")["state"] == "not_read"
    stoneless = cw.with_(cw.AMAYA, bags={12359: 1, 12363: 1})
    staged = base(holders=[stoneless])
    assert standing(staged, ARCANITE, "Amaya")["state"] == "needs_item"
    assert standing(staged, EARTH_TO_WATER, "Amaya")["state"] == "not_learned"      # not learned shows first


def test_CD11_a_tool_in_the_bank_counts_one_in_the_mail_or_on_another_character_does_not():
    banked = cw.with_(cw.BRUGAN, bags={8150: 1}, bank={15846: 1}, ready={SALT: (NOW - HOUR, NOW - 72 * HOUR)})
    st = standing(base(holders=[banked, cw.MIRA]), SALT, "Brugan")
    assert (st["state"], st["bank"]) == ("timed", True)
    assert row(render(base(holders=[banked, cw.MIRA]), NOW), SALT)["can_make"]["text"] == "Yes (bank)"
    assert standing(base(holders=[banked, cw.MIRA]), SALT, "Mira")["state"] == "needs_item"


def test_CD13_can_make_in_amayas_and_tessas_rows():
    staged = base(holders=[cw.AMAYA])
    texts = {s: row(render(staged, NOW), s)["can_make"] for s in (ARCANITE, TRUESILVER, WATER_TO_AIR, GOLD,
                                                                   EARTH_TO_WATER, FIRE)}
    assert texts[ARCANITE]["text"] == "Yes" and texts[TRUESILVER]["text"] == "Yes (bank)"
    assert texts[WATER_TO_AIR]["hover"] == "Amaya: missing 1 Essence of Water"
    assert texts[GOLD]["hover"] == "Amaya: missing 1 Iron Bar"
    assert texts[EARTH_TO_WATER]["hover"] == "Amaya: not learned"
    assert texts[FIRE]["hover"] == "Amaya: needs skill 300"
    tessa = render(base(holders=[cw.TESSA]), NOW)
    assert row(tessa, ARCANITE)["can_make"]["hover"] == "Tessa: on cooldown 1 d 2 h"
    assert row(tessa, FIRE)["can_make"]["text"] == "Yes"


def test_CD15_who_lists_every_character_with_the_profession_best_first():
    p = page()
    assert who(p, ARCANITE) == ["Amaya Ready", "Tessa 1 d 2 h", "Sorvin needs 275 (230)"]
    assert who(p, SALT) == ["Brugan 1 d 4 h", "Mira needs item"]
    assert [line["cls"] for line in row(p, SALT)["who"]["lines"]] == ["", "amber"]
    assert who(p, EARTH_TO_WATER) == ["Tessa 1 d 2 h", "Amaya not learned", "Sorvin needs 275 (230)"]
    assert who(p, 21935) == ["—"]                                                  # nobody has Engineering


def test_CD16_past_three_characters_who_folds_into_a_count_and_a_breakdown():
    def alchemist(name, known=(17187,), ready_in=None, skill=300):
        ready = {} if ready_in is None else {17187: (NOW + ready_in, NOW - HOUR)}
        return cw.holder(f"{name}-Grommash", {"Alchemy": skill}, known, bags={9149: 1}, ready=ready)
    seven = [alchemist("Anwen", ready_in=-HOUR), alchemist("Ilsa", ready_in=-HOUR),
             alchemist("Corin", ready_in=4 * HOUR + 20 * 60_000), alchemist("Dara", ready_in=9 * HOUR),
             alchemist("Edda", ready_in=20 * HOUR), alchemist("Fenn", known=()), alchemist("Gil", skill=200)]
    cell = row(render(base(holders=seven), NOW), ARCANITE)["who"]
    assert [line["text"] for line in cell["lines"]] == ["Anwen Ready", "Ilsa Ready", "Corin 4 h 20 m"]
    assert cell["summary"] == "2 Ready of 7"
    assert cell["more"] == "+4 more: 2 cooling down, 1 not learned, 1 below 275"
    assert cell["hover"].splitlines()[-1] == "Gil needs 275 (200)"


def test_CD17_the_three_filters():
    views = base()["views"]
    assert views["Leatherworking|all"]["keys"] == [SALT]
    no_tailors = base(holders=cw.without("Keldor", cw.without("Mira")))["views"]
    assert MOONCLOTH in no_tailors["|all"]["keys"] and MOONCLOTH not in no_tailors["|mine"]["keys"]
    thin = base(holders=cw.without("Tessa", cw.without("Mira")))["views"]["|skill"]["keys"]
    assert FIRE not in thin and MOONCLOTH not in thin and EARTH_TO_WATER in thin
    assert 21935 not in base()["views"]["|skill"]["keys"]                         # skill implies mine


def test_CD18_an_empty_result_says_which_filter_emptied_it():
    views = base(holders=[cw.KELDOR])["views"]
    assert views["Tailoring|skill"] == {"keys": [], "notice": "No cooldown recipe matches. Keldor has Tailoring 245; "
                                        "Mooncloth needs 250. Turn off \"My skill level only\" to see it."}
    assert views["Tailoring|mine"]["keys"] == [MOONCLOTH]
    assert views["Alchemy|mine"]["notice"] == ("No cooldown recipe matches: none of your characters has Alchemy. "
                                               "Turn off \"Just my characters\" to see it.")


def test_CD19_the_dashboards_can_make_is_any_characters_with_who_on_hover():
    p = page()
    assert row(p, ARCANITE)["can_make"]["hover"].startswith("Amaya (bags)")
    assert row(p, GOLD)["can_make"]["text"] == "Yes" and row(p, GOLD)["can_make"]["hover"].startswith("Sorvin (bags)")
    assert row(p, TRUESILVER)["can_make"]["text"] == "Yes (bank)"
    assert row(p, FIRE)["can_make"]["text"] == "Yes"
    assert row(p, MOONCLOTH)["can_make"]["hover"] == "Keldor: needs skill 250; Mira: on cooldown 2 d 3 h"
    ranks = [row(p, s)["sort"]["can_make"] for s in (ARCANITE, TRUESILVER, MOONCLOTH)]
    assert ranks == [0, 1, 2]
    assert row(p, 21935)["can_make"]["hover"] == "none of your characters has Engineering"


def test_CD20_the_page_is_made_at_each_request_from_the_staged_ready_times():
    staged = base()
    assert who(render(staged, NOW), ARCANITE)[1] == "Tessa 1 d 2 h"
    assert who(render(staged, NOW + HOUR), ARCANITE)[1] == "Tessa 1 d 1 h"
    assert who(render(staged, NOW + 27 * HOUR), ARCANITE)[:2] == ["Amaya Ready", "Tessa Ready"]


def test_CD21_the_profession_filter_lists_all_twelve_and_one_without_rows_says_so():
    p = page()
    assert [label for _, label in p["professions"]] == [
        "All professions", "Alchemy", "Blacksmithing", "Cooking", "Enchanting", "Engineering", "First Aid", "Fishing",
        "Herbalism", "Leatherworking", "Mining", "Skinning", "Tailoring"]
    assert p["views"]["Blacksmithing|all"] == {"keys": [], "notice": "Blacksmithing: no cooldown recipes."}
    assert p["header"] == "15 recipes · 6 characters"
