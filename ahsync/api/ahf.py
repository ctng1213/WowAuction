"""auctionhouse.fyi endpoints and response shapes (SPEC §2.1 "Response formats").

Each call returns a small record or plain rows, after checking the shape. A
response with an unexpected shape raises `FormatChanged` naming the endpoint, so
the caller disables just that endpoint for the run (SPEC §5.2, FM4). Capability
tokens from /files are registered with the client's redactor, because they are
secrets (SPEC §2.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ahsync.api.client import ApiClient, ApiError

DEFAULT_CDN = "https://cdn.auctionhouse.fyi/"
SUFFIX_BASE = 2_147_483_648   # item key = item + 2^31 × v (SPEC §2.1)

# Features from /me that unlock Premium paths (SPEC §2.1).
FEATURE_LIVE = "prices.live"
FEATURE_BOOKS = "books"
FEATURE_DEPTH = "depth"
PUBLIC_HISTORY_DAYS = 7


class FormatChanged(ApiError):
    """An AHF response no longer has the shape we parse (API change)."""

    def __init__(self, endpoint: str, detail: str) -> None:
        """Create the error.

        Args:
            endpoint: Short endpoint name for Status, e.g. "snapshot".
            detail: What was wrong, for the log.
        """
        super().__init__(f"AHF format changed: {endpoint} ({detail})")
        self.endpoint = endpoint


@dataclass(frozen=True)
class Capabilities:
    """What our tier allows this run, from GET /me."""

    features: frozenset[str]
    history_days: int
    api_per_minute: int
    contributor_until_ms: int | None = None

    @property
    def premium_history(self) -> bool:
        """Whether h1c/d1c history beyond the public 7 days is allowed.

        Returns:
            True when historyDays is above the public 7.
        """
        return self.history_days > PUBLIC_HISTORY_DAYS

    @property
    def premium(self) -> bool:
        """Whether any Premium feature used by AHSync is present.

        Returns:
            True for extra history, live prices, books or depth.
        """
        return self.premium_history or bool(self.features & {FEATURE_LIVE, FEATURE_BOOKS, FEATURE_DEPTH})


@dataclass(frozen=True)
class BookRef:
    """One AHF order book listed in /files."""

    at_ms: int
    url: str
    complete: bool


@dataclass(frozen=True)
class FileIndex:
    """GET /ah/:market/files: what exists on the CDN right now."""

    market: str
    cdn_base: str
    sealed_h1: int                       # newest complete hour (floor(ms/3600000))
    sealed_d1: int                       # newest complete day (floor(ms/86400000))
    d1_url: str | None                   # public 7-day day candles
    snapshot_url: str | None             # snapshot pointer (latest.json)
    caps_h1: dict[int, str] = field(default_factory=dict)   # day -> h1c token (Premium)
    caps_d1_url: str | None = None       # d1c, 90 days (Premium)
    caps_volume_url: str | None = None   # volume (Premium)
    books: tuple[BookRef, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    """Current prices as of the top of the hour."""

    at_ms: int
    rows: list[tuple[int, int | None, int, int]]   # (key, price copper or None, qty, scanAt ms)


def _require(condition: bool, endpoint: str, detail: str) -> None:
    """Raise FormatChanged unless a shape check holds.

    Args:
        condition: The check.
        endpoint: Endpoint name for Status.
        detail: What was expected.

    Raises:
        FormatChanged: When condition is false.
    """
    if not condition:
        raise FormatChanged(endpoint, detail)


def _is_int(value: object) -> bool:
    """Check for a JSON integer (not a bool).

    Args:
        value: Any decoded JSON value.

    Returns:
        True for int values other than True/False.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def parse_candles(data: object, endpoint: str) -> list[tuple[int, int, int | None, int | None, int | None, int | None, int]]:
    """Flatten a candle file: `items: [[key, [[t, open, high, low, close, qty], …]], …]`.

    Args:
        data: Decoded JSON of an h1, h1c, d1 or d1c file.
        endpoint: Endpoint name for errors ("h1", "d1", …).

    Returns:
        Rows (key, t ms, open, high, low, close, qty); prices in copper, None when nothing listed.

    Raises:
        FormatChanged: The file doesn't have that shape.
    """
    _require(isinstance(data, dict) and isinstance(data.get("items"), list), endpoint, "no items list")
    rows = []
    for item in data["items"]:
        _require(isinstance(item, list) and len(item) == 2 and _is_int(item[0]) and isinstance(item[1], list),
                 endpoint, "item is not [key, candles]")
        for candle in item[1]:
            _require(isinstance(candle, list) and len(candle) == 6 and _is_int(candle[0]), endpoint,
                     "candle is not [t, open, high, low, close, qty]")
            rows.append((item[0], candle[0], candle[1], candle[2], candle[3], candle[4], candle[5] or 0))
    return rows


def parse_snapshot(data: object) -> Snapshot:
    """Parse a snapshot file: `{at, rows: [[key, price, qty, scanAt], …]}`.

    Args:
        data: Decoded JSON.

    Returns:
        The snapshot; a null price stays None (no listing, never zero).

    Raises:
        FormatChanged: Missing `at`/`rows` or a row of the wrong length.
    """
    _require(isinstance(data, dict) and _is_int(data.get("at")) and isinstance(data.get("rows"), list),
             "snapshot", "no at/rows")
    rows = []
    for row in data["rows"]:
        _require(isinstance(row, list) and len(row) == 4 and _is_int(row[0]), "snapshot",
                 "row is not [key, price, qty, scanAt]")
        rows.append((row[0], row[1], row[2] or 0, row[3] if _is_int(row[3]) else data["at"]))
    return Snapshot(data["at"], rows)


def parse_book(data: object) -> tuple[int, bool, list[tuple[int, int, int, int, int]]]:
    """Parse an order book: `{at, complete, entries: [[key, price, stack, band, count], …]}`.

    Args:
        data: Decoded JSON.

    Returns:
        (at ms, complete, entries). Rows repeating the same (key, price, stack, band)
        are summed, so the store's primary key holds.

    Raises:
        FormatChanged: Wrong shape.
    """
    _require(isinstance(data, dict) and _is_int(data.get("at")) and isinstance(data.get("entries"), list),
             "books", "no at/entries")
    counts: dict[tuple[int, int, int, int], int] = {}
    for row in data["entries"]:
        _require(isinstance(row, list) and len(row) == 5 and all(_is_int(v) for v in row), "books",
                 "entry is not [key, price, stack, band, count]")
        group = (row[0], row[1], row[2], row[3])
        counts[group] = counts.get(group, 0) + row[4]
    entries = [(*group, count) for group, count in sorted(counts.items())]
    return data["at"], bool(data.get("complete", False)), entries


def parse_items(data: object) -> list[tuple[int, int, str | None, int | None, int | None, int | None,
                                            int | None, int | None, int | None, int | None]]:
    """Parse GET /items into item rows, base items and suffix variants.

    Args:
        data: Decoded JSON: `items: {id: {n, q, il, c, sc, sell, buy, st}}`, `variants: {key: {n, q, …}}`.

    Returns:
        Rows (key, item_id, name, quality, ilvl, class, subclass, vendor_buy, vendor_sell, stack),
        sorted by key. A variant takes its base item's values unless it overrides them.

    Raises:
        FormatChanged: No `items` table.
    """
    _require(isinstance(data, dict) and isinstance(data.get("items"), dict), "items", "no items table")
    base: dict[int, dict] = {int(k): v for k, v in data["items"].items() if isinstance(v, dict)}
    rows = {}
    entries = list(base.items()) + [(int(k), v) for k, v in (data.get("variants") or {}).items() if isinstance(v, dict)]
    for key, info in entries:
        item_id = key % SUFFIX_BASE
        merged = {**base.get(item_id, {}), **info}
        rows[key] = (key, item_id, merged.get("n"), merged.get("q"), merged.get("il"), merged.get("c"),
                     merged.get("sc"), merged.get("buy"), merged.get("sell"), merged.get("st"))
    return [rows[k] for k in sorted(rows)]


class AhfApi:
    """The AHF endpoints, over an ApiClient."""

    def __init__(self, client: ApiClient, cdn_base: str = DEFAULT_CDN) -> None:
        """Create the API wrapper.

        Args:
            client: Does the HTTP.
            cdn_base: CDN root; replaced by `cdnBase` from /files when present.
        """
        self.client = client
        self.cdn_base = cdn_base if cdn_base.endswith("/") else cdn_base + "/"

    def me(self) -> Capabilities:
        """GET /me: our features and limits, checked every run (SPEC §2.1).

        Returns:
            The capabilities. Missing limits fall back to the public tier (7 days, 60/min).

        Raises:
            FormatChanged: Not a JSON object.
            ApiError: As ApiClient.get_json.
        """
        data = self.client.get_json(f"{self.client.api_base}/me")
        _require(isinstance(data, dict), "me", "not an object")
        limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
        contributor = data.get("contributor") if isinstance(data.get("contributor"), dict) else {}
        until = contributor.get("until")
        return Capabilities(
            features=frozenset(f for f in data.get("features", []) if isinstance(f, str)),
            history_days=int(limits.get("historyDays", PUBLIC_HISTORY_DAYS)),
            api_per_minute=int(limits.get("apiPerMinute", 60)),
            contributor_until_ms=until if _is_int(until) else None,
        )

    def files(self, market: str) -> FileIndex:
        """GET /ah/:market/files and register its capability tokens as secrets.

        Args:
            market: AHF market id.

        Returns:
            The file index.

        Raises:
            FormatChanged: No `sealed.h1`/`sealed.d1`.
            ApiError: As ApiClient.get_json.
        """
        data = self.client.get_json(f"{self.client.api_base}/ah/{market}/files")
        _require(isinstance(data, dict) and isinstance(data.get("sealed"), dict), "files", "no sealed")
        sealed = data["sealed"]
        _require(_is_int(sealed.get("h1")) and _is_int(sealed.get("d1")), "files", "no sealed.h1/d1")
        if isinstance(data.get("cdnBase"), str) and data["cdnBase"].startswith("https://"):
            self.cdn_base = data["cdnBase"] if data["cdnBase"].endswith("/") else data["cdnBase"] + "/"
        caps = data.get("caps") if isinstance(data.get("caps"), dict) else {}
        caps_h1 = {int(day): token for day, token in (caps.get("h1") or {}).items() if isinstance(token, str)}
        for token in caps_h1.values():
            self.client.redactor.add(token)
        books = []
        for book in data.get("books") or []:
            if isinstance(book, dict) and _is_int(book.get("at")) and isinstance(book.get("url"), str):
                token = book["url"].rsplit("/", 1)[-1].removeprefix(f"{book['at']}-").removesuffix(".json.gz")
                self.client.redactor.add(token)
                books.append(BookRef(book["at"], book["url"], bool(book.get("complete"))))
        caps_d1 = caps.get("d1") if isinstance(caps.get("d1"), str) else None
        caps_volume = caps.get("volume") if isinstance(caps.get("volume"), str) else None
        for url in (caps_d1, caps_volume):
            if url:
                self.client.redactor.add(url.split("/")[-2])
        return FileIndex(market=market, cdn_base=self.cdn_base, sealed_h1=sealed["h1"], sealed_d1=sealed["d1"],
                         d1_url=data.get("d1") if isinstance(data.get("d1"), str) else None,
                         snapshot_url=data.get("snapshot") if isinstance(data.get("snapshot"), str) else None,
                         caps_h1=caps_h1, caps_d1_url=caps_d1, caps_volume_url=caps_volume,
                         books=tuple(sorted(books, key=lambda b: (b.at_ms, b.url))))

    def hour_url(self, market: str, hour: int, token: str | None = None) -> str:
        """URL of one hour's candles (SPEC §2.1 Response formats).

        Args:
            market: AHF market id.
            hour: floor(ms/3600000).
            token: `caps.h1[day]` for the Premium h1c file, None for the public h1 file.

        Returns:
            `candles/v1/h1/<m>/<hour>.json.gz` or `candles/v1/h1c/<m>/<token>/<hour>.json.gz` on the CDN.
        """
        if token is None:
            return f"{self.cdn_base}candles/v1/h1/{market}/{hour}.json.gz"
        return f"{self.cdn_base}candles/v1/h1c/{market}/{token}/{hour}.json.gz"

    def hour_candles(self, market: str, hour: int, token: str | None = None) -> list[tuple]:
        """Fetch one hour's candles.

        Args:
            market: AHF market id.
            hour: floor(ms/3600000); must be ≤ sealed.h1.
            token: h1c token for Premium history, or None for the public file.

        Returns:
            Candle rows as parse_candles.

        Raises:
            FormatChanged, BadFile, NotFound, FeatureUnavailable, RetriesExhausted.
        """
        data = self.client.get_json(self.hour_url(market, hour, token), auth=token is not None)
        return parse_candles(data, "h1c" if token else "h1")

    def day_candles(self, url: str, premium: bool) -> list[tuple]:
        """Fetch a day-candle file: public d1 (7 days) or Premium d1c (90 days).

        Args:
            url: `files.d1` or `caps.d1`.
            premium: True for d1c (sends the key).

        Returns:
            Candle rows as parse_candles, t = start of day in ms.

        Raises:
            FormatChanged, BadFile, NotFound, FeatureUnavailable, RetriesExhausted.
        """
        return parse_candles(self.client.get_json(url, auth=premium), "d1c" if premium else "d1")

    def snapshot(self, pointer_url: str) -> Snapshot:
        """Fetch the current-price snapshot via its pointer (`latest.json` → `path`).

        Args:
            pointer_url: `files.snapshot`.

        Returns:
            The snapshot.

        Raises:
            FormatChanged: Pointer or snapshot has an unexpected shape.
            BadFile, NotFound, RetriesExhausted.
        """
        pointer = self.client.get_json(pointer_url, auth=False)
        _require(isinstance(pointer, dict) and isinstance(pointer.get("path"), str), "snapshot", "pointer has no path")
        return parse_snapshot(self.client.get_json(self.cdn_base + pointer["path"].lstrip("/"), auth=False))

    def live(self, market: str, since_ms: int) -> Snapshot:
        """Fetch live price changes since a time (Premium `prices.live`).

        The response shape is assumed to match the snapshot's (see SPEC GAP below).

        Args:
            market: AHF market id.
            since_ms: Only changes after this time, UTC ms.

        Returns:
            The changed rows as a Snapshot.

        Raises:
            FormatChanged: As above (endpoint "live").
            FeatureUnavailable, BadFile, RetriesExhausted.
        """
        # SPEC GAP: §2.1 names `GET /ah/:market?since=` but its response wasn't recorded.
        # We assume the snapshot's shape (`at`, `rows: [[key, price, qty, scanAt]]`); any
        # other shape raises FormatChanged("live") and the caller keeps the snapshot.
        data = self.client.get_json(f"{self.client.api_base}/ah/{market}?since={since_ms}")
        try:
            return parse_snapshot(data)
        except FormatChanged as error:
            raise FormatChanged("live", str(error)) from None

    def book(self, url: str) -> tuple[int, bool, list[tuple[int, int, int, int, int]]]:
        """Fetch one AHF order book (Premium `books`).

        Args:
            url: `files.books[].url`.

        Returns:
            As parse_book.

        Raises:
            FormatChanged, BadFile, NotFound, FeatureUnavailable, RetriesExhausted.
        """
        return parse_book(self.client.get_json(url))

    def volume(self, url: str) -> list:
        """Fetch the Premium volume file. Rows are returned raw (see SPEC GAP below).

        Args:
            url: `caps.volume`.

        Returns:
            The raw `items` list.

        Raises:
            FormatChanged: No `items` list.
            BadFile, NotFound, FeatureUnavailable, RetriesExhausted.
        """
        # SPEC GAP: the volume row format is unknown (SPEC §2.1, §6 item 1: the recorded
        # file had no rows), so rows are returned raw and the caller doesn't store them yet.
        data = self.client.get_json(url)
        _require(isinstance(data, dict) and isinstance(data.get("items"), list), "volume", "no items list")
        return data["items"]

    def items(self) -> list[tuple]:
        """GET /items: item metadata.

        Returns:
            Rows as parse_items.

        Raises:
            FormatChanged, BadFile, RetriesExhausted.
        """
        return parse_items(self.client.get_json(f"{self.client.api_base}/items", auth=False))
