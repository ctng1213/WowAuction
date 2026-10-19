"""The History page: the journal of what you did and how it matched your plan, with 30-day tiles (SPEC §3.5, D47, D48).

Every text is made here; the dashboard only draws it. Pure.
"""
from __future__ import annotations

import time
from collections.abc import Sequence

from ahsync.engine import fees
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.views import money, signed_money

DAY_MS = 86_400_000
RECENT_DAYS = 6                 # within this many days the time shows its weekday, else its date

EVENTS = {   # kind -> (label, class)
    "posted": ("Posted", ""), "sold": ("Sold", "pos"), "expired": ("Expired", "neg"), "cancelled": ("Cancelled", "muted"),
    "won": ("Won", ""), "bought": ("Bought", ""), "bought_vendor": ("Bought (vendor)", ""), "crafted": ("Crafted", ""),
    "disenchanted": ("Disenchanted", ""), "paid_out": ("Paid out", "pos"), "mailed": ("Mailed", "muted"),
    "missed": ("—", "muted"), "skipped": ("—", "muted"),
}


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
    _, _, kind, _, qty, price, _, a, b = row
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
    return ""


def _tiles(rows: Sequence[tuple]) -> list[list[str]]:
    """The 30-day tiles: sold, expired, deposits lost, plan actions, realized profit (D48).

    Args:
        rows: Journal rows of the last 30 days.

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
    lost = sum(r[8] or 0 for r in expired)
    done = [r for r in rows if r[6] is not None and r[6][0] == "done"]           # events that completed an action
    realized = [r[8] for r in paid if r[8] is not None]
    return [["Sold (30 d)", f"{len(sold)} auctions", f"{money(after_cut)} after the cut"],
            ["Expired (30 d)", f"{len(expired)} auctions", f"{len(of('cancelled'))} cancelled"],
            ["Deposits lost", money(lost), "30 days"],
            ["Plan actions", f"{len(done)} done", f"{len(of('missed'))} missed · {len(of('skipped'))} skipped"],
            ["Realized profit", signed_money(sum(realized)), f"{len(realized)} payouts · after cost and deposit"]]


def history_view(rows: Sequence[tuple], looks: dict[int, ItemLook], names: dict[int, str], now_ms: int,
                 offset_ms: int) -> dict:
    """The History page (SPEC §3.5, D47, D48).

    Args:
        rows: Journal rows of the last 30 days, newest first (state.journal).
        looks: Item key -> name and quality.
        names: Item key -> name (disenchant results).
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"rows": [{"when", "character", "event", "event_class", "kind", "item", "quality", "qty", "price", "plan",
        "plan_class", "detail", "sort": {"at", "age_days"}}], "tiles": [[label, value, note]]}.
    """
    out = []
    for row in rows:
        at, char_id, kind, key, qty, price = row[:6]
        label, cls = EVENTS.get(kind, (kind, "muted"))
        plan, plan_class = _plan(row[6])
        look = looks[key]
        shown_price = row[7] if kind == "paid_out" else price
        out.append({"when": short_when(at, now_ms, offset_ms), "character": char_id.split("-")[0], "event": label,
                    "event_class": cls, "kind": kind, "item": look.name, "quality": look.quality, "qty": qty,
                    "price": money(shown_price), "plan": plan, "plan_class": plan_class,
                    "detail": _detail(row, names, now_ms, offset_ms),
                    "sort": {"at": at, "age_days": (now_ms - at) // DAY_MS}})
    return {"rows": out, "tiles": _tiles(rows)}
