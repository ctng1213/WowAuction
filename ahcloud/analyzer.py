"""The analyzer Lambda: one function, two modes (SPEC §2.4, M31, IS1).

- **daily** (`{"mode": "daily"}`, the 09:00 UTC schedule): `daily.run_daily`, the history into the baselines.
- **hourly** (an S3 event on `raw/`, or `{"mode": "hourly"}` from the hourly fallback schedule): `hourly.run_hourly`.
The history is the Ingest function's (`ahcloud.ingest`), and so is a rebuild (`{"mode": "rebuild"}` sent there,
BZ61). The environment holds names only (DATA_BUCKET, INTEL_BUCKET, MARKET), and no secret: the catalog's item
search is Ingest's too. Each run emits its outcome as a metric for the alarms, then records itself in
`state/analyzer.json` and writes the public status `status/<market>.json` (SPEC §2.4 The status page), a last step
that never fails the run (FM81).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from ahcloud import aws
from ahcloud.daily import DailyOutcome, run_daily
from ahcloud.hourly import HourlyOutcome, run_hourly
from ahcloud.ingest import key_time_ms, read_state as read_ingest_state
from ahcloud.publish import PUBLISHED_SCHEMAS, pointer_key, read_latest
from ahcloud.status import Alarm, PointerFacts, StatusInputs, alarm_id, build_status, to_json
from ahcloud.store import Store
from ahsync.clock import SystemClock
from ahsync.intel.contract import MarketIntelligence

WORKDIR = Path("/tmp/ahcloud-run")      # Lambda's ephemeral disk; emptied by every daily run
STATE_KEY = "state/analyzer.json"
COLLECTOR_STATE_KEY = "state/collector.json"
STATUS_CACHE_CONTROL = "max-age=60"     # SPEC §2.4 The status page
DAY_MS = 86_400_000
STATUS_BUDGET_MS = 60_000               # the status step needs this much of the run's time left, or is skipped (FM81)
log = logging.getLogger(__name__)


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


def status_key(market: str) -> str:
    """Where the public status lives in the intel bucket.

    Args:
        market: Market id.

    Returns:
        "status/<market>.json".
    """
    return f"status/{market}.json"


def read_json(store: Store, key: str) -> dict:
    """A small JSON state file, or {} when it is missing or unreadable.

    Args:
        store: The store.
        key: The file's key.

    Returns:
        Its object.
    """
    found = store.get(key)
    if found is None:
        return {}
    try:
        value = json.loads(found.data)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def run_record(state: dict, mode: str, outcome: HourlyOutcome | DailyOutcome, started_ms: int,
               finished_ms: int) -> dict:
    """`state/analyzer.json` with this run recorded under its mode (SPEC §2.4 What the functions record).

    Args:
        state: The file as it was ({} before the first run).
        mode: "hourly" or "daily".
        outcome: The run's outcome.
        started_ms: The run's start, UTC ms.
        finished_ms: Its end, UTC ms.

    Returns:
        The new file: per mode the finish, outcome, duration, the finish of the newest run that published
        (`published_ms`), and the hourly's `observations_until_ms` or the daily's `items` and `built_ms`.
    """
    before = state.get(mode) if isinstance(state.get(mode), dict) else {}
    entry = {"finished_ms": finished_ms, "duration_ms": finished_ms - started_ms, "outcome": outcome.state,
             "published_ms": finished_ms if outcome.state == "published" else before.get("published_ms")}
    if isinstance(outcome, HourlyOutcome):
        entry["observations_until_ms"] = outcome.observations_until_ms
    else:
        entry.update(items=outcome.items, built_ms=outcome.built_ms)
    return {**state, mode: entry}


def gather(data: Store, intel_store: Store, market: str, now_ms: int, analyzer_state: dict,
           document: MarketIntelligence | None) -> StatusInputs:
    """The status's inputs from the buckets: raw times, pointers, the newest document and the state files.

    Args:
        data: The data store.
        intel_store: The intel store.
        market: Market id.
        now_ms: N, UTC ms.
        analyzer_state: `state/analyzer.json` as this run left it.
        document: The document the run ended with; None reads the latest (a daily run that published nothing).

    Returns:
        The inputs, without the CloudWatch facts.
    """
    pointers = {}
    for schema in PUBLISHED_SCHEMAS:
        found = read_json(intel_store, pointer_key(market, schema))
        if found:
            pointers[schema] = PointerFacts(found["built_ms"], found.get("observations_until_ms"), found["bytes"])
    if document is None and pointers:
        try:
            latest = read_latest(intel_store, market)
        except ValueError:                                          # a torn pointer: the grid shows nothing analysed
            latest = None
        document = latest[0] if latest else None
    raw = tuple(key_time_ms(key) for key in data.list(f"raw/{market}/"))
    return StatusInputs(market=market, now_ms=now_ms, raw_ms=raw, pointers=pointers,
                        coverage=document.coverage if document else (),
                        items=len(document.items) if document else None,
                        collector=read_json(data, COLLECTOR_STATE_KEY), analyzer=analyzer_state,
                        ingest=read_ingest_state(data))


def cloud_facts(env: Mapping[str, str], market: str, now_ms: int) -> dict:
    """The alarms' states and the last 24 h's error, mismatch and refusal counts, from CloudWatch (SEC21).

    Args:
        env: The Lambda's environment: ALARM_PREFIX, COLLECTOR_FUNCTION, INGEST_FUNCTION and Lambda's own
            AWS_LAMBDA_FUNCTION_NAME.
        market: Market id.
        now_ms: N, UTC ms.

    Returns:
        StatusInputs fields: alarms, errors, mismatch, refused.
    """
    prefix = env["ALARM_PREFIX"]
    alarms = tuple(Alarm(found, state, since) for name, state, since in aws.alarm_states(prefix)
                   if (found := alarm_id(name, prefix)) is not None)
    functions = {"collector": env["COLLECTOR_FUNCTION"], "ingest": env["INGEST_FUNCTION"],
                 "analyzer": env["AWS_LAMBDA_FUNCTION_NAME"]}
    queries = {key: ("AWS/Lambda", "Errors", {"FunctionName": name}) for key, name in functions.items()}
    queries.update(mismatch=(aws.NAMESPACE, "mismatch", {"market": market}),
                   refused=(aws.NAMESPACE, "ingest_refused", {"market": market}))
    sums = aws.metric_sums(queries, now_ms - DAY_MS, now_ms)
    return {"alarms": alarms, "errors": {key: sums[key] for key in functions}, "mismatch": sums["mismatch"],
            "refused": sums["refused"]}


def finish_run(data: Store, intel_store: Store, market: str, mode: str, outcome: HourlyOutcome | DailyOutcome,
               times: tuple[int, int], cloud: Callable[[int], dict], put_status: Callable[[str, bytes], Any],
               remaining_ms: Callable[[], int] = lambda: STATUS_BUDGET_MS) -> None:
    """Record the run and write the status, the run's last step: an error is one log line and `status_failed` (FM81).

    Args:
        data: The data store (`state/analyzer.json`).
        intel_store: The intel store (the pointers).
        market: Market id.
        mode: "hourly" or "daily".
        outcome: The run's outcome, already settled and emitted.
        times: (the run's start, its end), UTC ms; the end is the status's N.
        cloud: N -> the CloudWatch facts (cloud_facts).
        put_status: Writes the status: (key, bytes).
        remaining_ms: The run's time left before its timeout, in ms; under STATUS_BUDGET_MS the step is skipped,
            so it can never time out a run that has already published.
    """
    started_ms, finished_ms = times
    key = status_key(market)
    if remaining_ms() < STATUS_BUDGET_MS:
        log.warning("status %s not written: %d ms left of the run", key, remaining_ms())
        aws.metric("status_failed", 1, {"market": market}, finished_ms)
        return
    try:
        state = run_record(read_json(data, STATE_KEY), mode, outcome, started_ms, finished_ms)
        data.put(STATE_KEY, json.dumps(state, sort_keys=True).encode("utf-8"))
        inputs = gather(data, intel_store, market, finished_ms, state, outcome.document)
        inputs = StatusInputs(**{**inputs.__dict__, **cloud(finished_ms)})
        put_status(key, to_json(build_status(inputs)).encode("utf-8"))
    except Exception as error:   # noqa: BLE001  FM81: the status never costs the run its outcome
        log.warning("status %s not written: %s: %s", key, type(error).__name__, error)
        aws.metric("status_failed", 1, {"market": market}, finished_ms)


def handler(event: Any, context: Any) -> dict:
    """The Lambda entry point.

    Args:
        event: The invocation's event (see mode_of).
        context: The Lambda context: its remaining time bounds the status step (FM81); None outside Lambda.

    Returns:
        The outcome as a dict, for the invocation log.
    """
    logging.getLogger().setLevel(logging.INFO)          # Lambda's root logger starts at WARNING
    env = os.environ
    market, clock = env["MARKET"], SystemClock()
    data, intel = aws.S3Store(env["DATA_BUCKET"]), aws.S3Store(env["INTEL_BUCKET"])
    mode, started_ms = mode_of(event), clock.now_ms()
    outcome: HourlyOutcome | DailyOutcome
    if mode == "hourly":
        outcome = run_hourly(data, intel, market)
        answer = {"mode": mode, "state": outcome.state, "observations_until_ms": outcome.observations_until_ms}
    else:
        outcome = run_daily(data, intel, market, WORKDIR)
        print(outcome.profile)                          # one JSON log line for Logs Insights (BZ65)
        answer = {"mode": mode, "state": outcome.state, "built_ms": outcome.built_ms, "items": outcome.items}
    finished_ms = clock.now_ms()
    emit(market, f"{mode}_{outcome.state}", finished_ms)
    finish_run(data, intel, market, mode, outcome, (started_ms, finished_ms),
               lambda now_ms: cloud_facts(env, market, now_ms),
               lambda key, body: intel.put(key, body, cache_control=STATUS_CACHE_CONTROL,
                                           content_type="application/json"),
               context.get_remaining_time_in_millis if context is not None else (lambda: STATUS_BUDGET_MS))
    return answer
