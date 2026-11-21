"""The Cooldowns page: rows, filters, Who and Can Make (SPEC §3.4F.6, §3.5; CD4-CD21, CD28, CD29).

Pure, in two steps. stage() runs at the full refresh and at every re-plan: it makes everything that
doesn't depend on the clock, including each character's absolute ready time, and the export's money
strings. render() runs at every request with that request's now_ms (CD20): it turns the ready times into
Ready, Who and Can Make. The filters are precomputed as views (profession × mode -> row keys and the
empty-result notice), so the page only draws.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ahsync.engine.cooldowns import (
    DAY_S, PROFESSIONS, CooldownRecipe, Holder, Standing, can_make, duration_text, group_members, mats_copper,
    net_per_use, per_week, standing)
from ahsync.engine.views import money, round_half_up

MODES = ("all", "mine", "skill")             # no toggle, Just my characters, My skill level only (implies mine)
OLD_PRICE_DAYS = 7                            # a price older than this shows muted with its age (§3.4F.4)
WHO_LINES = 3                                 # Who shows this many characters in full (§3.4F.6)
SHORT_COOLDOWN = "not limited by the cooldown: limited by the mats"
WHO_RANK = {"ready": 0, "cooling": 1, "not_learned": 2, "needs_item": 2, "not_read": 2, "below": 3}
HIDDEN_LABELS = (("cooling", "cooling down"), ("not_learned", "not learned"), ("needs_item", "needs item"),
                 ("not_read", "not read"))
CAN_MAKE = {"yes": ("Yes", "ready", 0), "bank": ("Yes (bank)", "ready", 1), "no": ("No", "muted", 2)}
COOLDOWNS_HELP = {
    "Cooldown": "How long the recipe is locked after each use. \"shared\": one use locks the whole group",
    "Per week": "What one character earns using it every time it is ready: Net / use × 7 ÷ the cooldown in days",
    "Who": "Every character with the profession: Ready, the time left, or what is missing",
    "Can Make": "Yes when a character can use it right now: skill, recipe, tool, cooldown and mats in its own bags",
}


@dataclass(frozen=True)
class PriceFact:
    """What the market says about one item (SPEC §3.4F.4)."""

    price: int | None                         # `cur`, copper; None = no price ever seen
    seen_ms: int | None = None                # when that price was seen, UTC ms
    sold_per_day: float | None = None


def _made_from(recipe: CooldownRecipe, names: Mapping[int, str]) -> str:
    """The Made from cell: "Thorium Bar, Arcane Crystal", "2 Felcloth".

    Args:
        recipe: A catalog row.
        names: Item key -> name.

    Returns:
        The mats in key order, with a count when more than 1.
    """
    return ", ".join(f"{count} {names.get(key, f'item {key}')}" if count > 1 else names.get(key, f"item {key}")
                     for key, count in recipe.reagents)


def _money_row(recipe: CooldownRecipe, prices: Mapping[int, PriceFact], names: Mapping[int, str]) -> dict:
    """A row's money: Mats, Net / use and Per week, with the mats' hover lines (SPEC §3.4F.4).

    Args:
        recipe: A catalog row.
        prices: Item key -> its PriceFact.
        names: Item key -> name.

    Returns:
        {"mats", "net", "per_week" (None when missing or under 1 day), "mats_lines"}.
    """
    cur = {key: f.price for key, f in prices.items()}
    mats = mats_copper(recipe, cur)
    net = net_per_use(recipe, cur.get(recipe.product), mats)
    lines = [f"{count} {names.get(key, f'item {key}')}: {money(cur.get(key) and cur[key] * count)}"
             for key, count in recipe.reagents]
    return {"mats": mats, "net": net, "per_week": per_week(recipe, net), "mats_lines": lines}


def _standing_dict(st: Standing) -> dict:
    """A standing as the staged payload keeps it.

    Args:
        st: A character's standing.

    Returns:
        Its fields as a JSON-ready dict.
    """
    return {"char": st.char_id, "name": st.name, "skill": st.skill, "state": st.state, "ready_at": st.ready_at_ms,
            "bank": st.bank, "missing": st.missing}


def _base_row(recipe: CooldownRecipe, recipes: Sequence[CooldownRecipe], holders: Sequence[Holder],
              prices: Mapping[int, PriceFact], names: Mapping[int, str], now_ms: int) -> dict:
    """One row without the clock (SPEC §3.4F.6).

    Args:
        recipe: The catalog row.
        recipes: The catalog (for its group).
        holders: Your characters, by name.
        prices: Item key -> PriceFact.
        names: Item key -> name.
        now_ms: The staging time, for the price's age.

    Returns:
        The staged row.
    """
    members = group_members(recipe, recipes)
    standings = [st for st in (standing(recipe, members, h, names) for h in holders) if st is not None]
    fact = prices.get(recipe.product, PriceFact(None))
    age = None if fact.seen_ms is None else (now_ms - fact.seen_ms) // (DAY_S * 1000)
    return {"spell": recipe.spell, "recipe": recipe.name, "product": recipe.product,
            "item": names.get(recipe.product, f"item {recipe.product}"), "profession": recipe.profession,
            "skill": recipe.skill, "cooldown_s": recipe.cooldown_s, "group": recipe.group,
            "group_size": len(members), "yield": recipe.yield_max, "tool": recipe.tool,
            "tool_name": names.get(recipe.tool, f"item {recipe.tool}") if recipe.tool is not None else None,
            "made_from": _made_from(recipe, names), "price": fact.price,
            "price_age_days": age if fact.price is not None and age is not None and age > OLD_PRICE_DAYS else None,
            "sold": fact.sold_per_day, **_money_row(recipe, prices, names),
            "standings": [_standing_dict(st) for st in standings], "mine": bool(standings),
            "skill_ok": any(st.skill >= recipe.skill for st in standings)}


def _order_key(row: dict) -> tuple:
    """The default order: AH price, highest first; no price last; ties by recipe name, then spell id (CD8).

    Args:
        row: A staged row.

    Returns:
        The sort key.
    """
    return (row["price"] is None, -(row["price"] or 0), row["recipe"], row["spell"])


def _notice(profession: str, mode: str, rows: Sequence[dict], holders: Sequence[Holder]) -> str:
    """Why a filter combination shows nothing (SPEC §3.4F.6, CD18, CD21).

    Args:
        profession: The Profession filter; "" = all.
        mode: One of MODES.
        rows: The rows of that profession, in default order.
        holders: Your characters, by name.

    Returns:
        The notice.
    """
    if not rows:
        return f"{profession}: no cooldown recipes."
    mine = [r for r in rows if r["mine"]]
    which = profession or "a profession with cooldown recipes"
    if mode == "mine" or not mine:
        return f"No cooldown recipe matches: none of your characters has {which}. Turn off \"Just my characters\" to see it."
    first = next(r for r in mine if not r["skill_ok"])
    best = max((st for st in first["standings"]), key=lambda st: (st["skill"], [-ord(c) for c in st["name"]]))
    return (f"No cooldown recipe matches. {best['name']} has {first['profession']} {best['skill']}; {first['item']} "
            f"needs {first['skill']}. Turn off \"My skill level only\" to see it.")


def _views(rows: Sequence[dict], holders: Sequence[Holder]) -> dict:
    """Every filter combination's row keys and notice (SPEC §3.4F.6, CD17).

    Args:
        rows: The staged rows, in default order.
        holders: Your characters.

    Returns:
        "<profession>|<mode>" -> {"keys": spell ids in default order, "notice": text or None}; profession "" = all.
    """
    out = {}
    for profession in ["", *PROFESSIONS]:
        of = [r for r in rows if not profession or r["profession"] == profession]
        for mode in MODES:
            keep = [r for r in of if mode == "all" or (r["mine"] and (mode == "mine" or r["skill_ok"]))]
            out[f"{profession}|{mode}"] = {"keys": [r["spell"] for r in keep],
                                           "notice": None if keep else _notice(profession, mode, of, holders)}
    return out


def export_text(row: dict) -> str:
    """A row's money for the addon's `CD` table (SPEC §3.5, EX23): "mats;net;perWeek", rounded half up.

    Args:
        row: A staged row.

    Returns:
        E.g. "292000;135500;474250"; an empty field = "—".
    """
    return ";".join("" if v is None else str(round_half_up(v)) for v in (row["mats"], row["net"], row["per_week"]))


def stage(recipes: Sequence[CooldownRecipe], version: int | None, holders: Sequence[Holder],
          prices: Mapping[int, PriceFact], names: Mapping[int, str], now_ms: int) -> dict:
    """The staged Cooldowns payload, without the clock (SPEC §3.4F.6).

    Args:
        recipes: The validated catalog.
        version: The catalog file's version.
        holders: The market's characters (any order).
        prices: Item key -> PriceFact for every catalog item.
        names: Item key -> name.
        now_ms: Staging time, for price ages.

    Returns:
        {"version", "characters" (count), "rows" (default order), "views", "export" (spell -> export_text)}.
    """
    ordered = sorted(holders, key=lambda h: (h.name, h.char_id))
    rows = sorted((_base_row(r, recipes, ordered, prices, names, now_ms) for r in recipes), key=_order_key)
    return {"version": version, "characters": len(ordered), "rows": rows, "views": _views(rows, ordered),
            "export": {str(r["spell"]): export_text(r) for r in rows}}


def _live(st: dict, now_ms: int) -> str:
    """A standing's state at a moment: "timed" becomes "ready" or "cooling".

    Args:
        st: A staged standing.
        now_ms: Now, UTC ms.

    Returns:
        A WHO_RANK key.
    """
    if st["state"] != "timed":
        return st["state"]
    return "ready" if st["ready_at"] <= now_ms else "cooling"


def _who_line(row: dict, st: dict, now_ms: int) -> dict:
    """One Who line (SPEC §3.4F.6, CD15).

    Args:
        row: A staged row.
        st: One of its standings.
        now_ms: Now.

    Returns:
        {"text", "cls" ("ready" | "amber" | ""), "rank" (sort key)}.
    """
    state = _live(st, now_ms)
    texts = {"ready": f"{st['name']} Ready", "not_learned": f"{st['name']} not learned",
             "needs_item": f"{st['name']} needs item", "not_read": f"{st['name']} not read",
             "below": f"{st['name']} needs {row['skill']} ({st['skill']})"}
    if state == "cooling":
        return {"text": f"{st['name']} {duration_text(st['ready_at'] - now_ms)}", "cls": "",
                "rank": (1, st["ready_at"], st["name"]), "state": state}
    return {"text": texts[state], "cls": "ready" if state == "ready" else "amber",
            "rank": (WHO_RANK[state], 0, st["name"]), "state": state}


def _who(row: dict, now_ms: int) -> dict:
    """The Who cell: every character with the profession, best first, folded past WHO_LINES (CD15, CD16).

    Args:
        row: A staged row.
        now_ms: Now.

    Returns:
        {"lines" (shown), "summary" ("2 Ready of 7" or None), "more" ("+4 more: …" or None), "hover" (all lines)}.
    """
    lines = sorted((_who_line(row, st, now_ms) for st in row["standings"]), key=lambda line: line["rank"])
    if not lines:
        return {"lines": [{"text": "—", "cls": "muted"}], "summary": None, "more": None, "hover": ""}
    shown, hidden = lines[:WHO_LINES], lines[WHO_LINES:]
    summary = more = None
    if hidden:
        ready = sum(1 for line in lines if line["state"] == "ready")
        summary = f"{ready} Ready of {len(lines)}"
        parts = [f"{sum(1 for h in hidden if h['state'] == s)} {label}" for s, label in HIDDEN_LABELS
                 if any(h["state"] == s for h in hidden)]
        below = sum(1 for h in hidden if h["state"] == "below")
        parts += [f"{below} below {row['skill']}"] if below else []
        more = f"+{len(hidden)} more: {', '.join(parts)}"
    return {"lines": [{"text": line["text"], "cls": line["cls"]} for line in shown], "summary": summary, "more": more,
            "hover": "\n".join(line["text"] for line in lines)}


def _recipe_of(row: dict) -> CooldownRecipe:
    """The catalog fields a staged row keeps, as a CooldownRecipe for engine.cooldowns.can_make.

    Args:
        row: A staged row.

    Returns:
        A CooldownRecipe without mats (Can Make's mats are already in each standing).
    """
    return CooldownRecipe(row["spell"], row["recipe"], row["profession"], row["skill"], row["cooldown_s"],
                          row["product"], row["yield"], row["yield"], (), row["group"], row["tool"])


def _can_make(row: dict, now_ms: int) -> dict:
    """The Can Make cell (SPEC §3.4F.5, §3.4F.6, CD19).

    Args:
        row: A staged row.
        now_ms: Now.

    Returns:
        {"text", "cls", "rank", "hover"}.
    """
    recipe, names = _recipe_of(row), {row["tool"]: row["tool_name"]} if row["tool"] is not None else {}
    outcomes = []
    for st in row["standings"]:
        held = Standing(st["char"], st["name"], st["skill"], st["state"], st["ready_at"], st["bank"], st["missing"])
        outcomes.append((st["name"],) + can_make(recipe, held, now_ms, names))
    best = min((o[1] for o in outcomes), key=lambda k: CAN_MAKE[k][2], default="no")
    text, cls, rank = CAN_MAKE[best]
    if best != "no":
        who = [f"{name} ({'bank' if k == 'bank' else 'bags'})" for name, k, _ in outcomes if k != "no"]
        hover = ", ".join(who) + ("; shared: one of these per cooldown" if row["group"] else "")
    elif outcomes:
        hover = "; ".join(f"{name}: {why}" for name, _, why in outcomes)
    else:
        hover = f"none of your characters has {row['profession']}"
    return {"text": text, "cls": cls, "rank": rank, "hover": hover}


def _cells(row: dict, now_ms: int) -> dict:
    """One row as the page draws it (SPEC §3.4F.6; the mockup's §3).

    Args:
        row: A staged row.
        now_ms: Now.

    Returns:
        The row's cells and its sort keys.
    """
    makes = f" · makes {row['yield']}" if row["yield"] > 1 else ""
    short = row["cooldown_s"] < DAY_S
    can = _can_make(row, now_ms)
    group_hover = (f"one cooldown for all {row['group_size']} {row['group']}s: using any of them locks all of them "
                   f"for the period of the one used") if row["group"] else ""
    return {
        "spell": row["spell"], "product": row["product"], "profession": row["profession"],
        "item": {"text": row["item"], "sub": row["recipe"] + makes},
        "skill": {"text": str(row["skill"]), "amber": not row["skill_ok"]},
        "cooldown": {"text": duration_text(row["cooldown_s"] * 1000), "sub": "shared" if row["group"] else "",
                     "hover": group_hover},
        "made_from": row["made_from"],
        "price": {"text": money(row["price"]), "muted": row["price"] is None or row["price_age_days"] is not None,
                  "sub": f"{row['price_age_days']} d old" if row["price_age_days"] is not None else ""},
        "mats": {"text": money(row["mats"]), "hover": "\n".join(row["mats_lines"])},
        "net": {"text": money(row["net"]), "neg": row["net"] is not None and row["net"] < 0},
        "per_week": {"text": money(row["per_week"]), "neg": row["per_week"] is not None and row["per_week"] < 0,
                     "hover": SHORT_COOLDOWN if short and row["net"] is not None else ""},
        "sold": "—" if row["sold"] is None else str(round_half_up(row["sold"])),
        "who": _who(row, now_ms), "can_make": {k: can[k] for k in ("text", "cls", "hover")},
        "sort": {"price": row["price"], "skill": row["skill"], "cooldown": row["cooldown_s"], "mats": row["mats"],
                 "net": row["net"], "per_week": row["per_week"], "sold": row["sold"], "can_make": can["rank"],
                 "name": row["item"]},
    }


def render(base: dict, now_ms: int) -> dict:
    """The Cooldowns page at a moment (SPEC §3.4F.6, CD20): the staged payload with the clock applied.

    Args:
        base: From stage().
        now_ms: The request's time, UTC ms.

    Returns:
        {"header", "professions" ([value, label] for the filter), "rows" (default order), "views", "help"}.
    """
    rows = [_cells(row, now_ms) for row in base["rows"]]
    return {"header": f"{len(rows)} recipes · {base['characters']} characters",
            "professions": [["", "All professions"], *[[p, p] for p in PROFESSIONS]], "rows": rows,
            "views": base["views"], "help": COOLDOWNS_HELP}
