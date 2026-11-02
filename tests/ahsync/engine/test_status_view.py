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
    ("volume_method_old", {"market": "m", "days": 5}, "Older sales estimates", "m: 5 days of sales estimates"),
    ("candidate_export_trimmed", {"kept": 112, "total": 150, "limit": 2_097_152}, "Candidate export trimmed",
     "candidate export trimmed to 112 of 150 to fit 2 MB"),
    ("candidate_export_trimmed", {"kept": 1, "total": 2, "limit": 204_800}, "Candidate export trimmed", "to fit 200 KB"),
    ("risk_tolerance_not_saved", {"error": "PermissionError"}, "Risk tolerance not saved",
     "couldn't rewrite config.toml (PermissionError)"),
    ("api_key_not_saved", {"reason": "permission denied"}, "API key not saved",
     "Enter it again, or set AUCTIONHOUSE_APIKEY."),
    ("autostart_not_set", {"error": "PermissionError"}, "Start at login not set",
     "The login entry couldn't be written (PermissionError). Turn it on again here or from the tray."),
    ("autostart_not_set", {}, "Start at login not set", "couldn't be written (?)"),
    ("window_unavailable", {}, "App window unavailable", "The dashboard opens in your browser."),
    ("gathering_catalog", {"text": "gathering catalog: 1 row skipped (Mithril Ore: unknown profession 'Minning')"},
     "Gathering catalog", "1 row skipped (Mithril Ore"),
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
    card = status_view.status_view([], facts(market(primary_books_7d=3)), NOW, 0)["cards"][0]
    assert card["lines"] == [
        ["Last sync run", "Sat 17 Oct 22:05", ""], ["Hours handled up to", "Sat 17 Oct 22:00", ""],
        ["Newest AHF price", "Sat 17 Oct 21:30 · 1 h ago", ""], ["Items with prices", "8,929", ""],
        ["Hourly history stored", "Thu 17 Sep 23:00–Sat 17 Oct 22:00", ""], ["Your scans (7 d)", "6", ""],
        ["AHF order books (7 d)", "3", ""], ["Your scans vs confirmed sales", "no confirmed sales to compare with yet", "muted"]]
    assert status_view.hour_ranges([5, 3, 4, 9]) == [(3, 5), (9, 9)]


def test_K4_the_market_card_compares_your_scan_estimates_with_each_confirmed_source():
    from ahsync.engine.volume import backtest
    k4 = backtest([0.8] * 4 + [1.0] * 4 + [1.5] * 2)
    card = status_view.status_view([], facts(market(backtests=(("ahf", k4), ("blizzard", backtest([])),
                                                                ("mail", backtest([2.0, 3.0]))))), NOW, 0)["cards"][0]
    assert card["lines"][-2:] == [["Your scans vs ahf (30 d)", "median 1.0 · 8 of 10 item-days (80%) within ±25%", ""],
                                  ["Your scans vs mail (30 d)", "median 2.5 · 0 of 2 item-days (0%) within ±25%", "amber"]]


def test_K6_status_shows_calibration_and_its_warnings():
    from ahsync.engine.sell_record import calibration
    c = calibration([(0.8, True)] * 8 + [(0.8, False)] * 2 + [(0.5, True)] * 7 + [(0.5, False)] * 18)
    section = status_view.status_view([], facts(calibration=c), NOW, 0)["calibration"]
    assert section["title"] == "Predicted vs actual (last 90 days, 35 resolved posts)"
    assert section["brier"] == "Brier score 0.22 · always guessing 50% would score 0.25"     # (1.6 + 6.25) ÷ 35
    assert [(r["bucket"], r["posts"], r["predicted"], r["actual"], r["off"], r["bad"]) for r in section["rows"]] == [
        ("40–60%", "25", "50%", "28%", "−22 pts", True), ("80–100%", "10", "80%", "80%", "0 pts", False)]
    assert section["warnings"] == ["Posts predicted 40–60% actually sold 28% (25 posts). Chances in that range are too "
                                   "optimistic."]
    assert status_view.status_view([], facts(calibration=calibration([])), NOW, 0)["calibration"] is None
    assert status_view.status_view([], facts(), NOW, 0)["calibration"] is None
    high = status_view.calibration_view(calibration([(0.1, True)] * 20))
    assert high["rows"][0]["off"] == "+90 pts" and high["warnings"][0].endswith("too pessimistic.")


def test_K7_status_sets_the_plans_expected_profit_against_what_it_realized():
    from ahsync.engine.sell_record import calibration
    from ahsync.engine.tracking import ProfitSums
    sums = ProfitSums(567.0, 407.0, 2, 1, 2)                                        # D70's resolved events
    c = calibration([(0.8, True)] * 8 + [(0.8, False)] * 2)
    section = status_view.status_view([], facts(calibration=c, profit=sums), NOW, 0)["calibration"]
    assert section["profit"] == "Expected vs realized profit (last 90 days): realized +4s 7c of expected +5s 67c (72%)"
    alone = status_view.status_view([], facts(profit=sums), NOW, 0)["calibration"]
    assert alone == {"title": "", "brier": "", "warnings": [], "rows": [], "profit": section["profit"]}
    assert status_view.status_view([], facts(profit=ProfitSums(0.0, 0.0, 0, 3, 0)), NOW, 0)["calibration"] is None
    assert status_view.calibration_view(c)["profit"] == ""


def test_SRC2_status_names_the_markets_primary_source():
    card = status_view.status_view([], facts(market(primary="blizzard", primary_books_7d=2)), NOW, 0)["cards"][0]
    labels = [line[0] for line in card["lines"]]
    assert "Newest blizzard price" in labels and "blizzard order books (7 d)" in labels
    stale = market(primary="blizzard", newest_price_ms=NOW - 7 * HOUR)
    [alert] = [a for a in status_view.status_view([], facts(stale), NOW, 0)["alerts"] if "data is old" in a["title"]]
    assert alert["title"] == "blizzard data is old" and "until blizzard has new data" in alert["text"]
    none = market(primary="blizzard", newest_price_ms=None)
    assert [a["title"] for a in status_view.status_view([], facts(none), NOW, 0)["alerts"]] == ["No blizzard prices yet"]


def test_GA34_an_info_line_lists_the_loot_the_gathering_catalog_cannot_place():
    text = "Rough Stone 22, Ruined Leather Scraps 13, Broken Fang 7, Chipped Claw 6, Light Hide 2"
    line = row("gathering_unplaced", {"text": text}, "info", f"gathering_unplaced:{M}")
    alert = status_view.alerts([line], facts(), NOW, 0)[0]
    assert alert["level"] == "info" and f"{alert['title']}: {alert['text']}" == (
        "Gathered, not in the catalog: Rough Stone 22, Ruined Leather Scraps 13, Broken Fang 7, Chipped Claw 6, "
        "Light Hide 2")



# ---------------------------------------------------------------- AHledger (M19)

AHL = "https://ahledger.com"
WED_21 = 497_940 * HOUR + 20 * MINUTE                   # Wed 21 Oct 2026 12:20 UTC (hour 497940 starts at 12:00)


def test_S18_the_market_card_shows_the_ahledger_tables_age_and_size():
    def line(now: int, **changes) -> list[str]:
        card = status_view.status_view([], facts(market(ahledger_on=True, **changes)), now, 0)["cards"][0]
        return [l for l in card["lines"] if l[0] == "AHledger table"][0]
    updated = {"ahledger_updated_ms": WED_21, "ahledger_items": 2207}
    assert line(WED_21 + 26 * HOUR, **updated) == ["AHledger table", "26 h old · 2,207 items", "amber", AHL]       # Thu 14:20
    assert line(WED_21 + 24 * HOUR, **updated) == ["AHledger table", "24 h old · 2,207 items", "", AHL]           # Thu 12:20
    assert line(WED_21 + 40 * MINUTE, **updated) == ["AHledger table", "40 min old · 2,207 items", "", AHL]       # Wed 13:00
    assert line(WED_21 + 50 * HOUR, **updated) == ["AHledger table", "2 d old · 2,207 items", "amber", AHL]       # Fri 14:20
    assert line(WED_21) == ["AHledger table", "not fetched yet", "muted", AHL]
    off = status_view.status_view([], facts(market(ahledger_on=False)), NOW, 0)["cards"][0]
    assert "AHledger table" not in [l[0] for l in off["lines"]]                    # no AHledger address: no line


def test_a_tables_age_is_in_minutes_then_hours_until_48_h_then_whole_days():
    from ahsync.engine.views import table_age_text
    assert [table_age_text(a) for a in (30_000, 40 * MINUTE, 59 * MINUTE, HOUR, 47 * HOUR + 59 * MINUTE, 48 * HOUR,
                                        50 * HOUR, 5 * DAY)] == ["now", "40 min", "59 min", "1 h", "47 h", "2 d", "2 d", "5 d"]


def test_the_ahledger_alerts_say_what_happened_and_carry_the_ahledger_link():
    wed_14 = 497_940 * HOUR + 2 * HOUR          # Wed 21 Oct 14:00 UTC
    rows = [StatusRow(f"ahledger:unreachable:{M}", "warn", "ahledger_unreachable",
                      {"market": M, "since_ms": wed_14, "tries": 3}, NOW),               # FM43
            StatusRow("format:ahledger_pricetable", "warn", "ahledger_format", {"endpoint": "pricetable"}, NOW),   # FM44
            StatusRow(f"ahledger:no_market:{M}", "warn", "ahledger_no_market", {"market": M}, NOW)]   # FM46
    alerts = status_view.alerts(rows, facts(), NOW, 0)
    assert sorted(a["text"] for a in alerts) == [
        "AHledger format changed: pricetable", "AHledger has no market forever.pvp.horde.us",
        "AHledger: unreachable since Wed 14:00 (3 tries)"]
    assert all(a["level"] == "warn" and a["url"] == AHL for a in alerts)
    assert "url" not in status_view.alerts([row("refresh_failed", {"error": "x"})], facts(), NOW, 0)[0]


def test_the_stale_alert_of_an_ahledger_primary_carries_the_link_and_names_ahledger():
    old = market(primary="ahledger", newest_price_ms=NOW - 7 * HOUR)
    [alert] = status_view.alerts([], facts(old), NOW, 0)
    assert alert["title"] == "AHledger data is old" and alert["url"] == AHL
    [ahf] = status_view.alerts([], facts(market(newest_price_ms=NOW - 7 * HOUR)), NOW, 0)
    assert "url" not in ahf


def test_D95_the_status_page_carries_the_data_coverage_card_after_the_summary_cards():
    from pathlib import Path
    hours = {"local": [NOW // HOUR - 1, NOW // HOUR - 25], "ahf": list(range(NOW // HOUR - 200, NOW // HOUR - 100)),
             "ahledger_h1": [NOW // HOUR - 5]}
    on = market(coverage_hours=hours, coverage_sources=("local", "ahledger", "ahf"))
    [card] = status_view.status_view([], facts(on), NOW, 0)["coverage"]
    assert card["market"] == M and card["title"] == "Data coverage"
    assert card["sub"] == "last 90 days to Sat 17 Oct 23:00 · your scans, AHledger and auctionhouse.fyi"
    assert card["heading"] == "Weeks with data per hour of the week, local time"
    assert card["reading"] == ("Coverage is the hours any source observed; it is what the weekly patterns and sales "
                               "estimates are built from. Dark cells make reliable windows; pale cells are hours "
                               "nobody has scanned yet.")
    assert len(card["cells"]) == 7 and all(len(row) == 24 for row in card["cells"])
    assert all(c["title"] and 0 <= c["step"] <= 5 for row in card["cells"] for c in row)
    assert [r["src"] for r in card["sources"]] == ["local", "ahledger", "ahf"]
    # without AHledger (address ""): the subtitle names two sources and the table has no ahledger row
    off = market(coverage_hours=hours, coverage_sources=("local", "ahf"))
    [bare] = status_view.status_view([], facts(off), NOW, 0)["coverage"]
    assert bare["sub"].endswith("· your scans and auctionhouse.fyi")
    assert [r["src"] for r in bare["sources"]] == ["local", "ahf"]
    # the page draws the cards after the summary cards and before the calibration section, computing nothing
    page = Path(status_view.__file__).parents[1].joinpath("web", "static", "index.html").read_text(encoding="utf-8")
    order = [page.index(text) for text in ("map(statusCard)", "(d.coverage || []).map(covCard)", "calibrationHtml(d.calibration)")]
    assert order == sorted(order)
    assert "const covCard" in page and "COV_STRENGTH = [6, 22, 40, 58, 78, 100]" in page


def test_ahledger_backfill_line_counts_items_and_says_when_it_resumes():
    running = row("ahledger_backfill", {"market": M, "done": 1200, "total": 2207, "paused": False}, level="info",
                  topic=f"ahledger:backfill:{M}")
    paused = row("ahledger_backfill", {"market": M, "done": 1200, "total": 2207, "paused": True}, level="info",
                 topic=f"ahledger:backfill:{M}")
    assert status_view.message(running, 0) == ("AHledger backfill", "AHledger backfill: 1,200 of 2,207 items")
    assert status_view.message(paused, 0) == ("AHledger backfill",
                                              "AHledger backfill: 1,200 of 2,207 items · resumes at the next run")
    alerts = status_view.alerts([paused, row("refresh_failed", {"error": "x"})], facts(), NOW, 0)
    assert [a["level"] for a in alerts] == ["warn", "info"]                  # info sorts after warn
    assert alerts[1]["url"] == AHL and alerts[1]["url_label"] == "AHledger"
