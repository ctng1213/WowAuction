"""Tests for ahsync/engine/transit.py: mail on its way to one of your characters (SPEC §3.4B, T22)."""
from __future__ import annotations

from ahsync.engine.transit import DAY_MS, Sent, Transit, in_transit, units

MINUTE, HOUR = 60_000, 3_600_000
T = 1_760_000_000_000
BAR = 2840
AMAYA, BRUGAN = "Amaya-G", "Brugan-G"
SENT = Sent("s1", BRUGAN, AMAYA, BAR, 6, T)


def test_T22_a_send_counts_for_the_recipient_until_a_newer_snapshot_of_hers_shows_it_or_30_days_pass():
    before = {(AMAYA, "mail"): T - HOUR, (AMAYA, "bags"): T - 30 * MINUTE, (BRUGAN, "bags"): T + MINUTE}
    assert in_transit([SENT], before, T + 2 * HOUR) == [Transit(AMAYA, BRUGAN, BAR, 6, T)]
    assert in_transit([SENT], {**before, (AMAYA, "bags"): T + 5 * MINUTE}, T + 2 * HOUR) == []   # her bags show it
    assert in_transit([SENT], {**before, (AMAYA, "mail"): T + 5 * MINUTE}, T + 2 * HOUR) == []   # her mailbox shows it
    assert in_transit([SENT], {**before, (BRUGAN, "bags"): T - 10 * MINUTE}, T + HOUR) == []     # still in his bags
    assert in_transit([SENT], {}, T + 30 * DAY_MS) == [Transit(AMAYA, BRUGAN, BAR, 6, T)]          # no snapshots at all
    assert in_transit([SENT], before, T + 30 * DAY_MS + 1) == []                                   # mail came back


def test_units_on_their_way_add_up_per_recipient_and_item():
    second = Sent("s2", "Cara-G", AMAYA, BAR, 4, T + MINUTE)
    records = in_transit([second, SENT], {}, T + HOUR)
    assert [r.sender for r in records] == [BRUGAN, "Cara-G"] and units(records) == {(AMAYA, BAR): 10}
