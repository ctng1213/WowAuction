"""The Market Intelligence contract: versioned, market-wide results of historical analysis (SPEC §5.0).

This is the boundary between analysis (which could run in the cloud) and everything
that uses it (recommendations, export). It holds no character state and no raw
history: only per-item statistics, weekly patterns, windows, the sell-probability
curve and the latest observed market depth (SPEC §5.0 contents). JSON is
deterministic (sorted keys), so the same intelligence always serializes to the same
bytes (AR1). A document from a newer schema is refused (AR2).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

INTEL_SCHEMA_VERSION = 7       # 2: weekly patterns, windows, strength, curve (AR5); 3: profiles packed (W13); 4: cur_suspect (FM5);
                               # 5: median_daily_supply (V23); 6: observed_listing_turnover (V37); 7: volume_kind (D57)


class IntelVersionError(ValueError):
    """Intelligence written by a newer AHSync than this one."""


@dataclass(frozen=True)
class IntelWindow:
    """One selling or buying window (SPEC §3.3.4, §3.3.6). Slots are hours of the week, 0 = Mon 00:00 UTC."""

    start_slot: int
    hours: int                            # length in slots
    best_slot: int                        # selling: the peak hour; buying: the cheapest hour
    how_avg: float                        # average price ratio over the window
    weeks: int                            # weeks of data behind it (the fewest of its slots)


@dataclass(frozen=True)
class ItemIntel:
    """One item's market intelligence (SPEC §3.3, §3.3.6). None means unavailable, never 0."""

    key: int
    cur: int | None                       # lowest buyout, copper
    cur_qty: int | None                   # units at it
    cur_age_ms: int | None
    med7: int | None
    med30: int | None
    p10_30: int | None
    p90_30: int | None
    trend7: float                         # % per day
    sold_per_day: float | None            # estimated sales per day (SPEC §3.3.6)
    expired_per_day: float | None
    listed_per_day: float | None
    volume_source: str | None             # the source covering most of the last 7 days (its kind: volume_kind)
    days_by_source: dict[str, int]
    post_price: int | None
    ref_stack: int
    chance: float                         # P for posting one reference stack now
    volume_unknown: bool
    sell_value: float | None              # E_post per unit posting now, float copper
    conf: int                             # confidence %
    units_on_ah: int | None               # current listed supply
    book_age_ms: int | None
    book: tuple[tuple[int, int, int, int], ...] = field(default=())   # latest depth: (price, stack, band, count)
    # Weekly patterns (SPEC §3.3.4, §3.3.6), packed (SPEC §5.0 contents, W13): 168 comma-separated
    # ratios in thousandths and 168 week counts; "" means no data (flat 1.0, 0 weeks).
    # engine.profile.unpack reads them.
    how: str = ""
    how_weeks: str = ""
    sales_how: str = ""
    sales_weeks: str = ""
    supply_how: str = ""
    supply_weeks: str = ""
    seasonality: int | None = None        # strength 0-100; None = insufficient evidence
    sell_windows: tuple[IntelWindow, ...] = ()     # up to 3, best first
    buy_windows: tuple[IntelWindow, ...] = ()      # up to 3, cheapest first
    curve: str = ""                       # 24 × 8 encoded P grid (SPEC §3.1.1); "" when sales are unknown
    curve_hour: int | None = None         # hour number floor(ms / 3600000) of the curve's first row
    cur_suspect: bool = False             # cur is out of the usual range and unconfirmed: never bought from (FM5)
    median_daily_supply: int | None = None   # typical listed units, 30 days (V23); None without observations
    observed_listing_turnover: float | None = None   # units/day that left the AH, 7 days (V37); info only, never sales
    volume_kind: str | None = None        # volume_source's kind, "ids" | "groups" | "own" (D57); None in documents
                                          # before version 7, which are labelled by source name (views.volume_label)


@dataclass(frozen=True)
class MarketIntelligence:
    """Everything analysis says about one market at one time."""

    market: str
    built_ms: int                         # when analysis ran
    observations_until_ms: int | None     # the newest observation it used
    volume_method_version: int            # SPEC §3.3.6
    items: dict[int, ItemIntel]
    schema_version: int = INTEL_SCHEMA_VERSION


def price_level(i: ItemIntel) -> int | None:
    """The price level forecasts start from: med7, else med30 (SPEC §3.4C.1, R21).

    Args:
        i: The item's intelligence.

    Returns:
        Copper; None when neither median is known.
    """
    return i.med7 if i.med7 is not None else i.med30


def supply_level(i: ItemIntel) -> int | None:
    """The supply the liquidity cap starts from: listed now, else the 30-day median daily supply (V23).

    Args:
        i: The item's intelligence.

    Returns:
        Units; None when neither is known.
    """
    return i.units_on_ah if i.units_on_ah is not None else i.median_daily_supply


def to_json(intel: MarketIntelligence) -> str:
    """Serialize intelligence deterministically.

    Args:
        intel: The intelligence.

    Returns:
        JSON text with sorted keys; item keys become strings.
    """
    body = asdict(intel)
    body["items"] = {str(k): v for k, v in sorted(body["items"].items())}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def from_json(text: str) -> MarketIntelligence:
    """Read intelligence back (AR2).

    Args:
        text: From to_json.

    Returns:
        The intelligence.

    Raises:
        IntelVersionError: When it was written with a newer schema version.
        ValueError, KeyError, TypeError: When it is malformed.
    """
    body = json.loads(text)
    if body["schema_version"] > INTEL_SCHEMA_VERSION:
        raise IntelVersionError(f"intelligence from a newer AHSync (schema {body['schema_version']}, "
                                f"this reads up to {INTEL_SCHEMA_VERSION})")
    items = {}
    for key, item in body["items"].items():
        item["book"] = tuple(tuple(row) for row in item["book"])
        if body["schema_version"] < 3:         # version 2's unpacked profiles are dropped: the next refresh rebuilds them
            for name in ("how", "how_weeks", "sales_how", "sales_weeks", "supply_how", "supply_weeks"):
                item.pop(name, None)
        for name in ("sell_windows", "buy_windows"):
            if name in item:
                item[name] = tuple(IntelWindow(**w) for w in item[name])
        items[int(key)] = ItemIntel(**item)
    return MarketIntelligence(body["market"], body["built_ms"], body["observations_until_ms"],
                              body["volume_method_version"], items, body["schema_version"])
