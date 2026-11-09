"""The simulator's truth log: every post, buy, cancel and expiry, as it really happened (SPEC §8.10).

The cloud analysis only ever sees snapshots; the truth log is what it is scored against. Each event carries the
units left in its auction afterwards, so an auction's end (bought out, expired or cancelled) is one event and a
partial fill is another. The log is written as JSON lines in time order, so the same run gives the same bytes.
"""
from __future__ import annotations

import bisect
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

KINDS = ("post", "buy", "cancel", "expire")


@dataclass(frozen=True)
class Event:
    """One thing that happened to an auction."""

    at_ms: int          # when, UTC ms
    kind: str           # one of KINDS
    auction: int        # the auction's id
    key: int            # item key
    units: int          # units posted, bought, cancelled or expired
    price: int          # the auction's unit price, copper
    left: int           # units left in the auction after this event; 0 when it ended
    actor: str          # who: a seller id for post and cancel, a buyer id for buy, "" for expire


class Truth:
    """An append-only event log with the sums the scorers compare against."""

    def __init__(self) -> None:
        """An empty log."""
        self.events: list[Event] = []

    def add(self, event: Event) -> None:
        """Append one event.

        Args:
            event: The event; its kind must be one of KINDS.

        Raises:
            ValueError: An unknown kind.
        """
        if event.kind not in KINDS:
            raise ValueError(f"unknown truth event kind: {event.kind!r}")
        self.events.append(event)

    def between(self, key: int, from_ms: int, to_ms: int) -> list[Event]:
        """An item's events in a time span.

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.

        Returns:
            The events with from_ms < at_ms ≤ to_ms, in log order.
        """
        return [e for e in self.events if e.key == key and from_ms < e.at_ms <= to_ms]

    def sold_units(self, key: int, from_ms: int, to_ms: int) -> int:
        """Units bought in a span (what `sold` should be).

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.

        Returns:
            Units.
        """
        return sum(e.units for e in self.between(key, from_ms, to_ms) if e.kind == "buy")

    def endings(self, key: int, from_ms: int, to_ms: int) -> tuple[int, int, int]:
        """Listings that ended in a span, and how (what `ended`, `expired` and `cancels` should be).

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.

        Returns:
            (ended, expired, cancelled) listings; a listing bought out counts in ended only.
        """
        ends = [e for e in self.between(key, from_ms, to_ms) if e.left == 0 and e.kind != "post"]
        return len(ends), sum(e.kind == "expire" for e in ends), sum(e.kind == "cancel" for e in ends)

    def positions(self, key: int, from_ms: int, to_ms: int, only: frozenset[int] | None = None
                  ) -> list[tuple[float, int]]:
        """Every post of an item in a span against the book at that instant, seen or not (what u(p) measures).

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.
            only: Keep only these auctions' posts (the book still holds every auction); None keeps all.

        Returns:
            (unit price ÷ the lowest other live price of the item, units posted) per post with another auction
            listed, in log order.
        """
        return [ratio_units for auction, ratio_units in self.post_ratios(key, from_ms, to_ms).items()
                if only is None or auction in only]

    def post_ratios(self, key: int, from_ms: int, to_ms: int) -> dict[int, tuple[float, int]]:
        """Each post of an item in a span, by auction, against the lowest other live price at that instant.

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.

        Returns:
            Auction id -> (unit price ÷ the lowest other live price, units posted), in log order; a post with no
            other auction of its item live is left out.
        """
        live: dict[int, int] = {}                  # auction -> unit price, for this item
        out: dict[int, tuple[float, int]] = {}
        for e in self.events:
            if e.key != key or e.at_ms > to_ms:
                continue
            if e.kind == "post":
                if live and e.at_ms > from_ms:
                    out[e.auction] = (e.price / min(live.values()), e.units)
                live[e.auction] = e.price
            elif e.left == 0:
                live.pop(e.auction, None)
        return out

    def early_leaves(self, key: int, from_ms: int, to_ms: int) -> tuple[int, int]:
        """Listings that ended before their expiry in a span, and how many of those were cancelled.

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.

        Returns:
            (bought out or cancelled listings, cancelled listings).
        """
        ended, expired, cancelled = self.endings(key, from_ms, to_ms)
        return ended - expired, cancelled

    def relists(self, key: int, from_ms: int, to_ms: int, within_ms: int) -> tuple[int, int]:
        """Cancelled listings their seller posted again (same item, same units), against all early leaves.

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms (of the cancel).
            to_ms: End, inclusive, UTC ms.
            within_ms: How soon after the cancel the new post must come.

        Returns:
            (early leaves in the span, of those the cancels followed by such a post).
        """
        ends = {e.auction: e for e in self.between(key, from_ms, to_ms) if e.kind == "cancel"}
        posts = [e for e in self.events if e.key == key and e.kind == "post"]
        relisted = sum(any(p.actor == c.actor and p.units == c.units and 0 <= p.at_ms - c.at_ms <= within_ms
                           and p.auction != c.auction for p in posts) for c in ends.values())
        return self.early_leaves(key, from_ms, to_ms)[0], relisted

    def seen_sold_units(self, key: int, from_ms: int, to_ms: int, snapshots: Sequence[int]) -> int:
        """Units bought in a span after their auction was in a snapshot (what a snapshot can see; plan §1.28).

        Args:
            key: Item key.
            from_ms: Start, exclusive, UTC ms.
            to_ms: End, inclusive, UTC ms.
            snapshots: Snapshot times, ascending, UTC ms.

        Returns:
            Units of the buys whose auction's first snapshot (the first at or after its post) came before the buy;
            units bought out before any snapshot saw them are left out.
        """
        posted = {e.auction: e.at_ms for e in self.events if e.key == key and e.kind == "post"}
        seen = 0
        for e in self.between(key, from_ms, to_ms):
            if e.kind != "buy":
                continue
            first = bisect.bisect_left(snapshots, posted[e.auction])
            if first < len(snapshots) and snapshots[first] < e.at_ms:
                seen += e.units
        return seen

    def seen(self, key: int, snapshots: Sequence[int]) -> frozenset[int]:
        """An item's auctions that at least one snapshot saw.

        Args:
            key: Item key.
            snapshots: Snapshot times, ascending, UTC ms.

        Returns:
            Auction ids still live at the first snapshot at or after their post.
        """
        posted, ended = {}, {}
        for e in self.events:
            if e.key == key and e.kind == "post":
                posted[e.auction] = e.at_ms
            elif e.key == key and e.left == 0:
                ended[e.auction] = e.at_ms
        out = set()
        for auction, at_ms in posted.items():
            first = bisect.bisect_left(snapshots, at_ms)
            if first < len(snapshots) and ended.get(auction, snapshots[first] + 1) > snapshots[first]:
                out.add(auction)
        return frozenset(out)

    def to_jsonl(self) -> bytes:
        """The log as JSON lines, one event a line, keys sorted.

        Returns:
            UTF-8 bytes; the same events always give the same bytes.
        """
        return "".join(json.dumps(asdict(e), sort_keys=True, separators=(",", ":")) + "\n"
                       for e in self.events).encode("utf-8")
