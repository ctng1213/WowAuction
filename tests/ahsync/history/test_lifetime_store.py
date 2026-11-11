"""Tests for ahsync/history/lifetime_store.py: the carried lifetimes and the per-gap ledger round-trip exactly (SPEC §2.4)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import BookScan, Database
from ahsync.engine.lifetimes import LifeFlow, LifeState, step
from ahsync.engine.volume import Auction, IdBook
from ahsync.history.lifetime_store import GapSummary, decode_units, encode_units
from tests.support.fake_clock import FakeClock

M, SRC = "forever.normal.horde.us", "blizzard"
H = 3_600_000
LINEN, ORE = 2589, 2770


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def states() -> tuple[LifeState, LifeState]:
    """A state after two linked snapshots, and after a third (one sold, one new, one bought down)."""
    first = IdBook(0, (Auction(1, LINEN, 20, 50, 4, True), Auction(2, ORE, 5, 30, 3)))
    second = IdBook(H, (Auction(1, LINEN, 20, 50, 4, True), Auction(2, ORE, 5, 30, 3), Auction(3, LINEN, 4, 48, 4, True)))
    third = IdBook(2 * H, (Auction(1, LINEN, 12, 50, 4, True), Auction(3, LINEN, 4, 48, 4, True), Auction(4, ORE, 1, 31, 2)))
    state = step(step(LifeState(), first)[0], second)[0]
    return state, step(state, third)[0]


def test_units_text_reads_back_bit_for_bit():
    units = {120: 0.1 + 0.2, 95: 1 / 3, 1000: 7.0}
    text = encode_units(units)
    assert text == "95:0.3333333333333333,120:0.30000000000000004,1000:7.0"        # by value, exact floats
    assert decode_units(text) == units and decode_units("") == {}


def test_a_state_saved_whole_or_as_a_diff_loads_back_equal(db):
    before, after = states()
    with db.transaction():
        db.save_life_state(M, SRC, None, before)
    assert db.load_life_state(M, SRC) == before
    changes = db.conn.total_changes
    with db.transaction():
        db.save_life_state(M, SRC, before, after)
    assert db.load_life_state(M, SRC) == after
    assert db.conn.total_changes - changes < 10       # the state row, a vanished auction, 3 changed or new, tallies
    assert db.load_life_state("other", SRC) is None


def test_FM86_a_state_whose_rows_do_not_add_up_is_none(db):
    before, _ = states()
    with db.transaction():
        db.save_life_state(M, SRC, None, before)
        db.conn.execute("DELETE FROM open_auctions WHERE auction = 2")
    assert db.load_life_state(M, SRC) is None
    with db.transaction():
        db.clear_life_state(M, SRC)
    assert db.count("lifetime_state") == db.count("open_auctions") == db.count("leave_tallies") == 0


def test_the_ledger_keeps_zero_items_exact_floats_and_hands_back_what_a_replay_replaces(db):
    gap = (0, H)
    flows = {LINEN: LifeFlow(sold=0.1 + 0.2, listed=4.0, commodity=True, unseen=1 / 3), ORE: LifeFlow(), 6584: LifeFlow()}
    summary = GapSummary(gap, flows, {LINEN: {960: 4.0}}, {LINEN: {50: 0.1 + 0.2}})
    later = GapSummary((H, 2 * H), {ORE: LifeFlow(expired=5.0)}, {}, {})
    with db.transaction():
        db.write_gaps(M, SRC, [summary, later])
    assert db.read_gaps(M, SRC, 0, 2 * H) == [summary, later]          # zeros from the seen keys; floats bit for bit
    assert db.count("gap_flows") == 2                                    # only the nonzero rows are stored
    assert db.read_gaps(M, SRC, H + 1, 2 * H) == [later]
    assert db.read_gaps(M, SRC, 0, H, units=False) == [GapSummary(gap, flows, {}, {})]   # the flows alone, for the hours
    with db.transaction():
        assert db.delete_gaps_from(M, SRC, 2 * H) == [(H, 2 * H)]
    assert db.read_gaps(M, SRC, 0, 3 * H) == [summary]


def test_the_newest_scan_is_the_histories_newest_snapshot_of_its_source(db):
    assert db.newest_scan_at(M, SRC) is None
    with db.transaction():
        for at, src in ((H, SRC), (3 * H, SRC), (5 * H, "local")):
            db.store_book(BookScan(f"{src}:{M}:{at}", M, src, None, at), [])
    assert db.newest_scan_at(M, SRC) == 3 * H
