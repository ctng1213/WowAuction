"""Generate the LP1 property cases: seeded inputs plus Python's exact results (SPEC §8.7 LP1).

Usage: python tools/gen_properties.py

Writes tests/properties/<module>.json for each ported engine module. The Lua specs
load them (converted by tools/gen_lua_fixtures.py) and must reproduce every result
exactly. tests/ahsync/engine tests regenerate the cases in memory and fail if the
committed file is stale, so re-run this after changing a ported formula.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ahsync.engine import sellprob  # noqa: E402
from tests.support.gen import save  # noqa: E402

SEEDS = {"sellprob": 7_402_026}
PER_CALL = 200


def sellprob_cases(seed: int) -> list[dict]:
    """Seeded cases for every ported sellprob function, with Python's result.

    Args:
        seed: The seed.

    Returns:
        [{"call", "args", "result"}, ...], PER_CALL per function, in a fixed order.
    """
    rng = random.Random(seed)
    out = []

    def add(call: str, **args) -> None:
        """Record one case with its Python result."""
        out.append({"call": call, "args": args, "result": getattr(sellprob, call)(**args)})

    for _ in range(PER_CALL):
        add("expected_post", p=rng.random(), qty=rng.randint(1, 200), price=rng.randint(1, 500_000),
            fallback=rng.random() * 5000, deposit=rng.randint(0, 20_000))
        has_history = rng.random() < 0.8
        add("blend", p_queue=rng.random(), tier_sell_through=rng.random() if has_history else None,
            tier_n=rng.randint(0, 60))
        add("with_record", p=rng.random(), sold=rng.randint(0, 12), expired=rng.randint(0, 12),
            weight=rng.choice([1, 2, 4, 8]))
        row = sorted((rng.random() for _ in range(8)), reverse=True)
        add("from_curve", row=row, ahead=rng.random() * 120, ahead_points=list(sellprob.CURVE_AHEAD))
        refresh = rng.randint(0, 2_000_000_000_000)
        add("curve_row", refresh_ms=refresh, now_ms=refresh + rng.randint(0, 72 * 3_600_000))
        add("quantize", p=rng.random())
        add("tier_of", price=rng.randint(1, 100_000), cur_others=rng.choice([None, rng.randint(1, 100_000)]))
    return out


def build() -> dict[str, tuple[int, list[dict]]]:
    """All property files' contents.

    Returns:
        Module name -> (seed, cases).
    """
    return {"sellprob": (SEEDS["sellprob"], sellprob_cases(SEEDS["sellprob"]))}


def main() -> int:
    """Write every property file.

    Returns:
        Process exit code: 0.
    """
    for name, (seed, generated) in build().items():
        path = save(name, seed, generated)
        print(f"gen_properties: {len(generated)} cases -> {path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
