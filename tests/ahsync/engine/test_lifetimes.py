"""Tests for ahsync/engine/lifetimes.py: every auction followed from first sight to its end (SPEC §2.4 Auction lifetimes).

Moved from test_volume.py with the module in CF1 (docs/plans/carry-forward-lifetimes.md), numbers unchanged; VI8 checks
that the fold of `step` is the batch pass it replaced (`tests/support/batch_lifetimes.py`).
"""
from __future__ import annotations

import pytest

from ahsync.engine.expiry import expired_fraction
from ahsync.engine.lifetime_prices import new_listing_positions, sold_prices
from ahsync.engine.lifetimes import (DURATION_MS, LifeFlow, Lifetimes, LifeState, band_window, gap_hours, life_flows, lifetimes,
                                     narrow, sold_by_hour, step)
from ahsync.engine.volume import Auction, IdBook
from tests.support import batch_lifetimes as batch
from tests.support.gen import cases

HOUR_MS = 3_600_000
LINEN = 2589
SHORT, MEDIUM, LONG, VERY_LONG = 1, 2, 3, 4
ORE = 2770
THU_1702 = (20_000 + 6) * 86_400_000 + 17 * HOUR_MS + 2 * 60_000     # a Thursday, 17:02 UTC
MINUTE = 60_000


def band_left(left_ms: int) -> int:
    """The time-left band Blizzard's API gives an auction with left_ms to run (ID_BAND_HOURS)."""
    hours_left = left_ms / HOUR_MS
    return SHORT if hours_left <= 0.5 else MEDIUM if hours_left <= 2 else LONG if hours_left <= 12 else VERY_LONG


def hourly(first_ms: int, count: int, auctions: list[tuple]) -> list[IdBook]:
    """Hourly snapshots; each auction (id, key, posted ms, expires ms, gone ms, qty at ms -> units) is listed from
    its posting until it expires or is gone, with the band its true time left gives."""
    out = []
    for k in range(count):
        at = first_ms + k * HOUR_MS
        listed = [Auction(i, key, qty(at), 125, band_left(expires - at))
                  for i, key, posted, expires, gone, qty in auctions if posted <= at < min(expires, gone)]
        out.append(IdBook(at, tuple(listed)))
    return out


def test_BZ22_lifetimes_name_each_listings_fate():
    thu_1700 = THU_1702 - 2 * MINUTE
    posted = thu_1700 + 30 * MINUTE
    snaps = hourly(thu_1700, 26, [
        (1, LINEN, posted, posted + 24 * HOUR_MS, 1 << 62, lambda at: 5),        # 24 h, runs out Fri 17:30
        (2, LINEN, posted, posted + 8 * HOUR_MS, thu_1700 + 4 * HOUR_MS - MINUTE, lambda at: 5),   # 8 h, gone at 20:59
        (3, ORE, posted + 30 * MINUTE - 1, posted + 30 * MINUTE + 20 * MINUTE, 1 << 62, lambda at: 2)])  # short at 18:00
    assert snaps[24].auctions[0].band == SHORT and snaps[3].auctions[1].band == LONG
    fates = {e.auction: (e.expired, e.gap) for e in lifetimes(snaps).endings}
    assert fates[1][1] == (thu_1700 + 24 * HOUR_MS, thu_1700 + 25 * HOUR_MS)                 # its end fell in 17:00-18:00
    assert 0.99 < fates[1][0] < 1.0      # BZ44: one early leave in about 26 listing-hours leaves a 1% chance it sold
    assert fates[2] == (0.0, (thu_1700 + 3 * HOUR_MS, thu_1700 + 4 * HOUR_MS))               # sold, 5-6 h left
    assert fates[3][0] == 1.0                                                                 # treated as 2 h: expired


def test_BZ26_the_bands_narrow_the_expiry_and_the_vanish_gap_decides():
    posted, expires = THU_1702 + 43 * MINUTE, THU_1702 + 24 * HOUR_MS + 43 * MINUTE     # truth: Fri 17:45
    qty = lambda at: 14 if at < THU_1702 + 6 * HOUR_MS else 10                          # 4 bought in (22:02, 23:02]
    snaps = hourly(THU_1702, 26, [(2455, LINEN, posted, expires, 1 << 62, qty)])
    lt = lifetimes(snaps)
    assert lt.fills == ((2455, LINEN, 4, (THU_1702 + 5 * HOUR_MS, THU_1702 + 6 * HOUR_MS), 125),)
    (ending,) = lt.endings
    assert (ending.qty, ending.expired, ending.gap) == (10, 1.0, (THU_1702 + 24 * HOUR_MS, THU_1702 + 25 * HOUR_MS))
    flow = life_flows(lt)[ending.gap][LINEN]
    assert (flow.sold, flow.expired, flow.ended, flow.expired_listings) == (0.0, 10.0, 1.0, 1.0)
    assert lt.clashes == 0
    early = hourly(THU_1702, 26, [(2455, LINEN, posted, expires, THU_1702 + 18 * HOUR_MS, qty)])   # gone by Fri 11:02
    (sold,) = lifetimes(early).endings
    assert (sold.expired, sold.gap) == (0.0, (THU_1702 + 17 * HOUR_MS, THU_1702 + 18 * HOUR_MS))


def test_BZ26_the_windows_come_from_posting_plus_duration_narrowed_by_bands():
    window, clash = narrow((THU_1702 + 24 * HOUR_MS, THU_1702 + 25 * HOUR_MS), THU_1702 + 24 * HOUR_MS, MEDIUM)
    assert (window, clash) == ((THU_1702 + 24 * HOUR_MS + 30 * MINUTE, THU_1702 + 25 * HOUR_MS), False)
    assert narrow(window, THU_1702, SHORT) == (band_window(THU_1702, SHORT), True)        # a clash keeps the newest
    assert expired_fraction(window, THU_1702 + 24 * HOUR_MS + 45 * MINUTE) == 0.5
    assert DURATION_MS == {VERY_LONG: 24 * HOUR_MS, LONG: 8 * HOUR_MS, MEDIUM: 2 * HOUR_MS, SHORT: 2 * HOUR_MS}
    assert band_window(THU_1702, LONG) == (THU_1702 + 2 * HOUR_MS, THU_1702 + 12 * HOUR_MS)        # Blizzard's bounds
    assert band_window(THU_1702, VERY_LONG) == (THU_1702 + 12 * HOUR_MS, THU_1702 + 24 * HOUR_MS)


def test_BZ27_relists_are_measured_and_never_subtracted():
    spaulders = 15210 + 28644 * 10 ** 6                       # a gear key with its bonus list's suffix
    at = THU_1702 + 17 * HOUR_MS                              # Fri 10:02
    before = IdBook(at, (Auction(1, spaulders, 1, 150_000, VERY_LONG), Auction(3, LINEN, 20, 125, VERY_LONG),
                         Auction(4, LINEN, 20, 125, VERY_LONG)))
    after = IdBook(at + HOUR_MS, (Auction(9, spaulders, 1, 140_000, VERY_LONG), Auction(11, LINEN, 20, 120, VERY_LONG)))
    lt = lifetimes([before, after])
    assert [(e.auction, e.matched) for e in lt.endings] == [(1, True), (3, True), (4, False)]   # one new listing, one match
    flows = life_flows(lt)[(at, at + HOUR_MS)]
    assert (flows[spaulders].sold, flows[spaulders].ended, flows[spaulders].matched) == (1.0, 1.0, 1.0)
    assert (flows[LINEN].sold, flows[LINEN].ended, flows[LINEN].matched) == (40.0, 2.0, 1.0)
    assert flows[spaulders].listed == 1.0 and flows[LINEN].listed == 20.0


def test_BZ30_sales_land_in_the_hour_they_happened():
    at = THU_1702 + 17 * HOUR_MS                              # 10:02
    one = lifetimes([IdBook(at, (Auction(1, LINEN, 10, 125, VERY_LONG),)), IdBook(at + HOUR_MS, ())])
    hour10 = at // HOUR_MS * HOUR_MS
    assert sold_by_hour(life_flows(one)) == {LINEN: {hour10: 10.0}}
    three = lifetimes([IdBook(at, (Auction(2, ORE, 9, 50, VERY_LONG),)), IdBook(at + 3 * HOUR_MS, ())])
    assert sold_by_hour(life_flows(three)) == {ORE: {hour10: 3.0, hour10 + HOUR_MS: 3.0, hour10 + 2 * HOUR_MS: 3.0}}
    assert gap_hours((at, at + 20 * MINUTE)) == [hour10]
    pooled = sold_by_hour({(at, at + 30 * MINUTE): {LINEN: LifeFlow(sold=2.0)},
                           (at + 30 * MINUTE, at + 50 * MINUTE): {LINEN: LifeFlow(sold=3.0)}})
    assert pooled == {LINEN: {hour10: pytest.approx(5.0 * 60 / 50)}}           # 5 units over 50 min
    assert sold_by_hour(life_flows(three), hours={hour10 + HOUR_MS}) == {ORE: {hour10 + HOUR_MS: 3.0}}


def test_lifetimes_keep_only_the_records_of_the_span_asked_for_but_every_window():
    at = THU_1702
    books = [IdBook(at, ()), IdBook(at + HOUR_MS, (Auction(1, LINEN, 10, 125, VERY_LONG),)),
             IdBook(at + 2 * HOUR_MS, (Auction(1, LINEN, 8, 125, VERY_LONG),)),            # a fill before the span
             IdBook(at + 3 * HOUR_MS, ())]
    full, kept = lifetimes(books), lifetimes(books, keep_from_ms=at + 3 * HOUR_MS)
    assert kept.gaps == ((at + 2 * HOUR_MS, at + 3 * HOUR_MS),) and kept.fills == () and kept.listed == ()
    assert kept.endings == full.endings                       # the posting gap, outside the span, still set the window


def test_lifetimes_do_not_link_across_a_hole():
    at = THU_1702
    lt = lifetimes([IdBook(at, (Auction(1, LINEN, 10, 125, VERY_LONG),)), IdBook(at + 7 * HOUR_MS, ())])
    assert lt.gaps == () and lt.endings == () and lt.listed == ()


SEED_VI5 = 5


def random_world(rng) -> list[IdBook]:
    """Hourly snapshots of a few auctions that shrink, vanish and appear at random (bands random too)."""
    live: dict[int, list] = {}
    snaps, next_id, at = [], 1, THU_1702
    for _ in range(rng.randint(2, 30)):
        for i in sorted(live):
            if rng.random() < 0.2:
                del live[i]
            elif rng.random() < 0.3:
                live[i][1] = max(1, live[i][1] - rng.randint(0, 5))
        for _ in range(rng.randint(0, 3)):
            live[next_id] = [rng.choice([LINEN, ORE, 15210]), rng.randint(1, 20), rng.choice([90, 100, 110])]
            next_id += 1
        snaps.append(IdBook(at, tuple(Auction(i, k, q, p, rng.randint(1, 4), k != 15210)       # gear is no commodity
                                      for i, (k, q, p) in sorted(live.items()))))
        at += rng.choice([HOUR_MS, HOUR_MS, 2 * HOUR_MS, 7 * HOUR_MS])
    return snaps


def test_VI5_lifetimes_account_for_every_unit_once():
    for n, snaps in enumerate(cases(SEED_VI5, random_world)):
        context = f"seed {SEED_VI5} case {n}"
        lt = lifetimes(snaps)
        flows = life_flows(lt)
        for e in lt.endings:
            assert 0.0 <= e.expired <= 1.0, context
        gone = sum(e.qty for e in lt.endings) + sum(f[2] for f in lt.fills)
        total = sum(f.sold - f.unseen + f.expired + f.cancelled for by_key in flows.values() for f in by_key.values())
        assert total == pytest.approx(gone), context              # observed sold + expired + cancelled = what left
        assert all(0.0 <= f.unseen <= f.sold + 1e-9 for by_key in flows.values() for f in by_key.values()), context
        for e in lt.endings:
            assert not e.cancelled or (e.expired == 0.0 and e.key != 15210), context   # BZ40: commodities, early only
        for by_key in flows.values():
            for f in by_key.values():
                assert min(f.sold, f.expired, f.listed, f.matched, f.cancelled, f.t1_left) >= 0, context
                assert f.expired_listings + f.cancels <= f.ended + 1e-9 and f.matched <= f.ended, context
                assert f.t1_left <= f.t1_ended + 1e-9 and f.t1_ended <= f.ended, context   # BZ41's share in [0, 1]
        bought: dict[int, int] = {}
        for auction, _, units, _, _ in lt.fills:
            bought[auction] = bought.get(auction, 0) + units
        first: dict[int, int] = {}
        last: dict[int, int] = {}
        for book in snaps:
            for a in book.auctions:
                first.setdefault(a.id, a.qty)
                last[a.id] = a.qty
        for auction, units in bought.items():
            assert units <= first[auction] - last[auction], context             # a partial fill never counts twice
        rates = sold_by_hour(flows)
        credited = sum(r for by_hour in rates.values() for r in by_hour.values())
        assert credited >= 0, context


def test_BZ40_a_commodity_cancel_is_no_sale():
    silk, gloves = 4306, 4243                            # Silk Cloth (a commodity); Embossed Leather Gloves (not)
    at = THU_1702
    a, b = Auction(1, silk, 5, 315, VERY_LONG, True), Auction(2, silk, 20, 320, LONG, True)
    cheap, dear = Auction(3, gloves, 1, 100, VERY_LONG), Auction(4, gloves, 1, 120, VERY_LONG)
    snaps = [IdBook(at, (a, b, cheap, dear)),
             IdBook(at + HOUR_MS, (a, cheap)),                                  # B gone, A unchanged; 1s 20c gone
             IdBook(at + 2 * HOUR_MS, (Auction(1, silk, 3, 315, VERY_LONG, True), cheap))]   # A loses 2
    lt = lifetimes(snaps)
    assert [(e.auction, e.expired, e.cancelled) for e in lt.endings] == [(2, 0.0, True), (4, 0.0, False)]
    assert lt.commodities == frozenset({silk})
    first, second = life_flows(lt)[(at, at + HOUR_MS)], life_flows(lt)[(at + HOUR_MS, at + 2 * HOUR_MS)]
    assert (first[silk].sold, first[silk].expired, first[silk].cancelled, first[silk].cancels) == (0.0, 0.0, 20.0, 1.0)
    assert first[silk].commodity and not first[gloves].commodity
    assert (first[gloves].sold, first[gloves].cancelled) == (1.0, 0.0)          # gear: left early, cancels included
    assert second[silk].sold == 2.0                                             # A's partial fill is a sale


def test_BZ40_an_equal_price_or_a_changed_cheaper_auction_is_no_proof():
    silk, at = 4306, THU_1702
    a, b = Auction(1, 4306, 5, 320, VERY_LONG, True), Auction(2, 4306, 20, 320, LONG, True)
    equal = lifetimes([IdBook(at, (a, b)), IdBook(at + HOUR_MS, (a,))])
    assert [e.cancelled for e in equal.endings] == [False]                      # same price: a buyer could take either
    cheaper = Auction(1, silk, 5, 315, VERY_LONG, True)
    bought = lifetimes([IdBook(at, (cheaper, b)), IdBook(at + HOUR_MS, (Auction(1, silk, 4, 315, VERY_LONG, True),))])
    assert [e.cancelled for e in bought.endings] == [False]                     # the cheaper one was bought from too


def test_BZ41_a_listing_posted_at_the_lowest_price_is_tier_one():
    at = THU_1702
    old = Auction(1, LINEN, 20, 125, VERY_LONG, True)
    snaps = [IdBook(at, (old,)),
             IdBook(at + HOUR_MS, (old, Auction(2, LINEN, 20, 120, VERY_LONG, True), Auction(3, LINEN, 5, 130, VERY_LONG, True))),
             IdBook(at + 2 * HOUR_MS, (old,))]
    lt = lifetimes(snaps)
    assert [(e.auction, e.tier1) for e in lt.endings] == [(2, True), (3, False)]
    flow = life_flows(lt)[(at + HOUR_MS, at + 2 * HOUR_MS)][LINEN]
    assert (flow.t1_ended, flow.t1_left, flow.ended) == (1.0, 1.0, 2.0)          # 3 was dearer: not tier 1
    assert lifetimes([IdBook(at, (old,)), IdBook(at + HOUR_MS, ())]).endings[0].tier1 is None   # posting unseen


def test_BZ40_a_snapshot_stored_before_m33b_still_finds_the_cancel():
    silk, at = 4306, THU_1702
    old = IdBook(at, (Auction(1, silk, 5, 315, VERY_LONG), Auction(2, silk, 20, 320, LONG)))   # no commodity flag
    new = IdBook(at + HOUR_MS, (Auction(1, silk, 5, 315, VERY_LONG, True), Auction(2, silk, 20, 320, LONG, True)))
    lt = lifetimes([old, new, IdBook(at + 2 * HOUR_MS, (Auction(1, silk, 5, 315, VERY_LONG, True),))])
    assert [e.cancelled for e in lt.endings] == [True]


def test_BZ40_an_item_whose_last_auction_vanishes_in_the_first_kept_gap_is_still_a_commodity():
    silk, at = 4306, THU_1702
    before = IdBook(at, (Auction(1, silk, 5, 315, VERY_LONG, True), Auction(2, silk, 20, 320, LONG, True)))
    lt = lifetimes([before, IdBook(at + HOUR_MS, ())], keep_from_ms=at + HOUR_MS)
    assert lt.commodities == frozenset({silk})
    flow = life_flows(lt)[(at, at + HOUR_MS)][silk]
    assert flow.commodity and flow.cancels == 0.0                               # nothing cheaper survived: both sold



SEED_VI8 = 8


def carry_world(rng) -> tuple[list[IdBook], int | None]:
    """Snapshots over up to three days (so the early-leave window drops gaps), with holes, missed refreshes and
    30-minute refreshes; commodities and gear (each key's flag fixed, D7), partial fills, cancels (a cheaper unchanged
    listing beside one that leaves), relists (a new id with a vanished one's key and units) and random bands (clashes);
    plus a random start of the kept span."""
    live: dict[int, list] = {}
    snaps, next_id, at = [], 1, THU_1702
    for _ in range(rng.randint(2, 70)):
        gone = []
        for i in sorted(live):
            r = rng.random()
            if r < 0.15:
                gone.append(live.pop(i))
            elif r < 0.35 and live[i][0] != 15210:
                live[i][1] = max(1, live[i][1] - rng.randint(1, 5))           # a partial fill
        for key, qty, _ in gone[:1] if rng.random() < 0.3 else []:
            live[next_id] = [key, qty, rng.choice([90, 100, 110])]           # a relist: same key and units
            next_id += 1
        for _ in range(rng.randint(0, 4)):
            live[next_id] = [rng.choice([LINEN, ORE, 15210]), rng.randint(1, 20), rng.choice([90, 100, 110])]
            next_id += 1
        snaps.append(IdBook(at, tuple(Auction(i, k, q, p, rng.randint(1, 4), k != 15210)
                                      for i, (k, q, p) in sorted(live.items()))))
        at += rng.choice([30 * MINUTE, HOUR_MS, HOUR_MS, HOUR_MS, 2 * HOUR_MS, 7 * HOUR_MS])
    keep_from = rng.choice([None, rng.choice(snaps).at_ms])
    return snaps, keep_from


def test_VI8_carried_lifetimes_are_the_batch_pass_one_snapshot_at_a_time():
    for n, (snaps, keep_from) in enumerate(cases(SEED_VI8, carry_world)):
        context = f"seed {SEED_VI8} case {n}"
        lt, ref = lifetimes(snaps, keep_from_ms=keep_from), batch.lifetimes(snaps, keep_from_ms=keep_from)
        assert (lt.gaps, lt.seen, lt.endings) == (ref.gaps, ref.seen, ref.endings), context    # expired shares exact
        assert (lt.fills, lt.listed, lt.first_seen) == (ref.fills, ref.listed, ref.first_seen), context
        assert (lt.clashes, lt.commodities) == (ref.clashes, ref.commodities), context
        flows = life_flows(lt)
        assert flows == batch.life_flows(ref), context                      # every float bit for bit, unseen included
        assert sold_by_hour(flows) == sold_by_hour(batch.life_flows(ref)), context
        assert (new_listing_positions(lt), sold_prices(lt)) == (new_listing_positions(ref), sold_prices(ref)), context


def test_VI8_a_state_carried_across_runs_gives_the_same_records():
    snaps, _ = carry_world(__import__("random").Random(SEED_VI8))
    whole = lifetimes(snaps).records
    state, records = LifeState(), []
    for book in snaps:                                       # one snapshot per run, the state handed over between them
        state, record = step(state, book)
        records += [record] if record else []
    assert tuple(records) == whole
    for at in (state.prev_at, state.prev_at - HOUR_MS):      # a repeat or a late snapshot is the caller's to replay
        with pytest.raises(ValueError):
            step(state, IdBook(at, ()))


def test_life_flows_needs_the_records_of_lifetimes():
    gap = (THU_1702, THU_1702 + HOUR_MS)
    with pytest.raises(ValueError):
        life_flows(Lifetimes((gap,), {gap: frozenset()}, (), (), (), 0))
