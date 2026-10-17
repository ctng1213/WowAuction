"""Seeded case generator for invariant tests (SPEC §8.7).

Every invariant draws its cases from `random.Random(seed)`, so a failure can be
reproduced from the seed printed with it. Cases for ported modules are also saved
to tests/properties/*.json so the Lua port is checked against the same inputs.
"""
from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

ROOT = Path(__file__).resolve().parents[2]
PROPERTIES = ROOT / "tests" / "properties"
DEFAULT_CASES = 1000       # SPEC §8.7: at least 1,000 cases per invariant

T = TypeVar("T")


def cases(seed: int, make: Callable[[random.Random], T], n: int = DEFAULT_CASES) -> list[T]:
    """Generate n cases from a fixed seed.

    Args:
        seed: The seed; print it with any failure.
        make: Builds one case from the shared Random.
        n: Number of cases.

    Returns:
        The cases, in generation order.
    """
    rng = random.Random(seed)
    return [make(rng) for _ in range(n)]


def save(name: str, seed: int, generated: list) -> Path:
    """Save generated cases for the Lua port (deterministic JSON).

    Args:
        name: File stem under tests/properties/.
        seed: The seed they came from.
        generated: JSON-serializable cases.

    Returns:
        The written path.
    """
    PROPERTIES.mkdir(parents=True, exist_ok=True)
    path = PROPERTIES / f"{name}.json"
    path.write_text(json.dumps({"seed": seed, "cases": generated}, sort_keys=True, indent=0) + "\n",
                    encoding="utf-8", newline="\n")
    return path
