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
