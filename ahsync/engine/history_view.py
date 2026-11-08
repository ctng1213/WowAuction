"""The History page: the journal of what you did and how it matched your plan, with 30-day tiles (SPEC §3.5, D47, D48),
and the plan's expected profit next to the profit realized (D68-D70).

Once positions exist (SPEC §3.4D.9, PO9) each one has a row of its own: Expected its expected net at open, Realized its
proceeds less its cost and lost deposits once Completed. The events that count on a position (its lots, posts, sold
mail and payouts) keep their journal rows without cells, so a profit is still counted once; events of items that never
matched a plan keep their event-level cells. Every text is made here; the dashboard only draws it. Pure.
"""
from __future__ import annotations

import time
from collections.abc import Sequence

from ahsync.engine import fees
from ahsync.engine.plan_chains import short_name
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.position_views import history_judged, history_positions
from ahsync.engine.positions import COMPLETED, Position, rolled_events
from ahsync.engine.tracking import Judged, ProfitSums, judge, profit_sums
from ahsync.engine.views import money, round_half_up, signed_money

DAY_MS = 86_400_000
RECENT_DAYS = 6                 # within this many days the time shows its weekday, else its date

EVENTS = {   # kind -> (label, class)
    "posted": ("Posted", ""), "sold": ("Sold", "pos"), "expired": ("Expired", "neg"), "cancelled": ("Cancelled", "muted"),
    "won": ("Won", ""), "bought": ("Bought", ""), "bought_vendor": ("Bought (vendor)", ""), "crafted": ("Crafted", ""),
    "disenchanted": ("Disenchanted", ""), "paid_out": ("Paid out", "pos"), "mailed": ("Mailed", "muted"),
    "missed": ("—", "muted"), "skipped": ("—", "muted"), "untracked": ("Untracked", "amber"),
    "position": ("Position", ""),
}
ROLLED_NOTE = "counts on its position"     # an event whose profit its position carries (PO9)


def short_when(ms: int, now_ms: int, offset_ms: int) -> str:
    """A journal time in local time: "Fri 14:10" within 6 days, else "9 Oct 14:10".

    Args:
        ms: UTC ms.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    t = time.gmtime((ms + offset_ms) // 1000)
    head = time.strftime("%a", t) if now_ms - ms < RECENT_DAYS * DAY_MS else f"{t.tm_mday} {time.strftime('%b', t)}"
    return f"{head} {time.strftime('%H:%M', t)}"


def _plan(plan: tuple[str, int, int] | None) -> tuple[str, str]:
    """The Plan action cell.

    Args:
        plan: (status, done units, planned units) of the matched action, or None.

    Returns:
        (text, class): "✓ done", "partial 6/10", "missed", "skipped", "pending", or "—".
    """
    if plan is None:
        return "—", "muted"
    status, done, qty = plan
    if status == "done":
        return "✓ done", "pos"
    if status == "partial":
        return f"partial {done}/{qty}", "amber"
    return status, "neg" if status == "missed" else "muted"


def _detail(row: tuple, names: dict[int, str], now_ms: int, offset_ms: int) -> str:
    """The Detail cell of one journal row.

    Args:
        row: A journal row (state.journal).
        names: Item key -> name, for disenchant results.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    _, _, kind, _, qty, price, _, a, b = row[:9]
    if kind == "posted":
        return f"{a} h · deposit {money(b)}"
    if kind in ("sold", "expired", "cancelled"):
        return f"post {short_when(a, now_ms, offset_ms)} resolved" if a is not None else "its post wasn't captured"
    if kind in ("bought", "bought_vendor"):
        return f"paid {money(price * qty)}" if price is not None else ""
    if kind == "disenchanted":
        parts = (part.split(",") for part in str(a).split(";") if part)
        return "gave " + ", ".join(f"{n} {names.get(int(k), k)}" for k, n in parts)
    if kind == "paid_out":
        return "gold taken from the mailbox" + (f" · realized {signed_money(b)}" if b is not None else "")
    if kind == "mailed":
        return f"to {str(a).split('-')[0]}"
    if kind == "missed":
        return f"{a} slot passed, nothing matched"
    if kind == "skipped":
        return "skipped by you"
    if kind == "untracked":
        return (f"+{a}: looted, traded or found · cost 0" if a > 0 else
                f"−{-a}: used, sold to a vendor or destroyed") + " · no recorded event"
    return ""


def ratio_text(sums: ProfitSums) -> str:
    """Realized ÷ expected as a whole percent (D70, K7).

    Args:
        sums: The resolved rows' sums.

    Returns:
        "87%" (half up; "−12%" below zero), or "—" when nothing is resolved or Σ expected is not above 0.
    """
    if not sums.resolved or sums.expected <= 0:
        return "—"
    pct = round_half_up(sums.realized / sums.expected * 100)
    return f"{'−' if pct < 0 else ''}{abs(pct)}%"


def open_text(sums: ProfitSums) -> str:
    """The line under the tiles: what is still open and so counted in neither sum (D70, PO9).

    Args:
        sums: The sums.

    Returns:
        "1 post open · 2 chains in progress · 1 position open (counted once resolved)", any part alone, or
        "nothing open".
    """
    parts = ([f"{sums.open_posts} post{'s' * (sums.open_posts != 1)} open"] * bool(sums.open_posts) +
             [f"{sums.open_chains} chain{'s' * (sums.open_chains != 1)} in progress"] * bool(sums.open_chains) +
             [f"{sums.open_positions} position{'s' * (sums.open_positions != 1)} open"] * bool(sums.open_positions))
    return " · ".join(parts) + " (counted once resolved)" if parts else "nothing open"


def judged_rows(rows: Sequence[tuple], positions: Sequence[Position], since_ms: int) -> list[Judged]:
    """Every judged History row: the events' own, less those that count on a position, then the positions' (PO9).

    The History tiles and the Status page's calibration line (K7) both sum these with profit_sums.

    Args:
        rows: Journal rows (state.journal; the 12th column is the event id).
        positions: Positions (engine.positions.track).
        since_ms: Oldest close of a position to include, UTC ms.

    Returns:
        Judged rows: each event's unless a position counted it, then each listed position's.
    """
    rolled = rolled_events(positions)
    out = [j for r in rows if not _rolled(r, rolled) and (j := judge(r[2], r[9], r[10])) is not None]
    return out + [history_judged(p) for p in history_positions(positions, since_ms)]


def _rolled(row: tuple, rolled: frozenset[str]) -> bool:
    """Whether a journal row's event counts on a position.

    Args:
        row: A journal row.
        rolled: Event ids positions counted.

    Returns:
        True when its event id (the 12th column, when present) is among them.
    """
    return len(row) > 11 and row[11] in rolled


def _tiles(rows: Sequence[tuple], sums: ProfitSums) -> list[list[str]]:
    """The 30-day tiles: sold, expired, deposits lost, plan actions, realized profit (D48), and expected against
    realized over the resolved plan events (D70).

    Args:
        rows: Journal rows of the last 30 days.
        sums: Expected and realized over the rows' resolved plan events.

    Returns:
        [[label, value, note]].
    """
    def of(*kinds: str) -> list[tuple]:
        """The rows of some kinds.

        Args:
            kinds: Row kinds.

        Returns:
            Those rows.
        """
        return [r for r in rows if r[2] in kinds]
    sold, expired, paid = of("sold"), of("expired"), of("paid_out")
    after_cut = sum((r[5] or 0) * r[4] for r in sold) * (1 - fees.AH_CUT)
    lost = sum(r[8] or 0 for r in of("expired", "cancelled"))      # the AH keeps a cancel's deposit (T24)
    done = [r for r in rows if r[6] is not None and r[6][0] == "done"]           # events that completed an action
    realized = [r[8] for r in paid if r[8] is not None]
    return [["Sold (30 d)", f"{len(sold)} auctions", f"{money(after_cut)} after the cut"],
            ["Expired (30 d)", f"{len(expired)} auctions", f"{len(of('cancelled'))} cancelled"],
            ["Deposits lost", money(lost), "30 days"],
            ["Plan actions", f"{len(done)} done", f"{len(of('missed'))} missed · {len(of('skipped'))} skipped"],
            ["Realized profit", signed_money(sum(realized)), f"{len(realized)} payouts · after cost and deposit"],
            ["Expected (resolved)", signed_money(sums.expected), f"{sums.resolved} resolved plan events"],
            ["Realized", signed_money(sums.realized), "the same events"],
            ["Realized ÷ expected", ratio_text(sums), "how the plan's estimates held up"]]


def _cells(j: Judged | None) -> dict:
    """A row's Expected and Realized cells (D68, D69).

    Args:
        j: The row's judgement, or None for a row that gets none.

    Returns:
        {"expected", "realized", "realized_class", "realized_note"}: realized "pos" when it is at least the expected
        (in whole copper), "amber" below it, "muted" while unknown; empty texts for a row without cells.
    """
    if j is None:
        return {"expected": "", "realized": "", "realized_class": "", "realized_note": ""}
    expected = "—" if j.expected is None else signed_money(j.expected)
    if j.realized is None:
        return {"expected": expected, "realized": "—", "realized_class": "muted", "realized_note": j.note}
    cls = "" if j.expected is None else "pos" if round_half_up(j.realized) >= round_half_up(j.expected) else "amber"
    return {"expected": expected, "realized": signed_money(j.realized), "realized_class": cls, "realized_note": j.note}


def position_row(p: Position, look: ItemLook, now_ms: int, offset_ms: int) -> dict:
    """A position's History row (SPEC §3.4D.9, PO9): when it closed (else opened), Paid, its state, and its Expected
    and Realized cells.

    Args:
        p: The position.
        look: Its item.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        A row dict in the journal rows' shape, kind "position".
    """
    at = p.closed_at if p.closed_at is not None else p.opened_at
    done = p.state == COMPLETED
    detail = f"paid {money(p.cost_total)} · opened {short_when(p.opened_at, now_ms, offset_ms)}"
    return {"when": short_when(at, now_ms, offset_ms), "character": short_name(p.char_id), "event": "Position",
            "event_class": "pos" if done else "", "kind": "position", "item": look.name, "quality": look.quality,
            "qty": p.units, "price": money(p.cost_total), "plan": p.state, "plan_class": "pos" if done else "muted",
            "detail": detail, **_cells(history_judged(p)), "sort": {"at": at, "age_days": (now_ms - at) // DAY_MS}}


def history_view(rows: Sequence[tuple], looks: dict[int, ItemLook], names: dict[int, str], now_ms: int,
                 offset_ms: int, positions: Sequence[Position] = (), since_ms: int | None = None) -> dict:
    """The History page (SPEC §3.5, D47, D48, D68-D70, PO9).

    Args:
        rows: Journal rows of the last 30 days, newest first (state.journal).
        looks: Item key -> name and quality (the positions' items too).
        names: Item key -> name (disenchant results).
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.
        positions: Positions (engine.positions.track); their events count on them, never again on their own.
        since_ms: Oldest close of a position to list, UTC ms; 30 days before now when None.

    Returns:
        {"rows": [{"when", "character", "event", "event_class", "kind", "item", "quality", "qty", "price", "plan",
        "plan_class", "detail", "expected", "realized", "realized_class", "realized_note", "sort": {"at",
        "age_days"}}] newest first, positions among them, "tiles": [[label, value, note]], "open": the line under
        the tiles}.
    """
    since = now_ms - 30 * DAY_MS if since_ms is None else since_ms
    rolled = rolled_events(positions)
    out = []
    for row in rows:
        at, char_id, kind, key, qty, price = row[:6]
        label, cls = EVENTS.get(kind, (kind, "muted"))
        plan, plan_class = _plan(row[6])
        look = looks[key]
        shown_price = row[7] if kind == "paid_out" else price
        cells = _cells(judge(kind, row[9], row[10]))
        if _rolled(row, rolled):
            cells = {**_cells(None), "realized_note": ROLLED_NOTE}
        out.append({"when": short_when(at, now_ms, offset_ms), "character": char_id.split("-")[0], "event": label,
                    "event_class": cls, "kind": kind, "item": look.name, "quality": look.quality, "qty": qty,
                    "price": money(shown_price), "plan": plan, "plan_class": plan_class,
                    "detail": _detail(row, names, now_ms, offset_ms), **cells,
                    "sort": {"at": at, "age_days": (now_ms - at) // DAY_MS}})
    listed = history_positions(positions, since)
    out += [position_row(p, looks[p.key], now_ms, offset_ms) for p in listed]
    out.sort(key=lambda r: -r["sort"]["at"])
    sums = profit_sums(judged_rows(rows, positions, since))
    return {"rows": out, "tiles": _tiles(rows, sums), "open": open_text(sums)}
