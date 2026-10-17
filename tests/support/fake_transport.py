"""A fake HTTP transport serving recorded AHF fixtures (SPEC §8.1: no network).

Routes by path, i.e. the URL without scheme, host and query: the API's
`https://auctionhouse.fyi/api/v1/me` is `api/v1/me`, the CDN's
`https://cdn.auctionhouse.fyi/candles/v1/h1/<m>/<hour>.json.gz` is
`candles/v1/h1/<m>/<hour>.json.gz`, matching `manifest.json`'s `source_path`.
Unknown paths are 404s. Faults are deterministic: "503 on this path 5 times",
"the 3rd request", "cut this file off at 60%", "crash when this is requested".
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ahsync.api.client import HttpRequest, HttpResponse, TransportError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ahf"
DEFAULT_MARKET = "forever.pvp.horde.us"


class SimulatedCrash(BaseException):
    """Stands in for a kill or power loss. A BaseException, so `except Exception` can't swallow it."""


def path_of(url: str) -> str:
    """Route key for a URL.

    Args:
        url: Absolute URL.

    Returns:
        The path without its leading slash, query or fragment.
    """
    return urlsplit(url).path.lstrip("/")


def encode(path: str, data: object) -> bytes:
    """Serialize a fixture the way AHF serves it: gzip for .gz paths, plain JSON otherwise.

    Args:
        path: Route path.
        data: JSON-serializable value.

    Returns:
        Body bytes. gzip uses mtime 0 so bodies are byte-identical across runs.
    """
    body = json.dumps(data).encode("utf-8")
    return gzip.compress(body, mtime=0) if path.endswith(".gz") else body


@dataclass
class Fault:
    """One injected fault. `remaining` None means every matching request."""

    matches: Callable[[int, str, HttpRequest], bool]   # (1-based request number, path, request) -> applies?
    kind: str                             # "status" | "truncate" | "network" | "crash"
    status: int = 200
    headers: dict[str, str] | None = None
    body: bytes = b""
    fraction: float = 1.0
    remaining: int | None = None


class FakeTransport:
    """Serves bodies by path, records every request, and injects faults on demand."""

    def __init__(self) -> None:
        """Start with no routes."""
        self.routes: dict[str, bytes] = {}
        self.requests: list[HttpRequest] = []
        self.faults: list[Fault] = []

    @classmethod
    def from_fixtures(cls, market: str = DEFAULT_MARKET) -> FakeTransport:
        """Serve every recorded fixture of a market at its source path.

        Args:
            market: Fixture folder under tests/fixtures/ahf.

        Returns:
            A transport with those routes.
        """
        transport = cls()
        folder = FIXTURES / market
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["fixtures"].values():
            data = json.loads((folder / entry["file"]).read_text(encoding="utf-8"))
            transport.serve(entry["source_path"], data)
        return transport

    # ------------------------------------------------------------ routes

    def serve(self, path: str, data: object) -> None:
        """Serve JSON at a path (gzip-encoded when the path ends in .gz).

        Args:
            path: Route path.
            data: JSON value.
        """
        self.routes[path] = encode(path, data)

    def serve_bytes(self, path: str, body: bytes) -> None:
        """Serve raw bytes at a path.

        Args:
            path: Route path.
            body: Exact body.
        """
        self.routes[path] = body

    def remove(self, path: str) -> None:
        """Make a path 404.

        Args:
            path: Route path.
        """
        self.routes.pop(path, None)

    def remove_matching(self, prefix: str) -> None:
        """Make every path with a prefix 404.

        Args:
            prefix: Path prefix, e.g. "candles/v1/h1/".
        """
        for path in [p for p in self.routes if p.startswith(prefix)]:
            del self.routes[path]

    # ------------------------------------------------------------ faults

    def fail(self, path: str, status: int, times: int | None = None, headers: dict[str, str] | None = None,
             body: bytes = b"") -> None:
        """Answer requests for a path with an HTTP status.

        Args:
            path: Route path.
            status: e.g. 429, 503, 403.
            times: How many requests fail; None = all of them.
            headers: Response headers, lower-case names (e.g. {"retry-after": "60"}).
            body: Response body.
        """
        self.faults.append(Fault(lambda _n, p, _r: p == path, "status", status, headers or {}, body, remaining=times))

    def fail_with_key(self, path: str, status: int) -> None:
        """Fail requests for a path that carry an Authorization header (an expired key).

        Args:
            path: Route path.
            status: HTTP status to return, e.g. 401.
        """
        self.faults.append(Fault(lambda _n, p, r: p == path and "Authorization" in r.headers, "status", status, {}))

    def fail_nth(self, number: int, status: int, headers: dict[str, str] | None = None) -> None:
        """Fail exactly the Nth request (1-based), whatever it is.

        Args:
            number: Request number.
            status: HTTP status to return.
            headers: Response headers, lower-case names.
        """
        self.faults.append(Fault(lambda n, _p, _r: n == number, "status", status, headers or {}, remaining=1))

    def truncate(self, path: str, fraction: float, times: int | None = None) -> None:
        """Serve only the first part of a path's body.

        Args:
            path: Route path.
            fraction: Share of the body kept, e.g. 0.6.
            times: How many requests are cut; None = all.
        """
        self.faults.append(Fault(lambda _n, p, _r: p == path, "truncate", fraction=fraction, remaining=times))

    def network_error(self, path: str, times: int | None = None) -> None:
        """Raise TransportError (a timeout) for a path.

        Args:
            path: Route path.
            times: How many requests fail; None = all.
        """
        self.faults.append(Fault(lambda _n, p, _r: p == path, "network", remaining=times))

    def crash_on(self, path: str) -> None:
        """Raise SimulatedCrash when a path is requested (once).

        Args:
            path: Route path.
        """
        self.faults.append(Fault(lambda _n, p, _r: p == path, "crash", remaining=1))

    def clear_faults(self) -> None:
        """Remove every fault (a restart with the network back)."""
        self.faults.clear()

    # ------------------------------------------------------------ inspection

    def paths(self) -> list[str]:
        """Paths requested so far, in order.

        Returns:
            Route paths (queries dropped).
        """
        return [path_of(r.url) for r in self.requests]

    def count(self, path: str) -> int:
        """How often a path was requested.

        Args:
            path: Route path.

        Returns:
            Request count.
        """
        return self.paths().count(path)

    # ------------------------------------------------------------ the transport

    def _active_fault(self, number: int, path: str, request: HttpRequest) -> Fault | None:
        """Find and consume the first fault matching a request.

        Args:
            number: 1-based request number.
            path: Route path.
            request: The request (for header-based faults).

        Returns:
            The fault, or None.
        """
        for fault in self.faults:
            if fault.remaining != 0 and fault.matches(number, path, request):
                if fault.remaining is not None:
                    fault.remaining -= 1
                return fault
        return None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Answer one request.

        Args:
            request: URL and headers; recorded in `requests`.

        Returns:
            The routed body (200), a fault's response, or 404.

        Raises:
            TransportError: For a network fault.
            SimulatedCrash: For a crash fault.
        """
        self.requests.append(request)
        path = path_of(request.url)
        fault = self._active_fault(len(self.requests), path, request)
        if fault is not None and fault.kind == "crash":
            raise SimulatedCrash(path)
        if fault is not None and fault.kind == "network":
            raise TransportError("timed out")
        if fault is not None and fault.kind == "status":
            return HttpResponse(fault.status, dict(fault.headers or {}), fault.body)
        if path not in self.routes:
            return HttpResponse(404, {}, b'{"error":"not found"}')
        body = self.routes[path]
        if fault is not None and fault.kind == "truncate":
            body = body[: int(len(body) * fault.fraction)]
        return HttpResponse(200, {}, body)
