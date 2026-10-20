"""What each position should do next, and how it shows (SPEC §3.4D.9, §3.5 section A and History, PO3-PO11).

engine.positions says what the facts are; this module adds the plan and the market, every refresh, re-plan and
status pass (the addon does the same live):

- **Adaptive exit** (PO5): a Holding position is Ready to sell when now is inside its window, or selling now beats
  waiting, `E_now ≥ E_target ÷ (1 + premium)` with the premium for the wait to its window (engine.timing, D.2).
  What you paid plays no part (sunk). E_now is the market's posting-now value of its units; E_target the plan's
  expected value at its window.
- **Next action**: the facts' own first ("collect mail on Amaya", "check bags and mail: 14 of 20 found", "collect
  mail: 15 sold, 25s 65c waiting", "buy 20 more by Tue 23:00"); then "undercut: relist at 4g 49s 99c" for a listed
  position someone else lists at or below; "smelt today · post Thu 11:00 for the 19:00 peak" while Processing;
  "sell now: price spike +38%" when Ready to sell; else the plan's action for its units ("post Sat 11:00 for the
  19:00 peak", "hold", "disenchant", "sell to a vendor"), flagged "below what you paid" when its expected value no
  longer covers the cost left (PO3, T7).
- **Found stock** the plan holds for a later window is a Holding row with Paid 0 (D.9's last bullet): rebuilt at
  every run, never stored, so it commits nothing (PF22).
- **History** has one row per position (PO9): Expected is its expected net at open, Realized its proceeds less the
  cost of the units gone and lost deposits once it is Completed.

Pure: data in, rows out. Money is rounded half up only when formatted (§3.3).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ahsync.engine.plan_chains import clock
from ahsync.engine.positions import ACQUIRING, COMPLETED, HOLDING, PROCESSING, READY, RECONCILE, Position
from ahsync.engine.timing import adjusted, premium_micro, wait_hundredths
from ahsync.engine.tracking import OPEN_POSITION, Judged
from ahsync.engine.views import money

DAY_MS = 86_400_000
STEP_TEXT = {"hold": "hold", "de": "disenchant", "vendor": "sell to a vendor", "craft": "craft"}


@dataclass(frozen=True)
class SellStep:
    """One plan action for units a character holds: a post, hold, disenchant, vendor sale or craft."""

    kind: str
    char_id: str
    key: int
    qty: int
    at_ms: int                     # when to act, UTC ms
    window_start_ms: int | None    # a post's selling window; None posting now
    window_end_ms: int | None
    peak_ms: int | None            # its best hour
    expected: float                # expected value, float copper
    net: float                     # expected net, float copper
    own: bool = True               # for units held now; False for a chain's later step (units still to buy or make)


@dataclass(frozen=True)
class MarketNow:
    """The market for one item now (the published intelligence, repriced by scans since)."""

    sell_value: float | None       # posting-now value per unit, float copper (E_now's)
    post_price: int | None         # the posting-now price, copper each
    median: int | None             # its median (med7, else med30), copper
    book: tuple[tuple[int, int], ...] = ()   # (price each, units) listed now, yours included


@dataclass(frozen=True)
class Outlook:
    """What the plan and the market say about one position."""

    steps: tuple[SellStep, ...] = ()       # the plan's actions for its character and item
    share: float = 1.0                     # its part of those actions' units
    market: MarketNow | None = None
    mine: tuple[tuple[int, int], ...] = ()  # (price each, units) of your listings of it now
    verb: str = "craft"                    # Processing: "smelt", "craft" or "disenchant"
    process_ms: int | None = None          # when the plan works on it, UTC ms; None: not planned
    then: SellStep | None = None           # Processing: the plan's first post of the product


@dataclass(frozen=True)
class Clock:
    """Now, the local offset and the timing premium's settings (SPEC §3.4D.2)."""

    now_ms: int
    offset_ms: int = 0
    premium_base: float = 0.15
    premium_per_day: float = 0.05
    immediate_hours: int = 8


@dataclass(frozen=True)
class PositionView:
    """A row of section A and of PortfolioPlan.lua's `positions` (SPEC §3.5)."""

    position_id: str               # the opening event's id; "found:<char>:<key>" for found stock
    char_id: str
    key: int
    state: str
    units: int
    units_left: int
    paid: int                      # copper paid (cost_total); 0 for found stock
    cost_left: int                 # copper still committed
    net_now: float | None          # est. net now, float copper; None unknown (Needs reconciliation)
    window_ms: int | None          # the latest recommended selling window's start (else the original), UTC ms
    next_action: str
    found: bool = False


def when(ms: int, c: Clock) -> str:
    """A moment as a next action says it.

    Args:
        ms: UTC ms.
        c: The clock.

    Returns:
        "now" at or before now, "today 11:00" later today (local), else "Thu 11:00".
    """
    if ms <= c.now_ms:
        return "now"
    day, hour = clock(ms, c.offset_ms)
    today = (ms + c.offset_ms) // DAY_MS == (c.now_ms + c.offset_ms) // DAY_MS
    return f"today {hour}" if today else f"{day} {hour}"


def step_text(s: SellStep | None, c: Clock) -> str:
    """A plan action as a next action.

    Args:
        s: The action, or None.
        c: The clock.

    Returns:
        "post Sat 11:00 for the 19:00 peak", "post now", "hold", "disenchant", "sell to a vendor", "craft"; "hold"
        without an action.
    """
    if s is None:
        return "hold"
    if s.kind != "post":
        return STEP_TEXT.get(s.kind, s.kind)
    if s.window_start_ms is None or s.peak_ms is None:
        return "post now"
    return f"post {when(s.at_ms, c)} for the {clock(s.peak_ms, c.offset_ms)[1]} peak"


def ready_to_sell(e_now: float, e_target: float, start_ms: int, end_ms: int | None, c: Clock) -> bool:
    """Whether selling now beats waiting for the window (SPEC §3.4D.9 adaptive exit, PO5).

    Args:
        e_now: Expected value of selling now, float copper (what you paid left out).
        e_target: Expected value at the window, float copper.
        start_ms: The window's start, UTC ms.
        end_ms: Its end, or None.
        c: The clock and the premium settings.

    Returns:
        True inside the window, or when e_now ≥ e_target ÷ (1 + premium for the wait to its start).
    """
    if start_ms <= c.now_ms and (end_ms is None or c.now_ms <= end_ms):
        return True
    premium = premium_micro(wait_hundredths(c.now_ms, start_ms), c.premium_base, c.premium_per_day, c.immediate_hours)
    return e_now >= adjusted(e_target, premium)


def relist_price(mine: Sequence[tuple[int, int]], book: Sequence[tuple[int, int]]) -> int | None:
    """The price to relist at when someone else lists at or below your price (PO11).

    Args:
        mine: (price each, units) of your listings.
        book: (price each, units) listed now, yours included.

    Returns:
        One copper under the lowest price others list at, when it is at or below your lowest; else None.
    """
    if not mine:
        return None
    yours: dict[int, int] = {}
    for price, units in mine:
        yours[price] = yours.get(price, 0) + units
    others = sorted(price for price, units in book if units > yours.get(price, 0))
    lowest = min(price for price, _ in mine)
    return others[0] - 1 if others and others[0] <= lowest else None


def sell_now_text(m: MarketNow) -> str:
    """The Ready to sell action, naming a price spike (SPEC §3.4D.9 mockup row).

    Args:
        m: The market.

    Returns:
        "sell now: price spike +38%" when the posting-now price is above its median (whole percent, half up),
        else "sell now".
    """
    if m.post_price is None or not m.median or m.post_price <= m.median:
        return "sell now"
    pct = (200 * (m.post_price - m.median) + m.median) // (2 * m.median)
    return f"sell now: price spike +{pct}%"


def _windows(o: Outlook) -> list[SellStep]:
    """The plan's posts at a selling window, by time.

    Args:
        o: The outlook.

    Returns:
        Its window posts.
    """
    return sorted((s for s in o.steps if s.kind == "post" and s.window_start_ms is not None), key=lambda s: s.at_ms)


def _ready(p: Position, o: Outlook, c: Clock) -> bool:
    """Whether a Holding position is Ready to sell.

    Args:
        p: The position.
        o: The plan and the market.
        c: The clock.

    Returns:
        True when the plan posts its units now and at no window, or when ready_to_sell holds for its first window.
    """
    if p.state != HOLDING or o.market is None or o.market.sell_value is None:
        return False
    windows = _windows(o)
    if not windows:
        return any(s.kind == "post" for s in o.steps)
    e_target = sum(s.expected for s in windows) * o.share
    return ready_to_sell(o.market.sell_value * p.held, e_target, windows[0].window_start_ms, windows[0].window_end_ms, c)


def _process_text(o: Outlook, c: Clock) -> str:
    """The Processing action: "smelt today · post Thu 11:00 for the 19:00 peak".

    Args:
        o: The outlook (verb, when, the product's post).
        c: The clock.

    Returns:
        The text; the day is "today" when unplanned, past or later today.
    """
    later = o.process_ms is not None and o.process_ms > c.now_ms and not when(o.process_ms, c).startswith("today")
    day = clock(o.process_ms, c.offset_ms)[0] if later else "today"
    return f"{o.verb} {day}" + (f" · {step_text(o.then, c)}" if o.then is not None else "")


def _plan_text(p: Position, o: Outlook, c: Clock) -> str:
    """The plan's action for the units, flagged when it no longer covers what is still committed (PO3, T7).

    Args:
        p: The position.
        o: The outlook.
        c: The clock.

    Returns:
        step_text of the first action, plus " · below what you paid" when Σ expected × share < cost left.
    """
    steps = sorted(o.steps, key=lambda s: (s.at_ms, s.kind))
    text = step_text(steps[0] if steps else None, c)
    below = bool(steps) and sum(s.expected for s in steps) * o.share < p.cost_left
    return text + (" · below what you paid" if below else "")


def _next(p: Position, o: Outlook, c: Clock, ready: bool) -> str:
    """A position's next action (SPEC §3.4D.9).

    Args:
        p: The position.
        o: The outlook.
        c: The clock.
        ready: Whether it is Ready to sell.

    Returns:
        The facts' own action first; then undercut, processing, sell now, or the plan's action.
    """
    if p.state == ACQUIRING and p.acquiring_until is not None:
        return f"{p.next_action} by {when(p.acquiring_until, c)}"
    if p.next_action:
        return p.next_action
    if p.listed and not p.held:
        relist = relist_price(o.mine, o.market.book if o.market else ())
        return "listed · cheapest" if relist is None else f"undercut: relist at {money(relist)}"
    if p.state == PROCESSING:
        return _process_text(o, c)
    return sell_now_text(o.market) if ready else _plan_text(p, o, c)


def evaluate(p: Position, o: Outlook, c: Clock) -> PositionView:
    """A position as section A and the addon show it (SPEC §3.4D.9).

    Args:
        p: The position (engine.positions.track).
        o: What the plan and the market say about it.
        c: The clock and the premium settings.

    Returns:
        Its PositionView: Ready to sell replaces Holding when selling now beats waiting (PO5); est. net now is the
        plan's actions' net for its units, else its expected net at open for the units still yours, unknown while
        it needs reconciliation; the window is the plan's first selling window, else the original target.
    """
    ready = _ready(p, o, c)
    windows = _windows(o)
    if p.state == RECONCILE:
        net = None
    elif o.steps:
        net = sum(s.net for s in o.steps) * o.share
    else:
        net = p.expected_net_open * (p.units_left + p.unpaid) / p.units if p.units else 0.0
    window = windows[0].window_start_ms if windows else p.target_window_ms
    if p.state == PROCESSING and o.then is not None and o.then.window_start_ms is not None:
        window = o.then.window_start_ms
    return PositionView(p.position_id, p.char_id, p.key, READY if ready else p.state, p.units, p.units_left,
                        p.cost_total, p.cost_left, net, window, _next(p, o, c, ready))


def found_rows(holdings: Sequence[tuple[str, int, int]], paid: Mapping[int, int],
               steps: Mapping[tuple[str, int], Sequence[SellStep]], covered: set[tuple[str, int]],
               c: Clock) -> list[PositionView]:
    """Found stock the plan holds for a later window: Holding rows with Paid 0, never stored (D.9, PF22).

    Args:
        holdings: (character, key, units) held, unbound.
        paid: Item key -> copper you ever paid for it (the cost basis); found items have none.
        steps: (character, key) -> the plan's actions (a chain's later steps are left out: not these units).
        covered: (character, key) pairs a position already shows.
        c: The clock.

    Returns:
        One row per found (character, item) whose plan holds it or posts it at a window after now, by character
        then key; its est. net is the plan's, its window the first post's.
    """
    out = []
    for char_id, key, units in sorted(holdings):
        mine = sorted((s for s in steps.get((char_id, key), ()) if s.own), key=lambda s: (s.at_ms, s.kind))
        later = [s for s in mine if s.kind == "hold" or (s.kind == "post" and (s.window_start_ms or 0) > c.now_ms)]
        if (char_id, key) in covered or paid.get(key, 0) > 0 or not later or units <= 0:
            continue
        posts = [s.window_start_ms for s in mine if s.window_start_ms is not None]
        out.append(PositionView(f"found:{char_id}:{key}", char_id, key, HOLDING, units, units, 0, 0,
                                sum(s.net for s in mine), posts[0] if posts else None, step_text(mine[0], c), True))
    return out


def history_judged(p: Position) -> Judged:
    """A position's History cells (SPEC §3.4.1 positions, PO9, PO10).

    Args:
        p: The position.

    Returns:
        Judged: a disenchant is not tracked ("mats sold separately"); a Completed position realized its proceeds less
        its cost and lost deposits ("completed", or "6 lost (untracked)" after the 31-day rule); an open one is open
        with its state and units left.
    """
    if p.strategy == "disenchant":
        return Judged(p.expected_net_open, None, "mats sold separately")
    if p.state == COMPLETED:
        return Judged(p.expected_net_open, p.realized, f"{p.lost} lost (untracked)" if p.lost else "completed")
    note = f"{p.state.lower()} · {p.units_left} of {p.units} left" + (f" · {p.lost} lost (untracked)" if p.lost else "")
    return Judged(p.expected_net_open, None, note, OPEN_POSITION)


def history_positions(positions: Sequence[Position], since_ms: int) -> list[Position]:
    """The positions History lists: open ones and those closed since a time (merged ones never).

    Args:
        positions: Positions.
        since_ms: Oldest close to include, UTC ms.

    Returns:
        Those positions, in the given order.
    """
    return [p for p in positions if not p.merged and (p.closed_at is None or p.closed_at >= since_ms)]
