"""The analyzer Lambda: one function, two modes (SPEC §2.4, M31, IS1).

- **daily** (`{"mode": "daily"}`, the 09:00 UTC schedule): `daily.run_daily`, the history into the baselines.
- **hourly** (an S3 event on `raw/`, or `{"mode": "hourly"}` from the hourly fallback schedule): `hourly.run_hourly`.
The history is the Ingest function's (`ahcloud.ingest`), and so is a rebuild (`{"mode": "rebuild"}` sent there,
BZ61). The environment holds names only (DATA_BUCKET, INTEL_BUCKET, MARKET), and no secret: the catalog's item
search is Ingest's too. Each run emits its outcome as a metric for the alarms.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ahcloud import aws
from ahcloud.daily import run_daily
from ahcloud.hourly import run_hourly
from ahsync.clock import SystemClock

WORKDIR = Path("/tmp/ahcloud-run")      # Lambda's ephemeral disk; emptied by every daily run


def mode_of(event: Any) -> str:
    """Which mode an invocation asks for.

    Args:
        event: The Lambda event: a schedule's {"mode": ...}, an S3 notification, or anything else.

    Returns:
        "daily", or "hourly" (S3 events, the hourly schedule and anything unrecognised, a rebuild included: that
        is the Ingest function's).
    """
    mode = event.get("mode") if isinstance(event, dict) else None
    return "daily" if mode == "daily" else "hourly"


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
    logging.getLogger().setLevel(logging.INFO)          # Lambda's root logger starts at WARNING
    env = os.environ
    market, clock = env["MARKET"], SystemClock()
    data, intel = aws.S3Store(env["DATA_BUCKET"]), aws.S3Store(env["INTEL_BUCKET"])
    mode = mode_of(event)
    if mode == "hourly":
        outcome = run_hourly(data, intel, market)
        emit(market, f"hourly_{outcome.state}", clock.now_ms())
        return {"mode": mode, "state": outcome.state, "observations_until_ms": outcome.observations_until_ms}
    result = run_daily(data, intel, market, WORKDIR)
    print(result.profile)                               # one JSON log line for Logs Insights (BZ65)
    emit(market, f"daily_{result.state}", clock.now_ms())
    return {"mode": mode, "state": result.state, "built_ms": result.built_ms, "items": result.items}
