"""Tracking what you did (SPEC §3.4.1): captured events matched to plan actions, and posts resolved by auction mail.

- An event matches the earliest open action (pending, late or partial) with the same character,
  kind and item that is still open for matching: a buy, craft or mail within 24 h of its slot; a
  post from 24 h before its post time to the end of its selling window. A buy matches only at or
  below the planned price; a post at any price; a mail only to its planned recipient (T21). One event
  matches one action.
- Matching the full quantity makes an action done; part of it, partial.
- A sold mail resolves the oldest open post with the same character, item, stack and price; an
  expired or cancelled mail, the oldest with the same character, item and stack.
- A payout pays the oldest unpaid sold mail with the same character, item and quantity, and
  realizes its profit once: money − units × cost each − the deposit coming back with it (T17).
- History sets each event's expected net (copied when it matched) against what it realized: a post its payout's
  profit, −deposit when it expired; a chain's buy or craft what the chain's posts realized so far (D68-D70).
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
    to_char: str | None = None     # a mail's recipient (C22); None for other kinds


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
    to_char: str | None = None     # a sent mail's recipient (T21)


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
    sold_units: int = 0            # units its sold mails have taken so far (a commodity sells in parts, T25)
    returned_units: int = 0        # units its expired or cancelled mail brought back
    returned_as: str = "expired"   # that mail's outcome, which the post resolves as unless it all sold
    duration_h: int | None = None  # how long it was listed for; an expiry comes then (None: unknown)


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
    """What one mail did to its post: its units, and the post's outcome once they make its stack (T25)."""

    mail_id: str
    post_id: str
    outcome: str | None            # sold | expired | cancelled once sold + returned = stack; None while it is up
    at_ms: int
    sold_units: int = 0            # the post's units sold, this mail included
    returned_units: int = 0        # the post's units returned, this mail included


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
        event's time, (a buy) at or below the planned price, and (a mail with a recipient) to that recipient (T21).
    """
    if (a.char_id, a.kind, a.key) != (e.char_id, e.kind, e.key) or done >= a.qty or not _open_for(a, e):
        return False
    if a.kind == "mail" and a.to_char is not None and e.to_char != a.to_char:
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
    """Resolve posts by their auction mail, by units, oldest mail first (SPEC §3.4.1 post outcomes, T4, T5, T25).

    A commodity sells in parts, each buyer's units a sold mail, and what is left comes back in one expired or
    cancelled mail, so a post counts its units: it resolves once its sold and returned units make its stack, as sold
    when none came back, else as the returning mail's outcome. A whole stack resolves as before.

    Args:
        posts: Posts with no outcome yet, with the units their mail has already taken.
        mails: Auction mail not yet used.

    Returns:
        One Resolution per mail that took a post's units, mails oldest first and, at the same time, largest first:
        a sold mail the oldest open post with the same character, item and price and as many units left; an
        expired or cancelled one an open post with the same character and item (any price) and enough units left,
        an expired mail preferring the post whose listing ran out within an hour of it, then one with exactly its
        units left, then the oldest.
    """
    open_posts = sorted(posts, key=lambda p: (p.at_ms, p.post_id))
    units = {p.post_id: [p.sold_units, p.returned_units] for p in open_posts}
    returned_as = {p.post_id: p.returned_as for p in open_posts}
    out = []
    # A purchase that spans two of your posts writes a mail for each at the same time; the older post's share is
    # taken first, and a bigger mail can't fit a smaller remainder, so same-time mails go largest first (T25).
    for m in sorted(mails, key=lambda m: (m.at_ms, -m.qty, m.event_id)):
        if m.outcome not in RESOLVING:
            continue
        post = _post_for(m, open_posts, units)
        if post is None:
            continue
        taken = units[post.post_id]
        taken[0 if m.outcome == "sold" else 1] += m.qty
        if m.outcome != "sold":
            returned_as[post.post_id] = m.outcome
        done = sum(taken) == post.stack
        if done:
            open_posts.remove(post)
        outcome = ("sold" if taken[1] == 0 else returned_as[post.post_id]) if done else None
        out.append(Resolution(m.event_id, post.post_id, outcome, m.at_ms, taken[0], taken[1]))
    return out


def _post_for(m: AuctionMail, posts: Sequence[OpenPost], units: dict[str, list[int]]) -> OpenPost | None:
    """The open post one mail's units belong to (resolve_posts).

    Args:
        m: The mail.
        posts: Open posts, oldest first.
        units: Post id -> [units sold, units returned] so far.

    Returns:
        The post; None when none has the units left.
    """
    left = {p.post_id: p.stack - sum(units[p.post_id]) for p in posts}
    fits = [p for p in posts if (p.char_id, p.key) == (m.char_id, m.key) and p.at_ms <= m.at_ms
            and left[p.post_id] >= m.qty and (m.outcome != "sold" or p.price == m.price)]
    if m.outcome != "sold":                                   # stable: oldest first within each
        fits.sort(key=lambda p: (not _ran_out(p, m), left[p.post_id] != m.qty))
    return fits[0] if fits else None


def _ran_out(p: OpenPost, m: AuctionMail) -> bool:
    """Whether an expired mail comes when a post's listing ran out (within an hour of its post time + duration).

    Args:
        p: An open post.
        m: The mail.

    Returns:
        True for an expired mail and a post of known duration that ended within an hour of it.
    """
    return m.outcome == "expired" and p.duration_h is not None and abs(p.at_ms + p.duration_h * HOUR_MS - m.at_ms) <= HOUR_MS


# ---- Expected against realized profit, per History row (SPEC §3.4.1 "Expected and realized", D68-D70) ----

OPEN_POST, OPEN_CHAIN, OPEN_POSITION = "post", "chain", "position"   # Judged.open: what is still open


@dataclass(frozen=True)
class PostResult:
    """What became of one post, as far as is known."""

    outcome: str | None            # sold | expired | cancelled; None while it is up
    realized: float | None         # its payout's realized profit, copper (T17); None until paid
    deposit: int | None            # copper; None when not captured
    chain_id: str | None = None    # the chain of the action it matched, when that action is a chain's follow-up
    stack: int = 1                 # units posted
    returned_units: int = 0        # units its expired or cancelled mail brought back (T25); 0 before they were counted
    unpaid: int = 0                # its sold mails not taken from the mailbox yet (a commodity sells in parts, T26)


@dataclass(frozen=True)
class ChainFacts:
    """A chain carrier's event (a flip's buy, a craft chain's craft) and the chain's posts so far."""

    chain_id: str
    posts: tuple[PostResult, ...]  # the post events matched to the chain's post actions
    open_posts: int = 0            # the chain's post actions still pending or late
    disenchants: bool = False      # the chain plans a disenchant and no post: its mats are sold as posts of their own
    share: float = 1.0             # this event's part of its action: matched units ÷ the action's units


@dataclass(frozen=True)
class Judged:
    """A History row's expected and realized profit."""

    expected: float | None         # the plan's expected net copied at match time, copper; None = matched no action
    realized: float | None         # copper; None while open or when not tracked
    note: str                      # "not sold yet", "expired", "2 of 3 posts paid", "mats sold separately", ...
    open: str | None = None        # OPEN_POST or OPEN_CHAIN while the realized profit is still to come
    chain_id: str | None = None    # a carrier's chain, or a follow-up post's chain
    carrier: bool = False          # carries its chain's profit


def post_value(p: PostResult) -> float | None:
    """The profit one post realized (SPEC §3.4.1, D68).

    Args:
        p: The post.

    Returns:
        Copper: its payouts' realized profit once all are paid; when it expired or was cancelled, that less the
        deposit's share of the units that came back (all of it when none sold: the AH keeps a cancelled post's
        deposit, T24; 0 when the deposit is unknown; T26); None while it is up or a sale is not paid out yet.
    """
    if p.unpaid:
        return None
    if p.outcome in ("expired", "cancelled"):
        share = p.returned_units / p.stack if 0 < p.returned_units < p.stack else 1.0
        return (p.realized or 0.0) - float(p.deposit or 0) * share
    return p.realized if p.outcome == "sold" else None


def _judge_post(expected: float | None, p: PostResult) -> Judged:
    """A post row's realized profit (D68).

    Args:
        expected: Its copied expected net, copper, or None.
        p: What became of it.

    Returns:
        Judged: paid, expired ("expired") and cancelled ("cancelled") are resolved; up ("not sold yet") and sold
        but not taken from the mailbox ("sold · not paid out yet") are open.
    """
    value = post_value(p)
    if value is None:
        note = "sold · not paid out yet" if p.outcome == "sold" or p.unpaid else "not sold yet"
        return Judged(expected, None, note, OPEN_POST, p.chain_id)
    note = p.outcome if p.outcome in ("expired", "cancelled") else ""
    return Judged(expected, value, note, None, p.chain_id)


def _judge_chain(expected: float | None, c: ChainFacts) -> Judged:
    """A chain carrier's realized profit: what its chain's posts realized so far, its share of it (D69).

    Args:
        expected: Its copied expected net (the chain's, for its share), copper.
        c: The chain.

    Returns:
        Judged: "mats sold separately" (not tracked) for a disenchant chain; "nothing sold yet" while no post is
        resolved; else Σ post_value of its resolved posts × share, "N of M posts paid", open until every post is
        resolved and no post action is open.
    """
    if c.disenchants and not c.posts:
        return Judged(expected, None, "mats sold separately", None, c.chain_id, True)
    values = [v for v in (post_value(p) for p in c.posts) if v is not None]
    still = len(values) < len(c.posts) or c.open_posts > 0 or not c.posts
    if not values:
        return Judged(expected, None, "nothing sold yet", OPEN_CHAIN, c.chain_id, True)
    paid = sum(1 for p in c.posts if p.realized is not None and post_value(p) is not None)   # all its sales paid
    return Judged(expected, sum(values) * c.share, f"{paid} of {len(c.posts)} posts paid",
                  OPEN_CHAIN if still else None, c.chain_id, True)


def judge(kind: str, expected: float | None, facts: PostResult | ChainFacts | None) -> Judged | None:
    """A History row's expected and realized profit (SPEC §3.4.1, D68, D69).

    Args:
        kind: The row's kind ("posted", "bought", "crafted", "disenchanted", ...).
        expected: The expected net copied when it matched an action, copper; None when it matched none.
        facts: A post's PostResult, a chain carrier's ChainFacts, or None.

    Returns:
        Judged for a post, a chain carrier, any craft and a disenchant ("mats sold separately": not tracked);
        None for every other row (no cells). A craft that carries no chain shows no expected or realized profit.
    """
    if isinstance(facts, PostResult):
        return _judge_post(expected, facts)
    if isinstance(facts, ChainFacts):
        return _judge_chain(expected, facts)
    if kind == "disenchanted":
        return Judged(expected, None, "mats sold separately")
    return Judged(None, None, "") if kind == "crafted" else None


@dataclass(frozen=True)
class ProfitSums:
    """Expected against realized over the rows whose realized profit is known (D70, K7)."""

    expected: float                # copper
    realized: float                # copper
    resolved: int                  # rows counted
    open_posts: int                # rows still open, not counted
    open_chains: int
    open_positions: int = 0        # positions not yet Completed (SPEC §3.4D.9, PO9)


def profit_sums(rows: Sequence[Judged]) -> ProfitSums:
    """Σ expected and Σ realized over the resolved rows that matched a plan action (SPEC §3.5 History, D70).

    A chain's profit counts once: a follow-up post whose chain's carrier is among the rows counts on that carrier,
    not on its own. Rows that matched no action, and disenchants (not tracked), count nowhere. A position's row
    (SPEC §3.4D.9, PO9) counts once it is Completed; its events are left out of the rows by the caller.

    Args:
        rows: Judged rows (None-free).

    Returns:
        ProfitSums: the sums over resolved rows, and how many counted rows are still open (posts, chains,
        positions).
    """
    carried = {r.chain_id for r in rows if r.carrier}
    expected = realized = 0.0
    resolved = 0
    still = {OPEN_POST: 0, OPEN_CHAIN: 0, OPEN_POSITION: 0}
    for r in rows:
        if r.expected is None or (not r.carrier and r.chain_id in carried):
            continue
        if r.open is not None:
            still[r.open] += 1
        elif r.realized is not None:
            expected, realized, resolved = expected + r.expected, realized + r.realized, resolved + 1
    return ProfitSums(expected, realized, resolved, still[OPEN_POST], still[OPEN_CHAIN], still[OPEN_POSITION])
