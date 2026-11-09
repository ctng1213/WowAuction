"""Decision quality: one world traded by AHSync's plan and by each benchmark, scored alike (SPEC §8.10; plan §1.6).

`benchmark` runs the same world four ways from the same seed: our characters following the plan (`Trader` with
AHSync's session), the naive trader, doing nothing, and the oracle (computed from the do-nothing run's truth, where
our characters never touch the market). Each run is its own market, so our trades move it and competitors react;
the do-nothing run is the market as it would have been. MS3 has one world, `STARTER` (stock to sell and a dump to
catch); MS4's SD rows add theirs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sim.market import DAY_MS, HOUR_MS, Item, Planted, World
from sim.inference import START_MS, commodity
from sim.runner import RunSettings, simulate
from sim.scorers import Score, oracle_profit, score
from sim.trader import OUR_PREFIX, CharacterSpec, Login, NaiveTrader, Trader
from sim.truth import Truth

GOLD = 10_000
LOGIN_HOURS = (10, 20)                 # the SD rows' characters log in mornings and evenings (UTC)
MATURE_DAYS = 28                       # past the 3-week trust gate (plan §1.37, §1.38), so the plan can buy
TRADER = (CharacterSpec("Amaya", 100 * GOLD),)
STARTER_CHARACTERS = (CharacterSpec("Amaya", 100 * GOLD, bags=((2589, 200), (4306, 60))),)


def starter(seed: int = 1, days: int = 6) -> World:
    """MS3's decision world: three real-shaped cloths; on day 3, 3,000 Silk dumped at half price just before the
    daily run.

    Args:
        seed: The world's seed.
        days: Its length.

    Returns:
        The World.
    """
    items = (commodity(2589, "Linen Cloth", 50, 400, 120, cancel_share=0.06),
             commodity(4306, "Silk Cloth", 150, 250, 80, cancel_share=0.06, max_units=8),
             commodity(2592, "Wool Cloth", 100, 150, 60, cancel_share=0.06, max_units=6))
    dump = (Planted(START_MS + 3 * DAY_MS + 8 * HOUR_MS + 50 * 60_000, 4306, 3000, 75, 24),)
    return World(seed, START_MS, days, items, planted=dump)


def benchmark(world: World, characters: tuple[CharacterSpec, ...], folder: Path) -> list[Score]:
    """Run a world with the plan, the naive trader and nothing, and score each against the oracle.

    Args:
        world: The scenario.
        characters: Our characters, the same for every benchmark.
        folder: An empty folder for the runs.

    Returns:
        Scores of "plan", "naive", "nothing" and "oracle", in that order.

    Raises:
        AssertionError: A ledger out of balance (SU12).
    """
    stock: dict[int, int] = {}
    for spec in characters:
        for key, units in spec.bags:
            stock[key] = stock.get(key, 0) + units
    plan = Trader(world, characters, folder / "ahsync")
    try:
        simulate(world, folder / "plan", agent=plan)
    finally:
        plan.close()
    naive = NaiveTrader(world, characters)
    simulate(world, folder / "naive", agent=naive)
    nothing = Trader(world, characters)
    quiet = simulate(world, folder / "nothing", agent=nothing)
    oracle = oracle_profit(quiet.truth.events, stock)
    scores = []
    for name, trader in (("plan", plan), ("naive", naive), ("nothing", nothing)):
        assert trader.ledger.balanced(), f"{name}'s ledger is out of balance (seed {world.seed})"
        scores.append(score(name, trader.ledger, trader.samples, oracle))
    return scores + [Score("oracle", oracle, 0.0, 0, 0, 0, 1.0 if oracle else None)]


@dataclass(frozen=True)
class Line:
    """One decision row's report line: what was measured against its target (plan §1.11; a mark, never a failure)."""

    row: str                           # the scenario ID
    what: str                          # the quantity, in words
    value: str                         # what happened
    target: str                        # the row's pass band
    met: bool | None                   # the report's mark; None: not exercised (the world never tested it)


def cloths() -> tuple[Item, ...]:
    """The SD worlds' steady background: three real-shaped cloths (as `starter`, without its dump).

    Returns:
        The items.
    """
    return starter(1).items


def plan_run(world: World, folder: Path, characters: tuple[CharacterSpec, ...] = TRADER,
             hourly: bool = False, login_hours: tuple[int, ...] = LOGIN_HOURS, first_login_ms: int | None = None
             ) -> Trader:
    """A world traded by the plan with the SD rows' logins; its database closed afterwards.

    Args:
        world: The scenario.
        folder: An empty folder.
        characters: Our characters.
        hourly: Run the cloud's hourly mode too, so a login plans with the newest hourly document (a row about
            reacting within hours needs it; it triples the run's time).
        login_hours: The UTC hours our characters log in.
        first_login_ms: No login before this time (Trader); None from the world's start.

    Returns:
        The trader, its session's logins and the run's truth kept.
    """
    trader = Trader(world, characters, folder / "ahsync", login_hours=login_hours, first_login_ms=first_login_ms)
    try:
        trader.truth = simulate(world, folder / "run", RunSettings(hourly=hourly), agent=trader).truth
    finally:
        trader.close()
    return trader


def funded(logins: list[Login], key: int, kind: str = "buy") -> list[Login]:
    """The logins whose plan held an action of a kind for an item.

    Args:
        logins: The session's logins.
        key: Item key.
        kind: The action kind.

    Returns:
        Those logins, in order.
    """
    return [l for l in logins if any(a["kind"] == kind and a["key"] == key for a in l.actions)]


NEW_ITEM = 7_777                       # SD31's brand-new item


def sd31(folder: Path, seed: int = 1, days: int = MATURE_DAYS) -> list[Line]:
    """SD31: a brand-new item with a 300% apparent margin and no sales, on a mature market.

    Five auctions at 1 g appear the evening before the last two days, then one at 25 s: the next price level is four
    times the cheapest; nobody buys it.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length (the item appears 2 days before its end).

    Returns:
        The plan's buys of it and the market file's flip offers for it, over every login after it appeared.
    """
    new = Item(NEW_ITEM, "Fresh Thing", True, GOLD, 0, 0, vendor_sell=100, stack=1)
    first = START_MS + (days - 2) * DAY_MS - 4 * HOUR_MS
    planted = tuple(Planted(first + n * 60_000, NEW_ITEM, 1, GOLD, 24) for n in range(5)) + (
        Planted(first + 12 * HOUR_MS, NEW_ITEM, 1, GOLD // 4, 24),)
    trader = plan_run(World(seed, START_MS, days, cloths() + (new,), planted=planted), folder)
    after = [l for l in trader.session.logins if l.at_ms > first]
    buys, offers = funded(after, NEW_ITEM), [l for l in after if NEW_ITEM in l.offers]
    return [Line("SD31", f"logins planning a buy of it, of {len(after)}", str(len(buys)), "0", not buys),
            Line("SD31", f"logins whose market file offers its flip, of {len(after)}", str(len(offers)), "0",
                 not offers)]



SILK = 4306
DIP_DAY = 22                            # SD28/SD29: the held Silk's price moves at noon of this day


def held_silk(seed: int, days: int, dip_day: int, shifts: tuple = (), extra: tuple[Planted, ...] = ()) -> World:
    """SD28/SD29's market: `starter`'s dump the day before the dip, which the plan buys on a mature market, then a
    price move.

    Args:
        seed: The world's seed.
        days: Its length.
        dip_day: The day of the move; the dump comes at 07:00 the day before.
        shifts: Silk's price and demand shifts (Item.shifts).
        extra: More planted auctions.

    Returns:
        The World.
    """
    items = tuple(replace(i, shifts=shifts) if i.key == SILK else i for i in cloths())
    dump = tuple(Planted(START_MS + (dip_day - 1) * DAY_MS + 7 * HOUR_MS + n * 1000, SILK, 20, 75, 24)
                 for n in range(150))
    return World(seed, START_MS, days, items, planted=dump + extra)


def silk_posts(login: Login) -> list[tuple[int, int, bool]]:
    """The held Silk's planned posts at a login.

    Args:
        login: The login.

    Returns:
        (units, unit price, due now) per post action.
    """
    return [(a["qty"], a["price"], (a["window_start_ms"] or a["slot_ms"]) <= login.at_ms + HOUR_MS)
            for a in login.actions if a["key"] == SILK and a["kind"] == "post"]


def silk_actions(login: Login) -> list[tuple[str, int, int, bool]]:
    """Silk's planned buys and posts at a login, sorted.

    Args:
        login: The login.

    Returns:
        (kind, units, unit price, due now) per action.
    """
    return sorted((a["kind"], a["qty"], a["price"], (a["window_start_ms"] or a["slot_ms"]) <= login.at_ms + HOUR_MS)
                  for a in login.actions if a["key"] == SILK and a["kind"] in ("buy", "post"))


def sd28(folder: Path, seed: int = 1, days: int = DIP_DAY + 3, dip_day: int = DIP_DAY, hourly: bool = True
         ) -> list[Line]:
    """SD28: a held flip's price drops 20% at noon and stays down; the plan against holding to its target.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.
        dip_day: The day of the drop (before day 21 the plan can't buy the dump: the trust gate).
        hourly: Plan with the hourly documents (the reaction within hours needs them).

    Returns:
        How soon a login sells now or cuts the price (≤ 12 h), and the run's drawdown against a trader that keeps
        posting at the pre-drop target (at most holding's).
    """
    drop = START_MS + dip_day * DAY_MS + 12 * HOUR_MS
    world = held_silk(seed, days, dip_day, ((drop, 0.8, 1.0),))
    plan = plan_run(world, folder / "plan", hourly=hourly)
    before = [l for l in plan.session.logins if l.at_ms < drop and silk_posts(l)]
    target = max((price for _, price, _ in silk_posts(before[-1])), default=0) if before else 0
    reacted = next((l for l in plan.session.logins if l.at_ms > drop and any(
        price < target or now for _, price, now in silk_posts(l))), None)
    hours = None if reacted is None else (reacted.at_ms - drop) / HOUR_MS
    holder = Trader(world, TRADER, folder / "hold" / "ahsync", login_hours=LOGIN_HOURS, held_prices={SILK: target})
    try:
        simulate(world, folder / "hold" / "run", RunSettings(hourly=hourly), agent=holder)
    finally:
        holder.close()
    mine, held = score("plan", plan.ledger, plan.samples, None), score("hold", holder.ledger, holder.samples, None)
    return [Line("SD28", f"hours from the drop to a login that sells now or under {target} c",
                 "never" if hours is None else f"{hours:.0f}", "≤ 12", hours is not None and hours <= 12),
            Line("SD28", "drawdown (g): the plan, holding to the target",
                 f"{mine.drawdown / GOLD:.2f}, {held.drawdown / GOLD:.2f}", "plan ≤ hold", mine.drawdown <= held.drawdown),
            Line("SD28", "made (g), stranded units: the plan; holding to the target",
                 f"{mine.made / GOLD:.2f}, {mine.stranded_units}; {held.made / GOLD:.2f}, {held.stranded_units}",
                 "reported", True)]


BLIP_PRICE = 131                        # SD29: 80% of Silk's 164 c


def sd29(folder: Path, seed: int = 1, days: int = DIP_DAY + 2, dip_day: int = DIP_DAY, hourly: bool = True
         ) -> list[Line]:
    """SD29: SD28's dip seen by one snapshot only: 30 auctions of 20 at 80% posted at noon, cancelled at 12:50.

    A login at 13:00 plans with the document of the snapshot that saw it (FM5 is what stands between them). Other
    sellers copy the cheapest price they see, so the dip leaves real cheaper auctions behind (plan §1.40): the
    evening login's undercut of them is reported, the confirmed-floor milestone's evidence, not marked.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.
        dip_day: The day of the dip.
        hourly: Plan with the hourly documents (the 13:00 login sees the dip only in an hourly one).

    Returns:
        The 13:00 login's Silk buys and posts with the dip against the same login without it (unchanged is met;
        the dip moves the plan through its price, or through the 600 units it adds to supply), and the held Silk's
        posts at the evening login with the dip and without it.
    """
    dip = START_MS + dip_day * DAY_MS + 12 * HOUR_MS
    blip = tuple(Planted(dip + n * 1000, SILK, 20, BLIP_PRICE, 24, dip + 50 * 60_000) for n in range(30))
    hours = (10, 13, 20)
    plain = plan_run(held_silk(seed, days, dip_day), folder / "plain", hourly=hourly, login_hours=hours)
    dipped = plan_run(held_silk(seed, days, dip_day, extra=blip), folder / "dip", hourly=hourly, login_hours=hours)
    seen = [next((l for l in t.session.logins if l.at_ms > dip), None) for t in (plain, dipped)]
    calm, moved = ([] if l is None else silk_actions(l) for l in seen)
    evening = dip + 8 * HOUR_MS
    first = [next((l for l in t.session.logins if l.at_ms >= evening), None) for t in (plain, dipped)]
    without, with_dip = (sorted(silk_posts(l)) if l is not None else [] for l in first)
    return [Line("SD29", "the 13:00 login's Silk buys and posts (units, c, now): with the dip; without",
                 f"{moved}; {calm}", "unchanged", None not in seen and moved == calm),
            Line("SD29", "the held Silk's posts at the evening login: with the dip; without (others' copies of it)",
                 f"{with_dip}; {without}", "reported", True)]


DROP_DAY = 28                           # SD5/SD7: demand moves at noon of this day
INSTALL_DAY = 19                        # SD5/SD7: AHSync installed on a market the cloud has watched since day 0
DEMAND_ITEMS = ((SILK, "Silk Cloth", 150, 60, 150, 4, 200, 65),          # key, name, fair c, buyers/day, posts/day,
                (4338, "Mageweave Cloth", 400, 9, 12, 6, 12, 240),       # most units a buyer takes, units dumped,
                (14047, "Runecloth", 800, 4, 6, 5, 5, 480))              # dump price c (below each real floor)
REACTION_HOURS = ((100, 12), (25, 36), (0, 96))   # plan §1.11: an item selling at least N a day stops buying in H h


def demand_world(seed: int, days: int, shifts: tuple = ()) -> World:
    """SD5/SD7's market: three cloths whose buyers, not sellers, set the sales (about 150, 20 and 6 a day), and a
    cheap dump of each every second morning from day 20 for the plan to flip.

    Args:
        seed: The world's seed.
        days: Its length.
        shifts: The cloths' price and demand shifts (Item.shifts).

    Returns:
        The World.
    """
    items = tuple(commodity(key, name, fair, buys, posts, cancel_share=0.06, max_units=most, shifts=shifts)
                  for key, name, fair, buys, posts, most, _, _ in DEMAND_ITEMS)
    dumps = tuple(Planted(START_MS + day * DAY_MS + 8 * HOUR_MS + n, key, units, price, 8)
                  for day in range(20, days, 2) for n, (key, *_, units, price) in enumerate(DEMAND_ITEMS))
    return World(seed, START_MS, days, items, planted=dumps)


def our_buys(truth: Truth, key: int) -> list:
    """Our characters' buys of an item, oldest first.

    Args:
        truth: The truth log.
        key: Item key.

    Returns:
        Buy events whose buyer is ours.
    """
    return [e for e in truth.events if e.key == key and e.kind == "buy" and e.actor.startswith(OUR_PREFIX)]


def others_sold_per_day(truth: Truth, key: int, from_ms: int, to_ms: int) -> float:
    """Units others bought a day between two times (the market's real sales, ours excluded).

    Args:
        truth: The truth log.
        key: Item key.
        from_ms: Start.
        to_ms: End.

    Returns:
        Units a day.
    """
    units = sum(e.units for e in truth.between(key, from_ms, to_ms) if e.kind == "buy" and not e.actor.startswith(OUR_PREFIX))
    return units / ((to_ms - from_ms) / DAY_MS)


def sd5(folder: Path, seed: int = 1, days: int = DROP_DAY + 5, drop_day: int = DROP_DAY, hourly: bool = True
        ) -> list[Line]:
    """SD5: demand falls 60% at noon and stays down; how soon the plan stops buying each cloth.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.
        drop_day: The day of the drop.
        hourly: Plan with the hourly documents.

    Returns:
        Per cloth: its sales a day before and after; the hours from the drop to its last buy against the target
        for its rate (plan §1.11; "not exercised" without a buy before the drop); the units bought after the
        drop against one day of the new rate.
    """
    drop = START_MS + drop_day * DAY_MS + 12 * HOUR_MS
    end = START_MS + days * DAY_MS
    plan = plan_run(demand_world(seed, days, ((drop, 1.0, 0.4),)), folder, hourly=hourly,
                    first_login_ms=START_MS + INSTALL_DAY * DAY_MS)
    lines = []
    for key, name, *_ in DEMAND_ITEMS:
        before, after = others_sold_per_day(plan.truth, key, drop - 7 * DAY_MS, drop), \
            others_sold_per_day(plan.truth, key, drop, end)
        target = next(hours for rate, hours in REACTION_HOURS if before >= rate)
        buys = our_buys(plan.truth, key)
        late = [e for e in buys if e.at_ms >= drop]
        hours = max(0.0, (late[-1].at_ms - drop) / HOUR_MS) if late else 0.0
        exercised = any(e.at_ms < drop for e in buys)
        lines.append(Line("SD5", f"{name}: sold/day before; after the drop", f"{before:.1f}; {after:.1f}", "reported", True))
        lines.append(Line("SD5", f"{name}: hours from the drop to its last buy", f"{hours:.0f}" if exercised else
                          "no buy before the drop", f"≤ {target}", hours <= target if exercised else None))
        bought = sum(e.units for e in late)
        lines.append(Line("SD5", f"{name}: units bought after the drop; one day of the new rate", f"{bought}; {after:.0f}",
                          "bought ≤ a day", bought <= after))
    return lines


def our_posts(truth: Truth, key: int, from_ms: int, to_ms: int) -> list:
    """Our characters' posts of an item between two times.

    Args:
        truth: The truth log.
        key: Item key.
        from_ms: Start.
        to_ms: End.

    Returns:
        Post events whose seller is ours.
    """
    return [e for e in truth.between(key, from_ms, to_ms) if e.kind == "post" and e.actor.startswith(OUR_PREFIX)]


def sd7(folder: Path, seed: int = 1, days: int = DROP_DAY + 4, quiet_day: int = DROP_DAY, hourly: bool = True
        ) -> list[Line]:
    """SD7: one quiet day at half the buyers, then normal, against the same world without it; the plan should
    neither sell below its plan nor stop buying.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.
        quiet_day: The quiet day (midnight to midnight).
        hourly: Plan with the hourly documents.

    Returns:
        Per cloth, over the quiet day and the next, with it and without it: our lowest post price (with ≥ without
        is met; "not exercised" when either posted none), and the units we bought (with ≥ 75% of without).
    """
    quiet = START_MS + quiet_day * DAY_MS
    install = START_MS + INSTALL_DAY * DAY_MS
    calm = plan_run(demand_world(seed, days), folder / "calm", hourly=hourly, first_login_ms=install)
    dip = plan_run(demand_world(seed, days, ((quiet, 1.0, 0.5), (quiet + DAY_MS, 1.0, 1.0))), folder / "quiet",
                   hourly=hourly, first_login_ms=install)
    window = (quiet, quiet + 2 * DAY_MS)
    lines = []
    for key, name, *_ in DEMAND_ITEMS:
        low = [min((e.price for e in our_posts(t.truth, key, *window)), default=None) for t in (dip, calm)]
        lines.append(Line("SD7", f"{name}: our lowest post price (c) over the quiet day and the next: with it; without",
                          f"{low[0]}; {low[1]}", "with ≥ without", None if None in low else low[0] >= low[1]))
        bought = [sum(e.units for e in our_buys(t.truth, key) if window[0] <= e.at_ms < window[1]) for t in (dip, calm)]
        lines.append(Line("SD7", f"{name}: units we bought over the quiet day and the next: with it; without",
                          f"{bought[0]}; {bought[1]}", "with ≥ 75% of without",
                          None if not bought[1] else bought[0] >= 0.75 * bought[1]))
    return lines


DRIFT_START, DRIFT_DAYS = 20, 30          # SD32/SD33: prices drift daily from day 20 for 30 days


def drift_world(seed: int, rate: float, days: int = DRIFT_START + DRIFT_DAYS) -> World:
    """SD32/SD33's market: the three cloths steady for 20 days, then every price moving by `rate` a day (demand
    steady), compounded at each midnight.

    Args:
        seed: The world's seed.
        rate: The daily price change, e.g. −0.02.
        days: The world's length.

    Returns:
        The World.
    """
    shifts = tuple((START_MS + (DRIFT_START + d) * DAY_MS, (1 + rate) ** (d + 1), 1.0)
                   for d in range(max(0, days - DRIFT_START)))
    return World(seed, START_MS, days, tuple(replace(i, shifts=shifts) for i in cloths()))


def drift_lines(row: str, folder: Path, seed: int, rate: float, days: int, hourly: bool) -> list[Line]:
    """A drift row's run and lines: what the plan made against the oracle, and what it was left holding.

    Args:
        row: "SD32" or "SD33".
        folder: An empty folder.
        seed: The world's seed.
        rate: The daily price change.
        days: The world's length.
        hourly: Plan with the hourly documents.

    Returns:
        The plan's result and stranded stock, made ≥ 0 the target (SD32 misses it by design: the clamps sit above
        tomorrow's price); its share of the oracle, reported (SD33: the money left on the table).
    """
    plan = plan_run(drift_world(seed, rate, days), folder, hourly=hourly,
                    first_login_ms=START_MS + INSTALL_DAY * DAY_MS)
    oracle = oracle_profit(plan.truth.events, {}, frozenset(OUR_PREFIX + c for c in plan.specs))
    mine = score("plan", plan.ledger, plan.samples, oracle)
    bought = sum(e.units for key in {i.key for i in cloths()} for e in our_buys(plan.truth, key))
    share = "—" if mine.oracle_share is None else f"{mine.oracle_share:.0%}"
    return [Line(row, "made (g); units bought; stranded units at the end",
                 f"{mine.made / GOLD:.2f}; {bought}; {mine.stranded_units}", "made ≥ 0",
                 mine.made >= 0 if bought else None),                  # nothing bought: never exercised
            Line(row, "the oracle's profit (g); the plan's share of it", f"{oracle / GOLD:.2f}; {share}", "reported", True)]


def sd32(folder: Path, seed: int = 1, days: int = DRIFT_START + DRIFT_DAYS, hourly: bool = True) -> list[Line]:
    """SD32: every price drifts −2% a day for 30 days, demand steady: what buying into a falling market costs.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.
        hourly: Plan with the hourly documents.

    Returns:
        drift_lines at −2% a day.
    """
    return drift_lines("SD32", folder, seed, -0.02, days, hourly)


def sd33(folder: Path, seed: int = 1, days: int = DRIFT_START + DRIFT_DAYS, hourly: bool = True) -> list[Line]:
    """SD33: every price drifts +2% a day for 30 days: what the plan leaves on the table against the oracle.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.
        hourly: Plan with the hourly documents.

    Returns:
        drift_lines at +2% a day.
    """
    return drift_lines("SD33", folder, seed, 0.02, days, hourly)


MAGEWEAVE = 4338                        # SM17/SD15's thin item, of which we hold a lot


def over_liquidity(truth: Truth, key: int, snapshots: list[int], ours: str, share: float) -> tuple[int, int]:
    """How often our listed units broke the liquidity rule at a snapshot (SPEC §3.4: max(1 stack, share × others')).

    Args:
        truth: The truth log.
        key: Item key.
        snapshots: Snapshot times, ascending.
        ours: Our sellers' name prefix.
        share: max_supply_share.

    Returns:
        (snapshots where our units exceeded max(our largest auction, floor(share × others' units)), snapshots with
        a unit of ours listed).
    """
    live: dict[int, tuple[bool, int]] = {}
    events = [e for e in truth.events if e.key == key]
    over = listed = n = 0
    for at in snapshots:
        while n < len(events) and events[n].at_ms <= at:
            e = events[n]
            if e.kind == "post":
                live[e.auction] = (e.actor.startswith(ours), e.left)
            elif e.left == 0:
                live.pop(e.auction, None)
            elif e.auction in live:
                live[e.auction] = (live[e.auction][0], e.left)
            n += 1
        mine = [units for is_ours, units in live.values() if is_ours]
        others = sum(units for is_ours, units in live.values() if not is_ours)
        if mine:
            listed += 1
            over += sum(mine) > max(max(mine), int(share * others))
    return over, listed


def sd15(folder: Path, seed: int = 1, days: int = 14) -> list[Line]:
    """SM17 and SD15: we hold 300 Mageweave in a market that buys about 10 a day from 2 other posts a day.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.

    Returns:
        SM17: the engine's sold/day against the truth's, and our part of it (fails by design: the API names no
        seller). SD15: the snapshots where our listings broke the liquidity rule (none); our pace, so that holding
        everything can't pass: our sales the last 7 days at least the share of listings the rule allows us,
        share ÷ (1 + share) (a third at 0.5); and our sale price against the market's.
    """
    mage = commodity(MAGEWEAVE, "Mageweave Cloth", 2000, 60, 2, cancel_share=0.06, max_units=1, stacks=((5, 1.0),),
                     buyer_level=1.3)
    world = World(seed, START_MS, days, cloths() + (mage,))
    trader = Trader(world, (CharacterSpec("Amaya", 100 * GOLD, bags=((MAGEWEAVE, 300),)),), folder / "ahsync",
                    login_hours=LOGIN_HOURS)
    try:
        run = simulate(world, folder / "run", RunSettings(hourly=True), agent=trader)   # production's cadence
    finally:
        trader.close()
    now, i = run.snapshots[-1], run.documents[-1].items[MAGEWEAVE]
    seller = {e.auction: e.actor for e in run.truth.events if e.key == MAGEWEAVE and e.kind == "post"}
    week = [e for e in run.truth.between(MAGEWEAVE, now - 7 * DAY_MS, now) if e.kind == "buy"]
    ours = [e for e in week if seller[e.auction].startswith(OUR_PREFIX)]
    sold, mine = sum(e.units for e in week) / 7, sum(e.units for e in ours) / 7
    others = [e for e in week if not seller[e.auction].startswith(OUR_PREFIX)]
    limit = trader.session.cfg.max_supply_share
    over, listed = over_liquidity(run.truth, MAGEWEAVE, run.snapshots, OUR_PREFIX, limit)
    allowed = limit / (1 + limit)                     # ours ≤ share × others' is this share of all listings
    return [Line("SM17", "sold/day: the engine; the truth, of it ours", f"{i.sold_per_day or 0:.2f}; {sold:.2f}, {mine:.2f}",
                 "reported (no seller in the API)", True),
            Line("SD15", f"snapshots where our listed units broke max(1 stack, {limit} × others'), of those listing ours",
                 f"{over} of {listed}", "0", over == 0),
            Line("SD15", "our share of the units sold, the last 7 days", f"{mine / sold:.0%}" if sold else "—",
                 f"≥ {allowed:.0%}", bool(sold) and mine / sold >= allowed),
            Line("SD15", "mean sale price (c): ours; others'; units ours held at the end",
                 f"{_mean_price(ours)}; {_mean_price(others)}; {trader.held('Amaya-Simulated', MAGEWEAVE)}",
                 "reported", True)]


def _mean_price(buys: list) -> str:
    """The units-weighted mean price of some buys, for a report line.

    Args:
        buys: Buy events.

    Returns:
        Copper, rounded half up, or "—" without a unit.
    """
    units = sum(e.units for e in buys)
    return "—" if not units else str(int(sum(e.units * e.price for e in buys) / units + 0.5))


SD_ROWS = {"SD31": sd31, "SD28": sd28, "SD29": sd29, "SD15": sd15, "SD5": sd5, "SD7": sd7, "SD32": sd32, "SD33": sd33}     # the report's rows (SD15 also measures SM17)

