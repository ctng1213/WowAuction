"""recommend/cooldowns_page.py: reading the cooldown catalog and its Status line (SPEC §3.4F.1; CD1, CD2)."""
from __future__ import annotations

import json

from ahsync.recommend import cooldowns_page as page

ROW = {"spell": 19566, "name": "Salt Shaker", "profession": "Leatherworking", "skill": 250, "cooldown_s": 259200,
       "product": 15409, "yield_min": 1, "yield_max": 1, "reagents": [[8150, 1]], "tool": 15846}


class FakeDb:
    """The Status lines only."""

    def __init__(self):
        """No lines yet."""
        self.status: dict[str, tuple] = {}

    def set_status(self, topic, level, kind, data, at):
        """Keep the line."""
        self.status[topic] = (level, kind, data, at)


def write(tmp_path, document) -> object:
    """A catalog file in tmp_path."""
    path = tmp_path / "cooldown_catalog.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
    return path


def test_CD1_a_good_catalog_sets_an_info_line_with_its_count(tmp_path):
    db = FakeDb()
    kept, version = page.load_catalog(db, 1000, write(tmp_path, {"version": 3, "recipes": [ROW]}))
    assert [r.spell for r in kept] == [19566] and version == 3
    assert db.status["cooldown_catalog"] == ("info", "cooldown_catalog", {"text": "cooldown catalog: 1 recipe"}, 1000)


def test_CD2_a_skipped_row_turns_the_line_into_a_warning(tmp_path):
    db = FakeDb()
    bad = dict(ROW, spell=1, name="Odd", skill=350)
    kept, _ = page.load_catalog(db, 1000, write(tmp_path, {"version": 1, "recipes": [ROW, bad]}))
    assert len(kept) == 1
    level, _, data, _ = db.status["cooldown_catalog"]
    assert level == "warn" and data["text"] == "cooldown catalog: 1 row skipped (Odd: skill 350 outside 1–300)"


def test_CD2_an_unreadable_catalog_leaves_the_page_empty_and_nothing_raises(tmp_path):
    db = FakeDb()
    assert page.load_catalog(db, 1000, tmp_path / "none.json") == ([], None)
    assert db.status["cooldown_catalog"][0] == "warn"
    assert db.status["cooldown_catalog"][2]["text"] == ("cooldown catalog not read (FileNotFoundError): the Cooldowns "
                                                        "page is empty")
    assert page.read_catalog(write(tmp_path, "{"))[2].startswith("cooldown catalog not read (JSONDecodeError)")
    assert page.read_catalog(write(tmp_path, {"recipes": {}})) == ([], None, "cooldown catalog not read (no recipes "
                                                                              "list): the Cooldowns page is empty")
    assert page.read_catalog(write(tmp_path, {"version": "1", "recipes": []})) == ([], None, "")


class WorldDb(FakeDb):
    """Two characters' stored data and the world's prices, as the database gives them."""

    def __init__(self):
        """Amaya (an alchemist with a stone, Arcanite ready) and Brugan (a leatherworker with a banked shaker)."""
        super().__init__()
        self.now = 1_760_000_000_000

    def current_price(self, market, key):
        """The world's price, seen an hour ago; Thorium Bar too, though it is only a mat."""
        from ahsync.history.reads import CurrentPrice
        price = {12360: 450000, 12359: 12000, 12363: 280000}.get(key)
        return CurrentPrice(key, price, 5, self.now - 3_600_000, "ahf") if price else None

    def known_spells(self, char_ids):
        """Amaya knows Arcanite."""
        return {"Amaya-Grommash": frozenset({17187})}

    def recipe_cooldowns(self, char_ids):
        """Arcanite ready an hour ago; the Salt Shaker in 2 days."""
        return {("Amaya-Grommash", 17187): (self.now - 3_600_000, "c", self.now - 49 * 3_600_000),
                ("Brugan-Grommash", 19566): (self.now + 172_800_000, "g", self.now)}

    def holdings(self, char_ids):
        """Amaya's mats and stone in her bags (two stacks of Thorium); Brugan's shaker in his bank."""
        return [("Amaya-Grommash", "bags", 9149, 1, True), ("Amaya-Grommash", "bags", 12359, 1, False),
                ("Amaya-Grommash", "bags", 12359, 1, True), ("Amaya-Grommash", "bags", 12363, 1, False),
                ("Brugan-Grommash", "bags", 8150, 1, False), ("Brugan-Grommash", "bank", 15846, 1, True)]

    def character_sheets(self, char_ids):
        """The two characters' professions."""
        return [("Amaya-Grommash", "MAGE", 60, "Horde", "m", 0, 1, self.now, (("Alchemy", 280, 300),)),
                ("Brugan-Grommash", "WARRIOR", 60, "Horde", "m", 0, 0, self.now, (("Leatherworking", 262, 300),))]


def test_CD1_the_page_holds_only_catalog_rows_and_reads_each_characters_stored_data():
    from ahsync.history.reads import ItemInfo
    db = WorldDb()
    items = {12360: ItemInfo(12360, "Arcanite Bar", 2, 60, 7, None, None, 20)}
    staged = page.cooldowns_page(db, "m", ["Amaya-Grommash", "Brugan-Grommash"], {}, items, db.now)
    assert len(staged["rows"]) == 15 and 12359 not in {r["product"] for r in staged["rows"]}   # Thorium: a mat only
    arcanite = next(r for r in staged["rows"] if r["spell"] == 17187)
    assert (arcanite["item"], arcanite["mats"], arcanite["net"]) == ("Arcanite Bar", 292000, 135500)
    assert arcanite["standings"] == [{"char": "Amaya-Grommash", "name": "Amaya", "skill": 280, "state": "timed",
                                      "ready_at": db.now - 3_600_000, "bank": False, "missing": ""}]
    salt = next(r for r in staged["rows"] if r["spell"] == 19566)
    assert [(st["name"], st["state"], st["bank"]) for st in salt["standings"]] == [("Brugan", "timed", True)]
    assert salt["item"] == "item 15409"                                            # no name known yet
    assert staged["export"]["17187"] == "292000;135500;67750" and staged["version"] == 1
    assert db.status["cooldown_catalog"][2] == {"text": "cooldown catalog: 15 recipes"}
