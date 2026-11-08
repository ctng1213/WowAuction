"""Tests for ahsync/engine/coverage.py: the Status page's Data coverage card (SPEC §3.5, D89-D94, D96)."""
from __future__ import annotations

import calendar
from collections import Counter

from ahsync.engine.coverage import age_text, analysed_until_hour, cloud_note, coverage, ranges_to_hours, share_text, span_text, step

HOUR = 3_600_000
END = calendar.timegm((2026, 10, 22, 14, 0, 0)) // 3600     # Thu 22 Oct 14:00, local written as UTC (the mockup's)
T0 = END - 2160                                              # Fri 24 Jul 14:00
SOURCES = ("local", "ahledger", "ahf")


def idx(month: int, day: int, hour: int) -> int:
    """The mockup's covIdx: an hour's index in the window."""
    return calendar.timegm((2026, month, day, hour, 0, 0)) // 3600 - T0


def slot(i: int) -> tuple[int, int, int, int]:
    """(day 0 = Monday, hour, month, day of month) of the window's hour i."""
    import time
    t = time.gmtime((T0 + i) * 3600)
    return t.tm_wday, t.tm_hour, t.tm_mon, t.tm_mday


def back(i: int) -> int:
    """The mockup's covBack: whole days from the hour's date to 22 Oct."""
    return END // 24 - (T0 + i) // 24


def main_world(src: str, i: int) -> bool:
    """The mockup's COV_MAIN, ported line for line."""
    di, h, month, day = slot(i)
    b = back(i)
    if month == 10 and 4 <= day <= 6:
        return False
    if src == "ahf":
        return i <= idx(10, 9, 13) and (h >= 12 or (h >= 6 and b % 5 == 0))
    if src == "ahledger":
        return b < 56 and i <= idx(10, 21, 12) and (8 <= h <= 18 if di < 5 else 10 <= h <= 20)
    return (b < 61 and (b // 7) % 4 != 2 and i <= idx(10, 21, 22) and i != idx(10, 20, 20)
            and ((1 <= di <= 3 and 19 <= h <= 22) or (di >= 5 and (15 <= h <= 22 or h <= 1))))


def healthy_world(src: str, i: int) -> bool:
    """The mockup's COV_HEALTHY, ported line for line."""
    di, h, _, _ = slot(i)
    b = back(i)
    if src == "ahf":
        return b % 11 != 5 and (not 2 <= h <= 6 or b % 3 == 0)
    if src == "ahledger":
        return 9 <= h <= 18 or (di >= 5 and h <= 6)
    return di >= 4 and 18 <= h <= 23


def hours_of(world) -> dict[str, list[int]]:
    """A world as observed_hours rows: src -> UTC hour numbers."""
    return {s: [T0 + i for i in range(2160) if world(s, i)] for s in SOURCES}


def cell(card: dict, day: str, hour: int) -> dict:
    """One heatmap cell by day name and hour."""
    return card["cells"][card["days"].index(day)][hour]


def rows(card: dict) -> dict[str, dict]:
    """The table's source rows by src."""
    return {r["src"]: r for r in card["sources"]}


PARTIAL = (calendar.timegm((2026, 10, 20, 20, 15, 0)) * 1000, 0.4)   # Tue 20 Oct 20:15, closed at 40%


def test_D89_the_heatmap_of_the_mockups_world():
    card = coverage(hours_of(main_world), END, 0, SOURCES, PARTIAL)
    expect = {("Tue", 19): ("Tue 19:00 · 12 of 13 weeks · local 7, ahledger 0, ahf 10", 4),
              ("Tue", 20): ("Tue 20:00 · 11 of 13 weeks · local 6, ahledger 0, ahf 10", 4),
              ("Sat", 14): ("Sat 14:00 · 13 of 13 weeks · local 0, ahledger 8, ahf 11", 5),
              ("Mon", 9): ("Mon 09:00 · 8 of 13 weeks · local 0, ahledger 7, ahf 2", 3),
              ("Sun", 0): ("Sun 00:00 · 7 of 13 weeks · local 7, ahledger 0, ahf 0", 3),
              ("Thu", 3): ("Thu 03:00 · 0 of 13 weeks · local 0, ahledger 0, ahf 0", 0),
              ("Fri", 13): ("Fri 13:00 · 12 of 12 weeks · local 0, ahledger 8, ahf 11", 5)}
    for (day, hour), (title, shade) in expect.items():
        c = cell(card, day, hour)
        assert (c["title"], c["step"]) == (title, shade)
    flat = [c for day in card["cells"] for c in day]
    assert len(flat) == 168
    assert sum(c["value"] == 0 for c in flat) == 38 and sum((c["value"], c["possible"]) == (13, 13) for c in flat) == 21
    by_step = Counter(c["step"] for c in flat)
    assert [by_step[s] for s in range(6)] == [38, 18, 2, 26, 52, 32]   # 32 = 21 at 13 of 13 + 11 edge cells at 12 of 12
    assert sum((c["value"], c["possible"]) == (12, 12) for c in flat) == 11
    assert cell(card, "Tue", 19)["by_src"] == {"local": 7, "ahledger": 0, "ahf": 10}   # 17 by source, 12 in the union


def test_D90_the_table_of_the_mockups_world():
    card = coverage(hours_of(main_world), END, 0, SOURCES, PARTIAL)
    r = rows(card)
    assert [s["src"] for s in card["sources"]] == ["local", "ahledger", "ahf"]
    assert {k: r["local"][k] for k in ("label", "url", "hours_text", "share", "newest", "newest_class", "oldest")} == {
        "label": "your scans", "url": None, "hours_text": "209 h", "share": "10%", "newest": "16 h ago",
        "newest_class": "", "oldest": "60 d ago"}                       # 209 × 100 ÷ 2,160 = 9.7; 1,454 h
    assert (r["ahledger"]["label"], r["ahledger"]["url"]) == ("AHledger", "https://ahledger.com")
    assert (r["ahledger"]["hours_text"], r["ahledger"]["share"], r["ahledger"]["newest"],
            r["ahledger"]["oldest"]) == ("566 h", "26%", "26 h ago", "55 d ago")      # Wed 12:00; 1,326 h
    assert (r["ahf"]["label"], r["ahf"]["hours_text"], r["ahf"]["share"], r["ahf"]["newest"], r["ahf"]["newest_class"],
            r["ahf"]["oldest"]) == ("auctionhouse.fyi", "978 h", "45%", "13 d ago", "amber", "90 d ago")   # 313 h > 72
    assert card["union"] == {"hours": 1268, "hours_text": "1,268 h", "share": "59%"}             # 58.7
    none = card["none"]
    assert (none["hours"], none["hours_text"], none["share"]) == (892, "892 h", "41%")
    assert none["gap_text"] == "longest gap 3 d 6 h (4 Oct 00:00 – 7 Oct 06:00)"
    assert none["longest_gap"] == {"hours": 78, "from": (T0 + idx(10, 4, 0)) * HOUR, "to": (T0 + idx(10, 7, 6)) * HOUR}
    assert card["note"] == ('of the 2,160 hours in the last 90 days; sources overlap, so "any source" is less than '
                            'their sum')
    assert card["sub"] == "last 90 days to Thu 22 Oct 14:00 · your scans, AHledger and auctionhouse.fyi"
    assert card["window"] == {"from": T0 * HOUR, "to": END * HOUR, "hours": 2160}


def test_D91_day_one():
    card = coverage({"local": [END - 1]}, END, 0, SOURCES, None)     # one complete scan at Thu 22 Oct 13:20
    flat = [c for day in card["cells"] for c in day]
    assert cell(card, "Thu", 13)["title"] == "Thu 13:00 · 1 of 13 weeks · local 1, ahledger 0, ahf 0"
    assert cell(card, "Thu", 13)["step"] == 1 and sum(c["value"] for c in flat) == 1
    r = rows(card)
    assert (r["local"]["hours_text"], r["local"]["share"], r["local"]["newest"], r["local"]["oldest"]) == (
        "1 h", "<1%", "1 h ago", "1 h ago")
    for src in ("ahledger", "ahf"):
        assert (r[src]["hours_text"], r[src]["share"], r[src]["newest"], r[src]["oldest"]) == ("0 h", "0%", "—", "—")
    assert (card["union"]["hours_text"], card["union"]["share"]) == ("1 h", "<1%")
    assert (card["none"]["hours_text"], card["none"]["share"]) == ("2,159 h", ">99%")
    assert card["none"]["gap_text"] == "longest gap 89 d 23 h (24 Jul 14:00 – 22 Oct 13:00)"


def test_D92_shading_by_fraction_of_the_weeks_possible():
    card = coverage({}, END, 0, SOURCES, None)
    twelve = {(d, h) for d, day in enumerate(card["cells"]) for h, c in enumerate(day) if c["possible"] == 12}
    assert len(twelve) == 24                                         # 2,160 = 12 × 168 + 144
    assert twelve == {(3, h) for h in range(14, 24)} | {(4, h) for h in range(14)}   # Thu 14:00 to Fri 13:00
    assert [step(v, 13) for v in (0, 1, 3, 4, 6, 7, 9, 10, 12, 13)] == [0, 1, 1, 2, 2, 3, 3, 4, 4, 5]
    assert [step(v, 12) for v in (3, 6, 9, 11, 12)] == [1, 2, 3, 4, 5]   # 25%, 50%, 75% stay in the lower step
    assert [(s["label"], s["step"]) for s in card["scale"]] == [
        ("0", 0), ("up to 25%", 1), ("up to 50%", 2), ("up to 75%", 3), ("under 100%", 4), ("100%", 5)]
    assert card["scale_note"] == "of the weeks possible"
    full = coverage({"ahf": range(END - 2160, END)}, END, 0, SOURCES, None)
    assert cell(full, "Fri", 13)["title"].startswith("Fri 13:00 · 12 of 12 weeks") and cell(full, "Fri", 13)["step"] == 5
    assert full["none"]["gap_text"] == "no gap" and full["none"]["longest_gap"] is None
    assert full["union"]["share"] == "100%"


def test_D93_only_complete_observations_count():
    tue_20, tue_21, tue_22, tue_23 = (END - 42 + k for k in range(4))  # Tue 20 Oct 20:00 ...
    # 20:00 had only a partial scan (not in observed_hours) and 21:00 only an empty AHF file (likewise); 22:00 an
    # AHledger table and an ahledger_h1 bucket; 23:00 a complete local scan and an AHF file
    card = coverage({"ahledger": [tue_22], "ahledger_h1": [tue_22], "local": [tue_23], "ahf": [tue_23]},
                    END, 0, SOURCES, PARTIAL)
    r = rows(card)
    assert (r["ahledger"]["hours"], r["local"]["hours"], r["ahf"]["hours"], card["union"]["hours"]) == (1, 1, 1, 2)
    assert cell(card, "Tue", 20)["value"] == cell(card, "Tue", 21)["value"] == 0
    assert card["rule"].endswith("Never a partial scan (Tue 20:15, closed at 40%, is not counted).")
    assert coverage({}, END, 0, SOURCES, None)["rule"].endswith("Never a partial scan.")
    assert tue_20 + 1 == tue_21


def test_D94_shares_ages_gaps_and_the_amber_newest():
    assert [share_text(h) for h in (0, 1, 10, 11, 2149, 2150, 2160)] == [
        "0%", "<1%", "<1%", "1%", "99%", ">99%", "100%"]
    assert [age_text(h) for h in (1, 47, 48, 71, 72)] == ["1 h ago", "47 h ago", "2 d ago", "2 d ago", "3 d ago"]
    assert [span_text(h) for h in (47, 48, 78)] == ["47 h", "2 d", "3 d 6 h"]
    tail = coverage({"local": [END - 2160]}, END, 0, SOURCES, None)
    assert tail["none"]["gap_text"].endswith(" – now)")              # one reaching the window's end
    for hours, amber in ((72, ""), (73, "amber")):
        assert rows(coverage({"ahf": [END - hours]}, END, 0, SOURCES, None))["ahf"]["newest_class"] == amber
    assert rows(coverage({"local": [END - 100]}, END, 0, SOURCES, None))["local"]["newest_class"] == ""


def test_D96_the_mockups_healthy_world():
    card = coverage(hours_of(healthy_world), END, 0, SOURCES, None)
    r = rows(card)
    assert (r["local"]["hours_text"], r["local"]["share"], r["local"]["newest"], r["local"]["newest_class"],
            r["local"]["oldest"]) == ("234 h", "11%", "3 d ago", "", "89 d ago")     # Sun 18 Oct 23:00, 87 h
    assert (r["ahledger"]["hours_text"], r["ahledger"]["share"], r["ahledger"]["newest"], r["ahledger"]["oldest"]) == (
        "1,082 h", "50%", "1 h ago", "90 d ago")
    assert (r["ahf"]["hours_text"], r["ahf"]["share"], r["ahf"]["newest"], r["ahf"]["oldest"]) == (
        "1,698 h", "79%", "1 h ago", "90 d ago")
    assert (card["union"]["hours_text"], card["union"]["share"]) == ("1,899 h", "88%")
    assert (card["none"]["hours_text"], card["none"]["share"]) == ("261 h", "12%")
    assert card["none"]["gap_text"] == "longest gap 9 h (12 Aug 00:00 – 12 Aug 09:00)"


def test_sources_order_aliases_others_and_the_local_offset():
    card = coverage({"blizzard": [END - 1], "ahledger_h1": [END - 2], "zeta": [END - 3], "old": [END - 5000]},
                    END, 2 * HOUR, ("zeta", "local", "blizzard", "ahledger_h1"), None)
    assert [s["src"] for s in card["sources"]] == ["local", "ahledger", "blizzard", "zeta"]
    assert card["sub"] == "last 90 days to Thu 22 Oct 16:00 · your scans, AHledger, Blizzard (cloud) and zeta"   # D99
    assert cell(card, "Thu", 15)["by_src"]["blizzard"] == 1          # 13:00 UTC is 15:00 local
    assert coverage({}, END, 0, ("local",), None)["sub"].endswith("· your scans")


def test_D99_a_cloud_markets_row_comes_from_the_summarys_coverage():
    start = calendar.timegm((2026, 11, 5, 14, 0, 0)) // 3600                # Thu 5 Nov 14:00
    gap = calendar.timegm((2026, 11, 6, 3, 0, 0)) // 3600                   # Fri 03:00-05:00 unobserved
    now = calendar.timegm((2026, 11, 7, 15, 0, 0)) // 3600
    document = ((start, gap - 1), (gap + 3, now - 1))                       # the summary's `coverage`, as ranges
    assert ranges_to_hours(document) == set(range(start, gap)) | set(range(gap + 3, now))
    card = coverage({"blizzard": ranges_to_hours(document)}, now, 0, ("local", "blizzard"), None)
    blizzard = rows(card)["blizzard"]
    assert blizzard["label"] == "Blizzard (cloud)" and blizzard["hours"] == now - start - 3
    assert card["union"]["hours"] == blizzard["hours"] and rows(card)["local"]["hours"] == 0
    assert card["none"]["gap_text"] == "longest gap 87 d 23 h (9 Aug 15:00 – 5 Nov 14:00)"   # before the first hour, as D91
    assert [cell(card, "Fri", h)["by_src"]["blizzard"] for h in (2, 3, 4, 5, 6)] == [1, 0, 0, 0, 1]   # the 3 h hole


def test_D104_a_cloud_markets_note_says_how_far_collection_runs_ahead_of_analysis():
    pacific = -8 * HOUR
    prices = 1_794_111_360_000                                 # 2026-11-08 04:16 UTC = Sat 20:16 Pacific
    analysed = analysed_until_hour([(1_793_491_200_000 // HOUR, 1_794_060_000_000 // HOUR - 1)])   # to 13:00
    assert analysed * HOUR == 1_794_060_000_000            # 2026-11-07 14:00 UTC = Sat 06:00 Pacific
    now = 1_794_112_200_000                                    # Sat 20:30 Pacific
    assert cloud_note(prices, analysed, now, pacific) == (
        "Blizzard data collected to Sat 20:16 · analysed to Sat 06:00 · next analysis Sun 01:00")
    sun_0900_utc = 1_794_128_400_000                          # 2026-11-08 09:00 UTC
    assert cloud_note(prices, analysed, sun_0900_utc - 60_000, pacific).endswith("next analysis Sun 01:00")
    assert cloud_note(prices, analysed, sun_0900_utc, pacific).endswith("next analysis Mon 01:00")   # strictly after now
    assert cloud_note(prices, None, now, pacific) == (
        "Blizzard data collected to Sat 20:16 · analysed to — · next analysis Sun 01:00")
    assert cloud_note(None, analysed, now, pacific) is None and analysed_until_hour([]) is None
