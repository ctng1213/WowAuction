"""The collector: one run fetches the house and stores a new snapshot's raw envelope (SPEC §2.4, M30).

`run` is the whole job and takes everything it touches (store, Blizzard client, clock, metric writer), so the
Lambda handler and `python -m ahcloud collect` are thin callers of the same code (plan decision 17, BZ31).
A run makes one attempt: a failure raises, the Lambda's error alarm emails, and the schedule is the retry
(FM68). The raw envelope is written before the state file, so a crash between them refetches the same
snapshot and rewrites the same key with the same bytes (BZ8). Every run that gets an answer writes the state file,
so the status page can tell "Blizzard hasn't refreshed" from "the collector is dead" (BZ55).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from ahcloud import aws
from ahcloud.credentials import from_secret
from ahcloud.store import Store
from ahsync.clock import Clock, SystemClock
from ahsync.collect.blizzard import BlizzardClient, parse_snapshot, raw_envelope, raw_key
from ahsync.collect.client import UrllibTransport
from ahsync.collect.provider import load_bonus_suffixes

log = logging.getLogger("ahcloud.collector")
STATE_KEY = "state/collector.json"


@dataclass(frozen=True)
class Settings:
    """Which house the collector watches."""

    market: str                       # AHSync's market id, e.g. "forever.normal.horde.us"
    ruleset: int                      # the route's ruleset (ignored by Blizzard, SPEC §2.4)
    house: int                        # the auction house id: the market's identity


@dataclass(frozen=True)
class Outcome:
    """What a run did."""

    state: str                        # "written", "unchanged" or "mismatch"
    key: str | None = None            # the raw object written
    last_modified: str | None = None
    auctions: int = 0                 # auctions in the snapshot (0 for an empty house)
    bid_only: int = 0
    empty: bool = False


def read_state(store: Store) -> dict:
    """The state file: `last_modified` (the newest stored snapshot's) and the last run's facts (BZ55).

    Args:
        store: The object store.

    Returns:
        The state; {} before the first run, or when the file can't be read (the next snapshot is then refetched).
    """
    found = store.get(STATE_KEY)
    if found is None:
        return {}
    try:
        state = json.loads(found.data)
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def write_state(store: Store, last_modified: str | None, auctions: int | None, outcome: str, started_ms: int,
                finished_ms: int) -> None:
    """Write the state file after a run that got an answer (SPEC §2.4 The status page, BZ55).

    Args:
        store: The object store.
        last_modified: The newest stored snapshot's Last-Modified (moved only after its raw object is written).
        auctions: The newest stored snapshot's auction count; None before one.
        outcome: "written", "unchanged" or "mismatch".
        started_ms: The run's start, UTC ms (`last_checked_ms`).
        finished_ms: Its end, UTC ms.
    """
    state = {"last_modified": last_modified, "last_checked_ms": started_ms, "last_outcome": outcome,
             "last_duration_ms": finished_ms - started_ms, "last_auctions": auctions}
    store.put(STATE_KEY, json.dumps(state, sort_keys=True).encode("utf-8"))


def run(store: Store, client: BlizzardClient, clock: Clock, settings: Settings,
        emit: Callable[[str, float], None]) -> Outcome:
    """Fetch the house; store a new snapshot's raw envelope, then the state file (SPEC §2.4, BZ7, BZ8, BZ9, BZ55).

    Args:
        store: The object store (S3, or a folder).
        client: The Blizzard client (its token is cached across runs of a warm Lambda).
        clock: The clock (the envelope's fetched_ms).
        settings: The house.
        emit: Writes one metric: (name, value).

    Returns:
        What the run did.

    Raises:
        ahsync.collect.blizzard.BlizzardError: A request failed or the snapshot is malformed; nothing is written.
        ahsync.collect.client.TransportError: The network failed; nothing is written.
    """
    started_ms = clock.now_ms()
    state = read_state(store)
    since = state.get("last_modified")
    fetch = client.fetch_house(settings.ruleset, settings.house, since)
    if fetch.state != "new":
        write_state(store, since, state.get("last_auctions"), fetch.state, started_ms, clock.now_ms())
        emit(fetch.state, 1)
        log.info("house %s %s since %s", settings.house, fetch.state, since)
        return Outcome(fetch.state, last_modified=since)
    assert fetch.at_ms is not None and fetch.last_modified is not None
    parsed = parse_snapshot(settings.market, fetch.auctions, fetch.commodities, fetch.at_ms, load_bonus_suffixes())
    key = raw_key(settings.market, fetch.at_ms)
    store.put(key, raw_envelope(settings.ruleset, settings.house, fetch.last_modified, clock.now_ms(),
                                fetch.auctions, fetch.commodities))
    count = len(parsed.observation.auctions) if parsed.observation else 0
    write_state(store, fetch.last_modified, count, "written", started_ms, clock.now_ms())
    emit("snapshot_written", 1)
    log.info("house %s snapshot %s: %d auctions, %d bid-only left out%s", settings.house, fetch.last_modified,
             count, parsed.bid_only, ", empty house" if parsed.empty else "")
    return Outcome("written", key, fetch.last_modified, count, parsed.bid_only, parsed.empty)


# The one piece of module state: a warm Lambda reuses its client, so the token is too (BZ6). Lambda only;
# run() takes its client as an argument.
_client: BlizzardClient | None = None


def handler(event: Any, context: Any) -> dict:
    """The Lambda entry point: settings from the environment, credentials from Secrets Manager.

    The environment holds names only (BUCKET, MARKET, RULESET, HOUSE, SECRET_NAME), never a credential (SEC17).

    Args:
        event: The schedule's event (unused).
        context: The Lambda context (unused).

    Returns:
        The outcome as a dict, for the invocation log.
    """
    global _client
    logging.getLogger().setLevel(logging.INFO)          # Lambda's root logger starts at WARNING
    env = os.environ
    settings = Settings(env["MARKET"], int(env["RULESET"]), int(env["HOUSE"]))
    clock = SystemClock()
    if _client is None:
        found = from_secret(env["SECRET_NAME"], aws.get_secret)
        _client = BlizzardClient(UrllibTransport(), clock, found.client_id, found.secret)
    dims = {"market": settings.market}
    outcome = run(aws.S3Store(env["BUCKET"]), _client, clock, settings,
                  lambda name, value: aws.metric(name, value, dims, clock.now_ms()))
    return {"state": outcome.state, "key": outcome.key, "auctions": outcome.auctions}

