"""Tests for tools/sim/runner.py: simulated worlds through the real cloud job (SPEC §2.4, §8.10; SU9, SM8)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from ahsync.intel.contract import to_json  # noqa: E402
from sim.market import DAY_MS, Item, World  # noqa: E402
from sim.runner import RunSettings, SimCatalog, market_books, rebuild, simulate  # noqa: E402

START = 1_793_923_200_000                    # Fri 2026-11-06 00:00 UTC
ITEMS = (Item(2589, "Linen Cloth", True, 50, 300, 40, vendor_sell=13),
         Item(4306, "Silk Cloth", True, 150, 200, 25, vendor_sell=150),
         Item(6585, "Scouting Gloves", False, 900, 6, 10, stack=1, vendor_sell=180, item_class=4, quality=2))


def files(folder: Path) -> dict[str, bytes]:
    """Every file under a folder, by its path relative to it."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_the_catalog_answers_forevers_search_from_the_worlds_items():
    catalog = SimCatalog(World(1, START, 1, ITEMS))
    [linen] = catalog.search_items(2000, 2999)
    assert (linen["name"]["en_US"], linen["sell_price"], linen["quality"]["type"]) == ("Linen Cloth", 13, "COMMON")
    assert catalog.search_items(6000, 6999)[0]["item_class"]["id"] == 4
    assert catalog.search_items(2000, 2999, "static-classic1x-us") == []


def test_a_run_writes_hourly_snapshots_publishes_each_day_and_skips_what_it_is_told(tmp_path):
    run = simulate(World(2, START, 3, ITEMS), tmp_path, RunSettings(skip_daily=frozenset({1})))
    assert 60 <= len(run.snapshots) <= 72 and len(run.data.list("raw/")) == len(run.snapshots)
    built = [d.built_ms // DAY_MS - START // DAY_MS for d in run.documents]
    assert built == [0, 2, 2]               # day 1's run skipped; the last ingests day 2's tail (built at its newest)
    assert (tmp_path / "truth.jsonl").read_bytes() == run.truth.to_jsonl()
    doc = run.documents[-1]
    assert set(doc.items) == {2589, 4306, 6585} and doc.catalog[4306][3] == 150
    with pytest.raises(ValueError, match="unknown fault"):
        simulate(World(2, START, 1, ITEMS), tmp_path / "bad", RunSettings(faults={0: "flood"}))


def test_SU9_the_same_seed_gives_the_same_raw_truth_and_documents(tmp_path):
    world = World(9, START, 2, ITEMS)
    one, two = simulate(world, tmp_path / "one"), simulate(world, tmp_path / "two")
    assert files(tmp_path / "one" / "data" / "raw") == files(tmp_path / "two" / "data" / "raw")
    assert one.truth.to_jsonl() == two.truth.to_jsonl()
    assert [to_json(d) for d in one.documents] == [to_json(d) for d in two.documents]
    other = simulate(World(10, START, 2, ITEMS), tmp_path / "other")
    assert other.truth.to_jsonl() != one.truth.to_jsonl()


def test_SM8_a_rebuild_from_raw_gives_the_daily_runs_document_byte_for_byte_through_faults(tmp_path):
    world = World(8, START, 4, ITEMS)
    settings = RunSettings(skip_daily=frozenset({1}), faults={5: "conflict", 30: "duplicate"})
    run = simulate(world, tmp_path / "run", settings)
    rebuilt = rebuild(run, world, tmp_path / "rebuild")
    assert to_json(rebuilt) == to_json(run.documents[-1]), f"seed {world.seed}"


def test_SM8_capped_ingest_runs_give_the_uncapped_document_byte_for_byte(tmp_path):
    world = World(8, START, 4, ITEMS)
    faults = {5: "conflict", 30: "duplicate"}
    whole = simulate(world, tmp_path / "whole", RunSettings(skip_daily=frozenset({1}), faults=faults, ingest_cap=None))
    for cap in (5, 1):
        capped = simulate(world, tmp_path / f"cap{cap}", RunSettings(skip_daily=frozenset({1}), faults=faults,
                                                                      ingest_cap=cap))
        assert [to_json(d) for d in capped.documents] == [to_json(d) for d in whole.documents], f"seed {world.seed} cap {cap}"


def test_the_market_alone_gives_the_snapshots_simulate_stores(tmp_path):
    world = World(4, START, 1, ITEMS)
    run = simulate(world, tmp_path)
    truth, books = market_books(world)
    assert truth.to_jsonl() == run.truth.to_jsonl() and [b.at_ms for b in books] == run.snapshots
    assert books[0].auctions and all(a.key in {2589, 4306, 6585} for b in books for a in b.auctions)
