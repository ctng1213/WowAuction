"""Benchmark the full refresh against its budgets (SPEC §3.1.2).

Usage: python tools/bench.py [--items 9000] [--hours 2160]
       python tools/bench.py --summary <document.json.gz>

Builds a generated world in a temporary database (no network, nothing in data/):
`--items` priced items with `--hours` hourly closes, listed units and sales rates
each (2160 = the 90 days analysis reads, every hour observed: the worst case),
7 days of volume, a complete order book, two characters, 200 known Blacksmithing
recipes and 50 Enchanting ones, and stock with a cost basis on both characters (some
of it listed), each lot an open position (M14), so the exported candidates, graph and positions are non-trivial.

The refresh then runs in a fresh process against that database, so the time is not
slowed by memory tracing and the peak is the process's own (it includes the
interpreter, so it is conservative); then both addon files are written into a
temporary AddOns folder. Budgets: full refresh ≤ 120 s for about 9k items, ≤ 512 MB
peak, MarketIntelligence.lua ≤ 3 MB and PortfolioPlan.lua ≤ 200 KB (SPEC §3.1.2, §3.5).

`--summary` measures a cloud market instead (SPEC §2.4, plan summary-memory SM0): a real
published document is served from a temporary store, and a fresh process runs what the
app does each hour: the poll (fetch, parse, stage, re-plan, both addon files), then the
dashboard's read of the summary on its own connection, then the next hour's summary and
the dashboard's read again. It prints the resident size and the peak after each step.
Budget: a cloud market's process peak ≤ 600 MB with the 88 MB summary of 2026-11-08.
"""
from __future__ import annotations

import argparse
import gzip
import random
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ahcloud.publish import publish  # noqa: E402
from ahcloud.store import LocalStore  # noqa: E402
from ahsync.app.cloud_job import poll_cloud  # noqa: E402
from ahsync.collect.client import HttpRequest, HttpResponse  # noqa: E402
from ahsync.config import CloudConfig, Config, MarketSources  # noqa: E402
from ahsync.db.sqlite import Database  # noqa: E402
from ahsync.app.refresh import refresh_all  # noqa: E402
from ahsync.intel.contract import INTEL_SCHEMA_VERSION, from_json  # noqa: E402
from ahsync.web.server import open_server_db  # noqa: E402
from ahsync.export.addon_files import (DATA_FOLDER, MARKET_FILE, MAX_MARKET_BYTES, MAX_PLAN_BYTES,  # noqa: E402
                                       PLAN_FILE, export_market, export_plan)

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
NOW = 20_500 * DAY
BUDGET_S, BUDGET_MB = 120.0, 512.0
SUMMARY_BUDGET_MB = 600.0                      # a cloud market's process peak (plan summary-memory §1 item 4)
ORIGIN = "https://bench.cloudfront.net"
SEED = 42


def build_world(db: Database, n_items: int, hours: int) -> None:
    """Fill the database with a generated, reproducible market.

    Args:
        db: An empty database.
        n_items: Priced items.
        hours: Hourly closes, listed units and sales rates per item.
    """
    rng = random.Random(SEED)
    keys = list(range(1000, 1000 + n_items))
    with db.transaction():
        db.conn.executemany(
            "INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_sell, stack) VALUES (?,?,?,?,?,?,?,?)",
            [(k, k, f"Item {k}", rng.choice([1, 2, 3]), rng.randint(5, 60), rng.choice([2, 4, 7]),
              rng.randint(1, 500), 20) for k in keys])
        db.conn.executemany("INSERT INTO current(market, key, price, qty, at, src) VALUES (?,?,?,?,?,?)",
                            [(M, k, rng.randint(10, 50_000), rng.randint(1, 200), NOW - HOUR, "ahf") for k in keys])
        db.conn.executemany("INSERT INTO candles_h1(market, key, t, close, qty, src) VALUES (?,?,?,?,?,?)",
                            ((M, k, NOW - h * HOUR, rng.randint(10, 50_000), rng.randint(0, 300), "ahf")
                             for k in keys for h in range(hours)))
        db.conn.executemany("INSERT INTO sales_h1(market, key, t, sold_per_hour) VALUES (?,?,?,?)",
                            ((M, k, NOW - h * HOUR, rng.random() * 4) for k in keys for h in range(hours)))
        db.conn.executemany(
            "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?,?,?,?,?,?,?)",
            ((M, k, NOW // DAY - d, rng.random() * 50, rng.random() * 40, rng.random() * 10, "books")
             for k in keys for d in range(7)))
        db.conn.execute("INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?,?,?,?,?,?)",
                        ("bench", M, "local", NOW - 2 * HOUR, "full", 1.0))
        db.conn.executemany("INSERT OR IGNORE INTO book_entries(sid, key, price, stack, band, count) "
                            "VALUES ((SELECT sid FROM scans WHERE scan_id = ?),?,?,?,?,?)",
                            (("bench", k, rng.randint(10, 50_000), rng.choice([1, 5, 20]), rng.randint(1, 4),
                              rng.randint(1, 5)) for k in keys for _ in range(3)))
        db.conn.executemany("INSERT INTO characters(id, market, gold) VALUES (?,?,?)",
                            [("A-R", M, 1_000_000), ("B-R", M, 500_000)])
        db.conn.executemany("INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?,?,?,?)",
                            [("A-R", "Enchanting", 200, 225), ("B-R", "Blacksmithing", 200, 225)])
        for spell in range(200):
            product, mats = rng.sample(keys, 1)[0], rng.sample(keys, 3)
            db.conn.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                            "VALUES (?,?,?,?,?)", ("Blacksmithing", spell, product, 1, 1))
            db.conn.executemany("INSERT INTO reagents(profession, spell_id, key, count) VALUES (?,?,?,?)",
                                [("Blacksmithing", spell, m, rng.randint(1, 4)) for m in mats])
            db.conn.execute("INSERT INTO known_recipes(char_id, spell_id) VALUES (?,?)", ("B-R", spell))
        stock_and_enchanting(db, rng, keys)


def stock_and_enchanting(db: Database, rng: random.Random, keys: list[int]) -> None:
    """Add what the M11 exports need to the world: Enchanting recipes, stock with a cost basis, some listed.

    Drawn after everything else from the same seeded generator, so the rest of the world is unchanged.

    Args:
        db: The database being built (inside its transaction).
        rng: The world's generator.
        keys: The priced items.
    """
    for spell in range(1000, 1050):
        product, mats = rng.sample(keys, 1)[0], rng.sample(keys, 2)
        db.conn.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                        "VALUES (?,?,?,?,?)", ("Enchanting", spell, product, 1, 1))
        db.conn.executemany("INSERT INTO reagents(profession, spell_id, key, count) VALUES (?,?,?,?)",
                            [("Enchanting", spell, m, rng.randint(1, 3)) for m in mats])
        db.conn.execute("INSERT INTO known_recipes(char_id, spell_id) VALUES (?,?)", ("A-R", spell))
    held = rng.sample(keys, 120)
    stock = [("A-R" if i % 2 else "B-R", k, rng.randint(1, 40), rng.randint(10, 100)) for i, k in enumerate(held)]
    db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?,?,?,?,0)",
                        [(c, "bags", k, n) for c, k, n, _ in stock])
    db.conn.executemany("INSERT INTO cost_basis(key, qty, total_cost) VALUES (?,?,?)",
                        [(k, 40, 40 * each) for _c, k, _n, each in stock])
    positions(db, stock)
    db.conn.executemany("INSERT INTO own_auctions(char_id, key, price, stack, band, count, seen, end_ms) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        [("A-R" if i % 2 else "B-R", k, rng.randint(10, 50_000), 5, 3, 1, NOW - HOUR, NOW + 8 * HOUR)
                         for i, k in enumerate(held[:20])])


def positions(db: Database, stock: list[tuple[str, int, int, int]]) -> None:
    """One open position per stock row: a funded buy for later, bought a day ago (M14), so the replay, the
    evaluation and the export's positions are non-trivial.

    Args:
        db: The database being built (inside its transaction).
        stock: (character, key, units, copper each) held.
    """
    rows = [(f"{c}:{NOW - DAY}:{i}", c, k, n, each) for i, (c, k, n, each) in enumerate(stock)]
    db.conn.executemany("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES (?,?,?,?,?,'ah',?,1)", [(e, c, k, n, each, NOW - DAY) for e, c, k, n, each in rows])
    db.conn.executemany("INSERT INTO positions(position_id, char_id, market, cand_id, strategy, key, state, opened_at, "
                        "units, units_left, cost_total, cost_left, expected_net_open, realized) "
                        "VALUES (?,?,?,?,'buy_later',?,'Holding',?,?,?,?,?,0,0)",
                        [(e, c, M, f"buy_later:{k}:{c}:-/now/now:1", k, NOW - DAY, n, n, n * each, n * each)
                         for e, c, k, n, each in rows])
    db.conn.executemany("INSERT INTO position_lots(position_id, event_id, units, unit_cost, at) VALUES (?,?,?,?,?)",
                        [(e, e, n, each, NOW - DAY) for e, _c, _k, n, each in rows])


def windows_memory_counters():
    """This process's memory counters on Windows.

    Returns:
        A PROCESS_MEMORY_COUNTERS structure (PeakWorkingSetSize and WorkingSetSize in bytes).

    Raises:
        OSError: GetProcessMemoryInfo failed.
    """
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        """PROCESS_MEMORY_COUNTERS."""
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    return counters


def peak_memory_mb() -> float:
    """This process's peak memory so far.

    Returns:
        Peak working set (Windows) or maximum resident set size (elsewhere), in MB.
    """
    if sys.platform == "win32":
        return windows_memory_counters().PeakWorkingSetSize / 1_048_576
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1_048_576 if sys.platform == "darwin" else peak / 1024      # bytes on macOS, KB on Linux


def resident_memory_mb() -> float:
    """This process's resident memory now.

    Returns:
        Working set (Windows) or resident set size from `ps` (elsewhere), in MB.
    """
    if sys.platform == "win32":
        return windows_memory_counters().WorkingSetSize / 1_048_576
    import os
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True)
    return int(out.stdout.strip()) / 1024


class StoreOrigin:
    """A transport serving a LocalStore's objects at ORIGIN, as CloudFront serves the intel bucket."""

    def __init__(self, store: LocalStore) -> None:
        """Serve this store.

        Args:
            store: The store holding the published documents and pointers.
        """
        self.store = store

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Answer one GET.

        Args:
            request: The request; its URL starts with ORIGIN.

        Returns:
            200 with the object and its ETag, 304 when If-None-Match matches it, 404 when it is missing.
        """
        found = self.store.get(request.url.removeprefix(ORIGIN + "/"))
        if found is None:
            return HttpResponse(404, {}, b"")
        etag = f'"{found.etag}"'
        if request.headers.get("If-None-Match") == etag:
            return HttpResponse(304, {"etag": etag}, b"")
        return HttpResponse(200, {"etag": etag}, found.data)


class BenchClock:
    """A clock that stays where it is set and never sleeps."""

    def __init__(self, now_ms: int) -> None:
        """Start at a time.

        Args:
            now_ms: UTC ms.
        """
        self.now = now_ms

    def now_ms(self) -> int:
        """Now.

        Returns:
            The time set, UTC ms.
        """
        return self.now

    def sleep(self, seconds: float) -> None:
        """Do not wait.

        Args:
            seconds: Ignored.
        """

    def local_offset_ms(self, at_ms: int) -> int:
        """UTC as local time.

        Args:
            at_ms: Ignored.

        Returns:
            0.
        """
        return 0


def publish_summaries(document: Path, tmp: Path) -> tuple[str, int]:
    """Publish a real document, and the next hour's copy of it, into two stores for the child process.

    Args:
        document: A published `<sha256>.json.gz` (any schema this AHSync reads).
        tmp: The temporary folder; the stores go to tmp/hour1 and tmp/hour2.

    Returns:
        (the document's market, its observations_until_ms).
    """
    intel = from_json(gzip.decompress(document.read_bytes()).decode("utf-8"))
    publish(LocalStore(tmp / "hour1"), intel, (INTEL_SCHEMA_VERSION,))
    later = replace(intel, built_ms=intel.built_ms + HOUR, observations_until_ms=intel.observations_until_ms + HOUR)
    publish(LocalStore(tmp / "hour2"), later, (INTEL_SCHEMA_VERSION,))
    return intel.market, intel.observations_until_ms


def summary_only(tmp: Path, market: str, now_ms: int) -> int:
    """Run the app's hourly cloud steps in this (fresh) process and compare the peak with the budget.

    Args:
        tmp: The folder publish_summaries wrote; the database and the WoW install go there too.
        market: The document's market.
        now_ms: Its observations_until_ms: the first poll's time.

    Returns:
        Process exit code: 0 within budget, 1 over it or when a poll did not publish.
    """
    install = tmp / "wow"
    install.joinpath(*DATA_FOLDER).mkdir(parents=True, exist_ok=True)
    cfg = Config(markets=(market,), wow_path=str(install), cloud=CloudConfig(ORIGIN),
                 sources={market: MarketSources("blizzard", ("blizzard",), {})})
    db_path = tmp / "bench.db"
    db = Database.open(db_path, now_ms, None)
    dashboard = open_server_db(db_path)
    clock, ok = BenchClock(now_ms), True
    print(f"bench: start, resident {resident_memory_mb():.0f} MB", flush=True)
    for hour in (1, 2):
        clock.now = now_ms + (hour - 1) * HOUR
        started = time.perf_counter()
        ok = poll_cloud(db, StoreOrigin(LocalStore(tmp / f"hour{hour}")), clock, cfg) == [market] and ok
        print(f"bench: hour {hour} poll, re-plan and export {time.perf_counter() - started:.1f} s, resident "
              f"{resident_memory_mb():.0f} MB, peak {peak_memory_mb():.0f} MB", flush=True)
        ok = dashboard.read_intel(market) is not None and ok
        print(f"bench: hour {hour} dashboard read, resident {resident_memory_mb():.0f} MB, "
              f"peak {peak_memory_mb():.0f} MB", flush=True)
    dashboard.close()
    db.close()
    peak = peak_memory_mb()
    ok = ok and peak <= SUMMARY_BUDGET_MB
    print(f"bench: cloud market peak {peak:.0f} MB (budget {SUMMARY_BUDGET_MB:.0f} MB) -> "
          f"{'OK' if ok else 'OVER BUDGET'}")
    return 0 if ok else 1


def refresh_only(db_path: Path) -> int:
    """Time one full refresh of a built world in this (fresh) process and compare with the budgets.

    Args:
        db_path: The database built by build_world.

    Returns:
        Process exit code: 0 within budget, 1 over it.
    """
    db = Database.open(db_path, NOW, None)
    started = time.perf_counter()
    result = refresh_all(db, NOW, Config(markets=(M,)))
    seconds = time.perf_counter() - started
    peak_mb = peak_memory_mb()
    sizes_ok = export_sizes(db, db_path.parent)
    db.close()
    ok = seconds <= BUDGET_S and peak_mb <= BUDGET_MB and result.error is None and sizes_ok
    print(f"bench: full refresh {seconds:.1f} s (budget {BUDGET_S:.0f} s), peak {peak_mb:.0f} MB "
          f"(budget {BUDGET_MB:.0f} MB), {result.items} items, {len(result.failed)} failed -> "
          f"{'OK' if ok else 'OVER BUDGET'}")
    return 0 if ok else 1


def export_sizes(db: Database, tmp: Path) -> bool:
    """Write both addon files of the refreshed world and compare their sizes with their budgets (SPEC §3.5, M11).

    Args:
        db: The refreshed database.
        tmp: A temporary folder for the WoW install.

    Returns:
        True when both files were written within their budgets.
    """
    install = tmp / "wow"
    folder = install.joinpath(*DATA_FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    cfg = Config(markets=(M,), wow_path=str(install))
    started = time.perf_counter()
    written = export_market(db, cfg, NOW, lambda _s: None) and export_plan(db, cfg, NOW, lambda _s: None)
    seconds = time.perf_counter() - started
    market, plan = (folder / MARKET_FILE).stat().st_size, (folder / PLAN_FILE).stat().st_size
    text = (folder / MARKET_FILE).read_text(encoding="utf-8")
    counts = {name: sum(1 for line in text.splitlines() if line.startswith(name)) for name in ("K[", "R[", "U[")}
    plan_text = (folder / PLAN_FILE).read_text(encoding="utf-8")
    block = plan_text.split("  positions = {", 1)[1].split("  records = {", 1)[0]
    positions = sum(1 for line in block.splitlines() if line.strip().startswith('"'))
    print(f"bench: {MARKET_FILE} {market / 1_048_576:.2f} MB (budget {MAX_MARKET_BYTES / 1_048_576:.0f} MB, "
          f"{counts['K[']} candidates, {counts['R[']} recipes, {counts['U[']} reverse entries), {PLAN_FILE} "
          f"{plan / 1024:.1f} KB (budget {MAX_PLAN_BYTES // 1024} KB, {positions} positions), written in {seconds:.2f} s")
    trimmed = db.status("candidate_export")
    if trimmed is not None:
        print(f"bench: candidate export trimmed to {trimmed.data['kept']} of {trimmed.data['total']} (FM40)")
    return written and market <= MAX_MARKET_BYTES and plan <= MAX_PLAN_BYTES


def main() -> int:
    """Build the world, then time one full refresh of it in a fresh process.

    Returns:
        Process exit code: 0 within budget, 1 over it.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", type=int, default=9000)
    parser.add_argument("--hours", type=int, default=2160)
    parser.add_argument("--refresh", type=Path, help=argparse.SUPPRESS)       # the child process's database
    parser.add_argument("--summary", type=Path, help="a published document.json.gz: measure a cloud market")
    parser.add_argument("--summary-child", nargs=3, help=argparse.SUPPRESS)    # tmp, market, now_ms
    args = parser.parse_args()
    if args.refresh:
        return refresh_only(args.refresh)
    if args.summary_child:
        tmp, market, now_ms = args.summary_child
        return summary_only(Path(tmp), market, int(now_ms))
    if args.summary:
        with tempfile.TemporaryDirectory() as tmp:
            market, now_ms = publish_summaries(args.summary, Path(tmp))
            print(f"bench: published {args.summary.name} for {market} as schema {INTEL_SCHEMA_VERSION}", flush=True)
            return subprocess.run([sys.executable, __file__, "--summary-child", tmp, market, str(now_ms)]).returncode
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        db = Database.open(db_path, NOW, None)
        started = time.perf_counter()
        build_world(db, args.items, args.hours)
        db.close()
        print(f"bench: built {args.items} items x {args.hours} h in {time.perf_counter() - started:.1f} s", flush=True)
        return subprocess.run([sys.executable, __file__, "--refresh", str(db_path)]).returncode


if __name__ == "__main__":
    sys.exit(main())
