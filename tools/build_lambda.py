"""Stage the Lambda asset: ahsync/ and ahcloud/, no third-party code (SPEC §2.4, plan M30; AR9).

Usage: python tools/build_lambda.py [--out build/lambda]

Copies the two packages into a fresh folder that the CDK stack packages as the functions' code. Left out:
the dashboard's static files (web/static), bytecode caches and anything that is not Python, SQL or a data
file the code reads. boto3 comes from the Lambda runtime. The output is the same bytes for the same tree.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("ahsync", "ahcloud")
KEPT_SUFFIXES = (".py", ".sql", ".json")
SKIPPED_DIRS = ("__pycache__",)
SKIPPED_PATHS = (Path("ahsync") / "web" / "static",)


def asset_files(root: Path) -> list[Path]:
    """The files the asset holds, relative to the repository.

    Args:
        root: The repository root.

    Returns:
        Sorted relative paths: every .py, .sql and .json under ahsync/ and ahcloud/, except bytecode caches and
        ahsync/web/static.
    """
    found = []
    for package in PACKAGES:
        for path in (root / package).rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file() or path.suffix not in KEPT_SUFFIXES:
                continue
            if any(part in SKIPPED_DIRS for part in relative.parts):
                continue
            if any(skipped in relative.parents for skipped in SKIPPED_PATHS):
                continue
            found.append(relative)
    return sorted(found)


def build(root: Path, out: Path) -> list[Path]:
    """Replace out/ with a fresh copy of the asset files.

    Args:
        root: The repository root.
        out: The asset folder (removed first, so nothing stale survives).

    Returns:
        The files copied, relative to out/.

    Raises:
        ValueError: out/ is the repository root or contains it (refused rather than deleted).
    """
    out = out.resolve()
    if out == root.resolve() or out in root.resolve().parents:
        raise ValueError(f"refusing to replace {out}")
    if out.exists():
        shutil.rmtree(out)
    files = asset_files(root)
    for relative in files:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    return files


def main(argv: list[str] | None = None) -> int:
    """Build the asset.

    Args:
        argv: Arguments (sys.argv[1:] when None).

    Returns:
        0.
    """
    parser = argparse.ArgumentParser(description="Stage the Lambda asset (ahsync/ + ahcloud/).")
    parser.add_argument("--out", default=str(ROOT / "build" / "lambda"))
    args = parser.parse_args(argv)
    files = build(ROOT, Path(args.out))
    print(f"{len(files)} files staged in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
