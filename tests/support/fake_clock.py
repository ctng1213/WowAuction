"""A fake clock for tests (SPEC §8.1: no real clock). Implements ahsync.clock.Clock."""
from __future__ import annotations

# 2026-10-16 12:00:00 UTC, the day the fixtures were recorded (sealed d1 = 20742).
DEFAULT_NOW_MS = 1_792_152_000_000


class FakeClock:
    """Time that moves only when told to. `sleep` advances it and is recorded in `sleeps`."""

    def __init__(self, now_ms: int = DEFAULT_NOW_MS, local_offset_ms: int = 0) -> None:
        """Create a clock.

        Args:
            now_ms: Starting time, UTC ms.
            local_offset_ms: Local time minus UTC, ms (0 = the machine is on UTC).
        """
        self._now_ms = now_ms
        self._offset_ms = local_offset_ms
        self.sleeps: list[float] = []

    def now_ms(self) -> int:
        """Current fake time.

        Returns:
            UTC ms.
        """
        return self._now_ms

    def sleep(self, seconds: float) -> None:
        """Record a wait and advance time by it.

        Args:
            seconds: Requested wait, seconds.
        """
        self.sleeps.append(seconds)
        self._now_ms += int(seconds * 1000)

    def advance(self, ms: int) -> None:
        """Move time forward without recording a sleep.

        Args:
            ms: Milliseconds to add.
        """
        self._now_ms += ms

    def set(self, now_ms: int) -> None:
        """Jump to a time.

        Args:
            now_ms: New time, UTC ms.
        """
        self._now_ms = now_ms

    def local_offset_ms(self, at_ms: int) -> int:
        """Fixed local offset.

        Args:
            at_ms: Ignored.

        Returns:
            The offset given at construction, ms.
        """
        return self._offset_ms
