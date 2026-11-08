"""The simulator's truth log: every post, buy, cancel and expiry, as it really happened (SPEC §8.10).

The cloud analysis only ever sees snapshots; the truth log is what it is scored against. Each event carries the
units left in its auction afterwards, so an auction's end (bought out, expired or cancelled) is one event and a
partial fill is another. The log is written as JSON lines in time order, so the same run gives the same bytes.
"""
from __future__ import annotations

import json
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

    def to_jsonl(self) -> bytes:
        """The log as JSON lines, one event a line, keys sorted.

        Returns:
            UTF-8 bytes; the same events always give the same bytes.
        """
        return "".join(json.dumps(asdict(e), sort_keys=True, separators=(",", ":")) + "\n"
                       for e in self.events).encode("utf-8")
