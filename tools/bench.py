"""Benchmark the full refresh against its budgets (SPEC §3.1.2).

Usage: python tools/bench.py [--items 9000] [--hours 168]

Builds a generated world in a temporary database (no network, nothing in data/):
`--items` priced items with `--hours` hourly closes each, 7 days of volume, a
complete order book, two characters, and 200 known recipes. Then it times one full
refresh and reports its peak Python memory against the budgets:
full refresh ≤ 60 s for about 9k items, ≤ 400 MB peak.
"""
from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ahsync.config import Config  # noqa: E402
from ahsync.store.db import Database  # noqa: E402
from ahsync.sync.refresh import refresh_all  # noqa: E402

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
NOW = 20_500 * DAY
BUDGET_S, BUDGET_MB = 60.0, 400.0
SEED = 42


def build_world(db: Database, n_items: int, hours: int) -> None:
    """Fill the database with a generated, reproducible market.

    Args:
        db: An empty database.
        n_items: Priced items.
        hours: Hourly closes per item.
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
        db.conn.executemany("INSERT INTO candles_h1(market, key, t, close, src) VALUES (?,?,?,?,?)",
                            ((M, k, NOW - h * HOUR, rng.randint(10, 50_000), "ahf") for k in keys for h in range(hours)))
        db.conn.executemany(
            "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?,?,?,?,?,?,?)",
            ((M, k, NOW // DAY - d, rng.random() * 50, rng.random() * 40, rng.random() * 10, "books")
             for k in keys for d in range(7)))
        db.conn.execute("INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?,?,?,?,?,?)",
                        ("bench", M, "local", NOW - 2 * HOUR, "full", 1.0))
        db.conn.executemany("INSERT OR IGNORE INTO book_entries(scan_id, key, price, stack, band, count) "
                            "VALUES (?,?,?,?,?,?)",
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


def main() -> int:
    """Build the world, time one full refresh and compare with the budgets.

    Returns:
        Process exit code: 0 within budget, 1 over it.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", type=int, default=9000)
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        db = Database.open(Path(tmp) / "bench.db", NOW, None)
        started = time.perf_counter()
        build_world(db, args.items, args.hours)
        print(f"bench: built {args.items} items x {args.hours} h in {time.perf_counter() - started:.1f} s")
        tracemalloc.start()
        started = time.perf_counter()
        result = refresh_all(db, NOW, Config(markets=(M,)))
        seconds = time.perf_counter() - started
        peak_mb = tracemalloc.get_traced_memory()[1] / 1_048_576
        tracemalloc.stop()
        db.close()
    ok = seconds <= BUDGET_S and peak_mb <= BUDGET_MB and result.error is None
    print(f"bench: full refresh {seconds:.1f} s (budget {BUDGET_S:.0f} s), peak {peak_mb:.0f} MB "
          f"(budget {BUDGET_MB:.0f} MB), {result.items} items, {len(result.failed)} failed -> "
          f"{'OK' if ok else 'OVER BUDGET'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
