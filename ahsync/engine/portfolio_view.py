"""The Portfolio page's payload: every number and text the dashboard's first page shows (SPEC §3.5, §3.4D, §8.5).

It reads one plan run (engine.allocation.PortfolioPlan: the funded batches, the alternatives with their reasons,
the accounting and every batch as selection saw it) and the positions, and lays them out as the mockup does:
summary tiles, A (positions), B (Today), C (Next 7 days), the funding card and the empty state. Nothing is
selected again here: a row is an opportunity, funded with the batches selection funded, or left out with its
first batch's reason (§3.4D.6). Money is rounded half up only when formatted (§3.3). Pure: data in, text out.
"""
from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ahsync.engine.allocation import PortfolioPlan
from ahsync.engine.position_views import PositionView
from ahsync.engine.candidates import PATTERN_WEEKS
from ahsync.engine.plan_chains import clock, short_name
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.portfolio import (Accounting, Candidate, CharacterGold, PortfolioSettings, accounting, cash_of,
                                     committed_share_pct, input_sources, pool_profit_pct, profit_pct)
from ahsync.engine.selection import (MIN_WINDOW_CLOSES, REASON_CODES, Eligibility, Pick, empty_state, per_day,
                                     profession_detail, reason_code)
from ahsync.engine.timing import premium_micro, wait_hundredths
from ahsync.engine.views import (COLUMN_HELP, HIGH_RISK_PCT, market_activity, money, risk, round_half_up,
                                 signed_money)

RISK_OPTIONS = (10, 20, 30, 40, 50)          # the Risk tolerance dropdown, percent (SPEC §3.5)
MIN_CHANCE_STEPS = (0, 25, 50, 75)           # section B's "Min chance" dropdown, percent: one staged view each
# A position's state -> its badge class (SPEC §3.4D.9).
STAGE_CLASS = {"Acquiring": "acq", "Processing": "proc", "Holding": "hold", "Ready to sell": "ready", "Listed": "listed",
               "Partially sold": "partial", "Completed": "done", "Needs reconciliation": "recon"}
# A strategy -> the Action column's badge (section B).
ACTION_OF = {"flip": "buy", "buy_later": "buy", "craft": "craft", "multistage": "craft", "disenchant": "de",
             "sell_stock": "post"}
PROCESS_OF = {"craft": "craft", "multistage": "craft chain", "disenchant": "disenchant"}
BUY_STRATEGIES = ("flip", "buy_later", "disenchant")       # their Each is the buy price; a craft's is its mat gold
SOFT_CODES = ("claimed", "gold_all", "gold_char", "limit", "risk")   # left out for gold, capacity or a list limit
NEG_CODES = ("profession", "missing")                # a status shown red; the rest amber
CODE_ORDER = {code: i for i, (_, code) in enumerate(REASON_CODES)}
# The detail line under a reason that needs no numbers (SPEC §3.4D.6).
DETAILS = {
    "stale": "prices older than 24 h are not planned on; run AHSync to fetch newer ones",
    "volume": "neither the chance to sell nor the liquidity can be measured without a sales rate",
    "no_listing": "nothing else is listed, and too few closes ground its price",
    "missing": "no stock, no AH listing and no vendor sells it",
    "claimed": "a better funded row uses it first",
    "gold_all": "all your characters together hold less",
    "gold_char": "your account could pay, but gold stays on its character",
    "limit": "the list is full of better rows",
}
# The empty state's second line, by a word of its title (SPEC §3.4D.7), in D.7's order.
EMPTY_TEXTS = (
    ("no gold in hand", "No character had gold at the last snapshot."),
    ("No characters yet", "Log in once with the addon so AHSync knows your gold and professions."),
    ("risk budget exhausted", "New suggestions come back as positions sell and free capital."),
    ("recommendation limit", "Both lists already hold their limit of funded rows."),
    ("market data too old", "The newest prices are more than 24 h old; run AHSync."),
    ("nothing pays", "No opportunity nets above 0 after fees, deposits and the chance it sells."),
    ("every candidate", "Each one has a reason: none fits your gold, professions or the market right now."),
)


@dataclass(frozen=True)
class PortfolioInputs:
    """What the Portfolio page is built from (SPEC §3.5)."""

    plan: PortfolioPlan | None                 # the run's plan; None without characters
    chars: tuple[CharacterGold, ...]           # the market's characters with their gold
    positions: tuple[PositionView, ...]        # section A's rows (recommend.positions_pass.views, M14)
    action_ids: tuple[str, ...]                # the plan's action ids, aligned with plan.actions (Skip)
    looks: Mapping[int, ItemLook]              # item key -> name, quality and market activity
    ps: PortfolioSettings                      # the settings the plan ran with
    now_ms: int                                # the run's time, UTC ms
    offset_ms: int = 0                         # local time minus UTC, ms
    prices_stale: bool = False                 # the newest prices are more than 24 h old (D.7)
    high_risk_pct: float = HIGH_RISK_PCT       # config high_risk_pct


@dataclass(frozen=True)
class OppRow:
    """One row of section B or C: an opportunity's funded batches, or its first batch when none is funded."""

    opp: str
    name: str                                  # its label, e.g. "Copper Rod"
    lst: str                                   # "today" | "week"
    cands: tuple[Candidate, ...]               # its funded batches in batch order, or its first batch alone
    adjusted: float                            # Σ adjusted profit, float copper
    reason: str | None = None                  # None when funded, else the §3.4D.6 status
    eligibility: Eligibility | None = None     # what its static checks read (excluded rows)
    action_id: str = ""                        # its first plan action's id, for Skip ("" when excluded)
    post_price: int | None = None              # a funded sell-stock row's post price, copper each

    @property
    def first(self) -> Candidate:
        """Its first batch.

        Returns:
            The candidate.
        """
        return self.cands[0]

    @property
    def qty(self) -> int:
        """Units (or crafts) in the row.

        Returns:
            Σ output units.
        """
        return sum(c.out_qty for c in self.cands)

    @property
    def gold(self) -> int:
        """Gold needed.

        Returns:
            Σ copper.
        """
        return sum(c.gold_needed for c in self.cands)

    @property
    def net(self) -> float:
        """Expected net.

        Returns:
            Σ float copper.
        """
        return sum(c.expected_net for c in self.cands)

    @property
    def cash(self) -> int:
        """Cash for profit % (SPEC §3.4D.8).

        Returns:
            Σ gold needed + cost basis of bought stock used, copper.
        """
        return sum(cash_of(c) for c in self.cands)


@dataclass(frozen=True)
class _Ctx:
    """One build's shared facts."""

    acct: Accounting
    left: int                                  # capacity the funded rows leave, copper
    inp: PortfolioInputs
    capacity_reached: bool = False             # some row was left out for the risk tolerance

    def look(self, key: int, name: str) -> ItemLook:
        """An item's look, its name when unknown.

        Args:
            key: Item key.
            name: The row's label, used when the item has no look.

        Returns:
            The ItemLook.
        """
        return self.inp.looks.get(key) or ItemLook(name, 1)


# ---------------------------------------------------------------- small formatters

def pct_text(pct: int | None) -> str:
    """A whole percent, or "—".

    Args:
        pct: Percent, or None.

    Returns:
        E.g. "124%"; "—" for None.
    """
    return "—" if pct is None else f"{pct}%"


def wait_text(hundredths: int) -> str:
    """A wait in days (SPEC §3.4D.2).

    Args:
        hundredths: Whole hundredths of a day.

    Returns:
        E.g. "3.35 d".
    """
    return f"{hundredths // 100}.{hundredths % 100:02d} d"


def premium_text(micro: int) -> str:
    """A premium as a percent to two decimals, half up (SPEC §3.4D.2).

    Args:
        micro: Premium in millionths.

    Returns:
        E.g. "31.75%".
    """
    bp = (micro + 50) // 100
    return f"{bp // 100}.{bp % 100:02d}%"


def when_text(ms: int | None, now_ms: int, offset_ms: int) -> str:
    """When something happens, as the page's day columns say it.

    Args:
        ms: UTC ms, or None.
        now_ms: Now.
        offset_ms: Local time minus UTC.

    Returns:
        "—" for None, "now" at or before now, "today 18:00" later today (local), else "Sat 18:00".
    """
    if ms is None:
        return "—"
    if ms <= now_ms:
        return "now"
    day, hour = clock(ms, offset_ms)
    same_day = (ms + offset_ms) // 86_400_000 == (now_ms + offset_ms) // 86_400_000
    return f"today {hour}" if same_day else f"{day} {hour}"


def sale_text(sale_ms: int | None, now_ms: int, offset_ms: int) -> str:
    """When a row's profit is realized: its expected sale hour (SPEC §3.4D.2).

    Args:
        sale_ms: The expected sale hour, UTC ms; None for selling now.
        now_ms: Now.
        offset_ms: Local time minus UTC.

    Returns:
        "now" for None, else when_text's.
    """
    return "now" if sale_ms is None else when_text(sale_ms, now_ms, offset_ms)


def shown_sum(values: Iterable[float]) -> int:
    """A total as the page shows it: the sum of its rows' displayed (half-up) values, so it always reconciles.

    Args:
        values: Float copper per row.

    Returns:
        Σ floor(x + 0.5), copper.
    """
    return sum(round_half_up(v) for v in values)


def weekday_name(ms: int, offset_ms: int) -> str:
    """The local weekday's full name.

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC.

    Returns:
        E.g. "Sunday".
    """
    return time.strftime("%A", time.gmtime((ms + offset_ms) // 1000))


# ---------------------------------------------------------------- rows

def opportunity_rows(plan: PortfolioPlan, action_ids: Sequence[str]) -> tuple[list[OppRow], list[OppRow]]:
    """Sections B and C's rows: one per opportunity (SPEC §3.4D.4: a row takes a slot with its first funded batch).

    Args:
        plan: The run's plan.
        action_ids: Its actions' ids, aligned with plan.actions.

    Returns:
        (funded rows, excluded rows), by opportunity id; an excluded row is an opportunity with no funded batch,
        shown with its first batch and that batch's reason.
    """
    funded_ids = set(plan.selection.funded)
    cands = {c.cand_id: c for c in plan.funded} | {a.candidate.cand_id: a.candidate for a in plan.alternatives}
    alts = {a.candidate.cand_id: a for a in plan.alternatives}
    adjusted = dict(plan.funded_adjusted) | {i: a.adjusted for i, a in alts.items()}
    spans = {opp: (first, end) for opp, first, end in plan.opp_actions}
    by_opp: dict[str, list[Pick]] = {}
    for p in plan.picks:
        by_opp.setdefault(p.opp, []).append(p)
    funded, excluded = [], []
    for opp in sorted(by_opp):
        picks = sorted(by_opp[opp], key=lambda p: p.batch)
        mine = [p for p in picks if p.cand_id in funded_ids]
        if mine:
            first, end = spans.get(opp, (0, 0))
            posts = [a.price for a in plan.actions[first:end] if a.kind == "post"]
            funded.append(OppRow(opp, mine[0].name, mine[0].lst, tuple(cands[p.cand_id] for p in mine),
                                 sum(adjusted[p.cand_id] for p in mine), None, None,
                                 action_ids[first] if end > first else "", posts[0] if posts else None))
        else:
            alt = alts[picks[0].cand_id]
            excluded.append(OppRow(opp, picks[0].name, picks[0].lst, (alt.candidate,), alt.adjusted, alt.reason,
                                   alt.eligibility))
    return funded, excluded


def excluded_order(r: OppRow) -> tuple:
    """Where an excluded row stands under the red line.

    Rows left out only for gold, capacity or a list limit come first (they would be funded with more room), then
    the rest in §3.4D.6's order; within a reason, the best adjusted profit first.

    Args:
        r: The row.

    Returns:
        A sort key ending in the opportunity id.
    """
    code = reason_code(r.reason)
    return (0 if code in SOFT_CODES else 1, CODE_ORDER[code], -r.adjusted, r.name, r.opp)


def each_copper(r: OppRow) -> int | None:
    """The Each column: what one unit costs you, or a post's price.

    Args:
        r: The row.

    Returns:
        A buy's price each, a craft's mat gold per craft (half up), a sell-stock post's price; None when nothing is
        paid (owned mats) or the post price isn't known.
    """
    if r.first.strategy == "sell_stock":
        return r.post_price
    paid = sum(c.gold_buys if c.strategy in BUY_STRATEGIES else c.gold_mats for c in r.cands)
    return round_half_up(paid / r.qty) if paid and r.qty else None


def materials_text(c: Candidate, ctx: _Ctx) -> list[str]:
    """A craft's mats, one entry each: "4 Silk Cloth (own)".

    Args:
        c: The candidate.
        ctx: The build.

    Returns:
        The entries for a craft or multistage craft; [] for any other strategy.
    """
    if c.strategy not in ("craft", "multistage"):
        return []
    return [f"{units} {ctx.look(key, str(key)).name} ({source})" for key, units, source in input_sources(c)]


def reason_detail(r: OppRow, ctx: _Ctx) -> str:
    """The detail line under an excluded row's status (SPEC §3.4D.6).

    Args:
        r: The excluded row.
        ctx: The build.

    Returns:
        The line, e.g. "Amaya has Enchanting 142" or "the market sells about 3 a day; 40 would take about 13 days".
    """
    code, e = reason_code(r.reason), r.eligibility
    if code == "profession":
        return profession_detail(e) if e is not None else ""
    if code == "uncertain":
        return uncertain_detail(e, r.first.sale_ms, ctx.inp.offset_ms)
    if code == "liquidity":
        if e is not None and e.sold_per_day:
            days = round_half_up(e.planned_units / e.sold_per_day)
            return f"the market sells about {per_day(e.sold_per_day)} a day; {e.planned_units} would take about {days} days"
        return "its windows' liquidity caps have no room left"
    if code == "risk":
        if r.gold > ctx.acct.remaining_capacity:
            return f"it alone needs more than the whole remaining capacity ({money(ctx.acct.remaining_capacity)})"
        return f"its turn came after every funded row, so {money(ctx.left)} is also what stays unallocated"
    return DETAILS[code]


def uncertain_detail(e: Eligibility | None, sale_ms: int | None, offset_ms: int) -> str:
    """The detail under "Forecast too uncertain" (SPEC §3.4D.2, §3.4D.6, PF20, PF30).

    Args:
        e: The batch's eligibility facts, or None.
        sale_ms: Its expected sale hour, or None.
        offset_ms: Local time minus UTC.

    Returns:
        "a forecast from 4 closes in 30 days is noise; a window sale needs 24" when closes failed, else "its Sunday
        peak has been seen once; a forecast needs 3 weeks"; "" without facts.
    """
    if e is None:
        return ""
    if e.window_closes is not None and e.window_closes < MIN_WINDOW_CLOSES:
        n = e.window_closes
        return f"a forecast from {n} close{'s' if n != 1 else ''} in 30 days is noise; a window sale needs {MIN_WINDOW_CLOSES}"
    weeks = e.untrusted_weeks or 0
    seen = {1: "once", 2: "twice"}.get(weeks, f"{weeks} times")
    peak = f"its {weekday_name(sale_ms, offset_ms)} peak" if sale_ms is not None else "its peak"
    return f"{peak} has been seen {seen}; a forecast needs {PATTERN_WEEKS} weeks"


def status_cells(r: OppRow, ctx: _Ctx) -> dict:
    """The Status column (SPEC §3.4D.6): the Funded badge, or the reason and its detail line.

    Args:
        r: The row.
        ctx: The build.

    Returns:
        {"status", "status_class", "detail", "excluded"}: status "funded" for a funded row.
    """
    if r.reason is None:
        return {"status": "funded", "status_class": "funded", "detail": "", "excluded": False}
    code = reason_code(r.reason)
    return {"status": r.reason, "status_class": "neg" if code in NEG_CODES else "amber",
            "detail": reason_detail(r, ctx), "excluded": True}


def market_cells(r: OppRow, ctx: _Ctx) -> dict:
    """The market activity cells (SPEC §3.5): Chance with its dot, Est. Sales/Day, Current Supply, Supply/Volume.

    Args:
        r: The row (its first batch's chance and confidence).
        ctx: The build.

    Returns:
        The cells and their sort values.
    """
    look = ctx.look(r.first.key, r.name)
    m = market_activity(r.first.chance, r.first.conf, look.sold_per_day, look.source, look.units_on_ah, look.kind)
    return {"chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot,
            "sales": m.sales, "supply": m.supply, "days": m.days,
            "sort": {"chance": m.percent if m.percent is not None else -1, "sales": m.sales_tenths,
                     "supply": m.supply_units, "days": m.days_tenths}}


def common_cells(r: OppRow, ctx: _Ctx) -> dict:
    """The cells sections B and C share.

    Args:
        r: The row.
        ctx: The build.

    Returns:
        Row dict: strategy, item, quality, qty, net, character, status and market cells, sort values.
    """
    m = market_cells(r, ctx)
    net = r.net
    look = ctx.look(r.first.key, r.name)
    return {**m, **status_cells(r, ctx), "id": r.action_id, "strategy": r.first.label, "item": look.name,
            "quality": look.quality, "qty": r.qty, "net": signed_money(net),
            "net_class": "pos" if round_half_up(net) > 0 else "neg" if round_half_up(net) < 0 else "",
            "gold": money(r.gold), "character": short_name(r.first.char_id),
            "sort": {**m["sort"], "net": round_half_up(net), "adjusted": round_half_up(r.adjusted), "gold": r.gold}}


def today_row(r: OppRow, ctx: _Ctx) -> dict:
    """One row of section B (SPEC §3.5): Strategy · Action · Item · Qty · Each · Total · Expected net · market · Risk.

    Args:
        r: The row.
        ctx: The build.

    Returns:
        Row dict; Total is Each × Qty; Risk is gold needed ÷ gold in hand.
    """
    each = each_copper(r)
    risk_text, amber = risk(r.gold, ctx.acct.cash_gold, ctx.inp.high_risk_pct)
    row = common_cells(r, ctx)
    row["sort"]["risk"] = round_half_up(r.gold / ctx.acct.cash_gold * 1000) if ctx.acct.cash_gold > 0 else -1
    return {**row, "action": ACTION_OF[r.first.strategy], "lines": materials_text(r.first, ctx),
            "each": money(each), "total": money(None if each is None else each * r.qty), "risk": risk_text,
            "risk_amber": amber}


def week_row(r: OppRow, ctx: _Ctx) -> dict:
    """One row of section C (SPEC §3.5): Buy when · Process · Sell when · Gold needed · Materials · Adjusted.

    Args:
        r: The row.
        ctx: The build.

    Returns:
        Row dict; Adjusted carries "Wait 3.35 d · premium 31.75%" for its first batch's sale hour (SPEC §3.4D.2).
    """
    c, inp = r.first, ctx.inp
    wait = wait_hundredths(inp.now_ms, c.sale_ms)
    premium = premium_micro(wait, inp.ps.premium_base, inp.ps.premium_per_day, inp.ps.immediate_hours)
    sell = sale_text(c.sale_ms, inp.now_ms, inp.offset_ms)
    if c.window_end_ms is not None and sell != "now":
        sell += "–" + clock(c.window_end_ms, inp.offset_ms)[1]
    sale_note = "" if c.sale_ms is None or c.sale_ms <= inp.now_ms else \
        f"expected sale {clock(c.sale_ms, inp.offset_ms)[1]}"
    return {**common_cells(r, ctx), "buy_when": when_text(c.buy_ms, inp.now_ms, inp.offset_ms),
            "process": PROCESS_OF.get(c.strategy, "—"), "sell_when": sell, "sale_note": sale_note,
            "materials": " · ".join(materials_text(c, ctx)) or "—", "adjusted": signed_money(r.adjusted),
            "wait_note": f"Wait {wait_text(wait)} · premium {premium_text(premium)}"}


# ---------------------------------------------------------------- sections

def alternatives_text(n: int) -> str:
    """The footer's count of excluded rows.

    Args:
        n: Excluded rows.

    Returns:
        "3 alternatives excluded (in no total)" or "1 alternative excluded (in no total)".
    """
    return f"{n} alternative{'s' if n != 1 else ''} excluded (in no total)"


def list_totals(rows: Sequence[OppRow], limit: int) -> str:
    """The start of a list's footer (SPEC §3.5, §3.4D.8).

    Args:
        rows: The list's funded rows.
        limit: Its limit (today_limit, week_limit).

    Returns:
        "5 of 20 funded · expected +1g 15s 14c"; the profit % leaves out rows with no cash.
    """
    return f"{len(rows)} of {limit} funded · expected {signed_money(shown_sum(r.net for r in rows))}"


def profit_on_cash(rows: Sequence[OppRow]) -> str:
    """A list's profit on cash (SPEC §3.4D.8).

    Args:
        rows: Funded rows.

    Returns:
        "profit 113% on cash"; "profit — on cash" when no row uses cash.
    """
    return f"profit {pct_text(pool_profit_pct((r.net, r.cash) for r in rows))} on cash"


def today_section(funded: list[OppRow], excluded: list[OppRow], ctx: _Ctx) -> dict:
    """Section B, one complete view per Min chance step (SPEC §3.5).

    Args:
        funded: Today's funded rows.
        excluded: Today's excluded rows.
        ctx: The build.

    Returns:
        {"by_min": {"0"|"25"|"50"|"75": {"funded", "excluded", "cut", "foot", "hidden"}}, "note"}: a hidden funded
        row stays funded and counts in the footer.
    """
    funded = sorted(funded, key=lambda r: (-r.net, r.name, r.first.char_id, r.opp))
    excluded = sorted(excluded, key=excluded_order)
    gold = sum(r.gold for r in funded)
    foot = [[f"{list_totals(funded, ctx.inp.ps.today_limit)} · gold needed {money(gold)} · {profit_on_cash(funded)}", ""],
            [alternatives_text(len(excluded)), "muted"]]
    rows = [(r, today_row(r, ctx)) for r in funded], [(r, today_row(r, ctx)) for r in excluded]
    by_min = {}
    for step in MIN_CHANCE_STEPS:
        shown = [[row for r, row in part if round_half_up(r.first.chance * 100) >= step] for part in rows]
        by_min[str(step)] = {"funded": shown[0], "excluded": shown[1], "cut": cut_text(ctx), "foot": foot,
                             "hidden": len(funded) + len(excluded) - len(shown[0]) - len(shown[1])}
    cashed = [r for r in funded if r.cash > 0]
    note = (f"Risk = gold needed ÷ gold in hand ({money(ctx.acct.cash_gold)}), as on every page. A post needs only its "
            f"deposit. Profit on cash = {money(shown_sum(r.net for r in cashed))} ÷ {money(sum(r.cash for r in cashed))}.")
    return {"by_min": by_min, "note": note}


def week_section(funded: list[OppRow], excluded: list[OppRow], ctx: _Ctx) -> dict:
    """Section C (SPEC §3.5): funded rows by adjusted profit, the red line, the excluded ones.

    Args:
        funded: The week's funded rows.
        excluded: The week's excluded rows.
        ctx: The build.

    Returns:
        {"funded", "excluded", "cut", "foot", "note"}; the note lists the wait and premium of every sale hour used.
    """
    funded = sorted(funded, key=lambda r: (-r.adjusted, r.name, r.first.char_id, r.opp))
    excluded = sorted(excluded, key=excluded_order)
    gold = sum(r.gold for r in funded)
    foot = [[f"{list_totals(funded, ctx.inp.ps.week_limit)} · adjusted {signed_money(shown_sum(r.adjusted for r in funded))}"
             f" · gold needed {money(gold)} · {profit_on_cash(funded)}", ""],
            [alternatives_text(len(excluded)), "muted"]]
    return {"funded": [week_row(r, ctx) for r in funded], "excluded": [week_row(r, ctx) for r in excluded],
            "cut": cut_text(ctx), "foot": foot, "note": premium_note(funded, ctx)}


def premium_note(funded: Sequence[OppRow], ctx: _Ctx) -> str:
    """The note under section C: the wait and premium of each expected sale hour the plan uses (SPEC §3.4D.2).

    Args:
        funded: The week's funded rows.
        ctx: The build.

    Returns:
        E.g. "Premium = 15% + 5% × the wait in days from now to the expected sale hour: Thu 19:00 3.35 d → 31.75% ·
        … Within the immediate window (8 h): 0%."
    """
    ps, inp = ctx.inp.ps, ctx.inp
    hours = sorted({r.first.sale_ms for r in funded if r.first.sale_ms is not None})
    parts = []
    for ms in hours:
        w = wait_hundredths(inp.now_ms, ms)
        day, hour = clock(ms, inp.offset_ms)
        parts.append(f"{day} {hour} {wait_text(w)} → "
                     f"{premium_text(premium_micro(w, ps.premium_base, ps.premium_per_day, ps.immediate_hours))}")
    base, per_day_pct = round_half_up(ps.premium_base * 100), round_half_up(ps.premium_per_day * 100)
    head = f"Premium = {base}% + {per_day_pct}% × the wait in days from now to the expected sale hour"
    return f"{head}{': ' + ' · '.join(parts) if parts else ''}. Within the immediate window ({ps.immediate_hours} h): 0%."


def cut_text(ctx: _Ctx) -> str:
    """The red line between funded rows and alternatives.

    Args:
        ctx: The build.

    Returns:
        "capacity reached · 24s 40c left" when a row was left out for the risk tolerance, else "funded rows end here ·
        17g 73s 42c left" (what stopped them was a reason other than the capacity).
    """
    return f"{'capacity reached' if ctx.capacity_reached else 'funded rows end here'} · {money(ctx.left)} left"


def pool_cells(r: OppRow, ctx: _Ctx) -> dict:
    """One row's cells in the funding card.

    Args:
        r: The row.
        ctx: The build.

    Returns:
        {"item", "quality", "sells", "adjusted", "net", "net_class", "gold", "cash", "pct", "pct_note"}.
    """
    pct = profit_pct(r.net, r.cash)
    look = ctx.look(r.first.key, r.name)
    return {"item": look.name, "quality": look.quality,
            "sells": sale_text(r.first.sale_ms, ctx.inp.now_ms, ctx.inp.offset_ms), "adjusted": money(r.adjusted),
            "net": signed_money(r.net), "net_class": "pos" if round_half_up(r.net) > 0 else "neg",
            "gold": money(r.gold), "cash": money(r.cash), "pct": pct_text(pct),
            "pct_note": "found stock" if pct is None else ""}


def pool_section(funded: list[OppRow], excluded: list[OppRow], ctx: _Ctx) -> dict:
    """The funding card, "How the pool was funded" (SPEC §3.5, D78).

    Args:
        funded: Every funded row, both lists.
        excluded: Every excluded row, both lists.
        ctx: The build.

    Returns:
        {"rows", "total", "cut", "first_excluded"}: rows by adjusted profit with the capacity left after each; the
        total; the red line; then the best row the capacity left out ("needs 45s: not funded"), or None.
    """
    rows, left = [], ctx.acct.remaining_capacity
    ordered = sorted(funded, key=lambda r: (-r.adjusted, r.name, r.opp))
    for n, r in enumerate(ordered, start=1):
        left -= r.gold
        rows.append({"n": n, **pool_cells(r, ctx), "left": money(left)})
    found = [ctx.look(r.first.key, r.name).name for r in ordered if r.cash <= 0]
    total = {"label": "Total funded", "note": f"% leaves out {', '.join(found)} (found stock only)" if found else "",
             "rows": f"{len(ordered)} row{'s' if len(ordered) != 1 else ''}", "adjusted": money(shown_sum(r.adjusted for r in ordered)),
             "net": signed_money(shown_sum(r.net for r in ordered)), "gold": money(sum(r.gold for r in ordered)),
             "cash": money(sum(r.cash for r in ordered)),
             "pct": pct_text(pool_profit_pct((r.net, r.cash) for r in ordered)), "left": money(left)}
    over = sorted((r for r in excluded if reason_code(r.reason) == "risk"), key=lambda r: (-r.adjusted, r.name, r.opp))
    first = None if not over else {**pool_cells(over[0], ctx), "left": f"needs {money(over[0].gold)}: not funded"}
    return {"rows": rows, "total": total, "cut": cut_text(ctx), "first_excluded": first}


# ---------------------------------------------------------------- tiles, positions, empty state

def _tile(label: str, value: str, detail: str, of: str = "", is_risk: bool = False) -> dict:
    """One summary tile.

    Args:
        label: Its name.
        value: Its formatted value.
        detail: The line under it.
        of: A muted suffix to the value ("of 6g 49s 40c"), or "".
        is_risk: True for the Risk tolerance tile (drawn as the dropdown).

    Returns:
        {"label", "value", "of", "detail", "risk"}.
    """
    return {"label": label, "value": value, "of": of, "detail": detail, "risk": is_risk}


def tiles(ctx: _Ctx, today_gold: int, week_gold: int) -> list[dict]:
    """The summary tiles (SPEC §3.5, D72).

    Args:
        ctx: The build.
        today_gold: Gold needed of Today's funded rows, copper.
        week_gold: Gold needed of the week's funded rows, copper.

    Returns:
        Six {"label", "value", "of", "detail", "risk"}; the Risk tolerance tile has risk True (the dropdown).
    """
    a, inp = ctx.acct, ctx.inp
    share = committed_share_pct(a)
    pct = risk_percent(inp.ps.risk_tolerance)
    golds = " · ".join(f"{short_name(c.char_id)} {money(c.gold)}" for c in sorted(inp.chars, key=lambda c: c.char_id))
    tile = _tile
    return [tile("Gold in hand", money(a.cash_gold), golds or "no characters yet"),
            tile("Committed capital", money(a.committed_capital), f"{pct_text(share)} of capital base"),
            tile("Capital base", money(a.capital_base), "gold in hand + committed"),
            tile("Risk tolerance", f"{pct}%", f"exposure limit {money(a.risk_budget)} · changing it re-runs the allocation",
                 is_risk=True),
            tile("Remaining capacity", money(a.remaining_capacity),
                 f"{money(a.risk_budget)} limit − {money(a.committed_capital)} committed"),
            tile("Allocated to new opportunities", money(today_gold + week_gold), f"Today {money(today_gold)} · next 7 "
                 f"days {money(week_gold)} · {money(ctx.left)} left", of=f"of {money(a.remaining_capacity)}")]


def risk_percent(risk_tolerance: float) -> int:
    """The risk tolerance as a whole percent.

    Args:
        risk_tolerance: Share in (0, 1].

    Returns:
        floor(x × 100 + 0.5).
    """
    return math.floor(risk_tolerance * 100 + 0.5)


def position_row(p: PositionView, ctx: _Ctx) -> dict:
    """One row of section A (SPEC §3.4D.9): Item · Qty · Character · Status · Paid · Cost basis left · Est. net ·
    Sell window · Next action.

    Args:
        p: The evaluated position (or found stock).
        ctx: The build.

    Returns:
        Row dict: Paid is what the position cost (0 and "found" for found stock), Cost basis left what it still
        commits, Sell window the latest recommended window, Next action the evaluation's text.
    """
    look, inp = ctx.look(p.key, str(p.key)), ctx.inp
    net = None if p.net_now is None else round_half_up(p.net_now)
    return {"item": look.name, "quality": look.quality, "note": "found · uses no capital" if p.found else "",
            "qty": str(p.units_left) if p.units_left == p.units else f"{p.units_left} of {p.units}",
            "character": short_name(p.char_id), "state": p.state, "state_class": STAGE_CLASS.get(p.state, "hold"),
            "paid": money(p.paid), "paid_note": "found" if p.found else "", "cost_left": money(p.cost_left),
            "net": "—" if net is None else signed_money(net),
            "net_class": "" if net is None else "pos" if net > 0 else "neg" if net < 0 else "",
            "window": when_text(p.window_ms, inp.now_ms, inp.offset_ms), "next": p.next_action,
            "sort": {"cost": p.cost_left, "net": -1 if net is None else net}}


def positions_section(ctx: _Ctx) -> dict:
    """Section A (SPEC §3.5, §3.4D.8, §3.4D.9).

    Args:
        ctx: The build.

    Returns:
        {"rows", "summary", "foot", "note"}: the summary counts the positions; foot segments [text, class] such as
        "+22% on committed capital": Σ est. net of the positions that cost something and have one ÷ committed capital,
        half up (found stock commits nothing, so it is left out of the %, like a funded row with no cash, D.8, and a
        "% leaves out Wool Cloth (found stock only)" segment names it). "estimated profit on positions" still counts
        found stock's est. net: it is what the positions are expected to make.
    """
    a = ctx.acct
    net = shown_sum(p.net_now for p in ctx.inp.positions if p.net_now is not None)
    costed = shown_sum(p.net_now for p in ctx.inp.positions if p.net_now is not None and not p.found)
    found = list(dict.fromkeys(ctx.look(p.key, str(p.key)).name for p in ctx.inp.positions
                               if p.found and p.net_now is not None))
    foot = [[f"Capital committed {money(a.committed_capital)}", ""]]
    share = committed_share_pct(a)
    if share is not None:
        foot.append([f"{share}% of capital base ({money(a.committed_capital)} ÷ {money(a.capital_base)})", ""])
    foot.append([f"estimated profit on positions {signed_money(net)}", ""])
    on_committed = profit_pct(costed, a.committed_capital)
    if on_committed is not None:
        sign = "+" if on_committed > 0 else "−" if on_committed < 0 else ""
        foot.append([f"{sign}{abs(on_committed)}% on committed capital",
                     "pos" if on_committed > 0 else "neg" if on_committed < 0 else ""])
        if found:
            foot.append([f"% leaves out {', '.join(found)} (found stock only)", "muted"])
    note = ("Cost basis left is what you paid for the units not yet paid out to you: the capital a position ties up. "
            "Found items cost 0 and use no capital, but their worth still counts in Est. net. Positions take no "
            "recommendation slots.")
    n = len(ctx.inp.positions)
    summary = f"gold already committed · {n} position{'s' if n != 1 else ''} · they take no recommendation slots"
    return {"rows": [position_row(p, ctx) for p in ctx.inp.positions], "summary": summary, "foot": foot, "note": note}


def empty_block(ctx: _Ctx, funded: int, today: int, week: int, any_pays: bool) -> dict | None:
    """The block that replaces sections B and C when nothing new is funded (SPEC §3.4D.7, D77).

    Args:
        ctx: The build.
        funded: Funded rows, both lists.
        today: Today's funded rows.
        week: The week's funded rows.
        any_pays: Some candidate nets above 0.

    Returns:
        None when something is funded, else {"title", "text"}: the first matching reason and what it means.
    """
    if funded:
        return None
    a, inp = ctx.acct, ctx.inp
    lists_full = today >= inp.ps.today_limit and week >= inp.ps.week_limit
    title = empty_state(len(inp.chars), a.cash_gold, a.committed_capital, a.risk_budget, lists_full,
                        inp.prices_stale, any_pays)
    text = next(t for word, t in EMPTY_TEXTS if word in title)
    return {"title": title, "text": text}


def portfolio_view(inp: PortfolioInputs) -> dict:
    """The Portfolio page's payload (SPEC §3.5 dashboard data, D72, D73, D77, D78).

    Args:
        inp: The plan run, the positions, the looks and the settings.

    Returns:
        {"tiles", "fresh", "risk": {"options", "selected", "values"}, "positions", "today", "week", "pool", "empty",
        "help"}: every cell formatted, every row with its status and sort values.
    """
    plan = inp.plan
    acct = plan.accounting if plan is not None else accounting(inp.chars, [], inp.ps.risk_tolerance)
    funded, excluded = opportunity_rows(plan, inp.action_ids) if plan is not None else ([], [])
    ctx = _Ctx(acct, plan.selection.capacity_left if plan is not None else acct.remaining_capacity, inp,
               any(reason_code(r.reason) == "risk" for r in excluded))
    t_funded = [r for r in funded if r.lst == "today"]
    w_funded = [r for r in funded if r.lst != "today"]
    pays = any(c.expected_net > 0 for c in (plan.funded if plan else ())) or \
        any(a.candidate.expected_net > 0 for a in (plan.alternatives if plan else ()))
    day, hour = clock(inp.now_ms, inp.offset_ms)
    pct = risk_percent(inp.ps.risk_tolerance)
    return {
        "tiles": tiles(ctx, sum(r.gold for r in t_funded), sum(r.gold for r in w_funded)),
        "fresh": f"portfolio snapshot {day} {hour} · tiles show the values at {pct}% · gold as each character last "
                 f"logged in · positions from the journal",
        "risk": {"options": list(RISK_OPTIONS), "selected": pct, "values": [p / 100 for p in RISK_OPTIONS]},
        "positions": positions_section(ctx),
        "today": today_section(t_funded, [r for r in excluded if r.lst == "today"], ctx),
        "week": week_section(w_funded, [r for r in excluded if r.lst != "today"], ctx),
        "pool": pool_section(funded, excluded, ctx),
        "empty": empty_block(ctx, len(funded), len(t_funded), len(w_funded), pays),
        "help": COLUMN_HELP,
    }
