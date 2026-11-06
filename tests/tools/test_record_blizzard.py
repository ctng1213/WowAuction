"""Tests for tools/record_blizzard.py: the M28 smoke-test recorder (SPEC §2.4, §6 items 23-25)."""
from __future__ import annotations

import base64
import gzip
import json

import pytest

from tools import record_blizzard as rb

CLIENT_ID, SECRET, TOKEN = "client-id-1234", "client-secret-5678", "token-abcdefgh"
STAMP = "Fri, 06 Nov 2026 14:02:40 GMT"


def house_body(auctions):
    """A house response as Blizzard shapes it."""
    return json.dumps({"_links": {"self": {"href": "x"}}, "id": 113, "auctions": auctions}).encode("utf-8")


class FakeHttp:
    """Answers GETs by path and namespace, POSTs with a token; records every request."""

    def __init__(self, routes=None, token_status=200):
        self.routes = routes or {}
        self.token_status = token_status
        self.gets, self.posts = [], []

    def get(self, url, headers):
        self.gets.append((url, headers))
        path, _, query = url.removeprefix(rb.API).partition("?namespace=")
        status, body, extra = self.routes.get((path, query), (404, b'{"code": 404}', {}))
        return rb.Response(status, {"last-modified": STAMP, "content-type": "application/json", **extra}, body)

    def post(self, url, data, headers):
        self.posts.append((url, data, headers))
        return rb.Response(self.token_status, {}, json.dumps({"access_token": TOKEN, "expires_in": 86399}).encode())


def test_credentials_come_from_the_environment_first(tmp_path):
    (tmp_path / ".ahsync").mkdir()
    (tmp_path / ".ahsync" / "blizzard").write_text("file-id\nfile-secret\n", encoding="utf-8")
    env = {"BLIZZARD_CLIENT_ID": "env-id", "BLIZZARD_CLIENT_SECRET": "env-secret"}
    assert rb.load_credentials(env, tmp_path) == ("env-id", "env-secret")


def test_credentials_file_is_two_lines_and_blank_lines_are_ignored(tmp_path):
    (tmp_path / ".ahsync").mkdir()
    path = tmp_path / ".ahsync" / "blizzard"
    path.write_text("\n  file-id  \n\nfile-secret", encoding="utf-8")
    path.chmod(0o600)
    assert rb.load_credentials({}, tmp_path) == ("file-id", "file-secret")


def test_missing_credentials_say_where_to_put_them_and_show_no_value(tmp_path):
    (tmp_path / ".ahsync").mkdir()
    (tmp_path / ".ahsync" / "blizzard").write_text("only-one-line\n", encoding="utf-8")
    with pytest.raises(rb.CredentialsMissing) as error:
        rb.load_credentials({"BLIZZARD_CLIENT_ID": "half"}, tmp_path)
    assert "BLIZZARD_CLIENT_SECRET" in str(error.value) and str(tmp_path / ".ahsync" / "blizzard") in str(error.value)   # the OS's separator
    assert "only-one-line" not in str(error.value) and "half" not in str(error.value)


def test_the_token_request_uses_basic_auth_and_a_failure_shows_only_its_status():
    http = FakeHttp()
    assert rb.request_token(http.post, CLIENT_ID, SECRET) == TOKEN
    url, data, headers = http.posts[0]
    assert url == rb.TOKEN_URL and data == b"grant_type=client_credentials"
    assert base64.b64decode(headers["Authorization"].removeprefix("Basic ")).decode() == f"{CLIENT_ID}:{SECRET}"
    with pytest.raises(rb.RecordError, match="answered 401") as error:
        rb.request_token(FakeHttp(token_status=401).post, CLIENT_ID, SECRET)
    assert SECRET not in str(error.value)


def test_trim_keeps_the_first_auctions_and_a_few_of_each_named_item():
    auctions = [{"id": i, "item": {"id": 9999}, "quantity": 1, "buyout": 100} for i in range(30)]
    auctions += [{"id": 100 + i, "item": {"id": 2589}, "quantity": 20, "unit_price": 125} for i in range(8)]
    kept = rb.trim(house_body(auctions))["auctions"]
    assert [a["id"] for a in kept] == list(range(20)) + [100, 101, 102, 103, 104]
    assert rb.trim(b'{"id": 115, "name": "Horde"}') == {"id": 115, "name": "Horde"}   # metadata only: unchanged


def test_summarize_counts_bid_only_auctions_and_hashes_independently_of_order():
    a = {"id": 1, "item": {"id": 8180}, "quantity": 1, "buyout": 1500, "time_left": "SHORT"}
    b = {"id": 2, "item": {"id": 8180}, "quantity": 1, "bid": 900, "time_left": "LONG"}
    first, second = rb.summarize(house_body([a, b])), rb.summarize(house_body([b, a]))
    assert first["records"] == 2 and first["bid_only"] == 1
    assert first["auctions_sha256"] == second["auctions_sha256"]          # duplicate houses compare equal
    assert rb.summarize(b'{"id": 112}') == {"records": 0, "bid_only": 0, "auctions_sha256": None}


def recording_routes():
    """A ruleset with house 113 full, house 115 metadata only, and the static probe in one namespace."""
    auctions = [{"id": 39643, "item": {"id": 8180}, "quantity": 1, "buyout": 1500, "time_left": "SHORT"},
                {"id": 39644, "item": {"id": 8180}, "quantity": 1, "bid": 900, "time_left": "LONG"}]
    commodities = [{"id": 2455, "item": {"id": 2589}, "quantity": 7, "unit_price": 12345, "time_left": "VERY_LONG"}]
    ns = rb.NAMESPACE
    return {("/data/wow/ruleset/index", ns): (200, b'{"rulesets": [{"id": 2}]}', {}),
            ("/data/wow/ruleset/2", ns): (200, b'{"id": 2, "name": "Normal"}', {}),
            ("/data/wow/ruleset/2/auctions/index", ns): (200, b'{"auctions": [{"id": 113}]}', {}),
            ("/data/wow/ruleset/2/auctions/113", ns): (200, house_body(auctions), {}),
            ("/data/wow/ruleset/2/auctions/113/commodities", ns): (200, house_body(commodities), {}),
            ("/data/wow/ruleset/2/auctions/115", ns): (200, b'{"id": 115, "name": "Horde Auction House"}', {}),
            ("/data/wow/ruleset/2/auctions/115/commodities", ns): (200, b'{"id": 115}', {}),
            ("/data/wow/item/2589", "static-forever-us"): (200, b'{"id": 2589, "name": "Linen Cloth"}', {})}


def test_record_writes_full_raw_trimmed_fixtures_and_a_summary_with_no_secret(tmp_path):
    http = FakeHttp(recording_routes())
    raw, fixtures = tmp_path / "raw", tmp_path / "fixtures"
    summary = rb.record(http.get, TOKEN, raw, fixtures, 2, (113, 115), rb.Secrets([CLIENT_ID, SECRET, TOKEN]))
    assert summary["house_113_auctions"]["records"] == 2 and summary["house_113_auctions"]["bid_only"] == 1
    assert summary["house_115_auctions"]["records"] == 0                     # an empty house is not an error
    assert summary["house_113_auctions"]["headers"]["last-modified"] == STAMP
    assert summary["item_2589_static_us"]["status"] == 404                   # recorded as an answer, not written
    assert not (raw / "item_2589_static_us.json.gz").exists()
    full = json.loads(gzip.decompress((raw / "house_113_commodities.json.gz").read_bytes()))
    assert full["auctions"][0]["unit_price"] == 12345
    assert json.loads((fixtures / "item_2589_static_forever_us.json").read_text())["name"] == "Linen Cloth"
    for path in list(raw.iterdir()) + list(fixtures.iterdir()):
        data = path.read_bytes()
        data = gzip.decompress(data) if path.suffix == ".gz" else data
        assert all(s.encode() not in data for s in (CLIENT_ID, SECRET, TOKEN)), path.name
    for url, headers in http.gets:
        assert headers["Authorization"] == f"Bearer {TOKEN}" and headers["Accept-Encoding"] == "gzip"
        assert "namespace=" in url


def test_a_response_echoing_the_token_is_refused_and_not_written(tmp_path):
    routes = {("/data/wow/ruleset/index", rb.NAMESPACE): (200, json.dumps({"echo": TOKEN}).encode(), {})}
    with pytest.raises(rb.RecordError, match="would contain a credential"):
        rb.record(FakeHttp(routes).get, TOKEN, tmp_path / "raw", tmp_path / "fx", 2, (), rb.Secrets([TOKEN]))
    assert not (tmp_path / "raw").exists()


def test_find_marker_names_the_house_holding_the_auction(tmp_path):
    raw = tmp_path / "raw"
    rb.record(FakeHttp(recording_routes()).get, TOKEN, raw, tmp_path / "fx", 2, (113, 115), rb.Secrets([TOKEN]))
    assert rb.find_marker(raw, 2589, 7, 12345) == [("house_113_commodities", 2455)]
    assert rb.find_marker(raw, 8180, 1, 1500) == [("house_113_auctions", 39643)]   # a regular auction by buyout
    assert rb.find_marker(raw, 2589, 7, 12346) == []


def test_watch_logs_each_new_last_modified_and_renews_the_token_on_401():
    stamps = iter([(200, "A"), (304, "A"), (401, None), (200, "B")])
    sent = []

    def get(url, headers):
        sent.append(headers)
        status, stamp = next(stamps)
        return rb.Response(status, {"last-modified": stamp} if stamp else {}, b"{}")

    renewed = []

    def token(renew):
        if renew:
            renewed.append(True)
        return "t2" if renewed else "t1"

    changes = rb.watch(get, token, "/p", polls=3, every_s=300, now=lambda: 1_762_437_760, sleep=lambda s: None)
    assert [c["last_modified"] for c in changes] == ["A", "B"]
    assert "If-Modified-Since" not in sent[0] and sent[1]["If-Modified-Since"] == "A"
    assert renewed == [True] and sent[3]["Authorization"] == "Bearer t2"


def test_main_without_credentials_fails_before_any_request(tmp_path, capsys):
    http = FakeHttp()
    assert rb.main(["record"], env={}, home=tmp_path, get=http.get, post=http.post) == 1
    assert http.posts == [] and http.gets == []
    assert "BLIZZARD_CLIENT_ID" in capsys.readouterr().err


def test_no_option_takes_a_credential():
    parser = rb._parser()
    commands = next(a for a in parser._actions if a.dest == "command").choices
    assert isinstance(commands, dict)
    options = {a.dest for sub in commands.values() for a in sub._actions}
    assert not any(word in name for name in options for word in ("secret", "token", "client", "password"))
