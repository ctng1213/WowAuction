"""Which operating systems CI tests a change on (SPEC §3.7.12, DR35).

    python tools/ci_scope.py <base> <head>    # prints the GitHub Actions outputs `cloud_only` and `oses`

The cloud job (`ahcloud/`, `infra/`) runs on AWS Lambda's Linux, and the app never imports it, so a change that
touches only the cloud is tested once on Linux and the app is not built. Anything else, including `ahsync/`
(shared by the app, the Lambda and the addon's exports), is tested on Windows and macOS as before. Documentation
counts for neither side. When the change cannot be listed (a manual run, a new branch, a tag), everything runs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable, Sequence

CLOUD_PREFIXES = ("ahcloud/", "infra/", "tests/ahcloud/", "tests/infra/")
CLOUD_FILES = frozenset({"tools/build_lambda.py", "tests/tools/test_build_lambda.py", "requirements-cloud.txt",
                         "requirements-infra.txt"})
FULL_OSES = ("windows-latest", "macos-15")
CLOUD_OSES = ("ubuntu-latest",)
UNKNOWN = "0" * 40                      # GitHub's `before` on a branch's first push


def is_documentation(path: str) -> bool:
    """Whether a changed file is documentation, which narrows nothing either way.

    Args:
        path: A repository path with forward slashes.

    Returns:
        True for `docs/**` and any `*.md` file.
    """
    return path.startswith("docs/") or path.endswith(".md")


def is_cloud(path: str) -> bool:
    """Whether a changed file belongs to the cloud job only.

    Args:
        path: A repository path with forward slashes.

    Returns:
        True for the cloud's code, infrastructure, their tests, the Lambda asset builder and the cloud's requirements.
    """
    return path.startswith(CLOUD_PREFIXES) or path in CLOUD_FILES


def cloud_only(paths: Iterable[str]) -> bool:
    """Whether a change touches the cloud and nothing else but documentation (DR35).

    Args:
        paths: The changed files.

    Returns:
        True when at least one changed file is the cloud's and every other one is documentation; False for any
        other change, and for none (nothing to narrow).
    """
    code = [p for p in paths if not is_documentation(p)]
    return bool(code) and all(is_cloud(p) for p in code)


def changed_files(base: str, head: str) -> list[str] | None:
    """The files changed between two commits, from git.

    Args:
        base: The earlier commit (a pull request's base, a push's `before`); "" or all zeros when unknown.
        head: The later commit.

    Returns:
        The paths, or None when the change cannot be listed (no base, or git cannot diff the two).
    """
    if not base or base == UNKNOWN or not head:
        return None
    result = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], capture_output=True, text=True,
                            check=False)
    return result.stdout.split() if result.returncode == 0 else None


def outputs(paths: Sequence[str] | None) -> dict[str, str]:
    """The workflow outputs for a change.

    Args:
        paths: The changed files; None when unknown.

    Returns:
        {"cloud_only": "true" | "false", "oses": a JSON list of runner labels}.
    """
    narrow = paths is not None and cloud_only(paths)
    return {"cloud_only": "true" if narrow else "false", "oses": json.dumps(list(CLOUD_OSES if narrow else FULL_OSES))}


def main(argv: Sequence[str]) -> int:
    """Print the outputs as `key=value` lines, for `$GITHUB_OUTPUT`.

    Args:
        argv: [base, head]; missing ones count as unknown.

    Returns:
        Exit code 0.
    """
    base, head = (list(argv) + ["", ""])[:2]
    for key, value in outputs(changed_files(base, head)).items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
