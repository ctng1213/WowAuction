"""Blizzard's Forever auction API as a snapshot provider (SPEC §2.4, §5.0 Collector; M29).

Two requests make one snapshot of a house: the regular auctions and the commodities, each gzipped and sent
`If-Modified-Since` the last stored `Last-Modified`. `parse_snapshot` turns the two bodies into a
`SnapshotObservation` (src "blizzard", kind book): one `Auction` per record, keyed from the item id and its
`bonus_lists` (SRC5), band from `time_left`, unit price from `unit_price` or `buyout ÷ quantity`. A regular
auction with only a bid has no asking price and is left out and counted. A body without an `auctions` array is
an empty house: kept raw, never ingested. `raw_envelope` wraps Blizzard's bytes, unchanged, with their
provenance for the raw store.

The client id, the secret and the token never reach a log, an exception text or a file.
"""
from __future__ import annotations

import base64
import gzip
import json
import zlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ahsync.clock import Clock
from ahsync.collect.client import HttpRequest, HttpResponse, Transport
from ahsync.collect.provider import SnapshotObservation
from ahsync.engine.item_key import item_key
from ahsync.engine.volume import Auction

SOURCE = "blizzard"
TOKEN_URL = "https://oauth.battle.net/token"
API = "https://us.api.blizzard.com"
REGION = "us"
NAMESPACE = "dynamic-forever-us"
STATIC_NAMESPACE = "static-forever-us"   # item metadata (SPEC §6 item 24)
FALLBACK_NAMESPACE = "static-classic1x-us"   # Classic Era: items Forever's static data lacks (the user, 2026-11-06)
SEARCH_SPAN = 1000                    # ids per catalog search: a search answers at most 1,000 hits (M31)
QUALITY = {"POOR": 0, "COMMON": 1, "UNCOMMON": 2, "RARE": 3, "EPIC": 4, "LEGENDARY": 5, "ARTIFACT": 6, "HEIRLOOM": 7}
TIME_LEFT = {"SHORT": 1, "MEDIUM": 2, "LONG": 3, "VERY_LONG": 4}   # WoW's band codes (SPEC §2.2)
STAMP_SKEW_MS = 15 * 60_000           # the two endpoints' Last-Modified further apart: not one snapshot (BZ9)
TOKEN_MARGIN_MS = 5 * 60_000          # renew this long before the token expires (BZ6)
USER_AGENT = "AHSync/0.1"


class BlizzardError(Exception):
    """A request or a body failed for good this run. The message names a status or a cause, never a secret."""


@dataclass(frozen=True)
class HouseFetch:
    """What one fetch of a house found."""

    state: str                        # "new", "unchanged" (both 304) or "mismatch" (the halves differ, BZ9)
    last_modified: str | None         # the newer Last-Modified header as sent, for the next If-Modified-Since
    at_ms: int | None                 # that time, UTC ms
    auctions: bytes = b""             # the decompressed bodies ("new" only)
    commodities: bytes = b""


@dataclass(frozen=True)
class ParsedSnapshot:
    """A parsed house: the observation, or None for an empty house, and what was left out."""

    observation: SnapshotObservation | None
    bid_only: int                     # regular auctions with a bid and no buyout, left out (BZ3)
    empty: bool                       # a body had no auctions array (BZ5)


def http_date_ms(value: str) -> int:
    """An HTTP date (`Last-Modified`) as UTC ms.

    Args:
        value: E.g. "Fri, 06 Nov 2026 14:02:40 GMT".

    Returns:
        UTC ms.

    Raises:
        BlizzardError: It is not an HTTP date.
    """
    try:
        return int(parsedate_to_datetime(value).timestamp() * 1000)
    except (TypeError, ValueError):
        raise BlizzardError(f"Last-Modified is not a date: {value!r}") from None


class BlizzardClient:
    """Fetches a house's two endpoints with a cached client-credentials token (SPEC §2.4, BZ6, BZ8, BZ9)."""

    def __init__(self, transport: Transport, clock: Clock, client_id: str, secret: str) -> None:
        """Create the client; no request is made yet.

        Args:
            transport: The HTTP transport.
            clock: The clock (token expiry).
            client_id: The Battle.net client id.
            secret: The client secret.
        """
        self._transport, self._clock = transport, clock
        self._basic = base64.b64encode(f"{client_id}:{secret}".encode("utf-8")).decode("ascii")
        self._token: str | None = None
        self._expires_ms = 0

    def _fresh_token(self) -> str:
        """The cached token, or a new one when there is none or it expires within 5 min.

        Returns:
            The access token.

        Raises:
            BlizzardError: The token request failed (its status only).
        """
        now = self._clock.now_ms()
        if self._token is None or now >= self._expires_ms - TOKEN_MARGIN_MS:
            response = self._transport(HttpRequest(
                TOKEN_URL, {"Authorization": f"Basic {self._basic}", "User-Agent": USER_AGENT,
                            "Content-Type": "application/x-www-form-urlencoded"},
                method="POST", body=b"grant_type=client_credentials"))
            if response.status != 200:
                raise BlizzardError(f"token request answered {response.status}")
            body = json.loads(response.body)
            self._token = str(body["access_token"])
            self._expires_ms = now + int(body["expires_in"]) * 1000
            return self._token
        return self._token

    def search_items(self, first_id: int, last_id: int, namespace: str = STATIC_NAMESPACE) -> list[dict]:
        """Every item whose id lies in a range, from the static namespace's item search (SPEC §2.4 catalog, BZ16).

        Args:
            first_id: The range's first item id.
            last_id: Its last; at most SEARCH_SPAN ids, so one page holds every hit.
            namespace: The static namespace: Forever's, or FALLBACK_NAMESPACE for what it lacks.

        Returns:
            The items' records (`data` of each hit), in id order.

        Raises:
            BlizzardError: The range is too wide, or the search failed.
        """
        if last_id - first_id + 1 > SEARCH_SPAN:
            raise BlizzardError(f"item search range {first_id}-{last_id} is wider than {SEARCH_SPAN}")
        path = "/data/wow/search/item"
        query = f"&id=[{first_id},{last_id}]&orderby=id&_pageSize={SEARCH_SPAN}&_page=1"
        response = self._get(path, None, namespace=namespace, query=query)
        if response.status != 200:
            raise BlizzardError(f"{path} answered {response.status}")
        return [hit["data"] for hit in json.loads(response.body).get("results", [])]

    def _get(self, path: str, since: str | None, namespace: str = NAMESPACE, query: str = "") -> HttpResponse:
        """One GET of an API route, its body decompressed; a 401 renews the token once.

        Args:
            path: The route.
            since: The If-Modified-Since value, or None.
            namespace: The namespace query value.
            query: More query parameters, each starting with "&".

        Returns:
            The response.

        Raises:
            BlizzardError: A cut-off or corrupt gzip body (FM69).
        """
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._fresh_token()}", "Accept-Encoding": "gzip",
                       "User-Agent": USER_AGENT}
            if since:
                headers["If-Modified-Since"] = since
            response = self._transport(HttpRequest(f"{API}{path}?namespace={namespace}{query}", headers))
            if response.status != 401 or attempt:
                break
            self._token = None
        if response.headers.get("content-encoding") == "gzip" and response.body:
            try:
                return HttpResponse(response.status, response.headers, gzip.decompress(response.body))
            except (EOFError, OSError, zlib.error):
                raise BlizzardError(f"{path}: gzip body cut off or corrupt") from None
        return response

    def fetch_house(self, ruleset: int, house: int, since: str | None) -> HouseFetch:
        """Both endpoints of a house, conditionally (SPEC §2.4, BZ8, BZ9).

        Args:
            ruleset: The ruleset id in the route (ignored by Blizzard, required by the route).
            house: The house id.
            since: The last stored Last-Modified, or None.

        Returns:
            "unchanged" when both answer 304; "mismatch" when one answers 304 or their stamps are more than
            15 min apart; else "new" with both bodies.

        Raises:
            BlizzardError: A status other than 200 or 304, a missing Last-Modified or a bad body.
        """
        base = f"/data/wow/ruleset/{ruleset}/auctions/{house}"
        answers = [self._get(base, since), self._get(f"{base}/commodities", since)]
        for path, response in zip((base, f"{base}/commodities"), answers):
            if response.status not in (200, 304):
                raise BlizzardError(f"{path} answered {response.status}")
        if all(r.status == 304 for r in answers):
            return HouseFetch("unchanged", since, http_date_ms(since) if since else None)
        stamps = [r.headers.get("last-modified") for r in answers]
        if any(r.status == 304 for r in answers):
            return HouseFetch("mismatch", since, None)
        present = [s for s in stamps if s]
        if len(present) != 2:
            raise BlizzardError(f"{base}: no Last-Modified")
        times = [http_date_ms(s) for s in present]
        if abs(times[0] - times[1]) > STAMP_SKEW_MS:
            return HouseFetch("mismatch", since, None)
        newer = 0 if times[0] >= times[1] else 1
        return HouseFetch("new", present[newer], times[newer], answers[0].body, answers[1].body)


def _auctions_of(body: bytes, name: str) -> list[dict] | None:
    """The auction records of one body.

    Args:
        body: A decompressed response.
        name: "auctions" or "commodities", for errors.

    Returns:
        The records; None when the body has no auctions array (an empty house).

    Raises:
        BlizzardError: The body is not a JSON object.
    """
    try:
        data = json.loads(body)
    except ValueError:
        raise BlizzardError(f"{name} body is not JSON") from None
    if not isinstance(data, dict):
        raise BlizzardError(f"{name} body is not an object")
    records = data.get("auctions")
    return records if isinstance(records, list) else None


def _auction(record: dict, suffixes: dict[int, int], commodity: bool = False) -> Auction | None:
    """One record as an Auction.

    Args:
        record: A regular or commodity auction record.
        suffixes: Bonus id -> random suffix id.
        commodity: The record came from the commodities list (bought cheapest first, BZ40).

    Returns:
        The auction; None for a regular auction with a bid and no buyout (no asking price, BZ3).

    Raises:
        BlizzardError: A field is missing or its value is unknown (names the auction id and the value, BZ4).
    """
    auction_id = record.get("id")
    band = TIME_LEFT.get(str(record.get("time_left")))
    if band is None:
        raise BlizzardError(f"auction {auction_id}: unknown time_left {record.get('time_left')!r}")
    item, quantity = record.get("item") or {}, record.get("quantity")
    if "unit_price" in record:
        price = record["unit_price"]
    elif "buyout" in record:
        price = record["buyout"] // quantity if isinstance(quantity, int) and quantity > 0 else 0
    else:
        return None
    if not isinstance(auction_id, int) or not isinstance(item.get("id"), int) or not isinstance(quantity, int):
        raise BlizzardError(f"auction {auction_id}: missing id, item or quantity")
    return Auction(auction_id, item_key(item["id"], item.get("bonus_lists") or (), suffixes), quantity, price, band,
                   commodity)


def parse_snapshot(market: str, auctions_body: bytes, commodities_body: bytes, at_ms: int,
                   suffixes: dict[int, int]) -> ParsedSnapshot:
    """A house's two bodies as one snapshot (SPEC §2.4, BZ1-BZ5).

    Args:
        market: AHSync's market id.
        auctions_body: The regular auctions, decompressed.
        commodities_body: The commodities, decompressed.
        at_ms: The snapshot's time (the newer Last-Modified), UTC ms.
        suffixes: Bonus id -> random suffix id (collect.provider.load_bonus_suffixes).

    Returns:
        The parsed snapshot; its observation is None for an empty house. A record Blizzard repeats field for
        field counts once (BZ38: its answer can list one auction twice); two different records with one id are
        both kept. The observation is not checked here: `collect.observations.ingest_snapshot` refuses it whole
        when `snapshot_problems` finds anything (FM33).

    Raises:
        BlizzardError: A body is not JSON, or a record is malformed (the snapshot is refused whole).
    """
    regular, commodities = _auctions_of(auctions_body, "auctions"), _auctions_of(commodities_body, "commodities")
    if regular is None or commodities is None:
        return ParsedSnapshot(None, 0, True)
    auctions, bid_only, seen = [], 0, set()
    for record, commodity in [(r, False) for r in regular] + [(r, True) for r in commodities]:
        auction = _auction(record, suffixes, commodity)
        if auction is None:
            bid_only += 1
        elif replace(auction, commodity=False) not in seen:   # Blizzard's echo of the same record (BZ38), in either list
            seen.add(replace(auction, commodity=False))
            auctions.append(auction)
    return ParsedSnapshot(SnapshotObservation(market, SOURCE, at_ms, tuple(auctions)), bid_only, False)


def raw_key(market: str, at_ms: int) -> str:
    """The raw store's key of a snapshot (SPEC §2.4, BZ7).

    Args:
        market: Market id.
        at_ms: The snapshot's time, UTC ms.

    Returns:
        "raw/<market>/<YYYY>/<MM>/<DD>/<HHMMSS>.json.gz", UTC.
    """
    return datetime.fromtimestamp(at_ms / 1000, timezone.utc).strftime(f"raw/{market}/%Y/%m/%d/%H%M%S.json.gz")


def raw_envelope(ruleset: int, house: int, last_modified: str, fetched_ms: int, auctions_body: bytes,
                 commodities_body: bytes) -> bytes:
    """Blizzard's two bodies, byte for byte, in an envelope with their provenance, gzipped (SPEC §2.4, BZ7).

    The bodies are spliced in as they came rather than re-serialized, so the raw is Blizzard's bytes; the
    gzip header carries no time, so the same snapshot always gives the same object.

    Args:
        ruleset: The ruleset id of the route.
        house: The house id.
        last_modified: The snapshot's Last-Modified header.
        fetched_ms: When it was fetched, UTC ms.
        auctions_body: The regular auctions, decompressed.
        commodities_body: The commodities, decompressed.

    Returns:
        The gzipped envelope: {"region", "namespace", "ruleset", "house", "last_modified", "fetched_ms",
        "auctions", "commodities"}.
    """
    head = json.dumps({"region": REGION, "namespace": NAMESPACE, "ruleset": ruleset, "house": house,
                       "last_modified": last_modified, "fetched_ms": fetched_ms}, separators=(",", ":"))
    text = (head[:-1].encode("utf-8") + b',"auctions":' + auctions_body.strip() + b',"commodities":'
            + commodities_body.strip() + b"}")
    return gzip.compress(text, mtime=0)


def open_envelope(data: bytes) -> tuple[str, bytes, bytes]:
    """A raw envelope's snapshot: the inverse of raw_envelope (SPEC §2.4).

    Args:
        data: The gzipped envelope, as stored.

    Returns:
        (Last-Modified, the regular auctions body, the commodities body), the bodies as JSON bytes for
        parse_snapshot.

    Raises:
        BlizzardError: It is not a gzipped envelope with those fields.
    """
    try:
        envelope = json.loads(gzip.decompress(data))
        return (str(envelope["last_modified"]), json.dumps(envelope["auctions"]).encode("utf-8"),
                json.dumps(envelope["commodities"]).encode("utf-8"))
    except (EOFError, OSError, zlib.error, ValueError, KeyError, TypeError):
        raise BlizzardError("not a raw envelope") from None


def catalog_row(record: dict) -> tuple:
    """An item search record as an `items` row (SPEC §2.4 catalog; the shape db.upsert_items takes).

    Args:
        record: One hit's `data` from the static item search.

    Returns:
        (key, item_id, name, quality, ilvl, class, subclass, vendor_buy, vendor_sell, stack); the stack is None,
        since Blizzard's record has none (M28: `max_count` is the unique-equip limit).

    Raises:
        BlizzardError: The record has no id or name.
    """
    try:
        item_id, name = int(record["id"]), str(record["name"]["en_US"])
    except (KeyError, TypeError, ValueError):
        raise BlizzardError("item record without an id or an en_US name") from None
    quality = QUALITY.get(str((record.get("quality") or {}).get("type")))
    return (item_id, item_id, name, quality, record.get("level"), (record.get("item_class") or {}).get("id"),
            (record.get("item_subclass") or {}).get("id"), record.get("purchase_price"), record.get("sell_price"), None)
