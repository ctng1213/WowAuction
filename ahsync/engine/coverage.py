"""The Status page's Data coverage card: which hours of the last 90 days each source observed (SPEC §3.5, D89-D96).

Pure: per-source lists of UTC hour numbers in, the card's payload out, every text formatted here. An hour counts for a
source when the source observed it completely (`observed_hours`, §3.2); the heatmap shows, per hour-of-week slot in
local time, how many of the slot's hours any source observed, shaded by the fraction of the weeks possible.
"""
from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence

from ahsync.engine.views import SOURCE_URLS

HOUR_MS = 3_600_000
WINDOW_HOURS = 2160                       # 90 days
SOURCE_ORDER = ("local", "ahledger", "ahf")
SOURCE_ALIASES = {"ahledger_h1": "ahledger"}   # the table and the backfill show as one source
LABELS = {"local": "your scans", "ahledger": "AHledger", "ahf": "auctionhouse.fyi",
          "blizzard": "Blizzard (cloud)"}       # its hours are the cloud summary's coverage (D99)
LOCAL_ONLY = ("local",)                   # never amber: you scan when you play
STALE_HOURS = 72                          # a remote source's newest hour older than this is amber
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SCALE = (("0", 0), ("up to 25%", 1), ("up to 50%", 2), ("up to 75%", 3), ("under 100%", 4), ("100%", 5))
READING = ("Coverage is the hours any source observed; it is what the weekly patterns and sales estimates are built "
           "from. Dark cells make reliable windows; pale cells are hours nobody has scanned yet.")
RULE = ("A source counts an hour when it has at least one complete observation in it: a complete scan, an AHF hour "
        "file, an AHledger table update or hourly bucket. Never a partial scan")


def ranges_to_hours(ranges: Iterable[tuple[int, int]]) -> set[int]:
    """A cloud summary's `coverage` as the hours it observed (SPEC §2.4, D99).

    Args:
        ranges: (first hour, last hour) runs of observed UTC hour numbers, both inclusive.

    Returns:
        Every hour in them.
    """
    return {hour for first, last in ranges for hour in range(first, last + 1)}


def step(value: int, possible: int) -> int:
    """A cell's shading step by fraction of the weeks possible, in exact integers (SPEC §3.5, D92).

    Args:
        value: Hours of the slot any source observed.
        possible: Hours of the slot in the window (12 or 13).

    Returns:
        0 none; 1 at most 25%; 2 at most 50%; 3 at most 75%; 4 more but not all; 5 every possible week.
    """
    if value == 0:
        return 0
    if 4 * value <= possible:
        return 1
    if 2 * value <= possible:
        return 2
    if 4 * value <= 3 * possible:
        return 3
    return 4 if value < possible else 5


def share_text(hours: int, total: int = WINDOW_HOURS) -> str:
    """Hours as a share of the window, rounded half up (D94).

    Args:
        hours: Hours counted.
        total: Hours in the window.

    Returns:
        "41%"; "<1%" when it rounds to 0 but isn't 0; ">99%" when it rounds to 100 but isn't all.
    """
    pct = (200 * hours + total) // (2 * total)
    if hours and pct == 0:
        return "<1%"
    if hours < total and pct == 100:
        return ">99%"
    return f"{pct}%"


def age_text(hours: int) -> str:
    """An hour's age from the window's end (D94).

    Args:
        hours: Hours between the counted hour's start and the window's end, at least 1.

    Returns:
        "N h ago" under 48 h, whole days after ("2 d ago").
    """
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} d ago"


def span_text(hours: int) -> str:
    """A gap's length (D94).

    Args:
        hours: Its length in hours.

    Returns:
        "N h" under 48 h, else "D d" plus " H h" when not whole days ("3 d 6 h").
    """
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} d" + (f" {hours % 24} h" if hours % 24 else "")


def _local(hour: int, offset_ms: int) -> time.struct_time:
    """An hour's start in local time.

    Args:
        hour: UTC hour number.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The broken-down local time.
    """
    return time.gmtime((hour * HOUR_MS + offset_ms) // 1000)


def _slot(hour: int, offset_ms: int) -> tuple[int, int]:
    """An hour's hour-of-week slot in local time.

    Args:
        hour: UTC hour number.
        offset_ms: Local time minus UTC, ms.

    Returns:
        (day 0 = Monday, hour of day 0-23).
    """
    t = _local(hour, offset_ms)
    return t.tm_wday, t.tm_hour


def _when(hour: int, offset_ms: int) -> str:
    """A gap's end point, e.g. "4 Oct 00:00".

    Args:
        hour: UTC hour number.
        offset_ms: Local time minus UTC, ms.

    Returns:
        Day of month, month and local time.
    """
    t = _local(hour, offset_ms)
    return f"{t.tm_mday} {time.strftime('%b %H:%M', t)}"


def _sources(sources: Sequence[str]) -> list[str]:
    """The table's rows: SOURCE_ORDER first, then any other source by name; aliases folded.

    Args:
        sources: The market's sources.

    Returns:
        Source names, each once.
    """
    names = {SOURCE_ALIASES.get(s, s) for s in sources}
    return [s for s in SOURCE_ORDER if s in names] + sorted(names - set(SOURCE_ORDER))


def _joined(labels: list[str]) -> str:
    """Labels joined with commas and a final "and".

    Args:
        labels: The labels.

    Returns:
        "a, b and c"; "a" for one.
    """
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _longest_gap(seen: set[int], first: int, end: int) -> tuple[int, int] | None:
    """The longest run of hours nobody observed; the earliest of equal runs.

    Args:
        seen: Hours any source observed.
        first: The window's first hour.
        end: The hour after the window's last.

    Returns:
        (first hour of the run, its length), or None when every hour has data.
    """
    best: tuple[int, int] | None = None
    start = None
    for hour in range(first, end + 1):
        if hour < end and hour not in seen:
            start = hour if start is None else start
        elif start is not None:
            if best is None or hour - start > best[1]:
                best = (start, hour - start)
            start = None
    return best


def _none_row(seen: set[int], first: int, end: int, offset_ms: int) -> dict:
    """The "no data" row: the hours nobody observed and the longest gap (D90, D91, D94).

    Args:
        seen: Hours any source observed.
        first: The window's first hour.
        end: The hour after the window's last.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"hours", "hours_text", "share", "gap_text", "longest_gap": {"hours", "from", "to"} ms, or None}.
    """
    hours = WINDOW_HOURS - len(seen)
    gap = _longest_gap(seen, first, end)
    row = {"hours": hours, "hours_text": f"{hours:,} h", "share": share_text(hours), "gap_text": "no gap",
           "longest_gap": None}
    if gap is not None:
        start, length = gap
        until = "now" if start + length == end else _when(start + length, offset_ms)
        row["gap_text"] = f"longest gap {span_text(length)} ({_when(start, offset_ms)} – {until})"
        row["longest_gap"] = {"hours": length, "from": start * HOUR_MS, "to": (start + length) * HOUR_MS}
    return row


def _source_row(src: str, hours: set[int], end: int) -> dict:
    """One source's table row (D90).

    Args:
        src: The source.
        hours: The window's hours it observed.
        end: The hour after the window's last.

    Returns:
        {"src", "label", "url", "hours", "hours_text", "share", "newest", "newest_class", "oldest"}.
    """
    newest = end - max(hours) if hours else None
    return {"src": src, "label": LABELS.get(src, src), "url": SOURCE_URLS.get(src), "hours": len(hours),
            "hours_text": f"{len(hours):,} h", "share": share_text(len(hours)),
            "newest": "—" if newest is None else age_text(newest),
            "newest_class": "amber" if newest is not None and newest > STALE_HOURS and src not in LOCAL_ONLY else "",
            "oldest": "—" if not hours else age_text(end - min(hours))}


def _cells(by_src: dict[str, set[int]], rows: list[str], first: int, end: int, offset_ms: int) -> list[list[dict]]:
    """The 7 × 24 heatmap, Monday first, hour 0 first (D89, D92).

    Args:
        by_src: Source -> the window's hours it observed.
        rows: The sources, in table order.
        first: The window's first hour.
        end: The hour after the window's last.
        offset_ms: Local time minus UTC, ms.

    Returns:
        Cells {"value", "possible", "step", "by_src", "title"}.
    """
    counts = [[[0, 0, {s: 0 for s in rows}] for _ in range(24)] for _ in DAYS]
    for hour in range(first, end):
        day, hod = _slot(hour, offset_ms)
        cell = counts[day][hod]
        cell[1] += 1
        seen = [s for s in rows if hour in by_src[s]]
        cell[0] += 1 if seen else 0
        for s in seen:
            cell[2][s] += 1
    return [[{"value": v, "possible": p, "step": step(v, p), "by_src": per,
              "title": f"{DAYS[day]} {hod:02d}:00 · {v} of {p} weeks · " + ", ".join(f"{s} {per[s]}" for s in rows)}
             for hod, (v, p, per) in enumerate(day_cells)] for day, day_cells in enumerate(counts)]


def _rule(partial: tuple[int, float] | None, offset_ms: int) -> str:
    """The counting rule, naming the newest partial scan of the window (D93).

    Args:
        partial: (time ms, coverage 0..1) of the newest partial scan in the window, or None.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The sentence.
    """
    if partial is None:
        return RULE + "."
    at_ms, reached = partial
    when = time.strftime("%a %H:%M", time.gmtime((at_ms + offset_ms) // 1000))
    return f"{RULE} ({when}, closed at {int(reached * 100 + 0.5)}%, is not counted)."


def coverage(hours_by_src: Mapping[str, Iterable[int]], window_end_hour: int, offset_ms: int,
             sources: Sequence[str], partial: tuple[int, float] | None) -> dict:
    """The Data coverage card of one market (SPEC §3.5, D89-D96).

    Args:
        hours_by_src: Source -> UTC hour numbers (floor(ms / 3600000)) it observed completely; "ahledger_h1" counts
            as "ahledger"; hours outside the window and sources not in `sources` are ignored.
        window_end_hour: The current hour's start, as an hour number: the window is the 2,160 hours before it.
        offset_ms: Local time minus UTC, ms (days, slots and times are local).
        sources: The market's sources: "local" and each enabled remote source.
        partial: (time ms, coverage 0..1) of the newest partial scan in the window, or None.

    Returns:
        The payload of SPEC §3.5 without "market": title, sub, window, heading, days, hour_labels, cells, scale,
        scale_note, sources, union, none, note, reading, rule.
    """
    first, end = window_end_hour - WINDOW_HOURS, window_end_hour
    rows = _sources(sources)
    by_src: dict[str, set[int]] = {s: set() for s in rows}
    for src, hours in hours_by_src.items():
        name = SOURCE_ALIASES.get(src, src)
        if name in by_src:
            by_src[name].update(h for h in hours if first <= h < end)
    seen = set().union(*by_src.values())
    t = _local(end, offset_ms)
    end_text = f"{DAYS[t.tm_wday]} {t.tm_mday} {time.strftime('%b %H:%M', t)}"
    return {
        "title": "Data coverage",
        "sub": f"last 90 days to {end_text} · " + _joined([LABELS.get(s, s) for s in rows]),
        "window": {"from": first * HOUR_MS, "to": end * HOUR_MS, "hours": WINDOW_HOURS},
        "heading": "Weeks with data per hour of the week, local time",
        "days": list(DAYS), "hour_labels": [str(h) if h % 6 == 0 else "" for h in range(24)],
        "cells": _cells(by_src, rows, first, end, offset_ms),
        "scale": [{"label": label, "step": s} for label, s in SCALE], "scale_note": "of the weeks possible",
        "sources": [_source_row(s, by_src[s], end) for s in rows],
        "union": {"hours": len(seen), "hours_text": f"{len(seen):,} h", "share": share_text(len(seen))},
        "none": _none_row(seen, first, end, offset_ms),
        "note": f"of the {WINDOW_HOURS:,} hours in the last 90 days; sources overlap, so \"any source\" is less than "
                "their sum",
        "reading": READING, "rule": _rule(partial, offset_ms),
    }
