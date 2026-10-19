"""Mail on its way: items one of your characters mailed to another that no snapshot holds yet (SPEC §3.4B, T22).

Between the send and the recipient's next mailbox or bags capture, a mailed item is in nobody's recorded stock: the
sender's bags no longer hold it and the recipient's mail snapshot is older than the send. Counting it as the
recipient's stock (place "transit") keeps a re-plan from buying the mats again or crafting fewer (C23). Pure.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DAY_MS = 86_400_000
TRANSIT_DAYS = 30                 # mail is returned after 30 days (SPEC §3.4B): after that it is no longer on its way


@dataclass(frozen=True)
class Sent:
    """A confirmed mail_sent event (SPEC §2.2.1)."""

    event_id: str
    sender: str                   # character id
    recipient: str                # character id
    key: int
    qty: int
    at_ms: int


@dataclass(frozen=True)
class Transit:
    """Units on their way to a character."""

    recipient: str
    sender: str
    key: int
    qty: int
    sent_at_ms: int


def in_transit(sent: Sequence[Sent], snapshots: Mapping[tuple[str, str], int], now_ms: int,
               max_age_ms: int = TRANSIT_DAYS * DAY_MS) -> list[Transit]:
    """The sends still on their way (SPEC §3.4B mail on its way, T22).

    A send is on its way while (all three):
    - it is newer than the recipient's newest mail and bags snapshots (a snapshot after it already shows the item);
    - the sender's bags snapshot is at or after it, or the sender has none (before that, the snapshot still counts
      the item in the sender's bags, so counting it twice would be wrong);
    - it is at most max_age_ms old (mail comes back after 30 days).

    Args:
        sent: Confirmed sends.
        snapshots: (character id, place) -> capture time, UTC ms; missing = never captured.
        now_ms: Now, UTC ms.
        max_age_ms: How long a send can be on its way, ms.

    Returns:
        One Transit per send on its way, by recipient, key, send time, then event id.
    """
    out = []
    for s in sorted(sent, key=lambda s: (s.recipient, s.key, s.at_ms, s.event_id)):
        seen = max(snapshots.get((s.recipient, "mail"), -1), snapshots.get((s.recipient, "bags"), -1))
        left = snapshots.get((s.sender, "bags"))
        if s.at_ms > seen and (left is None or left >= s.at_ms) and now_ms - s.at_ms <= max_age_ms:
            out.append(Transit(s.recipient, s.sender, s.key, s.qty, s.at_ms))
    return out


def units(transit: Sequence[Transit]) -> dict[tuple[str, int], int]:
    """Units on their way per recipient and item.

    Args:
        transit: From in_transit.

    Returns:
        (recipient, key) -> units, sorted.
    """
    out: dict[tuple[str, int], int] = {}
    for t in transit:
        out[(t.recipient, t.key)] = out.get((t.recipient, t.key), 0) + t.qty
    return dict(sorted(out.items()))
