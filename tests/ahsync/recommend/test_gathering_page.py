"""Tests for ahsync/recommend/gathering_page.py: the Gathering payload from the catalog file, prices and professions."""
from __future__ import annotations

import json
from dataclasses import replace

from ahsync.config import Config
from ahsync.engine.sessions import sessions
from ahsync.history.reads import CurrentPrice, ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel

from ahsync.recommend import gathering_page as page
from tests.support import gathering_world as gw

HOUR = 3_600_000
COPPER_INTEL = ItemIntel(gw.COPPER, 220, 10, 0, 210, 200, 150, 260, 0.0, 30.0, 2.0, 40.0, "ahf", {"ahf": 7}, 220, 20,
                         0.85, False, 200.0, 80, 64, None, ((220, 10, 3, 1), (230, 54, 3, 1)),
                         closes_30d=30, volume_kind="ids")


class FakeDb:
    """Current prices, two characters' sheets and the Status lines."""

    def __init__(self):
        """Copper Ore seen by AHF 26 h ago; Tin Ore by your scan 12 min ago, nothing listed."""
        self.prices = {gw.COPPER: CurrentPrice(gw.COPPER, 220, 10, gw.AHF_SEEN, "ahf"),
                       gw.TIN: CurrentPrice(gw.TIN, None, 0, gw.NOW - 12 * 60_000, "local")}
        self.status: dict[str, tuple] = {}
        self.stored: list = []            # stored farming sessions
        self.events: list = []            # gather events after them
        self.manual: dict = {}
        self.sources: dict = {}           # item key -> the sources its gather events looted it from

    def looted_sources(self, char_ids):
        """The sources per item (the fake keeps one table for every character)."""
        return self.sources

    def farming_sessions(self, char_ids):
        """The stored sessions of these characters."""
        return [s for s in self.stored if s.char_id in char_ids]

    def events_after_sessions(self, char_ids):
        """The gather events after each character's last stored session."""
        return [e for e in self.events if e.char_id in char_ids]

    def manual_rates(self, char_ids):
        """The overrides of these characters."""
        return {k: v for k, v in self.manual.items() if k[0] in char_ids}

    def current_price(self, market, key):
        """The newest price, or None."""
        return self.prices.get(key)

    def character_sheets(self, char_ids):
        """Brugan with Mining 68 and Skinning 40, Amaya with Herbalism 45 (synced at different times)."""
        sheets = {gw.AMAYA: (gw.AMAYA, "Mage", 28, "Horde", "m", 0, 0, gw.NOW - HOUR, (("Herbalism", 45, 75),)),
                  gw.BRUGAN: (gw.BRUGAN, "Warrior", 30, "Horde", "m", 0, 0, gw.NOW,
                              (("Mining", 68, 75), ("Skinning", 40, 75)))}
        return [sheets[c] for c in sorted(char_ids) if c in sheets]

    def set_status(self, topic, level, kind, data, at):
        """Keep the line."""
        self.status[topic] = (level, kind, data, at)

    def clear_status(self, topic):
        """Drop the line."""
        self.status.pop(topic, None)


def write_catalog(tmp_path, rows):
    """A catalog file with these rows."""
    path = tmp_path / "gathering_catalog.json"
    path.write_text(json.dumps({"_comment": "test", "version": 1, "items": rows}), encoding="utf-8")
    return path


def build(tmp_path, rows=None, db=None, stats=None, names=None):
    """The page from the mockup catalog (or rows) for both characters."""
    db = db or FakeDb()
    path = write_catalog(tmp_path, gw.catalog_rows() if rows is None else rows)
    items = {gw.COPPER: ItemInfo(gw.COPPER, "Copper Ore", 1, 1, 7, 5, 5, 20),
             **{k: ItemInfo(k, n, 1, 1, 7, 0, 1, 20) for k, n in (names or {}).items()}}
    return page.gathering_page(db, "m", [gw.AMAYA, gw.BRUGAN], {gw.COPPER: COPPER_INTEL} if stats is None else stats,
                               items, gw.NOW, 0, Config(), path), db


def test_the_page_joins_the_catalog_with_current_prices_intelligence_and_professions(tmp_path):
    payload, db = build(tmp_path)
    rows = {r["item"]: r for r in gw.view_rows(payload, "")}
    copper = rows["Copper Ore"]
    assert (copper["ah_price"], copper["qty"], copper["rate"], copper["net"]) == ("2s 20c", "64", "—", "—")  # no rates yet
    assert copper["price_title"] == "seen 26 h ago (AHF scan Sun 08:38) · stale: over 24 h"
    assert payload["details"][str(gw.COPPER)]["net_unit"] == ("2s 20c − 5% cut 11.00c − (1 − 85%) × 3.00c deposit = "
                                                              "208.55c")
    tin = rows["Tin Ore"]
    assert (tin["ah_price"], tin["qty"], tin["note"]) == ("—", "0", "no listing in the newest scan")
    brugan = [r["item"] for r in gw.view_rows(payload, gw.BRUGAN) if r["eligible"]]
    assert brugan[0] == "Copper Ore" and "Tin Ore" in brugan and "Silver Ore" not in brugan
    assert payload["fresh"] == [["AH prices 12 min old (your scan)", "pos"], ["professions synced Mon 10:38", ""],
                                ["0 farming sessions", ""]]
    assert payload["no_rates"] and "gathering_catalog" not in db.status


def test_the_rates_come_from_the_stored_sessions_the_later_events_and_the_overrides(tmp_path):
    db = FakeDb()
    sun = gw.at(gw.SUN, "16:00")                                    # the last read: Sun 16:00, before Mon's loot
    db.stored, _ = sessions([e for e in gw.EVENTS if e.at_ms <= sun], sun, gw.GAP_MS, 20)
    last = {c: max(s.end_ms for s in db.stored if s.char_id == c) for c in (gw.AMAYA, gw.BRUGAN)}
    db.events = [e for e in gw.EVENTS if e.at_ms > last[e.char_id]]
    db.manual = {(gw.AMAYA, gw.EARTHROOT): 12}
    payload, _ = build(tmp_path, db=db)
    rows = {r["item"]: r for r in gw.view_rows(payload, "")}
    assert (rows["Copper Ore"]["rate"], rows["Copper Ore"]["note"]) == ("38", "Brugan: 3 sessions, 1 h 50 min")
    assert (rows["Earthroot"]["rate"], rows["Earthroot"]["rate_sub"]) == ("12", "manual")
    lines = payload["rate_cards"][str(gw.COPPER)]["blocks"][0]["lines"]
    assert lines[0]["text"].startswith("open: Mon 10:21– · 9 Copper Ore so far")   # derived, never stored
    assert payload["fresh"][2] == ["7 farming sessions, 3 h 40 min observed", ""]
    found, open_ = page.farming_state(db, [gw.BRUGAN], gw.NOW, page.session_settings(Config()))
    assert len(found) == 4 and set(open_) == {gw.BRUGAN}


def test_GA2_skipped_rows_set_a_status_line_and_the_other_rows_still_show(tmp_path):
    bad = {"key": 3858, "name": "Mithril Ore", "categories": ["Mining"],
           "sources": [{"type": "node", "profession": "Minning", "skill": 175}]}
    payload, db = build(tmp_path, gw.catalog_rows() + [bad])
    assert len(gw.view_rows(payload, "")) == 16
    level, kind, data, _ = db.status["gathering_catalog"]
    assert (level, kind) == ("warn", "gathering_catalog")
    assert data == {"text": "gathering catalog: 1 row skipped (Mithril Ore: unknown profession 'Minning')"}
    build(tmp_path, db=db)                                                   # a good catalog clears it
    assert "gathering_catalog" not in db.status


def test_an_unreadable_catalog_leaves_the_page_empty_with_a_status_line(tmp_path):
    db = FakeDb()
    missing = page.gathering_page(db, "m", [gw.AMAYA], {}, {}, gw.NOW, 0, Config(), tmp_path / "none.json")
    assert gw.view_rows(missing, "") == []
    assert db.status["gathering_catalog"][2]["text"].startswith("gathering catalog not read (FileNotFoundError)")
    (tmp_path / "odd.json").write_text('{"items": {}}', encoding="utf-8")
    assert page.read_catalog(tmp_path / "odd.json") == ([], "gathering catalog not read (no items list): the Gathering "
                                                             "page is empty")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert page.read_catalog(tmp_path / "bad.json")[1].startswith("gathering catalog not read (JSONDecodeError)")


def all_sessions(db: FakeDb) -> FakeDb:
    """The mockup's sessions stored as of Sun 16:00 and every event after each character's last one."""
    sun = gw.at(gw.SUN, "16:00")
    db.stored, _ = sessions([e for e in gw.EVENTS if e.at_ms <= sun], sun, gw.GAP_MS, 20)
    last = {c: max(s.end_ms for s in db.stored if s.char_id == c) for c in (gw.AMAYA, gw.BRUGAN)}
    db.events = [e for e in gw.EVENTS if e.at_ms > last[e.char_id]]
    return db


NAMES = {gw.ROUGH_STONE: "Rough Stone", gw.RUINED_SCRAPS: "Ruined Leather Scraps", gw.BROKEN_FANG: "Broken Fang",
         gw.CHIPPED_CLAW: "Chipped Claw", gw.LIGHT_HIDE: "Light Hide"}


def test_GA34_status_lists_what_was_gathered_that_the_catalog_cant_place(tmp_path):
    payload, db = build(tmp_path, db=all_sessions(FakeDb()), names=NAMES)
    level, kind, data, at = db.status["gathering_unplaced:m"]
    # Rough Stone 8 + 11 + 3 (the open candidate's); the dropped Sun 15:14 candidate's 2 are not farming
    assert (level, kind, at) == ("info", "gathering_unplaced", gw.NOW)
    assert data == {"text": "Rough Stone 22, Ruined Leather Scraps 13, Broken Fang 7, Chipped Claw 6, Light Hide 2"}
    assert not {"Rough Stone", "Ruined Leather Scraps", "Light Hide"} & {r["item"] for r in gw.view_rows(payload, "")}
    db.stored, db.events = [], []                                # nothing gathered outside the catalog: cleared
    build(tmp_path, db=db)
    assert "gathering_unplaced:m" not in db.status


def test_GA33_the_page_passes_the_events_sources_to_the_observed_categories(tmp_path):
    rows = [{**row, "categories": ["Skinning"], "sources": row["sources"][:1]} if row["key"] == gw.LIGHT_LEATHER
            else row for row in gw.catalog_rows()]
    db = FakeDb()
    db.sources = {gw.LIGHT_LEATHER: ("monster", "skinning")}
    payload, _ = build(tmp_path, rows, db=db)
    leather = next(r for r in gw.view_rows(payload, "") if r["item"] == "Light Leather")
    assert (leather["category"], leather["cats"]) == ("Skinning · Monster (observed)", ["Skinning", "Monster"])


def test_the_history_comes_from_the_intelligence_with_its_profile_and_windows(tmp_path):
    how = ",".join(str(int(gw.COPPER_HOW.values[s] * 1000 + 0.5)) for s in range(168))
    weeks = ",".join(str(w) for w in gw.COPPER_HOW.weeks)
    windows = tuple(IntelWindow(w.start_slot, w.hours, w.best_slot, w.how_avg, w.weeks) for w in gw.WINDOWS[gw.COPPER])
    intel = replace(COPPER_INTEL, how=how, how_weeks=weeks, seasonality=61, sell_windows=windows)
    facts = page.price_facts(gw.COPPER, CurrentPrice(gw.COPPER, 220, 10, gw.AHF_SEEN, "ahf"), intel, None)
    assert (facts.med30, facts.closes_30d, facts.seasonality, facts.how.values[10]) == (200, 30, 61, 0.97)
    assert [(w.start_slot, w.hours, w.how_avg, w.weeks) for w in facts.windows] == [(137, 4, 1.12, 7), (163, 3, 1.09, 7),
                                                                                  (68, 2, 1.08, 4)]
    assert page.price_facts(gw.COPPER, None, COPPER_INTEL, None).how is None           # no profile published
    payload, _ = build(tmp_path, stats={gw.COPPER: intel})
    copper = next(r for r in gw.view_rows(payload, "") if r["item"] == "Copper Ore")
    assert (copper["median"], copper["window"], copper["window_price"]) == ("2s", "Sat 17:00", "2s 24c")
    strict = page.gathering_page(FakeDb(), "m", [gw.AMAYA, gw.BRUGAN], {gw.COPPER: intel}, {}, gw.NOW, 0,
                                 replace(Config(), timing_edge_min_strength=62), write_catalog(tmp_path, gw.catalog_rows()))
    assert next(r for r in gw.view_rows(strict, "") if r["item"] == "Copper Ore")["window"] == "no timing edge"


def test_the_shipped_catalog_loads_without_a_status_line():
    db = FakeDb()
    items = page.load_catalog(db, gw.NOW)
    assert len(items) >= 100 and "gathering_catalog" not in db.status


def test_price_facts_take_the_intelligence_supply_else_the_units_at_the_newest_price():
    cur = CurrentPrice(gw.COPPER, 220, 10, gw.AHF_SEEN, "ahf")
    facts = page.price_facts(gw.COPPER, cur, COPPER_INTEL, None)
    assert (facts.supply, facts.market_chance, facts.cur_others, facts.vendor_sell, facts.quality) == (64, 0.85, 220,
                                                                                                       None, 1)
    bare = page.price_facts(gw.COPPER, cur, None, ItemInfo(gw.COPPER, "Copper Ore", None, 1, 7, 5, 5, 20))
    assert (bare.supply, bare.market_chance, bare.cur_others, bare.quality) == (10, None, 220, 1)
    unknown = page.price_facts(gw.COPPER, cur, replace(COPPER_INTEL, volume_unknown=True, units_on_ah=None, book=()),
                               None)
    assert (unknown.market_chance, unknown.supply, unknown.cur_others) == (None, 10, 220)
    never = page.price_facts(gw.COPPER, None, None, None)
    assert (never.price, never.seen_ms, never.source, never.supply) == (None, None, None, None)


def test_GA35_a_cloud_market_shows_the_newer_of_current_and_the_summarys_price(tmp_path):
    prices_ms = gw.at(gw.MON, "10:00")
    stats = {gw.COPPER: replace(COPPER_INTEL, cur=210, cur_age_ms=0),
             gw.TIN: replace(COPPER_INTEL, key=gw.TIN, cur=90, cur_qty=4, cur_age_ms=12 * HOUR, book=((90, 4, 1, 1),)),
             gw.SILVER: replace(COPPER_INTEL, key=gw.SILVER, cur=900, cur_qty=2, cur_age_ms=12 * HOUR,
                                book=((900, 2, 1, 1),))}
    db, path = FakeDb(), write_catalog(tmp_path, gw.catalog_rows())

    def rows(prices):
        payload = page.gathering_page(db, "m", [gw.AMAYA, gw.BRUGAN], stats, {}, gw.NOW, 0, Config(), path, prices)
        return {r["item"]: (r["ah_price"], r["price_title"]) for r in gw.view_rows(payload, "")}

    cloud = rows(prices_ms)
    assert cloud["Copper Ore"] == ("2s 10c", "seen 38 min ago (Blizzard scan Mon 10:00)")
    assert cloud["Tin Ore"] == ("—", "No listing in the newest scan")               # your Mon 10:26 scan is newer
    assert cloud["Silver Ore"] == ("9s", "seen 12 h ago (Blizzard scan Sun 22:00)")  # its last price, with its age
    db.prices[gw.COPPER] = CurrentPrice(gw.COPPER, 230, 5, gw.at(gw.MON, "10:30"), "local")
    stats[gw.COPPER] = replace(stats[gw.COPPER], cur=230, cur_age_ms=-30 * 60_000)   # the scan applied (IU6)
    assert rows(prices_ms)["Copper Ore"] == ("2s 30c", "seen 8 min ago (your scan)")  # a tie goes to your scan
    desktop = rows(None)
    assert desktop["Copper Ore"][0] == "2s 30c" and desktop["Silver Ore"][0] == "—"
