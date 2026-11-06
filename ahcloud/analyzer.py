"""The analyzer Lambda: one function, three modes (SPEC §2.4, M31).

- **daily** (`{"mode": "daily"}`, the 09:00 UTC schedule): `daily.run_daily`.
- **hourly** (an S3 event on `raw/`, or `{"mode": "hourly"}` from the hourly fallback schedule): `hourly.run_hourly`.
- **rebuild** (`{"mode": "rebuild"}`, invoked by hand): the daily run from raw alone (FM72).
The environment holds names only (DATA_BUCKET, INTEL_BUCKET, MARKET, SECRET_NAME); the daily mode reads the
Blizzard secret for the catalog's item search. Each run emits its outcome as a metric for the alarms.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ahcloud import aws
from ahcloud.credentials import from_secret
from ahcloud.daily import run_daily
from ahcloud.hourly import run_hourly
from ahsync.clock import SystemClock
from ahsync.collect.blizzard import BlizzardClient
from ahsync.collect.client import UrllibTransport

WORKDIR = Path("/tmp/ahcloud-run")      # Lambda's ephemeral disk; emptied by every daily run


def mode_of(event: Any) -> str:
    """Which mode an invocation asks for.

    Args:
        event: The Lambda event: a schedule's {"mode": ...}, an S3 notification, or anything else.

    Returns:
        "daily", "rebuild" or "hourly" (S3 events, the hourly schedule and anything unrecognised).
    """
    mode = event.get("mode") if isinstance(event, dict) else None
    return mode if mode in ("daily", "rebuild") else "hourly"


def emit(market: str, name: str, now_ms: int) -> None:
    """One count metric for a market (the alarms read "daily_published" and "hourly_published").

    Args:
        market: Market id.
        name: Metric name, e.g. "daily_published".
        now_ms: Now, UTC ms.
    """
    aws.metric(name, 1, {"market": market}, now_ms)


def handler(event: Any, context: Any) -> dict:
    """The Lambda entry point.

    Args:
        event: The invocation's event (see mode_of).
        context: The Lambda context (unused).

    Returns:
        The outcome as a dict, for the invocation log.
    """
    env = os.environ
    market, clock = env["MARKET"], SystemClock()
    data, intel = aws.S3Store(env["DATA_BUCKET"]), aws.S3Store(env["INTEL_BUCKET"])
    mode = mode_of(event)
    if mode == "hourly":
        outcome = run_hourly(data, intel, market)
        emit(market, f"hourly_{outcome.state}", clock.now_ms())
        return {"mode": mode, "state": outcome.state, "observations_until_ms": outcome.observations_until_ms}
    found = from_secret(env["SECRET_NAME"], aws.get_secret)
    client = BlizzardClient(UrllibTransport(), clock, found.client_id, found.secret)
    result = run_daily(data, intel, client, market, WORKDIR, fresh=mode == "rebuild")
    emit(market, f"daily_{result.state}", clock.now_ms())
    return {"mode": mode, "state": result.state, "ingested": result.ingested, "items": result.items}
