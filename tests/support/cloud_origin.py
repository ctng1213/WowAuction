"""CloudFront over a LocalStore for the desktop's cloud tests (SPEC §2.4): objects with ETags, 304 on a match."""
from __future__ import annotations

from ahcloud.store import LocalStore
from ahsync.collect.client import HttpRequest, HttpResponse

ORIGIN = "https://dtb79di8mf92m.cloudfront.net"


class Origin:
    """A transport serving a LocalStore's objects at ORIGIN; `fail` raises instead (offline)."""

    def __init__(self, store: LocalStore) -> None:
        self.store, self.requests, self.fail = store, [], None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.fail:
            raise self.fail
        found = self.store.get(request.url.removeprefix(ORIGIN + "/"))
        if found is None:
            return HttpResponse(404, {}, b"")
        etag = f'"{found.etag}"'
        if request.headers.get("If-None-Match") == etag:
            return HttpResponse(304, {"etag": etag}, b"")
        return HttpResponse(200, {"etag": etag}, found.data)
