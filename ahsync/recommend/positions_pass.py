"""Positions kept current (SPEC §3.4D.9): opened and replayed after every SavedVariables read, evaluated at every
plan run and hourly.

- `track_positions` (inside the tracking pass, after matching and the cost basis): every matched confirmed purchase
  or craft of a funded opportunity becomes a lot (a new position, or the open position of its chain and item), then
  every position is replayed from its lots and events (engine.positions.track) and its facts stored.
- `current` replays without writing, for the plan run: committed capital is the cost left of the open positions it
  returns (§3.4D.1), the positions table's lots and the journal being its only sources.
- `views` evaluates them against the plan and the market (engine.position_views): section A's rows and
  PortfolioPlan.lua's `positions`, found stock included. `store_views` keeps each position's latest evaluation.
- `reevaluate` is the hourly status pass's part: the stored plan actions and the published intelligence, no history
  read, and the published snapshot's positions are swapped for the new evaluation so the next PortfolioPlan.lua
  carries it.
engine.positions and engine.position_views decide; this module only gathers and stores.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ahsync.config import Config, portfolio_settings
from ahsync.db.sqlite import Database
from ahsync.engine.plan import PlanAction
from ahsync.engine.position_views import Clock, MarketNow, Outlook, PositionView, SellStep, evaluate, found_rows
from ahsync.engine.positions import COMPLETED, PROCESSING, Position, place, track
from ahsync.engine.portfolio import PortfolioSettings
from ahsync.intel.contract import ItemIntel
from ahsync.state.reads import StateReader

STEP_KINDS = ("post", "hold", "de", "vendor", "craft")


def current(db: Database, now_ms: int | None) -> list[Position]:
    """Every stored position as its lots and the journal leave it, without writing (SPEC §3.4D.9).

    Args:
        db: Database.
        now_ms: Now, UTC ms; None skips the time rules.

    Returns:
        engine.positions.track's records, oldest first; [] without positions.
    """
    seeds = db.position_seeds()
    if not seeds:
        return []
    return track(seeds, db.position_moves(min(s.opened_at for s in seeds)), db.position_world(), now_ms)


def track_positions(db: Database, now_ms: int | None = None) -> dict[str, int]:
    """Open positions from newly matched events, then replay and store every position (SPEC §3.4D.9, PO2-PO13).

    Args:
        db: Database (inside the tracking pass's transaction, after matching and the cost basis).
        now_ms: Now, UTC ms; None skips the time rules.

    Returns:
        {"opened": new positions, "lots": new lots, "open": positions not Completed}.

    Side effects:
        Inserts positions and position_lots (upserts by event id, PO8) and updates every position's facts.
    """
    markets = {c: m for c, m in db.conn.execute("SELECT id, market FROM characters")}
    opened = lots = 0
    for p in place(db.position_openings(), db.existing_positions()):
        if p.new:
            db.add_position(p.opening, markets.get(p.opening.char_id))
            opened += 1
        lots += db.add_lot(p.position_id, p.opening)
    positions = current(db, now_ms)
    db.write_positions(positions)
    return {"opened": opened, "lots": lots, "open": sum(1 for p in positions if p.state != COMPLETED)}


def plan_steps(actions: Sequence[PlanAction]) -> list[SellStep]:
    """The plan's actions for units you hold, as engine.position_views reads them.

    Args:
        actions: The run's PlanActions.

    Returns:
        One SellStep per post, hold, disenchant, vendor sale and craft, in plan order; a chain's step (a flip's or
        craft's post, for units still to buy or make) is not `own`.
    """
    return [SellStep(a.kind, a.char_id, a.key, a.qty, a.at_ms, a.window_start_ms, a.window_end_ms, a.peak_ms,
                     a.expected, a.net, a.chain is None) for a in actions if a.kind in STEP_KINDS]


def stored_steps(db: Database, char_ids: list[str]) -> list[SellStep]:
    """The published plan's open actions for units you hold, for the hourly status pass.

    The store keeps an action's expected net only; its expected value is that net plus what the units cost you (the
    account-wide cost basis), the way the sell plan made it.

    Args:
        db: Database.
        char_ids: Character ids.

    Returns:
        One SellStep per pending or late post, hold, disenchant, vendor sale and craft, by time then id; a chain's
        later step is `own` only once its chain's first action (the buy or craft that makes its units) is done or
        partial, the follow-up it keeps (SPEC §3.4.1).
    """
    if not char_ids:
        return []
    paid = {k: total / qty for k, (qty, total) in db.cost_basis().items() if qty > 0}
    marks = ",".join("?" * len(char_ids))
    rows = db.conn.execute(
        f"SELECT a.kind, a.char_id, a.key, a.qty, a.slot_ms, a.window_start_ms, a.window_end_ms, a.peak_ms, "
        f"a.expected_net, a.parent_id IS NULL OR p.status IN ('done', 'partial') FROM actions a "
        f"LEFT JOIN actions p ON p.action_id = a.parent_id "
        f"WHERE a.status IN ('pending', 'late') AND a.kind IN ({','.join('?' * len(STEP_KINDS))}) "
        f"AND a.char_id IN ({marks}) ORDER BY a.slot_ms, a.action_id", (*STEP_KINDS, *char_ids))
    return [SellStep(kind, c, key, qty, at, ws, we, peak, (net or 0.0) + (0.0 if kind == "craft" else paid.get(key, 0.0)
                     * qty), net or 0.0, bool(own)) for kind, c, key, qty, at, ws, we, peak, net, own in rows]


def market_now(i: ItemIntel | None) -> MarketNow | None:
    """An item's market as the evaluation reads it.

    Args:
        i: Its intelligence, or None.

    Returns:
        MarketNow (sell value, posting-now price, median med7 else med30, the book's units by price); None without.
    """
    if i is None:
        return None
    return MarketNow(i.sell_value, i.post_price, i.med7 or i.med30,
                     tuple((price, stack * count) for price, stack, _band, count in i.book))


def _outlook(p: Position, steps: Sequence[SellStep], held: Mapping[tuple[str, int], int], stats: Mapping[int, ItemIntel],
             mine: Mapping[tuple[str, int], list[tuple[int, int]]], uses: Mapping[int, list[tuple[int, str]]]) -> Outlook:
    """What the plan and the market say about one position.

    Args:
        p: The position.
        steps: The plan's actions.
        held: (character, key) -> units held (holdings).
        stats: Item key -> intelligence.
        mine: (character, key) -> your listings (price each, units).
        uses: Reagent key -> (product key, profession) of every known recipe that uses it.

    Returns:
        Its Outlook: its share of the actions for its units; while Processing the verb ("smelt" for Mining, else
        "craft"; "disenchant" for a disenchant), when the plan works on it and the product's first post.
    """
    own = [s for s in steps if (s.char_id, s.key) == (p.char_id, p.key) and s.kind != "craft" and s.own]
    share = p.held / max(held.get((p.char_id, p.key), 0), p.held, 1)
    if p.state != PROCESSING:
        return Outlook(tuple(own), share, market_now(stats.get(p.key)), tuple(mine.get((p.char_id, p.key), ())))
    products = {k for k, _ in uses.get(p.key, ())}
    verb = "disenchant" if p.strategy == "disenchant" else "smelt" if any(
        prof == "Mining" for _, prof in uses.get(p.key, ())) else "craft"
    work = [s.at_ms for s in steps if s.char_id == p.char_id and ((s.kind == "craft" and s.key in products)
                                                                   or (s.kind == "de" and s.key == p.key))]
    target = int(p.cand_id.split(":")[1]) if p.cand_id.count(":") else p.key
    then = [s for s in steps if s.key == target and s.kind == "post"]
    return Outlook(tuple(own), share, market_now(stats.get(p.key)), (), verb, min(work) if work else None,
                   min(then, key=lambda s: (s.at_ms, s.char_id)) if then else None)


def views(state: StateReader, positions: Sequence[Position], char_ids: list[str], steps: Sequence[SellStep],
          stats: Mapping[int, ItemIntel], c: Clock) -> list[PositionView]:
    """Section A's rows and PortfolioPlan.lua's `positions` for one market (SPEC §3.5, §3.4D.9).

    Args:
        state: Character state (holdings, auctions, cost basis, known recipes).
        positions: Every position (current()).
        char_ids: The market's characters.
        steps: The plan's actions for units held.
        stats: Item key -> intelligence (repriced by newer scans).
        c: The clock and the premium settings.

    Returns:
        The market's open positions evaluated, oldest first, then the found stock the plan holds for a later window.
    """
    mine_open = [p for p in positions if p.state != COMPLETED and p.char_id in char_ids]
    holdings = [(ch, key, qty) for ch, _place, key, qty, bound in state.holdings(char_ids) if not bound]
    held: dict[tuple[str, int], int] = {}
    for ch, key, qty in holdings:
        held[(ch, key)] = held.get((ch, key), 0) + qty
    mine: dict[tuple[str, int], list[tuple[int, int]]] = {}
    for ch, key, price, stack, _band, count, _dep, _end in state.own_auctions(char_ids):
        mine.setdefault((ch, key), []).append((price, stack * count))
    uses: dict[int, list[tuple[int, str]]] = {}
    for r in state.known_recipes(char_ids):
        for key, _n in r.reagents:
            uses.setdefault(key, []).append((r.product, r.profession))
    out = [evaluate(p, _outlook(p, steps, held, stats, mine, uses), c) for p in mine_open]
    by_unit: dict[tuple[str, int], list[SellStep]] = {}
    for s in steps:
        by_unit.setdefault((s.char_id, s.key), []).append(s)
    paid = {k: total for k, (_qty, total) in state.cost_basis().items()}
    covered = {(p.char_id, p.key) for p in mine_open}
    return out + found_rows([(ch, k, n) for (ch, k), n in sorted(held.items())], paid, by_unit, covered, c)


def clock_of(now_ms: int, offset_ms: int, ps: PortfolioSettings) -> Clock:
    """The evaluation's clock from the portfolio settings.

    Args:
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.
        ps: The portfolio settings (the premium).

    Returns:
        The Clock.
    """
    return Clock(now_ms, offset_ms, ps.premium_base, ps.premium_per_day, ps.immediate_hours)


def position_json(v: PositionView) -> list:
    """One position as the portfolio snapshot stores it (SPEC §3.5 `positions`, EX18).

    Args:
        v: The row.

    Returns:
        [position_id, character, key, state, units, units left, cost left, net now float copper or None,
        window ms or None, next action].
    """
    return [v.position_id, v.char_id, v.key, v.state, v.units, v.units_left, v.cost_left, v.net_now, v.window_ms,
            v.next_action]


def reevaluate(db: Database, cfg: Config, now_ms: int, offset_ms: int = 0) -> int:
    """The hourly status pass's part: re-evaluate every market's positions from the published plan and intelligence
    (SPEC §3.1.1, §3.4D.9 adaptive exit); no history read.

    Args:
        db: Database.
        cfg: Config (markets, the premium settings).
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        Positions evaluated.

    Side effects:
        Updates each position's state, est. net now, latest window and next action, and the published snapshot's
        positions (so the next PortfolioPlan.lua carries them).
    """
    positions = current(db, now_ms)
    c = clock_of(now_ms, offset_ms, portfolio_settings(cfg))
    done = 0
    for market in cfg.markets:
        char_ids = [ch for ch, _ in db.characters_in(market)]
        intel = db.read_intel(market)
        rows = views(db, positions, char_ids, stored_steps(db, char_ids), intel.items if intel else {}, c)
        with db.transaction():
            db.write_position_views(rows)
            db.replace_snapshot_positions(market, [position_json(v) for v in rows])
        done += sum(1 for v in rows if not v.found)
    return done
