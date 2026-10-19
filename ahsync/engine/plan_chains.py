"""Plan actions as the Today and Week views see them, and how a chain becomes one row (SPEC §3.4B, §3.4C, D62-D66).

A chain (a buy or craft with its linked steps) is one row on Today and Week: the action that carries its profit
(a flip's buy, a craft chain's final craft). Its mat buys and intermediate crafts by the same character fold into
that row as lines, in the Craft page's wording (planned_crafts.chain_lines); mail that another character sends for it
becomes a line too ("6 Copper Bar from Brugan · not mailed yet"), and the last line says when to sell. A step another
character does stays a row of its own, so the character who acts sees it; mail is always the sender's row (C22).
Holds and vendor sales are no rows on either view (D64). Pure: data in, text and numbers out.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ahsync.engine.transit import Transit
from ahsync.engine.views import money, round_half_up

LEFT_OUT = ("hold", "vendor")          # never a Today or Week row, and in no total (D64)
FOLDS = ("buy", "craft")               # step kinds that fold into their chain's row


@dataclass(frozen=True)
class ViewAction:
    """One plan action as the views see it: a new one, or one kept as history."""

    action_id: str
    parent_id: str | None          # the chain's first action, or None
    kind: str                      # buy | craft | mail | post | de | vendor | hold
    char_id: str
    key: int
    qty: int
    price: int | None              # each, copper
    at_ms: int
    net: float                     # as planned (see plan_views for how views count it)
    p: float | None                # chance it sells over its window (posts), or None
    status: str                    # pending | late | done | partial | missed | skipped
    peak_ms: int | None = None     # posts: the target window's best hour
    in_chain: bool = False         # part of a chain (has a parent, or is a parent)
    expected: float = 0.0          # expected value as planned (a flip's buy: its posts' value), float copper
    vendor: bool = False           # a buy from a vendor (D41)
    to_char: str | None = None     # a mail's recipient (C22)
    done_at: int | None = None     # when it was done, UTC ms (history), else None
    deposit: int | None = None     # posts: the deposit, copper (the only gold a post can lose); None when unknown


@dataclass(frozen=True)
class Arriving:
    """Mail on its way, for the lines under the crafts that use it (C23)."""

    records: tuple[Transit, ...] = ()                                  # engine.transit.in_transit
    uses: Mapping[int, frozenset[int]] = field(default_factory=dict)   # product key -> the mats its recipes use

    def for_craft(self, a: "ViewAction") -> list[Transit]:
        """The mail on its way to a craft's character of the mats its product's recipes use.

        Args:
            a: A craft row's action.

        Returns:
            Those records, in their order.
        """
        mats = self.uses.get(a.key, frozenset())
        return [t for t in self.records if t.recipient == a.char_id and t.key in mats]


def clock(ms: int, offset_ms: int) -> tuple[str, str]:
    """Local weekday and time of a moment.

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        ("Fri", "14:10").
    """
    t = time.gmtime((ms + offset_ms) // 1000)
    return time.strftime("%a", t), time.strftime("%H:%M", t)


def role(a: ViewAction) -> str:
    """How an action counts toward totals.

    Args:
        a: The action.

    Returns:
        "sale" (its own net), "step" (no net), "profit" (the chain's profit) or "follow" (a chain's post).
    """
    if not a.in_chain:
        return "sale"
    if a.kind == "post":
        return "follow"
    return "profit" if a.net else "step"


def chain_id(a: ViewAction) -> str:
    """The chain an action belongs to.

    Args:
        a: The action.

    Returns:
        Its parent's id, or its own when it starts the chain or stands alone.
    """
    return a.parent_id or a.action_id


def short_name(char_id: str) -> str:
    """A character's name without its realm, as the views show it.

    Args:
        char_id: "Name-Realm".

    Returns:
        "Name".
    """
    return char_id.split("-")[0]


def buy_line(qty: int, name: str, vendor: bool, cost_copper: float) -> str:
    """A mat buy as a line under its chain's row, the Craft page's wording (D56, A63).

    Args:
        qty: Units.
        name: The mat's name.
        vendor: Bought from a vendor (else the AH).
        cost_copper: What the units cost together.

    Returns:
        "buy 76 Empty Vial from a vendor · 15s 20c" or "buy 22 Copper Ore on the AH · 50s 82c".
    """
    return f"buy {qty} {name} {'from a vendor' if vendor else 'on the AH'} · {money(cost_copper)}"


def first_line(qty: int, name: str) -> str:
    """An intermediate craft as a line under its chain's row (D56, A63).

    Args:
        qty: Units made.
        name: The intermediate's name.

    Returns:
        "craft 21 Tin Bar first".
    """
    return f"craft {qty} {name} first"


def by_chain(actions: Sequence[ViewAction]) -> dict[str, list[ViewAction]]:
    """Every chain's actions, each list by time then id.

    Args:
        actions: Plan actions, history included.

    Returns:
        chain id -> its actions; actions outside a chain are left out.
    """
    out: dict[str, list[ViewAction]] = {}
    for a in sorted(actions, key=lambda a: (a.at_ms, a.action_id)):
        if a.in_chain:
            out.setdefault(chain_id(a), []).append(a)
    return out


def carrier_of(members: Sequence[ViewAction]) -> ViewAction | None:
    """The action a chain's row is: its earliest profit step, else its last craft (D62).

    Args:
        members: The chain's actions by time then id.

    Returns:
        The carrier; None for a chain with neither (its steps stay rows).
    """
    profit = next((a for a in members if role(a) == "profit"), None)
    if profit is not None:
        return profit
    crafts = [a for a in members if a.kind == "craft"]
    return crafts[-1] if crafts else None


def folded(candidates: Sequence[ViewAction], chains: dict[str, list[ViewAction]]) -> dict[str, str]:
    """The steps that fold into their chain's row in a view (D62, D63).

    A mat buy or intermediate craft folds when it carries no net, its chain's carrier is a candidate row of the same
    view, and the same character does both; any other step stays a row (C22: the character who acts sees it).

    Args:
        candidates: The actions the view would show.
        chains: From by_chain, over every action.

    Returns:
        Step id -> its carrier's id.
    """
    ids = {a.action_id for a in candidates}
    out = {}
    for a in candidates:
        if not a.in_chain or a.kind not in FOLDS or role(a) != "step":
            continue
        carrier = carrier_of(chains.get(chain_id(a), ()))
        if carrier is not None and carrier.action_id in ids and carrier is not a and carrier.char_id == a.char_id:
            out[a.action_id] = carrier.action_id
    return out


def mail_line(m: ViewAction, name: str, offset_ms: int) -> str:
    """Incoming mail as a line under the crafter's row (C22, D66).

    Args:
        m: The mail action (its char_id is the sender).
        name: The item's name.
        offset_ms: Local time minus UTC.

    Returns:
        "6 Copper Bar from Brugan · not mailed yet", "… · mailed Mon 10:40" once done (its done time, else its slot),
        "… · partly mailed Mon 10:40" when part of it was sent, "… · skipped" when skipped.
    """
    head = f"{m.qty} {name} from {short_name(m.char_id)}"
    if m.status in ("done", "partial"):
        day, hour = clock(m.done_at if m.done_at is not None else m.at_ms, offset_ms)
        return f"{head} · {'mailed' if m.status == 'done' else 'partly mailed'} {day} {hour}"
    return f"{head} · {'skipped' if m.status == 'skipped' else 'not mailed yet'}"


def transit_line(t: Transit, name: str, offset_ms: int) -> str:
    """Mail on its way as a line under the craft that waits for it (C23).

    Args:
        t: The record.
        name: The item's name.
        offset_ms: Local time minus UTC.

    Returns:
        "6 Copper Bar from Brugan · mailed Mon 10:40 · on its way".
    """
    day, hour = clock(t.sent_at_ms, offset_ms)
    return f"{t.qty} {name} from {short_name(t.sender)} · mailed {day} {hour} · on its way"


def sale_line(carrier: ViewAction, members: Sequence[ViewAction], offset_ms: int) -> str | None:
    """A chain row's last line: what happens to what it buys or makes (D62).

    Args:
        carrier: The chain's row.
        members: The chain's actions by time then id.
        offset_ms: Local time minus UTC.

    Returns:
        "then: post Fri 11:00, for the 19:00 peak", "then: relist now" (a flip posting now), "then: post now" (a craft
        posting now), "then: disenchant it", or None when the chain plans no sale.
    """
    post = next((a for a in members if a.kind == "post"), None)
    if post is not None and post.peak_ms is not None:
        day, hour = clock(post.at_ms, offset_ms)
        return f"then: post {day} {hour}, for the {clock(post.peak_ms, offset_ms)[1]} peak"
    if post is not None:
        return "then: relist now" if carrier.kind == "buy" else "then: post now"
    return "then: disenchant it" if any(a.kind == "de" for a in members) else None


def chain_lines(carrier: ViewAction, members: Sequence[ViewAction], folds: dict[str, str], names: dict[int, str],
                offset_ms: int, arriving: Sequence[Transit] = ()) -> list[str]:
    """A chain row's lines: its folded mat buys, its folded intermediate crafts, its incoming mail, the mail on its way
    to it, then the sale.

    Args:
        carrier: The chain's row.
        members: The chain's actions by time then id.
        folds: From folded.
        names: Item key -> name.
        offset_ms: Local time minus UTC.
        arriving: Mail on its way that the craft uses (Arriving.for_craft, C23).

    Returns:
        The lines, e.g. ["buy 76 Empty Vial from a vendor · 15s 20c", "6 Copper Bar from Brugan · not mailed yet",
        "then: post Fri 11:00, for the 19:00 peak"] (D62, D66).
    """
    mine = [a for a in members if folds.get(a.action_id) == carrier.action_id]
    lines = [buy_line(a.qty, names.get(a.key, str(a.key)), a.vendor, (a.price or 0) * a.qty) for a in mine
             if a.kind == "buy"]
    lines += [first_line(a.qty, names.get(a.key, str(a.key))) for a in mine if a.kind == "craft"]
    lines += [mail_line(m, names.get(m.key, str(m.key)), offset_ms) for m in members
              if m.kind == "mail" and m.to_char == carrier.char_id and m.char_id != carrier.char_id]
    lines += [transit_line(t, names.get(t.key, str(t.key)), offset_ms) for t in arriving]
    sale = sale_line(carrier, members, offset_ms)
    return lines + ([sale] if sale else [])


def mat_gold(members: Sequence[ViewAction]) -> int:
    """What a chain's mat buys cost together: the gold it spends on mats (SPEC §3.4 money table).

    Args:
        members: The chain's actions.

    Returns:
        Copper: price × units over its buys, every character's.
    """
    return sum((a.price or 0) * a.qty for a in members if a.kind == "buy")


def row_money(a: ViewAction, members: Sequence[ViewAction]) -> tuple[str, str, int]:
    """A row's Each and Total cells and the gold it puts at risk (SPEC §3.4 money table, Risk).

    Args:
        a: The row's action.
        members: Its chain's actions when it carries the chain's row, else empty.

    Returns:
        (each, total, spend copper). A craft carrying a chain (members given): each = the chain's mat gold ÷ its crafts,
        half up, total = the mat gold, both "—" when every mat is owned; spend = the mat gold. A buy: its price each and
        price × units, spend the same. A post: its price and listing value, spend its deposit (SPEC §3.4 Risk; 0 when
        unknown). Anything else: its price and price × units ("—" without one), spend 0.
    """
    if a.kind == "craft" and members:
        gold = mat_gold(members)
        if gold <= 0:
            return "—", "—", 0
        return money(round_half_up(gold / max(1, a.qty))), money(gold), gold
    total = money(a.price * a.qty) if a.price is not None else "—"
    spend = (a.price or 0) * a.qty if a.kind == "buy" else (a.deposit or 0) if a.kind == "post" else 0
    return money(a.price), total, spend
