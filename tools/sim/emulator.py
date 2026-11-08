"""Blizzard's side of the simulator: snapshots of the market, as the collector stores them (SPEC §2.4, §8.10).

A snapshot lists every live auction in Blizzard's record shapes: a regular auction with its total `buyout`, a
commodity with its `unit_price`, in two separate bodies, with `time_left` from the auction's true time remaining
read with Blizzard's band bounds (`LONG` 2–12 h, `VERY_LONG` over 12 h; `engine.volume.ID_BAND_HOURS`). The two
bodies go into the collector's raw envelope (`collect.blizzard.raw_envelope`) under the collector's key, so the
cloud job reads them exactly as it reads Blizzard's. Snapshot times follow Blizzard's measured cadence: about
hourly, between :16 and :30, with an occasional skipped refresh (SPEC §6 item 25).
"""
from __future__ import annotations

import json
import random
from collections.abc import Iterable, Sequence
from email.utils import formatdate

from ahcloud.store import Store
from ahsync.collect.blizzard import http_date_ms, open_envelope, parse_snapshot, raw_envelope, raw_key
from ahsync.engine.volume import IdBook
from sim.market import HOUR_MS, Live

RULESET, HOUSE = 2, 113
CADENCE_MINUTES = (16, 30)          # a refresh lands between :16 and :30 past the hour (measured, SPEC §6 item 25)
# Blizzard's time_left by hours remaining (its bounds, not the game client's): (upper bound in hours, name).
TIME_LEFT_BOUNDS = ((0.5, "SHORT"), (2.0, "MEDIUM"), (12.0, "LONG"))


def time_left(remaining_ms: int) -> str:
    """Blizzard's time-left band of an auction (SPEC §2.4 Auction lifetimes).

    Args:
        remaining_ms: Time until it expires, ms; over 0.

    Returns:
        "SHORT" up to 30 min, "MEDIUM" up to 2 h, "LONG" up to 12 h, else "VERY_LONG".
    """
    for hours, name in TIME_LEFT_BOUNDS:
        if remaining_ms <= hours * HOUR_MS:
            return name
    return "VERY_LONG"


def records(auctions: Iterable[Live], at_ms: int) -> tuple[list[dict], list[dict]]:
    """The live auctions as Blizzard's records.

    Args:
        auctions: The auctions listed at the snapshot.
        at_ms: The snapshot's time, UTC ms.

    Returns:
        (regular records, commodity records), each by auction id: a regular one carries its total `buyout`
        (unit price × units), a commodity its `unit_price`.
    """
    regular, commodities = [], []
    for a in sorted(auctions, key=lambda a: a.id):
        band = time_left(a.expires_ms - at_ms)
        if a.commodity:
            commodities.append({"id": a.id, "item": {"id": a.key}, "quantity": a.units, "unit_price": a.price,
                                "time_left": band})
        else:
            regular.append({"id": a.id, "item": {"id": a.key}, "quantity": a.units, "buyout": a.price * a.units,
                            "time_left": band})
    return regular, commodities


def envelope(at_ms: int, regular: Sequence[dict], commodities: Sequence[dict]) -> bytes:
    """A snapshot as the collector's raw envelope.

    Args:
        at_ms: The snapshot's time, UTC ms, whole seconds (Last-Modified has second precision).
        regular: Regular auction records.
        commodities: Commodity records.

    Returns:
        The gzipped envelope bytes, as `collect.blizzard.raw_envelope` writes them.

    Raises:
        ValueError: at_ms is not a whole second.
    """
    if at_ms % 1000:
        raise ValueError(f"a snapshot time must be a whole second: {at_ms}")
    return raw_envelope(RULESET, HOUSE, formatdate(at_ms / 1000, usegmt=True), at_ms, _body(regular), _body(commodities))


def capture(auctions: Iterable[Live], at_ms: int) -> bytes:
    """A snapshot of the live auctions.

    Args:
        auctions: The auctions listed at the snapshot.
        at_ms: The snapshot's time, UTC ms, whole seconds.

    Returns:
        The raw envelope.
    """
    return envelope(at_ms, *records(auctions, at_ms))


def with_duplicate(recs: Sequence[dict], auction_id: int) -> list[dict]:
    """Records with one of them repeated field for field (Blizzard did this once, BZ38).

    Args:
        recs: Records.
        auction_id: The record to repeat.

    Returns:
        A new list, the repeat right after the original.
    """
    out: list[dict] = []
    for r in recs:
        out.append(r)
        if r["id"] == auction_id:
            out.append(dict(r))
    return out


def with_conflict(recs: Sequence[dict], auction_id: int) -> list[dict]:
    """Records with a second, different record under one auction's id (refused whole, FM33).

    Args:
        recs: Records.
        auction_id: The record to contradict.

    Returns:
        A new list with a copy of that record holding one more unit.
    """
    out: list[dict] = []
    for r in recs:
        out.append(r)
        if r["id"] == auction_id:
            out.append(dict(r, quantity=r["quantity"] + 1))
    return out


def snapshot_times(start_ms: int, end_ms: int, rng: random.Random, skip: float = 0.0) -> list[int]:
    """When Blizzard refreshes: each hour between :16 and :30, some hours skipped.

    Args:
        start_ms: The span's start, UTC ms.
        end_ms: Its end, UTC ms.
        rng: The schedule's own random stream (so the schedule never moves the market's draws).
        skip: The chance an hour has no refresh.

    Returns:
        Whole-second times with start_ms < t ≤ end_ms, ascending.
    """
    times = []
    hour = start_ms // HOUR_MS * HOUR_MS
    while hour <= end_ms:
        at = hour + rng.randint(CADENCE_MINUTES[0] * 60, CADENCE_MINUTES[1] * 60) * 1000
        if rng.random() >= skip and start_ms < at <= end_ms:
            times.append(at)
        hour += HOUR_MS
    return times


def store_snapshot(store: Store, market: str, at_ms: int, data: bytes) -> str:
    """Write a snapshot where the collector would.

    Args:
        store: An `ahcloud.store.Store` (a LocalStore in the simulator).
        market: Market id.
        at_ms: The snapshot's time, UTC ms.
        data: The raw envelope.

    Returns:
        The key written, `raw/<market>/<YYYY>/<MM>/<DD>/<HHMMSS>.json.gz`.
    """
    key = raw_key(market, at_ms)
    store.put(key, data)
    return key


def id_book(data: bytes, market: str) -> IdBook:
    """A raw envelope read back as the engine sees it: the collector's parse, then an id book.

    Args:
        data: The raw envelope.
        market: Market id.

    Returns:
        The snapshot's IdBook (an empty house gives no auctions).
    """
    last_modified, regular, commodities = open_envelope(data)
    at_ms = http_date_ms(last_modified)
    parsed = parse_snapshot(market, regular, commodities, at_ms, {})
    return IdBook(at_ms, parsed.observation.auctions if parsed.observation else ())


def _body(recs: Sequence[dict]) -> bytes:
    """One response body: the house id and its auctions, compact JSON.

    Args:
        recs: Records.

    Returns:
        UTF-8 JSON bytes.
    """
    return json.dumps({"id": HOUSE, "auctions": list(recs)}, separators=(",", ":"), sort_keys=True).encode("utf-8")

