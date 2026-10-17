"""The Market Intelligence contract: versioned, market-wide results of historical analysis (SPEC §5.0).

This is the boundary between analysis (which could run in the cloud) and everything
that uses it (recommendations, export). It holds no character state and no raw
history: only per-item statistics and the latest observed market depth. JSON is
deterministic (sorted keys), so the same intelligence always serializes to the same
bytes (AR1). A document from a newer schema is refused (AR2).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

INTEL_SCHEMA_VERSION = 1


class IntelVersionError(ValueError):
    """Intelligence written by a newer AHSync than this one."""


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
    volume_source: str | None             # the source covering most of the last 7 days
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


@dataclass(frozen=True)
class MarketIntelligence:
    """Everything analysis says about one market at one time."""

    market: str
    built_ms: int                         # when analysis ran
    observations_until_ms: int | None     # the newest observation it used
    volume_method_version: int            # SPEC §3.3.6
    items: dict[int, ItemIntel]
    schema_version: int = INTEL_SCHEMA_VERSION


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
        items[int(key)] = ItemIntel(**item)
    return MarketIntelligence(body["market"], body["built_ms"], body["observations_until_ms"],
                              body["volume_method_version"], items, body["schema_version"])
