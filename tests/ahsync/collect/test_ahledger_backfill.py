"""Tests for ahsync/collect/ahledger_backfill.py: AHledger's per-item history backfill (SRC12-SRC15, FM47)."""
from __future__ import annotations

import json
import time

import pytest

from ahsync.collect.ahledger import AhledgerSource
from ahsync.collect.ahledger_backfill import AhledgerBackfill
from ahsync.collect.client import ApiClient
from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import FakeTransport, SimulatedCrash

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
WED_1225 = 1_792_585_500_000               # Wed 21 Oct 12:25 UTC (local = UTC here)
TUE_10 = 497_914                           # Tue 20 Oct 10:00, as an hour number (Wed 12:00 is 497940)
LINEN = 2589
EMPTY_7D = {"range": "7d", "latest": None, "series": []}
EMPTY_90D = {"range": "90d", "latest": None, "series": []}


def iso(ms: int) -> str:
    """UTC ms as AHledger's ISO time."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ms // 1000))


def path(key: int, rng: str) -> str:
    """The FakeTransport route of one key's history."""
    return f"v1/prices/{M}/{key}?range={rng}"


class World:
    """A database with a price table of `keys`, a paced keyless client, the poller and the backfill."""

    def __init__(self, tmp_path, keys, clock=None, enabled=True, transport=None):
        self.clock = clock or FakeClock(WED_1225)
        self.db = Database.open(tmp_path / "ahsync.db", self.clock.now_ms(), None)
        self.transport = transport or FakeTransport()
        self.times: list[tuple[int, str]] = []
        with self.db.transaction():
            self.db.upsert_ahledger_rows(M, WED_1225 // 1000 - 300, [(k, 50, None, None, None, None, None) for k in keys])
        self.restart(enabled)

    def restart(self, enabled=True):
        """New client, poller and backfill over the same database, as after a restart."""
        def send(request):
            self.times.append((self.clock.now_ms(), request.url))
            return self.transport(request)
        self.client = ApiClient(send, self.clock, api_base="https://api.ahledger.com", per_minute=300, paced=True)
        self.source = AhledgerSource(self.client, self.db, self.clock)
        self.backfill = AhledgerBackfill(self.client, self.db, self.clock, self.source, lambda: enabled)

    def serve_empty(self, keys, headers=None):
        """Every key answers an empty history."""
        for k in keys:
            self.transport.serve_bytes(path(k, "7d"), json.dumps(EMPTY_7D).encode(), headers)
            self.transport.serve_bytes(path(k, "90d"), json.dumps(EMPTY_90D).encode(), headers)

    def run(self):
        """Step until the run ends."""
        while self.backfill.step([(M, M)]):
            pass

    def prices_requests(self):
        """(time, url) of every history request, in order."""
        return [(t, u) for t, u in self.times if "/v1/prices/" in u]


@pytest.fixture
def close_dbs():
    """Close every world's database after the test."""
    worlds = []
    yield worlds
    for w in worlds:
        w.db.close()


def test_SRC12_the_backfill_fills_gaps_and_never_overwrites(tmp_path, close_dbs):
    w = World(tmp_path, [LINEN])
    close_dbs.append(w)
    tue_day = TUE_10 * HOUR // DAY * DAY                                      # 20 Oct 00:00
    with w.db.transaction():
        w.db.upsert_candles("candles_h1", M, [(LINEN, TUE_10 * HOUR, 45, 45, 45, 45, 20)], "local")
        w.db.upsert_candles("candles_h1", M, [(LINEN, (TUE_10 + 1) * HOUR, 52, 52, 52, 52, 30)], "ahf")
        w.db.upsert_candles("candles_d1", M, [(LINEN, tue_day, 50, 50, 50, 50, 9)], "ahf")
    hourly = [{"t": iso((TUE_10 + i) * HOUR), "min": m, "median": m + 2, "p25": None, "p75": None,
               "quantity": q, "auctions": 5} for i, (m, q) in enumerate(((48, 100), (49, 200), (50, 300)))]
    daily = [{"t": iso(tue_day - DAY), "min": 46, "median": 48, "p25": 1, "p75": 2, "quantity": 9, "auctions": 1},
             {"t": iso(tue_day), "min": 47, "median": 49, "p25": 1, "p75": 2, "quantity": 9, "auctions": 1}]
    w.transport.serve(path(LINEN, "7d"), {"range": "7d", "latest": None, "series": hourly})
    w.transport.serve(path(LINEN, "90d"), {"range": "90d", "latest": None, "series": daily})
    w.run()
    assert w.db.conn.execute("SELECT t, open, close, qty FROM candles_h1 WHERE src = 'ahledger_h1'").fetchall() == [
        ((TUE_10 + 2) * HOUR, None, 50, 300)]                                  # only 12:00: 10:00 and 11:00 are taken
    assert w.db.conn.execute("SELECT t, close FROM candles_d1 WHERE src = 'ahledger'").fetchall() == [
        (tue_day - DAY, 46)]                                                   # 19 Oct; AHF has 20 Oct
    assert w.db.conn.execute("SELECT hour FROM observed_hours WHERE src = 'ahledger_h1' ORDER BY hour").fetchall() == [
        (TUE_10,), (TUE_10 + 1,), (TUE_10 + 2,)]                               # it observed all three
    before = [w.db.conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3").fetchall()
              for t in ("candles_h1", "candles_d1", "observed_hours")]
    w.db.set_source_state(M, "ahledger", checkpoint=None)                      # the backfill runs again
    w.clock.advance(DAY)
    w.restart()
    w.run()
    assert len(w.prices_requests()) == 4
    assert [w.db.conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2, 3").fetchall()
            for t in ("candles_h1", "candles_d1", "observed_hours")] == before   # adds nothing


def test_SRC13_ahledgers_limit_and_caching_are_honoured(tmp_path, close_dbs):
    keys = list(range(1, 2208))                                                # a 2,207-key table
    w = World(tmp_path, keys)
    close_dbs.append(w)
    w.serve_empty(keys, {"cache-control": "max-age=60"})
    w.transport.serve_etag(f"v1/pricetable/{M}", b"AHL1|forever/pvp/horde/us|1792585200|0\n", '"t"')
    w.transport.serve("v1/markets", [{"id": M}])
    w.source.poll(M, M)                                                        # the table poll shares the pacing
    w.run()
    sent = [t for t, _ in w.times]
    prices = w.prices_requests()
    assert len(prices) == 4414                                                 # 7d and 90d per key
    assert all(b - a == 200 for a, b in zip(sent, sent[1:]))                   # one request every 0.2 s, poll included
    assert prices[-1][0] - prices[0][0] == 882_600                             # 4,413 × 0.2 s
    assert max(sum(1 for t in sent if s <= t < s + 60_000) for s in sent) == 300
    w.backfill._histories(M, 2207)                                             # still fresh: not asked again
    assert len(w.prices_requests()) == 4414


def test_SRC14_the_backfill_resumes_and_runs_at_most_once_a_day_only_when_there_is_a_gap(tmp_path, close_dbs):
    keys = list(range(1, 1301))
    w = World(tmp_path, keys)
    close_dbs.append(w)
    w.serve_empty(keys)
    w.transport.crash_on(path(1250, "7d"))
    with pytest.raises(SimulatedCrash):
        w.run()                                                                # stops while fetching the 1,250th key
    assert json.loads(w.db.source_state(M, "ahledger")[3])["last_key"] == 1200
    w.transport.clear_faults()
    w.restart()
    first = len(w.prices_requests())
    w.run()
    assert w.prices_requests()[first][1].endswith(f"/{M}/1201?range=7d")       # none before it
    assert len(w.prices_requests()) - first == 2 * 100
    assert w.db.status(f"ahledger:backfill:{M}") is None                      # done: the line goes
    w.restart()
    w.run()
    assert len(w.prices_requests()) - first == 200                             # the same day: nothing
    w.clock.advance(DAY)                                                       # the next day: a gap, so it runs again
    w.restart()
    w.run()
    assert len(w.prices_requests()) - first == 200 + 2 * 1300
    with w.db.transaction():                                                   # no gap any more: the day after, nothing
        w.db.add_observed_hours(M, "ahf", range(w.clock.now_ms() // HOUR - 200, w.clock.now_ms() // HOUR + 30))
        w.db.upsert_candles("candles_h1", M, [(1, 0, None, None, None, 5, 1)], "ahledger_h1")
    w.clock.advance(DAY)
    w.restart()
    w.run()
    assert len(w.prices_requests()) - first == 200 + 2 * 1300


def test_SRC15_the_backfill_is_optional(tmp_path, close_dbs):
    w = World(tmp_path, [LINEN], enabled=False)
    close_dbs.append(w)
    w.serve_empty([LINEN])
    w.transport.serve_etag(f"v1/pricetable/{M}", b"AHL1|forever/pvp/horde/us|1792585200|1\n2589:50:47:340:::::", '"t"')
    w.transport.serve("v1/markets", [{"id": M}])
    w.source.poll(M, M)
    w.run()
    assert w.prices_requests() == []                                           # never a /v1/prices request
    assert w.db.conn.execute("SELECT price FROM current WHERE src = 'ahledger'").fetchall() == [(47,)]


def test_FM47_ahledgers_429_is_honoured_and_a_long_one_is_a_pause(tmp_path, close_dbs):
    keys = list(range(1, 201))
    w = World(tmp_path, keys)
    close_dbs.append(w)
    w.serve_empty(keys)
    w.transport.fail(path(40, "7d"), 429, times=1, headers={"retry-after": "30"})
    start = w.clock.now_ms()
    w.run()
    asked = [u for _, u in w.prices_requests()]
    assert asked.count(f"https://api.ahledger.com/{path(40, '7d')}") == 2      # asked again after the wait
    assert w.clock.now_ms() - start >= 30_000 + 399 * 200 and len(asked) == 401
    # another time: Retry-After 600 at the 40th key ends the run with no checkpoint yet, so it starts over
    (tmp_path / "b").mkdir()
    w2 = World(tmp_path / "b", keys)
    close_dbs.append(w2)
    w2.serve_empty(keys)
    w2.transport.fail(path(40, "7d"), 429, times=1, headers={"retry-after": "600"})
    w2.run()
    assert w2.db.source_state(M, "ahledger")[3] is None
    line = w2.db.status(f"ahledger:backfill:{M}")
    assert (line.level, line.data["paused"], line.data["done"]) == ("info", True, 0)   # a pause, not an alert
    assert [s for s in w2.db.statuses() if s.level != "info"] == []
    w2.clock.advance(HOUR)
    w2.restart()
    first = len(w2.prices_requests())
    w2.transport.fail(path(140, "7d"), 429, times=1, headers={"retry-after": "600"})
    w2.run()
    assert w2.prices_requests()[first][1].endswith(f"/{M}/1?range=7d")         # the first key again
    assert json.loads(w2.db.source_state(M, "ahledger")[3])["last_key"] == 100
    w2.clock.advance(HOUR)
    w2.restart()
    first = len(w2.prices_requests())
    w2.run()
    assert w2.prices_requests()[first][1].endswith(f"/{M}/101?range=7d")       # at the 140th it starts at the 101st
    assert w2.db.status(f"ahledger:backfill:{M}") is None


def test_a_paused_market_waits_and_unknown_or_malformed_answers_are_skipped(tmp_path, close_dbs):
    w = World(tmp_path, [1, 2, 3])
    close_dbs.append(w)
    w.transport.serve(path(1, "7d"), {"range": "nope"})                        # a format change for one item
    w.transport.serve_bytes(path(1, "90d"), b"{not json")                       # a bad body
    w.serve_empty([3])                                                         # key 2 answers 404
    w.run()
    assert json.loads(w.db.source_state(M, "ahledger")[3])["done"] is True
    w.db.set_source_state(M, "ahledger", checkpoint="{bad")                    # an unreadable checkpoint is none
    assert w.backfill._checkpoint(M) is None
    w.db.set_source_state(M, "ahledger", checkpoint="[1]")
    assert w.backfill._checkpoint(M) is None
    w.transport.fail(path(1, "7d"), 503)                                       # unreachable: paused for an hour
    w.clock.advance(DAY)
    w.restart()
    assert w.backfill.step([(M, M)]) is False
    w.transport.clear_faults()
    w.clock.advance(HOUR // 2)
    asked = len(w.prices_requests())
    assert w.backfill.step([(M, M)]) is False and len(w.prices_requests()) == asked   # still paused
    with w.db.transaction():
        w.db.conn.execute("DELETE FROM ahledger_rows")
    w.clock.advance(DAY)
    w.restart()
    assert w.backfill.step([(M, M)]) is False                                  # no table: nothing to do
