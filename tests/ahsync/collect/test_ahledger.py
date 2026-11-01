"""AHledger parsers (SPEC §2.3): scenarios SRC7, SRC17, SRC19, FM44 (parsing half), FM45 (parsing half)."""
from __future__ import annotations

import gzip

import pytest

from ahsync.collect.ahledger import FormatChanged, PriceRow, parse_markets, parse_pricetable

MARKET = "forever.pvp.horde.us"
HEADER = "AHL1|forever/pvp/horde/us|1792585200|3"
SRC7_ROWS = "2589:50:47:340:52:49:40:60:3600;2770:30:28:1200:::25:35:4;4306::115:12:::::"


def test_SRC7_the_addons_price_table_read_field_by_field():
    table = parse_pricetable(f"{HEADER}\n{SRC7_ROWS}", MARKET)
    assert table.market == MARKET
    assert table.updated_s == 1792585200          # seconds as sent; x1000 only when stored
    assert [r.key for r in table.rows] == [2589, 2770, 4306] and table.skipped == 0   # base ids
    linen, ore, silk = table.rows
    assert linen == PriceRow(2589, 50, 47, 340, 52, 49, 40, 60, 3600)     # every field as sent
    assert linen.per_day == 360.0                                         # 3600 tenths
    assert ore == PriceRow(2770, 30, 28, 1200, None, None, 25, 35, 4)     # empty m7, m30 = unknown
    assert ore.per_day == 0.4                                             # 4 tenths
    assert silk == PriceRow(4306, None, 115, 12, None, None, None, None, None)   # empty is never 0
    assert silk.per_day is None


def test_FM44_a_bad_price_table_header_is_a_format_change():
    bad = ["AHL2|forever/pvp/horde/us|1792585200|3",        # another format version
           "AHL1|forever/pvp/alliance/us|1792585200|3",     # another market
           "AHL1|forever/pvp/horde/us|soon|3"]              # updated is not a number
    for header in bad:
        with pytest.raises(FormatChanged, match="pricetable"):
            parse_pricetable(f"{header}\n{SRC7_ROWS}", MARKET)


def test_FM45_one_bad_row_costs_only_itself():
    body = f"{HEADER}\n2589:50:47:340:52:49:40:60:3600;abc:1:2;2770:30:28:1200:::25:35:4"
    table = parse_pricetable(body, MARKET)
    assert [r.key for r in table.rows] == [2589, 2770]      # Linen and Copper Ore kept
    assert table.skipped == 1                               # abc:1:2


def test_header_problems_all_raise():
    for header in ["", "AHL1", "AHL1|forever/pvp/horde/us|1|2|3", "AHL1|forever/pvp/horde/us|-5|3",
                   "AHL1|forever/pvp/horde/us|1.5|3", "AHL1|forever/pvp/horde/us|1792585200|many"]:
        with pytest.raises(FormatChanged):
            parse_pricetable(header, MARKET)


def test_non_utf8_body_is_a_format_change():
    with pytest.raises(FormatChanged):
        parse_pricetable(b"\xff\xfe\x00", MARKET)


def test_bytes_newlines_between_rows_and_trailing_separator_are_tolerated():
    body = f"{HEADER}\r\n2589:50:47:340:52:49:40:60:3600;\n2770:30:28:1200:::25:35:4;\n\n".encode()
    table = parse_pricetable(body, MARKET)
    assert [r.key for r in table.rows] == [2589, 2770] and table.skipped == 0


def test_a_header_with_no_rows_gives_an_empty_table():
    for body in (HEADER, HEADER + "\n", HEADER + "\n\n"):
        table = parse_pricetable(body, MARKET)
        assert table.rows == () and table.skipped == 0       # a count mismatch is not an error


def test_rows_come_back_sorted_and_a_repeated_key_keeps_the_last():
    table = parse_pricetable(f"{HEADER}\n4306:::1:::::;2589:1:::::::;2589:2:::::::", MARKET)
    assert [(r.key, r.median) for r in table.rows] == [(2589, 2), (4306, None)]


def test_bad_fields_skip_the_row():
    rows = ["1:2:3", "1:2:3:4:5:6:7:8:9:10", "0:1:::::::", ":1:::::::", "5:-1:::::::", "5:1.5:::::::",
            "5:x:::::::", "5:١:::::::"]
    table = parse_pricetable(HEADER + "\n" + ";".join(rows), MARKET)
    assert table.rows == () and table.skipped == len(rows)


def test_markets_accepts_a_list_or_a_markets_object():
    entries = [{"id": "forever.pvp.horde.us", "label": "x"}, {"id": "forever.pvp.alliance.us"}]
    expected = {"forever.pvp.horde.us", "forever.pvp.alliance.us"}
    assert parse_markets(entries) == expected
    assert parse_markets({"markets": entries}) == expected


def test_markets_of_another_shape_is_a_format_change():
    for data in ({"markets": "x"}, {"other": []}, "text", None, [1], [{"name": "x"}], [{"id": 5}]):
        with pytest.raises(FormatChanged, match="markets"):
            parse_markets(data)


# ---------------------------------------------------------------- the live source: writes and polling (M19)

from ahsync.collect.ahledger import AhledgerSource, store_pricetable    # noqa: E402
from ahsync.collect.client import ApiClient                              # noqa: E402
from ahsync.db.sqlite import Database                                    # noqa: E402
from tests.support.fake_clock import FakeClock                           # noqa: E402
from tests.support.fake_transport import FakeTransport                   # noqa: E402
from tests.support.ahledger_synth import history_json                   # noqa: E402

UPDATED_MS = 1_792_585_200_000             # Wed 21 Oct 12:20 UTC
HOUR_497940_MS = 497_940 * 3_600_000       # its hour, 12:00
TABLE_PATH = f"v1/pricetable/{MARKET}"
SRC7_BODY = f"{HEADER}\n{SRC7_ROWS}".encode()
MINUTE = 60_000


@pytest.fixture
def world(tmp_path):
    """A database, a fake clock at Wed 12:25, a transport serving the markets list, and a source."""
    clock = FakeClock(UPDATED_MS + 5 * MINUTE)
    db = Database.open(tmp_path / "ahsync.db", clock.now_ms(), None)
    transport = FakeTransport()
    transport.serve("v1/markets", [{"id": MARKET}, {"id": "forever.pvp.alliance.us"}])
    client = ApiClient(transport, clock, api_base="https://api.ahledger.com", per_minute=300)
    yield db, clock, transport, AhledgerSource(client, db, clock)
    db.close()


def _rows(db, sql_text):
    """All rows of a query."""
    return db.conn.execute(sql_text).fetchall()


def test_SRC6_ahledgers_market_ids_are_ahsyncs_and_only_markets_that_exist_are_asked_for(world):
    db, clock, transport, source = world
    transport.serve_etag(TABLE_PATH, SRC7_BODY, '"t1"')
    source.poll(MARKET, MARKET)
    source.poll("forever.normal.horde.us", "forever.normal.horde.us")
    assert transport.count(TABLE_PATH) == 1 and transport.count("v1/pricetable/forever.normal.horde.us") == 0
    assert db.status("ahledger:no_market:forever.normal.horde.us").kind == "ahledger_no_market"
    clock.advance(30 * MINUTE)
    source.poll(MARKET, MARKET)
    source.poll("forever.normal.horde.us", "forever.normal.horde.us")
    assert transport.count("v1/markets") == 1                      # at most once a day
    clock.advance(24 * 60 * MINUTE)
    source.poll(MARKET, MARKET)
    assert transport.count("v1/markets") == 2


def test_SRC8_a_changed_table_becomes_one_current_price_and_one_close_per_item_idempotently(world):
    db, clock, transport, source = world
    transport.serve_etag(TABLE_PATH, SRC7_BODY + b";2592:12::0:::::", '"t1"')
    assert source.poll(MARKET, MARKET) == {2589, 2770, 4306}
    assert _rows(db, "SELECT key, price, qty, at, src FROM current ORDER BY key") == [
        (2589, 47, 340, UPDATED_MS, "ahledger"), (2770, 28, 1200, UPDATED_MS, "ahledger"),
        (4306, 115, 12, UPDATED_MS, "ahledger")]                  # price = minBuyout; Wool has none
    assert _rows(db, "SELECT key, t, open, high, low, close, qty, src FROM candles_h1 ORDER BY key") == [
        (2589, HOUR_497940_MS, None, None, None, 47, 340, "ahledger"),
        (2770, HOUR_497940_MS, None, None, None, 28, 1200, "ahledger"),
        (4306, HOUR_497940_MS, None, None, None, 115, 12, "ahledger")]   # floor(1792585200 / 3600) = 497940
    assert [r[0] for r in _rows(db, "SELECT key FROM ahledger_rows ORDER BY key")] == [2589, 2592, 2770, 4306]
    assert _rows(db, "SELECT market, src, hour FROM observed_hours") == [(MARKET, "ahledger", 497_940)]
    assert db.source_state(MARKET, "ahledger") == ('"t1"', UPDATED_MS, UPDATED_MS + 5 * MINUTE, None)
    before = [_rows(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("current", "candles_h1", "ahledger_rows")]
    clock.advance(30 * MINUTE)                                     # 304: only the fetch time moves
    assert source.poll(MARKET, MARKET) == set()
    assert transport.requests[-1].headers["If-None-Match"] == '"t1"'
    assert db.source_state(MARKET, "ahledger")[2] == UPDATED_MS + 35 * MINUTE
    transport.serve_bytes(TABLE_PATH, SRC7_BODY)                   # the same table again, 200, no ETag
    clock.advance(30 * MINUTE)
    assert source.poll(MARKET, MARKET) == set()
    assert [_rows(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("current", "candles_h1", "ahledger_rows")] == before


def test_FM43_an_unreachable_source_keeps_its_last_good_rows_and_says_so(world):
    db, clock, transport, source = world
    with db.transaction():
        db.upsert_newer_current(MARKET, [(2589, 47, 340, UPDATED_MS)], "ahledger")
    wed_14 = UPDATED_MS + 100 * MINUTE                             # Wed 14:00
    transport.network_error(TABLE_PATH)
    for poll in range(3):
        clock.set(wed_14 + poll * 30 * MINUTE)
        assert source.poll(MARKET, MARKET) == set()                # no exception reaches the scheduler
        if poll < 2:
            assert db.status(f"ahledger:unreachable:{MARKET}") is None
    alert = db.status(f"ahledger:unreachable:{MARKET}")
    assert alert.level == "warn" and alert.data == {"market": MARKET, "since_ms": wed_14, "tries": 3}
    assert _rows(db, "SELECT key, price FROM current") == [(2589, 47)]
    transport.clear_faults()
    transport.serve_etag(TABLE_PATH, SRC7_BODY, '"t1"')
    clock.set(wed_14 + 90 * MINUTE)
    source.poll(MARKET, MARKET)
    assert db.status(f"ahledger:unreachable:{MARKET}") is None     # cleared at 15:30


def test_FM44_writer_half_nothing_is_written_and_the_etag_is_not_kept(world):
    db, clock, transport, source = world
    transport.serve_etag(TABLE_PATH, SRC7_BODY, '"t1"')
    source.poll(MARKET, MARKET)
    before = [_rows(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("current", "candles_h1", "ahledger_rows")]
    for header in ("AHL2|forever/pvp/horde/us|1792589200|3", "AHL1|forever/pvp/alliance/us|1792589200|3",
                   "AHL1|forever/pvp/horde/us|soon|3"):
        transport.serve_etag(TABLE_PATH, f"{header}\n2589:1:1:1:::::".encode(), '"t2"')
        clock.advance(30 * MINUTE)
        assert source.poll(MARKET, MARKET) == set()
        assert db.source_state(MARKET, "ahledger")[0] is None      # the next poll sends no If-None-Match
        assert db.status("format:ahledger_pricetable").data == {"endpoint": "pricetable"}
    assert "If-None-Match" not in transport.requests[-1].headers
    assert [_rows(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("current", "candles_h1", "ahledger_rows")] == before


def test_FM45_writer_half_logs_rows_stored_and_skipped_without_an_alert(world, caplog):
    db, _, transport, source = world
    transport.serve_etag(TABLE_PATH, f"{HEADER}\n2589:50:47:340:52:49:40:60:3600;abc:1:2;"
                                     "2770:30:28:1200:::25:35:4".encode(), '"t1"')
    with caplog.at_level("INFO", logger="ahsync.collect.ahledger"):
        assert source.poll(MARKET, MARKET) == {2589, 2770}
    assert "AHledger forever.pvp.horde.us: 2 rows stored, 1 skipped" in caplog.text
    assert db.statuses() == []


def test_FM46_a_wrong_address_degrades_to_the_other_sources(world):
    db, clock, transport, source = world
    transport.fail(TABLE_PATH, 404)
    source.poll(MARKET, MARKET)
    assert db.status(f"ahledger:no_market:{MARKET}").data == {"market": MARKET}
    assert source.refused(MARKET)
    clock.advance(30 * MINUTE)
    source.poll(MARKET, MARKET)
    assert transport.count(TABLE_PATH) == 1                        # no further request today
    clock.advance(24 * 60 * MINUTE)                                # the next day asks again
    transport.clear_faults()
    transport.serve_etag(TABLE_PATH, SRC7_BODY, '"t1"')
    assert not source.refused(MARKET) and source.poll(MARKET, MARKET)
    assert db.status(f"ahledger:no_market:{MARKET}") is None


def test_cache_control_rate_limits_and_markets_failures(world):
    db, clock, transport, source = world
    transport.serve_etag(TABLE_PATH, SRC7_BODY, '"t1"', {"cache-control": "max-age=3600"})
    source.poll(MARKET, MARKET)
    clock.advance(30 * MINUTE)
    source.poll(MARKET, MARKET)                                    # still fresh: not asked again
    assert transport.count(TABLE_PATH) == 1
    clock.advance(31 * MINUTE)
    transport.fail(TABLE_PATH, 429, headers={"retry-after": "600"})
    assert source.poll(MARKET, MARKET) == set() and db.statuses() == []   # a long Retry-After skips quietly
    older = AhledgerSource(source.client, db, clock)
    transport.remove("v1/markets")                                 # the list can't be read: the table decides
    transport.clear_faults()
    clock.advance(30 * MINUTE)
    assert older.poll(MARKET, MARKET) == set()                     # 304 with the stored ETag
    assert store_pricetable(db, MARKET, parse_pricetable(SRC7_BODY, MARKET), None, 1) == set()   # not newer


# --- parse_prices (SPEC §2.3, Item history) ---------------------------------------------------------------------
import json                                                              # noqa: E402
from pathlib import Path                                                 # noqa: E402

from ahsync.collect.ahledger import Bucket, PriceHistory, parse_prices   # noqa: E402

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ahledger" / "forever.pvp.horde.us"
_HOUR_MS = 3_600_000


def _fixture(name: str) -> dict:
    """Load a recorded prices response.

    Args:
        name: File name under the AHledger fixture folder.

    Returns:
        The decoded JSON.
    """
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _doc(series: list, **over) -> dict:
    """A minimal valid prices response around the given series.

    Args:
        series: The series entries.
        **over: Top-level fields to replace.

    Returns:
        The response dict.
    """
    return {"item": {"id": 2589}, "latest": None, "range": "7d", "series": series, **over}


def test_prices_7d_fixture_recorded_live_is_empty_but_keeps_its_latest_time():
    history = parse_prices(_fixture("prices_2589_7d.json"), 2589)
    assert (history.key, history.range, history.hourly, history.buckets, history.skipped) == (2589, "7d", True, (), 0)
    # Why: no scan in the 7 days before recording; latest.observedAt 2026-10-21T16:25:06Z is the table's updated.
    assert history.latest_ms == 1_792_599_906_000


def test_prices_7d_hourly_series_is_168_buckets():
    history = parse_prices(history_json(MARKET, 2589, "7d", 20, 100), 2589)
    assert (history.hourly, len(history.buckets), history.skipped) == (True, 168, 0)
    # Why: the newest scan 1792585200 s (Wed 21 Oct 12:20) is in hour 497940; the first bucket is 167 hours earlier.
    assert history.buckets[-1].t_ms == 497_940 * _HOUR_MS and history.buckets[0].t_ms == 497_773 * _HOUR_MS


def test_prices_90d_fixture_recorded_live_has_one_bucket_per_scanned_day():
    history = parse_prices(_fixture("prices_2589_90d.json"), 2589)
    assert (history.range, history.hourly, len(history.buckets), history.skipped) == ("90d", False, 30, 0)
    # Why: AHledger's history of this market starts 2026-09-21 (hour 497208) and skips 22 Sep, so 31 days give 30
    # buckets; the last is 2026-10-21 00:00 (hour 497928).
    assert history.buckets[0] == Bucket(497_208 * _HOUR_MS, 49, 67, 60, 100, 6506, 262)
    assert history.buckets[-1].t_ms == 497_928 * _HOUR_MS


def test_prices_ranges_say_whether_they_are_hourly():
    assert [parse_prices(_doc([], range=r), 2589).hourly for r in ("24h", "7d", "30d", "90d")] == [True, True, False, False]


def test_prices_null_latest_gives_no_latest_time_and_null_fields_stay_none():
    history = parse_prices(_doc([{"t": "2026-11-04T14:00:00Z", "median": 19, "min": None}]), 2589)
    assert history.latest_ms is None
    # Why: 2026-11-04 is day 20761, so 14:00 is hour 498278; no milliseconds in the text is fine.
    assert history.buckets == (Bucket(498_278 * _HOUR_MS, None, 19, None, None, None, None),)


def test_prices_latest_without_observed_time_gives_none():
    assert parse_prices(_doc([], latest={"median": 5}), 2589).latest_ms is None


def test_prices_buckets_come_back_sorted_and_milliseconds_are_kept():
    history = parse_prices(_doc([{"t": "2026-11-04T15:00:00.250Z"}, {"t": "2026-11-04T14:00:00.000Z"}]), 2589)
    assert [b.t_ms for b in history.buckets] == [498_278 * _HOUR_MS, 498_279 * _HOUR_MS + 250]


@pytest.mark.parametrize("data", [
    [], "x", None,
    {"item": {"id": 2589}, "series": []},                                  # no range
    {"range": "1y", "series": []},                                         # unknown range
    {"range": "7d", "series": {}},                                         # series not a list
    {"range": "7d"},                                                       # no series
    {"range": "7d", "series": [], "item": {"id": 7}},                      # another item
    {"range": "7d", "series": [], "latest": []},                           # latest of another shape
])
def test_prices_of_another_shape_is_a_format_change(data):
    with pytest.raises(FormatChanged, match="prices"):
        parse_prices(data, 2589)


def test_prices_without_an_item_object_or_id_is_accepted():
    assert parse_prices({"range": "7d", "series": []}, 2589).buckets == ()
    assert parse_prices({"range": "7d", "series": [], "item": {}}, 2589).buckets == ()


def test_prices_unusable_buckets_are_skipped_and_counted():
    good = {"t": "2026-11-04T14:00:00.000Z", "median": 19}
    series = [good, "x", {"median": 1}, {"t": 5}, {"t": "2026-11-04T14:00:00"}, {"t": "not a time Z"},
              {"t": "2026-11-04T14:00:00Z", "median": 1.5}, {"t": "2026-11-04T14:00:00Z", "min": -1},
              {"t": "2026-11-04T14:00:00Z", "p25": True}, {"t": "2026-11-04T14:00:00Z", "quantity": "9"}]
    history = parse_prices(_doc(series), 2589)
    assert len(history.buckets) == 1 and history.skipped == 9
    assert isinstance(history, PriceHistory)


def test_SRC17_a_gzip_compressed_price_table_is_stored_like_a_plain_one(world):
    db, clock, transport, source = world
    transport.serve_etag(TABLE_PATH, gzip.compress(SRC7_BODY), '"t1"', {"content-encoding": "gzip"})
    assert source.poll(MARKET, MARKET) == {2589, 2770, 4306}
    assert [r[0] for r in _rows(db, "SELECT key FROM current ORDER BY key")] == [2589, 2770, 4306]
    assert db.source_state(MARKET, "ahledger")[0] == '"t1"'
    transport.serve_etag(TABLE_PATH, gzip.compress(SRC7_BODY)[:20], '"t2"', {"content-encoding": "gzip"})
    clock.advance(30 * MINUTE)
    assert source.poll(MARKET, MARKET) == set()
    assert db.source_state(MARKET, "ahledger")[0] is None          # forgotten, as FM44
    assert db.status("format:ahledger_pricetable").kind == "ahledger_format"


def test_SRC19_an_empty_table_observes_no_hour(world):
    db, clock, transport, source = world
    transport.serve_etag(TABLE_PATH, SRC7_BODY, '"t1"')
    source.poll(MARKET, MARKET)
    before = [_rows(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("current", "candles_h1", "ahledger_rows")]
    transport.serve_etag(TABLE_PATH, b"AHL1|forever/pvp/horde/us|1792592400|0\n", '"t2"')
    clock.advance(2 * 60 * MINUTE)
    assert source.poll(MARKET, MARKET) == set()
    assert [_rows(db, f"SELECT * FROM {t} ORDER BY 1, 2") for t in ("current", "candles_h1", "ahledger_rows")] == before
    assert _rows(db, "SELECT market, src, hour FROM observed_hours") == [(MARKET, "ahledger", 497_940)]
