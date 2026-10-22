"""The Status page: what needs attention, sync state per market, the API, characters and storage (SPEC §3.5).

Status lines are stored as structured fields (SPEC §3.2 status); this module turns them
into sentences in local time, groups the per-hour "bad file" lines into one alert per
market, and adds the one alert no stored line covers: AHF data that has stopped
arriving (D33). The page shows these texts as they are (SPEC §8.5).
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.history_view import ratio_text
from ahsync.engine.sell_record import Calibration
from ahsync.engine.tracking import ProfitSums
from ahsync.engine.volume import Backtest
from ahsync.engine.views import age_text, round_half_up, signed_money, source_label

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
STALE_MS = 6 * HOUR_MS          # newest primary-source price older than this: the data has stopped arriving (D33)
LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class StatusRow:
    """One stored Status line (SPEC §3.2 status)."""

    topic: str
    level: str                    # "info" | "warn" | "error"
    kind: str
    data: dict
    at_ms: int


@dataclass(frozen=True)
class MarketFacts:
    """What the Status page shows about one market's sync."""

    market: str
    last_hour: int | None         # newest hour handled, floor(ms / 3600000)
    last_run_ms: int | None
    newest_price_ms: int | None   # newest current-price observation of the primary source
    items_priced: int
    oldest_candle_ms: int | None  # hourly history in the database
    newest_candle_ms: int | None
    local_scans_7d: int           # your full or partial scans in the last 7 days
    primary_books_7d: int         # the primary source's order books in the last 7 days
    backtests: tuple[tuple[str, Backtest], ...] = ()   # (confirmed source, estimates against it), by source (K4)
    primary: str = "ahf"          # the market's primary remote source (config [sources], SRC2)


@dataclass(frozen=True)
class CharacterFacts:
    """One character as the Status page shows it."""

    char_id: str
    realm: str
    faction: str
    market: str | None
    updated_ms: int | None        # its newest snapshot


@dataclass(frozen=True)
class StatusFacts:
    """Everything the Status page shows besides the stored lines."""

    markets: tuple[MarketFacts, ...]
    characters: tuple[CharacterFacts, ...]
    key_set: bool
    db_bytes: int
    retention_days: int
    calibration: Calibration | None = None   # resolved posts with their planned chance (SPEC §8.8)
    profit: ProfitSums | None = None         # expected against realized profit, last 90 days (SPEC §8.8, K7)


def when(ms: int, offset_ms: int) -> str:
    """A moment in local time, e.g. "Fri 9 Oct 21:14".

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    t = time.gmtime((ms + offset_ms) // 1000)
    return f"{time.strftime('%a', t)} {t.tm_mday} {time.strftime('%b %H:%M', t)}"


def day(ms: int, offset_ms: int) -> str:
    """A local date, e.g. "23 Oct".

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    t = time.gmtime((ms + offset_ms) // 1000)
    return f"{t.tm_mday} {time.strftime('%b', t)}"


def ms_range(start_ms: int, end_ms: int, offset_ms: int) -> str:
    """A time range in local time, end exclusive, e.g. "Fri 9 Oct 00:00–21:00".

    Args:
        start_ms: Start, UTC ms.
        end_ms: End (exclusive), UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text; the end shows its date only when it differs from the start's.
    """
    start, end = when(start_ms, offset_ms), when(end_ms, offset_ms)
    return f"{start}–{end[-5:] if start[:-6] == end[:-6] else end}"


def hour_ranges(hours: Sequence[int]) -> list[tuple[int, int]]:
    """Consecutive hours merged into ranges.

    Args:
        hours: Hour numbers, any order.

    Returns:
        (first, last) inclusive ranges, ascending.
    """
    out: list[list[int]] = []
    for h in sorted(set(hours)):
        if out and h == out[-1][1] + 1:
            out[-1][1] = h
        else:
            out.append([h, h])
    return [(a, b) for a, b in out]


def _ranges_text(ranges_ms: Sequence[Sequence[int]], offset_ms: int) -> str:
    """Stored [start, end) ms ranges as text.

    Args:
        ranges_ms: [[start ms, end ms (exclusive)], ...].
        offset_ms: Local time minus UTC, ms.

    Returns:
        "Fri 9 Oct 00:00–21:00, Sat 10 Oct 03:00–05:00".
    """
    return ", ".join(ms_range(a, b, offset_ms) for a, b in ranges_ms)


def _account_message(kind: str, d: dict) -> tuple[str, str]:
    """Messages about reading SavedVariables.

    Args:
        kind: The line's kind.
        d: Its fields.

    Returns:
        (title, text).
    """
    account = d.get("account", "?")
    if kind == "savedvars_rows_skipped":
        rows = "; ".join(d.get("rows", [])[:3])
        return ("Rows set aside", f"{account}: {d.get('count', 0)} rows were not stored. They stay in the addon and are "
                                  f"stored once the problem is fixed. First: {rows}")
    return {
        "update_ahsync": ("Update AHSync", f"{account}: SavedVariables come from a newer addon version than this AHSync reads."),
        "savedvars_unreadable": ("SavedVariables unreadable", f"{account}: the file couldn't be read; it is read again when it changes."),
        "savedvars_backup_used": ("Read from the backup", f"{account}: AHProfit.lua was unreadable, so AHProfit.lua.bak was read."),
        "savedvars_failed": ("SavedVariables failed", f"{account}: reading the file failed; it is tried again."),
    }[kind]


def size_text(size: int) -> str:
    """A size budget in whole MB, or whole KB under 1 MB (FM40's "2 MB").

    Args:
        size: Bytes.

    Returns:
        E.g. "2 MB" or "200 KB".
    """
    return f"{size // 1_048_576} MB" if size >= 1_048_576 else f"{size // 1024} KB"


def message(row: StatusRow, offset_ms: int) -> tuple[str, str]:
    """A stored Status line as a title and a sentence (SPEC §3.2, §5.2).

    Args:
        row: The line.
        offset_ms: Local time minus UTC, ms.

    Returns:
        (title, text); an unknown kind shows its name.
    """
    d, kind = row.data, row.kind
    market = d.get("market", "")
    if kind in ("update_ahsync", "savedvars_unreadable", "savedvars_backup_used", "savedvars_failed",
                "savedvars_rows_skipped"):
        return _account_message(kind, d)
    if kind == "unknown_realm":
        realm = d.get("realm", "?")
        return ("Realm not mapped to a market",
                f'Characters on "{realm}" ({d.get("faction", "?")}) have no market: your config syncs no market, or '
                f'several, for {d.get("faction", "?")}, so AHSync cannot tell which is theirs. Nothing is planned for them '
                f'and their scans wait in the addon. Add it to config.toml: [realms] "{realm}" = "pvp" (or "normal", '
                f'"rp"), then restart AHSync.')
    texts = {
        "refresh_failed": ("Refresh failed", f"The last full refresh failed ({d.get('error', '?')}). The numbers shown "
                                             f"are from the refresh before it."),
        "refresh_items_failed": ("Items left out", f"{d.get('count', 0)} items failed in the last refresh and were left "
                                                   f"out: {', '.join(d.get('items', []))}"),
        "lost": ("Hours lost", f"{market}: no source has data for {_ranges_text(d.get('ranges_ms', []), offset_ms)} any more."),
        "day_only": ("Day candles only", f"{market}: {_ranges_text(d.get('ranges_ms', []), offset_ms)} have day candles "
                                         f"only, no hours."),
        "format_changed": ("AHF format changed", f"AHF's {d.get('endpoint', '?')} data has a shape AHSync doesn't know. "
                                                 f"It is skipped until AHSync is updated; nothing is lost meanwhile."),
        "feature_refused": ("Premium feature refused", f"{market}: AHF refused {d.get('endpoint', '?')} "
                                                       f"({d.get('feature', '?')}); skipped this run."),
        "premium_until": ("Premium", f"Premium until {day(d['until_ms'], offset_ms)} · upload scans to keep it"
                          if "until_ms" in d else "Premium"),
        "premium_ended": ("Premium ended", f"Premium ended {day(d.get('ended_ms', row.at_ms), offset_ms)}; AHSync "
                                           f"carries on with public data."),
        "long_gap": ("Long gap", f"{market}: AHSync last ran {d.get('days', '?')} days ago. Run it at least every 6 days "
                                 f"to keep the full hourly history."),
        "unreachable": ("AHF unreachable", ("AHF is limiting requests" if d.get("reason") == "rate_limited"
                                            else "AHF didn't answer after retries") +
                        "; the next run carries on where this one stopped."),
        "key_rejected": ("API key rejected", "AHF rejected the API key, so AHSync uses public data. Check the "
                                             "AUCTIONHOUSE_APIKEY environment variable."),
        "market_skipped": ("Market skipped", f"{market} was skipped this run ({d.get('error', '?')}); the next run retries."),
        "addon_file_blocked": ("Addon file not written", f"{d.get('file', '?')} couldn't be replaced after 3 tries "
                                                         f"(locked, or blocked by antivirus). The addon keeps the "
                                                         f"previous one; the next refresh tries again."),
        "addon_file_large": ("Addon file over budget", f"{d.get('file', '?')} is {d.get('bytes', 0) // 1024} KB, over "
                                                       f"its {d.get('limit', 0) // 1024} KB budget; WoW may load slowly."),
        "candidate_export_trimmed": ("Candidate export trimmed",
                                     f"candidate export trimmed to {d.get('kept', 0)} of {d.get('total', 0)} to fit "
                                     f"{size_text(d.get('limit', 0))}: the lowest alternatives stay on the dashboard; "
                                     f"every funded row is in the addon file (FM40)."),
        "volume_method_old": ("Older sales estimates", f"{market}: {d.get('days', 0)} days of sales estimates are "
                                                       f"from an older method; their scans are gone, so they can't be "
                                                       f"recomputed. They age out with the 90-day history."),
        "risk_tolerance_not_saved": ("Risk tolerance not saved",
                                     f"The dashboard couldn't rewrite config.toml ({d.get('error', '?')}), so the risk "
                                     f"tolerance and the plan are unchanged. Check that the file can be written, then "
                                     f"pick the value again."),
        "gathering_catalog": ("Gathering catalog", str(d.get("text", ""))),          # SPEC §3.4E.1, GA2
        "restored": ("Database restored", f"The database was damaged and was restored from "
                                          f"{d.get('backup') or 'nothing (started empty)'}."),
    }
    return texts.get(kind, (kind, ""))


def _bad_files(rows: Sequence[StatusRow], offset_ms: int) -> list[tuple[StatusRow, str, str]]:
    """The per-hour "bad file" lines, one alert per market (D32).

    Args:
        rows: Lines of kind bad_file.
        offset_ms: Local time minus UTC, ms.

    Returns:
        (a representative line, title, text) per market, by market.
    """
    by_market: dict[str, list[StatusRow]] = {}
    for r in rows:
        by_market.setdefault(r.data.get("market", ""), []).append(r)
    out = []
    for market in sorted(by_market):
        group = by_market[market]
        hours = [r.data["hour"] for r in group]
        spans = ", ".join(ms_range(a * HOUR_MS, (b + 1) * HOUR_MS, offset_ms) for a, b in hour_ranges(hours))
        tries = min(r.data.get("failures", 0) for r in group)
        text = (f"{len(hours)} hours of {market} failed {tries} times: {spans}. AHSync retries them every run; "
                f"usually AHF simply has no file for those hours.")
        out.append((min(group, key=lambda r: r.topic), "AHF files failing", text))
    return out


def _stale(m: MarketFacts, now_ms: int, offset_ms: int) -> tuple[str, str] | None:
    """The alert for primary-source data that has stopped arriving (D33, SRC2).

    Args:
        m: The market.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        (title, text), or None while the newest price is at most 6 h old.
    """
    name = source_label(m.primary)
    if m.newest_price_ms is None:
        return f"No {name} prices yet", f"{m.market}: AHSync has no prices for this market yet."
    if now_ms - m.newest_price_ms <= STALE_MS:
        return None
    return (f"{name} data is old",
            f"{m.market}: {name}'s newest price is from {when(m.newest_price_ms, offset_ms)} "
            f"({age_text(now_ms - m.newest_price_ms)} ago). Prices, sales and windows rest on that; your own AH scans "
            f"are the fresh source until {name} has new data.")


def alerts(rows: Sequence[StatusRow], facts: StatusFacts, now_ms: int, offset_ms: int) -> list[dict]:
    """Everything that needs attention, most serious first (D32-D35).

    Args:
        rows: Stored Status lines.
        facts: Sync facts, for the stale-data alert.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        [{"level", "title", "text"}], ordered error, warn, info; ties by title, then topic.
    """
    items = [(r, *message(r, offset_ms)) for r in rows if r.kind not in ("bad_file", "premium_until")]
    items += _bad_files([r for r in rows if r.kind == "bad_file"], offset_ms)
    for m in facts.markets:
        stale = _stale(m, now_ms, offset_ms)
        if stale is not None:
            items.append((StatusRow(f"stale:{m.market}", "warn", "stale", {}, now_ms), *stale))
    items += [(r, *message(r, offset_ms)) for r in rows if r.kind == "premium_until" and r.level == "warn"]
    items.sort(key=lambda x: (LEVEL_ORDER.get(x[0].level, 3), x[1], x[0].topic))
    return [{"level": r.level, "title": title, "text": text} for r, title, text in items]


def _market_card(m: MarketFacts, now_ms: int, offset_ms: int) -> dict:
    """One market's sync card (D33).

    Args:
        m: The market.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"title", "badge", "badge_class", "lines": [[label, value, class]]}.
    """
    fresh = m.newest_price_ms is not None and now_ms - m.newest_price_ms <= STALE_MS
    badge = "up to date" if fresh else "no data" if m.newest_price_ms is None else \
        f"data {age_text(now_ms - m.newest_price_ms)} old"
    price = "—" if m.newest_price_ms is None else \
        f"{when(m.newest_price_ms, offset_ms)} · {age_text(now_ms - m.newest_price_ms)} ago"
    history = "—" if m.oldest_candle_ms is None or m.newest_candle_ms is None else \
        ms_range(m.oldest_candle_ms, m.newest_candle_ms + HOUR_MS, offset_ms)
    return {"title": m.market, "badge": badge, "badge_class": "ok" if fresh else "warn", "lines": [
        ["Last sync run", "—" if m.last_run_ms is None else when(m.last_run_ms, offset_ms), ""],
        ["Hours handled up to", "—" if m.last_hour is None else when(m.last_hour * HOUR_MS, offset_ms), ""],
        [f"Newest {source_label(m.primary)} price", price, "" if fresh else "amber"],
        ["Items with prices", f"{m.items_priced:,}", ""],
        ["Hourly history stored", history, ""],
        ["Your scans (7 d)", str(m.local_scans_7d), ""],
        [f"{source_label(m.primary)} order books (7 d)", str(m.primary_books_7d), "" if m.primary_books_7d else "muted"],
        *_backtest_lines(m.backtests),
    ]}


def _backtest_lines(backtests: tuple[tuple[str, Backtest], ...]) -> list[list[str]]:
    """The market card's estimate checks: your scans' sales against each confirmed source (SPEC §8.8, K4).

    Args:
        backtests: (confirmed source, Backtest), by source.

    Returns:
        One line per source with overlap; one muted line when there is none.
    """
    lines = []
    for source, b in backtests:
        if b.days:
            tenths = round_half_up(b.median_ratio * 10)
            share = round_half_up(100 * b.within / b.days)
            lines.append([f"Your scans vs {source} (30 d)", f"median {tenths // 10}.{tenths % 10} · {b.within} of {b.days} "
                          f"item-days ({share}%) within ±25%", "" if share >= 50 else "amber"])
    return lines or [["Your scans vs confirmed sales", "no confirmed sales to compare with yet", "muted"]]


def _api_card(rows: Sequence[StatusRow], facts: StatusFacts, offset_ms: int) -> dict:
    """The API card: key and Premium (D36).

    Args:
        rows: Stored Status lines.
        facts: The facts.
        offset_ms: Local time minus UTC, ms.

    Returns:
        A card dict.
    """
    premium = next((r for r in rows if r.kind in ("premium_until", "premium_ended")), None)
    badge, line, cls = "Public data", "none", "muted"
    if premium is not None and premium.kind == "premium_until":
        badge = f"Premium until {day(premium.data['until_ms'], offset_ms)}"
        line, cls = f"until {day(premium.data['until_ms'], offset_ms)} · upload scans to keep it", \
            "amber" if premium.level == "warn" else ""
    elif premium is not None:
        line = f"ended {day(premium.data.get('ended_ms', premium.at_ms), offset_ms)}"
    return {"title": "API", "badge": badge, "badge_class": "gold" if premium and premium.kind == "premium_until" else "",
            "lines": [["API key", "set" if facts.key_set else "not set · public data only", "" if facts.key_set else "muted"],
                      ["Premium", line, cls]]}


def _characters_card(facts: StatusFacts, now_ms: int) -> dict:
    """Your characters and the market each maps to (D34).

    Args:
        facts: The facts.
        now_ms: Now.

    Returns:
        A card dict; a character without a market is amber.
    """
    lines = []
    for c in sorted(facts.characters, key=lambda c: c.char_id):
        seen = "" if c.updated_ms is None else f" · seen {age_text(now_ms - c.updated_ms)} ago"
        lines.append([f"{c.char_id.split('-')[0]} ({c.realm}, {c.faction})",
                      (c.market or "no market · realm not in config.toml") + seen, "" if c.market else "amber"])
    return {"title": "Characters", "badge": f"{len(lines)}", "badge_class": "",
            "lines": lines or [["No characters yet", "log in with AHProfit enabled, then /reload", "muted"]]}


def _pct(x: float) -> int:
    """A share as whole percent.

    Args:
        x: In [0, 1].

    Returns:
        Percent, half up.
    """
    return round_half_up(x * 100)


def profit_line(sums: ProfitSums | None) -> str:
    """The calibration section's profit line: how the plan's expected profit held up (SPEC §8.8, K7).

    Args:
        sums: Expected and realized over the last 90 days' resolved plan events, or None.

    Returns:
        "Expected vs realized profit (last 90 days): realized +3s 50c of expected +4s (88%)"; "" when nothing
        is resolved.
    """
    if sums is None or not sums.resolved:
        return ""
    return (f"Expected vs realized profit (last 90 days): realized {signed_money(sums.realized)} of expected "
            f"{signed_money(sums.expected)} ({ratio_text(sums)})")


def calibration_view(c: Calibration | None, profit: ProfitSums | None = None) -> dict | None:
    """The Status page's calibration section: sell probability (SPEC §8.8, K6) and expected profit (K7).

    Args:
        c: The calibration, or None.
        profit: Expected against realized profit, or None.

    Returns:
        {"title", "brier", "warnings": [text], "rows": [{"bucket", "posts", "predicted", "actual", "off", "bad",
        "predicted_pct", "actual_pct"}], "profit": line or ""}; title, brier and rows empty without resolved posts;
        None when there is neither.
    """
    line = profit_line(profit)
    if c is None or not c.posts:
        return {"title": "", "brier": "", "warnings": [], "rows": [], "profit": line} if line else None
    rows, warnings = [], []
    for b in c.buckets:
        name = f"{_pct(b.low)}–{_pct(b.high)}%"
        off = _pct(b.actual) - _pct(b.predicted)
        rows.append({"bucket": name, "posts": str(b.posts), "predicted": f"{_pct(b.predicted)}%",
                     "actual": f"{_pct(b.actual)}%", "off": f"{'+' if off > 0 else '−' if off < 0 else ''}{abs(off)} pts",
                     "bad": b.off_target, "predicted_pct": _pct(b.predicted), "actual_pct": _pct(b.actual)})
        if b.off_target:
            warnings.append(f"Posts predicted {name} actually sold {_pct(b.actual)}% ({b.posts} posts). Chances in that "
                            f"range are too {'optimistic' if off < 0 else 'pessimistic'}.")
    brier = round_half_up(c.brier * 100)
    return {"title": f"Predicted vs actual (last 90 days, {c.posts} resolved posts)",
            "brier": f"Brier score {brier // 100}.{brier % 100:02d} · always guessing 50% would score 0.25",
            "warnings": warnings, "rows": rows, "profit": line}


def status_view(rows: Sequence[StatusRow], facts: StatusFacts, now_ms: int, offset_ms: int) -> dict:
    """The Status page (SPEC §3.5, D32-D36).

    Args:
        rows: Stored Status lines.
        facts: Sync, character and storage facts.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"alerts": [...], "cards": [...], "calibration": section or None}: market cards in config order, then API,
        Characters, Storage.
    """
    megabytes = facts.db_bytes * 10 // 1_048_576
    storage = {"title": "Storage", "badge": "", "badge_class": "", "lines": [
        ["Database", f"data/ahsync.db · {megabytes // 10}.{megabytes % 10} MB", ""],
        ["Retention", f"{facts.retention_days} days", ""]]}
    cards = [_market_card(m, now_ms, offset_ms) for m in facts.markets]
    cards += [_api_card(rows, facts, offset_ms), _characters_card(facts, now_ms), storage]
    return {"alerts": alerts(rows, facts, now_ms, offset_ms), "cards": cards,
            "calibration": calibration_view(facts.calibration, facts.profit)}
