"""Positions: the gold you actually committed, and what became of it (SPEC §3.4D.9, PO1-PO13).

A position opens when a confirmed purchase or craft matches a funded opportunity's buy or craft action (§3.4.1
matching); an unconfirmed attempt, or a purchase that matches no funded row, opens none (PO2, PO12). It is not an
action, so a re-plan never replaces it (PO3). Its id is the opening event's id (`charId:timeMs:seq`), which the addon
also knows, so a local position in game and AHSync's exported one are the same record (A81).

- `place` decides which lot each newly matched event becomes: a lot of the open position of the same character,
  chain and item (several purchases filling one buy), else a new position. A craft whose chain already holds an
  open Processing position opens none: the craft converts that position (PO13). An extra purchase matched to a new
  funded action opens a new position (PO6).
- `track` replays, in time order, every event that moves the positions' units, and splits each one over the
  positions of that character and item oldest first (FIFO): a post lists units, its sold mail moves them to "sold,
  not paid out", its payout takes their cost out of committed capital and realizes `money − deposit − cost` (T17,
  PO4, PO9, PO11); an expired post brings them back and loses its deposit; a disenchant or a craft uses them (their
  cost leaves); a craft turns a Processing position's mats into its product, carrying the cost; a sent mail moves
  them to the recipient, in transit until a snapshot of hers is newer (T22, PO7). Then it reconciles: when the
  newest snapshots of a character show fewer units than its positions hold and no later event explains it, the
  positions are Needs reconciliation, their cost still committed (never a sale on missing inventory alone); after
  31 days the missing units close as an untracked loss at their cost (PO10).

Money: lot costs and cost totals are integer copper; cost left is `cost_total × units still yours ÷ units`, half
up in integer arithmetic; realized profit is float copper (it carries average costs). Pure: data in, records out.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ahsync.engine.fees import AH_CUT
from ahsync.engine.plan_chains import short_name
from ahsync.engine.portfolio import OpenPosition
from ahsync.engine.views import money

DAY_MS = 86_400_000
RECONCILE_DAYS = 31              # auction mail is kept 30 days: after 31, nothing can still explain missing units
ACQUIRING, PROCESSING, HOLDING, READY = "Acquiring", "Processing", "Holding", "Ready to sell"
LISTED, PARTIAL, COMPLETED, RECONCILE = "Listed", "Partially sold", "Completed", "Needs reconciliation"
STATES = (ACQUIRING, PROCESSING, HOLDING, READY, LISTED, PARTIAL, COMPLETED, RECONCILE)
PROCESS_STRATEGIES = ("craft", "multistage", "disenchant")    # their bought units are worked on before they sell
PLACES = ("bags", "bank", "mail")


@dataclass(frozen=True)
class Lot:
    """One matched purchase or craft event of a position (SPEC §3.2 position_lots)."""

    event_id: str
    units: int
    unit_cost: int                 # copper per unit
    at_ms: int


@dataclass(frozen=True)
class Opening:
    """A confirmed purchase or craft that matched a funded opportunity's buy or craft action."""

    event_id: str
    char_id: str
    key: int                       # the item bought, or the craft's product
    units: int
    unit_cost: int                 # copper per unit
    at_ms: int
    chain_id: str                  # the matched action's chain: its parent, else itself
    cand_id: str                   # the funded candidate whose opportunity made the action
    expected_net: float = 0.0      # this event's share of its opportunity's expected net, float copper
    target_window_ms: int | None = None   # the chain's first selling window's start, UTC ms
    craft: bool = False


@dataclass(frozen=True)
class Existing:
    """A stored position as `place` sees it."""

    position_id: str
    char_id: str
    key: int
    chain_id: str
    state: str


@dataclass(frozen=True)
class Placement:
    """Where one opening goes: a lot of an existing position, or a new position."""

    position_id: str
    opening: Opening
    new: bool


def place(openings: Sequence[Opening], existing: Sequence[Existing]) -> list[Placement]:
    """Turn newly matched events into lots (SPEC §3.4D.9, PO2, PO6, PO13).

    Args:
        openings: Matched confirmed purchases and crafts that are no lot yet.
        existing: The stored positions.

    Returns:
        One Placement per opening that becomes a lot, oldest first: the open position of the same character, chain
        and item takes it; else a craft whose chain holds an open Processing position becomes none (it converts that
        position); else it opens a new position whose id is its event id.
    """
    known = [e for e in existing if e.state != COMPLETED]
    out = []
    for o in sorted(openings, key=lambda o: (o.at_ms, o.event_id)):
        same = [e for e in known if (e.char_id, e.chain_id, e.key) == (o.char_id, o.chain_id, o.key)]
        if same:
            out.append(Placement(same[0].position_id, o, False))
        elif not (o.craft and any((e.char_id, e.chain_id, e.state) == (o.char_id, o.chain_id, PROCESSING)
                                  for e in known)):
            out.append(Placement(o.event_id, o, True))
            known.append(Existing(o.event_id, o.char_id, o.key, o.chain_id, ACQUIRING))
    return out


def strategy_of(cand_id: str) -> str:
    """A candidate id's strategy (SPEC §3.4D.3 ids: `<strategy>:<key>:<charId>:<route>:<batch>`).

    Args:
        cand_id: The candidate id.

    Returns:
        Its first part, e.g. "flip".
    """
    return cand_id.split(":", 1)[0]


@dataclass(frozen=True)
class Seed:
    """A stored position's opening facts: what `track` starts from."""

    position_id: str
    char_id: str                   # the character who committed the gold
    market: str | None
    cand_id: str
    key: int                       # the item it opened with (a Processing position: the mats)
    opened_at: int
    lots: tuple[Lot, ...]
    expected_net_open: float = 0.0
    target_window_ms: int | None = None
    acquiring_until: int | None = None   # its buy is partly done and open for matching until then, UTC ms
    to_buy: int = 0                      # units of that buy still to buy

    @property
    def strategy(self) -> str:
        """Its strategy.

        Returns:
            strategy_of(cand_id).
        """
        return strategy_of(self.cand_id)

    @property
    def processing(self) -> bool:
        """Whether a craft or disenchant step is still to do on its units when it opens.

        Returns:
            True for a disenchant, and for a craft or multistage craft whose opening item isn't its product.
        """
        target = int(self.cand_id.split(":")[1])
        return self.strategy == "disenchant" or (self.strategy in PROCESS_STRATEGIES and target != self.key)


@dataclass(frozen=True)
class Move:
    """One event that moves units of an item (SPEC §2.2.1 events)."""

    at_ms: int
    event_id: str
    kind: str                      # post | sold | expired | cancelled | payout | de | craft | mail
    char_id: str
    key: int                       # a craft: its product
    units: int                     # a craft: the units it made
    post_id: str | None = None     # post: its id; sold, expired, cancelled: the post it resolved (None: unlinked)
    mail_id: str | None = None     # sold, expired, cancelled: the mail; payout: the sold mail it paid (None: unknown)
    money: int | None = None       # sold: price each, copper; payout: copper taken
    deposit: int | None = None     # post: its deposit; payout: the invoice's deposit when captured; copper
    to_char: str | None = None     # mail: the recipient
    reagents: tuple[tuple[int, int], ...] = ()   # craft: (key, units used), by key


@dataclass(frozen=True)
class World:
    """What the newest snapshots show."""

    holdings: Mapping[tuple[str, int], int]       # (character, key) -> unbound units in bags, bank and mail
    snapshots: Mapping[tuple[str, str], int]      # (character, place) -> capture time, UTC ms


@dataclass(frozen=True)
class Position:
    """A position as its events leave it (SPEC §3.4D.9, §3.2 positions)."""

    position_id: str
    char_id: str                   # who holds its units now (a mail moves them to the recipient)
    market: str | None
    cand_id: str
    key: int                       # the item now (a craft turned Processing mats into its product)
    state: str
    opened_at: int
    units: int                     # units bought or made
    units_left: int                # held, in transit or listed
    cost_total: int                # copper paid
    cost_left: int                 # copper of units held, in transit, listed or sold but not paid out
    expected_net_open: float
    realized: float                # proceeds − cost of the units gone − lost deposits, float copper
    closed_at: int | None
    next_action: str               # what the facts alone say; position_views adds the plan's and the market's
    held: int = 0
    listed: int = 0
    sold: int = 0                  # units sold, paid out or not
    unpaid: int = 0                # units sold whose gold waits in the mailbox
    waiting: float = 0.0           # that gold after the cut, float copper
    transit_to: str | None = None  # the recipient of units still in transit
    missing: int = 0               # units the snapshots don't show (Needs reconciliation)
    lost: int = 0                  # units closed as an untracked loss after 31 days
    to_buy: int = 0
    acquiring_until: int | None = None
    target_window_ms: int | None = None
    strategy: str = ""
    events: tuple[str, ...] = ()   # the events that count on it (lots, posts, mail, payouts), for History (PO9)
    merged: bool = False           # its mats went into another Processing position's craft


class _Book:
    """One position's running state while `track` replays the events."""

    def __init__(self, seed: Seed) -> None:
        """Start from the opening facts.

        Args:
            seed: The position.
        """
        self.seed, self.holder, self.key = seed, seed.char_id, seed.key
        self.units = self.cost_total = self.held = self.sold = self.lost = self.missing = 0
        self.realized = 0.0
        self.transit: list[list] = []                    # [recipient, units, sent ms]
        self.listed: dict[str, int] = {}                 # post id -> units
        self.deposits: dict[str, float] = {}             # post id -> its deposit share, copper
        self.unpaid: dict[str, tuple[int, float]] = {}   # mail id -> (units, proceeds after the cut)
        self.last, self.processing, self.merged = seed.opened_at, seed.processing, False
        self.events: list[str] = []

    def avg(self) -> float:
        """Its average cost per unit.

        Returns:
            cost_total ÷ units, float copper; 0 before any unit.
        """
        return self.cost_total / self.units if self.units else 0.0

    def touch(self, at_ms: int, event_id: str) -> None:
        """Record that an event counted on it.

        Args:
            at_ms: The event's time.
            event_id: Its id.
        """
        self.last = max(self.last, at_ms)
        self.events.append(event_id)

    def fold(self, char_id: str, until_ms: int) -> None:
        """Units mailed to a character by then count as held (she acted on them, or a snapshot shows them).

        Args:
            char_id: The recipient.
            until_ms: Sends at or before this time arrive, UTC ms.
        """
        for t in [t for t in self.transit if t[0] == char_id and t[2] <= until_ms]:
            self.held += t[1]
            self.transit.remove(t)

    def remaining(self) -> int:
        """Units still yours: held, in transit, listed, or sold but not paid out.

        Returns:
            Units.
        """
        return (self.held + sum(t[1] for t in self.transit) + sum(self.listed.values())
                + sum(n for n, _ in self.unpaid.values()))


def _take(books: Sequence[_Book], m: Move, key: int, units: int) -> list[tuple[_Book, int]]:
    """Take units from the positions of the event's character and item, oldest first (FIFO).

    Args:
        books: Every position, oldest first.
        m: The event (its character, time and id).
        key: The item.
        units: Units to take.

    Returns:
        (position, units taken) for each position that gave some; units no position holds are your other stock.
    """
    out = []
    for b in books:
        if units <= 0:
            break
        if b.merged or b.holder != m.char_id or b.key != key or b.seed.opened_at > m.at_ms:
            continue
        b.fold(m.char_id, m.at_ms)
        n = min(b.held, units)
        if n:
            b.held, units = b.held - n, units - n
            b.touch(m.at_ms, m.event_id)
            out.append((b, n))
    return out


class _Replay:
    """The replay of every event over the positions (one `track` call)."""

    def __init__(self, books: list[_Book]) -> None:
        """Start the replay.

        Args:
            books: Every position, oldest first.
        """
        self.books = books
        self.posts: dict[str, list[tuple[_Book, int]]] = {}       # post id -> (position, units) listed
        self.mails: dict[str, list[tuple[_Book, int, float]]] = {}  # mail id -> (position, units, deposit share)

    def post(self, m: Move) -> None:
        """A post lists units (SPEC §3.4.1).

        Args:
            m: The post.
        """
        takes = _take(self.books, m, m.key, m.units)
        for b, n in takes:
            b.listed[m.event_id] = b.listed.get(m.event_id, 0) + n
            b.deposits[m.event_id] = b.deposits.get(m.event_id, 0.0) + (m.deposit or 0) * n / m.units
        self.posts[m.event_id] = takes

    def sold(self, m: Move) -> None:
        """A sold mail: its units wait in the mailbox, their cost still committed (T17, PO4).

        Args:
            m: The sold mail (money = price each).
        """
        if m.post_id in self.posts:
            takes = [(b, b.listed.pop(m.post_id, 0), b.deposits.pop(m.post_id, 0.0)) for b, _ in self.posts[m.post_id]]
            for b, _, _ in takes:
                b.touch(m.at_ms, m.event_id)
        else:                                            # its post wasn't captured: the units were held
            takes = [(b, n, 0.0) for b, n in _take(self.books, m, m.key, m.units)]
        for b, n, _ in takes:
            b.unpaid[m.event_id] = (n, (m.money or 0) * n * (1 - AH_CUT))
            b.sold += n
        self.mails[m.event_id] = [(b, n, d) for b, n, d in takes if n]

    def back(self, m: Move) -> None:
        """An expired or cancelled mail: its units come back; an expiry loses its deposit (D68).

        Args:
            m: The mail.
        """
        for b in [b for b in self.books if b.holder == m.char_id and b.key == m.key and b.seed.opened_at <= m.at_ms]:
            b.touch(m.at_ms, m.event_id)                 # unlinked: it still explains units the snapshot lacks
        for b, _ in self.posts.get(m.post_id or "", []):
            b.held += b.listed.pop(m.post_id, 0)
            deposit = b.deposits.pop(m.post_id, 0.0)
            b.realized -= deposit if m.kind == "expired" else 0.0

    def payout(self, m: Move) -> None:
        """Gold taken from a sold mail: the units' cost leaves committed capital as the gold joins your cash (T17).

        Args:
            m: The payout (money = copper taken; deposit = the invoice's, when captured).
        """
        paid = self.mails.pop(m.mail_id or "", None)
        if paid is None:                                 # its sold mail is unknown: units sold from what is held
            paid = [(b, n, 0.0) for b, n in _take(self.books, m, m.key, m.units)]
            for b, n, _ in paid:
                b.sold += n
        total = sum(n for _, n, _ in paid)
        for b, n, deposit in paid:
            b.unpaid.pop(m.mail_id or "", None)
            share = n / total
            own = deposit if m.deposit is None else m.deposit * share
            b.realized += (m.money or 0) * share - own - b.avg() * n
            b.touch(m.at_ms, m.event_id)

    def used(self, m: Move) -> None:
        """A disenchant uses one unit: its cost leaves (SPEC §3.4D.9).

        Args:
            m: The disenchant.
        """
        for b, n in _take(self.books, m, m.key, m.units):
            b.realized -= b.avg() * n

    def craft(self, m: Move) -> None:
        """A craft uses its reagents: a Processing position's mats become its product, carrying their cost (PO13);
        other positions' units are used (their cost leaves).

        Args:
            m: The craft (key = product, units = units made, reagents = what it used).
        """
        target = None
        for key, need in m.reagents:
            for b, n in _take(self.books, m, key, need):
                if b.processing and target is None:
                    target, made = b, max(1, m.units * n // need)
                    b.key, b.units, b.held, b.processing = m.key, made, made, False
                elif b.processing:
                    moved = int(b.avg() * n + 0.5)
                    target.cost_total, b.cost_total, b.units = target.cost_total + moved, b.cost_total - moved, b.units - n
                    b.merged = b.remaining() == 0
                else:
                    b.realized -= b.avg() * n

    def mail(self, m: Move) -> None:
        """A sent mail: units go to the recipient, in transit until a snapshot of hers shows them (T22, PO7).

        Args:
            m: The mail.
        """
        for b, n in _take(self.books, m, m.key, m.units):
            b.transit.append([m.to_char, n, m.at_ms])
            if b.held == 0:
                b.holder = m.to_char

    def lot(self, b: _Book, lot: Lot) -> None:
        """A lot arrives: its units are held and its cost committed.

        Args:
            b: Its position.
            lot: The lot.
        """
        b.units += lot.units
        b.cost_total += lot.units * lot.unit_cost
        b.held += lot.units
        b.touch(lot.at_ms, lot.event_id)


_HANDLERS = {"post": _Replay.post, "sold": _Replay.sold, "expired": _Replay.back, "cancelled": _Replay.back,
             "payout": _Replay.payout, "de": _Replay.used, "craft": _Replay.craft, "mail": _Replay.mail}


def _replay(seeds: Sequence[Seed], moves: Sequence[Move]) -> list[_Book]:
    """Replay lots and events in time order (a lot before any other event of the same moment).

    Args:
        seeds: The positions.
        moves: The events.

    Returns:
        Each position's book, oldest first.
    """
    books = [_Book(s) for s in sorted(seeds, key=lambda s: (s.opened_at, s.position_id))]
    replay = _Replay(books)
    timeline = [(lot.at_ms, 0, lot.event_id, b, lot) for b in books for lot in b.seed.lots]
    timeline += [(m.at_ms, 1, m.event_id, None, m) for m in moves if m.kind in _HANDLERS]
    for _, _, _, b, item in sorted(timeline, key=lambda t: (t[0], t[1], t[2], t[3].seed.position_id if t[3] else "")):
        if b is not None:
            replay.lot(b, item)
        else:
            _HANDLERS[item.kind](replay, item)
    return books


def _newest(snapshots: Mapping[tuple[str, str], int], char_id: str, places: Sequence[str]) -> int:
    """A character's newest capture time over some places.

    Args:
        snapshots: (character, place) -> ms.
        char_id: The character.
        places: Places.

    Returns:
        UTC ms; −1 when none was captured.
    """
    return max(snapshots.get((char_id, p), -1) for p in places)


def _reconcile(books: Sequence[_Book], world: World, now_ms: int | None) -> None:
    """Compare what the positions hold with the newest snapshots (SPEC §3.4D.9, PO10).

    Units mailed to a character arrive once her mailbox or bags snapshot is newer than the send. A character whose
    snapshots are all at or after her positions' last events and show fewer units than they hold has a gap no event
    explains: the positions, oldest first, miss those units (Needs reconciliation). After RECONCILE_DAYS without an
    event the missing units close as an untracked loss at their cost.

    Args:
        books: Every position.
        world: Holdings and snapshot times.
        now_ms: Now, UTC ms; None skips the 31-day rule.
    """
    for b in books:
        for t in list(b.transit):
            b.fold(t[0], _newest(world.snapshots, t[0], ("mail", "bags")))
    groups: dict[tuple[str, int], list[_Book]] = {}
    for b in books:
        if not b.merged and b.held > 0:
            groups.setdefault((b.holder, b.key), []).append(b)
    for (char_id, key), group in sorted(groups.items()):
        if _newest(world.snapshots, char_id, PLACES) < max(b.last for b in group):
            continue                                     # a snapshot older than an event can't show a gap
        short = sum(b.held for b in group) - world.holdings.get((char_id, key), 0)
        for b in group:
            n = max(0, min(b.held, short))
            short -= n
            if n and now_ms is not None and now_ms - b.last > RECONCILE_DAYS * DAY_MS:
                b.held, b.lost, b.realized = b.held - n, b.lost + n, b.realized - b.avg() * n
            else:
                b.missing = n


def _state(b: _Book, now_ms: int | None) -> str:
    """A position's state from its facts (SPEC §3.4D.9 table; Ready to sell needs the market: position_views).

    Args:
        b: The book after replay and reconciliation.
        now_ms: Now, UTC ms; None counts an unfinished buy as still open.

    Returns:
        Completed, Needs reconciliation, Acquiring, Processing, Partially sold, Listed or Holding, the first that holds.
    """
    s = b.seed
    if b.merged or b.remaining() == 0:
        return COMPLETED
    if b.missing:
        return RECONCILE
    if s.acquiring_until is not None and (now_ms is None or now_ms <= s.acquiring_until):
        return ACQUIRING
    if b.processing:
        return PROCESSING
    if b.sold:
        return PARTIAL
    return LISTED if any(b.listed.values()) else HOLDING


def fact_action(state: str, held: int, missing: int, unpaid: int, waiting: float, transit_to: str | None,
                to_buy: int) -> str:
    """What the facts alone say to do next (SPEC §3.4D.9 rows).

    Args:
        state: The position's state.
        held: Units the journal says are held.
        missing: Units the snapshots don't show.
        unpaid: Units sold whose gold waits in the mailbox.
        waiting: That gold after the cut, copper.
        transit_to: The recipient of units still in transit, or None.
        to_buy: Units of an unfinished buy still to buy.

    Returns:
        "collect mail on Amaya", "check bags and mail: 14 of 20 found", "collect mail: 15 sold, 25s 65c waiting",
        "buy 20 more", "—" for a closed position, or "" when the plan and the market decide (position_views).
    """
    if state == COMPLETED:
        return "—"
    if transit_to is not None:
        return f"collect mail on {short_name(transit_to)}"
    if state == RECONCILE:
        return f"check bags and mail: {held - missing} of {held} found"
    if unpaid:
        return f"collect mail: {unpaid} sold, {money(waiting)} waiting"
    return f"buy {to_buy} more" if state == ACQUIRING else ""


def _position(b: _Book, now_ms: int | None) -> Position:
    """A replayed book as a Position.

    Args:
        b: The book.
        now_ms: Now, UTC ms, or None.

    Returns:
        The Position: cost left = cost_total × units still yours ÷ units, half up (integer arithmetic).
    """
    s, state = b.seed, _state(b, now_ms)
    left = b.remaining()
    cost_left = (2 * b.cost_total * left + b.units) // (2 * b.units) if b.units else 0
    unpaid = sum(n for n, _ in b.unpaid.values())
    waiting = sum(v for _, v in b.unpaid.values())
    transit_to = b.transit[0][0] if b.transit else None
    listed = sum(b.listed.values())
    return Position(s.position_id, b.holder, s.market, s.cand_id, b.key, state, s.opened_at, b.units, left - unpaid,
                    b.cost_total, cost_left, s.expected_net_open, b.realized, b.last if state == COMPLETED else None,
                    fact_action(state, b.held, b.missing, unpaid, waiting, transit_to, s.to_buy), b.held, listed,
                    b.sold, unpaid, waiting, transit_to, b.missing, b.lost, s.to_buy, s.acquiring_until,
                    s.target_window_ms, s.strategy, tuple(dict.fromkeys(b.events)), b.merged)


def track(seeds: Sequence[Seed], moves: Sequence[Move], world: World, now_ms: int | None) -> list[Position]:
    """Every position as its lots and events leave it (SPEC §3.4D.9, PO2-PO13).

    Args:
        seeds: The stored positions with their lots.
        moves: Every event of their items since the oldest opened (any order; replayed by time, then id).
        world: The newest holdings and snapshot times.
        now_ms: Now, UTC ms; None skips the time rules (an unfinished buy's window, the 31-day loss).

    Returns:
        One Position per seed, oldest first (ties: position id).
    """
    books = _replay(seeds, moves)
    _reconcile(books, world, now_ms)
    return [_position(b, now_ms) for b in books]


def committed(positions: Sequence[Position]) -> list[OpenPosition]:
    """The committed capital: the cost left of every open position (SPEC §3.4D.1).

    Args:
        positions: Positions.

    Returns:
        One OpenPosition per position not Completed, in the given order.
    """
    return [OpenPosition(p.position_id, p.char_id, p.cost_left) for p in positions if p.state != COMPLETED]


def rolled_events(positions: Sequence[Position]) -> frozenset[str]:
    """The journal events that count on a position rather than on their own (SPEC §3.4.1, PO9).

    Args:
        positions: Positions.

    Returns:
        Every event id any position counted (lots, posts, mail, payouts).
    """
    return frozenset(e for p in positions for e in p.events)
