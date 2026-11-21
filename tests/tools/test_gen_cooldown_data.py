"""tools/gen_cooldown_data.py: the addon's cooldown catalog is generated from AHSync's (SPEC §3.4F.1, §4.5; CD3)."""
from __future__ import annotations

import json

import pytest

import tools.gen_cooldown_data as gen

ROW = {"spell": 19566, "name": "Salt Shaker", "profession": "Leatherworking", "skill": 250, "cooldown_s": 259200,
       "product": 15409, "yield_min": 1, "yield_max": 1, "reagents": [[8150, 1]], "tool": 15846,
       "tool_use": True}
ARCANITE = {"spell": 17187, "name": "Transmute: Arcanite", "profession": "Alchemy", "skill": 275, "cooldown_s": 172800,
            "group": "transmute", "product": 12360, "yield_min": 1, "yield_max": 1, "reagents": [[12363, 1], [12359, 1]]}


def catalog_file(tmp_path, rows, version=1):
    """A catalog JSON file in tmp_path."""
    path = tmp_path / "cooldown_catalog.json"
    path.write_text(json.dumps({"version": version, "recipes": rows}), encoding="utf-8")
    return path


def test_CD3_the_shipped_lua_matches_the_json():
    actual = gen.OUT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert gen.first_difference(gen.render(), actual) == "", "run python tools/gen_cooldown_data.py"


def test_CD3_rows_and_version_are_written_by_spell_id(tmp_path):
    text = gen.render(catalog_file(tmp_path, [ROW, ARCANITE], version=2))
    assert "AHProfit.Data.COOLDOWNS_VERSION = 2\n" in text
    rows = [line.strip() for line in text.splitlines() if line.startswith('  "')]
    assert rows == ['"17187;Transmute: Arcanite;Alchemy;275;172800;transmute;12360;1;1;12359,1/12363,1;;",',
                    '"19566;Salt Shaker;Leatherworking;250;259200;;15409;1;1;8150,1;15846;1",']


def test_CD3_a_stale_file_names_the_first_differing_spell(tmp_path):
    fresh = gen.render(catalog_file(tmp_path, [ROW, ARCANITE]))
    changed = gen.render(catalog_file(tmp_path, [dict(ROW, skill=255), ARCANITE]))
    assert gen.first_difference(changed, fresh) == "spell 19566 differs"
    newer = gen.render(catalog_file(tmp_path, [ROW, ARCANITE], version=2))
    assert gen.first_difference(newer, fresh) == "the version differs"
    assert gen.first_difference(fresh, fresh) == ""
    assert gen.first_difference(fresh, fresh.replace("Do not edit.", "Edited.")) == "line 1 differs"


def test_a_catalog_with_a_bad_row_or_no_version_is_refused(tmp_path):
    with pytest.raises(ValueError, match="would be skipped"):
        gen.render(catalog_file(tmp_path, [dict(ROW, skill=350)]))
    with pytest.raises(ValueError, match="no version"):
        gen.render(catalog_file(tmp_path, [ROW], version=0))
    with pytest.raises(ValueError, match="holds a separator"):
        gen.render(catalog_file(tmp_path, [dict(ROW, name="Salt; Shaker")]))


def test_check_mode_reports_a_stale_file(tmp_path, monkeypatch, capsys):
    out = tmp_path / "Cooldowns.lua"
    monkeypatch.setattr(gen, "OUT", out)
    monkeypatch.setattr(gen, "render", lambda: "fresh\n")
    assert gen.main(["--check"]) == 1 and "stale" in capsys.readouterr().err
    assert gen.main([]) == 0 and out.read_text(encoding="utf-8") == "fresh\n"
    assert gen.main(["--check"]) == 0
