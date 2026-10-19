"""Tests for ahsync/collect/provider.py: the snapshot contract and its checks (FM33)."""
from __future__ import annotations

from ahsync.collect.provider import SnapshotObservation, load_bonus_suffixes, snapshot_problems
from ahsync.engine.volume import Auction

M = "forever.pvp.horde.us"
GOOD = Auction(1, 2589, 20, 50, 3)


def test_FM33_a_malformed_snapshot_is_refused_whole_with_every_reason():
    assert snapshot_problems(SnapshotObservation(M, "blizzard", 1000, (GOOD,))) == []
    bad = SnapshotObservation(M, "local", 0, (GOOD, GOOD, Auction(2, 2589, 0, 50, 5)))
    assert snapshot_problems(bad) == [
        "source name 'local' is not allowed", "no observation time", "an auction id appears twice",
        "an auction has no units, no price, an unknown time-left band or no item"]
    assert snapshot_problems(SnapshotObservation(M, "Blizzard!", 1000, ())) == ["source name 'Blizzard!' is not allowed"]


def test_SRC5_the_python_bonus_table_matches_the_addons():
    import re
    from pathlib import Path
    suffixes = load_bonus_suffixes()
    lua = (Path(__file__).resolve().parents[3] / "addon/AHProfit/Data/BonusSuffixes.lua").read_text(encoding="utf-8")
    rows = re.search(r'BONUS_SUFFIXES = "([^"]*)"', lua).group(1)
    assert suffixes == {int(b): int(s) for b, s in (r.split(":") for r in rows.split(";"))}
    assert suffixes[12742] == 14322                             # "of Frost Protection" (SPEC §6 item 11)
