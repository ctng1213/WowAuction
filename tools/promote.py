"""Promote a GitHub pre-release to the latest release (SPEC §3.7.10).

Usage: python tools/promote.py v0.4.0

Runs `gh release edit <tag> --prerelease=false --latest`, but only after checking that the
Release exists, is still a pre-release, lists every asset and is not lower than the current
latest release (DR27). Any refusal prints the reason and edits nothing.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable

GH_TIMEOUT_S = 60
TAG_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$", re.ASCII)

Gh = Callable[[list[str]], tuple[int, str]]


def run_gh(args: list[str]) -> tuple[int, str]:
    """Run the real `gh` CLI with an argument list (no shell).

    Args:
        args: The arguments after `gh`.

    Returns:
        (exit code, stdout text); (1, "") when gh is missing or times out.
    """
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout


def parse_tag(tag: str) -> tuple[int, int, int] | None:
    """Split a release tag into its numeric parts.

    Args:
        tag: A string such as "v0.4.0".

    Returns:
        (major, minor, patch), or None when the tag is not vMAJOR.MINOR.PATCH.
    """
    m = TAG_RE.fullmatch(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _json(text: str) -> object:
    """Parse gh's JSON output.

    Args:
        text: The stdout of a gh call.

    Returns:
        The parsed value, or None when the text is not valid JSON.
    """
    try:
        return json.loads(text)
    except ValueError:
        return None


def check(tag: str, gh: Gh) -> str | None:
    """Check that a tag may be promoted, without changing anything.

    Args:
        tag: The release tag, e.g. "v0.4.0".
        gh: Runs `gh` with an argument list and returns (exit code, stdout).

    Returns:
        The refusal reason, or None when the release may be promoted.
    """
    parts = parse_tag(tag)
    if parts is None:
        return "release tags are vMAJOR.MINOR.PATCH"
    code, out = gh(["release", "view", tag, "--json", "tagName,isPrerelease,assets"])
    info = _json(out) if code == 0 else None
    if not isinstance(info, dict):
        return f"no release {tag}"
    if not info.get("isPrerelease"):
        return f"{tag} is already the latest release, not a pre-release"
    ver = tag[1:]
    names = {a.get("name") for a in info.get("assets") or [] if isinstance(a, dict)}
    for name in (f"AHSync-{ver}-windows-x64-setup.exe", f"AHSync-{ver}-macos-arm64.dmg",
                 f"AHSync-{ver}-macos-arm64.zip", "SHA256SUMS"):
        if name not in names:
            return f"release {tag} lacks {name}"
    code, out = gh(["release", "list", "--exclude-pre-releases", "--json", "tagName,isLatest"])
    rows = _json(out) if code == 0 else None
    if not isinstance(rows, list):        # without the latest release the lower-version check can't be made
        return "could not read the latest release"
    for row in rows:
        latest = row.get("tagName") if isinstance(row, dict) and row.get("isLatest") else None
        latest_parts = parse_tag(latest) if isinstance(latest, str) else None
        if latest_parts and parts < latest_parts:
            return f"{tag} is lower than the latest release {latest}"
    return None


def promote(tag: str, gh: Gh, say: Callable[[str], None], err: Callable[[str], None]) -> int:
    """Check a release, then make it the latest non-pre-release.

    Args:
        tag: The release tag, e.g. "v0.4.0".
        gh: Runs `gh` with an argument list and returns (exit code, stdout).
        say: Receives normal messages.
        err: Receives refusal and failure messages.

    Returns:
        0 when promoted, 1 on a refusal or a failed edit (nothing is edited after a refusal).
    """
    reason = check(tag, gh)
    if reason:
        err(reason)
        return 1
    code, _ = gh(["release", "edit", tag, "--prerelease=false", "--latest"])
    if code != 0:
        err(f"gh release edit {tag} failed")
        return 1
    say(f"promoted {tag} to the latest release")
    return 0


def main(argv: list[str] | None = None, gh: Gh = run_gh) -> int:
    """Parse the command line and promote the named release.

    Args:
        argv: Arguments after the program name; defaults to sys.argv[1:].
        gh: The gh runner; tests pass a fake.

    Returns:
        Process exit code: 0 promoted, 1 refused or failed, 2 on a usage error.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python tools/promote.py vMAJOR.MINOR.PATCH", file=sys.stderr)
        return 2
    return promote(args[0], gh, print, lambda m: print(m, file=sys.stderr))


if __name__ == "__main__":
    sys.exit(main())
