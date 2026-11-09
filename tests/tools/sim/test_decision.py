"""Tests for tools/sim/decision.py: one world traded by the plan and each benchmark (SPEC §8.10; plan §1.6)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from sim.decision import STARTER_CHARACTERS, benchmark, starter  # noqa: E402


def test_the_starter_world_is_scored_for_the_plan_the_naive_trader_nothing_and_the_oracle(tmp_path):
    plan, naive, nothing, oracle = benchmark(starter(1), STARTER_CHARACTERS, tmp_path)
    assert [s.name for s in (plan, naive, nothing, oracle)] == ["plan", "naive", "nothing", "oracle"]
    assert nothing.made == 0 and nothing.stranded_units == 260 and oracle.made > plan.made > 0
    assert 0 < plan.oracle_share < 1 and plan.stranded_units == 0       # the plan sold the starting stock
    assert naive.stranded_units > 0                                    # it bought the dump and could not resell it
