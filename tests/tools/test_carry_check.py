"""Tests for tools/carry_check.py: the carried lifetimes against a history the batch pass wrote (SPEC §2.4, BZ67)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import carry_check  # noqa: E402

from ahsync.collect.provider import SnapshotObservation  # noqa: E402
from ahsync.collect.snapshots import SnapshotStore  # noqa: E402
from ahsync.db.sqlite import Database  # noqa: E402
from ahsync.engine.volume import Auction  # noqa: E402
from tests.support import batch_lifetimes as batch  # noqa: E402

M = "forever.normal.horde.us"
H, DAY, D = 3_600_000, 86_400_000, 20_000
LINEN = 2589


def batch_history(tmp_path: Path) -> tuple[Path, Path]:
    """A history the batch ingest wrote: Linen listed every other hour over three days, some sold, some bought down."""
    db_path, store_root = tmp_path / "history.db", tmp_path / "snapshots"
    db, store = Database.open(db_path, 0, None), SnapshotStore(store_root)
    for h in range(72):
        listed = tuple(Auction(1000 + k, LINEN, 5 - (h - k) // 3 if h - k < 9 else 1, 50, 4)
                       for k in range(max(0, h - 10), h + 1, 2))
        batch.batch_ingest(db, store, SnapshotObservation(M, "blizzard", (D - 1) * DAY + h * H, listed))
    db.close()
    return db_path, store_root


def test_BZ67_the_dry_run_finds_the_batch_history_unchanged_and_leaves_it_alone(tmp_path, capsys):
    db_path, store_root = batch_history(tmp_path)
    before = db_path.read_bytes()
    assert carry_check.main(["--db", str(db_path), "--snapshots", str(store_root), "--market", M, "--day", str(D)]) == 0
    report = capsys.readouterr().out
    assert '"differences": 0' in report and '"steps": 47' in report     # the day's other 23 hours and the next day
    assert db_path.read_bytes() == before                               # read only


def test_a_changed_row_is_reported_with_its_table(tmp_path):
    db_path, store_root = batch_history(tmp_path)
    db = Database.open(db_path, 0, None)
    with db.transaction():
        db.conn.execute("UPDATE volume_d1 SET sold = sold + 1 WHERE day = ? AND src = 'blizzard'", (D,))
    db.close()
    work = tmp_path / "work"
    work.mkdir()
    report = carry_check.run(db_path, store_root, M, D, work)
    assert report["day"]["tables"]["volume_d1"]["differences"] == 2   # the stored row and the carried one, apart
    assert report["cutover"]["tables"]["volume_d1"]["differences"] == 0       # the cutover rewrites only the open day
    assert report["differences"] == 2
    empty = carry_check.run(db_path, tmp_path / "none", M, D, work)
    assert empty["day"] == {"skipped": f"no stored snapshot in day {D}"}
