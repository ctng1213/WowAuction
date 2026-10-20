"""Weekly patterns: weighted medians, sell scores, best selling and buying windows (SPEC §3.3.4, §3.3.6).

The week's 168 hours are repeating slots, slot = weekday × 24 + hour with
0 = Monday 00:00 UTC (SPEC §3.3 weekday rule). Windows may cross midnight and
the Sunday → Monday boundary. Each window can carry its forecast (W15): the expected
price there, the units the market can take there and the confidence; buying windows
rank by the actionable value those give (W14).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from ahsync.engine import confidence
from ahsync.engine.capacity import liquidity_cap, slot_of, window_supply
from ahsync.engine.fees import AH_CUT
from ahsync.engine.plan import slot_price

SLOTS = 168
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TOP_WINDOWS = 3
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


@dataclass(frozen=True)
class Forecast:
    """What a window is expected to give next time it comes round (SPEC §3.3.6, W15)."""

    price: int               # expected price in the window, copper: level × how × trend factor, clamped (§3.3.4)
    exec_qty: int            # units the market can take there: the window's liquidity cap (§3.4 Liquidity)
    conf: int                # confidence %, the window's weeks as the pattern component (§3.3.5)


@dataclass(frozen=True)
class Window:
    """One best selling or buying window (SPEC §3.3.4, §3.3.6 buying windows)."""

    start_slot: int          # first slot, 0 = Mon 00:00 UTC
    hours: int               # length in slots
    score_sum: float         # selling: sum of sell_score over its slots (the ranking key); buying: sum of how
    how_avg: float           # average price ratio (how) over its slots; buying ranks by it, lowest first
    weeks: int               # weeks of data behind it: the fewest of any of its slots
    best_slot: int           # selling: highest sell_score; buying: lowest how (ties: earliest in the window)
    forecast: Forecast | None = None   # its forecast (W15); None when the item has no price level

    @property
    def strength(self) -> str:
        """Pattern strength from weeks of data (SPEC §3.3.4).

        Returns:
            "early pattern" (< 3 weeks), "pattern" (3-5) or "established" (≥ 6).
        """
        if self.weeks < 3:
            return "early pattern"
        return "pattern" if self.weeks < 6 else "established"


def weighted_median(observations: Sequence[tuple[float, float]]) -> float | None:
    """Deterministic weighted median (SPEC §3.3.4).

    Args:
        observations: (value, weight) pairs; weights > 0.

    Returns:
        After sorting by value, the first value where the cumulative weight reaches
        half the total; None when there are no observations.
    """
    if not observations:
        return None
    ordered = sorted(observations)
    half = sum(weight for _, weight in ordered) / 2
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= half:
            return value
    raise AssertionError("unreachable: the cumulative weight ends at the total")  # pragma: no cover


def recency_weight(age_weeks: int, half_life_weeks: float) -> float:
    """Weight of one week's observation (SPEC §3.3.4 recency weighting).

    Args:
        age_weeks: Whole weeks between the observation and now (0 = this week).
        half_life_weeks: pattern_half_life_weeks from config (4).

    Returns:
        0.5 ^ (age_weeks ÷ half_life_weeks).
    """
    return 0.5 ** (age_weeks / half_life_weeks)


def sell_scores(how: Sequence[float], sales_how: Sequence[float]) -> list[float]:
    """How good each hour of the week is for selling (SPEC §3.3 sell_score).

    Args:
        how: 168 price ratios.
        sales_how: 168 sales ratios (mean 1.0).

    Returns:
        how[slot] × sales_how[slot] ÷ max(sales_how) for each slot.
    """
    busiest = max(sales_how)
    return [how[s] * sales_how[s] / busiest for s in range(SLOTS)]


def _runs(qualifying: list[bool]) -> list[list[int]]:
    """Group qualifying slots into circular runs of adjacent slots.

    Args:
        qualifying: 168 flags; at least one False.

    Returns:
        Each run's slots in time order; a run may wrap from slot 167 to slot 0.
    """
    # Begin just after a gap: the walk then ends on that gap, so every run is closed by one.
    start = qualifying.index(False) + 1
    runs: list[list[int]] = []
    current: list[int] = []
    for i in range(SLOTS):
        slot = (start + i) % SLOTS
        if qualifying[slot]:
            current.append(slot)
        elif current:
            runs.append(current)
            current = []
    return runs


WindowForecaster = Callable[[Window], Forecast]       # a window -> its forecast (window_forecast with the item's facts)


def sell_windows(how: Sequence[float], sales_how: Sequence[float], weeks: Sequence[int],
                 threshold: float = 0.90, top: int = TOP_WINDOWS,
                 forecast: WindowForecaster | None = None) -> list[Window]:
    """The item's best selling windows (SPEC §3.3.4, W15).

    Args:
        how: 168 price ratios.
        sales_how: 168 sales ratios.
        weeks: 168 counts of weeks of data per slot.
        threshold: window_threshold: a slot with data qualifies at ≥ threshold × the best such slot's score.
        top: top_sell_windows: how many to keep (3).
        forecast: Gives each kept window its forecast (W15); None leaves them without one.

    Returns:
        Up to `top` windows ranked by score sum (ties: earlier start slot), only of slots with at least 1 week of
        data, so every window's weeks is at least 1 (W16). Empty when
        there is no data at all, or when every slot with data qualifies (a flat week has no
        best time; the plan then posts now).
    """
    if not any(weeks):
        return []
    scores = sell_scores(how, sales_how)
    cutoff = threshold * max(score for score, w in zip(scores, weeks) if w >= 1)
    # W16: a slot without data (its value only a weekday fallback) never joins a window.
    qualifying = [score >= cutoff and w >= 1 for score, w in zip(scores, weeks)]
    if all(q for q, w in zip(qualifying, weeks) if w >= 1):           # flat over every hour with data
        # SPEC GAP: a flat week has no best window; treated like "no pattern".
        return []
    windows = []
    for run in _runs(qualifying):
        best = max(run, key=lambda s: (scores[s], -run.index(s)))
        windows.append(Window(
            start_slot=run[0], hours=len(run), score_sum=sum(scores[s] for s in run),
            how_avg=sum(how[s] for s in run) / len(run), weeks=min(weeks[s] for s in run), best_slot=best,
        ))
    windows.sort(key=lambda w: (-w.score_sum, w.start_slot))
    return [w if forecast is None else replace(w, forecast=forecast(w)) for w in windows[:top]]


def buy_windows(how: Sequence[float], weeks: Sequence[int], threshold: float = 0.90, top: int = TOP_WINDOWS,
                forecast: WindowForecaster | None = None, sell_net: float | None = None) -> list[Window]:
    """The item's best buying windows: the mirror of selling windows, ranked by actionable value (SPEC §3.3.6, W14).

    Args:
        how: 168 price ratios.
        weeks: 168 counts of weeks of data per slot.
        threshold: window_threshold: a slot with data qualifies at a price ratio ≤ its cheapest ÷ threshold.
        top: top_buy_windows: how many to keep (3).
        forecast: Gives each window its forecast; None (the item has no price level) ranks by price ratio instead.
        sell_net: What a unit sells for after the AH cut, copper (sell_net_price); needed with forecast.

    Returns:
        Up to `top` windows of slots with data (W16). With a forecast: by buy_value, highest first (ties: the lower
        expected price, then the earlier start slot), windows worth 0 dropped, each carrying its forecast. Without
        one: by lowest average how (ties: earlier start slot). Empty when there is no data at all, or when every
        slot with data qualifies (a buy is then planned for now).
    """
    if not any(weeks):
        return []
    cutoff = min(ratio for ratio, w in zip(how, weeks) if w >= 1) / threshold
    qualifying = [ratio <= cutoff and w >= 1 for ratio, w in zip(how, weeks)]     # W16: data slots only
    if all(q for q, w in zip(qualifying, weeks) if w >= 1):           # flat over every hour with data
        return []
    windows = []
    for run in _runs(qualifying):
        best = min(run, key=lambda s: (how[s], run.index(s)))
        total = sum(how[s] for s in run)
        windows.append(Window(start_slot=run[0], hours=len(run), score_sum=total, how_avg=total / len(run),
                              weeks=min(weeks[s] for s in run), best_slot=best))
    if forecast is None or sell_net is None:
        windows.sort(key=lambda w: (w.how_avg, w.start_slot))
        return windows[:top]
    valued = []
    for w in windows:
        f = forecast(w)
        value = buy_value(sell_net, f)
        if value > 0:                                       # a window worth 0 is dropped (W14)
            valued.append((-value, f.price, w.start_slot, replace(w, forecast=f)))
    valued.sort(key=lambda v: v[:3])
    return [v[3] for v in valued[:top]]


def buy_value(sell_net: float, f: Forecast) -> float:
    """A buying window's actionable value (SPEC §3.3.6, W14): the margin it can really earn.

    Args:
        sell_net: What a unit sells for after the AH cut, copper.
        f: The buying window's forecast.

    Returns:
        max(0, sell_net − f.price) × f.exec_qty × f.conf ÷ 100, float copper.
    """
    return max(0.0, sell_net - f.price) * f.exec_qty * f.conf / 100


def sell_net_price(sell: Sequence[Window], level: int) -> float:
    """What a unit bought now sells for after the AH cut: at the top selling window, else at the level (W14).

    Args:
        sell: The item's selling windows, best first, with their forecasts.
        level: The price level, copper.

    Returns:
        s × (1 − AH cut), float copper, s being the top selling window's expected price, or the level when the
        item has no selling window (or it has no forecast).
    """
    top = sell[0].forecast if sell else None
    return (top.price if top is not None else level) * (1 - AH_CUT)


@dataclass(frozen=True)
class ForecastFacts:
    """What forecasting one item's windows needs (SPEC §3.3.4 forecast, §3.4 Liquidity). Money copper, times UTC ms."""

    now_ms: int
    level: int                         # the price level: med7, else med30
    trend7: float                      # % per day
    p10: int | None                    # 30-day 10th percentile: the forecast's floor
    med7: int | None                   # with p90, the forecast's ceiling
    p90: int | None
    closes_30d: int | None             # hourly closes in 30 days: thin data caps at the level (TT23)
    supply: int | None                 # others' listed units now (else the typical daily supply); None unknown
    supply_how: Sequence[float]        # 168 listed-supply ratios; () without a pattern
    stack: int                         # the fewest units a window allows: the reference stack (gear: its minimum)
    share: float                       # max_supply_share (gear: max_supply_share_gear)
    volume_score: float                # the volume confidence component, 0-1
    book_age_ms: int | None            # age of the order book used; None without one
    max_trend_total: float             # config max_trend_total


def window_forecast(w: Window, f: ForecastFacts) -> Forecast:
    """A window's forecast for the next time it comes round (SPEC §3.3.6, W15).

    Args:
        w: The window.
        f: The item's facts.

    Returns:
        Forecast: price = plan.slot_price of the level × the window's average how at its next best hour (trend
        factor and clamps as §3.3.4); exec_qty = the liquidity cap of others' listed units over its next
        occurrence (capacity.window_supply; the stack when supply is unknown); conf = §3.3.5 with the window's
        weeks as the pattern component.
    """
    now_h = f.now_ms // HOUR_MS
    start_h = now_h + (w.start_slot - slot_of(now_h)) % SLOTS
    best_h = now_h + (w.best_slot - slot_of(now_h)) % SLOTS
    price = slot_price(f.level, w.how_avg, f.trend7, (best_h * HOUR_MS - f.now_ms) / DAY_MS, f.max_trend_total,
                       f.p10, f.med7, f.p90, f.closes_30d)
    listed = window_supply(f.supply, f.supply_how, now_h, range(start_h, start_h + w.hours))
    conf = confidence.percent(confidence.score(f.closes_30d or 0, f.volume_score, f.book_age_ms, w.weeks, 0))
    return Forecast(price, liquidity_cap(listed, f.share, f.stack), conf)


def slot_label(slot: int) -> str:
    """Display a slot as weekday and hour, e.g. "Tue 18:00" (UTC slot; views convert to local).

    Args:
        slot: 0-167.

    Returns:
        "<Ddd> HH:00".
    """
    return f"{WEEKDAYS[slot // 24 % 7]} {slot % 24:02d}:00"


def window_label(window: Window) -> str:
    """Display a window as start–end, e.g. "Tue 18:00–21:00" or "Sun 23:00–Mon 02:00".

    Args:
        window: A Window.

    Returns:
        The label; the end weekday is shown only when it differs from the start's.
    """
    end = (window.start_slot + window.hours) % SLOTS
    start_label, end_label = slot_label(window.start_slot), slot_label(end)
    if start_label[:3] == end_label[:3]:
        end_label = end_label[4:]
    return f"{start_label}–{end_label}"
