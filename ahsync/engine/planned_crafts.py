"""The plan's craft chains, for the Craft pages (SPEC §3.5 Craft, §4.3, D56, A63).

A chain (a plan action's `chain`) with a craft is one row for its final product: the last craft. It lists
every mat buy, then the intermediate crafts made first, then when to sell; its profit is counted once, on the
action that carries it (chain_net, the Today and Week rule of plan_views.counted, D59). Pure.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.plan import PlanAction
from ahsync.engine.plan_views import clock
from ahsync.engine.views import money


@dataclass(frozen=True)
class CraftChain:
    """One planned craft chain."""

    chain: str
    final: PlanAction                       # the last craft: its product is what the chain makes
    buys: tuple[PlanAction, ...]            # mat buys, in plan order
    intermediates: tuple[PlanAction, ...]   # crafts made first, in plan order
    sale: PlanAction | None                 # the final product's first post, hold, vendor sale or disenchant
    net: float                              # the chain's expected net, float copper

    @property
    def mat_gold(self) -> int:
        """What the chain's buys cost.

        Returns:
            Copper.
        """
        return sum((b.price or 0) * b.qty for b in self.buys)


def chain_net(members: Sequence[PlanAction]) -> float:
    """A chain's profit, counted once (SPEC §3.4 expected net, VW1, D59), as plan_views.counted counts it.

    The chain's profit step (any action but a post that has a net: the final craft) carries the profit; its posts
    carry their own net against their cost only for when that step has left the view, so they add nothing while
    it is there. A chain with no such step (every craft net 0) counts its posts' own nets.

    Args:
        members: The chain's actions.

    Returns:
        Float copper.
    """
    steps = [a.net for a in members if a.kind != "post" and a.net]
    return sum(steps) if steps else sum(a.net for a in members if a.kind == "post")


def craft_chains(actions: Sequence[PlanAction]) -> list[CraftChain]:
    """The plan's craft chains, best net first (ties: chain id) (D56).

    Args:
        actions: Plan actions in plan order.

    Returns:
        One CraftChain per chain that has a craft.
    """
    groups: dict[str, list[PlanAction]] = {}
    for a in actions:
        if a.chain:
            groups.setdefault(a.chain, []).append(a)
    out = []
    for chain, members in groups.items():
        crafts = [a for a in members if a.kind == "craft"]
        if not crafts:
            continue
        final = max(crafts, key=lambda a: (a.at_ms, members.index(a)))
        sale = next((a for a in members if a.kind not in ("buy", "craft", "mail") and a.key == final.key), None)
        out.append(CraftChain(chain, final, tuple(a for a in members if a.kind == "buy"),
                              tuple(a for a in crafts if a is not final), sale, chain_net(members)))
    out.sort(key=lambda c: (-c.net, c.chain))
    return out


def chain_lines(c: CraftChain, names: dict[int, str], offset_ms: int) -> tuple[str, ...]:
    """A chain row's lines: each mat buy, each intermediate craft, then when to sell (D56, A63).

    Args:
        c: The chain.
        names: Item key -> name.
        offset_ms: Local time minus UTC, ms.

    Returns:
        E.g. "buy 22 Copper Ore on the AH · 50s 82c", "craft 22 Copper Bar first",
        "post Sat 09:00, for the 18:00 peak".
    """
    def name(key: int) -> str:
        """An item's name.

        Args:
            key: Item key.

        Returns:
            Its name, or its key.
        """
        return names.get(key, str(key))
    lines = [f"buy {b.qty} {name(b.key)} {'from a vendor' if b.source == 'vendor' else 'on the AH'} · "
             f"{money((b.price or 0) * b.qty)}" for b in c.buys]
    lines += [f"craft {a.qty} {name(a.key)} first" for a in c.intermediates]
    s = c.sale
    if s is None:
        lines.append("post it at the AH")
    elif s.kind == "post":
        day, hour = clock(s.at_ms, offset_ms)
        peak = f", for the {clock(s.peak_ms, offset_ms)[1]} peak" if s.peak_ms is not None else ""
        lines.append(f"post {day} {hour}{peak}")
    elif s.kind == "hold":
        lines.append("hold for a later week")
    elif s.kind == "vendor":
        lines.append(f"sell it to a merchant at {money(s.price)} each")
    else:
        lines.append("disenchant it")
    return tuple(lines)
