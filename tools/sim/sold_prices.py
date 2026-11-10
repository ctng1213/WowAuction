"""The sold-price rows (SPEC §8.10, `docs/plans/sold-price.md` §1c): what sold against what was asked.

An hourly close is the cheapest ask each hour, so a high ask that sits unsold fills most hours while cheap listings
that sell fill few (LESSONS 2026-11-07). SP1a publishes `sold_med7`, the units-weighted median of what sold; these
rows set it, med7 and today's post price against the simulator's truth:

- SM10, the high ask doesn't sell: cheap listings sell within hours of posting, a 15 g one sits unsold.
- SM11, demand catches up: the item's worth rises, buyers take the 15 g listing and later ones near it.
- SM18, a lone cheap listing under a commodity's level: where today's post price lands, and where a confirmed floor
  would for a grid of units (N) and auctions (K) within a band (plan §1.32; measured, the constants are real data's).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ahsync.engine.prices import median

from sim.inference import GOLD, START_MS, Measure, commodity, gear, sold_price_truth, within
from sim.market import DAY_MS, HOUR_MS, Planted, World
from sim.runner import market_books, simulate

GLOVES = 6585                        # SM10/SM11's gear item
CLOTH = 4306                         # SM18's commodity (Silk Cloth)
HIGH = 15 * GOLD                     # the lone high ask, copper
BAND = 0.10                          # SM10/SM11: sold_med7 within ±10% of the true sold price (SCENARIOS)
POST_BAND = 0.05                     # SM11 at SP2: the post price within ±5% (reported until then)
CLOTH_WORTH = 100                    # copper: SM18's fair price
LONE_PRICE = 60                      # copper: SM18's lone listing
FLOOR_N = (20, 50, 100)              # SM18's grid: units within FLOOR_BAND of a price confirm it
FLOOR_K = (2, 3, 5)                  # ... or this many auctions
FLOOR_BAND = 0.03


def gloves_world(seed: int, days: int, shift_ms: int | None = None) -> World:
    """SM10/SM11's market: thin gear selling at 2 g, a lone 15 g ask relisted for 30 h, and optionally a rise.

    About two 2 g listings a day, each bought within a few hours by one of about twelve buyers a day (who pay up to
    about 2.6 g), so most hours list nothing cheap; the 15 g listing is up from hour 1 to the world's end (relisted
    every 24 h), never bought at 2 g.

    Args:
        seed: The world's seed.
        days: Its length.
        shift_ms: From this time the item is worth 15 g (SM11); None = never. Buyers come as often as before, so
            what is listed is still seen before it sells.

    Returns:
        The World.
    """
    shifts = () if shift_ms is None else ((shift_ms, HIGH / (2 * GOLD), 1.0),)
    item = gear(GLOVES, "Scouting Gloves", 2 * GOLD, 12, 2, price_mix=((1.0, 1.0, 1.0),), anchor=1.0,
                buyer_level=1.3, buyer_spread=0.05, shifts=shifts)
    planted = tuple(Planted(START_MS + (1 + 24 * d) * HOUR_MS, GLOVES, 1, HIGH, 24) for d in range(days))
    return World(seed, START_MS, days, (item,), planted=planted)


def sm10(folder: Path, seed: int = 1) -> list[Measure]:
    """SM10: the high ask doesn't sell; med7 and the post price follow it, sold_med7 follows what sold.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.

    Returns:
        med7 and the post price against the true sold price (reported), and sold_med7 (±10%).
    """
    run = simulate(gloves_world(seed, 3), folder)
    i = run.documents[-1].items[GLOVES]
    sold = sold_price_truth(run.truth, GLOVES, START_MS, run.snapshots[-1])
    return [Measure("SM10", "med7 against the true sold price", i.med7, sold, "reported"),
            Measure("SM10", "post price against the true sold price", i.post_price, sold, "reported"),
            Measure("SM10", "sold_med7 against the true sold price", i.sold_med7, sold, "±10%",
                    within(i.sold_med7, sold, BAND))]


def sm11(folder: Path, seed: int = 1) -> list[Measure]:
    """SM11: demand catches up; sold_med7 rises to what sells now within a day of those sales.

    The rise comes on day 2 at 00:00; the document of day 3's run reads the week, two days of it at the new price.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.

    Returns:
        sold_med7 against the median of the buys since the rise (±10%), and the post price against it (reported
        until SP2, whose band is ±5%).
    """
    rise = START_MS + DAY_MS
    run = simulate(gloves_world(seed, 3, rise), folder)
    i = run.documents[-1].items[GLOVES]
    sold = sold_price_truth(run.truth, GLOVES, rise, run.snapshots[-1])
    return [Measure("SM11", "sold_med7 against the true sold price since the rise", i.sold_med7, sold, "±10%",
                    within(i.sold_med7, sold, BAND)),
            Measure("SM11", "post price against the true sold price since the rise (SP2: ±5%)", i.post_price, sold,
                    "reported")]


def cloth_world(seed: int, days: int) -> World:
    """SM18's market: Silk Cloth worth 1 s, a deep book, under a lone 5-unit listing at 60 c posted every 3 hours.

    Args:
        seed: The world's seed.
        days: Its length.

    Returns:
        The World; each lone listing is up 2 h unless bought first.
    """
    item = commodity(CLOTH, "Silk Cloth", CLOTH_WORTH, 40, 60, max_units=5)
    lone = tuple(Planted(START_MS + h * HOUR_MS + 50 * 60_000, CLOTH, 5, LONE_PRICE, 2) for h in range(0, days * 24, 3))
    return World(seed, START_MS, days, (item,), planted=lone)


def confirmed_floor(listings: Sequence[tuple[int, int]], units: int, auctions: int, band: float) -> int | None:
    """The cheapest price that enough of the book confirms (plan §1.32; measured here, built in SP2).

    Args:
        listings: (unit price copper, units) per auction.
        units: N: units listed from a price up to band above it that confirm it.
        auctions: K: auctions in that range that confirm it.
        band: The range above a price, as a share of it.

    Returns:
        The cheapest confirmed price, copper; None when nothing is confirmed.
    """
    rows = sorted(listings)
    for price, _ in rows:
        near = [qty for p, qty in rows if price <= p <= price * (1 + band)]
        if sum(near) >= units or len(near) >= auctions:
            return price
    return None


def sm18(folder: Path, seed: int = 1) -> list[Measure]:
    """SM18: a lone cheap listing; where today's undercut lands while it shows, and a confirmed floor's price.

    Measured on the snapshots themselves (the market alone, `runner.market_books`): a daily document rarely catches
    a lone listing, which buyers take within the hour.

    Args:
        folder: Unused (the market runs without the cloud job); kept for the report's row signature.
        seed: The world's seed.

    Returns:
        The share of snapshots whose cheapest auction is the lone listing; the true sold price over the run against
        the item's worth (1 s); over those snapshots the median of today's undercut (the cheapest − 1 c, SPEC §3.4
        PP1, below any cap here) and of each (N, K) confirmed floor less 1 c, against the true sold price. All
        reported. The sellers price off the cheapest auction (plan §1.27), so a lone listing that keeps coming
        back drags them too: the true sold price is the yardstick, not the worth.
    """
    truth, books = market_books(cloth_world(seed, 2))
    seen = []
    for book in books:
        listings = [(a.price, a.qty) for a in book.auctions if a.key == CLOTH]
        if listings and min(listings)[0] == LONE_PRICE:
            seen.append(listings)
    sold = sold_price_truth(truth, CLOTH, START_MS, books[-1].at_ms)
    out = [Measure("SM18", "share of snapshots led by the lone listing", len(seen) / len(books), None, "reported"),
           Measure("SM18", "true sold price against the worth", sold, CLOTH_WORTH, "reported"),
           Measure("SM18", "today's undercut while it shows (median)", median([LONE_PRICE - 1] * len(seen)), sold,
                   "reported")]
    for n in FLOOR_N:
        for k in FLOOR_K:
            floors = [f - 1 for f in (confirmed_floor(s, n, k, FLOOR_BAND) for s in seen) if f is not None]
            out.append(Measure("SM18", f"confirmed floor − 1 c, N {n} or K {k} within 3%, while it shows (median; "
                               f"{len(floors)} of {len(seen)} confirmed)", median(floors), sold, "reported"))
    return out


SOLD_PRICE_ROWS = {"SM10": sm10, "SM11": sm11, "SM18": sm18}   # tools/sim_report merges them with its ROWS
