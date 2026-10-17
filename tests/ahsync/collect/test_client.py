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
                               Unauthorized, UrllibTransport, backoff_seconds, build_request, decode_body,
                               retry_after_seconds)
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
