"""The simulator's reports (SPEC §8.10; docs/plans/market-simulator.md). MS2 builds the inference report.

    python tools/sim_report.py inference [--seeds 1,2,3] [--rows SM1,SM2] [--out report.md]
    python tools/sim_report.py decision [--seeds 1,2,3] [--out report.md]

Every SM row built so far runs its world once per seed through the unchanged cloud job (`sim.inference`), and the
report lists each measure: the engine's number, the truth's, the gap and the row's band. A row that fails by design
(SM2, SM10; plan §1.30) shows its gap as a number, which is what M33b and the sold-price fix read. The same
seeds always give the same report. The decision report (MS3) trades a world with AHSync's plan through the real
addon import and with each benchmark (`sim.decision`), and lists their scores.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]

from sim import decision, inference  # noqa: E402
from sim.inference import Measure  # noqa: E402
from sim.scorers import GOLD, Score  # noqa: E402

ROWS: dict[str, Callable[[Path, int], list[Measure]]] = {
    "SM1": inference.sm1, "SM2": inference.sm2, "SM3": inference.sm3, "SM4": inference.sm4,
    "SM5": inference.sm5, "SM10": inference.sm10, "SM11": inference.sm11}


def number(value: float | None) -> str:
    """A measure's value as the report prints it.

    Args:
        value: A count, rate, share or copper amount; None when unknown.

    Returns:
        "—" for None; three decimals under 10; one decimal otherwise.
    """
    if value is None:
        return "—"
    return f"{value:.3f}" if abs(value) < 10 else f"{value:.1f}"


def line(seed: int, m: Measure) -> str:
    """One measure as a markdown table row.

    Args:
        seed: The world's seed.
        m: The measure.

    Returns:
        "| row | seed | what | engine | truth | gap | band | result |".
    """
    gap = "—" if m.gap is None else f"{m.gap * 100:+.1f}%"
    result = {True: "pass", False: "**fail**", None: ""}[m.passed]
    return f"| {m.row} | {seed} | {m.what} | {number(m.engine)} | {number(m.truth)} | {gap} | {m.band} | {result} |"


def inference_report(rows: Sequence[str], seeds: Sequence[int], folder: Path) -> str:
    """The inference report: every measure of the chosen rows for each seed.

    Args:
        rows: Scenario IDs, each a key of ROWS.
        seeds: World seeds.
        folder: An empty folder for the runs.

    Returns:
        Markdown.

    Raises:
        KeyError: An unknown row.
    """
    out = ["# Inference against truth", "",
           "The analysis's numbers on simulated markets beside what really happened (SPEC §8.10). Sales compare "
           "against what a snapshot can see (plan §1.28); a row that fails by design shows its gap (§1.30).", "",
           "| Row | Seed | Measure | Engine | Truth | Gap | Band | Result |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        run = ROWS[row]
        for seed in seeds:
            out += [line(seed, m) for m in run(folder / f"{row}-{seed}", seed)]
    return "\n".join(out) + "\n"


def score_line(seed: int, s: Score) -> str:
    """One benchmark's score as a markdown table row.

    Args:
        seed: The world's seed.
        s: The score.

    Returns:
        "| seed | benchmark | made g | gold-days | drawdown g | stranded units | stranded cost g | of the oracle |".
    """
    share = "—" if s.oracle_share is None else f"{s.oracle_share * 100:.0f}%"
    return (f"| {seed} | {s.name} | {s.made / GOLD:.2f} | {s.gold_days:.2f} | {s.drawdown / GOLD:.2f} | "
            f"{s.stranded_units} | {s.stranded_cost / GOLD:.2f} | {share} |")


def decision_report(seeds: Sequence[int], folder: Path) -> str:
    """The decision report: the starter world traded by the plan and each benchmark (plan §1.6).

    Args:
        seeds: World seeds.
        folder: An empty folder for the runs.

    Returns:
        Markdown.
    """
    out = ["# Decision quality", "",
           "MS3's starter world (stock to sell, a Silk dump on day 3) traded by AHSync's plan through the real import, "
           "by the naive trader and by nobody, against the oracle (plan §1.35: an optimistic yardstick). Gold made "
           "is realized; unsold stock is listed as stranded at its cost.", "",
           "| Seed | Benchmark | Made (g) | Gold-days | Drawdown (g) | Stranded units | Stranded cost (g) | Of the oracle |",
           "|---|---|---|---|---|---|---|---|"]
    for seed in seeds:
        scores = decision.benchmark(decision.starter(seed), decision.STARTER_CHARACTERS, folder / f"starter-{seed}")
        out += [score_line(seed, s) for s in scores]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run a report and print it, or write it to --out.

    Args:
        argv: Command-line arguments; sys.argv when None.

    Returns:
        The exit code: 0, or 2 for an unknown row.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("report", choices=["inference", "decision"])
    parser.add_argument("--seeds", default="1", help="comma-separated world seeds")
    parser.add_argument("--rows", default=",".join(ROWS), help="comma-separated SM rows")
    parser.add_argument("--out", type=Path, help="write the markdown here instead of printing it")
    args = parser.parse_args(argv)
    rows = [r for r in args.rows.split(",") if r]
    unknown = [r for r in rows if r not in ROWS]
    if unknown and args.report == "inference":
        print(f"unknown rows: {', '.join(unknown)} (built: {', '.join(ROWS)})", file=sys.stderr)
        return 2
    seeds = [int(s) for s in args.seeds.split(",") if s]
    with tempfile.TemporaryDirectory() as scratch:
        text = (inference_report(rows, seeds, Path(scratch)) if args.report == "inference"
                else decision_report(seeds, Path(scratch)))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
