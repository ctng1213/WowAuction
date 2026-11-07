"""Tests for ahsync/collect/blizzard.py: Blizzard's Forever auction API as a snapshot provider (SPEC §2.4)."""
from __future__ import annotations

import base64
import gzip
import json
import logging
from pathlib import Path

import pytest

from ahsync.collect import blizzard as bz
from ahsync.collect.client import HttpRequest, HttpResponse
from ahsync.collect.provider import load_bonus_suffixes, snapshot_problems, source_kind, source_precedence
from ahsync.engine.item_key import KEY_SUFFIX_FACTOR
from ahsync.engine.volume import Auction
from tests.support.fake_clock import FakeClock

M = "forever.normal.horde.us"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "blizzard"
CLIENT_ID, SECRET = "client-id-1234", "client-secret-5678"
B = "Fri, 06 Nov 2026 14:02:40 GMT"
B_MS = 1_793_973_760_000
A = "Fri, 06 Nov 2026 13:02:11 GMT"
SUFFIXES = {12742: 14322}


def body(records) -> bytes:
    """A house body as Blizzard shapes it; None records for a metadata-only house."""
    data = {"_links": {"self": {"href": "x"}}, "id": 113, "name": {"en_US": "Horde Auction House"}}
    if records is not None:
        data["auctions"] = records
    return json.dumps(data).encode("utf-8")


SPAULDERS = {"id": 39643, "item": {"id": 8180}, "quantity": 1, "buyout": 1500, "time_left": "SHORT"}
LINEN = {"id": 2455, "item": {"id": 2589}, "quantity": 14, "unit_price": 125, "time_left": "VERY_LONG"}


class ScriptedHttp:
    """Answers the token POST and the two house GETs from a script; records every request."""

    def __init__(self, auctions=(200, body([SPAULDERS]), B), commodities=(200, body([LINEN]), B), token_status=200):
        self.routes = {"auctions": auctions, "commodities": commodities}
        self.token_status, self.tokens, self.requests = token_status, 0, []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.method == "POST":
            self.tokens += 1
            payload = {"access_token": f"token-{self.tokens:04d}", "expires_in": 86399}
            return HttpResponse(self.token_status, {}, json.dumps(payload).encode())
        status, data, stamp = self.routes["commodities" if "/commodities" in request.url else "auctions"]
        since = request.headers.get("If-Modified-Since")
        if status == 200 and since and since == stamp:
            status = 304
        headers = {"last-modified": stamp} if stamp else {}
        if status == 200:
            headers["content-encoding"] = "gzip"
            data = gzip.compress(data, mtime=0)
        return HttpResponse(status, headers, data if status == 200 else b"")


def observation(parsed):
    """The parsed snapshot's observation, which these tests expect to be there."""
    assert parsed.observation is not None
    return parsed.observation


def client(http, clock=None):
    """A client over the scripted HTTP."""
    return bz.BlizzardClient(http, clock or FakeClock(B_MS), CLIENT_ID, SECRET)


def test_BZ1_the_two_endpoints_are_one_snapshot():
    fetch = client(ScriptedHttp()).fetch_house(2, 113, None)
    assert (fetch.state, fetch.last_modified, fetch.at_ms) == ("new", B, B_MS)
    assert fetch.at_ms is not None
    parsed = bz.parse_snapshot(M, fetch.auctions, fetch.commodities, fetch.at_ms, SUFFIXES)
    assert observation(parsed).market == M and observation(parsed).src == "blizzard" and observation(parsed).at_ms == B_MS
    assert observation(parsed).auctions == (Auction(39643, 8180, 1, 1500, 1), Auction(2455, 2589, 14, 125, 4))
    assert snapshot_problems(observation(parsed)) == []


def test_BZ1_the_recorded_house_parses_whole():
    regular = json.dumps(json.loads((FIXTURES / "house_113_auctions.json").read_text())).encode()
    commodities = json.dumps(json.loads((FIXTURES / "house_113_commodities.json").read_text())).encode()
    parsed = bz.parse_snapshot(M, regular, commodities, B_MS, load_bonus_suffixes())
    records = len(json.loads(regular)["auctions"]) + len(json.loads(commodities)["auctions"])
    assert len(observation(parsed).auctions) + parsed.bid_only == records
    assert snapshot_problems(observation(parsed)) == []
    assert all(a.key > 0 for a in observation(parsed).auctions)      # every recorded bonus list is a known suffix


def test_BZ2_bonus_lists_key_variants_as_the_addon_does():
    records = [{"id": 1, "item": {"id": 15210, "bonus_lists": [1, 12742]}, "quantity": 1, "buyout": 5000, "time_left": "LONG"},
               {"id": 2, "item": {"id": 15210, "bonus_lists": [99999]}, "quantity": 1, "buyout": 5000, "time_left": "LONG"},
               {"id": 3, "item": {"id": 2840, "context": 1, "modifiers": [{"type": 28, "value": 5244}]}, "quantity": 1,
                "buyout": 90, "time_left": "LONG"}]
    keys = [a.key for a in observation(bz.parse_snapshot(M, body(records), body([]), B_MS, SUFFIXES)).auctions]
    assert keys == [15210 + KEY_SUFFIX_FACTOR * 28644, -15210, 2840]       # context and modifiers don't key


def test_BZ3_a_bid_only_auction_is_left_out_and_counted_and_a_zero_unit_one_is_refused():
    bid_only = {"id": 5, "item": {"id": 8180}, "quantity": 1, "bid": 900, "time_left": "LONG"}
    parsed = bz.parse_snapshot(M, body([SPAULDERS, bid_only]), body([LINEN]), B_MS, SUFFIXES)
    assert [a.id for a in observation(parsed).auctions] == [39643, 2455] and parsed.bid_only == 1
    empty = bz.parse_snapshot(M, body([]), body([{**LINEN, "quantity": 0}]), B_MS, SUFFIXES)
    assert snapshot_problems(observation(empty)) == [
        "an auction has no units, no price, an unknown time-left band or no item"]   # FM33 refuses it whole


def test_BZ3_a_regular_buyout_is_the_auctions_total():
    stack = {"id": 7, "item": {"id": 4306}, "quantity": 4, "buyout": 1003, "time_left": "LONG"}
    assert observation(bz.parse_snapshot(M, body([stack]), body([]), B_MS, SUFFIXES)).auctions[0].price == 250


def test_BZ4_bands_come_from_time_left_and_an_unknown_one_refuses_the_snapshot():
    medium, long_ = {**LINEN, "id": 1, "time_left": "MEDIUM"}, {**LINEN, "id": 2, "time_left": "LONG"}
    bands = [a.band for a in observation(bz.parse_snapshot(M, body([]), body([medium, long_]), B_MS, SUFFIXES)).auctions]
    assert bands == [2, 3]
    with pytest.raises(bz.BlizzardError, match="auction 3: unknown time_left 'EXPIRED'"):
        bz.parse_snapshot(M, body([]), body([medium, {**LINEN, "id": 3, "time_left": "EXPIRED"}]), B_MS, SUFFIXES)


def test_BZ5_an_empty_house_ingests_nothing():
    parsed = bz.parse_snapshot(M, body(None), body([LINEN] * 1), B_MS, SUFFIXES)
    assert parsed.empty and parsed.observation is None
    assert not bz.parse_snapshot(M, body([]), body([]), B_MS, SUFFIXES).empty   # an empty list is a real answer


def test_BZ38_a_record_blizzard_repeats_field_for_field_counts_once():
    echo = {"id": 6690572, "item": {"id": 7281}, "bid": 100, "buyout": 200, "quantity": 1, "time_left": "LONG"}
    parsed = observation(bz.parse_snapshot(M, body([echo, SPAULDERS, echo]), body([LINEN]), B_MS, SUFFIXES))
    assert [a.id for a in parsed.auctions] == [6690572, 39643, 2455] and snapshot_problems(parsed) == []
    other = {**echo, "id": 6690573}
    clash = observation(bz.parse_snapshot(M, body([other, {**other, "quantity": 2}]), body([]), B_MS, SUFFIXES))
    assert len(clash.auctions) == 2 and snapshot_problems(clash) == ["an auction id appears twice"]   # FM33


def test_BZ6_the_token_is_cached_renewed_before_expiry_and_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    clock, http = FakeClock(B_MS), ScriptedHttp()
    blizzard = client(http, clock)
    blizzard.fetch_house(2, 113, None)
    post, first, second = http.requests[:3]
    assert post.method == "POST" and post.url == bz.TOKEN_URL and post.body == b"grant_type=client_credentials"
    assert base64.b64decode(post.headers["Authorization"].removeprefix("Basic ")).decode() == f"{CLIENT_ID}:{SECRET}"
    for request in (first, second):
        assert request.headers["Authorization"] == "Bearer token-0001" and request.headers["Accept-Encoding"] == "gzip"
        assert request.url.endswith("?namespace=dynamic-forever-us") and "If-Modified-Since" not in request.headers
    assert first.url == f"{bz.API}/data/wow/ruleset/2/auctions/113?namespace=dynamic-forever-us" and second.url.endswith("/113/commodities?namespace=dynamic-forever-us")
    clock._now_ms += (23 * 60 + 50) * 60_000              # 23 h 50 min later: still valid, no new token
    blizzard.fetch_house(2, 113, B)
    assert http.tokens == 1 and http.requests[-1].headers["If-Modified-Since"] == B
    clock._now_ms += 6 * 60_000                            # 23 h 56 min: within 5 min of expiry
    blizzard.fetch_house(2, 113, B)
    assert http.tokens == 2 and http.requests[-1].headers["Authorization"] == "Bearer token-0002"
    text = caplog.text
    assert all(s not in text for s in (CLIENT_ID, SECRET, "token-0001", "token-0002"))


def test_BZ6_a_401_renews_the_token_once_and_a_failed_token_request_names_only_its_status():
    http = ScriptedHttp(auctions=(401, b"", B))
    with pytest.raises(bz.BlizzardError, match="answered 401") as error:
        client(http).fetch_house(2, 113, None)
    assert http.tokens == 2                                # renewed once, then given up
    assert SECRET not in str(error.value)
    with pytest.raises(bz.BlizzardError, match="token request answered 401") as error:
        client(ScriptedHttp(token_status=401)).fetch_house(2, 113, None)
    assert SECRET not in str(error.value) and CLIENT_ID not in str(error.value)


def test_BZ7_the_raw_envelope_is_blizzards_bytes_with_provenance_under_its_hour_key():
    regular, commodities = body([SPAULDERS]), body([LINEN])
    raw = bz.raw_envelope(2, 113, B, B_MS + 5_000, regular, commodities)
    envelope = json.loads(gzip.decompress(raw))
    assert envelope == {"region": "us", "namespace": "dynamic-forever-us", "ruleset": 2, "house": 113,
                        "last_modified": B, "fetched_ms": B_MS + 5_000,
                        "auctions": json.loads(regular), "commodities": json.loads(commodities)}
    assert regular in gzip.decompress(raw) and commodities in gzip.decompress(raw)     # spliced, not re-serialized
    assert raw == bz.raw_envelope(2, 113, B, B_MS + 5_000, regular, commodities)       # same snapshot, same bytes
    assert bz.raw_key(M, B_MS) == "raw/forever.normal.horde.us/2026/11/06/140240.json.gz"


def test_BZ8_both_endpoints_unchanged_is_nothing():
    fetch = client(ScriptedHttp()).fetch_house(2, 113, B)
    assert (fetch.state, fetch.last_modified, fetch.auctions) == ("unchanged", B, b"")


def test_BZ9_halves_an_hour_apart_or_one_unchanged_are_not_one_snapshot():
    assert client(ScriptedHttp(commodities=(200, body([LINEN]), A))).fetch_house(2, 113, None).state == "mismatch"
    one_new = ScriptedHttp(auctions=(200, body([SPAULDERS]), "Fri, 06 Nov 2026 15:02:05 GMT"))
    assert client(one_new).fetch_house(2, 113, B).state == "mismatch"      # commodities still 304 at B
    close = ScriptedHttp(commodities=(200, body([LINEN]), "Fri, 06 Nov 2026 14:02:35 GMT"))
    assert client(close).fetch_house(2, 113, None).last_modified == B   # 5 s apart: one snapshot, the newer stamp


def test_FM69_a_cut_off_gzip_body_is_refused_whole():
    http = ScriptedHttp()
    original = http.__call__

    def cut(request):
        response = original(request)
        if "/commodities" in request.url:
            return HttpResponse(response.status, response.headers, response.body[: len(response.body) * 6 // 10])
        return response

    with pytest.raises(bz.BlizzardError, match="gzip body cut off"):
        bz.BlizzardClient(cut, FakeClock(B_MS), CLIENT_ID, SECRET).fetch_house(2, 113, None)


def test_FM69_a_server_error_or_a_missing_stamp_ends_the_fetch():
    with pytest.raises(bz.BlizzardError, match="answered 503"):
        client(ScriptedHttp(auctions=(503, b"", B))).fetch_house(2, 113, None)
    with pytest.raises(bz.BlizzardError, match="no Last-Modified"):
        client(ScriptedHttp(auctions=(200, body([]), None))).fetch_house(2, 113, None)
    with pytest.raises(bz.BlizzardError, match="not a date"):
        bz.http_date_ms("yesterday")
    with pytest.raises(bz.BlizzardError, match="auctions body is not JSON"):
        bz.parse_snapshot(M, b"<html>", body([]), B_MS, SUFFIXES)
    with pytest.raises(bz.BlizzardError, match="commodities body is not an object"):
        bz.parse_snapshot(M, body([]), b"[]", B_MS, SUFFIXES)
    with pytest.raises(bz.BlizzardError, match="auction 9: missing id, item or quantity"):
        bz.parse_snapshot(M, body([{"id": 9, "item": {}, "quantity": 1, "buyout": 5, "time_left": "LONG"}]), body([]),
                          B_MS, SUFFIXES)


def test_SRC20_a_blizzard_snapshot_is_a_complete_book():
    assert source_kind("blizzard") == "book"
    ranked = {"blizzard": 0, "ahledger": 1}
    order = sorted(["ahledger", "ahf", "blizzard", "local"], key=lambda s: source_precedence(s, ranked))
    assert order == ["local", "blizzard", "ahf", "ahledger"]   # book kinds first, your scans on a tie, then hourly, table


def test_BZ7_an_envelope_opens_back_into_its_snapshot():
    regular, commodities = body([SPAULDERS]), body([LINEN])
    stamp, a, c = bz.open_envelope(bz.raw_envelope(2, 113, B, B_MS, regular, commodities))
    assert stamp == B and json.loads(a) == json.loads(regular) and json.loads(c) == json.loads(commodities)
    parsed = bz.parse_snapshot(M, a, c, bz.http_date_ms(stamp), SUFFIXES)
    assert observation(parsed).auctions == (Auction(39643, 8180, 1, 1500, 1), Auction(2455, 2589, 14, 125, 4))
    for bad in (b"not gzip", gzip.compress(b"{}"), gzip.compress(b"[1]")):
        with pytest.raises(bz.BlizzardError, match="not a raw envelope"):
            bz.open_envelope(bad)


SEARCH_LINEN = {"id": 2589, "name": {"en_US": "Linen Cloth"}, "quality": {"type": "COMMON"}, "item_class": {"id": 7},
                "item_subclass": {"id": 5}, "level": 5, "purchase_price": 55, "sell_price": 13, "max_count": 0}


def test_BZ16_the_catalog_comes_from_ranged_item_searches():
    requests = []

    def search(request):
        requests.append(request)
        if request.method == "POST":
            return HttpResponse(200, {}, json.dumps({"access_token": "t", "expires_in": 86399}).encode())
        return HttpResponse(200, {}, json.dumps({"results": [{"data": SEARCH_LINEN}], "pageCount": 1}).encode())

    records = bz.BlizzardClient(search, FakeClock(B_MS), CLIENT_ID, SECRET).search_items(2001, 3000)
    assert records == [SEARCH_LINEN]
    assert requests[-1].url == (f"{bz.API}/data/wow/search/item?namespace=static-forever-us"
                                "&id=[2001,3000]&orderby=id&_pageSize=1000&_page=1")
    assert bz.catalog_row(SEARCH_LINEN) == (2589, 2589, "Linen Cloth", 1, 5, 7, 5, 55, 13, None)
    bz.BlizzardClient(search, FakeClock(B_MS), CLIENT_ID, SECRET).search_items(2001, 3000, bz.FALLBACK_NAMESPACE)
    assert "namespace=static-classic1x-us&id=[2001,3000]" in requests[-1].url
    with pytest.raises(bz.BlizzardError, match="wider than 1000"):
        bz.BlizzardClient(search, FakeClock(B_MS), CLIENT_ID, SECRET).search_items(1, 1001)
    with pytest.raises(bz.BlizzardError, match="without an id"):
        bz.catalog_row({"id": 1})
