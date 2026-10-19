"""The Rules page: items waiting for a decision, items kept on a fresh start, and the blacklist (SPEC §3.3.3, §3.4.2, D50).

Every text is made here; the dashboard only draws it and posts the buttons to /api/rule. Pure.
"""
from __future__ import annotations

from collections.abc import Sequence

from ahsync.engine.history_view import short_when
from ahsync.engine.plan_views import ItemLook, record_line

BLACKLIST = {"no_buy": ("Don't buy or craft", "owned stock still sold"),
             "ignore": ("Ignore completely", "no recommendations at all")}


def _owned(lots: Sequence[tuple[str, int, int]], key: int) -> str:
    """Who holds an item, e.g. "10 (Brugan)" or "12 (Amaya 2, Brugan 10)".

    Args:
        lots: (character id, item key, units) you hold, unbound and not kept.
        key: The item.

    Returns:
        The text; "0" when nobody does.
    """
    held = sorted((c.split("-")[0], n) for c, k, n in lots if k == key and n > 0)
    if not held:
        return "0"
    total = sum(n for _, n in held)
    return f"{total} ({held[0][0]})" if len(held) == 1 else f"{total} ({', '.join(f'{c} {n}' for c, n in held)})"


def rules_view(rules: Sequence[tuple[int, str, int, str]], looks: dict[int, ItemLook],
               outcomes: Sequence[tuple[int, str, int, int, int, str]], lots: Sequence[tuple[str, int, int]],
               now_ms: int, offset_ms: int) -> dict:
    """The Rules page (SPEC §3.4.2, D50).

    Args:
        rules: Live item rules (key, rule, set_at ms, source "addon" | "dashboard"); cleared ones left out.
        looks: Item key -> look, with its sell record (records flag "not selling").
        outcomes: Resolved posts (key, post id, price, posted at, resolved at, outcome) of the last 90 days.
        lots: (character id, item key, units) you hold.
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"decisions": [{"key", "item", "quality", "record", "last_expired", "owned"}],
         "kept": [{"key", "item", "quality", "kept", "since", "status", "status_class"}],
         "blacklist": [{"key", "item", "quality", "rule", "effect", "set", "from"}]}, each by item name then key.
    """
    ruled = {key for key, *_ in rules}
    expired: dict[int, int] = {}
    for key, _, _, _, resolved, outcome in outcomes:
        if outcome == "expired":
            expired[key] = max(expired.get(key, 0), resolved)
    decisions = [{"key": str(key), "item": look.name, "quality": look.quality, "record": record_line(look.record),
                  "last_expired": short_when(expired[key], now_ms, offset_ms) if key in expired else "—",
                  "owned": _owned(lots, key)}
                 for key, look in looks.items()
                 if look.record is not None and look.record.not_selling and key not in ruled]
    kept, blacklist = [], []
    for key, rule, set_at, source in rules:
        look = looks[key]
        if rule == "keep":
            after = [o for k, _, _, at, _, o in outcomes if k == key and at >= set_at]
            sold, gone = after.count("sold"), after.count("expired")
            kept.append({"key": str(key), "item": look.name, "quality": look.quality, "kept": short_when(set_at, now_ms, offset_ms),
                         "since": f"{sold} sold · {gone} expired",
                         "status": "selling again" if sold else "watching: an expiry while cheapest asks again",
                         "status_class": "pos" if sold else "amber"})
        elif rule in BLACKLIST:
            label, effect = BLACKLIST[rule]
            blacklist.append({"key": str(key), "item": look.name, "quality": look.quality, "rule": label, "effect": effect,
                              "set": short_when(set_at, now_ms, offset_ms), "from": source or "—"})
    by_name = lambda row: (row["item"], int(row["key"]))      # noqa: E731
    return {"decisions": sorted(decisions, key=by_name), "kept": sorted(kept, key=by_name),
            "blacklist": sorted(blacklist, key=by_name)}
