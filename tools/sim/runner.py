"""Run a simulated world through the real cloud job, in-process (SPEC §2.4, §8.10).

The market runs forward to each Blizzard refresh; the snapshot goes into a local data store under the collector's
key; the hourly mode runs on it (optional: it is slow at scale and the daily document is what most scenarios
read); and at `DAILY_HOUR_UTC` each simulated day the daily mode ingests what arrived and publishes. The catalog's
item search is answered from the world's items. Nothing here changes the cloud code: it reads the store exactly
as it reads S3. Faults (a refused snapshot, a repeated record, a skipped daily run) are injected by settings.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ahcloud.daily import run_daily
from ahcloud.hourly import run_hourly
from ahcloud.publish import read_latest
from ahcloud.store import LocalStore
from ahsync.collect.blizzard import QUALITY
from ahsync.intel.contract import MarketIntelligence
from ahsync.engine.volume import IdBook
from sim.emulator import (capture, envelope, id_book, records, snapshot_times, store_snapshot, with_conflict,
                         with_duplicate)
from sim.market import DAY_MS, HOUR_MS, Market, World
from sim.truth import Truth

DAILY_HOUR_UTC = 9                     # the stack's default daily run (SPEC §2.4)
SCHEDULE_SEED_OFFSET = 1               # the snapshot schedule's random stream: world seed + this
QUALITY_NAMES = {code: name for name, code in QUALITY.items()}
FAULTS = ("duplicate", "conflict")


@dataclass(frozen=True)
class RunSettings:
    """How the cloud job is run over a world, and which faults are injected."""

    hourly: bool = False                               # run the hourly mode after each snapshot
    skip_refresh: float = 0.0                          # chance an hour has no Blizzard refresh
    skip_daily: frozenset[int] = frozenset()           # simulated day numbers (0 = the start's day) with no daily run
    faults: dict[int, str] = field(default_factory=dict)   # snapshot number -> one of FAULTS


class Agent(Protocol):
    """Anyone acting on the market between snapshots: our trader (`trader.Trader`)."""

    def next_wake(self) -> int | None:
        """When it next acts.

        Returns:
            UTC ms; None for never.
        """

    def act(self, market: Market, documents: list[MarketIntelligence], now_ms: int) -> None:
        """Act at a wake.

        Args:
            market: The market, run up to now_ms.
            documents: The daily documents published so far.
            now_ms: The wake's time, UTC ms.
        """


@dataclass
class Run:
    """What a run left behind."""

    folder: Path
    truth: Truth
    snapshots: list[int]                               # snapshot times written, UTC ms
    documents: list[MarketIntelligence]                # each daily run's published document, in order
    data: LocalStore
    intel: LocalStore
    published: list[MarketIntelligence] = field(default_factory=list)   # every document published, daily and hourly


class SimCatalog:
    """Stands in for `BlizzardClient.search_items`: the world's items as Forever's static item search answers."""

    def __init__(self, world: World) -> None:
        """The catalog of a world.

        Args:
            world: The scenario; each item gives one record.
        """
        self.records = {item.key: {"id": item.key, "name": {"en_US": item.name},
                                   "quality": {"type": QUALITY_NAMES.get(item.quality, "COMMON")},
                                   "item_class": {"id": item.item_class}, "item_subclass": {"id": 0}, "level": 1,
                                   "purchase_price": item.vendor_sell * 4, "sell_price": item.vendor_sell}
                        for item in world.items}

    def search_items(self, first_id: int, last_id: int, namespace: str = "static-forever-us") -> list[dict]:
        """The records with ids in a range; Classic Era's namespace knows nothing more.

        Args:
            first_id: First id of the range.
            last_id: Last id, inclusive.
            namespace: The static namespace searched.

        Returns:
            Matching records by id; [] outside Forever's namespace.
        """
        if namespace != "static-forever-us":
            return []
        return [r for key, r in sorted(self.records.items()) if first_id <= key <= last_id]


def simulate(world: World, folder: Path, settings: RunSettings = RunSettings(), agent: Agent | None = None) -> Run:
    """Run a world through the cloud job and keep what it published.

    Args:
        world: The scenario.
        folder: An empty folder for the stores, the run's scratch space and `truth.jsonl`.
        settings: Cadence and faults.
        agent: Who else acts on the market between snapshots (our trader, `trader.py`), or None; it is handed every
            document published so far, the hourly ones too when settings.hourly.

    Returns:
        The run: truth, snapshot times and every daily document.
    """
    market = Market(world)
    end_ms = world.start_ms + world.days * DAY_MS
    data, intel = LocalStore(folder / "data"), LocalStore(folder / "intel")
    run = Run(folder, market.truth, [], [], data, intel)
    catalog = SimCatalog(world)
    schedule = snapshot_times(world.start_ms, end_ms, random.Random(world.seed + SCHEDULE_SEED_OFFSET),
                              settings.skip_refresh)
    daily_at = _first_daily(world.start_ms)
    for number, at_ms in enumerate(schedule):
        while daily_at <= at_ms:
            _daily(run, catalog, world, daily_at, settings)
            daily_at += DAY_MS
        while agent is not None and (wake := agent.next_wake()) is not None and wake <= at_ms:
            market.run_until(wake)
            agent.act(market, run.published, wake)
        market.run_until(at_ms)
        store_snapshot(data, world.market, at_ms, _snapshot(market, at_ms, settings.faults.get(number)))
        run.snapshots.append(at_ms)
        if settings.hourly and run.documents:
            run_hourly(data, intel, world.market)
            found = read_latest(intel, world.market)
            if found is not None and found[0].observations_until_ms != run.published[-1].observations_until_ms:
                run.published.append(found[0])
    _daily(run, catalog, world, daily_at, settings)      # the last snapshots, ingested as the next day's run would
    (folder / "truth.jsonl").write_bytes(run.truth.to_jsonl())
    return run


def market_books(world: World, skip_refresh: float = 0.0) -> tuple[Truth, list[IdBook]]:
    """The market alone, its snapshots as the cloud job would parse them (for fitting: no store, no cloud job).

    Args:
        world: The scenario.
        skip_refresh: Chance an hour has no Blizzard refresh.

    Returns:
        (the truth, each snapshot's raw envelope parsed back into an IdBook, oldest first); the same schedule
        and market draws as `simulate` with the same world and skip_refresh.
    """
    market = Market(world)
    schedule = snapshot_times(world.start_ms, world.start_ms + world.days * DAY_MS,
                              random.Random(world.seed + SCHEDULE_SEED_OFFSET), skip_refresh)
    books = []
    for at_ms in schedule:
        market.run_until(at_ms)
        books.append(id_book(capture(market.book(), at_ms), world.market))
    return market.truth, books


def rebuild(run: Run, world: World, folder: Path) -> MarketIntelligence:
    """A rebuild from the run's raw (FM72), published to the run's intel store as production's rebuild is, so it
    carries the latest document's hourly state as a daily run does.

    Args:
        run: A finished run.
        world: Its world (for the catalog).
        folder: An empty folder for the rebuild's scratch space.

    Returns:
        The rebuild's published document.
    """
    run_daily(run.data, run.intel, SimCatalog(world), world.market, folder / "work", fresh=True)
    found = read_latest(run.intel, world.market)
    assert found is not None, "the rebuild published nothing"
    return found[0]


def _snapshot(market: Market, at_ms: int, fault: str | None) -> bytes:
    """The market's snapshot, with a fault injected when asked.

    Args:
        market: The market, run up to at_ms.
        at_ms: The snapshot's time.
        fault: None, "duplicate" (the first record repeated) or "conflict" (the first record contradicted).

    Returns:
        The raw envelope.

    Raises:
        ValueError: An unknown fault, or a fault on a snapshot with no auctions.
    """
    regular, commodities = records(market.book(), at_ms)
    if fault is None:
        return envelope(at_ms, regular, commodities)
    if fault not in FAULTS:
        raise ValueError(f"unknown fault: {fault!r}")
    target, others, is_regular = (regular, commodities, True) if regular else (commodities, regular, False)
    if not target:
        raise ValueError(f"no auction to inject a {fault} into at {at_ms}")
    changed = (with_duplicate if fault == "duplicate" else with_conflict)(target, target[0]["id"])
    return envelope(at_ms, changed, others) if is_regular else envelope(at_ms, others, changed)


def _daily(run: Run, catalog: SimCatalog, world: World, at_ms: int, settings: RunSettings) -> None:
    """The daily run due at a time, unless that day is skipped or nothing has arrived yet.

    Args:
        run: The run so far (its documents gain the published one).
        catalog: The catalog stand-in.
        world: The scenario.
        at_ms: When the run is due, UTC ms.
        settings: Which days are skipped.
    """
    if (at_ms - world.start_ms // DAY_MS * DAY_MS) // DAY_MS in settings.skip_daily or not run.snapshots:
        return
    run_daily(run.data, run.intel, catalog, world.market, run.folder / "work")
    found = read_latest(run.intel, world.market)
    if found is not None:
        run.documents.append(found[0])
        run.published.append(found[0])


def _first_daily(start_ms: int) -> int:
    """The first daily run after a start.

    Args:
        start_ms: UTC ms.

    Returns:
        The next DAILY_HOUR_UTC o'clock strictly after start_ms.
    """
    at = start_ms // DAY_MS * DAY_MS + DAILY_HOUR_UTC * HOUR_MS
    return at if at > start_ms else at + DAY_MS
