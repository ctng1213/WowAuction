"""Tests for ahsync/web/responses.py: the dashboard's JSON responses and its body reader."""
from __future__ import annotations

from ahsync.web.responses import Response, body_field, json_response


def test_json_responses_are_compact_utf8_and_carry_their_headers():
    reply = json_response(200, {"name": "Linen Cloth", "price": "19c · now"}, (("Allow", "POST"),))
    assert reply == Response(200, '{"name":"Linen Cloth","price":"19c · now"}'.encode("utf-8"),
                             "application/json; charset=utf-8", (("Allow", "POST"),))


def test_body_field_reads_one_field_of_a_json_object_and_none_otherwise():
    assert body_field(b'{"enabled": true}', "enabled") is True
    assert [body_field(b, "enabled") for b in (b'{"other": 1}', b"[true]", b"{bad", b"\xff")] == [None] * 4
