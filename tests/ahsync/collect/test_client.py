"""HTTP client (SPEC §2.1, §5.2): retries, 429, gzip, secrets. Scenario S5."""
from __future__ import annotations

import gzip
import io
import logging
import urllib.error
import urllib.request

import pytest

from ahsync.collect.client import (ApiClient, ApiError, BadFile, FeatureUnavailable, HttpRequest, NotFound,
                               RateLimited, RedactingFilter, Redactor, RetriesExhausted, TransportError,
                               Unauthorized, UrllibTransport, backoff_seconds, build_request, cache_max_age_s,
                               decode_body, retry_after_seconds)
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import FakeTransport

API = "https://auctionhouse.fyi/api/v1"
KEY = "ak_client_test_secret"


def client_with(transport: FakeTransport, clock: FakeClock, key: str = "") -> ApiClient:
    """A client over a fake transport."""
    return ApiClient(transport, clock, key)


def test_S5_429_with_retry_after_60_waits_60_seconds_then_succeeds():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve("api/v1/me", {"features": []})
    transport.fail("api/v1/me", 429, times=1, headers={"retry-after": "60"})

    data = client_with(transport, clock).get_json(f"{API}/me")

    assert data == {"features": []}
    assert clock.sleeps == [60]
    assert transport.count("api/v1/me") == 2


def test_backoff_doubles_and_caps_at_60_seconds():
    assert [backoff_seconds(n) for n in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert retry_after_seconds({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, 2) == 4
    assert retry_after_seconds({}, 0) == 1


def test_network_errors_and_5xx_are_retried_then_give_up():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve("x.json", {"ok": 1})
    transport.network_error("x.json", times=2)
    assert client_with(transport, clock).get_json("https://cdn.example/x.json") == {"ok": 1}
    assert clock.sleeps == [1, 2]
    transport.fail("x.json", 500)
    with pytest.raises(RetriesExhausted):
        client_with(transport, clock).get_json("https://cdn.example/x.json")


@pytest.mark.parametrize("status, error", [(404, NotFound), (400, ApiError), (401, ApiError)])
def test_client_errors_are_not_retried(status, error):
    clock, transport = FakeClock(), FakeTransport()
    transport.fail("x.json", status)
    with pytest.raises(error):
        client_with(transport, clock).get_json("https://cdn.example/x.json")
    assert clock.sleeps == [] and transport.count("x.json") == 1


def test_403_names_the_missing_feature():
    clock, transport = FakeClock(), FakeTransport()
    transport.fail("a", 403, body=b'{"feature": "books"}')
    transport.fail("b", 403, body=b"denied")
    with pytest.raises(FeatureUnavailable) as error:
        client_with(transport, clock).get_json("https://x/a")
    assert error.value.feature == "books"
    with pytest.raises(FeatureUnavailable) as error:
        client_with(transport, clock).get_json("https://x/b")
    assert error.value.feature == "unknown"


def test_gzip_bodies_are_decoded_and_truncation_is_a_bad_file():
    assert decode_body(gzip.compress(b'{"a": 1}')) == {"a": 1}
    assert decode_body(b'{"a": 1}') == {"a": 1}
    for body in (gzip.compress(b'{"a": 1}')[:-6], b'{"a": ', b"\xff\xfe"):
        with pytest.raises(BadFile):
            decode_body(body)


def test_key_goes_only_in_the_authorization_header():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve("api/v1/me", {})
    client = client_with(transport, clock, KEY)
    client.get_json(f"{API}/me")
    client.get_json(f"{API}/me", auth=False)
    assert transport.requests[0].headers["Authorization"] == "Bearer " + KEY
    assert "Authorization" not in transport.requests[1].headers
    assert all(KEY not in r.url for r in transport.requests)


def test_errors_never_contain_secrets():
    clock, transport = FakeClock(), FakeTransport()
    client = client_with(transport, clock, KEY)
    client.redactor.add("cap-token-123")
    transport.fail("t/cap-token-123/1.json", 403, body=('{"feature": "%s"}' % KEY).encode())
    with pytest.raises(FeatureUnavailable) as error:
        client.get_json("https://x/t/cap-token-123/1.json")
    assert KEY not in str(error.value) and "cap-token-123" not in str(error.value)


def test_api_requests_are_rate_limited_but_cdn_files_are_not():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve("api/v1/me", {})
    transport.serve("cdn.json", {})
    client = ApiClient(transport, clock, per_minute=2)
    for _ in range(3):
        client.get_json("https://cdn.example/cdn.json")
    assert clock.sleeps == []
    for _ in range(3):
        client.get_json(f"{API}/me")
    assert clock.sleeps == [60]


def test_redactor_and_log_filter_scrub_secrets():
    redactor = Redactor()
    redactor.add(KEY)
    redactor.add("ab")          # too short to scrub safely
    redactor.add(None)
    assert redactor.scrub(f"key={KEY} ab") == "key=*** ab"
    record = logging.LogRecord("ahsync", logging.INFO, __file__, 1, "using %s", (KEY,), None)
    assert RedactingFilter(redactor).filter(record) and record.getMessage() == "using ***"
    try:
        raise ValueError(KEY)
    except ValueError:
        import sys
        record = logging.LogRecord("ahsync", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    RedactingFilter(redactor).filter(record)
    assert KEY not in record.exc_text and record.exc_info is None


class FakeResponse(io.BytesIO):
    """Stands in for urlopen's response object."""

    status = 200
    headers = {"Content-Type": "application/json"}


def test_urllib_transport_maps_responses_and_errors():
    seen = {}

    def opener(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse(b"{}")

    response = UrllibTransport(opener)(HttpRequest("https://x/a", {"Authorization": "Bearer k"}))
    assert (response.status, response.headers, response.body) == (200, {"content-type": "application/json"}, b"{}")
    assert seen["auth"] == "Bearer k"

    def http_error(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "5"}, io.BytesIO(b"x"))

    response = UrllibTransport(http_error)(HttpRequest("https://x/a"))
    assert (response.status, response.headers["retry-after"]) == (429, "5")

    def down(request, timeout):
        raise urllib.error.URLError("no route")

    with pytest.raises(TransportError):
        UrllibTransport(down)(HttpRequest("https://x/a"))


def test_401_with_key_retries_once_without_it_and_drops_the_key():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve("api/v1/me", {"ok": 1})
    transport.fail("api/v1/me", 401, times=1)
    client = client_with(transport, clock, KEY)

    assert client.get_json(f"{API}/me") == {"ok": 1}
    assert client.key_rejected and not client.has_key and not client.key_used
    assert [("Authorization" in r.headers) for r in transport.requests] == [True, False]
    client.get_json(f"{API}/me")
    assert "Authorization" not in transport.requests[-1].headers      # rest of the run is keyless


def test_401_without_a_key_or_twice_is_unauthorized():
    clock, transport = FakeClock(), FakeTransport()
    transport.fail("a", 401)
    with pytest.raises(Unauthorized):
        client_with(transport, clock).get_json("https://x/a")
    with pytest.raises(Unauthorized):
        client_with(transport, clock, KEY).get_json("https://x/a")


def test_retry_after_over_300_seconds_skips_the_run():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve("api/v1/me", {})
    transport.fail("api/v1/me", 429, times=1, headers={"retry-after": "300"})
    client_with(transport, clock).get_json(f"{API}/me")
    assert clock.sleeps == [300]
    transport.fail("api/v1/me", 429, times=1, headers={"retry-after": "301"})
    with pytest.raises(RateLimited):
        client_with(transport, clock).get_json(f"{API}/me")
    assert clock.sleeps == [300]


def test_authorization_is_not_forwarded_on_redirect():
    req = build_request(HttpRequest("https://auctionhouse.fyi/api/v1/me",
                                    {"Authorization": "Bearer " + KEY, "User-Agent": "AHSync"}))
    assert req.get_header("Authorization") == "Bearer " + KEY          # sent on the first request
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        req, io.BytesIO(), 302, "Found", {}, "https://elsewhere.example/me")
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("User-agent") == "AHSync"


# ---------------------------------------------------------------- conditional GET (SPEC §2.3)

LEDGER = "https://api.ahledger.com/v1/pricetable/forever.pvp.horde.us"
LEDGER_PATH = "v1/pricetable/forever.pvp.horde.us"


def test_conditional_get_sends_if_none_match_and_gets_304_for_an_unchanged_table():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve_etag(LEDGER_PATH, b"AHL1|x", '"abc"', {"cache-control": "max-age=60"})
    client = client_with(transport, clock)
    first = client.get_conditional(LEDGER)
    assert (first.status, first.body, first.headers["etag"]) == (200, b"AHL1|x", '"abc"')   # body left undecoded
    assert "If-None-Match" not in transport.requests[0].headers
    second = client.get_conditional(LEDGER, etag='"abc"')
    assert (second.status, second.body) == (304, b"")
    assert transport.requests[1].headers["If-None-Match"] == '"abc"'
    assert client.get_conditional(LEDGER, etag='"old"').status == 200      # a different ETag gets the new body
    assert all("Authorization" not in r.headers for r in transport.requests)


def test_conditional_get_retries_like_get_json_and_raises_the_same_errors():
    clock, transport = FakeClock(), FakeTransport()
    transport.serve_bytes(LEDGER_PATH, b"ok")
    transport.fail(LEDGER_PATH, 503, times=2)
    transport.network_error(LEDGER_PATH, times=1)
    assert client_with(transport, clock).get_conditional(LEDGER).body == b"ok"
    assert transport.count(LEDGER_PATH) == 4        # two 503s, one timeout, then the body
    with pytest.raises(NotFound):
        client_with(transport, clock).get_conditional("https://api.ahledger.com/v1/pricetable/none")
    transport.fail(LEDGER_PATH, 429, times=1, headers={"retry-after": "7"})
    assert client_with(transport, clock).get_conditional(LEDGER).status == 200
    transport.fail(LEDGER_PATH, 418)
    with pytest.raises(ApiError):
        client_with(transport, clock).get_conditional(LEDGER)


def test_a_304_is_an_error_for_get_json():
    transport = FakeTransport()
    transport.fail("api/v1/me", 304)
    with pytest.raises(ApiError):
        client_with(transport, FakeClock()).get_json(f"{API}/me")


def test_cache_max_age_reads_the_directive_or_returns_none():
    assert cache_max_age_s({"cache-control": "public, max-age=60"}) == 60
    assert cache_max_age_s({"cache-control": "MAX-AGE=300, must-revalidate"}) == 300
    assert cache_max_age_s({"cache-control": 'max-age="5"'}) == 5
    assert cache_max_age_s({"cache-control": "no-store"}) is None
    assert cache_max_age_s({"cache-control": "max-age=soon"}) is None
    assert cache_max_age_s({}) is None


def test_ahledger_fixture_routes_are_distinct_and_the_table_has_an_etag():
    from ahsync.collect.ahledger import parse_markets, parse_pricetable
    transport = FakeTransport.serve_ahledger_fixtures()
    client = client_with(transport, FakeClock())
    base = "https://api.ahledger.com/v1"
    assert "forever.pvp.horde.us" in parse_markets(client.get_json(f"{base}/markets", auth=False))   # recorded live
    seven = client.get_json(f"{base}/prices/forever.pvp.horde.us/2589?range=7d", auth=False)
    ninety = client.get_json(f"{base}/prices/forever.pvp.horde.us/2589?range=90d", auth=False)
    assert (seven["range"], len(seven["series"])) == ("7d", 0)          # no scan in the 7 days before recording
    assert (ninety["range"], len(ninety["series"])) == ("90d", 30)      # one bucket per scanned day
    table = client.get_conditional(f"{base}/pricetable/forever.pvp.horde.us")
    assert table.headers["content-encoding"] == "gzip" and cache_max_age_s(table.headers) == 60
    assert parse_pricetable(table.body, "forever.pvp.horde.us").updated_s == 1792599906   # Wed 21 Oct 16:25 UTC
    assert client.get_conditional(f"{base}/pricetable/forever.pvp.horde.us", table.headers["etag"]).status == 304


def test_a_paced_client_spaces_requests_evenly_and_an_unpaced_one_bursts():
    from tests.support.fake_clock import FakeClock
    from tests.support.fake_transport import FakeTransport
    clock = FakeClock()
    transport = FakeTransport()
    transport.serve("v1/markets", [])
    start = clock.now_ms()
    paced = ApiClient(transport, clock, api_base="https://api.ahledger.com", per_minute=300, paced=True)
    times = []
    for _ in range(3):
        paced.get_json("https://api.ahledger.com/v1/markets", auth=False)
        times.append(clock.now_ms() - start)
    assert times == [0, 200, 400]                                  # 60 000 / 300 ms apart
    plain = ApiClient(transport, clock, api_base="https://api.ahledger.com", per_minute=300)
    before = clock.now_ms()
    for _ in range(3):
        plain.get_json("https://api.ahledger.com/v1/markets", auth=False)
    assert clock.now_ms() == before


def test_FM53_a_body_cut_off_mid_read_is_a_transport_error():
    import http.client

    class CutOff(FakeResponse):
        def read(self):
            raise http.client.IncompleteRead(b"x" * 1000, 1000)

    with pytest.raises(TransportError):
        UrllibTransport(lambda request, timeout: CutOff(b""))(HttpRequest("https://x/a"))


def test_FM63_a_failure_below_http_names_the_wrapped_cause_s_type_never_its_text():
    import ssl

    def failing(error):
        def opener(request, timeout):
            raise error
        return UrllibTransport(opener)

    cases = [(urllib.error.URLError(ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")),
              "URLError (SSLCertVerificationError)"),
             (urllib.error.URLError(ConnectionRefusedError(61, "refused")), "URLError (ConnectionRefusedError)"),
             (TimeoutError("timed out"), "TimeoutError"),
             (urllib.error.URLError("unknown url type: https://x/a?secret"), "URLError")]
    for error, name in cases:
        with pytest.raises(TransportError) as raised:
            failing(error)(HttpRequest("https://x/a"))
        assert str(raised.value) == name


def test_FM62_a_traceback_an_earlier_filter_already_formatted_is_still_scrubbed():
    first, second = Redactor(), Redactor()
    second.add("SECRETKEY123")
    try:
        raise ValueError("bad key SECRETKEY123")
    except ValueError:
        import sys
        record = logging.LogRecord("ahsync", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    record.stack_info = "Stack (most recent call last): SECRETKEY123"
    RedactingFilter(first).filter(record)          # formats exc_text, clears exc_info, scrubs nothing
    RedactingFilter(second).filter(record)
    assert "SECRETKEY123" not in record.exc_text and "bad key ***" in record.exc_text
    assert "SECRETKEY123" not in record.stack_info



def test_a_post_keeps_its_method_and_body_and_its_authorization_off_redirects():
    from ahsync.collect.client import HttpRequest, build_request
    req = build_request(HttpRequest("https://oauth.battle.net/token", {"Authorization": "Basic x", "Content-Type": "f"},
                                    method="POST", body=b"grant_type=client_credentials"))
    assert req.get_method() == "POST" and req.data == b"grant_type=client_credentials"
    assert "Authorization" not in req.headers and req.unredirected_hdrs["Authorization"] == "Basic x"
    assert build_request(HttpRequest("https://x/y")).get_method() == "GET"      # the default is unchanged



def test_SEC16_a_bounded_transport_refuses_a_longer_body_and_redirects_are_not_followed():
    import io
    import urllib.error
    from ahsync.collect.client import HttpRequest, TransportError, UrllibTransport, _RefuseRedirects

    class Answer(io.BytesIO):
        status, headers = 200, {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    big = UrllibTransport(opener=lambda req, timeout: Answer(b"x" * 11), max_bytes=10)
    with pytest.raises(TransportError, match="over 10 bytes"):
        big(HttpRequest("https://x.cloudfront.net/intel/m/latest-10.json"))
    assert UrllibTransport(opener=lambda req, timeout: Answer(b"x" * 10), max_bytes=10)(HttpRequest("https://x")).body == b"x" * 10
    assert _RefuseRedirects().redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None

    def redirected(req, timeout):
        raise urllib.error.HTTPError("https://x", 302, "Found", {"Location": "https://elsewhere"}, io.BytesIO(b""))

    assert UrllibTransport(opener=redirected, max_bytes=10)(HttpRequest("https://x")).status == 302   # handed back
