"""Sell probability and expected value (SPEC §3.3.2, §3.3.3, §3.1.1 curves).

`simulate` (the queue simulation) and `build_curve` run only in AHSync's full refresh.
The rest is ported to addon/AHProfit/Engine/SellProb.lua and must stay identical:
`tier_of`, `blend`, `with_record`, `expected_post`, `curve_row`, `from_curve`,
`quantize`, `encode_curve`, `decode_curve`, `default_chance`.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.fees import AH_CUT
from ahsync.engine.volume import VolumePrior

HOUR_MS = 3_600_000
# Band midpoints in hours: a competitor is available during window hour k only while k < m
# (SPEC §3.3.2). Very long = (8 + longest duration 24) / 2. UNVERIFIED bands (§6 item 5).
BAND_MIDPOINT_H = {1: 0.25, 2: 1.25, 3: 5.0, 4: 16.0}
NEVER_EXPIRES = math.inf
TIER_LIMITS = (1.00, 1.05, 1.15)        # T1 ≤ 1.00 < T2 ≤ 1.05 < T3 ≤ 1.15 < T4 (SPEC §3.3.2)
TIER_FULL_WEIGHT_N = 30                 # tier history reaches its full weight at 30 auctions
TIER_MAX_WEIGHT = 0.5                   # history never counts for more than half
CURVE_AHEAD = (0, 1, 2, 5, 10, 20, 50, 100)      # units ahead for listed items (SPEC §3.1.1)
CURVE_AHEAD_TOOLTIP = (0, 5, 20)                 # units ahead for tooltip-only items
CURVE_ROWS = 24
CURVE_STEPS = 50                        # P quantized to 2% steps (SPEC §3.1.1, IU11)
CURVE_CHAR_BASE = 35                    # codes 35-85: no '"' and no '\\'
FLAT_HOW = (1.0,) * 168                 # a flat hourly sales profile


@dataclass(frozen=True)
class Competitor:
    """Units listed at or below your price, ahead of you in the queue."""

    price: int          # unit price, copper
    units: float
    band: int           # time-left code 1-4, or 0 for "no band known" (never expires in the window)


@dataclass(frozen=True)
class QueueInputs:
    """Everything the queue simulation needs (SPEC §3.3.2 inputs)."""

    sold_per_day: float               # S
    listed_per_day: float             # L
    undercut_share: float             # u(p)
    demand_profile: tuple[float, ...] # sales_how for each window hour, in order
    qty: int                          # q, your units
    ahead: tuple[Competitor, ...]     # competing auctions priced at or below yours


def _midpoint(band: int) -> float:
    """Hours a competitor stays available (SPEC §3.3.2).

    Args:
        band: Time-left code 1-4, or 0 when unknown.

    Returns:
        The band midpoint; infinity for 0 (an unknown band stays the whole window).
    """
    return BAND_MIDPOINT_H.get(band, NEVER_EXPIRES)


@dataclass(frozen=True)
class QueueResult:
    """Where the window's demand went in the queue simulation (for PI15 and the explanation)."""

    yours: float          # your units sold
    competitors: float    # units sold by the competitors ahead of you
    undercutters: float   # units sold by new undercutting listings
    demand: float         # total demand over the window


def simulate_detail(inputs: QueueInputs) -> QueueResult:
    """Queue simulation over the window's clock hours (SPEC §3.3.2), with who sold what.

    Each hour: new undercutters arrive (L × u ÷ 24 units, cheapest from then on),
    competitors past their band midpoint leave, and S ÷ 24 × sales_how buyers take
    the cheapest units: undercutters, then competitors by price (book order at equal
    price, standing in for "oldest first"), then you, up to your q units. The order
    never depends on the band, so a competitor expiring sooner can't reorder the queue (PI12).

    Args:
        inputs: Demand, undercut arrivals, your units and the competitors ahead of you.

    Returns:
        Units sold by you, by the competitors ahead and by undercutters, and the total demand.
    """
    order = sorted(range(len(inputs.ahead)), key=lambda i: (inputs.ahead[i].price, i))
    left = [inputs.ahead[i].units for i in order]
    midpoints = [_midpoint(inputs.ahead[i].band) for i in order]
    arrivals = inputs.listed_per_day * inputs.undercut_share / 24
    undercut_left = 0.0
    yours_left = float(inputs.qty)
    sold_undercut = sold_competitors = total_demand = 0.0
    for k, weight in enumerate(inputs.demand_profile):
        undercut_left += arrivals
        demand = inputs.sold_per_day / 24 * weight
        total_demand += demand
        taken = min(demand, undercut_left)
        undercut_left -= taken
        sold_undercut += taken
        demand -= taken
        for i in range(len(left)):
            if k < midpoints[i] and demand > 0:
                taken = min(demand, left[i])
                left[i] -= taken
                sold_competitors += taken
                demand -= taken
        yours_left -= min(demand, yours_left)
    return QueueResult(inputs.qty - yours_left, sold_competitors, sold_undercut, total_demand)


def simulate(inputs: QueueInputs) -> float:
    """Queue simulation: the share of your units that sell within the window (SPEC §3.3.2).

    Args:
        inputs: Demand, undercut arrivals, your units and the competitors ahead of you.

    Returns:
        P_queue in [0, 1]: your units sold ÷ q; 0 when q is 0.
    """
    if inputs.qty <= 0:
        return 0.0
    return simulate_detail(inputs).yours / inputs.qty


def ahead_of(book: Sequence[tuple[int, float, int]] | None, price: int,
             cur: int | None, cur_qty: int | None) -> tuple[Competitor, ...]:
    """Competitors ahead of a post at `price` (SPEC §3.3.2).

    Args:
        book: (unit price, units, band) of other sellers' auctions, or None when
            there is no usable book.
        price: Your unit price, copper.
        cur: Lowest buyout, used only without a book.
        cur_qty: Units at the lowest buyout, used only without a book.

    Returns:
        Auctions priced at or below yours (equal price counts as ahead); without a
        book, one never-expiring block of cur_qty units when price ≥ cur.
    """
    if book is not None:
        return tuple(Competitor(p, units, band) for p, units, band in book if p <= price)
    if cur is not None and cur_qty and price >= cur:
        return (Competitor(cur, float(cur_qty), 0),)
    return ()


def tier_of(price: int, cur_others: int | None) -> int:
    """Price tier of a post relative to the cheapest competitor (SPEC §3.3.2).

    Args:
        price: Your unit price, copper.
        cur_others: Cheapest competing buyout, or None when there is none.

    Returns:
        1 (cheapest) to 4; 1 when there is no competitor.
    """
    if not cur_others:
        return 1
    ratio = price / cur_others
    for tier, limit in enumerate(TIER_LIMITS, start=1):
        if ratio <= limit:
            return tier
    return 4


def blend(p_queue: float, tier_sell_through: float | None, tier_n: int) -> float:
    """Blend the simulation with the tier's history (SPEC §3.3.2 step 3).

    Args:
        p_queue: Queue simulation result.
        tier_sell_through: ST_tier, sold ÷ (sold + expired); None without history.
        tier_n: Auctions behind ST_tier.

    Returns:
        (1 − e) × P_queue + e × ST_tier with e = 0.5 × min(1, n ÷ 30); P_queue without history.
    """
    if tier_sell_through is None:
        return p_queue
    e = TIER_MAX_WEIGHT * min(1.0, tier_n / TIER_FULL_WEIGHT_N)
    return (1 - e) * p_queue + e * tier_sell_through


def with_record(p: float, sold: int, expired: int, weight: float = 4) -> float:
    """Blend P with your own sell record (SPEC §3.3.3): P' = (k·P + s) ÷ (k + s + x).

    Args:
        p: Market-model probability.
        sold: Your posts of the item that sold in the record window.
        expired: Your posts that expired.
        weight: own_record_weight k, the model's worth in posts.

    Returns:
        P'; equal to P with no record.
    """
    return (weight * p + sold) / (weight + sold + expired)


def expected_post(p: float, qty: int, price: int, fallback: float, deposit: int) -> float:
    """Expected net of posting q units at price p (SPEC §3.3.2).

    Args:
        p: Chance the units sell (P').
        qty: Units posted.
        price: Unit price, copper.
        fallback: Value per unsold unit (max of vendor and DE value), float copper.
        deposit: Deposit in copper; refunded on a sale, lost on expiry.

    Returns:
        P·q·p·(1 − cut) + (1 − P)·(q·fallback − deposit), float copper.
    """
    return p * qty * price * (1 - AH_CUT) + (1 - p) * (qty * fallback - deposit)


def market_facts(sold_per_day: float, expired_per_day: float, units_listed: int) -> tuple[float | None, float | None]:
    """Informational market facts (SPEC §3.3.2).

    Args:
        sold_per_day: S.
        expired_per_day: X.
        units_listed: N, units on the AH now.

    Returns:
        (sell-through S ÷ (S + X), days of supply N ÷ S); None where undefined.
    """
    through = sold_per_day / (sold_per_day + expired_per_day) if sold_per_day + expired_per_day > 0 else None
    supply = units_listed / sold_per_day if sold_per_day > 0 else None
    return through, supply


def undercut_share(new_listings: Sequence[tuple[float, int, int]], price: int, lowest_now: int) -> float | None:
    """u(p): share of newly listed units that would be at or below you (SPEC §3.3.2).

    Args:
        new_listings: (units, price, lowest price at the time) for listings in the last 7 days.
        price: Your unit price.
        lowest_now: The lowest price now.

    Returns:
        Units listed at or below your position relative to the lowest at their time
        ÷ all new units; None without history (callers use default_undercut_share).
    """
    total = sum(units for units, _, _ in new_listings)
    if total <= 0:
        return None
    position = price / lowest_now
    below = sum(units for units, p, lowest in new_listings if p / lowest <= position)
    return below / total


def window_hours(start_ms: int, end_ms: int) -> list[int]:
    """The whole clock hours of a sell window; the current hour counts in full (SPEC §3.3.2).

    Args:
        start_ms: Window start (now, or the post time).
        end_ms: Window end, exclusive.

    Returns:
        Hour numbers floor(ms / 3600000) from the start's hour up to the end.
    """
    return list(range(start_ms // HOUR_MS, -(-end_ms // HOUR_MS)))


def posting_now_window(now_ms: int, hours: int = 8) -> list[int]:
    """Window for posting now: the next sell_window_hours whole hours (SPEC §3.3.2).

    Args:
        now_ms: Now, UTC ms.
        hours: sell_window_hours.

    Returns:
        Hour numbers.
    """
    first = now_ms // HOUR_MS
    return list(range(first, first + hours))


def planned_window(post_ms: int, window_end_ms: int, now_ms: int) -> list[int]:
    """Window for a planned post: from the post time (or now, if passed) to the window's end (SPEC §3.3.2).

    Args:
        post_ms: Planned post time (post_lead_hours before the best hour).
        window_end_ms: End of the target selling window, exclusive.
        now_ms: Now, UTC ms.

    Returns:
        Hour numbers; empty when the window has ended (a missed peak, SPEC §3.4.0).
    """
    return window_hours(max(post_ms, now_ms), window_end_ms)


def demand_for(hours: Sequence[int], sales_how: Sequence[float]) -> tuple[float, ...]:
    """sales_how for each hour of a window (hour of week on the UTC date).

    Args:
        hours: Hour numbers.
        sales_how: 168 sales ratios.

    Returns:
        The ratio for each hour, in order.
    """
    return tuple(sales_how[(h // 24 + 3) % 7 * 24 + h % 24] for h in hours)


# ---------------------------------------------------------------- curves (SPEC §3.1.1)

def build_curve(sold_per_day: float, listed_per_day: float, undercut: float, sales_how: Sequence[float],
                refresh_ms: int, qty: int, ahead_points: Sequence[int] = CURVE_AHEAD,
                hours: int = 8) -> list[list[float]]:
    """The sell-probability grid: 24 rows (posting hours from the refresh hour) × units-ahead points.

    Args:
        sold_per_day: S.
        listed_per_day: L.
        undercut: u(p) at the current price tier.
        sales_how: 168 sales ratios.
        refresh_ms: Time of the full refresh; row 0 is its clock hour.
        qty: The reference stack posted.
        ahead_points: Units ahead for each column.
        hours: sell_window_hours.

    Returns:
        P_queue per row and column. SPEC GAP: the ahead units are modelled as one
        block that stays the whole window (conservative), since the curve can't
        know which of tomorrow's competitors expire when.
    """
    first_hour = refresh_ms // HOUR_MS
    rows_by_demand: dict[tuple[float, ...], list[float]] = {}     # equal demand → equal row (flat profiles)
    grid = []
    for row in range(CURVE_ROWS):
        demand = demand_for(range(first_hour + row, first_hour + row + hours), sales_how)
        if demand not in rows_by_demand:
            rows_by_demand[demand] = _block_row(sold_per_day, listed_per_day, undercut, demand, qty, ahead_points)
        grid.append(list(rows_by_demand[demand]))
    return grid


def _block_row(sold_per_day: float, listed_per_day: float, undercut: float, demand: Sequence[float], qty: int,
               ahead_points: Sequence[int]) -> list[float]:
    """One curve row: `simulate` for each units-ahead point, in a single pass over the window's hours.

    Each point is one block of competitors that never expires (band 0), as in
    build_curve. The arithmetic is simulate_detail's, step for step, so every value is
    bit-identical to simulate(QueueInputs(...)) (checked by an invariant test); it only
    avoids building inputs and results per point, which dominated the full refresh.

    Args:
        sold_per_day: S.
        listed_per_day: L.
        undercut: u(p).
        demand: sales_how for each window hour.
        qty: Your units.
        ahead_points: Units ahead for each point.

    Returns:
        P_queue per point; 0 for every point when qty is 0.
    """
    if qty <= 0:
        return [0.0] * len(ahead_points)
    arrivals = listed_per_day * undercut / 24
    left = [float(a) for a in ahead_points]
    yours_left = [float(qty)] * len(ahead_points)
    undercut_left = 0.0
    for weight in demand:
        undercut_left += arrivals
        hour_demand = sold_per_day / 24 * weight
        taken = min(hour_demand, undercut_left)
        undercut_left -= taken
        hour_demand -= taken
        for j in range(len(left)):
            rest = hour_demand
            if left[j] and rest > 0:          # a point with 0 ahead has no competitor at all
                taken = min(rest, left[j])
                left[j] -= taken
                rest -= taken
            yours_left[j] -= min(rest, yours_left[j])
    return [(qty - y) / qty for y in yours_left]


def curve_row(refresh_ms: int, now_ms: int) -> int:
    """Which curve row applies now (SPEC §3.1.1).

    Args:
        refresh_ms: Time of the refresh the curve came from.
        now_ms: Now.

    Returns:
        (current hour − refresh hour) mod 24: after 24 h the same clock hour is reused.
    """
    return (now_ms // HOUR_MS - refresh_ms // HOUR_MS) % CURVE_ROWS


def from_curve(row: Sequence[float], ahead: float, ahead_points: Sequence[int] = CURVE_AHEAD) -> float:
    """P for a number of units ahead, interpolating linearly between curve points (SPEC §3.1.1).

    Args:
        row: One curve row, one P per ahead point.
        ahead: Units ahead now.
        ahead_points: The row's units-ahead points, ascending.

    Returns:
        The interpolated P; clamped to the first and last points outside their range.
    """
    if ahead <= ahead_points[0]:
        return row[0]
    for i in range(1, len(ahead_points)):
        if ahead <= ahead_points[i]:
            lo, hi = ahead_points[i - 1], ahead_points[i]
            return row[i - 1] + (row[i] - row[i - 1]) * ((ahead - lo) / (hi - lo))
    return row[-1]


def quantize(p: float) -> int:
    """A probability in 2% steps, as exported (SPEC §3.1.1).

    Args:
        p: Probability in [0, 1].

    Returns:
        floor(p × 50 + 0.5), 0-50.
    """
    return math.floor(p * CURVE_STEPS + 0.5)


def encode_curve(grid: Sequence[Sequence[float]]) -> str:
    """Encode a curve one character per point, row by row (SPEC §3.1.1).

    Args:
        grid: Rows of probabilities.

    Returns:
        One character chr(35 + quantize(P)) per point.
    """
    return "".join(chr(CURVE_CHAR_BASE + quantize(p)) for row in grid for p in row)


def decode_curve(text: str, points_per_row: int) -> list[list[float]]:
    """Decode an encoded curve back into rows of 2%-step probabilities.

    Args:
        text: From encode_curve.
        points_per_row: Units-ahead points per row (8 or 3).

    Returns:
        Rows of n ÷ 50.
    """
    values = [(ord(ch) - CURVE_CHAR_BASE) / CURVE_STEPS for ch in text]
    return [values[i:i + points_per_row] for i in range(0, len(values), points_per_row)]


def lookup(curve: Sequence[Sequence[float]], refresh_ms: int, now_ms: int, ahead: float,
           ahead_points: Sequence[int] = CURVE_AHEAD) -> float:
    """P from a curve for now and the units ahead: the row for the current hour, interpolated (SPEC §3.1.1).

    Args:
        curve: 24 rows from build_curve (or decode_curve), or the one row of a tooltip-only item (EX19).
        refresh_ms: Time of the refresh the curve came from.
        now_ms: Now.
        ahead: Units ahead now.
        ahead_points: The curve's units-ahead points.

    Returns:
        P_queue for posting now: the row curve_row(...) mod the number of rows, so a one-row curve is used at any
        hour.
    """
    return from_curve(curve[curve_row(refresh_ms, now_ms) % len(curve)], ahead, ahead_points)


def default_chance(price: int | None, cur_others: int | None, default_sell_prob: float = 0.25,
                   default_sell_prob_cheapest: float = 0.5) -> float:
    """P when sales volume is unknown, by where the post would stand (SPEC §3.3.2 fallbacks, PD1).

    Args:
        price: Your unit price, copper; None when unknown.
        cur_others: The cheapest competing buyout, copper; None when nobody else lists it.
        default_sell_prob: P when someone else is cheaper (config default_sell_prob).
        default_sell_prob_cheapest: P when you would be the cheapest seller (config default_sell_prob_cheapest).

    Returns:
        default_sell_prob_cheapest when there is no competitor or price ≤ cur_others; else default_sell_prob.
    """
    if cur_others is None or (price is not None and price <= cur_others):
        return default_sell_prob_cheapest
    return default_sell_prob


def cheapest_other(book: Sequence[tuple[int, int, int, int]] | None, cur: int | None) -> int | None:
    """The cheapest competing price a post faces: the book's lowest price, else the lowest buyout.

    Args:
        book: (price, stack, band, count) rows of other sellers, or None without a book.
        cur: Lowest buyout, used without a book.

    Returns:
        Copper; None when nothing else is listed.
    """
    if book:
        return min(price for price, _, _, _ in book)
    return cur if book is None else None


def chance(queue: QueueInputs | None, tier_sell_through: float | None, tier_n: int, sold: int, expired: int,
           default_sell_prob: float = 0.25, record_weight: float = 4) -> tuple[float, bool]:
    """P' for one post: simulation, tier blend and own record, or the default without volume (SPEC §3.3.2-3).

    Args:
        queue: Simulation inputs, or None when sold/day is unknown.
        tier_sell_through: ST_tier, or None.
        tier_n: Auctions behind ST_tier.
        sold: Your sold posts of the item in the record window.
        expired: Your expired posts.
        default_sell_prob: P when there is no volume data (default_chance, from the post's position).
        record_weight: own_record_weight.

    Returns:
        (P', volume_unknown). With no volume, P is default_sell_prob before the record blend.
    """
    if queue is None:
        return with_record(default_sell_prob, sold, expired, record_weight), True
    p = blend(simulate(queue), tier_sell_through, tier_n)
    return with_record(p, sold, expired, record_weight), False


# ---------------------------------------------------------------- posting now, from market data (SPEC §3.3.2)

@dataclass(frozen=True)
class MarketSettings:
    """The config values market-wide analysis needs (probability, patterns), passed in (no config file in the engine)."""

    default_sell_prob: float = 0.25
    own_record_weight: float = 4
    default_undercut_share: float = 0.5
    sell_window_hours: int = 8
    pattern_half_life_weeks: float = 4      # recency half-life of the weekly patterns (SPEC §3.3.4)
    window_threshold: float = 0.90          # share of the best slot a window slot needs (SPEC §3.3.4)
    default_sell_prob_cheapest: float = 0.5 # P without volume when you'd be the cheapest seller (SPEC §3.3.2, PD1)
    volume_prior: VolumePrior = VolumePrior()   # the sales-rate prior (SPEC §3.3.1, V41)
    max_supply_share: float = 0.5           # a window's liquidity cap: this share of others' listed units (W15)
    max_supply_share_gear: float = 1.0      # the same for weapons and armor
    min_gear_units: int = 2                 # gear always gets this many per window
    max_trend_total: float = 0.15           # the most the trend may move a window's forecast (TT7)
    top_buy_windows: int = 3                # buying windows kept per item, by actionable value (W14)
    top_sell_windows: int = 3               # selling windows kept per item (W15)


@dataclass(frozen=True)
class Supply:
    """What a post competes with: market volume and the competing listings."""

    sold_per_day: float | None                           # S; None when unknown
    listed_per_day: float | None                         # L
    book: tuple[tuple[int, int, int, int], ...] | None   # (price, stack, band, count); None without a book
    cur_price: int | None                                # lowest buyout, used without a book
    cur_qty: int | None


def posting_chance(supply: Supply, price: int | None, qty: int, now_ms: int,
                   settings: MarketSettings, sales_how: Sequence[float] | None = None) -> tuple[float, bool]:
    """P for posting qty units now at price, from market data only (no own record, SPEC §3.3.2).

    Args:
        supply: Volume and competition.
        price: Post price in copper, or None.
        qty: Units posted.
        now_ms: Now, UTC ms.
        settings: Defaults and the posting-now window.
        sales_how: The item's 168-slot hourly sales profile, the window's demand (P26); None for flat.

    Returns:
        (P, volume_unknown); without sales or a price, the default chance for the post's position (default_chance).
    """
    if supply.sold_per_day is None or price is None:
        p = default_chance(price, cheapest_other(supply.book, supply.cur_price), settings.default_sell_prob,
                           settings.default_sell_prob_cheapest)
        return chance(None, None, 0, 0, 0, p, settings.own_record_weight)
    competitors = None if supply.book is None else [(p, float(stack * count), band)
                                                    for p, stack, band, count in supply.book]
    ahead = ahead_of(competitors, price, supply.cur_price, supply.cur_qty)
    demand = demand_for(posting_now_window(now_ms, settings.sell_window_hours), sales_how or FLAT_HOW)
    queue = QueueInputs(supply.sold_per_day, supply.listed_per_day or 0.0, settings.default_undercut_share,
                        demand, qty, ahead)
    return chance(queue, None, 0, 0, 0, settings.default_sell_prob, settings.own_record_weight)
