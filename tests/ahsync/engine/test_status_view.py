"""Tests for ahsync/engine/status_view.py: the Status page (SPEC §3.5)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.engine import status_view
from ahsync.engine.status_view import CharacterFacts, MarketFacts, StatusFacts, StatusRow

HOUR, DAY, MINUTE = 3_600_000, 86_400_000, 60_000
M, OTHER = "forever.pvp.horde.us", "forever.pvp.alliance.us"
FRI_9 = 497640 * HOUR                                  # Fri 9 Oct 2026 00:00 UTC
NOW = FRI_9 + 8 * DAY + 23 * HOUR                      # Sat 17 Oct 23:00 UTC


def market(**changes) -> MarketFacts:
    """A market synced at 22:05 with prices from 21:30, 30 days of hourly history and 6 scans of yours."""
    base = MarketFacts(M, NOW // HOUR - 1, NOW - 55 * MINUTE, NOW - 90 * MINUTE, 8929, NOW - 30 * DAY, NOW - 2 * HOUR, 6, 0)
    return replace(base, **changes)


def facts(*markets: MarketFacts, **changes) -> StatusFacts:
    """Status facts: one Horde character on M, a key set, a 46.2 MB database."""
    base = StatusFacts(markets or (market(),), (CharacterFacts("Amaya-Grommash", "Grommash", "Horde", M, NOW - 2 * HOUR),),
                       True, 48_444_211, 90)
    return replace(base, **changes)


def row(kind: str, data: dict, level: str = "warn", topic: str | None = None) -> StatusRow:
    """A stored Status line."""
    return StatusRow(topic or kind, level, kind, data, NOW - HOUR)


def test_D32_failing_hour_files_are_one_alert_per_market():
    rows = [row("bad_file", {"market": M, "hour": h, "failures": 4}, topic=f"bad_file:{M}:{h}")
            for h in range(497640, 497661)]
    [alert] = status_view.alerts(rows, facts(), NOW, 0)
    assert alert["title"] == "AHF files failing"
    assert alert["text"].startswith(f"21 hours of {M} failed 4 times: Fri 9 Oct 00:00–21:00. ")


def test_D33_ahf_data_older_than_6_hours_is_flagged_on_the_card_and_as_a_warning():
    old = market(newest_price_ms=FRI_9 + 21 * HOUR + 14 * MINUTE)
    fresh = market(market=OTHER)
    view = status_view.status_view([], facts(old, fresh), NOW, 0)
    [alert] = view["alerts"]
    assert (alert["level"], alert["title"]) == ("warn", "AHF data is old") and "Fri 9 Oct 21:14 (8 d ago)" in alert["text"]
    assert [(c["badge"], c["badge_class"]) for c in view["cards"][:2]] == [("data 8 d old", "warn"), ("up to date", "ok")]
    assert view["cards"][0]["lines"][2] == ["Newest AHF price", "Fri 9 Oct 21:14 · 8 d ago", "amber"]


def test_D34_an_unmapped_realm_says_how_to_fix_it_and_the_character_shows_no_market():
    rows = [row("unknown_realm", {"realm": "Classic Beta PvP", "faction": "Horde"}, topic="realm:Classic Beta PvP")]
    lost = CharacterFacts("Monstrscondm-Classic Beta PvP", "Classic Beta PvP", "Horde", None, NOW - DAY)
    view = status_view.status_view(rows, facts(characters=(lost,)), NOW, 0)
    [alert] = view["alerts"]
    assert alert["title"] == "Realm not mapped to a market"
    assert '[realms] "Classic Beta PvP" = "pvp"' in alert["text"] and alert["text"].endswith("then restart AHSync.")
    characters = next(c for c in view["cards"] if c["title"] == "Characters")
    assert characters["lines"] == [["Monstrscondm (Classic Beta PvP, Horde)",
                                    "no market · realm not in config.toml · seen 1 d ago", "amber"]]


def test_D35_alerts_are_ordered_error_then_warn_then_info():
    rows = [row("day_only", {"market": M, "ranges_ms": [[FRI_9, FRI_9 + 3 * HOUR]]}, "info"),
            row("format_changed", {"endpoint": "h1"}, "error"), row("key_rejected", {}, "warn")]
    assert [a["title"] for a in status_view.alerts(rows, facts(), NOW, 0)] == [
        "AHF format changed", "API key rejected", "Day candles only"]


def test_D36_premium_shows_on_the_api_card_and_warns_near_its_end():
    until = NOW + 5 * DAY + HOUR                            # Fri 23 Oct
    rows = [row("premium_until", {"until_ms": until}, "warn", "premium")]
    view = status_view.status_view(rows, facts(key_set=False), NOW, 0)
    api = next(c for c in view["cards"] if c["title"] == "API")
    assert (api["badge"], api["badge_class"]) == ("Premium until 23 Oct", "gold")
    assert api["lines"] == [["API key", "not set · public data only", "muted"],
                            ["Premium", "until 23 Oct · upload scans to keep it", "amber"]]
    assert [a["text"] for a in view["alerts"]] == ["Premium until 23 Oct · upload scans to keep it"]
    calm = status_view.status_view([replace(rows[0], level="info")], facts(), NOW, 0)
    assert calm["alerts"] == [] and next(c for c in calm["cards"] if c["title"] == "API")["lines"][1][2] == ""


@pytest.mark.parametrize("kind, data, title, phrase", [
    ("refresh_failed", {"error": "OperationalError: disk full"}, "Refresh failed", "(OperationalError: disk full)"),
    ("refresh_items_failed", {"count": 2, "items": [f"{M}:1", f"{M}:2"]}, "Items left out", f"out: {M}:1, {M}:2"),
    ("lost", {"market": M, "ranges_ms": [[FRI_9, FRI_9 + DAY + HOUR]]}, "Hours lost", "Fri 9 Oct 00:00–Sat 10 Oct 01:00"),
    ("feature_refused", {"market": M, "endpoint": "books", "feature": "depth"}, "Premium feature refused", "books (depth)"),
    ("premium_ended", {"ended_ms": FRI_9}, "Premium ended", "ended 9 Oct"),
    ("long_gap", {"market": M, "days": 10}, "Long gap", "10 days ago"),
    ("unreachable", {"reason": "rate_limited"}, "AHF unreachable", "limiting requests"),
    ("unreachable", {"reason": "retries_exhausted"}, "AHF unreachable", "didn't answer"),
    ("market_skipped", {"market": M, "error": "ApiError"}, "Market skipped", "(ApiError)"),
    ("restored", {"backup": "ahsync-2026-10-16.db"}, "Database restored", "from ahsync-2026-10-16.db"),
    ("restored", {"backup": None}, "Database restored", "nothing (started empty)"),
    ("update_ahsync", {"account": "A#1"}, "Update AHSync", "A#1: SavedVariables"),
    ("savedvars_unreadable", {"account": "A#1"}, "SavedVariables unreadable", "read again"),
    ("savedvars_backup_used", {"account": "A#1"}, "Read from the backup", ".bak"),
    ("savedvars_failed", {"account": "A#1"}, "SavedVariables failed", "tried again"),
    ("savedvars_rows_skipped", {"account": "A#1", "count": 4, "rows": ["a", "b", "c", "d"]}, "Rows set aside", "First: a; b; c"),
    ("premium_until", {}, "Premium", "Premium"),
    ("addon_file_blocked", {"file": "MarketIntelligence.lua"}, "Addon file not written", "after 3 tries"),
    ("addon_file_large", {"file": "MarketIntelligence.lua", "bytes": 2_500_000, "limit": 2_097_152},
     "Addon file over budget", "2441 KB, over its 2048 KB"),
    ("something_new", {}, "something_new", ""),
])
def test_every_stored_kind_reads_as_a_sentence(kind, data, title, phrase):
    got_title, text = status_view.message(row(kind, data), 0)
    assert got_title == title and phrase in text


def test_no_prices_no_history_no_characters_and_a_premium_that_ended():
    view = status_view.status_view([row("premium_ended", {"ended_ms": FRI_9}, topic="premium")],
                                   facts(market(newest_price_ms=None, oldest_candle_ms=None, last_run_ms=None,
                                                last_hour=None), characters=()), NOW, 0)
    assert [a["title"] for a in view["alerts"]] == ["No AHF prices yet", "Premium ended"]
    card = view["cards"][0]
    assert card["badge"] == "no data" and [v for _, v, _ in card["lines"][:3]] == ["—", "—", "—"]
    assert card["lines"][4][1] == "—" and card["lines"][6] == ["AHF order books (7 d)", "0", "muted"]
    api, chars, storage = view["cards"][1:]
    assert (api["badge"], api["lines"][1][1]) == ("Public data", "ended 9 Oct")
    assert chars["lines"][0][0] == "No characters yet" and storage["lines"][0][1] == "data/ahsync.db · 46.1 MB"


def test_a_fresh_market_card():
    card = status_view.status_view([], facts(market(ahf_books_7d=3)), NOW, 0)["cards"][0]
    assert card["lines"] == [
        ["Last sync run", "Sat 17 Oct 22:05", ""], ["Hours handled up to", "Sat 17 Oct 22:00", ""],
        ["Newest AHF price", "Sat 17 Oct 21:30 · 1 h ago", ""], ["Items with prices", "8,929", ""],
        ["Hourly history stored", "Thu 17 Sep 23:00–Sat 17 Oct 22:00", ""], ["Your scans (7 d)", "6", ""],
        ["AHF order books (7 d)", "3", ""]]
    assert status_view.hour_ranges([5, 3, 4, 9]) == [(3, 5), (9, 9)]
