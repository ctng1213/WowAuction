"""Raw snapshots of a small house for the cloud job's tests (SPEC §2.4, BZ10-BZ16, BZ32-BZ34).

A (13:02): Linen 2455 ×14 at 125 c (very long), Fortified Spaulders 39643 at 15 s (short).
B (14:02): Linen 2455 ×10; the Spaulders gone (a short auction: expired).
C (16:02): Linen 2455 ×10 and a new 2470 ×20 at 118 c.
"""
from __future__ import annotations

import json

from ahsync.collect.blizzard import raw_envelope

M = "forever.normal.horde.us"
HOUR = 3_600_000
A, A_MS = "Fri, 06 Nov 2026 13:02:11 GMT", 1_793_970_131_000
B, B_MS = "Fri, 06 Nov 2026 14:02:40 GMT", 1_793_973_760_000
C, C_MS = "Fri, 06 Nov 2026 16:02:05 GMT", 1_793_980_925_000
LINEN, SPAULDERS = 2589, 8180


def house(stamp: str, regular: list, commodities: list) -> bytes:
    """A raw envelope of one snapshot of house 113."""
    def body(records):
        return json.dumps({"id": 113, "auctions": records}).encode()
    return raw_envelope(2, 113, stamp, 0, body(regular), body(commodities))


def linen(auction_id: int, qty: int, price: int = 125) -> dict:
    """A Linen Cloth commodity auction, very long."""
    return {"id": auction_id, "item": {"id": LINEN}, "quantity": qty, "unit_price": price, "time_left": "VERY_LONG"}


SNAP_A = house(A, [{"id": 39643, "item": {"id": SPAULDERS}, "quantity": 1, "buyout": 1500, "time_left": "SHORT"}],
               [linen(2455, 14)])
SNAP_B = house(B, [], [linen(2455, 10)])
SNAP_C = house(C, [], [linen(2455, 10), linen(2470, 20, 118)])

SEARCH = {LINEN: {"id": LINEN, "name": {"en_US": "Linen Cloth"}, "quality": {"type": "COMMON"}, "item_class": {"id": 7},
                  "item_subclass": {"id": 5}, "level": 5, "purchase_price": 55, "sell_price": 13},
          SPAULDERS: {"id": SPAULDERS, "name": {"en_US": "Fortified Spaulders"}, "quality": {"type": "UNCOMMON"},
                      "item_class": {"id": 4}, "item_subclass": {"id": 2}, "level": 20, "purchase_price": 2060,
                      "sell_price": 412}}


class FakeCatalog:
    """Stands in for BlizzardClient.search_items: answers from SEARCH, records each range asked for."""

    def __init__(self, records: dict | None = None) -> None:
        self.records = SEARCH if records is None else records
        self.ranges: list[tuple[int, int]] = []

    def search_items(self, first_id: int, last_id: int) -> list[dict]:
        self.ranges.append((first_id, last_id))
        return [r for i, r in sorted(self.records.items()) if first_id <= i <= last_id]
