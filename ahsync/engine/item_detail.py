"""The item detail: when an item sells best, and why (SPEC §3.5 item detail, §3.3.4, §3.3.6).

Everything here is made from the item's intelligence: the hour-of-week profiles, its
selling windows and its market activity. Slots are UTC (0 = Mon 00:00); the caller
passes the local offset and every label, cell and window comes out in local time, so
the dashboard only draws (SPEC §8.5).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.market_lines import LineFacts, coverage_text, market_lines
from ahsync.engine.patterns import SLOTS, WEEKDAYS, Window
from ahsync.engine.profile import Profile
from ahsync.engine.views import market_activity, money, round_half_up

HOUR_MS = 3_600_000
LEVELS = 4                     # heatmap cells are level 0 (the item's lowest) to 4 (its highest)


@dataclass(frozen=True)
class DetailFacts:
    """What the item detail shows about one item, from its intelligence."""

    name: str
    quality: int
    med30: int | None                 # copper
    trend7: float                     # % per day
    sold_per_day: float | None
    source: str | None                # main volume source: "books", "mail", "ahf" or a provider's name
    days_covered: int                 # days of the last 7 with any volume source
    units_on_ah: int | None
    listed_per_day: float | None      # newly listed units per day (§3.3.1)
    conf: int | None                  # confidence %
    seasonality: int | None           # 0-100; None = too little evidence
    how: Profile
    sales: Profile
    supply: Profile
    sell_windows: tuple[Window, ...] = ()     # best first
    turnover_per_day: float | None = None   # observed listing turnover: info only, never sales (§3.3.6, V37)
    kind: str | None = None           # the main source's kind, "ids" | "groups" | "own"; it picks the label (D57)
    vendor_sell: int | None = None    # copper a merchant pays for one; None = unknown (BZ25)
    vendor_from_game: bool = False    # the price came from the addon's itemNames, not AHF or the cloud summary
    lines: LineFacts | None = None    # the market lines' facts (D105); None leaves them out
    unseen_per_day: float | None = None   # of sold_per_day, the units estimated sold before first sight (BZ45, D106)


def local_slot(slot: int, offset_ms: int) -> int:
    """The local hour of the week a UTC slot falls on.

    Args:
        slot: UTC slot, 0-167.
        offset_ms: Local time minus UTC, ms; whole hours are used (floor).

    Returns:
        Local slot, 0-167.
    """
    return (slot + offset_ms // HOUR_MS) % SLOTS


def hour_text(local: int) -> str:
    """A local slot as weekday and hour.

    Args:
        local: Local slot, 0-167.

    Returns:
        "Sat 22:00".
    """
    return f"{WEEKDAYS[local // 24]} {local % 24:02d}:00"


def window_long(w: Window, offset_ms: int) -> str:
    """A window in local time, e.g. "Sat 17:00–21:00" or "Sun 23:00–Mon 02:00".

    Args:
        w: The window (UTC slots).
        offset_ms: Local time minus UTC, ms.

    Returns:
        The label; the end weekday shows only when it differs from the start's.
    """
    start = local_slot(w.start_slot, offset_ms)
    end = (start + w.hours) % SLOTS
    end_text = hour_text(end) if end // 24 != start // 24 else f"{end % 24:02d}:00"
    return f"{hour_text(start)}–{end_text}"


def windows_short(windows: Sequence[Window], offset_ms: int) -> str:
    """The Inventory column: windows in local time, compact, e.g. "Sat 17–21 · Sun 19–22 (early pattern)" (D31).

    Args:
        windows: Selling windows, best first.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text; "—" when there are none (W7).
    """
    parts = []
    for w in windows:
        start = local_slot(w.start_slot, offset_ms)
        end = (start + w.hours) % SLOTS
        end_text = f"{WEEKDAYS[end // 24]} {end % 24:02d}" if end // 24 != start // 24 else f"{end % 24:02d}"
        label = f"{WEEKDAYS[start // 24]} {start % 24:02d}–{end_text}"
        parts.append(label + (" (early pattern)" if w.strength == "early pattern" else ""))
    return " · ".join(parts) or "—"


def ratio_text(value: float, places: int) -> str:
    """A ratio with fixed decimals, rounded half up.

    Args:
        value: The ratio.
        places: Decimals.

    Returns:
        E.g. 1.135 → "1.14" with places 2.
    """
    scale = 10 ** places
    n = round_half_up(value * scale)
    return f"{n // scale}.{n % scale:0{places}d}"


def heatmap(prof: Profile, offset_ms: int, places: int = 2) -> list[dict]:
    """A 7 × 24 heatmap of a profile in local time (SPEC §3.5 item detail, D30).

    Levels spread the item's own lowest to highest slot over 0-4, so a quiet item's pattern
    still shows. Slots without any weeks of data have no level.

    Args:
        prof: The profile (UTC slots).
        offset_ms: Local time minus UTC, ms.
        places: Decimals in the cell titles.

    Returns:
        Rows Mon..Sun: {"day", "cells": [{"level": 0-4 | None, "title"}] × 24}.
    """
    seen = [v for v, w in zip(prof.values, prof.weeks) if w > 0]
    lo, hi = (min(seen), max(seen)) if seen else (0.0, 0.0)
    cells: list[dict] = [{}] * SLOTS
    for slot in range(SLOTS):
        local, value, weeks = local_slot(slot, offset_ms), prof.values[slot], prof.weeks[slot]
        if weeks == 0:
            cells[local] = {"level": None, "title": f"{hour_text(local)} no data"}
            continue
        level = round_half_up(LEVELS * (value - lo) / (hi - lo)) if hi > lo else LEVELS // 2
        cells[local] = {"level": level, "title": f"{hour_text(local)} ×{ratio_text(value, places)} · {weeks} weeks"}
    return [{"day": WEEKDAYS[d], "cells": cells[d * 24:(d + 1) * 24]} for d in range(7)]


def _peak(prof: Profile) -> int | None:
    """The slot with the highest value among slots with data.

    Args:
        prof: A profile.

    Returns:
        The UTC slot (ties: the earliest), or None when no slot has data.
    """
    slots = [s for s in range(SLOTS) if prof.weeks[s] > 0]
    return max(slots, key=lambda s: (prof.values[s], -s)) if slots else None


def three_answers(how: Profile, sales: Profile, windows: Sequence[Window], offset_ms: int) -> dict[str, str]:
    """The three selling answers, kept apart (SPEC §3.3.6, W9).

    Args:
        how: Price profile.
        sales: Estimated sales profile.
        windows: Selling windows, best first (ranked by sell_score).
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"price": "Sat 20:00 · ×1.50", "sales": "Tue 19:00 · 3.0× an average hour",
         "best": "Wed 19:00–20:00"}; "—" for each answer without data.
    """
    price, busy = _peak(how), _peak(sales)
    return {
        "price": "—" if price is None else
        f"{hour_text(local_slot(price, offset_ms))} · ×{ratio_text(how.values[price], 2)}",
        "sales": "—" if busy is None else
        f"{hour_text(local_slot(busy, offset_ms))} · {ratio_text(sales.values[busy], 1)}× an average hour",
        "best": window_long(windows[0], offset_ms) if windows else "—",
    }


def _window_rows(windows: Sequence[Window], offset_ms: int) -> list[dict]:
    """The detail's window list (D31).

    Args:
        windows: Selling windows, best first.
        offset_ms: Local time minus UTC, ms.

    Returns:
        One {"when", "uplift", "uplift_class", "strength"} per window.
    """
    out = []
    for w in windows:
        uplift = round_half_up((w.how_avg - 1) * 100)
        out.append({"when": window_long(w, offset_ms), "uplift": f"{'+' if uplift >= 0 else '−'}{abs(uplift)}% price",
                    "uplift_class": "pos" if uplift > 0 else "neg" if uplift < 0 else "",
                    "strength": f"{w.strength} · {w.weeks} weeks"})
    return out


def _activity_lines(f: DetailFacts) -> list[tuple[str, str]]:
    """The market activity breakdown (SPEC §3.3.6).

    Args:
        f: The item's facts.

    Returns:
        (label, value) lines; "—" for anything unavailable.
    """
    m = market_activity(None, f.conf, f.sold_per_day, f.source, f.units_on_ah, f.kind)
    sales = m.sales if f.sold_per_day is None else f"{m.sales} · {f.days_covered} of 7 days covered"
    unseen = None if f.unseen_per_day is None else round_half_up(f.unseen_per_day)
    if unseen:                                            # shown from 1 a day (D106); it is already in the rate
        sales += f" · about {unseen} a day unseen (est.)"
    listed = "—" if f.listed_per_day is None else f"{round_half_up(f.listed_per_day)}/day"
    turnover = "—" if f.turnover_per_day is None else f"{round_half_up(f.turnover_per_day)}/day"
    return [
        ("Est. sales per day", sales),
        ("Current supply", "—" if f.units_on_ah is None else f"{f.units_on_ah} units"),
        ("Supply/volume", m.days),
        ("Volume confidence", "—" if f.conf is None else f"{f.conf}%"),
        ("Seasonality strength", "— · too little evidence" if f.seasonality is None else str(f.seasonality)),
        ("Newly listed per day", listed),
        ("Listing turnover (info only, not sales)", turnover),
    ] + ([] if f.lines else [("Vendor price", vendor_text(f.vendor_sell, f.vendor_from_game))])   # in the lines (D105)


def vendor_text(vendor_sell: int | None, from_game: bool) -> str:
    """The item detail's vendor price line (SPEC §2.4 "Vendor prices locally too", BZ25).

    Args:
        vendor_sell: Copper a merchant pays for one; 0 = it can't be sold to one; None = unknown.
        from_game: Whether the price came from the game (the addon's itemNames), not AHF or the cloud summary.

    Returns:
        "1s 50c", "1s 50c · from the game", "none: can't be sold to a merchant", or "unknown" (no deposit counted).
    """
    if vendor_sell is None:
        return "unknown"
    text = money(vendor_sell) if vendor_sell > 0 else "none: can't be sold to a merchant"
    return f"{text} · from the game" if from_game else text


def item_detail(f: DetailFacts, offset_ms: int) -> dict:
    """The item detail payload (SPEC §3.5 item detail, W9, D30, D31).

    Args:
        f: The item's facts.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"name", "quality", "summary", "windows", "answers", "activity": [[label, value]],
         "heat": {"price", "sales", "supply"}, "lines": market_lines's payload or None (D105)}.
    """
    trend = round_half_up(f.trend7 * 10)
    weeks = max(f.how.weeks)
    summary = [f"med30 {money(f.med30)}", f"trend {'+' if trend >= 0 else '−'}{abs(trend) // 10}.{abs(trend) % 10}%/day",
               f"{weeks} weeks of data" if weeks else "no weekly data yet"]
    if f.lines:
        summary.append(coverage_text(f.lines.coverage30))
    return {
        "name": f.name, "quality": f.quality, "summary": " · ".join(summary),
        "windows": _window_rows(f.sell_windows, offset_ms),
        "answers": three_answers(f.how, f.sales, f.sell_windows, offset_ms),
        "activity": [list(line) for line in _activity_lines(f)],
        "heat": {"price": heatmap(f.how, offset_ms), "sales": heatmap(f.sales, offset_ms, 1),
                 "supply": heatmap(f.supply, offset_ms, 1)},
        "lines": market_lines(f.lines, offset_ms) if f.lines else None,
    }
