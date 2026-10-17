"""AHF endpoints and response shapes (SPEC §2.1 Response formats), against the real recordings."""
from __future__ import annotations

import pytest

from ahsync.collect.ahf import (AhfApi, FormatChanged, parse_book, parse_candles, parse_items, parse_snapshot)
from ahsync.collect.client import ApiClient
from tests.support.builders import item_key, template
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET, FakeTransport

MARKET = DEFAULT_MARKET


def recorded_api(key: str = "ak_x_secret") -> tuple[AhfApi, FakeTransport]:
    """An AhfApi over the recorded fixtures."""
    transport = FakeTransport.from_fixtures()
    return AhfApi(ApiClient(transport, FakeClock(), key)), transport


def test_me_reads_features_and_limits():
    api, _ = recorded_api()
    caps = api.me()
    assert caps.history_days == 90 and caps.api_per_minute == 600
    assert {"prices.live", "books", "depth"} <= caps.features
    assert caps.contributor_until_ms == 1_792_798_058_214
    assert caps.premium and caps.premium_history


def test_files_reads_sealed_caps_and_books_and_registers_tokens():
    api, _ = recorded_api()
    files = api.files(MARKET)
    assert (files.sealed_h1, files.sealed_d1) == (497_832, 20_742)
    assert files.caps_h1[20_735] == "cap-h1-20735"
    assert files.books[0].complete and files.books[0].at_ms == 1_791_580_476_000
    scrubbed = api.client.redactor.scrub(files.books[0].url + files.caps_d1_url + files.caps_volume_url)
    assert "cap-book" not in scrubbed and "cap-d1" not in scrubbed and "cap-volume" not in scrubbed
    assert api.client.redactor.scrub("x/cap-h1-20735/1") == "x/***/1"


def test_urls_follow_the_cdn_patterns():
    api, transport = recorded_api()
    api.files(MARKET)
    assert api.hour_url(MARKET, 497_661) == f"https://cdn.auctionhouse.fyi/candles/v1/h1/{MARKET}/497661.json.gz"
    assert api.hour_url(MARKET, 497_661, "cap-h1-20735") == (
        f"https://cdn.auctionhouse.fyi/candles/v1/h1c/{MARKET}/cap-h1-20735/497661.json.gz")
    assert len(api.hour_candles(MARKET, 497_661)) == 27
    assert len(api.hour_candles(MARKET, 497_661, "cap-h1-20735")) == 27
    public, premium = transport.requests[-2:]
    assert "Authorization" not in public.headers and "Authorization" in premium.headers


def test_recorded_files_parse():
    api, _ = recorded_api()
    files = api.files(MARKET)
    assert api.day_candles(files.d1_url, False) == []                 # the public d1 file was empty
    assert len(api.day_candles(files.caps_d1_url, True)) == 27
    snap = api.snapshot(files.snapshot_url)
    assert snap.at_ms == 1_792_198_800_000 and len(snap.rows) == 27
    at, complete, entries = api.book(files.books[0].url)
    assert complete and len(entries) == 2147            # no band 1: shifted bands map one to one (V28)
    assert {e[3] for e in entries} == {1, 2, 3, 4}       # 2, 3, 4, 0 → short, medium, long, very long
    assert api.volume(files.caps_volume_url) == []
    items = {row[0]: row for row in api.items()}
    assert items[item_key("Linen Cloth")][2] == "Linen Cloth"


def test_candle_rows_keep_null_prices():
    rows = parse_candles({"items": [[2589, [[0, None, None, None, None, None]]]]}, "h1")
    assert rows == [(2589, 0, None, None, None, None, 0)]


@pytest.mark.parametrize("data", [None, {"items": {}}, {"items": [[1]]}, {"items": [[1, [[0, 1]]]]}])
def test_bad_candle_shapes_are_format_changes(data):
    with pytest.raises(FormatChanged) as error:
        parse_candles(data, "h1")
    assert error.value.endpoint == "h1"


def test_snapshot_shape_checks():
    snap = parse_snapshot({"at": 5, "rows": [[1, None, None, None]]})
    assert snap.rows == [(1, None, 0, 5)]
    for data in ({"at": 5, "entries": []}, {"at": 5, "rows": [[1, 2, 3]]}):
        with pytest.raises(FormatChanged, match="snapshot"):
            parse_snapshot(data)


def test_book_sums_repeated_groups_and_checks_shape():
    at, complete, entries = parse_book({"at": 1, "entries": [[5, 10, 1, 4, 2], [5, 10, 1, 4, 3]]})
    assert (at, complete, entries) == (1, False, [(5, 10, 1, 3, 5)])     # no band 1: AHF's shifted bands (V28)
    with pytest.raises(FormatChanged):
        parse_book({"at": 1, "entries": [[5, 10, 1, 4]]})


def test_V28_auctionhousefyi_books_are_shifted_back_to_wows_bands():
    # AHF's addon uploads Forever's time left + 1 (short 2, medium 3, long 4) and very long as 0.
    _at, _complete, entries = parse_book({"at": 1, "entries": [[5, 10, 1, 0, 7], [5, 10, 1, 2, 1], [5, 10, 1, 3, 2],
                                                               [5, 10, 1, 4, 3]]})
    assert entries == [(5, 10, 1, 1, 1), (5, 10, 1, 2, 2), (5, 10, 1, 3, 3), (5, 10, 1, 4, 7)]


def test_book_band_0_is_treated_as_very_long():
    _at, _complete, entries = parse_book({"at": 1, "entries": [[5, 10, 1, 0, 2], [5, 10, 1, 4, 3], [5, 10, 1, 1, 1]]})
    assert entries == [(5, 10, 1, 1, 1), (5, 10, 1, 4, 5)]


def test_items_include_variants_with_base_values():
    rows = parse_items({"items": {"10": {"n": "Base", "q": 2, "st": 1}},
                        "variants": {str(10 + 2_147_483_648 * 3): {"n": "Base of the Bear"}}})
    assert rows[1] == (10 + 2_147_483_648 * 3, 10, "Base of the Bear", 2, None, None, None, None, None, 1)
    with pytest.raises(FormatChanged):
        parse_items({"variants": {}})


def test_m1_and_live_checkpoint_tokens_are_secrets():
    api, transport = recorded_api()
    files = template("files")
    files["caps"]["m1"] = {"20741": "m1tokenAAAA"}
    files["live"]["checkpoint"]["url"] = "https://cdn.auctionhouse.fyi/live/v1/m/c1-livetokenBBBB.json.gz"
    transport.serve(f"api/v1/ah/{MARKET}/files", files)
    api.files(MARKET)
    assert api.client.redactor.scrub("m1tokenAAAA livetokenBBBB") == "*** ***"


def test_volume_shape_error_names_its_endpoint():
    api, transport = recorded_api()
    transport.serve("v.json", {"rows": []})
    with pytest.raises(FormatChanged, match="volume"):
        api.volume("https://cdn.auctionhouse.fyi/v.json")


def test_me_and_files_shape_checks():
    api, transport = recorded_api()
    transport.serve("api/v1/me", {})
    caps = api.me()
    assert caps.history_days == 7 and not caps.premium
    transport.serve("api/v1/me", [])
    with pytest.raises(FormatChanged):
        api.me()
    files = template("files")
    del files["sealed"]["d1"]
    transport.serve(f"api/v1/ah/{MARKET}/files", files)
    with pytest.raises(FormatChanged, match="files"):
        api.files(MARKET)
    transport.serve("snap/v1/x/latest.json", {"hour": 1})
    with pytest.raises(FormatChanged, match="snapshot"):
        api.snapshot("https://cdn.auctionhouse.fyi/snap/v1/x/latest.json")
