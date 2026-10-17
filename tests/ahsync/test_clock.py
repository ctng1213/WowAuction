"""The real clock (ahsync.clock). Tests otherwise use FakeClock."""
from __future__ import annotations

import time

from ahsync.clock import SystemClock


def test_system_clock_reads_wall_time_in_ms():
    clock = SystemClock()
    before = int(time.time() * 1000)
    assert abs(clock.now_ms() - before) < 5_000
    clock.sleep(0)
    clock.sleep(-1)
    offset = clock.local_offset_ms(clock.now_ms())
    assert isinstance(offset, int) and abs(offset) <= 14 * 3_600_000
