"""Sales a commodity listing made before its first snapshot (SPEC §2.4 Auction lifetimes, BZ45).

Pure. A listing is first seen at the end of the gap it was posted in, so whatever it sold since posting is never
observed; the market simulator measured that at 4–9% of a commodity's units in common market shapes (MS1, the
unseen-sales research of 2026-11-08). The estimate: a listing sells in its first observed gap at some rate, and
was on the AH for about half its posting gap before it was first seen (posting is equally likely anywhere in the
gap), so it had sold about rate × half the posting gap by then. The units are credited to the gap that measured
the rate, never to the posting gap, so a later snapshot never changes an earlier gap (BZ44's review). Gear is left
out: a single item cannot be partly sold before it is seen, and the simulator found its unseen share negligible.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping


def unseen_sales(gaps: Iterable[tuple[int, int]], first_seen: Iterable[tuple[int, int, tuple[int, int], bool]],
                 fills: Iterable[tuple[int, int, int, tuple]],
                 sold_endings: Mapping[tuple[int, tuple[int, int]], float]) -> dict[tuple[int, int], dict[int, float]]:
    """Estimated units sold before first sight, per gap and item.

    Args:
        gaps: The kept linked gaps, (earlier ms, later ms); only these are credited.
        first_seen: (auction, key, posting gap, commodity) per listing first seen after any linked gap of the pass,
            kept or not (its posting gap may lie before the kept range while its first observed gap is kept), the
            commodity flag being the listing's own.
        fills: (auction, key, units bought, gap) per partial fill.
        sold_endings: (auction, gap) -> units its ending counted sold in that gap (cancels left out).

    Returns:
        gap -> item key -> units, float: each commodity listing's units sold in its first observed gap × half its
        posting gap ÷ that gap's length, credited to the first observed gap; a listing whose next gap isn't a kept
        linked gap gets nothing.
    """
    after = {g[0]: g for g in gaps}
    sold: dict[tuple[int, tuple[int, int]], float] = dict(sold_endings)
    for auction, _, units, gap in fills:
        sold[(auction, gap)] = sold.get((auction, gap), 0.0) + units
    out: dict[tuple[int, int], dict[int, float]] = {}
    for auction, key, posted, commodity in first_seen:
        seen = after.get(posted[1])
        if not commodity or seen is None:
            continue
        units = sold.get((auction, seen), 0.0)
        if units > 0.0:
            row = out.setdefault(seen, {})
            row[key] = row.get(key, 0.0) + units * (posted[1] - posted[0]) / 2 / (seen[1] - seen[0])
    return out
