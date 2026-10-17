"""Time source for AHSync's edges (CLAUDE.md: I/O takes a `clock` argument).

The engine never reads the clock; the sync, API and scheduler code take a
`Clock` so tests can pass `tests/support/fake_clock.FakeClock` instead.
"""
from __future__ import annotations

import time
from typing import Protocol

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


class Clock(Protocol):
    """What AHSync needs from a clock: the time, a way to wait, and the local offset."""

    def now_ms(self) -> int:
        """Current time.

        Returns:
            UTC Unix time in ms.
        """
        ...

    def sleep(self, seconds: float) -> None:
        """Block for a while.

        Args:
            seconds: How long to wait, in seconds.
        """
        ...

    def local_offset_ms(self, at_ms: int) -> int:
        """Offset of the machine's local time from UTC at a given moment.

        Args:
            at_ms: UTC Unix time in ms.

        Returns:
            local time minus UTC, in ms (e.g. +7_200_000 for UTC+2).
        """
        ...


class SystemClock:
    """The real wall clock. Used only by `ahsync.__main__`."""

    def now_ms(self) -> int:
        """Current wall-clock time.

        Returns:
            UTC Unix time in ms.
        """
        return time.time_ns() // 1_000_000

    def sleep(self, seconds: float) -> None:
        """Block the calling thread.

        Args:
            seconds: How long to wait, in seconds; negative values do not wait.
        """
        if seconds > 0:
            time.sleep(seconds)

    def local_offset_ms(self, at_ms: int) -> int:
        """Offset of local time from UTC, honouring daylight saving at that moment.

        Args:
            at_ms: UTC Unix time in ms.

        Returns:
            local time minus UTC, in ms.
        """
        offset = time.localtime(at_ms // 1000).tm_gmtoff
        return int(offset or 0) * 1000
