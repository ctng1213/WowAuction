"""Tracking what you did (SPEC §3.4.1): captured events matched to plan actions, and posts resolved by auction mail.

- An event matches the earliest open action (pending, late or partial) with the same character,
  kind and item that is still open for matching: a buy, craft or mail within 24 h of its slot; a
  post from 24 h before its post time to the end of its selling window. A buy matches only at or
  below the planned price; a post at any price. One event matches one action.
- Matching the full quantity makes an action done; part of it, partial.
- A sold mail resolves the oldest open post with the same character, item, stack and price; an
  expired or cancelled mail, the oldest with the same character, item and stack.
- A payout pays the oldest unpaid sold mail with the same character, item and quantity, and
  realizes its profit once: money − units × cost each − the deposit coming back with it (T17).
Pure: data in, decisions out.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

HOUR_MS = 3_600_000
MATCH_WINDOW_MS = 24 * HOUR_MS
RESOLVING = ("sold", "expired", "cancelled")   # mail outcomes that end a post; "won" is a purchase's


@dataclass(frozen=True)
class OpenAction:
    """A plan action still open for matching."""

    action_id: str
    char_id: str
    kind: str                      # buy | post | craft | mail
    key: int                       # the item (a craft: its product)
    qty: int
    done_qty: int
    price: int | None              # each, copper (a buy: the most to pay)
    slot_ms: int                   # when it was planned (a post: its post time)
    window_end_ms: int | None      # a post's selling window end


@dataclass(frozen=True)
class DoneEvent:
    """Something you did, as the action kind it can match."""

    event_id: str
    char_id: str
    kind: str                      # buy | post | craft | mail
    key: int                       # the item (a craft: the recipe's product)
    qty: int
    price: int | None              # each, copper (buys and posts)
    at_ms: int


@dataclass(frozen=True)
class Match:
    """An event matched to an action, for qty units."""

    event_id: str
    action_id: str
    qty: int


@dataclass(frozen=True)
class OpenPost:
    """A post with no outcome yet."""

    post_id: str
    char_id: str
    key: int
    stack: int
    price: int
    at_ms: int


@dataclass(frozen=True)
class AuctionMail:
    """An auction mail not yet used to resolve a post."""

    event_id: str
    char_id: str
    key: int
    qty: int
    price: int | None              # each, copper (sold)
    outcome: str                   # sold | expired | cancelled | won
    at_ms: int


@dataclass(frozen=True)
class Resolution:
    """A post's outcome, from a mail."""

    mail_id: str
    post_id: str
    outcome: str
    at_ms: int


def _open_for(a: OpenAction, e: DoneEvent) -> bool:
    """Whether an action is still open for matching an event at its time (SPEC §3.4.1).

    Args:
        a: The action.
        e: The event.

    Returns:
        A post: from 24 h before its post time to its window's end (24 h after its post time without one);
        any other kind: within 24 h of its slot.
    """
    if a.kind == "post":
        end = a.window_end_ms if a.window_end_ms is not None else a.slot_ms + MATCH_WINDOW_MS
        return a.slot_ms - MATCH_WINDOW_MS <= e.at_ms <= end
    return abs(e.at_ms - a.slot_ms) <= MATCH_WINDOW_MS


def _fits(a: OpenAction, e: DoneEvent, done: int) -> bool:
    """Whether an event can match an action.

    Args:
        a: The action.
        e: The event.
        done: Units of the action already matched.

    Returns:
        True for the same character, kind and item, units still to do, open for matching at the
        event's time, and (a buy) at or below the planned price.
    """
    if (a.char_id, a.kind, a.key) != (e.char_id, e.kind, e.key) or done >= a.qty or not _open_for(a, e):
        return False
    return a.kind != "buy" or (e.price is not None and a.price is not None and e.price <= a.price)


def match(events: Sequence[DoneEvent], actions: Sequence[OpenAction]) -> tuple[list[Match], dict[str, tuple[int, str]]]:
    """Match events to actions, oldest event first (SPEC §3.4.1, T1-T3, T16).

    Args:
        events: Unmatched events.
        actions: Open actions.

    Returns:
        (the matches by event time, action id -> (done units, "done" | "partial") for each action that changed).
    """
    done = {a.action_id: a.done_qty for a in actions}
    matches = []
    for e in sorted(events, key=lambda e: (e.at_ms, e.event_id)):
        fits = [a for a in actions if _fits(a, e, done[a.action_id])]
        if not fits:
            continue                # recorded anyway; it still updates inventory and cost
        a = min(fits, key=lambda a: (a.slot_ms, a.action_id))
        units = min(e.qty, a.qty - done[a.action_id])
        done[a.action_id] += units
        matches.append(Match(e.event_id, a.action_id, units))
    by_id = {a.action_id: a for a in actions}
    changed = {m.action_id for m in matches}
    status = {aid: (done[aid], "done" if done[aid] >= by_id[aid].qty else "partial") for aid in sorted(changed)}
    return matches, status


@dataclass(frozen=True)
class Payout:
    """Gold taken from a sold mail, not yet realized (SPEC §2.2.1 payout)."""

    event_id: str
    char_id: str
    key: int
    qty: int
    money: int                     # copper taken
    at_ms: int
    deposit: int | None = None     # the invoice's deposit, copper; None when not captured


@dataclass(frozen=True)
class SoldMail:
    """A sold mail no payout has paid yet."""

    event_id: str
    char_id: str
    key: int
    qty: int
    at_ms: int
    deposit: float                 # its post's deposit for these units, copper; 0 when its post is unknown


@dataclass(frozen=True)
class Realized:
    """A payout, the sold mail it paid and the profit it realized."""

    payout_id: str
    mail_id: str | None            # None when its sold mail was never captured
    profit: float                  # copper


def realize(payouts: Sequence[Payout], sold: Sequence[SoldMail], cost_each: dict[int, float]) -> list[Realized]:
    """Link payouts to sold mail and realize their profit, once each (SPEC §3.4.1, T17).

    Args:
        payouts: Payouts not yet realized.
        sold: Sold mails not yet paid.
        cost_each: Item key -> what a unit cost you, copper (the cost basis); missing counts 0.

    Returns:
        One Realized per payout, oldest first: it pays the oldest sold mail with the same character, item and
        quantity, and realizes money − qty × cost each − the deposit: the payout's own (its invoice's), else
        that mail's post's, else none.
    """
    unpaid = sorted(sold, key=lambda m: (m.at_ms, m.event_id))
    out = []
    for p in sorted(payouts, key=lambda p: (p.at_ms, p.event_id)):
        fits = [m for m in unpaid if (m.char_id, m.key, m.qty) == (p.char_id, p.key, p.qty)]
        mail = fits[0] if fits else None
        if mail:
            unpaid.remove(mail)
        deposit = p.deposit if p.deposit is not None else mail.deposit if mail else 0.0
        out.append(Realized(p.event_id, mail.event_id if mail else None, p.money - p.qty * cost_each.get(p.key, 0.0) - deposit))
    return out


def resolve_posts(posts: Sequence[OpenPost], mails: Sequence[AuctionMail]) -> list[Resolution]:
    """Resolve posts by their auction mail, oldest mail first (SPEC §3.4.1 post outcomes, T4, T5).

    Args:
        posts: Posts with no outcome yet.
        mails: Auction mail not yet used.

    Returns:
        One resolution per mail that resolved a post: a sold mail the oldest open post with the same character,
        item, stack and price; an expired or cancelled mail, the oldest with the same character, item and stack.
    """
    open_posts = sorted(posts, key=lambda p: (p.at_ms, p.post_id))
    out = []
    for m in sorted(mails, key=lambda m: (m.at_ms, m.event_id)):
        if m.outcome not in RESOLVING:
            continue
        fits = [p for p in open_posts if (p.char_id, p.key, p.stack) == (m.char_id, m.key, m.qty) and p.at_ms <= m.at_ms
                and (m.outcome != "sold" or p.price == m.price)]
        if fits:
            open_posts.remove(fits[0])
            out.append(Resolution(m.event_id, fits[0].post_id, m.outcome, m.at_ms))
    return out
