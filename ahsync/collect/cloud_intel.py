"""Reading the cloud's published market summary (SPEC §2.4 "AHSync with a cloud market", BZ17-BZ19, SEC16).

`poll` asks for `<intel_url>/intel/<market>/latest-<schema>.json` with If-None-Match. When the pointer names a
new document it fetches it and checks it before anyone sees it: the key's exact shape, the size compressed and
decompressed, the hash against the pointer, the schema, and that it is this market's. A good document is
returned for the caller to stage with its re-plan; anything else leaves the last good one in place and says why
on the Status page. One plain GET per request through the transport: no retries (the next poll is the retry),
no AHF key, no redirects followed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import zlib
from dataclasses import dataclass

from ahsync.collect.client import HttpRequest, Transport, TransportError
from ahsync.db.sqlite import Database
from ahsync.intel.contract import INTEL_SCHEMA_VERSION, IntelVersionError, MarketIntelligence, from_json

log = logging.getLogger("ahsync.cloud_intel")
SRC = "cloud"                                  # source_state's row for the summary (its ETag and last fetch)
MAX_BYTES = 256 * 1024 * 1024                  # a document over this, compressed or not, is refused (SEC16);
                                               # 64 MB until 0.1.8: the real market's reached 88 MB on 2026-11-08
USER_AGENT = "AHSync/0.1"


@dataclass(frozen=True)
class PollResult:
    """What one poll found."""

    state: str                                 # "new", "unchanged", "failed"
    intel: MarketIntelligence | None = None    # the checked document, for "new"
    reason: str = ""                           # why it failed, for the log and Status
    etag: str | None = None                    # the pointer's ETag, for "new": remember() it once staged
    sha256: str | None = None                  # the document's hash, for "new"
    body: bytes | None = None                  # the document's gzip as downloaded, for "new": stored as it is (BZ43)


def pointer_url(intel_url: str, market: str) -> str:
    """The pointer's URL.

    Args:
        intel_url: The configured origin, https://<host>.
        market: Market id.

    Returns:
        "<intel_url>/intel/<market>/latest-<INTEL_SCHEMA_VERSION>.json".
    """
    return f"{intel_url}/intel/{market}/latest-{INTEL_SCHEMA_VERSION}.json"


def check_key(market: str, key: object) -> bool:
    """Whether a pointer's document key is exactly this market's document by hash (SEC16).

    Args:
        market: Market id.
        key: The pointer's `key`.

    Returns:
        True for "intel/<market>/<64 hex>.json.gz" and nothing else (no "..", no absolute URL, no encoding).
    """
    return isinstance(key, str) and re.fullmatch(rf"intel/{re.escape(market)}/[0-9a-f]{{64}}\.json\.gz", key) is not None


def inflate(data: bytes) -> bytes:
    """A gzip body, decompressed with a bound (SEC16: a small gzip can inflate without limit).

    Args:
        data: The gzipped document.

    Returns:
        Its bytes.

    Raises:
        ValueError: It inflates past MAX_BYTES, or it is not gzip.
    """
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = inflater.decompress(data, MAX_BYTES + 1)
    except zlib.error:
        raise ValueError("not a gzip document") from None
    if len(out) > MAX_BYTES or inflater.unconsumed_tail:
        raise ValueError(f"inflates past {MAX_BYTES} bytes")
    return out


def _get(transport: Transport, url: str, etag: str | None = None) -> tuple[int, dict[str, str], bytes]:
    """One plain GET.

    Args:
        transport: The HTTP transport.
        url: The URL.
        etag: An If-None-Match value, or None.

    Returns:
        (status, headers, body).

    Raises:
        TransportError: The network failed, or the body is over the transport's bound.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    response = transport(HttpRequest(url, headers))
    return response.status, response.headers, response.body


def fetch_document(transport: Transport, intel_url: str, market: str,
                   pointer: dict) -> tuple[MarketIntelligence, bytes]:
    """The document a pointer names, checked (SEC16, FM71, AR2).

    Args:
        transport: The HTTP transport.
        intel_url: The configured origin.
        market: Market id.
        pointer: The parsed pointer.

    Returns:
        (the intelligence, its gzip as downloaded and checked).

    Raises:
        ValueError: The key, the size, the hash, the content or the market is wrong (the message says which).
        IntelVersionError: The document is from a newer schema than this AHSync reads.
        TransportError: The network failed.
    """
    key = pointer.get("key")
    if not check_key(market, key):
        raise ValueError(f"the pointer names a key outside intel/{market}/")
    status, _, data = _get(transport, f"{intel_url}/{key}")
    if status != 200:
        raise ValueError(f"the document answered {status}")
    if len(data) > MAX_BYTES:
        raise ValueError(f"the document is over {MAX_BYTES} bytes")
    if hashlib.sha256(data).hexdigest() != pointer.get("sha256"):
        raise ValueError("the document's hash differs from the pointer's")
    intel = from_json(inflate(data).decode("utf-8"))
    if intel.market != market:
        raise ValueError(f"the document is {intel.market}'s, not {market}'s")
    return intel, data


def poll(db: Database, transport: Transport, intel_url: str, market: str, now_ms: int) -> PollResult:
    """Ask for a newer summary and fetch it when there is one (SPEC §2.4, BZ17-BZ19).

    Args:
        db: Database (source_state "cloud" keeps the pointer's ETag and the last fetch).
        transport: The HTTP transport (a no-redirect, bounded one in production).
        intel_url: The configured origin, https://<host>.
        market: Market id.
        now_ms: Now, UTC ms.

    Returns:
        "new" with the checked document (the caller stages it, then calls remember(), so a failed re-plan
        fetches it again next time), "unchanged" (304, or the pointer names the document already held), or
        "failed" with the reason; the Status lines `cloud_schema:<market>` and `cloud_fetch:<market>` say why.
    """
    etag, _, _, held = db.source_state(market, SRC)
    try:
        status, headers, body = _get(transport, pointer_url(intel_url, market), etag)
        if status == 304:
            with db.transaction():
                db.set_source_state(market, SRC, fetched_ms=now_ms)          # the Status line's last check
            return PollResult("unchanged")
        if status != 200:
            raise ValueError(f"the pointer answered {status}")
        pointer = json.loads(body)
        if pointer.get("sha256") == held:
            remember(db, market, headers.get("etag"), held, now_ms)
            return PollResult("unchanged")
        db.intel_cache.release(market)                # never two parsed summaries at once (BZ43)
        intel, body = fetch_document(transport, intel_url, market, pointer)
    except IntelVersionError as error:
        db.set_status(f"cloud_schema:{market}", "error", "cloud_schema", {"market": market, "detail": str(error)}, now_ms)
        return PollResult("failed", reason=str(error))
    except (ValueError, KeyError, TypeError, TransportError) as error:
        reason = f"{type(error).__name__}: {error}"
        log.warning("market summary for %s not read: %s", market, reason)
        db.set_status(f"cloud_fetch:{market}", "warn", "cloud_fetch", {"market": market, "detail": reason}, now_ms)
        return PollResult("failed", reason=reason)
    db.clear_status(f"cloud_schema:{market}")
    db.clear_status(f"cloud_fetch:{market}")
    return PollResult("new", intel, etag=headers.get("etag"), sha256=pointer["sha256"], body=body)


def remember(db: Database, market: str, etag: str | None, sha256: str | None, now_ms: int) -> None:
    """Keep the pointer's ETag and the held document's hash for the next poll (after it is staged).

    Args:
        db: Database.
        market: Market id.
        etag: The pointer's ETag, or None.
        sha256: The held document's hash.
        now_ms: Now, UTC ms.
    """
    with db.transaction():
        db.set_source_state(market, SRC, etag=etag, checkpoint=sha256, fetched_ms=now_ms)
