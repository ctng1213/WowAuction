"""engine/cooldowns.py: the curated cooldown catalog (SPEC §3.4F.1; CD1, CD2)."""
from __future__ import annotations

import copy

from ahsync.engine import cooldowns
from ahsync.recommend.cooldowns_page import CATALOG_PATH, read_catalog

ARCANITE = {"spell": 17187, "name": "Transmute: Arcanite", "profession": "Alchemy", "skill": 275,
            "cooldown_s": 172800, "group": "transmute", "product": 12360, "yield_min": 1, "yield_max": 1,
            "reagents": [[12363, 1], [12359, 1]], "url": "https://www.wowhead.com/classic/spell=17187"}
MOONCLOTH = {"spell": 18560, "name": "Mooncloth", "profession": "Tailoring", "skill": 250, "cooldown_s": 345600,
             "product": 14342, "yield_min": 1, "yield_max": 1, "reagents": [[14256, 2]]}
SALT = {"spell": 19566, "name": "Salt Shaker", "profession": "Leatherworking", "skill": 250, "cooldown_s": 259200,
        "product": 15409, "yield_min": 1, "yield_max": 1, "reagents": [[8150, 1]], "tool": 15846, "tool_use": True}


def with_(row: dict, **changes) -> dict:
    """A copy of a row with some fields changed (None removes the field)."""
    out = copy.deepcopy(row)
    for field, value in changes.items():
        if value is None:
            out.pop(field, None)
        else:
            out[field] = value
    return out


def test_CD1_the_shipped_catalog_loads_with_a_status_line_and_only_catalog_items_are_cooldown_items():
    rows, version, unread = read_catalog(CATALOG_PATH)
    kept, skipped = cooldowns.catalog(rows)
    assert (unread, version, skipped) == ("", 1, [])
    assert len(kept) == 15
    assert cooldowns.catalog_text(kept, skipped) == "cooldown catalog: 15 recipes"
    assert [r.spell for r in kept] == sorted(r.spell for r in kept)
    products = {r.product for r in kept}
    assert 12360 in products and 12359 not in products     # Arcanite Bar is; Thorium Bar (a mat with a price) isn't
    assert {r.profession for r in kept} == {"Alchemy", "Engineering", "Leatherworking", "Tailoring"}
    assert [r.name for r in kept if r.tool_use] == ["Salt Shaker", "SnowMaster 9000"]
    transmutes = [r for r in kept if r.name.startswith("Transmute:")]
    assert len(transmutes) == 12 and {r.tool for r in transmutes} == {9149}       # all need a Philosopher's Stone
    assert [r.name for r in transmutes if r.group != "transmute"] == ["Transmute: Elemental Fire"]   # its own 10 m


def test_CD1_a_row_reads_into_a_recipe_with_its_mats_by_key():
    kept, skipped = cooldowns.catalog([ARCANITE, SALT])
    assert skipped == []
    arcanite, salt = kept                                       # by spell id
    assert arcanite == cooldowns.CooldownRecipe(17187, "Transmute: Arcanite", "Alchemy", 275, 172800, 12360, 1, 1,
                                                ((12359, 1), (12363, 1)), "transmute", None, False,
                                                "https://www.wowhead.com/classic/spell=17187", None)
    assert (salt.tool, salt.tool_use, salt.group) == (15846, True, None)


def test_CD2_bad_rows_are_skipped_with_a_status_line_and_the_others_still_show():
    rows = [ARCANITE, MOONCLOTH, SALT,
            with_(ARCANITE, spell=99001, name="Transmute: Oddity", profession="Alchemyy"),
            with_(MOONCLOTH, spell=99002, skill=350),
            with_(ARCANITE, name="Transmute: Arcanite again"),
            with_(SALT, spell=99003, name="Salt Shaker copy", product=None)]
    kept, skipped = cooldowns.catalog(rows)
    assert [r.spell for r in kept] == [17187, 18560, 19566]
    assert cooldowns.catalog_text(kept, skipped) == (
        "cooldown catalog: 4 rows skipped (Transmute: Oddity: unknown profession 'Alchemyy'; Mooncloth: skill 350 "
        "outside 1–300; Transmute: Arcanite again: duplicate spell 17187; Salt Shaker copy: no product)")


def test_CD2_each_rule_names_its_reason():
    cases = [(with_(SALT, cooldown_s=0), "cooldown 0 not positive"),
             (with_(SALT, reagents=[]), "no reagents list"),
             (with_(SALT, reagents=[[8150, 1], [8150, 2]]), "no reagents list"),
             (with_(SALT, reagents=[[8150, 0]]), "no reagents list"),
             (with_(SALT, yield_min=2, yield_max=1), "yield 2–1 not valid"),
             (with_(SALT, tool="shaker"), "tool 'shaker' not an item"),
             (with_(SALT, tool=None), "a tool use without a tool"),
             (with_(SALT, skill=True), "skill True outside 1–300"),
             (with_(SALT, spell=None), "no spell or name"),
             ("not a row", "not a row")]
    for raw, why in cases:
        kept, skipped = cooldowns.catalog([raw])
        assert kept == [] and skipped[0].endswith(why), (raw, skipped)
    assert cooldowns.catalog(["x"])[1] == ["?: not a row"]


def test_CD2_a_group_that_mixes_professions_is_skipped_whole():
    odd = with_(MOONCLOTH, group="transmute")
    kept, skipped = cooldowns.catalog([ARCANITE, odd, SALT])
    assert [r.spell for r in kept] == [19566]
    assert skipped == ["Transmute: Arcanite: group 'transmute' mixes professions",
                       "Mooncloth: group 'transmute' mixes professions"]


def test_the_status_line_counts_in_the_singular():
    kept, _ = cooldowns.catalog([SALT])
    assert cooldowns.catalog_text(kept, []) == "cooldown catalog: 1 recipe"
    assert cooldowns.catalog_text([], ["Salt: no product"]) == "cooldown catalog: 1 row skipped (Salt: no product)"


def test_can_make_names_a_missing_tool_and_an_unread_cooldown():
    shaker, = [r for r in cooldowns.catalog([SALT])[0]]
    needs = cooldowns.Standing("B", "Brugan", 262, "needs_item")
    assert cooldowns.can_make(shaker, needs, 0, {15846: "Salt Shaker"}) == ("no", "needs Salt Shaker")
    assert cooldowns.can_make(shaker, needs, 0, {}) == ("no", "needs its tool")
    assert cooldowns.can_make(shaker, cooldowns.Standing("B", "Brugan", 262, "not_read"), 0, {}) == \
        ("no", "cooldown not read")


def test_catalog_keys_are_every_product_mat_and_tool():
    kept, _ = cooldowns.catalog([ARCANITE, MOONCLOTH, SALT])
    assert cooldowns.catalog_keys(kept) == {12360, 12359, 12363, 14342, 14256, 15409, 8150, 15846}
