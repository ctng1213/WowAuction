"""The AI assistant tests' shared world: WP1's fixture world, refreshed once into a database file (SPEC §8.6)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ahsync.app.refresh import refresh_all
from ahsync.config import Config
from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock
from tools.snapshot import WORLD, load_world

MARKET = "forever.pvp.horde.us"


@dataclass(frozen=True)
class World:
    """A refreshed fixture world on disk: its database file, config and now."""

    db_path: Path
    config: Config
    now_ms: int

    def clock(self) -> FakeClock:
        """A clock at the world's now, on UTC.

        Returns:
            A FakeClock.
        """
        return FakeClock(self.now_ms)


@pytest.fixture(scope="session")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    """WP1's world, refreshed once for every MCP test (the tools only read it)."""
    data = json.loads(WORLD.read_text(encoding="utf-8"))
    folder = tmp_path_factory.mktemp("mcp-world")
    install = folder / "wow"
    (install / "Interface" / "AddOns").mkdir(parents=True)
    config = Config(markets=tuple(data["config"]["markets"]), wow_path=str(install))
    db = Database.open(folder / "ahsync.db", data["now_ms"], None)
    load_world(db, data)
    result = refresh_all(db, data["now_ms"], config)
    assert not (result.error or result.failed)
    db.conn.close()
    return World(folder / "ahsync.db", config, data["now_ms"])
