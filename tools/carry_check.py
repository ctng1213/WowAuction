"""The carried lifetimes against a history the batch pass wrote: the cutover's dry run (SPEC §2.4; carry-forward plan
CF2's gate and D4's check before deploying).

    python tools/carry_check.py --db <history.db> --snapshots <compact store folder> [--day <N>] [--market <id>]

Reads only local copies (fetch them read-only first, e.g. with `python -m ahcloud ingest --out`); every write goes to
a copy of the database in a temporary folder. Two checks, each comparing the rows the analysis reads (`volume_d1`
and its cover, `lifetimes_d1`, `new_listings_d1`, `sold_prices_d1`, `sales_h1`) with the original, byte for byte:

- **cutover**: rebuild the state at the history's newest snapshot (what the first run with the carried state does,
  BZ67); every row must be unchanged.
- **closed day** (with --day, a day the history has closed): rebuild the state at the day's first snapshot, then step
  every later stored snapshot one at a time as Ingest does; the day's rows and its hours must be unchanged.

Prints a JSON report (rows compared, differences with the first few, and the time each part took) and exits 1 on any
difference.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahsync.collect.observations import DAY_MS, advance_to, bootstrap  # noqa: E402
from ahsync.collect.snapshots import SnapshotStore  # noqa: E402
from ahsync.db.sqlite import Database  # noqa: E402

SRC = "blizzard"
TABLES = ("volume_d1", "volume_cover", "lifetimes_d1", "new_listings_d1", "sold_prices_d1", "sales_h1")
SAMPLE = 5                         # differences printed per table


def rows(db: Database, table: str, day: int | None) -> list[tuple]:
    """A derived table's rows, whole or of one day (its hours for `sales_h1`), in a full order.

    Args:
        db: The database.
        table: One of TABLES.
        day: A day number, or None for every row.

    Returns:
        The rows, sorted.
    """
    if day is None:
        found = db.conn.execute(f"SELECT * FROM {table}").fetchall()
    elif table == "sales_h1":
        found = db.conn.execute("SELECT * FROM sales_h1 WHERE t >= ? AND t < ?", (day * DAY_MS, (day + 1) * DAY_MS)).fetchall()
    else:
        found = db.conn.execute(f"SELECT * FROM {table} WHERE day = ?", (day,)).fetchall()
    return sorted(found, key=repr)


def compare(original: Database, carried: Database, day: int | None) -> dict:
    """The derived rows of two databases side by side.

    Args:
        original: The history as the batch pass wrote it.
        carried: The copy the carried state rewrote.
        day: Compare only this day (and its hours), or None for everything.

    Returns:
        Table -> {"rows": rows in the original, "differences": count, "sample": up to SAMPLE (original, carried)
        pairs, as text}.
    """
    out = {}
    for table in TABLES:
        a, b = rows(original, table, day), rows(carried, table, day)
        only_a, only_b = sorted(set(a) - set(b), key=repr), sorted(set(b) - set(a), key=repr)
        out[table] = {"rows": len(a), "differences": len(only_a) + len(only_b),
                      "sample": [[repr(x) for x in only_a[:SAMPLE]], [repr(x) for x in only_b[:SAMPLE]]]}
    return out


def check_cutover(original: Database, carried: Database, store: SnapshotStore, market: str) -> dict:
    """Rebuild the state at the history's newest snapshot in the copy, then compare every derived row (BZ67).

    Args:
        original: The history, read only.
        carried: Its copy, rewritten.
        store: The compact snapshot store.
        market: Market id.

    Returns:
        {"newest": its time ms, "seconds": the rebuild's time, "tables": `compare`}; {"skipped": reason} without a
        history.
    """
    newest = carried.newest_scan_at(market, SRC)
    if newest is None:
        return {"skipped": "the history has no snapshot"}
    start = time.perf_counter()
    with carried.transaction():
        bootstrap(carried, store, market, SRC, newest)
    return {"newest": newest, "seconds": round(time.perf_counter() - start, 1), "tables": compare(original, carried, None)}


def check_day(original: Database, carried: Database, store: SnapshotStore, market: str, day: int) -> dict:
    """Rebuild the state at a closed day's first snapshot, step every later one, then compare the day (§2.4).

    Args:
        original: The history, read only.
        carried: Its copy, rewritten.
        store: The compact snapshot store.
        market: Market id.
        day: The day number.

    Returns:
        {"steps", "bootstrap_seconds", "step_seconds": [mean, max], "tables": `compare` of the day}; {"skipped":
        reason} when the store holds no snapshot in the day.
    """
    times = store.times(market, SRC, day * DAY_MS, 1 << 62)
    if not times or times[0] >= (day + 1) * DAY_MS:
        return {"skipped": f"no stored snapshot in day {day}"}
    start = time.perf_counter()
    with carried.transaction():
        state = bootstrap(carried, store, market, SRC, times[0])
    boot, steps = time.perf_counter() - start, []
    for at in times[1:]:
        start = time.perf_counter()
        with carried.transaction():
            advance_to(carried, store, (market, SRC, at), state)
        state = carried.load_life_state(market, SRC)
        steps.append(time.perf_counter() - start)
    spent = [round(sum(steps) / len(steps), 2), round(max(steps), 2)] if steps else [0.0, 0.0]
    return {"steps": len(steps), "bootstrap_seconds": round(boot, 1), "step_seconds": spent,
            "tables": compare(original, carried, day)}


def run(db_path: Path, store_root: Path, market: str, day: int | None, work: Path) -> dict:
    """Both checks on copies of a history.

    Args:
        db_path: The history database, never written.
        store_root: The compact snapshot store's root (holding <market>/blizzard/), never written.
        market: Market id.
        day: A closed day to check, or None for the cutover only.
        work: An empty folder for the copies.

    Returns:
        {"cutover": ..., "day": ... (with --day), "differences": the total}.
    """
    store = SnapshotStore(store_root)
    report: dict = {}
    checks: list[tuple[str, Callable[[Database, Database], dict]]] = [
        ("cutover", lambda o, c: check_cutover(o, c, store, market))]
    if day is not None:
        checks.append(("day", lambda o, c: check_day(o, c, store, market, day)))
    pristine = work / "original.db"                      # opening migrates: read the original from a copy too
    shutil.copyfile(db_path, pristine)
    original = Database.open(pristine, 0, None)
    try:
        for name, check in checks:
            copy = work / f"{name}.db"
            shutil.copyfile(db_path, copy)
            carried = Database.open(copy, 0, None)
            try:
                report[name] = check(original, carried)
            finally:
                carried.close()
    finally:
        original.close()
    report["differences"] = sum(t["differences"] for part in report.values() for t in part.get("tables", {}).values())
    return report


def main(argv: list[str] | None = None) -> int:
    """The command line.

    Args:
        argv: Arguments; None reads sys.argv.

    Returns:
        0 when every row matched, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="a copy of state/history.db")
    parser.add_argument("--snapshots", type=Path, required=True, help="the compact store's root (holds <market>/blizzard/)")
    parser.add_argument("--market", default="forever.normal.horde.us")
    parser.add_argument("--day", type=int, help="a closed day to replay, as a day number (UTC ms ÷ 86,400,000)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as work:
        report = run(args.db, args.snapshots, args.market, args.day, Path(work))
    print(json.dumps(report, indent=1))
    return 1 if report["differences"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
