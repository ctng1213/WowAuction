"""The analyzer's hourly mode: the newest snapshot's state over the latest document, no database (SPEC §2.4, BZ32).

Inputs: the newest raw snapshot, the one before it (both from the listing, whatever the event said, BZ13) and
the latest published document. Each item the snapshot lists is repriced over its baselines
(`analysis.market.reprice_item`, the same formulas as the daily analysis); an item it no longer lists keeps its
fields and only its ages grow. Per item, `last_hour` is what sold, expired and was listed between the two
snapshots, and the `recent` rings carry the last 24 hours forward. Every baseline is copied through unchanged,
so the hourly cadence can't move a window or a rate (BZ33). `built_ms` stays the baselines' time;
`observations_until_ms` becomes the snapshot's.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ahcloud.publish import publish, read_latest, settings_from_dict
from ahcloud.store import Store
from ahsync.analysis.market import Quote, reprice_item
from ahsync.collect.blizzard import http_date_ms, open_envelope, parse_snapshot
from ahsync.collect.local_scans import prices_from_book
from ahsync.collect.provider import SnapshotObservation, load_bonus_suffixes
from ahsync.engine.volume import IdBook, Volume, estimate_volume_ids, group_book
from ahsync.intel.contract import ItemIntel, MarketIntelligence

HOUR_MS = 3_600_000
RING = 24                                  # hours in each recent ring


@dataclass(frozen=True)
class HourlyOutcome:
    """What an hourly run did."""

    state: str                             # "published", "up_to_date" (nothing newer) or "no_base" (no document yet)
    observations_until_ms: int | None = None
    repriced: int = 0                      # items the snapshot listed


def snapshot(store: Store, key: str, market: str) -> SnapshotObservation | None:
    """A raw object as a snapshot.

    Args:
        store: The data store.
        key: A raw envelope's key.
        market: Market id.

    Returns:
        The observation; None for an empty house (BZ5).

    Raises:
        ahsync.collect.blizzard.BlizzardError: The object is not a valid envelope or snapshot.
    """
    found = store.get(key)
    if found is None:
        return None
    stamp, regular, commodities = open_envelope(found.data)
    return parse_snapshot(market, regular, commodities, http_date_ms(stamp), load_bonus_suffixes()).observation


def number(value: float) -> str:
    """A ring value as text: integers plain, fractions to at most 3 decimals, never Python's round().

    Args:
        value: A count or a price.

    Returns:
        E.g. "4", "2.5", "0.333".
    """
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def shift_ring(ring: str, hours: int, latest: str) -> str:
    """A 24-value ring moved on by some hours, with the newest hour's value at the end (SPEC §2.4).

    Args:
        ring: The packed ring ("" for none yet: 24 empty hours).
        hours: Hours since the ring's last value (at least 1); the skipped hours become "".
        latest: The newest hour's value ("" when unknown).

    Returns:
        The packed ring, oldest first.
    """
    values = ring.split(",") if ring else [""] * RING
    values = (values + [""] * (hours - 1) + [latest])[-RING:]
    return ",".join([""] * (RING - len(values)) + values)


def ring_closes(ring: str) -> list[int]:
    """The closes a ring holds, for confirming an out-of-range price (FM5).

    Args:
        ring: A packed closes ring.

    Returns:
        The non-empty values as copper.
    """
    return [int(v) for v in ring.split(",") if v] if ring else []


def apply_snapshot(intel: MarketIntelligence, new: SnapshotObservation, flow: dict[int, Volume] | None
                   ) -> MarketIntelligence:
    """The document with a newer snapshot applied (SPEC §2.4 hourly mode, BZ32).

    Args:
        intel: The latest document.
        new: The newest snapshot (newer than intel.observations_until_ms).
        flow: Per item, what sold, expired and was listed since the previous snapshot; None when unknown.

    Returns:
        The new document: items the snapshot lists repriced, the others aged; rings shifted; built_ms unchanged.
    """
    settings = settings_from_dict(intel.settings)
    entries = [(e.key, e.price, e.stack, e.band, e.count) for e in group_book(IdBook(new.at_ms, new.auctions)).entries]
    quotes = prices_from_book(entries)
    books: dict[int, list[tuple[int, int, int, int]]] = {}
    for key, price, stack, band, count in entries:
        books.setdefault(key, []).append((price, stack, band, count))
    previous = intel.observations_until_ms or intel.built_ms
    hours = max(1, new.at_ms // HOUR_MS - previous // HOUR_MS)
    elapsed = new.at_ms - previous
    items: dict[int, ItemIntel] = {}
    for key, i in sorted(intel.items.items()):
        moved = (flow or {}).get(key)
        low = quotes.get(key)
        closes = shift_ring(i.recent_closes, hours, str(low[0]) if low else "")
        rings = dict(recent_closes=closes,
                     recent_sold=shift_ring(i.recent_sold, hours, number(moved.sold) if moved else ""),
                     recent_listed=shift_ring(i.recent_listed, hours, number(moved.listed) if moved else ""),
                     recent_units=shift_ring(i.recent_units, hours, str(low[2]) if low else "0"),
                     last_hour=(moved.sold, moved.expired, moved.listed) if moved else None)
        if low is None:                      # no longer listed: its last book stays, only the ages grow
            items[key] = replace(i, cur_age_ms=None if i.cur_age_ms is None else i.cur_age_ms + elapsed,
                                 book_age_ms=None if i.book_age_ms is None else i.book_age_ms + elapsed, **rings)
            continue
        vendor = intel.catalog.get(key, (None, None, None, 0))[3] or 0
        quote = Quote(low[0], low[1], tuple(books[key]))
        items[key] = replace(reprice_item(i, quote, new.at_ms, settings, int(vendor), ring_closes(closes)), **rings)
    return replace(intel, items=items, observations_until_ms=new.at_ms, ages_at_ms=new.at_ms)


def advance(data: Store, intel: MarketIntelligence, market: str) -> MarketIntelligence | None:
    """The document with the newest raw snapshot applied, when it is newer than the document's prices.

    Shared by the hourly mode and the end of the daily run, so both layer the state the same way.

    Args:
        data: The data store (raw/).
        intel: The document to layer over.
        market: Market id.

    Returns:
        The new document; None when no raw snapshot is newer than intel.observations_until_ms.

    Raises:
        ahsync.collect.blizzard.BlizzardError: A raw object is malformed.
    """
    keys = data.list(f"raw/{market}/")
    if not keys:
        return None
    new = snapshot(data, keys[-1], market)
    if new is None or new.at_ms <= (intel.observations_until_ms or 0):
        return None
    before = snapshot(data, keys[-2], market) if len(keys) > 1 else None
    flow = estimate_volume_ids(IdBook(before.at_ms, before.auctions), IdBook(new.at_ms, new.auctions)) if before else None
    return apply_snapshot(intel, new, flow)


def run_hourly(data: Store, intel_store: Store, market: str) -> HourlyOutcome:
    """One hourly run: apply the newest raw snapshot to the latest document and publish (SPEC §2.4, BZ32, BZ33).

    Args:
        data: The data store (raw/).
        intel_store: The intel store (the published documents).
        market: Market id.

    Returns:
        What it did.

    Raises:
        ValueError: The latest document doesn't match its pointer.
        ahsync.collect.blizzard.BlizzardError: A raw object is malformed.
    """
    latest = read_latest(intel_store, market)
    if latest is None:
        return HourlyOutcome("no_base")
    updated = advance(data, latest[0], market)
    if updated is None:
        return HourlyOutcome("up_to_date", latest[0].observations_until_ms)
    publish(intel_store, updated)
    listed = {i.key for i in updated.items.values() if i.cur_age_ms == 0}
    return HourlyOutcome("published", updated.observations_until_ms, len(listed))
