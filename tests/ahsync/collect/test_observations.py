"""Tests for ahsync/collect/observations.py: snapshots with ids into the store (V36, FM32, FM33), their lifetimes
carried one snapshot at a time (SPEC §2.4, BZ66, BZ67, FM86, VI8), checked against the ingest they replaced
(`tests/support/batch_lifetimes.batch_ingest`)."""
from __future__ import annotations

import random

import pytest

from ahsync.collect.observations import bootstrap_window, ingest_snapshot
from ahsync.collect.provider import SnapshotObservation
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import Database
from ahsync.engine.volume import Auction
from tests.support import batch_lifetimes as batch
from tests.support.fake_clock import FakeClock
from tests.support.gen import cases
from tests.support.fake_transport import SimulatedCrash

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
D = 20_000
LINEN, ORE = 2589, 2770
T = D * DAY + 18 * HOUR
FIRST = SnapshotObservation(M, "blizzard", T, (Auction(1, LINEN, 20, 50, 3), Auction(2, ORE, 200, 30, 4),
                                               Auction(3, LINEN, 20, 50, 3)))
SECOND = SnapshotObservation(M, "blizzard", T + HOUR, (Auction(2, ORE, 150, 30, 4), Auction(3, LINEN, 20, 50, 3),
                                                       Auction(4, LINEN, 10, 48, 4)))


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def volume(db: Database) -> list[tuple]:
    """Stored volume rows with their covers."""
    return db.conn.execute(
        "SELECT v.src, v.key, v.sold, v.expired, v.listed, c.kind FROM volume_d1 v LEFT JOIN volume_cover c "
        "ON c.market = v.market AND c.day = v.day AND c.src = v.src ORDER BY v.src, v.key").fetchall()


def test_V36_a_snapshot_with_ids_prices_items_and_feeds_id_and_group_volume_idempotently(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    assert ingest_snapshot(db, store, FIRST) == {LINEN, ORE}
    assert ingest_snapshot(db, store, SECOND) >= {LINEN, ORE}
    assert db.conn.execute("SELECT key, price, qty, src FROM current ORDER BY key").fetchall() == [
        (LINEN, 48, 10, "blizzard"), (ORE, 30, 150, "blizzard")]
    assert db.conn.execute("SELECT src, kind FROM scans ORDER BY at").fetchall() == [("blizzard", "full")] * 2
    expected = [("blizzard", LINEN, 20.0, 0.0, 10.0, "ids"), ("blizzard", ORE, 50.0, 0.0, 0.0, "ids"),
                ("books", LINEN, 20.0, 0.0, 10.0, "groups"), ("books", ORE, 50.0, 0.0, 0.0, "groups")]
    assert volume(db) == expected              # units per price level measure the partial buy too (SPEC §3.3.1)
    assert db.conn.execute("SELECT covered FROM volume_cover WHERE src = 'blizzard'").fetchone()[0] == \
        ",".join("3600" if h == 18 else "0" for h in range(24))
    ingest_snapshot(db, store, SECOND)
    assert volume(db) == expected


def test_BZ52_a_day_stores_what_sold_by_price_and_a_rerun_writes_the_same_row(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    ingest_snapshot(db, store, FIRST)
    ingest_snapshot(db, store, SECOND)                  # Linen 1 (20 at 50 c) left early; 50 Ore bought at 30 c
    rows = lambda: db.conn.execute("SELECT key, src, prices FROM sold_prices_d1 ORDER BY key").fetchall()
    assert rows() == [(LINEN, "blizzard", "50:20.000"), (ORE, "blizzard", "30:50.000")]
    ingest_snapshot(db, store, SECOND)
    assert rows() == [(LINEN, "blizzard", "50:20.000"), (ORE, "blizzard", "30:50.000")]
    with db.transaction():
        db.replace_sold_prices_day(M, D, "blizzard", {LINEN: {200: 3.0, 48: 5.0, 50: 10.0}, ORE: {}})
    assert rows() == [(LINEN, "blizzard", "48:5.000,50:10.000,200:3.000")]       # by price; an empty item is no row


def test_BZ52_snapshots_with_a_sale_a_partial_buy_and_a_cancel_store_only_what_sold(db, tmp_path):
    store, bracers = SnapshotStore(tmp_path / "snapshots"), 6584
    first = snap(T, Auction(1, bracers, 3, 200, 4), Auction(2, LINEN, 8, 45, 4, True), Auction(3, LINEN, 5, 40, 4, True),
                 Auction(4, LINEN, 12, 48, 4, True))
    second = snap(T + HOUR, Auction(3, LINEN, 5, 40, 4, True), Auction(4, LINEN, 7, 48, 4, True))
    ingest_snapshot(db, store, first)
    ingest_snapshot(db, store, second)      # the bracers sold; 5 Linen bought at 48 c; the 45 c Linen left while the
    #                                         cheaper 40 c stayed untouched: a cancel (BZ40), no sale
    assert db.conn.execute("SELECT key, prices FROM sold_prices_d1 ORDER BY key").fetchall() == [
        (LINEN, "48:5.000"), (bracers, "200:3.000")]


def test_FM32_a_crash_after_the_file_before_the_commit_is_mended_by_delivering_it_again(db, tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "snapshots")
    ingest_snapshot(db, store, FIRST)
    real = db.store_book

    def crash(*args, **kwargs):
        """The database write fails after the file is on disk."""
        raise SimulatedCrash("store_book")
    monkeypatch.setattr(db, "store_book", crash)
    with pytest.raises(SimulatedCrash):
        ingest_snapshot(db, store, SECOND)
    assert store.times(M, "blizzard", 0, T + DAY) == [T, T + HOUR] and volume(db) == []
    monkeypatch.setattr(db, "store_book", real)
    ingest_snapshot(db, store, SECOND)
    assert [r[:3] for r in volume(db) if r[0] == "blizzard"] == [("blizzard", LINEN, 20.0), ("blizzard", ORE, 50.0)]


def test_FM33_a_malformed_snapshot_stores_nothing(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    with pytest.raises(ValueError, match="refused: an auction id appears twice"):
        ingest_snapshot(db, store, SnapshotObservation(M, "blizzard", T, FIRST.auctions + FIRST.auctions[:1]))
    assert store.times(M, "blizzard", 0, T + DAY) == [] and db.conn.execute("SELECT COUNT(*) FROM scans").fetchone() == (0,)


def test_a_snapshot_delivered_late_pairs_with_the_next_one_on_another_day(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    late = SnapshotObservation(M, "blizzard", D * DAY - HOUR, FIRST.auctions)
    morning = SnapshotObservation(M, "blizzard", D * DAY + HOUR, SECOND.auctions)
    ingest_snapshot(db, store, morning)
    ingest_snapshot(db, store, late)
    assert db.conn.execute("SELECT DISTINCT day FROM volume_d1 WHERE src = 'blizzard'").fetchall() == [(D,)]


# --- id volume from lifetimes carried one snapshot at a time (SPEC §2.4), against the batch ingest it replaced ------

def snap(at: int, *auctions: Auction) -> SnapshotObservation:
    """A snapshot of the market at a time."""
    return SnapshotObservation(M, "blizzard", at, tuple(auctions))


def day_rows(db: Database, day: int) -> tuple[list, list, list, list]:
    """A day's id volume, ended listings, new-listing positions and sold prices."""
    return (db.conn.execute("SELECT key, sold, expired, listed FROM volume_d1 WHERE src = 'blizzard' AND day = ? "
                            "ORDER BY key", (day,)).fetchall(),
            db.conn.execute("SELECT key, ended, expired, matched FROM lifetimes_d1 WHERE day = ? ORDER BY key",
                            (day,)).fetchall(),
            db.conn.execute("SELECT key, positions FROM new_listings_d1 WHERE day = ? ORDER BY key", (day,)).fetchall(),
            db.conn.execute("SELECT key, prices FROM sold_prices_d1 WHERE day = ? ORDER BY key", (day,)).fetchall())


DERIVED = ("volume_d1", "volume_cover", "lifetimes_d1", "new_listings_d1", "sold_prices_d1", "sales_h1")


def derived(db: Database) -> dict[str, list]:
    """Every row the analysis reads that a snapshot derives, whole and in key order."""
    return {table: db.conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3, 4").fetchall() for table in DERIVED}


def ingest_all(tmp_path, name: str, world: dict[int, tuple[Auction, ...]], order: list[int] | None = None,
               ingest=ingest_snapshot) -> Database:
    """A fresh database and snapshot store with a world ingested snapshot by snapshot, in time order or `order`."""
    db = Database.open(tmp_path / f"{name}.db", FakeClock().now_ms(), None)
    store = SnapshotStore(tmp_path / name)
    for at in order if order is not None else sorted(world):
        ingest(db, store, snap(at, *world[at]))
    return db


def sparse_world() -> dict[int, tuple[Auction, ...]]:
    """Snapshots 5-6 h apart; a 24 h Linen auction posted at D−25 h is first seen at D−21 h and runs out at D−1 h."""
    ore = Auction(9, ORE, 5, 30, 4)
    bands = {-21: 4, -16: 4, -11: 3, -6: 3}                  # Blizzard's bands for 20, 15, 10 and 5 h left
    return {D * DAY + h * HOUR: (ore, *((Auction(1, LINEN, 10, 50, bands[h]),) if h in bands else ()))
            for h in (-27, -21, -16, -11, -6, 0)}


def test_a_day_folded_one_snapshot_at_a_time_has_the_batch_passes_numbers(tmp_path):
    world = sparse_world()
    db = ingest_all(tmp_path, "new", world)
    assert day_rows(db, D)[1] == [(LINEN, 1.0, 1.0, 0.0)]   # its window narrows to (D−3 h, D+1 h]; gone by D, and no
    #   listing ever left early here, so selling never competed: wholly expired (BZ44; the old overlap split said 3/4)
    assert derived(db) == derived(ingest_all(tmp_path, "old", world, ingest=batch.batch_ingest))


def test_a_lifetime_credited_hour_keeps_its_rate_when_the_next_day_starts(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    t = D * DAY - 90 * 60_000                                    # D−1 22:30
    ingest_snapshot(db, store, snap(t, Auction(100, LINEN, 5, 50, 4)))
    ingest_snapshot(db, store, snap(t + HOUR, Auction(200, LINEN, 5, 50, 4)))     # sold, and an identical new one
    ingest_snapshot(db, store, snap(t + 2 * HOUR, Auction(200, LINEN, 5, 50, 4)))  # D 00:30
    hour = t // HOUR * HOUR
    assert db.conn.execute("SELECT sold_per_hour FROM sales_h1 WHERE key = ? AND t = ?", (LINEN, hour)).fetchone() == (5.0,)


def test_a_pooled_hour_comes_out_as_the_batch_pass_wrote_it(tmp_path):
    t = D * DAY - 110 * 60_000                                   # D−1 22:10, then 23:50, then D 00:20
    world = {at: (Auction(1, LINEN, qty, 50, 4),) for at, qty in ((t, 10), (t + 100 * 60_000, 6), (t + 130 * 60_000, 3))}
    hour = (D * DAY - HOUR) // HOUR * HOUR                       # 23:00, credited by both gaps
    new, old = ingest_all(tmp_path, "new", world), ingest_all(tmp_path, "old", world, ingest=batch.batch_ingest)
    rate = lambda db: db.conn.execute("SELECT sold_per_hour FROM sales_h1 WHERE t = ?", (hour,)).fetchone()   # noqa: E731
    assert rate(new) == rate(old) and rate(new) is not None and derived(new) == derived(old)


def leaving_world(ending: dict[int, tuple[Auction, ...]]) -> dict[int, tuple[Auction, ...]]:
    """Hourly snapshots from D−2 00:00 to D+1 12:00 in which 1-unit 24 h Linen listings appear and leave early every
    other hour (so the early-leave rate is not 0, BZ44), merged with an ending auction's own snapshots."""
    world: dict[int, tuple[Auction, ...]] = {}
    for h in range(0, 2 * 24 + 36):
        at = D * DAY - 2 * DAY + h * HOUR
        world[at] = (Auction(1000 + h, LINEN, 1, 50, 4),) if h % 2 == 0 else ()
    for at, auctions in ending.items():
        world[at] = world.get(at, ()) + auctions
    return world


def test_BZ44_a_days_rows_with_a_nonzero_rate_are_the_batch_passes(tmp_path):
    m = D * DAY
    world = leaving_world({m + 10 * HOUR: (Auction(1, LINEN, 10, 50, 2),), m + 11 * HOUR: (Auction(1, LINEN, 10, 50, 2),)})
    new, old = ingest_all(tmp_path, "new", world), ingest_all(tmp_path, "old", world, ingest=batch.batch_ingest)
    linen = [r for r in day_rows(new, D)[1] if r[0] == LINEN]
    assert 0.0 < linen[0][2] < linen[0][1]                        # not all expired: listings do leave early here
    assert derived(new) == derived(old)                            # later snapshots never change day D (causal rate)


def test_BZ44_an_hour_pooled_across_midnight_with_a_nonzero_rate_is_the_batch_passes(tmp_path):
    m = D * DAY
    world = leaving_world({m - 110 * 60_000: (Auction(1, LINEN, 10, 50, 2),), m - 50 * 60_000: (Auction(1, LINEN, 10, 50, 2),),
                           m - 10 * 60_000: (Auction(1, LINEN, 8, 50, 1),), m + 40 * 60_000: ()})
    new, old = ingest_all(tmp_path, "new", world), ingest_all(tmp_path, "old", world, ingest=batch.batch_ingest)
    rate = lambda db: db.conn.execute("SELECT sold_per_hour FROM sales_h1 WHERE key = ? AND t = ?",   # noqa: E731
                                      (LINEN, m - HOUR)).fetchone()          # 23:00, credited by a gap of each day
    assert rate(new) == rate(old) and rate(new) is not None and derived(new) == derived(old)


def test_BZ45_an_hour_pooled_by_two_days_keeps_its_unseen_estimate(tmp_path):
    m, minute = D * DAY, 60_000
    linen = lambda qty: Auction(1, LINEN, qty, 50, 4, True)                    # noqa: E731  a commodity listing
    # posted in (m − 8 h, m − 6:50], first seen then; a 5.5 h gap (crossing day D's kept start, m − 6 h) sells 6
    world = {m - 8 * HOUR: (), m - 410 * minute: (linen(20),), m - 80 * minute: (linen(14),), m + 10 * minute: (linen(14),)}
    new, old = ingest_all(tmp_path, "new", world), ingest_all(tmp_path, "old", world, ingest=batch.batch_ingest)
    rate = new.conn.execute("SELECT sold_per_hour FROM sales_h1 WHERE key = ? AND t = ?", (LINEN, m - 2 * HOUR)).fetchone()
    assert rate[0] > 0.6 and derived(new) == derived(old)        # 0.6 is the observed rate alone: the estimate is in


SEED_VI8_DB = 88


def busy_world(rng) -> dict[int, tuple[Auction, ...]]:
    """Up to two days of snapshots across midnights, 30 min to 7 h apart (holes), with commodities and gear, partial
    fills, cancels and relists, and random bands."""
    live: dict[int, list] = {}
    world, next_id, at = {}, 1, D * DAY - rng.randint(0, 20) * HOUR
    for _ in range(rng.randint(2, 40)):
        for i in sorted(live):
            r = rng.random()
            if r < 0.2:
                key, qty, _ = live.pop(i)
                if rng.random() < 0.3:
                    live[next_id] = [key, qty, rng.choice([45, 50, 55])]      # a relist
                    next_id += 1
            elif r < 0.4 and live[i][0] != 6584:
                live[i][1] = max(1, live[i][1] - rng.randint(1, 4))
        for _ in range(rng.randint(0, 3)):
            live[next_id] = [rng.choice([LINEN, ORE, 6584]), rng.randint(1, 20), rng.choice([45, 50, 55])]
            next_id += 1
        world[at] = tuple(Auction(i, k, q, p, rng.randint(1, 4), k != 6584) for i, (k, q, p) in sorted(live.items()))
        at += rng.choice([30 * 60_000, HOUR, HOUR, HOUR, 2 * HOUR, 7 * HOUR])
    return world


def test_VI8_the_rows_folded_one_snapshot_at_a_time_are_the_batch_ingests(tmp_path):
    for n, world in enumerate(cases(SEED_VI8_DB, busy_world, n=60)):
        context = f"seed {SEED_VI8_DB} case {n}"
        new = ingest_all(tmp_path, f"new{n}", world)
        assert derived(new) == derived(ingest_all(tmp_path, f"old{n}", world, ingest=batch.batch_ingest)), context


def test_BZ66_a_late_snapshot_is_linked_where_it_belongs(tmp_path):
    world = busy_world(random.Random(66))
    times = sorted(world)
    while len(times) < 6:                                        # a world long enough to deliver one late
        world = busy_world(random.Random(len(world) + 660))
        times = sorted(world)
    late = times[len(times) // 2]
    order = [t for t in times if t != late] + [late]             # stored after newer ones, a day late at most here
    new = ingest_all(tmp_path, "late", world, order)
    assert derived(new) == derived(ingest_all(tmp_path, "rebuilt", world))            # every row equals a rebuild's
    state = new.conn.execute("SELECT prev_at FROM lifetime_state").fetchone()
    assert state == (times[-1],)


def test_BZ67_the_first_run_with_the_carried_state_bootstraps_from_the_snapshot_store(tmp_path):
    world = leaving_world({D * DAY + 10 * HOUR: (Auction(1, LINEN, 10, 50, 2),)})
    times = sorted(world)
    db = Database.open(tmp_path / "cut.db", FakeClock().now_ms(), None)
    store = SnapshotStore(tmp_path / "cut")
    for at in times[:-3]:                                        # a history the batch ingest wrote: no state
        batch.batch_ingest(db, store, snap(at, *world[at]))
    yesterday = day_rows(db, D - 1)
    for at in times[-3:]:                                        # then 3 snapshots with the carried state
        ingest_snapshot(db, store, snap(at, *world[at]))
    assert day_rows(db, D - 1) == yesterday
    assert derived(db) == derived(ingest_all(tmp_path, "all", world, ingest=batch.batch_ingest))
    assert db.conn.execute("SELECT prev_at FROM lifetime_state").fetchone() == (times[-1],)
    newest = times[-4]
    assert newest == (D + 1) * DAY + 8 * HOUR
    assert bootstrap_window(newest) == (D * DAY - 66 * HOUR, D * DAY)   # the day before the newest one's, whole
    assert bootstrap_window(D * DAY + 5 * HOUR) == ((D - 1) * DAY - 66 * HOUR, (D - 1) * DAY)    # early: still a day back


def test_FM86_a_damaged_or_missing_state_repairs_itself_and_a_repeat_changes_nothing(tmp_path, caplog):
    world = leaving_world({D * DAY + 10 * HOUR: (Auction(1, LINEN, 10, 50, 2),)})
    times = sorted(world)
    expected = derived(ingest_all(tmp_path, "whole", world))
    for damage in ("UPDATE lifetime_state SET prev_at = prev_at - 3600000", "DELETE FROM lifetime_state",
                   "DELETE FROM open_auctions"):
        db = Database.open(tmp_path / f"{len(damage)}.db", FakeClock().now_ms(), None)
        store = SnapshotStore(tmp_path / f"s{len(damage)}")
        for at in times[:-3]:                                    # its last snapshot lists a live auction
            ingest_snapshot(db, store, snap(at, *world[at]))
        assert db.conn.execute("SELECT COUNT(*) FROM open_auctions").fetchone()[0] > 0
        with db.transaction():
            db.conn.execute(damage)
        caplog.clear()
        for at in times[-3:]:
            ingest_snapshot(db, store, snap(at, *world[at]))
        assert derived(db) == expected, damage
        repairs = [r for r in caplog.records if "rebuilding it from the snapshot store" in r.getMessage()]
        assert len(repairs) == 1, damage                         # one line, then the next run steps as usual
    tables = DERIVED + ("lifetime_state", "open_auctions", "leave_tallies", "gap_flows", "gap_seen")
    dump = lambda: {t: db.conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3").fetchall() for t in tables}  # noqa: E731
    before = dump()
    ingest_snapshot(db, store, snap(times[-1], *world[times[-1]]))          # the same snapshot again (FM32)
    ingest_snapshot(db, store, snap(times[-5], *world[times[-5]]))          # an older one again
    assert dump() == before


def relisted_hourly(first: int, count: int) -> dict[int, tuple[Auction, ...]]:
    """Hourly snapshots in which a 5-unit Linen listing sells and an identical one is posted every hour: the book
    pairs see no sale, the ids see 5 an hour."""
    return {first + h * HOUR: (Auction(100 + h, LINEN, 5, 50, 4),) for h in range(count)}


def test_BZ66_a_late_snapshots_own_book_pairs_never_leave_an_id_hour_behind(tmp_path):
    world = relisted_hourly(D * DAY + 5 * 60_000, 8)
    times = sorted(world)
    order = [t for t in times if t != times[5]] + [times[5]]     # 05:05 stored after 06:05 and 07:05
    new = ingest_all(tmp_path, "late", world, order)
    rates = [r for (r,) in new.conn.execute("SELECT sold_per_hour FROM sales_h1 ORDER BY t")]
    assert rates == [5.0] * 7                                     # 03:00, credited by a gap ending before it, too
    assert derived(new) == derived(ingest_all(tmp_path, "rebuilt", world))


def test_BZ66_a_late_snapshot_of_the_day_before_after_a_bootstrap_folds_that_whole_day(tmp_path):
    world = relisted_hourly(D * DAY - DAY + 5 * 60_000, 35)       # D−1 00:05 to D 10:05
    times = sorted(world)
    late = times[16]                                              # D−1 16:05, stored last
    db = Database.open(tmp_path / "cut.db", FakeClock().now_ms(), None)
    store = SnapshotStore(tmp_path / "cut")
    for at in times:
        if at != late:
            batch.batch_ingest(db, store, snap(at, *world[at]))   # a history the batch wrote: no state yet
    ingest_snapshot(db, store, snap(late, *world[late]))          # bootstrap, then the replay
    assert day_rows(db, D - 1)[0] == [(LINEN, 115.0, 0.0, 115.0)]   # 23 gaps of 5 sold, never a partial day
    assert derived(db) == derived(ingest_all(tmp_path, "rebuilt", world))


def test_BZ66_an_hour_a_late_snapshot_takes_its_credit_from_reads_as_a_rebuild_does(tmp_path):
    m, minute = D * DAY + 10 * HOUR, 60_000
    world = {m: (Auction(1, LINEN, 5, 50, 4),), m + 20 * minute: (Auction(1, LINEN, 5, 50, 4),),
             m + 160 * minute: (Auction(2, LINEN, 5, 50, 4),)}
    times = sorted(world)                                         # (10:00, 12:40) credited 10-12; split at 10:20,
    new = ingest_all(tmp_path, "late", world, [times[0], times[2], times[1]])   # (10:20, 12:40) credits 10-11 only
    assert derived(new) == derived(ingest_all(tmp_path, "rebuilt", world))
