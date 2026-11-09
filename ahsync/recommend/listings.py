"""Your auctions listed now, as the plan's and the pages' liquidity caps count them (SPEC §3.4 Liquidity, R31, R33)."""
from __future__ import annotations

from ahsync.engine.volume import BAND_HOURS
from ahsync.state.reads import StateReader

HOUR_MS = 3_600_000


def own_listed(state: StateReader, char_ids: list[str],
               now_ms: int | None) -> dict[int, tuple[tuple[int, int | None], ...]]:
    """Your auctions listed now, per item, across your characters (SPEC §3.4 Liquidity, R31).

    Args:
        state: Character state (the addon's last view of your auctions).
        char_ids: Character ids.
        now_ms: Now; an auction that ended before it is left out. None keeps every one.

    Returns:
        Item key -> ((units, end ms or None when unknown), ...), sorted. An auction without an end time ends at the
        latest when its time-left band, counted from the character's last read of its auctions, runs out; without
        that read too, its end stays unknown.
    """
    seen = state.auctions_read_ms(char_ids)
    out: dict[int, list[tuple[int, int | None]]] = {}
    for char_id, key, _, stack, band, count, _, end_ms in state.own_auctions(char_ids):
        read_ms = seen.get(char_id)
        if end_ms is None and read_ms is not None:
            end_ms = read_ms + int(BAND_HOURS.get(band, BAND_HOURS[4])[1] * HOUR_MS)
        if now_ms is None or end_ms is None or end_ms > now_ms:
            out.setdefault(key, []).append((stack * count, end_ms))
    return {key: tuple(sorted(rows, key=lambda r: (r[0], r[1] is None, r[1] or 0))) for key, rows in out.items()}


def listed_units(state: StateReader, char_ids: list[str], now_ms: int) -> dict[int, int]:
    """Your units listed now, per item (own_listed summed).

    Args:
        state: Character state.
        char_ids: Character ids.
        now_ms: Now.

    Returns:
        Item key -> units.
    """
    return {key: sum(units for units, _ in rows) for key, rows in own_listed(state, char_ids, now_ms).items()}
