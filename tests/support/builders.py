"""Builders: real recorded data as the template, shaped per scenario (SPEC §8.1, §8.2).

- `item_key("Linen Cloth")` → AHF key, from the recorded /items.
- `copper("1g 20s 5c")` → 10205.
- `ms("T0+2h", t0)` / `duration_ms("89d23h")` → milliseconds.
- `hour_file`, `files_index`, `me`, `snapshot` reshape recorded responses.
- `AhfWorld` publishes a whole consistent AHF (API + CDN) into a FakeTransport for
  the fake clock's current time: /me, /files, every listed hour file, day candles,
  snapshot, and with Premium the h1c/d1c/volume/books files.
"""
from __future__ import annotations

import json
import re
from functools import cache

from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET, FIXTURES, FakeTransport

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
API = "api/v1"
CDN = "https://cdn.auctionhouse.fyi/"
PUBLIC_FEATURES = ["prices.snapshot", "candles.daily", "candles.hourly"]
UNIT_MS = {"d": DAY_MS, "h": HOUR_MS, "m": 60_000, "s": 1000}


@cache
def _load(name: str, market: str) -> str:
    """Read a fixture file once.

    Args:
        name: Fixture name without .json, e.g. "h1_data".
        market: Fixture folder.

    Returns:
        The file's text.
    """
    return (FIXTURES / market / f"{name}.json").read_text(encoding="utf-8")


def template(name: str, market: str = DEFAULT_MARKET) -> dict:
    """A fresh, mutable copy of a recorded response.

    Args:
        name: Fixture name, e.g. "files", "me", "h1_data".
        market: Fixture folder.

    Returns:
        The decoded JSON.
    """
    return json.loads(_load(name, market))


def item_key(name: str, market: str = DEFAULT_MARKET) -> int:
    """AHF item key for an item name in the recorded /items.

    Args:
        name: e.g. "Linen Cloth".
        market: Fixture folder.

    Returns:
        The key.

    Raises:
        KeyError: Unknown name.
    """
    items = template("items", market)
    for key, info in list(items["items"].items()) + list(items.get("variants", {}).items()):
        if info.get("n") == name:
            return int(key)
    raise KeyError(name)


def copper(text: str) -> int:
    """Parse a WoW price like "1g 20s 5c" (any parts, any order of spacing).

    Args:
        text: e.g. "48c", "1g 20s 5c".

    Returns:
        Copper.

    Raises:
        ValueError: Text that isn't a price.
    """
    parts = re.findall(r"(\d+)\s*([gsc])", text)
    if not parts or re.sub(r"[\d\sgsc]", "", text):
        raise ValueError(f"not a price: {text!r}")
    factor = {"g": 10_000, "s": 100, "c": 1}
    return sum(int(n) * factor[unit] for n, unit in parts)


def duration_ms(text: str) -> int:
    """Parse a duration like "89d23h" or "2h30m".

    Args:
        text: Number+unit pairs, units d/h/m/s.

    Returns:
        Milliseconds.

    Raises:
        ValueError: Not a duration.
    """
    parts = re.findall(r"(\d+)([dhms])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"not a duration: {text!r}")
    return sum(int(n) * UNIT_MS[u] for n, u in parts)


def ms(text: str, t0_ms: int) -> int:
    """Parse a scenario time like "T0", "T0+2h" or "T0-89d23h".

    Args:
        text: The time expression.
        t0_ms: What T0 means, UTC ms.

    Returns:
        UTC ms.

    Raises:
        ValueError: Not a time expression.
    """
    match = re.fullmatch(r"T0(?:([+-])(\w+))?", text)
    if not match:
        raise ValueError(f"not a time: {text!r}")
    if not match.group(1):
        return t0_ms
    delta = duration_ms(match.group(2))
    return t0_ms + delta if match.group(1) == "+" else t0_ms - delta


def hour_file(hour: int, close_by_key: dict[int, int] | None = None, market: str = DEFAULT_MARKET) -> dict:
    """The recorded hour-candle file moved to another hour.

    Args:
        hour: floor(ms/3600000) to stamp on every candle.
        close_by_key: Optional close prices (copper) to set per key; others keep the recording's.
        market: Fixture folder.

    Returns:
        An h1 file whose candles all have t = hour start.
    """
    data = template("h1_data", market)
    for item in data["items"]:
        for candle in item[1]:
            candle[0] = hour * HOUR_MS
            if close_by_key and item[0] in close_by_key:
                candle[4] = close_by_key[item[0]]
    data["t"] = hour * HOUR_MS
    return data


def day_file(day: int, days: int, market: str = DEFAULT_MARKET) -> dict:
    """The recorded d1c file reshaped to cover `days` days ending at `day`.

    Args:
        day: Newest day, floor(ms/86400000).
        days: Days covered (7 for public d1, 90 for d1c).
        market: Fixture folder.

    Returns:
        A day-candle file with one candle per item per day.
    """
    data = template("d1c", market)
    for item in data["items"]:
        candle = item[1][0]
        item[1] = [[d * DAY_MS, *candle[1:]] for d in range(day - days + 1, day + 1)]
    data["t"], data["days"] = day * DAY_MS, days
    return data


def me(premium: bool = False, until_ms: int | None = None, market: str = DEFAULT_MARKET) -> dict:
    """A /me response with or without the Premium (contributor) feature set.

    Args:
        premium: True for the recorded keyed response; False for the public tier.
        until_ms: contributor.until when premium; None keeps the recording's.
        market: Fixture folder.

    Returns:
        The /me JSON.
    """
    data = template("me", market)
    if premium:
        if until_ms is not None:
            data["contributor"]["until"] = until_ms
        return data
    return {"features": list(PUBLIC_FEATURES), "limits": {"apiPerMinute": 60, "historyDays": 7, "feedMinutes": 60},
            "roles": ["anonymous"], "now": data["now"]}


def files_index(market: str, sealed_h1: int, sealed_d1: int, premium: bool = False,
                caps_days: list[int] | None = None, book_at_ms: int | None = None) -> dict:
    """A /files response shaped from the recording.

    Args:
        market: AHF market id.
        sealed_h1: Newest sealed hour.
        sealed_d1: Newest sealed day.
        premium: Include `caps` and `books` (only listed for Premium keys).
        caps_days: Days with an h1c token "cap-h1-<day>" (Premium only).
        book_at_ms: Time of the listed AHF book (Premium only).

    Returns:
        The /files JSON.
    """
    data = template("files", DEFAULT_MARKET)
    data["market"] = market
    data["sealed"] = {"d1": sealed_d1, "h1": sealed_h1, "m1": sealed_h1 * 60}
    data["d1"] = f"{CDN}candles/v1/d1/{market}/{sealed_d1}.json.gz"
    data["snapshot"] = f"{CDN}snap/v1/{market}/latest.json"
    if not premium:
        for name in ("caps", "books", "live"):
            data.pop(name, None)
        return data
    data["caps"] = {
        "d1": f"{CDN}candles/v1/d1c/{market}/cap-d1/{sealed_d1}.json.gz",
        "h1": {str(day): f"cap-h1-{day}" for day in (caps_days or [])},
        "volume": f"{CDN}volume/v1/{market}/cap-volume/{sealed_d1}.json.gz",
    }
    at = book_at_ms if book_at_ms is not None else sealed_h1 * HOUR_MS
    data["books"] = [{"at": at, "complete": True, "url": f"{CDN}books/v1/{market}/{at}-cap-book-{at}.json.gz"}]
    return data


def snapshot(at_ms: int, market: str = DEFAULT_MARKET) -> dict:
    """The recorded snapshot re-stamped to a time.

    Args:
        at_ms: Snapshot time (top of an hour), ms.
        market: AHF market id.

    Returns:
        The snapshot JSON; each row's scanAt is kept from the recording.
    """
    data = template("snapshot", DEFAULT_MARKET)
    data["at"], data["now"], data["market"] = at_ms, at_ms + 60_000, market
    return data


def h1_path(market: str, hour: int) -> str:
    """CDN path of a public hour file.

    Args:
        market: AHF market id.
        hour: The hour.

    Returns:
        Route path.
    """
    return f"candles/v1/h1/{market}/{hour}.json.gz"


def h1c_path(market: str, hour: int) -> str:
    """CDN path of a Premium hour file (token per day, as AhfWorld publishes it).

    Args:
        market: AHF market id.
        hour: The hour.

    Returns:
        Route path.
    """
    return f"candles/v1/h1c/{market}/cap-h1-{hour * HOUR_MS // DAY_MS}/{hour}.json.gz"


class AhfWorld:
    """A consistent fake AHF for the clock's current time, served by a FakeTransport."""

    def __init__(self, clock: FakeClock, premium: bool = False, market: str = DEFAULT_MARKET,
                 until_ms: int | None = None, transport: FakeTransport | None = None) -> None:
        """Publish everything for now.

        Args:
            clock: The fake clock; `publish()` uses its current time.
            premium: Serve the keyed (contributor) responses and Premium files.
            market: AHF market id.
            until_ms: contributor.until for /me when premium.
            transport: Reuse a transport (keeps recorded requests and faults); default a new one.
        """
        self.clock, self.premium, self.market, self.until_ms = clock, premium, market, until_ms
        self.transport = transport or FakeTransport()
        self.publish()

    @property
    def now_hour(self) -> int:
        """Current hour.

        Returns:
            floor(now/3600000).
        """
        return self.clock.now_ms() // HOUR_MS

    @property
    def sealed_h1(self) -> int:
        """Newest complete hour: the previous one.

        Returns:
            now_hour - 1.
        """
        return self.now_hour - 1

    @property
    def sealed_d1(self) -> int:
        """Newest complete day: yesterday.

        Returns:
            floor(now/86400000) - 1.
        """
        return self.clock.now_ms() // DAY_MS - 1

    def public_hours(self) -> list[int]:
        """Hours a public h1 file exists for: under 7 days old, up to sealed.

        Returns:
            Ascending hours.
        """
        return list(range(self.now_hour - 7 * 24 + 1, self.sealed_h1 + 1))

    def premium_hours(self) -> list[int]:
        """Hours an h1c file exists for: 7 to 14 days old.

        Returns:
            Ascending hours.
        """
        return list(range(self.now_hour - 14 * 24 + 1, self.now_hour - 7 * 24 + 1))

    def publish(self) -> None:
        """(Re)serve every endpoint for the clock's current time. Old hour files stay served."""
        t, m = self.transport, self.market
        t.serve(f"{API}/config", template("config"))
        t.serve(f"{API}/me", me(self.premium, self.until_ms))
        t.serve(f"{API}/items", template("items"))
        caps_days = sorted({h * HOUR_MS // DAY_MS for h in self.premium_hours()})
        t.serve(f"{API}/ah/{m}/files", files_index(m, self.sealed_h1, self.sealed_d1, self.premium, caps_days,
                                                   book_at_ms=self.sealed_h1 * HOUR_MS))
        pointer_hour = self.now_hour
        t.serve(f"snap/v1/{m}/latest.json", {"at": pointer_hour * HOUR_MS, "hour": pointer_hour, "market": m,
                                             "path": f"snap/v1/{m}/{pointer_hour}.json.gz", "v": 1})
        t.serve(f"snap/v1/{m}/{pointer_hour}.json.gz", snapshot(pointer_hour * HOUR_MS, m))
        t.serve(f"candles/v1/d1/{m}/{self.sealed_d1}.json.gz", day_file(self.sealed_d1, 7))
        for hour in self.public_hours():
            t.serve(h1_path(m, hour), hour_file(hour))
        if self.premium:
            for hour in self.premium_hours():
                t.serve(h1c_path(m, hour), hour_file(hour))
            t.serve(f"candles/v1/d1c/{m}/cap-d1/{self.sealed_d1}.json.gz", day_file(self.sealed_d1, 90))
            t.serve(f"volume/v1/{m}/cap-volume/{self.sealed_d1}.json.gz", template("volume"))
            at = self.sealed_h1 * HOUR_MS
            book = template("book")
            book["at"] = at
            t.serve(f"books/v1/{m}/{at}-cap-book-{at}.json.gz", book)
            now = self.clock.now_ms()
            live_rows = [[row[0], row[1], row[2], now] for row in snapshot(now, m)["rows"][:3]]
            t.serve(f"{API}/ah/{m}", {"at": now, "rows": live_rows})   # assumed live shape (SPEC GAP in ahf.live)

    def advance(self, ms_: int) -> None:
        """Move the clock forward and republish (AHF keeps sealing new hours).

        Args:
            ms_: Milliseconds to advance.
        """
        self.clock.advance(ms_)
        self.publish()
