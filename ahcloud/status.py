"""The cloud status document: every text and colour the public status page shows (SPEC §2.4 The status page; CS2-CS11).

Pure: the raw snapshots' times, the pointers, the functions' state files, the alarm states, the last 24 hours'
metric sums and `now_ms` in; a dict out, which `to_json` turns into the same bytes for the same inputs. The analyzer
gathers the inputs and writes the result (`ahcloud/analyzer.py`); the page only draws it, apart from the status's
own age (SPEC §8.5's one exception), which it compares against `age_limits_ms`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from ahsync.engine.prices import median

SCHEMA = 1
MINUTE_MS, HOUR_MS, DAY_MS = 60_000, 3_600_000, 86_400_000
AGE_LIMITS_MS = (70 * MINUTE_MS, 3 * HOUR_MS)       # the page: amber over 70 min, red over 3 h (plan decision 5)
DAILY_HOUR_UTC = 9                                   # the daily run's hour; the stack test holds it equal (CS11)
DAILY_DUE_MS = 30 * MINUTE_MS                        # the daily analysis is due by 09:30 (CS4)
DAILY_LATE_MS = 3 * HOUR_MS                          # missing after 12:00 (CS4)
DAILY_ALARM_MS = 27 * HOUR_MS                        # NoDailyBaselines' limit (FM79)
SNAPSHOT_FRESH_MS = 75 * MINUTE_MS                   # green only with the newest snapshot this young (CS2)
SNAPSHOT_STALE_MS = 3 * HOUR_MS                      # NoNewSnapshot's limit (CS2)
PRICES_FRESH_MS = 20 * MINUTE_MS                     # CS3
PRICES_STALE_MS = 3 * HOUR_MS                        # NoMarketSummary's limit (CS3)
TIMEOUTS_S = {"collector": 120, "ingest": 900, "analyzer": 900}   # the stack test holds these equal (CS6)
RUN_AMBER_PCT, RUN_RED_PCT = 66, 90                  # CS6: amber from 66%, red over 90%
WEEK_BLOCKS = 12                                     # 84 days, inside the document's 90-day coverage (CS8)
# The stack's alarms, by the logical id CloudFormation puts after the stack name (SEC21 scopes DescribeAlarms to them).
ALARM_IDS = ("CollectorErrors", "NoNewSnapshot", "IngestErrors", "IngestSlowRun", "HistoryBehind", "AnalyzerErrors",
             "AnalyzerDeadLettersWaiting", "NoDailyBaselines", "NoMarketSummary", "AnalyzerSlowRun")
ERROR_ALARMS = (("CollectorErrors", "collector"), ("IngestErrors", "ingest"), ("AnalyzerErrors", "analyzer"))
DEAD_LETTERS = "AnalyzerDeadLettersWaiting"
FUNCTION_LABELS = {"collector": "collector", "ingest": "Ingest", "analyzer": "analyzer"}
RED_PHRASES = {"collection": "Collection stopped", "prices": "Prices not publishing", "daily": "Daily analysis missing",
               "failures": "Runs failing", "runtime": "Near the time limit"}
COLOUR_RANK = {"grey": 0, "green": 1, "amber": 2, "red": 3}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Alarm:
    """One of the stack's alarms, as DescribeAlarms reports it."""

    name: str                         # its id in ALARM_IDS, e.g. "CollectorErrors"
    state: str                        # "OK", "ALARM" or "INSUFFICIENT_DATA"
    since_ms: int                     # when it entered that state, UTC ms


@dataclass(frozen=True)
class PointerFacts:
    """What one `latest-<schema>.json` says that the status needs."""

    built_ms: int
    observations_until_ms: int | None
    bytes: int                        # the gzipped document's size


@dataclass(frozen=True)
class StatusInputs:
    """Everything the status is made from, gathered by the analyzer at the end of a run."""

    market: str
    now_ms: int                                       # N: the status's own time, UTC ms
    raw_ms: tuple[int, ...]                           # every raw snapshot's time, ascending
    pointers: dict[int, PointerFacts]                 # schema -> its pointer
    coverage: tuple[tuple[int, int], ...] = ()        # the newest document's analysed hours, (first, last) hour runs
    items: int | None = None                          # the newest document's item count
    collector: dict = field(default_factory=dict)     # state/collector.json
    analyzer: dict = field(default_factory=dict)      # state/analyzer.json: {"hourly": {...}, "daily": {...}}
    ingest: dict = field(default_factory=dict)        # state/ingest.json
    alarms: tuple[Alarm, ...] = ()
    errors: dict[str, int] = field(default_factory=dict)   # function -> Errors over the last 24 h
    mismatch: int = 0                                 # the collector's `mismatch` over the last 24 h
    refused: int = 0                                  # Ingest's `ingest_refused` over the last 24 h


# --- text ----------------------------------------------------------------------------------------------------------

def utc(ms: int) -> datetime:
    """A UTC time as a datetime.

    Args:
        ms: UTC ms.

    Returns:
        The aware datetime.
    """
    return datetime.fromtimestamp(ms // 1000, timezone.utc)


def age_text(ms: int) -> str:
    """An age in whole minutes, floored: "12 min" under an hour, then "3 h 12 min", or "5 h" on the hour.

    Args:
        ms: The age in ms (a negative age reads 0).

    Returns:
        The text.
    """
    minutes = max(0, ms) // MINUTE_MS
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h" if rest == 0 else f"{hours} h {rest} min"


def duration_text(ms: int) -> str:
    """A run's duration in whole seconds, floored: "41 s" under a minute, then "9 min 54 s", or "15 min".

    Args:
        ms: The duration in ms.

    Returns:
        The text.
    """
    seconds = max(0, ms) // 1000
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min" if rest == 0 else f"{minutes} min {rest} s"


def clock_text(ms: int, now_ms: int) -> str:
    """A time of day, "14:18", with the weekday ("Mon 09:04") when it is not on N's UTC day.

    Args:
        ms: The time, UTC ms.
        now_ms: N, UTC ms.

    Returns:
        The text.
    """
    at = utc(ms)
    text = f"{at.hour:02d}:{at.minute:02d}"
    return text if ms // DAY_MS == now_ms // DAY_MS else f"{WEEKDAYS[at.weekday()]} {text}"


def hour_text(hour: int) -> str:
    """An hour with its date, "Sat 24 Oct 06:00".

    Args:
        hour: Hours since the epoch (ms // HOUR_MS).

    Returns:
        The text.
    """
    at = utc(hour * HOUR_MS)
    return f"{WEEKDAYS[at.weekday()]} {at.day} {MONTHS[at.month - 1]} {at.hour:02d}:00"


def count_text(n: int, one: str, many: str) -> str:
    """A count with its noun, "1 snapshot" or "3,412 auctions".

    Args:
        n: The count.
        one: The noun after 1.
        many: The noun after any other count.

    Returns:
        The text, with thousands separators.
    """
    return f"{n:,} {one if n == 1 else many}"


def megabytes_text(size: int) -> str:
    """A size in MB to one decimal, rounded half up: floor(x × 10 + 0.5) ÷ 10 (CS10).

    Args:
        size: Bytes.

    Returns:
        E.g. "6.2 MB".
    """
    tenths = (size * 10 + 500_000) // 1_000_000
    return f"{tenths // 10}.{tenths % 10} MB"


def schemas_text(schemas: list[int]) -> str:
    """The published schemas: "13–16" when consecutive, else listed.

    Args:
        schemas: Ascending schema numbers (at least one).

    Returns:
        The text.
    """
    if len(schemas) > 1 and schemas == list(range(schemas[0], schemas[-1] + 1)):
        return f"{schemas[0]}–{schemas[-1]}"
    return ", ".join(str(s) for s in schemas)


def sla(sla_id: str, title: str, colour: str, value: str, sub: str, sentence: str) -> dict:
    """One SLA tile.

    Args:
        sla_id: "collection", "prices", "daily", "failures" or "runtime".
        title: The tile's name.
        colour: "green", "amber", "red" or "grey".
        value: The main line.
        sub: The small line under it.
        sentence: What the headline says when this SLA decides it.

    Returns:
        The tile as the JSON holds it.
    """
    return {"id": sla_id, "title": title, "colour": colour, "value": value, "sub": sub, "sentence": sentence}


def grey(sla_id: str, title: str, sub: str = "") -> dict:
    """An SLA not yet measured (before the first snapshot, or before its function has run).

    Args:
        sla_id: The SLA's id.
        title: Its name.
        sub: Why, when there is more to say.

    Returns:
        The tile.
    """
    return sla(sla_id, title, "grey", "not yet measured", sub, "")


# --- the SLAs ------------------------------------------------------------------------------------------------------

def collection_sla(raw_ms: tuple[int, ...], now_ms: int) -> dict:
    """SLA 1, every hour collected: the 24 whole UTC hours before N's hour (CS2).

    Args:
        raw_ms: Raw snapshot times, ascending.
        now_ms: N.

    Returns:
        The tile.
    """
    title = "Every hour collected"
    if not raw_ms:
        return grey("collection", title)
    now_hour, hours = now_ms // HOUR_MS, {t // HOUR_MS for t in raw_ms}
    got = sum(1 for h in range(now_hour - 24, now_hour) if h in hours)
    age = now_ms - raw_ms[-1]
    value, sub = f"{got} of 24 hours", f"last 24 h · newest snapshot {age_text(age)} old"
    if age > SNAPSHOT_STALE_MS:
        return sla("collection", title, "red", value, sub, f"No new snapshot for {age_text(age)}.")
    if got == 24 and age <= SNAPSHOT_FRESH_MS:
        return sla("collection", title, "green", value, sub, "Every hour of the last 24 has a snapshot.")
    if got < 24:
        missing = 24 - got
        verb = "has" if missing == 1 else "have"
        return sla("collection", title, "amber", value, sub, f"{missing} of the last 24 hours {verb} no snapshot.")
    return sla("collection", title, "amber", value, sub, f"No new snapshot for {age_text(age)}.")


def prices_sla(raw_ms: tuple[int, ...], newest: PointerFacts | None, hourly: dict, now_ms: int) -> dict:
    """SLA 2, hourly prices published: the newest snapshot against the newest pointer's prices (CS3).

    Args:
        raw_ms: Raw snapshot times, ascending.
        newest: The newest schema's pointer; None before the first publish.
        hourly: The analyzer's last hourly run ({} before one).
        now_ms: N.

    Returns:
        The tile.
    """
    title = "Hourly prices published"
    if not raw_ms:
        return grey("prices", title)
    if newest is None:
        return sla("prices", title, "amber", "No summary yet", "the first daily run publishes it",
                   "No market summary is published yet.")
    until = newest.observations_until_ms or newest.built_ms
    lag = raw_ms[-1] - until
    value = "up to date" if lag <= 0 else f"{age_text(lag)} behind"
    sub = f"prices up to {clock_text(until, now_ms)} · newest snapshot {clock_text(raw_ms[-1], now_ms)}"
    behind = f"Prices are {age_text(lag)} behind the newest snapshot." if lag > 0 else "Prices are up to date."
    if lag > PRICES_STALE_MS:
        return sla("prices", title, "red", value, sub, behind)
    if hourly.get("outcome") == "no_base":
        return sla("prices", title, "amber", value, sub, "The last hourly run found no baselines to price over.")
    return sla("prices", title, "green" if lag <= PRICES_FRESH_MS else "amber", value, sub, behind)


def daily_sla(raw_ms: tuple[int, ...], daily: dict, now_ms: int) -> dict:
    """SLA 3, the daily analysis: published by DAILY_HOUR_UTC + 30 min, missing after + 3 h or 27 h (CS4).

    Args:
        raw_ms: Raw snapshot times, ascending.
        daily: The analyzer's last daily run ({} before one): `published_ms` is the newest that published.
        now_ms: N.

    Returns:
        The tile.
    """
    title = "Daily analysis"
    if not raw_ms:
        return grey("daily", title)
    if not daily:
        return grey("daily", title, "no daily run recorded yet")
    due_day = now_ms // DAY_MS * DAY_MS + DAILY_HOUR_UTC * HOUR_MS
    if due_day > now_ms:
        due_day -= DAY_MS
    due = due_day + DAILY_DUE_MS
    day = "today" if due_day // DAY_MS == now_ms // DAY_MS else WEEKDAYS[utc(due_day).weekday()]
    due_text = f"{DAILY_HOUR_UTC:02d}:30"
    published = daily.get("published_ms")
    if published is not None and published >= due_day:
        at = clock_text(published, now_ms)
        sub = f"{day} · due by {due_text} · next {DAILY_HOUR_UTC:02d}:00 {WEEKDAYS[utc(due_day + DAY_MS).weekday()]}"
        if published <= due:
            return sla("daily", title, "green", f"Published {at}", sub, f"The daily analysis was published at {at}.")
        return sla("daily", title, "amber", f"Published {at}, late", sub,
                   f"The daily analysis was published late, at {at}.")
    last = (f"last published {clock_text(published, now_ms)}, {age_text(now_ms - published)} ago"
            if published is not None else "never published")
    if now_ms > due_day + DAILY_LATE_MS:
        return sla("daily", title, "red", f"Not published {day}", last, f"No daily analysis {day}; {last}.")
    if published is not None and now_ms - published > DAILY_ALARM_MS:
        return sla("daily", title, "red", f"None for {age_text(now_ms - published)}", last,
                   f"No daily analysis for {age_text(now_ms - published)}; {last}.")
    if now_ms > due:
        return sla("daily", title, "amber", f"Not published yet, due {due_text}", last,
                   f"The daily analysis isn't published yet; it was due at {due_text}.")
    return sla("daily", title, "green", f"Due by {due_text}", last, f"The daily analysis is due by {due_text}.")


def failures_sla(inputs: StatusInputs, alarms_on: dict[str, Alarm]) -> dict:
    """SLA 4, no failed runs: an error alarm or the dead-letter queue red; errors or refusals in 24 h amber (CS5).

    Args:
        inputs: The status inputs (errors and refusals over the last 24 h).
        alarms_on: The alarms in ALARM, by id.

    Returns:
        The tile.
    """
    title, now_ms = "No failed runs", inputs.now_ms
    if not inputs.raw_ms:
        return grey("failures", title)
    for name, function in ERROR_ALARMS:
        if name in alarms_on:
            label = FUNCTION_LABELS[function]
            value = count_text(inputs.errors.get(function, 0), f"{label} error", f"{label} errors")
            sub = f"alarm {name} since {clock_text(alarms_on[name].since_ms, now_ms)}"
            return sla("failures", title, "red", value, sub, f"{value}; {sub}.")
    if DEAD_LETTERS in alarms_on:
        sub = f"alarm {DEAD_LETTERS} since {clock_text(alarms_on[DEAD_LETTERS].since_ms, now_ms)}"
        return sla("failures", title, "red", "Failed events waiting", sub,
                   "An analyzer event failed twice and waits in the dead-letter queue.")
    counts = [count_text(inputs.errors[f], f"{FUNCTION_LABELS[f]} error", f"{FUNCTION_LABELS[f]} errors")
              for _, f in ERROR_ALARMS if inputs.errors.get(f, 0) > 0]
    if counts:
        value = f"{', '.join(counts)} in 24 h, none now"
        return sla("failures", title, "amber", value, "last 24 h · every alarm OK", f"{value}.")
    if inputs.refused > 0:
        value = count_text(inputs.refused, "snapshot refused", "snapshots refused")
        return sla("failures", title, "amber", value, "by Ingest in the last 24 h",
                   f"Ingest refused {count_text(inputs.refused, 'snapshot', 'snapshots')} in the last 24 h.")
    return sla("failures", title, "green", "0 errors", "last 24 h · dead-letter queue empty",
               "No failed runs in the last 24 h.")


def runtime_sla(inputs: StatusInputs) -> dict:
    """SLA 5, run time vs limit: each newest run's share of its timeout, whole percent floored; the worst decides (CS6).

    Args:
        inputs: The status inputs (the state files' durations).

    Returns:
        The tile.
    """
    title = "Run time vs limit"
    runs = [("daily", "Daily", inputs.analyzer.get("daily", {}).get("duration_ms"), TIMEOUTS_S["analyzer"]),
            ("hourly", "Hourly", inputs.analyzer.get("hourly", {}).get("duration_ms"), TIMEOUTS_S["analyzer"]),
            ("Ingest", "Ingest", inputs.ingest.get("duration_ms"), TIMEOUTS_S["ingest"]),
            ("collector", "Collector", inputs.collector.get("last_duration_ms"), TIMEOUTS_S["collector"])]
    known = [(name, label, ms, limit, ms * 100 // (limit * 1000)) for name, label, ms, limit in runs
             if isinstance(ms, int)]
    if not inputs.raw_ms or not known:
        return grey("runtime", title)
    worst = max(range(len(known)), key=lambda i: (known[i][4], -i))   # the earlier in the list wins a tie
    name, label, ms, limit, pct = known[worst]
    value = f"{label} {duration_text(ms)} of {duration_text(limit * 1000)}"
    sub = " · ".join([f"{pct}%"] + [f"{n} {duration_text(m)}" for i, (n, _, m, _, _) in enumerate(known) if i != worst])
    colour = "red" if pct > RUN_RED_PCT else "amber" if pct >= RUN_AMBER_PCT else "green"
    return sla("runtime", title, colour, value, sub, f"The {name} run used {pct}% of its time limit.")


def headline(slas: list[dict], raw_ms: tuple[int, ...]) -> dict:
    """The one verdict: the worst SLA colour, named by the first SLA of that colour (CS7).

    Args:
        slas: The five tiles, in order.
        raw_ms: Raw snapshot times (none: waiting for the first snapshot).

    Returns:
        {"colour", "title", "sentence"}.
    """
    if not raw_ms:
        return {"colour": "grey", "title": "Waiting for the first snapshot",
                "sentence": "The collector hasn't stored a snapshot yet."}
    worst = max((s["colour"] for s in slas), key=COLOUR_RANK.__getitem__)
    first = next(s for s in slas if s["colour"] == worst)
    if worst == "red":
        return {"colour": "red", "title": RED_PHRASES[first["id"]], "sentence": first["sentence"]}
    if worst == "amber":
        warnings = sum(1 for s in slas if s["colour"] == "amber")
        return {"colour": "amber", "title": f"Running, {count_text(warnings, 'warning', 'warnings')}",
                "sentence": first["sentence"]}
    if worst == "green":
        return {"colour": "green", "title": "Healthy", "sentence": "Every SLA is met."}
    return {"colour": "grey", "title": "Waiting for the first runs", "sentence": "No run has been recorded yet."}


# --- the hour grid -------------------------------------------------------------------------------------------------

def hour_runs(hours: list[int]) -> str:
    """Hours as runs: "Sat 24 Oct 06:00–13:00, Mon 26 Oct 20:00" (CS8).

    Args:
        hours: Ascending hours since the epoch (at least one).

    Returns:
        The text.
    """
    runs: list[list[int]] = []
    for hour in hours:
        if runs and hour == runs[-1][1] + 1:
            runs[-1][1] = hour
        else:
            runs.append([hour, hour])
    texts = []
    for first, last in runs:
        if first == last:
            texts.append(hour_text(first))
        elif first // 24 == last // 24:
            texts.append(f"{hour_text(first)}–{utc(last * HOUR_MS).hour:02d}:00")
        else:
            texts.append(f"{hour_text(first)} – {hour_text(last)}")
    return ", ".join(texts)


def date_range_text(first_day: int, last_day: int) -> str:
    """A block's dates: "4–10 Nov", or "28 Oct – 3 Nov" across a month.

    Args:
        first_day: Days since the epoch.
        last_day: Days since the epoch.

    Returns:
        The text.
    """
    a, b = utc(first_day * DAY_MS), utc(last_day * DAY_MS)
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTHS[b.month - 1]}"
    return f"{a.day} {MONTHS[a.month - 1]} – {b.day} {MONTHS[b.month - 1]}"


def cell(hour: int, now_hour: int, first_hour: int | None, analysed: set[int], collected: set[int]) -> str:
    """One hour's character: p this hour, f future or before collection, a analysed, l collected, m missing (CS8).

    Args:
        hour: Hours since the epoch.
        now_hour: N's hour.
        first_hour: The first raw snapshot's hour; None before one.
        analysed: Hours in the newest document's coverage.
        collected: Hours with a raw snapshot.

    Returns:
        One of "p", "f", "a", "l", "m".
    """
    if hour == now_hour:
        return "p"
    if hour > now_hour or first_hour is None or hour < first_hour:
        return "f"
    if hour in analysed:
        return "a"
    return "l" if hour in collected else "m"


def week_line(hours: list[int], cells: str, began: int | None, built_hour: int | None) -> str:
    """The line under a block: its missing hours, then hours collected but not analysed, else complete (CS8).

    Args:
        hours: The block's 168 hours.
        cells: Their characters.
        began: The first raw snapshot's hour when it falls inside this block, else None.
        built_hour: The newest daily document's built_ms hour; collected hours from it on wait for the next run.

    Returns:
        The text.
    """
    missing = [h for h, c in zip(hours, cells) if c == "m"]
    unanalysed = [h for h, c in zip(hours, cells) if c == "l" and built_hour is not None and h < built_hour]
    waiting = sum(1 for h, c in zip(hours, cells) if c == "l" and (built_hour is None or h >= built_hour))
    parts = [f"Collection began {hour_text(began)}"] if began is not None else []
    if missing:
        parts.append(f"{count_text(len(missing), 'hour', 'hours')} missing: {hour_runs(missing)}")
    if unanalysed:
        noun = count_text(len(unanalysed), "hour collected", "hours collected")
        parts.append(f"{noun} but not analysed: {hour_runs(unanalysed)}")
    if not missing and not unanalysed:
        if began is not None:
            parts.append("complete since")
        else:
            done = cells.count("a")
            text = f"Complete: {done} of {done + waiting} hours analysed"
            parts.append(text + (f" · {waiting} waiting for the daily run" if waiting else ""))
    return " · ".join(parts)


def week(block: int, inputs: StatusInputs, analysed: set[int], collected: set[int]) -> dict:
    """One 7-day block of the grid: its rows, cells, picker label and line (CS8).

    Args:
        block: 0 for the 7 UTC days ending on N's day, 1 for the 7 before, and so on.
        inputs: The status inputs.
        analysed: Hours in the newest document's coverage.
        collected: Hours with a raw snapshot.

    Returns:
        {"label", "days", "cells", "missing"}.
    """
    last_day = inputs.now_ms // DAY_MS - 7 * block
    first_day = last_day - 6
    hours = list(range(first_day * 24, (last_day + 1) * 24))
    now_hour = inputs.now_ms // HOUR_MS
    first_hour = inputs.raw_ms[0] // HOUR_MS if inputs.raw_ms else None
    cells = "".join(cell(h, now_hour, first_hour, analysed, collected) for h in hours)
    began = first_hour if first_hour is not None and hours[0] < first_hour <= hours[-1] else None
    newest = max(inputs.pointers) if inputs.pointers else None
    built_hour = inputs.pointers[newest].built_ms // HOUR_MS if newest is not None else None
    days = [f"{WEEKDAYS[utc(d * DAY_MS).weekday()]} {utc(d * DAY_MS).day}" for d in range(first_day, last_day + 1)]
    label = ("Last 7 days: " if block == 0 else "") + date_range_text(first_day, last_day)
    if began is not None:
        label += f" · collection began {WEEKDAYS[utc(began * HOUR_MS).weekday()]} {utc(began * HOUR_MS).day}"
    elif first_hour is None:
        label += " · no snapshot yet"
    else:
        missing = cells.count("m")
        label += f" · {missing} h missing" if missing else " · complete"
    line = week_line(hours, cells, began, built_hour) if first_hour is not None else "No snapshot yet."
    return {"label": label, "days": days, "cells": cells, "missing": line}


def weeks(inputs: StatusInputs) -> list[dict]:
    """The grid's blocks, newest first; blocks wholly before the first raw snapshot are left out (CS8).

    Args:
        inputs: The status inputs.

    Returns:
        Up to WEEK_BLOCKS blocks (block 0 always).
    """
    analysed = {h for first, last in inputs.coverage for h in range(first, last + 1)}
    collected = {t // HOUR_MS for t in inputs.raw_ms}
    first_day = inputs.raw_ms[0] // DAY_MS if inputs.raw_ms else None
    blocks = []
    for block in range(WEEK_BLOCKS):
        if block and (first_day is None or inputs.now_ms // DAY_MS - 7 * block < first_day):
            break
        blocks.append(week(block, inputs, analysed, collected))
    return blocks


# --- the cards -----------------------------------------------------------------------------------------------------

def function_colour(function: str, ran: bool, inputs: StatusInputs, alarms_on: dict[str, Alarm]) -> str:
    """A function row's dot: red with its error alarm on, amber with errors in 24 h, grey before a run, else green.

    Args:
        function: "collector", "ingest" or "analyzer".
        ran: Whether its state file records a run (a pre-H2 collector file holds only `last_modified`).
        inputs: The status inputs.
        alarms_on: The alarms in ALARM, by id.

    Returns:
        The colour.
    """
    alarm = next(name for name, f in ERROR_ALARMS if f == function)
    if alarm in alarms_on:
        return "red"
    if inputs.errors.get(function, 0) > 0:
        return "amber"
    return "green" if ran else "grey"


def collector_row(inputs: StatusInputs, alarms_on: dict[str, Alarm]) -> dict:
    """The collector's last run and outcome (CS10).

    Args:
        inputs: The status inputs.
        alarms_on: The alarms in ALARM, by id.

    Returns:
        {"name", "colour", "value", "sub"}.
    """
    state, now_ms = inputs.collector, inputs.now_ms
    ran = isinstance(state.get("last_checked_ms"), int)
    row = {"name": "Collector", "colour": function_colour("collector", ran, inputs, alarms_on)}
    if not ran:
        return {**row, "value": "no run recorded yet", "sub": "runs every 15 min"}
    last_outcome = str(state.get("last_outcome"))
    outcome = {"written": "new snapshot", "unchanged": "no new snapshot",
               "mismatch": "mismatched answer"}.get(last_outcome, last_outcome)
    parts = []
    if inputs.raw_ms:
        newest = clock_text(inputs.raw_ms[-1], now_ms)
        auctions = state.get("last_auctions")
        counted = f" ({count_text(auctions, 'auction', 'auctions')})" if isinstance(auctions, int) else ""
        if state.get("last_outcome") == "unchanged":
            parts.append(f"Blizzard hasn't refreshed since {newest}")
        elif state.get("last_outcome") == "mismatch":
            parts.append("the two halves of the house disagreed; refetched next run")
        parts.append(f"last new snapshot {newest}{counted}")
    parts.append("runs every 15 min")
    return {**row, "value": f"{clock_text(state['last_checked_ms'], now_ms)} · {outcome}", "sub": " · ".join(parts)}


def ingest_row(inputs: StatusInputs, alarms_on: dict[str, Alarm]) -> dict:
    """Ingest's last run: snapshots ingested, the history's newest and what is still pending (IS1).

    Args:
        inputs: The status inputs.
        alarms_on: The alarms in ALARM, by id.

    Returns:
        {"name", "colour", "value", "sub"}.
    """
    state, now_ms = inputs.ingest, inputs.now_ms
    finished, duration = state.get("finished_ms"), state.get("duration_ms")
    row = {"name": "Ingest", "colour": function_colour("ingest", isinstance(finished, int), inputs, alarms_on)}
    if not isinstance(finished, int):
        return {**row, "value": "no run recorded yet", "sub": "runs at :10 and :40"}
    started = finished - (duration if isinstance(duration, int) else 0)
    outcome = str(state.get("outcome"))
    what = {"up_to_date": "up to date", "empty": "nothing ingested"}.get(
        outcome, f"{count_text(int(state.get('ingested') or 0), 'snapshot', 'snapshots')} ingested")
    parts = []
    last = state.get("last_ingested_at_ms")
    if isinstance(last, int):
        parts.append(f"history up to {clock_text(last, now_ms)}")
    parts.append(f"{int(state.get('pending') or 0):,} pending")
    if isinstance(state.get("rebuilding_until_ms"), int):
        parts.append("rebuilding")
    parts.append("runs at :10 and :40")
    return {**row, "value": f"{clock_text(started, now_ms)} · {what}", "sub": " · ".join(parts)}


def analyzer_rows(inputs: StatusInputs, alarms_on: dict[str, Alarm]) -> list[dict]:
    """The analyzer's last hourly and daily runs (CS10).

    Args:
        inputs: The status inputs.
        alarms_on: The alarms in ALARM, by id.

    Returns:
        Two rows, hourly then daily.
    """
    now_ms, rows = inputs.now_ms, []
    texts = {"hourly": {"published": "prices published", "up_to_date": "nothing new", "no_base": "no baselines yet"},
             "daily": {"published": "baselines published", "rebuilding": "history rebuilding, nothing published",
                       "empty": "no history yet"}}
    for mode in ("hourly", "daily"):
        state = inputs.analyzer.get(mode, {})
        ran = isinstance(state.get("finished_ms"), int)
        row = {"name": f"Analyzer, {mode}", "colour": function_colour("analyzer", ran, inputs, alarms_on)}
        if not ran:
            rows.append({**row, "value": "no run recorded yet", "sub": ""})
            continue
        started = state["finished_ms"] - int(state.get("duration_ms") or 0)
        outcome = str(state.get("outcome"))
        value = f"{clock_text(started, now_ms)} · {texts[mode].get(outcome, outcome)}"
        if mode == "hourly":
            until = state.get("observations_until_ms")
            sub = (f"triggered by the {clock_text(until, now_ms)} snapshot" if outcome == "published" and
                   isinstance(until, int) else "waiting for the first daily run" if outcome == "no_base" else "")
        else:
            parts = [count_text(state["items"], "item", "items")] if isinstance(state.get("items"), int) else []
            if isinstance(state.get("built_ms"), int):
                parts.append(f"history up to {clock_text(state['built_ms'], now_ms)}")
            sub = " · ".join(parts)
        rows.append({**row, "value": value, "sub": sub})
    return rows


def functions(inputs: StatusInputs, alarms_on: dict[str, Alarm]) -> dict:
    """The Functions card: each function's last run, the dead-letter queue and the alarms (CS10).

    Args:
        inputs: The status inputs.
        alarms_on: The alarms in ALARM, by id.

    Returns:
        {"alarms": header text, "rows": [...]}.
    """
    on = [name for name in ALARM_IDS if name in alarms_on]
    queue = ({"name": "Failed events", "colour": "red", "value": "events waiting in the dead-letter queue", "sub": ""}
             if DEAD_LETTERS in alarms_on else
             {"name": "Failed events", "colour": "green", "value": "dead-letter queue empty", "sub": ""})
    return {"alarms": "alarms: all OK" if not on else f"alarms in ALARM: {', '.join(on)}",
            "rows": [collector_row(inputs, alarms_on), ingest_row(inputs, alarms_on),
                     *analyzer_rows(inputs, alarms_on), queue]}


def time_and_age(ms: int | None, now_ms: int, prefix: str = "") -> str:
    """A time and its age as of N, "14:18 · 12 min" (CS10).

    Args:
        ms: The time, UTC ms; None when there is none yet.
        now_ms: N.
        prefix: Text before the time, e.g. "up to ".

    Returns:
        The text; "none yet" without a time.
    """
    return "none yet" if not isinstance(ms, int) else f"{prefix}{clock_text(ms, now_ms)} · {age_text(now_ms - ms)}"


def freshness(inputs: StatusInputs, newest: PointerFacts | None) -> dict:
    """The Freshness card, as of N: the newest snapshot, the history, the prices and the baselines (CS10).

    Args:
        inputs: The status inputs.
        newest: The newest schema's pointer, or None.

    Returns:
        {"as_of": header text, "rows": [{"name", "value"}]}.
    """
    now_ms = inputs.now_ms
    until = (newest.observations_until_ms or newest.built_ms) if newest else None
    return {"as_of": f"ages at {clock_text(now_ms, now_ms)}", "rows": [
        {"name": "Newest snapshot", "value": time_and_age(inputs.raw_ms[-1] if inputs.raw_ms else None, now_ms)},
        {"name": "History", "value": time_and_age(inputs.ingest.get("last_ingested_at_ms"), now_ms)},
        {"name": "Hourly prices", "value": time_and_age(until, now_ms, "up to ")},
        {"name": "Daily baselines", "value": time_and_age(inputs.analyzer.get("daily", {}).get("published_ms"), now_ms)}]}


def blizzard(inputs: StatusInputs) -> list[dict]:
    """Blizzard over the last 24 h: the interval between snapshots by §3.3's median, and mismatched answers (CS9).

    Args:
        inputs: The status inputs.

    Returns:
        Rows {"name", "value", "sub"}.
    """
    recent = [t for t in inputs.raw_ms if inputs.now_ms - DAY_MS <= t <= inputs.now_ms]
    gaps = [b - a for a, b in zip(recent, recent[1:])]
    typical = median(gaps)
    interval = ("not enough snapshots" if typical is None else
                f"typically {typical // MINUTE_MS} min · longest {age_text(max(gaps))}")
    return [{"name": "Snapshot interval", "value": interval, "sub": ""},
            {"name": "Mismatched answers", "value": f"{inputs.mismatch:,}",
             "sub": "the two halves of the house disagreed; refetched next run (BZ9)"}]


def document(inputs: StatusInputs) -> list[dict]:
    """The summary document: the schemas with a pointer, the largest size and the newest item count (CS10).

    Args:
        inputs: The status inputs.

    Returns:
        One row {"name", "value", "sub"}.
    """
    if not inputs.pointers:
        return [{"name": "Summary document", "value": "not published yet", "sub": ""}]
    schemas = sorted(inputs.pointers)
    parts = [f"schemas {schemas_text(schemas)}",
             f"largest {megabytes_text(max(p.bytes for p in inputs.pointers.values()))}"]
    if inputs.items is not None:
        parts.append(count_text(inputs.items, "item", "items"))
    return [{"name": "Summary document", "value": " · ".join(parts), "sub": ""}]


def market_label(market: str) -> str:
    """The market as the page's header shows it: "forever · normal · horde · US".

    Args:
        market: Market id, e.g. "forever.normal.horde.us".

    Returns:
        The text.
    """
    parts = market.split(".")
    return " · ".join(parts[:-1] + [parts[-1].upper()])


def alarm_id(name: str, prefix: str) -> str | None:
    """An alarm's id from its CloudFormation name, "<stack>-<id><hash>-<suffix>".

    Args:
        name: The alarm's full name.
        prefix: The stack's name and "-".

    Returns:
        Its id in ALARM_IDS, or None for an alarm that isn't the stack's.
    """
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix):]
    return next((known for known in ALARM_IDS if rest.startswith(known)), None)


def ints_only(state: object) -> dict:
    """A state file's object with every time, duration and count field that isn't an int left out.

    The status reads these files written by other functions (and older versions); a damaged field must cost
    that one value, never the whole status (FM81).

    Args:
        state: A parsed state file or entry (anything).

    Returns:
        The dict, minus non-int `*_ms` and count fields; {} for anything but a dict.
    """
    if not isinstance(state, dict):
        return {}
    counts = ("items", "ingested", "pending", "refused", "last_auctions")
    return {k: v for k, v in state.items()
            if not (k.endswith("_ms") or k in counts) or (isinstance(v, int) and not isinstance(v, bool))}


def build_status(inputs: StatusInputs) -> dict:
    """The status document (SPEC §2.4 The status page; CS2-CS11).

    Args:
        inputs: Everything it is made from.

    Returns:
        The document, every value and colour the page shows; `to_json` gives its bytes.
    """
    analyzer = inputs.analyzer if isinstance(inputs.analyzer, dict) else {}
    inputs = replace(inputs, collector=ints_only(inputs.collector), ingest=ints_only(inputs.ingest),
                     analyzer={mode: ints_only(analyzer.get(mode)) for mode in ("hourly", "daily") if mode in analyzer})
    alarms_on = {a.name: a for a in inputs.alarms if a.state == "ALARM"}
    newest = inputs.pointers[max(inputs.pointers)] if inputs.pointers else None
    slas = [collection_sla(inputs.raw_ms, inputs.now_ms),
            prices_sla(inputs.raw_ms, newest, inputs.analyzer.get("hourly", {}), inputs.now_ms),
            daily_sla(inputs.raw_ms, inputs.analyzer.get("daily", {}), inputs.now_ms),
            failures_sla(inputs, alarms_on), runtime_sla(inputs)]
    return {"schema": SCHEMA, "market": inputs.market, "market_label": market_label(inputs.market),
            "written_ms": inputs.now_ms, "written": f"{clock_text(inputs.now_ms, inputs.now_ms)} UTC",
            "age_limits_ms": list(AGE_LIMITS_MS), "daily_hour_utc": DAILY_HOUR_UTC,
            "headline": headline(slas, inputs.raw_ms), "slas": slas, "functions": functions(inputs, alarms_on),
            "weeks": weeks(inputs), "freshness": freshness(inputs, newest), "blizzard": blizzard(inputs),
            "document": document(inputs)}


def to_json(status: dict) -> str:
    """The status as JSON text: keys sorted, so the same inputs give the same bytes (CS11).

    Args:
        status: From build_status.

    Returns:
        The JSON text.
    """
    return json.dumps(status, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
