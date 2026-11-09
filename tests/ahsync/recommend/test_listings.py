"""Tests for ahsync/recommend/listings.py: your auctions listed now (SPEC §3.4 Liquidity, R31, R33)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from ahsync.recommend import listings
from tests.support.fake_clock import FakeClock

HOUR = 3_600_000
NOW = 20_000 * 86_400_000
BAR, DUST = 2840, 10940


@pytest.fixture
def state(tmp_path):
    """Brugan has 10 bars up for 12 h; Amaya 4 bars of unknown end (30 min-2 h left), 5 that ended an hour ago and
    7 dust of under 30 min, her auctions read an hour ago."""
    db = Database.open(tmp_path / "l.db", FakeClock().now_ms(), None)
    with db.transaction():
        db.conn.executemany("INSERT INTO own_auctions(char_id, key, price, stack, band, count, seen, end_ms) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [("Brugan-G", BAR, 1100, 5, 3, 2, NOW, NOW + 12 * HOUR),
                             ("Amaya-G", BAR, 1200, 4, 2, 1, NOW, None),
                             ("Amaya-G", BAR, 1300, 5, 1, 1, NOW, NOW - HOUR),
                             ("Amaya-G", DUST, 600, 7, 1, 1, NOW, None)])
        db.conn.execute("INSERT INTO snapshots(char_id, location, at) VALUES ('Amaya-G', 'auctions', ?)", (NOW - HOUR,))
    yield db
    db.close()


def test_R31_an_auction_without_an_end_ends_when_its_band_runs_out_from_the_last_read(state):
    bounded = NOW - HOUR + 2 * HOUR                                     # read + the band's 2 h
    assert listings.own_listed(state, ["Amaya-G", "Brugan-G"], NOW) == {BAR: ((4, bounded), (10, NOW + 12 * HOUR))}
    assert listings.own_listed(state, ["Brugan-G"], NOW) == {BAR: ((10, NOW + 12 * HOUR),)}
    assert listings.own_listed(state, ["Amaya-G"], None)[DUST] == ((7, NOW - HOUR + HOUR // 2),)   # None keeps all


def test_R33_your_listed_units_per_item_are_summed_across_characters(state):
    assert listings.listed_units(state, ["Amaya-G", "Brugan-G"], NOW) == {BAR: 14}
