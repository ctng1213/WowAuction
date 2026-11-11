"""Per-stage profiling of a cloud run: each stage's wall time and the memory high-water mark after it (SPEC §2.4
Profiling; BZ64, BZ65).

One `Profile` per run; `stage(name)` times a block, and a stage entered twice adds up; `line()` is the run's one
JSON log line. It is printed, not sent as a metric, so it costs no custom metric; Logs Insights and the dashboard's
profile widget read it. Nothing here goes into a state file or a document, so it never touches byte identity.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from typing import Callable, Generator

BYTES_PER_MB = 1_048_576


def peak_rss_mb() -> int | None:
    """This process's memory high-water mark so far.

    Returns:
        The maximum resident set size in whole MB (`ru_maxrss` is KB on Linux, Lambda's OS, and bytes on macOS);
        None where `resource` doesn't exist (Windows).
    """
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // BYTES_PER_MB if sys.platform == "darwin" else peak // 1024


class Profile:
    """One run's stages: each one's whole milliseconds, and the memory high-water mark after it."""

    def __init__(self, name: str, timer: Callable[[], float] = time.perf_counter,
                 memory: Callable[[], int | None] = peak_rss_mb) -> None:
        """Start the run's clock.

        Args:
            name: The run's kind, "ingest" or "daily".
            timer: Seconds from a monotonic clock (tests pass a fake).
            memory: The memory high-water mark in MB, None when unknown (tests pass a fake).
        """
        self.name, self._timer, self._memory = name, timer, memory
        self._start = timer()
        self.stages_ms: dict[str, int] = {}
        self.rss_mb: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Generator[None, None, None]:
        """Time a block as one stage, even when it raises; a stage entered again adds to its time.

        Args:
            name: The stage, e.g. "download".

        Yields:
            Nothing; the block runs inside.
        """
        start = self._timer()
        try:
            yield
        finally:
            self.stages_ms[name] = self.stages_ms.get(name, 0) + int((self._timer() - start) * 1000)
            peak = self._memory()
            if peak is not None:
                self.rss_mb[name] = peak

    def line(self, **facts: object) -> str:
        """The run's one log line.

        Args:
            **facts: What the run did, e.g. outcome="ingested", snapshots=2 (names, counts and states only).

        Returns:
            JSON with sorted keys: profile, the facts, total_ms (whole ms since the start), stages_ms and rss_mb.
        """
        total_ms = int((self._timer() - self._start) * 1000)
        return json.dumps({"profile": self.name, **facts, "total_ms": total_ms, "stages_ms": self.stages_ms,
                           "rss_mb": self.rss_mb}, sort_keys=True)
