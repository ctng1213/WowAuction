"""Tests for tools/record_fixtures.py: recording AHledger (SPEC §2.3, S20)."""
from __future__ import annotations

import json

from ahsync.collect.ahledger import parse_pricetable
from tests.support.ahledger_synth import TABLE_ROWS, ahf_prices, ahledger_transport
from tests.support.fake_transport import DEFAULT_MARKET
from tools import record_fixtures

NAMES = ["markets.json", "prices_2589_7d.json", "prices_2589_90d.json", "pricetable.txt"]


def test_S20_recording_ahledger_writes_four_trimmed_keyless_deterministic_fixtures(tmp_path):
    transport = ahledger_transport()
    assert len(parse_pricetable(transport.routes[f"v1/pricetable/{DEFAULT_MARKET}"], DEFAULT_MARKET).rows) == TABLE_ROWS
    first = record_fixtures.record_ahledger(DEFAULT_MARKET, transport, tmp_path / "a")
    assert sorted(p.name for p in first.iterdir()) == NAMES
    table = parse_pricetable((first / "pricetable.txt").read_bytes(), DEFAULT_MARKET)
    assert [r.key for r in table.rows] == list(ahf_prices()) and table.skipped == 0   # only the fixture items
    header = (first / "pricetable.txt").read_text(encoding="utf-8").split("\n")[0]
    assert header.endswith(f"|{len(ahf_prices())}")                    # the header count rewritten to the kept rows
    assert json.loads((first / "prices_2589_7d.json").read_text(encoding="utf-8"))["range"] == "7d"
    assert json.loads((first / "prices_2589_90d.json").read_text(encoding="utf-8"))["range"] == "90d"
    assert len(transport.requests) == 4
    assert all("authorization" not in {k.lower() for k in r.headers} for r in transport.requests)   # keyless
    second = record_fixtures.record_ahledger(DEFAULT_MARKET, ahledger_transport(), tmp_path / "b")
    assert [(first / n).read_bytes() for n in NAMES] == [(second / n).read_bytes() for n in NAMES]   # same bytes


def test_a_table_with_few_fixture_items_is_topped_up_by_ascending_key():
    body = ahledger_transport().routes[f"v1/pricetable/{DEFAULT_MARKET}"]
    text = record_fixtures.trim_pricetable(body, DEFAULT_MARKET, {20_005, 20_001})
    table = parse_pricetable(text, DEFAULT_MARKET)
    assert len(table.rows) == record_fixtures.LEDGER_TOP_UP_TO
    assert {20_001, 20_005} <= {r.key for r in table.rows}
    assert text.split("\n")[0].endswith(f"|{record_fixtures.LEDGER_TOP_UP_TO}")


def test_the_ahf_fixture_keys_of_a_market_without_a_snapshot_are_empty(tmp_path):
    assert record_fixtures.ahf_fixture_keys("nowhere", tmp_path) == set()
