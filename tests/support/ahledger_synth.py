"""Synthetic AHledger responses shaped like the real ones (SPEC §2.3), built from the AHF fixture items.

Used until fixtures are recorded live (`python tools/record_fixtures.py --ahledger`) and by the
recorder's own test. Deterministic: no clock, no randomness.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tests.support.fake_transport import DEFAULT_MARKET, FakeTransport

AHF_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ahf"
SUFFIX_BASE = 2_147_483_648
UPDATED_S = 1792585200          # Wed 21 Oct 2026 12:20 UTC
HOUR_S = 3600
DAY_S = 86_400
TABLE_ROWS = 2_207              # the real table's size on 2026-10-22


def ahf_prices(market: str = DEFAULT_MARKET) -> dict[int, tuple[int, int]]:
    """Base item id -> (price_copper, quantity) from the AHF snapshot fixture (lowest key wins per item).

    Args:
        market: Fixture folder under tests/fixtures/ahf.

    Returns:
        The prices, by ascending id.
    """
    rows = json.loads((AHF_FIXTURES / market / "snapshot.json").read_text(encoding="utf-8"))["rows"]
    out: dict[int, tuple[int, int]] = {}
    for key, price, qty, _at in sorted(rows):
        out.setdefault(key % SUFFIX_BASE, (price, qty))
    return dict(sorted(out.items()))


def table_text(market: str = DEFAULT_MARKET, total_rows: int = TABLE_ROWS, updated_s: int = UPDATED_S) -> str:
    """A price table of `total_rows` rows: the AHF fixture items first, then filler keys.

    Args:
        market: AHSync's dotted market id.
        total_rows: Rows in the table and in the header count.
        updated_s: Header update time, Unix seconds.

    Returns:
        The table text. Every 7th fixture row has no 7/30-day medians and every 11th no median,
        so the empty-field paths are exercised.
    """
    prices = ahf_prices(market)
    rows = []
    for index, key in enumerate(prices):
        price, qty = prices[key]
        median = "" if index % 11 == 10 else str(price + price // 10)
        m7, m30 = ("", "") if index % 7 == 6 else (str(price + price // 8), str(price + price // 12))
        rows.append(f"{key}:{median}:{price}:{qty}:{m7}:{m30}:{max(1, price * 3 // 4)}:{price * 2}:{qty // 3}")
    filler = 20_000
    while len(rows) < total_rows:
        rows.append(f"{filler}:120:100:5:::90:150:12")
        filler += 1
    return f"AHL1|{market.replace('.', '/')}|{updated_s}|{len(rows)}\n" + ";".join(rows) + "\n"


def markets_json(market: str = DEFAULT_MARKET) -> list[dict]:
    """The /v1/markets answer: our market and its alliance twin.

    Args:
        market: AHSync's dotted market id, which is among them.

    Returns:
        A list of market objects (id, game, ruleset, faction, region, label).
    """
    other = market.replace("horde", "alliance")
    return [{"id": m, "game": "forever", "ruleset": "pvp", "faction": m.split(".")[2], "region": "us",
             "label": "WoW: Forever PvP " + m.split(".")[2].title() + " (US)"} for m in sorted({market, other})]


def _iso(seconds: int) -> str:
    """UTC ISO-8601 with milliseconds and a Z, as AHledger writes times.

    Args:
        seconds: Unix seconds.

    Returns:
        e.g. "2026-10-21T12:00:00.000Z".
    """
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def history_json(market: str, key: int, rng: str, price: int, qty: int, updated_s: int = UPDATED_S) -> dict:
    """An item's /v1/prices answer: hourly buckets for 7d, daily for 90d (SPEC §2.3).

    Args:
        market: AHSync's dotted market id.
        key: Item id.
        rng: "7d" (168 hourly buckets) or "90d" (90 daily buckets).
        price: Typical minBuyout in copper; buckets wobble around it deterministically.
        qty: Typical quantity.
        updated_s: Time of the newest scan, Unix seconds.

    Returns:
        The decoded JSON, newest bucket last.
    """
    step, count = (HOUR_S, 168) if rng == "7d" else (DAY_S, 90)
    newest = updated_s // step * step
    series = []
    for age in range(count - 1, -1, -1):
        low = price + (age * 7) % 5 - 2
        series.append({"t": _iso(newest - age * step), "min": low, "median": low + 2, "p25": low + 1, "p75": low + 4,
                       "quantity": qty + (age * 13) % 40, "auctions": max(1, qty // 40)})
    return {"market": market, "item": {"id": key, "name": "Linen Cloth", "quality": 1},
            "latest": {"observedAt": _iso(updated_s), "source": "Addon scan", "minBuyout": price,
                       "median": price + 2, "quantity": qty, "auctions": max(1, qty // 40), "median7d": price + 2,
                       "median30d": price + 1, "median90d": None},
            "range": rng, "series": series}


def ahledger_transport(market: str = DEFAULT_MARKET) -> FakeTransport:
    """A FakeTransport answering like api.ahledger.com for the recorder: markets, table, Linen's histories.

    Args:
        market: AHSync's dotted market id.

    Returns:
        The transport (no ETag on the table).
    """
    transport = FakeTransport()
    transport.serve_bytes("v1/markets", json.dumps(markets_json(market)).encode())
    transport.serve_bytes(f"v1/pricetable/{market}", table_text(market).encode())
    price, qty = ahf_prices(market)[2589]
    for rng in ("7d", "90d"):
        transport.serve_bytes(f"v1/prices/{market}/2589?range={rng}",
                              json.dumps(history_json(market, 2589, rng, price, qty)).encode())
    return transport
