"""Tests for ahsync/ingest/luadata.py: the SavedVariables Lua parser (SPEC §2.2)."""
from __future__ import annotations

import pytest

from ahsync.state.luadata import SavedVarsError, SavedVarsParseError, parse_lua


def test_parses_wow_style_tables_with_array_comments():
    text = '\nX = {\n\t["a"] = 1,\n\t[2663] = {\n\t},\n\t"first", -- [1]\n\t{\n\t\t["b"] = true,\n\t}, -- [2]\n}\n'
    assert parse_lua(text) == {"X": {"a": 1, 2663: {}, 1: "first", 2: {"b": True}}}


def test_parses_numbers_negatives_floats_hex_and_exponents():
    text = "X = { 42, -7, 0.25, -1.5, 1e3, 0x1F, 1760000000000, .5 }"
    assert parse_lua(text)["X"] == {1: 42, 2: -7, 3: 0.25, 4: -1.5, 5: 1000.0, 6: 31, 7: 1760000000000, 8: 0.5}


def test_parses_string_escapes_as_utf8_bytes():
    text = r'''X = { "a\"b", 'it\'s', "tab\tnl\n", "back\\slash", "\195\169", "caf\195\169", "\65\066" }'''
    assert parse_lua(text)["X"] == {1: 'a"b', 2: "it's", 3: "tab\tnl\n", 4: "back\\slash",
                                     5: "é", 6: "café", 7: "AB"}


def test_parses_raw_utf8_text_in_strings():
    assert parse_lua('X = "Brügan"') == {"X": "Brügan"}


def test_backslash_newline_in_a_string_is_a_newline():
    assert parse_lua('X = "a\\\nb"') == {"X": "a\nb"}


def test_nil_fields_are_left_out_but_still_take_an_array_slot():
    assert parse_lua("X = { a = nil, 1, nil, 3 }")["X"] == {1: 1, 3: 3}


def test_name_keys_semicolons_comments_and_trailing_separators():
    text = "-- header\nX = { a = 1; b = 'two', --[[ long\ncomment ]] [1.0] = false, }\nY = nil"
    assert parse_lua(text) == {"X": {"a": 1, "b": "two", 1: False}, "Y": None}


def test_later_assignment_wins_and_several_globals_parse():
    assert parse_lua("A = 1\nB = { }\nA = 2;") == {"A": 2, "B": {}}


def test_empty_file_has_no_globals():
    assert parse_lua("\n") == {}


@pytest.mark.parametrize("text, line, column", [
    ("X = {\n\t[\"a\"] = 1,\n", 3, 1),          # half-written: file ends inside the table
    ("X = { 1 2 }", 1, 9),                     # missing separator
    ("X = 'unterminated", 1, 5),
    ("X = { [nil] = 1 }", 1, 7),
    ("X = foo", 1, 5),                         # identifiers are not values in SavedVariables
    ("= 1", 1, 1),
    ("true = 1", 1, 1),
    ("X = { [1] 2 }", 1, 11),                  # missing '='
    ('X = "\\q"', 1, 5),                       # unknown escape
    ('X = "\\300"', 1, 5),                     # byte escape above 255
])
def test_unparsable_text_raises_with_position(text, line, column):
    with pytest.raises(SavedVarsParseError) as caught:
        parse_lua(text)
    assert (caught.value.line, caught.value.column) == (line, column)
    assert isinstance(caught.value, SavedVarsError)
