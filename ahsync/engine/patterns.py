"""Weekly patterns: weighted medians, sell scores, best selling and buying windows (SPEC §3.3.4, §3.3.6).

The week's 168 hours are repeating slots, slot = weekday × 24 + hour with
0 = Monday 00:00 UTC (SPEC §3.3 weekday rule). Windows may cross midnight and
the Sunday → Monday boundary.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

SLOTS = 168
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TOP_WINDOWS = 3


@dataclass(frozen=True)
class Window:
    """One best selling or buying window (SPEC §3.3.4, §3.3.6 buying windows)."""

    start_slot: int          # first slot, 0 = Mon 00:00 UTC
    hours: int               # length in slots
    score_sum: float         # selling: sum of sell_score over its slots (the ranking key); buying: sum of how
    how_avg: float           # average price ratio (how) over its slots; buying ranks by it, lowest first
    weeks: int               # weeks of data behind it: the fewest of any of its slots
    best_slot: int           # selling: highest sell_score; buying: lowest how (ties: earliest in the window)

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


def sell_windows(how: Sequence[float], sales_how: Sequence[float], weeks: Sequence[int],
                 threshold: float = 0.90) -> list[Window]:
    """The item's best selling windows (SPEC §3.3.4).

    Args:
        how: 168 price ratios.
        sales_how: 168 sales ratios.
        weeks: 168 counts of weeks of data per slot.
        threshold: window_threshold: a slot with data qualifies at ≥ threshold × the best such slot's score.

    Returns:
        Up to 3 windows ranked by score sum (ties: earlier start slot), only of slots with at least 1 week of
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
    return windows[:TOP_WINDOWS]


def buy_windows(how: Sequence[float], weeks: Sequence[int], threshold: float = 0.90) -> list[Window]:
    """The item's best buying windows: the mirror of selling windows (SPEC §3.3.6 buying windows).

    Args:
        how: 168 price ratios.
        weeks: 168 counts of weeks of data per slot.
        threshold: window_threshold: a slot with data qualifies at a price ratio ≤ its cheapest ÷ threshold.

    Returns:
        Up to 3 windows ranked by lowest average how (ties: earlier start slot), only of slots with data (W16). Empty
        when there is no data at all, or when every slot with data qualifies (a buy is then planned for now).
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
    windows.sort(key=lambda w: (w.how_avg, w.start_slot))
    return windows[:TOP_WINDOWS]


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
