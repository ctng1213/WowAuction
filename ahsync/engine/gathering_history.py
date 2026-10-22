"""Gathering's price history: the historical columns and the full detail (SPEC §3.4E.7, §3.4E.8; built M18).

Everything comes from the item's published intelligence (med30, its hourly closes in 30 days, the
168-slot `how`, its selling windows and seasonality strength; §3.3.4, §3.3.6), never from history
reads (§3.1.2). The heatmap's cells are the Inventory item detail's (`item_detail.heatmap`) with
Gathering's three marks laid over them. Slots are UTC (0 = Mon 00:00); the caller passes the local
offset and every label comes out in local time. Pure: data in, texts out.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.item_detail import heatmap, hour_text, local_slot, ratio_text, window_long
from ahsync.engine.patterns import SLOTS, Window
from ahsync.engine.prices import MIN_HOURLY_CLOSES, weekday_hour
from ahsync.engine.profile import Profile
from ahsync.engine.views import delta_percent, money

TIMING_EDGE_MIN_STRENGTH = 40      # config timing_edge_min_strength (§3.6): a sell window from this strength up
HOUR_MS, MINUTE_MS = 3_600_000, 60_000
WEEK_MINUTES = 7 * 1440
DASH = "—"
WINDOW, WEAK, NO_WINDOW = "window", "weak", "none"     # what the Sell window column can say (§3.4E.7)
EVIDENCE_TITLE = "Seasonality strength: insufficient evidence (fewer than 3 weeks of data)"
NO_WINDOW_TITLE = "No selling window: too little weekly data"
STRONG = "the weekly profile beat a flat price on the held-out week"
WEAK_TEXT = "weak pattern: no strong timing advantage"
HEAT_TEXT = ("Brighter = higher. The wait runs from now to the window's start. Hover a cell for its ratio and the "
             "weeks behind it.")
WINDOWS_NOTE = "Favourable listing windows, not guaranteed sale times."
HEAT_HOURS = [f"{hour:02d}:00" for hour in range(24)]   # the hour of a heatmap cell's hover, sent once per page
HOVER_PREFIX = len("Mon 00:00 ")   # item_detail.heatmap's titles start with the cell's day and hour


@dataclass(frozen=True)
class History:
    """One item's price history, as its intelligence publishes it."""

    price: int | None                  # current AH unit price, copper; None = no listing
    med30: int | None                  # copper
    closes_30d: int | None             # hourly closes in the last 30 days; None = unknown (older documents)
    seasonality: int | None            # 0-100; None = insufficient evidence
    how: Profile | None                # the 168-slot price profile; None = no profile published
    windows: tuple[Window, ...] = ()   # selling windows, best first
    min_strength: int = TIMING_EDGE_MIN_STRENGTH


def has_median(h: History) -> bool:
    """Whether the 30-day median may be shown: at least MIN_HOURLY_CLOSES closes (SPEC §3.4E.7, §3.3.4 thin data).

    Args:
        h: The item's history.

    Returns:
        True with a med30 on 24+ closes, or on an unknown count (an older document keeps it, as it keeps windows).
    """
    return h.med30 is not None and (h.closes_30d is None or h.closes_30d >= MIN_HOURLY_CLOSES)


def state(h: History) -> str:
    """What the Sell window column says (SPEC §3.4E.7; GA29, GA30).

    Args:
        h: The item's history.

    Returns:
        WINDOW when it has a top window and strength ≥ min_strength (inclusive); WEAK under it; NO_WINDOW for
        thin data, insufficient evidence or no window.
    """
    if not has_median(h) or h.seasonality is None:
        return NO_WINDOW
    if h.seasonality < h.min_strength:
        return WEAK
    return WINDOW if h.windows else NO_WINDOW


def signed(percent: int) -> str:
    """A whole percent with its sign, e.g. "+10%", "−22%", "0%".

    Args:
        percent: The percent.

    Returns:
        The text (a true minus sign).
    """
    return f"{'+' if percent > 0 else '−' if percent < 0 else ''}{abs(percent)}%"


def sign_class(percent: int | None) -> str:
    """The delta colours (views.delta_percent's rule, TT1-TT5).

    Args:
        percent: The percent, or None.

    Returns:
        "pos" above 0, "neg" below, else "".
    """
    return "pos" if percent and percent > 0 else "neg" if percent and percent < 0 else ""


def window_price(med30: int, w: Window) -> int:
    """A window's median price = med30 × its average price ratio, half up (SPEC §3.4E.7).

    Args:
        med30: The 30-day median, copper.
        w: The window (its how_avg from the intelligence).

    Returns:
        Copper: 2s × 1.12 = 224.
    """
    return math.floor(med30 * w.how_avg + 0.5)


def _thin_title(h: History) -> str:
    """The Median 30d cell's hover when the median is not shown (GA28).

    Args:
        h: The item's history (has_median False).

    Returns:
        "no price history yet", or "thin data: 23 hourly closes in 30 days; the median needs 24".
    """
    if h.med30 is None:
        return "no price history yet"
    return f"thin data: {h.closes_30d} hourly closes in 30 days; the median needs {MIN_HOURLY_CLOSES}"


def wait_minutes(w: Window, now_ms: int, offset_ms: int) -> int:
    """Whole minutes from now to the window's next start; 0 while now is inside it (SPEC §3.4E.7).

    Args:
        w: The window (UTC slots).
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms (whole hours, as local_slot).

    Returns:
        Minutes: Mon 10:38 to Sat 17:00 is 7,582.
    """
    local_ms = now_ms + offset_ms // HOUR_MS * HOUR_MS
    day, hour = weekday_hour(local_ms)
    now_min = day * 1440 + hour * 60 + (local_ms // MINUTE_MS) % 60
    start_min = local_slot(w.start_slot, offset_ms) * 60
    if (now_min - start_min) % WEEK_MINUTES < w.hours * 60:
        return 0
    return (start_min - now_min) % WEEK_MINUTES


def wait_text(minutes: int) -> str:
    """A wait for the reading line, e.g. "5 d 6 h 22 min" (the mockup's gaWait).

    Args:
        minutes: Whole minutes.

    Returns:
        "<d> d <h> h" plus " <m> min" when there are minutes left over.
    """
    rest = f" {minutes % 60} min" if minutes % 60 else ""
    return f"{minutes // 1440} d {minutes % 1440 // 60} h{rest}"


def _window_cell(h: History, how: str, offset_ms: int) -> dict:
    """The Sell window cell (SPEC §3.4E.7; GA29, GA30).

    Args:
        h: The item's history.
        how: state(h).
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"window", "window_sub", "window_title", "window_muted"}: the top window's start with a hover naming the
        window and its best hour; "no timing edge" (muted) under the strength; "—" "too little data" (muted).
    """
    if how == WEAK:
        return {"window": "no timing edge", "window_sub": "", "window_muted": True,
                "window_title": f"Seasonality strength {h.seasonality}, under {h.min_strength}: weak pattern, no "
                                f"strong timing advantage"}
    if how == NO_WINDOW:
        title = (_thin_title(h) if not has_median(h) else EVIDENCE_TITLE if h.seasonality is None
                 else NO_WINDOW_TITLE)
        return {"window": DASH, "window_sub": "too little data", "window_muted": True, "window_title": title}
    top = h.windows[0]
    return {"window": hour_text(local_slot(top.start_slot, offset_ms)), "window_sub": "", "window_muted": False,
            "window_title": f"Start of the top selling window {window_long(top, offset_ms)} (best hour "
                            f"{hour_text(local_slot(top.best_slot, offset_ms))}) · seasonality strength "
                            f"{h.seasonality}"}


def columns(h: History, now_ms: int, offset_ms: int) -> dict:
    """The row's historical cells and their sort keys (SPEC §3.4E.7; GA28-GA30).

    Args:
        h: The item's history.
        now_ms: Now, UTC ms (the Sell window sorts by the wait to it).
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"median", "median_title", "deviation", "deviation_class", "window", "window_sub", "window_title",
        "window_muted", "window_price", "window_price_title", "window_delta", "window_delta_class", "sort":
        {"median", "deviation", "window", "window_price"} (None for "—")}.
    """
    shown = has_median(h)
    deviation = delta_percent(h.price, h.med30) if shown else None
    how = state(h)
    out = {"median": money(h.med30) if shown else DASH, "median_title": "" if shown else _thin_title(h),
           "deviation": DASH if deviation is None else signed(deviation), "deviation_class": sign_class(deviation),
           **_window_cell(h, how, offset_ms), "window_price": DASH, "window_price_title": "", "window_delta": "",
           "window_delta_class": ""}
    sort = {"median": h.med30 if shown else None, "deviation": deviation, "window": None, "window_price": None}
    if how == WINDOW:
        top, med30 = h.windows[0], int(h.med30 or 0)
        price = window_price(med30, top)
        delta = delta_percent(price, h.price)
        out.update({"window_price": money(price),
                    "window_price_title": f"{money(med30)} median × {ratio_text(top.how_avg, 2)} "
                                          f"({window_long(top, offset_ms)})",
                    "window_delta": "" if delta is None else f"{signed(delta)} vs now",
                    "window_delta_class": sign_class(delta)})
        sort.update({"window": wait_minutes(top, now_ms, offset_ms), "window_price": price})
    return {**out, "sort": sort}


def blank_columns(header_note: str) -> dict:
    """The historical cells on day one: "—", their hover the header's "needs price history" (SPEC §3.4E.8, D84).

    Args:
        header_note: The hover text.

    Returns:
        columns()'s fields, every one "—" or empty, every sort key None.
    """
    return {"median": DASH, "median_title": header_note, "deviation": DASH, "deviation_class": "", "window": DASH,
            "window_sub": "", "window_title": header_note, "window_muted": False, "window_price": DASH,
            "window_price_title": "", "window_delta": "", "window_delta_class": "",
            "sort": {"median": None, "deviation": None, "window": None, "window_price": None}}


def _now_slot(now_ms: int) -> int:
    """The UTC hour-of-week slot of now.

    Args:
        now_ms: UTC ms.

    Returns:
        0-167, 0 = Mon 00:00 UTC.
    """
    day, hour = weekday_hour(now_ms)
    return day * 24 + hour


def _marked(windows: Sequence[Window]) -> dict[int, int]:
    """Which window each UTC slot belongs to.

    Args:
        windows: The windows shown, best first.

    Returns:
        UTC slot -> rank (0 = the top window); slots outside every window are absent.
    """
    out: dict[int, int] = {}
    for rank, w in enumerate(windows):
        for i in range(w.hours):
            out.setdefault((w.start_slot + i) % SLOTS, rank)
    return out


def heat(h: History, windows: Sequence[Window], now_ms: int, offset_ms: int) -> dict | None:
    """The 7 × 24 price heatmap with Gathering's marks (SPEC §3.4E.7 item 5; GA31).

    Args:
        h: The item's history.
        windows: The windows to mark, best first ([] without a timing edge).
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        item_detail.heatmap's cells (Mon..Sun, local time) in a compact form, so 150 catalog items' heatmaps stay
        a couple of KB each: {"texts": the distinct hovers after "Mon 10:00 " ("×0.97 · 7 weeks", adding " · now"
        and " · window <n>"), in first-seen order; "rows": [{"day", "cells": 24 × [level 0-4 or None, index into
        texts, mark]}]}, the mark "now" (the thick accent outline), "top" (dashed, the top window), "next"
        (dotted, windows 2 and 3) or "". The page writes a hover as the day, HEAT_HOURS[hour] and its text. None
        when no slot has data.
    """
    if h.how is None or not any(h.how.weeks):
        return None
    now_local = local_slot(_now_slot(now_ms), offset_ms)
    ranks = {local_slot(s, offset_ms): r for s, r in _marked(windows).items()}
    texts: dict[str, int] = {}
    rows = []
    for d, row in enumerate(heatmap(h.how, offset_ms)):
        cells = []
        for hour, cell in enumerate(row["cells"]):
            local, rank = d * 24 + hour, ranks.get(d * 24 + hour)
            mark = "now" if local == now_local else "" if rank is None else "top" if rank == 0 else "next"
            text = cell["title"][HOVER_PREFIX:]
            if local == now_local or rank is not None:
                text += (" · now" if local == now_local else "") + ("" if rank is None else f" · window {rank + 1}")
            cells.append([cell["level"], texts.setdefault(text, len(texts)), mark])
        rows.append({"day": row["day"], "cells": cells})
    return {"texts": list(texts), "rows": rows}


def reading(h: History, now_ms: int, offset_ms: int) -> list[list[str]]:
    """The reading line under the heatmap (SPEC §3.4E.7 item 5; GA31).

    Args:
        h: The item's history.
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        [text, class] segments, joined by spaces on the page: "now 2s 20c", "(Mon 10:00 slot ratio 0.97)" muted,
        "· best Sat 17:00–21:00 at 2s 24c ·", "+2%" pos, "for a 5 d 6 h 22 min wait"; without a timing edge the
        line ends with why; [] without a profile.
    """
    if h.how is None or not any(h.how.weeks):
        return []
    slot = _now_slot(now_ms)
    now = [f"now {money(h.price)}" if h.price is not None else "now no listing", ""]
    # A slot without weeks of data has only its fallback value: say so, as its heatmap cell does
    seen = f"slot ratio {ratio_text(h.how.values[slot], 2)}" if h.how.weeks[slot] else "slot: no data"
    ratio = [f"({hour_text(local_slot(slot, offset_ms))} {seen})", "muted"]
    how = state(h)
    if how == WEAK:
        return [now, ratio, [f"· no timing edge: seasonality strength {h.seasonality}, under {h.min_strength}", ""]]
    if how == NO_WINDOW:
        return [now, ratio, ["· too little data for a selling window", ""]]
    top = h.windows[0]
    price = window_price(int(h.med30 or 0), top)
    wait = wait_minutes(top, now_ms, offset_ms)
    best = f"· best {window_long(top, offset_ms)} at {money(price)} ·"
    delta = delta_percent(price, h.price)
    tail = "in the window now" if wait == 0 else f"for a {wait_text(wait)} wait"
    return [now, ratio, [best, ""], *([[signed(delta), sign_class(delta)]] if delta is not None else []), [tail, ""]]


def legend(windows: Sequence[Window], now_ms: int, offset_ms: int) -> dict:
    """The heatmap's legend texts; the page draws a swatch before each non-empty one (SPEC §3.4E.7 item 5).

    Args:
        windows: The marked windows, best first.
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"now": "now (Mon 10:00)", "top": "the top selling window (Sat 17:00–21:00)" or "", "next": "windows 2 and
        3", "window 2" or "", "text"}.
    """
    nxt = {0: "", 1: "window 2"}.get(len(windows) - 1, "windows 2 and 3") if windows else ""
    return {"now": f"now ({hour_text(local_slot(_now_slot(now_ms), offset_ms))})",
            "top": f"the top selling window ({window_long(windows[0], offset_ms)})" if windows else "",
            "next": nxt, "text": HEAT_TEXT}


def window_rows(h: History, windows: Sequence[Window], offset_ms: int) -> list[dict]:
    """The top selling windows table (SPEC §3.4E.7 item 6; GA32).

    Args:
        h: The item's history (has_median True when windows is not empty).
        windows: The windows, best first.
        offset_ms: Local time minus UTC, ms.

    Returns:
        [{"rank", "window", "price", "title", "weeks"}]: "1", "Sat 17:00–21:00", "2s 24c", "2s median × 1.12", "7"
        (the weeks behind the window's thinnest slot).
    """
    med30 = int(h.med30 or 0)
    return [{"rank": str(i + 1), "window": window_long(w, offset_ms), "price": money(window_price(med30, w)),
             "title": f"{money(med30)} median × {ratio_text(w.how_avg, 2)}", "weeks": str(w.weeks)}
            for i, w in enumerate(windows)]


def timing_lines(h: History, offset_ms: int) -> list[list]:
    """Seasonality strength with its sentence, and what the table's Sell window says (SPEC §3.4E.7 item 7; GA32).

    Args:
        h: The item's history.
        offset_ms: Local time minus UTC, ms.

    Returns:
        [label, value, note, note class, dot] lines: ["Seasonality strength", "61", "· the weekly profile beat a
        flat price on the held-out week", "", None] (under min_strength the weak sentence; "—" with "· insufficient
        evidence"), then ["Sell window in the table", "Sat 17:00", "· start of Sat 17:00–21:00 (best hour Sat
        19:00)", "", None].
    """
    s = h.seasonality
    if s is None:
        strength = ["Seasonality strength", DASH, "· insufficient evidence", "", None]
    else:
        strength = ["Seasonality strength", str(s), f"· {STRONG if s >= h.min_strength else WEAK_TEXT}", "", None]
    how = state(h)
    if how == WINDOW:
        top = h.windows[0]
        start = hour_text(local_slot(top.start_slot, offset_ms))
        note = (f"· start of {window_long(top, offset_ms)} (best hour "
                f"{hour_text(local_slot(top.best_slot, offset_ms))})")
        return [strength, ["Sell window in the table", start, note, "", None]]
    cell = ["no timing edge", f"· seasonality strength {s}, under {h.min_strength}"] if how == WEAK \
        else [DASH, "· too little data"]
    return [strength, ["Sell window in the table", cell[0], cell[1], "", None]]


def median_lines(h: History) -> list[list]:
    """The price card's 30-day median and deviation (SPEC §3.4E.7 item 1; D83).

    Args:
        h: The item's history.

    Returns:
        [label, value, note, note class, dot] lines: ["30-day median", "2s", "", "", None] and ["Deviation", "",
        "+10%", "pos", None] (the coloured percent as the note); "—" with the thin-data note without a median.
    """
    if not has_median(h):
        return [["30-day median", DASH, _thin_title(h), "", None], ["Deviation", DASH, "", "", None]]
    deviation = delta_percent(h.price, h.med30)
    dev = ["Deviation", DASH, "", "", None] if deviation is None else \
        ["Deviation", "", signed(deviation), sign_class(deviation), None]
    return [["30-day median", money(h.med30), "", "", None], dev]


def detail(h: History, now_ms: int, offset_ms: int) -> dict:
    """The detail's history blocks, in E.7's order after the formulas (SPEC §3.4E.7 items 5-7; GA31, GA32, D83).

    Args:
        h: The item's history.
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"heat" (heat(), or None), "reading" (segments), "legend", "windows" (the table's rows; [] without a timing
        edge), "windows_note", "timing" (timing_lines), "timing_note" (what changes under min_strength)}. The
        windows and their marks show only where the table's Sell window is a window.
    """
    shown = list(h.windows) if state(h) == WINDOW else []
    return {"heat": heat(h, shown, now_ms, offset_ms), "reading": reading(h, now_ms, offset_ms),
            "legend": legend(shown, now_ms, offset_ms), "windows": window_rows(h, shown, offset_ms),
            "windows_note": WINDOWS_NOTE if shown else "", "timing": timing_lines(h, offset_ms),
            "timing_note": f'Under {h.min_strength} the sentence reads "{WEAK_TEXT}" and the table says "no timing '
                           f'edge".'}
