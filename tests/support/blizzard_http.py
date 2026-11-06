"""A scripted Blizzard API over the recorded fixtures (tests/fixtures/blizzard/, M28), for the cloud job's tests.

Serves the token POST and both endpoints of house 113 as Blizzard does: gzipped, with Last-Modified, and
304 when If-Modified-Since equals the current stamp. A test changes `stamp` to publish a new snapshot, or
`status` to inject a fault; every request is recorded.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from ahsync.collect.client import HttpRequest, HttpResponse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "blizzard"
B = "Fri, 06 Nov 2026 14:02:40 GMT"
B_MS = 1_793_973_760_000
C = "Fri, 06 Nov 2026 15:02:05 GMT"


def recorded(name: str) -> bytes:
    """A recorded fixture's JSON as compact bytes.

    Args:
        name: File stem, e.g. "house_113_auctions".

    Returns:
        The body Blizzard would send (decompressed).
    """
    return json.dumps(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")), separators=(",", ":")).encode()


class BlizzardHttp:
    """The transport: the token endpoint plus house 113's two endpoints."""

    def __init__(self, stamp: str = B) -> None:
        self.stamp = stamp
        self.status: dict[str, int] = {}          # "auctions" / "commodities" / "token" -> forced status
        self.requests: list[HttpRequest] = []
        self.tokens = 0
        self.bodies = {"auctions": recorded("house_113_auctions"), "commodities": recorded("house_113_commodities")}

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.method == "POST":
            self.tokens += 1
            status = self.status.get("token", 200)
            body = json.dumps({"access_token": f"token-{self.tokens:04d}", "expires_in": 86399}).encode()
            return HttpResponse(status, {}, body if status == 200 else b"")
        part = "commodities" if "/commodities" in request.url else "auctions"
        if part in self.status:
            return HttpResponse(self.status[part], {}, b"")
        if request.headers.get("If-Modified-Since") == self.stamp:
            return HttpResponse(304, {"last-modified": self.stamp}, b"")
        return HttpResponse(200, {"last-modified": self.stamp, "content-encoding": "gzip"},
                            gzip.compress(self.bodies[part], mtime=0))
