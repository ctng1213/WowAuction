"""The analyzer's daily mode: the history into the baselines (SPEC §2.4; BZ59-BZ61, FM70).

One run, in a fresh folder: download `state/history.db`, which only the Ingest function writes (`ahcloud.ingest`),
and never write it back; publish nothing while the history is marked rebuilding (BZ61); analyze with `now_ms` = the
newest snapshot ingested, so a rebuild from raw gives the same bytes (FM72); carry the previous document's hourly
fields, apply the snapshots it had not seen, and publish the base document. A history that lags the raw still
publishes, from the newest snapshot it holds (BZ60).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path

from ahcloud.hourly import advance
from ahcloud.ingest import DB_KEY, LAST_INGESTED, REBUILDING, SRC
from ahcloud.profile import Profile
from ahcloud.publish import publish, read_latest, settings_to_dict
from ahcloud.store import Store
from ahsync.analysis.market import analyze_market
from ahsync.db.sqlite import Database
from ahsync.engine.item_key import KEY_SUFFIX_FACTOR
from ahsync.engine.sellprob import MarketSettings
from ahsync.intel.contract import MarketIntelligence

DAY_MS, HOUR_MS = 86_400_000, 3_600_000


@dataclass(frozen=True)
class DailyOutcome:
    """What a daily analysis did."""

    state: str                          # "published", "rebuilding" (a rebuild under way, BZ61) or "empty"
    built_ms: int | None = None
    items: int = 0                      # items in the document
    profile: str = field(default="", compare=False)   # the run's profile line (BZ65), printed by the caller


def coverage(db: Database, market: str, now_ms: int) -> tuple[tuple[int, int], ...]:
    """The hours the cloud observed in the last 90 days, as ranges (SPEC §2.4, D99).

    Args:
        db: The history database.
        market: Market id.
        now_ms: Now, UTC ms.

    Returns:
        (first hour, last hour) runs of consecutive observed hours, ascending.
    """
    hours = db.observed_hours(market, (now_ms - 90 * DAY_MS) // HOUR_MS, now_ms // HOUR_MS).get(SRC, [])
    runs: list[list[int]] = []
    for hour in hours:
        if runs and hour == runs[-1][1] + 1:
            runs[-1][1] = hour
        else:
            runs.append([hour, hour])
    return tuple((a, b) for a, b in runs)


def catalog_of(db: Database, keys: list[int]) -> dict[int, tuple]:
    """The document's catalog: each priced item's name, quality, class, vendor price and stack.

    Args:
        db: The history database.
        keys: The document's item keys.

    Returns:
        Key -> (name, quality, item_class, vendor_sell, stack, source), for the keys the catalog knows; source is
        "forever", or "classic1x" for a row from the fallback.
    """
    items, fallback = db.items(), set(json.loads(db.get_meta("catalog_fallback") or "[]"))
    return {k: (items[k].name, items[k].quality, items[k].item_class, items[k].vendor_sell, items[k].stack,
                "classic1x" if k % KEY_SUFFIX_FACTOR in fallback else "forever")
            for k in sorted(keys) if k in items}


def run_daily(data: Store, intel_store: Store, market: str, workdir: Path,
              settings: MarketSettings = MarketSettings()) -> DailyOutcome:
    """One daily analysis (SPEC §2.4 daily mode).

    Args:
        data: The data store (raw/ for `advance`, state/ for the history; read only).
        intel_store: The intel store.
        market: Market id.
        workdir: A folder for this run only (emptied first).
        settings: The analysis settings (the code's defaults; they travel in the document).

    Returns:
        What it did, with its profile line (BZ65).

    Raises:
        ahsync.db.sqlite.DatabaseError: The downloaded database is damaged (FM72: run a rebuild).
    """
    profile = Profile("daily")
    with profile.stage("download"):
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        found = data.get(DB_KEY)
        if found is not None:
            (workdir / "history.db").write_bytes(found.data)
    if found is None:
        return _profiled(DailyOutcome("empty"), profile)
    with profile.stage("download"):
        db = Database.open(workdir / "history.db", 0, None)
    try:
        return _profiled(_analyze_and_publish(db, data, intel_store, market, (settings, profile)), profile)
    finally:
        db.close()


def _profiled(outcome: DailyOutcome, profile: Profile) -> DailyOutcome:
    """The outcome with the run's profile line.

    Args:
        outcome: What the run did.
        profile: Its stage times.

    Returns:
        The outcome, its `profile` set to the line (outcome and item count only).
    """
    return replace(outcome, profile=profile.line(outcome=outcome.state, items=outcome.items))


def _analyze_and_publish(db: Database, data: Store, intel_store: Store, market: str,
                         run: tuple[MarketSettings, Profile]) -> DailyOutcome:
    """The run's body, with the downloaded history open (see run_daily).

    Args:
        db: The downloaded history database (a local copy; nothing goes back).
        data: The data store.
        intel_store: The intel store.
        market: Market id.
        run: (the analysis settings, the run's profile).

    Returns:
        What it did.
    """
    settings, profile = run
    if db.get_meta(REBUILDING):
        return DailyOutcome("rebuilding")
    last = int(db.get_meta(LAST_INGESTED) or 0)
    if not last:
        return DailyOutcome("empty")
    with profile.stage("analyze"):
        intel, _failed = analyze_market(db, market, last, settings, (SRC,))
        intel = replace(intel, catalog=catalog_of(db, list(intel.items)), coverage=coverage(db, market, last),
                        settings=settings_to_dict(settings), ages_at_ms=last)
    with profile.stage("layer"):
        try:
            prior = read_latest(intel_store, market)
        except ValueError:                                          # a torn pointer: rebuild the rings from now
            prior = None
        carried = carry_state(intel, prior[0] if prior else None)
        intel = advance(data, carried, market) or replace(carried, observations_until_ms=last)
    with profile.stage("publish"):
        publish(intel_store, intel)
    return DailyOutcome("published", intel.built_ms, len(intel.items))


def carry_state(base: MarketIntelligence, prior: MarketIntelligence | None) -> MarketIntelligence:
    """A fresh analysis with the previous document's hourly fields carried over (SPEC §2.4, BZ33).

    The analysis knows nothing of the last 24 hours' rings; the previous document does. Its observations time
    is kept, so `advance` then applies only the snapshots it had not seen.

    Args:
        base: The new analysis (no hourly fields).
        prior: The latest published document, or None.

    Returns:
        The analysis with last_hour and the recent rings of every item the prior knew, and the prior's
        observations_until_ms (None without a prior).
    """
    if prior is None:
        return replace(base, observations_until_ms=None)
    items = {}
    for key, i in base.items.items():
        p = prior.items.get(key)
        items[key] = i if p is None else replace(
            i, last_hour=p.last_hour, recent_closes=p.recent_closes, recent_sold=p.recent_sold,
            recent_listed=p.recent_listed, recent_units=p.recent_units)
    return replace(base, items=items, observations_until_ms=prior.observations_until_ms)
