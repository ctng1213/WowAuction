"""AHledger: its price table as the live source `ahledger` (SPEC §2.3).

The parsers are pure (bytes or JSON in, records out); `store_pricetable` writes a parsed table in one
transaction, and `AhledgerSource` polls each market's table with its ETag and turns failures into Status lines.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import datetime

from ahsync.clock import Clock
from ahsync.collect.client import ApiClient, ApiError, NotFound, RateLimited, cache_max_age_s
from ahsync.db.sqlite import Database

log = logging.getLogger(__name__)

FORMAT = "AHL1"
ROW_FIELDS = 9
BASE = "https://api.ahledger.com"
SRC = "ahledger"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
UNREACHABLE_TRIES = 3     # failed polls in a row before Status says so (FM43)


class FormatChanged(Exception):
    """An AHledger response whose shape changed (bad header, bad JSON shape). The message names the endpoint."""


@dataclass(frozen=True)
class PriceRow:
    """One price-table row as it came; None means the field was empty (unknown, never 0)."""

    key: int                      # Blizzard item id (the base key; AHledger sends no suffix keys)
    median: int | None            # copper
    min_buyout: int | None        # copper
    qty: int | None               # units on the AH
    m7: int | None                # 7-day median, copper
    m30: int | None               # 30-day median, copper
    lo: int | None                # 30-day low, copper
    hi: int | None                # 30-day high, copper
    per_day_x10: int | None       # sold per day in tenths, as sent

    @property
    def per_day(self) -> float | None:
        """Sold per day, for display only (SPEC §2.3: stored, never used in a statistic).

        Returns:
            per_day_x10 / 10 in units a day, or None when unknown.
        """
        return None if self.per_day_x10 is None else self.per_day_x10 / 10


@dataclass(frozen=True)
class PriceTable:
    """A parsed price table: AHSync's market id, the update time, rows by ascending key, rows skipped."""

    market: str
    updated_s: int                # Unix seconds, as in the header
    rows: tuple[PriceRow, ...]
    skipped: int


def _whole(text: str) -> int | None:
    """Parse a non-negative whole number.

    Args:
        text: Digits only, e.g. "3600".

    Returns:
        The integer, or None when text is empty or not plain ASCII digits.
    """
    return int(text) if text.isascii() and text.isdigit() else None


def _parse_header(line: str, market: str) -> int:
    """Check the header line `AHL1|market|updated|count`.

    Args:
        line: The first line of the table.
        market: AHSync's dotted market id the table must be for.

    Returns:
        The update time in Unix seconds.

    Raises:
        FormatChanged: Wrong format, market, updated time or row count.
    """
    parts = line.strip().split("|")
    if len(parts) != 4 or parts[0] != FORMAT or parts[1] != market.replace(".", "/"):
        raise FormatChanged("pricetable")
    updated, count = _whole(parts[2]), _whole(parts[3])
    if updated is None or count is None:
        raise FormatChanged("pricetable")
    return updated


def _parse_row(text: str) -> PriceRow | None:
    """Parse one `itemId:median:minBuyout:quantity:m7:m30:lo:hi:perDayX10` row.

    Args:
        text: One row without its `;`.

    Returns:
        The row, or None when it has the wrong field count, a field that is
        neither empty nor a non-negative integer, or an item id that is not positive.
    """
    fields = text.strip().split(":")
    if len(fields) != ROW_FIELDS:
        return None
    key = _whole(fields[0])
    if not key:
        return None
    values: list[int | None] = []
    for field in fields[1:]:
        number = _whole(field)
        if field and number is None:
            return None
        values.append(number)
    return PriceRow(key, *values)


def parse_pricetable(body: bytes | str, market: str) -> PriceTable:
    """Parse the addon-format price table of one market (SPEC §2.3, SRC7, FM44, FM45).

    Args:
        body: The response body, UTF-8 text.
        market: AHSync's dotted market id, e.g. "forever.pvp.horde.us".

    Returns:
        The table with rows sorted by ascending key (a repeated key keeps the
        last row). A bad row is skipped and counted; a row count that differs
        from the header's is not an error.

    Raises:
        FormatChanged: ("pricetable") the body is not UTF-8 or its header is bad.
    """
    try:
        text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    except UnicodeDecodeError:
        raise FormatChanged("pricetable") from None
    header, _, rest = text.partition("\n")
    updated_s = _parse_header(header, market)
    rows: dict[int, PriceRow] = {}
    skipped = 0
    for chunk in rest.replace("\r", "\n").replace("\n", ";").split(";"):
        if not chunk.strip():
            continue
        row = _parse_row(chunk)
        if row is None:
            skipped += 1
        else:
            rows[row.key] = row
    return PriceTable(market, updated_s, tuple(rows[k] for k in sorted(rows)), skipped)


@dataclass(frozen=True)
class Bucket:
    """One price-history bucket (an hour for 24h/7d, a day for 30d/90d); None means the field was null (never 0)."""

    t_ms: int                     # UTC ms of the bucket's start
    min: int | None               # copper
    median: int | None            # copper
    p25: int | None               # copper
    p75: int | None               # copper
    quantity: int | None          # units on the AH
    auctions: int | None          # auctions on the AH


@dataclass(frozen=True)
class PriceHistory:
    """A parsed `/v1/prices/<market>/<id>` response: the buckets by ascending time and how many were skipped."""

    key: int
    range: str                    # "24h" | "7d" | "30d" | "90d"
    hourly: bool                  # True for 24h and 7d, False for the daily 30d and 90d
    buckets: tuple[Bucket, ...]
    latest_ms: int | None         # UTC ms of latest.observedAt; None when AHledger has no latest
    skipped: int = 0              # buckets dropped for a bad time or price field


_RANGES = {"24h": True, "7d": True, "30d": False, "90d": False}
_BUCKET_FIELDS = ("min", "median", "p25", "p75", "quantity", "auctions")


def _iso_ms(text: object) -> int | None:
    """An ISO 8601 UTC time ("2026-11-04T14:00:00.000Z", milliseconds optional) as Unix ms.

    Args:
        text: The value of a `t` or `observedAt` field.

    Returns:
        UTC ms, or None when it is not a string ending in Z or does not parse.
    """
    if not isinstance(text, str) or not text.endswith("Z"):
        return None
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        return None
    return calendar.timegm(moment.utctimetuple()) * 1000 + moment.microsecond // 1000


def _parse_bucket(raw: object) -> Bucket | None:
    """One series entry, or None when it is unusable.

    Args:
        raw: A decoded JSON series entry.

    Returns:
        The Bucket; None when not a dict, `t` does not parse, or a price field is not a non-negative integer or null.
    """
    if not isinstance(raw, dict):
        return None
    t_ms = _iso_ms(raw.get("t"))
    if t_ms is None:
        return None
    values = []
    for name in _BUCKET_FIELDS:
        value = raw.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            return None
        values.append(value)
    return Bucket(t_ms, *values)


def parse_prices(data: object, key: int) -> PriceHistory:
    """Read a `/v1/prices/<market>/<id>?range=...` response (SPEC §2.3, Item history).

    Args:
        data: The decoded JSON.
        key: The item key that was asked for.

    Returns:
        The history; buckets sorted by time, unusable ones counted in `skipped`.

    Raises:
        FormatChanged: ("prices") not an object, a missing or unknown range, a series that is not a list,
            an item id other than `key`, or a latest that is neither null nor an object.
    """
    if not isinstance(data, dict) or data.get("range") not in _RANGES or not isinstance(data.get("series"), list):
        raise FormatChanged("prices")
    item, latest = data.get("item"), data.get("latest")
    if isinstance(item, dict) and "id" in item and item["id"] != key:
        raise FormatChanged("prices")
    if latest is not None and not isinstance(latest, dict):
        raise FormatChanged("prices")
    parsed = [_parse_bucket(raw) for raw in data["series"]]
    buckets = tuple(sorted((b for b in parsed if b is not None), key=lambda b: b.t_ms))
    latest_ms = None if latest is None else _iso_ms(latest.get("observedAt"))
    return PriceHistory(key, data["range"], _RANGES[data["range"]], buckets, latest_ms, parsed.count(None))


def parse_markets(data: object) -> set[str]:
    """Read the market ids from `GET /v1/markets` (SPEC §2.3).

    Args:
        data: Decoded JSON: a list of objects with an "id", or {"markets": [that list]}.

    Returns:
        The set of ids.

    Raises:
        FormatChanged: ("markets") any other shape.
    """
    if isinstance(data, dict):
        data = data.get("markets")
    if not isinstance(data, list):
        raise FormatChanged("markets")
    ids: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise FormatChanged("markets")
        ids.add(entry["id"])
    return ids


def store_pricetable(db: Database, market: str, table: PriceTable, etag: str | None, fetched_ms: int) -> set[int]:
    """Write a price table: current prices, one hourly close per item, side rows, the observed hour (SRC8).

    A table no newer than the stored one writes nothing but the ETag and the fetch time, so every write is
    idempotent. A row without minBuyout gets only its side row.

    Args:
        db: Database.
        market: AHSync's market id.
        table: The parsed table.
        etag: The response's ETag, or None.
        fetched_ms: When it was fetched, UTC ms.

    Returns:
        The item keys whose price was written.
    """
    updated_ms = table.updated_s * 1000
    known_ms = db.source_state(market, SRC)[1]
    with db.transaction():
        db.set_source_state(market, SRC, etag=etag, fetched_ms=fetched_ms)
        if known_ms is not None and updated_ms <= known_ms:
            return set()
        priced = [r for r in table.rows if r.min_buyout is not None]
        hour_ms = table.updated_s // 3600 * HOUR_MS
        db.upsert_newer_current(market, [(r.key, r.min_buyout, r.qty, updated_ms) for r in priced], SRC)
        db.upsert_candles("candles_h1", market, [(r.key, hour_ms, None, None, None, r.min_buyout, r.qty)
                                                 for r in priced], SRC)
        db.upsert_ahledger_rows(market, table.updated_s,
                                [(r.key, r.median, r.m7, r.m30, r.lo, r.hi, r.per_day_x10) for r in table.rows])
        db.add_observed_hours(market, SRC, [table.updated_s // 3600])
        db.set_source_state(market, SRC, updated_ms=updated_ms)
    return {r.key for r in priced}


@dataclass
class AhledgerSource:
    """Polls AHledger's price table per market and keeps the last good rows on every failure (SPEC §2.3, §5.2).

    Holds per-run memory only: the markets list (fetched at most once a UTC day, SRC6), markets refused until the
    next day or start (FM46), consecutive failures (FM43) and when each table is fresh again (Cache-Control).
    """

    client: ApiClient          # keyless, api_base BASE, 300 requests a minute
    db: Database
    clock: Clock
    _markets: set[str] | None = None
    _markets_day: int | None = None
    _refused: dict[str, int] = field(default_factory=dict)                # market -> UTC day refused
    _failures: dict[str, tuple[int, int]] = field(default_factory=dict)   # market -> (first failure ms, tries)
    _fresh_until: dict[str, int] = field(default_factory=dict)            # market -> ms its copy is fresh until

    def refused(self, market: str) -> bool:
        """Whether a market is not asked for today (no such market at AHledger, FM46, SRC6).

        Args:
            market: AHSync's market id.

        Returns:
            True until the next UTC day or the next start.
        """
        return self._refused.get(market) == self.clock.now_ms() // DAY_MS

    def poll(self, market: str, address: str) -> set[int]:
        """Poll one market's price table and store it when it changed; never raises an API error.

        Args:
            market: AHSync's market id.
            address: Its id at AHledger (config addresses.ahledger, non-empty).

        Returns:
            The item keys whose price was written (empty on 304, a repeat, a skip or a failure).
        """
        now = self.clock.now_ms()
        if self.refused(market) or now < self._fresh_until.get(market, 0) or not self._listed(market, address, now):
            return set()
        etag = self.db.source_state(market, SRC)[0]
        try:
            response = self.client.get_conditional(f"{BASE}/v1/pricetable/{address}", etag)
        except NotFound:
            self._no_market(market, now)
            return set()
        except RateLimited:                       # a Retry-After over 300 s: skip this poll, quietly
            return set()
        except ApiError as error:
            self._unreachable(market, now, error)
            return set()
        self._reached(market)
        self._fresh_until[market] = now + (cache_max_age_s(response.headers) or 0) * 1000
        if response.status == 304:
            with self.db.transaction():
                self.db.set_source_state(market, SRC, fetched_ms=now)
            return set()
        return self._store(market, address, response.body, response.headers.get("etag"), now)

    def _store(self, market: str, address: str, body: bytes, etag: str | None, now: int) -> set[int]:
        """Parse and store a changed table; a bad header writes nothing and forgets the ETag (FM44).

        Args:
            market: AHSync's market id.
            address: Its id at AHledger, which the header names.
            body: The response body.
            etag: The response's ETag, or None.
            now: Now, UTC ms.

        Returns:
            The item keys whose price was written.
        """
        try:
            table = parse_pricetable(body, address)
        except FormatChanged:
            with self.db.transaction():
                self.db.set_source_state(market, SRC, etag=None, fetched_ms=now)
            self.db.set_status("format:ahledger_pricetable", "warn", "ahledger_format", {"endpoint": "pricetable"},
                               now)
            log.warning("AHledger %s: the price table's format changed; nothing stored", market)
            return set()
        dirty = store_pricetable(self.db, market, table, etag, now)
        self.db.clear_status("format:ahledger_pricetable")
        log.info("AHledger %s: %d rows stored, %d skipped", market, len(table.rows), table.skipped)   # FM45
        return dirty

    def _listed(self, market: str, address: str, now: int) -> bool:
        """Whether AHledger lists the market, from `/v1/markets` fetched at most once a day (SRC6).

        Args:
            market: AHSync's market id.
            address: Its id at AHledger.
            now: Now, UTC ms.

        Returns:
            False (with the alert, and the market refused for today) when the list lacks it; True when it has it
            or the list couldn't be read (the table request then decides).
        """
        if self._markets_day != now // DAY_MS:
            self._markets_day = now // DAY_MS
            try:
                self._markets = parse_markets(self.client.get_json(f"{BASE}/v1/markets", auth=False))
            except (ApiError, FormatChanged) as error:
                log.warning("AHledger markets list unavailable: %s", error)
                self._markets = None
        if self._markets is None or address in self._markets:
            return True
        self._no_market(market, now)
        return False

    def _no_market(self, market: str, now: int) -> None:
        """Say AHledger has no such market and stop asking for it until tomorrow (FM46, SRC6).

        Args:
            market: AHSync's market id.
            now: Now, UTC ms.
        """
        self._refused[market] = now // DAY_MS
        self.db.set_status(f"ahledger:no_market:{market}", "warn", "ahledger_no_market", {"market": market}, now)

    def _unreachable(self, market: str, now: int, error: Exception) -> None:
        """Count a failed poll; from the third in a row Status says since when (FM43).

        Args:
            market: AHSync's market id.
            now: Now, UTC ms.
            error: What failed, for the log.
        """
        since, tries = self._failures.get(market, (now, 0))
        self._failures[market] = (since, tries + 1)
        log.warning("AHledger %s: price table poll failed (%d in a row): %s", market, tries + 1, error)
        if tries + 1 >= UNREACHABLE_TRIES:
            self.db.set_status(f"ahledger:unreachable:{market}", "warn", "ahledger_unreachable",
                               {"market": market, "since_ms": since, "tries": tries + 1}, now)

    def _reached(self, market: str) -> None:
        """Clear a market's failure count and its AHledger alerts after an answer.

        Args:
            market: AHSync's market id.
        """
        self._failures.pop(market, None)
        self.db.clear_status(f"ahledger:unreachable:{market}")
        self.db.clear_status(f"ahledger:no_market:{market}")
