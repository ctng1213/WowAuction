"""Build AHProfit SavedVariables files for ingest tests (SPEC §2.2.1).

* ``savedvars_text`` writes an AHProfitDB table as Lua, the way WoW does.
* ``generate_sessions`` makes a seeded series of files, one per play session,
  from one evolving addon state: snapshots get newer, attempts are confirmed in
  the next file, old events are pruned, rules change and clear (FI2, SPEC §8.7).
"""
from __future__ import annotations

import copy
import random

T0_MS = 1_760_000_000_000          # times are whole seconds in ms (SPEC §2.2.1)
SESSION_MS = 6 * 3_600_000
REALMS = {"Grommash": "pvp"}       # Cira's realm is deliberately missing (market unknown)
CHARACTERS = {
    "Amaya-Grommash": ("Amaya", "Grommash", "Horde", "MAGE"),
    "Brugan-Grommash": ("Brugan", "Grommash", "Horde", "WARRIOR"),
    "Cira-Elsewhere": ("Cira", "Elsewhere", "Alliance", "ROGUE"),
}
ITEMS = (2589, 2592, 2840, 4306, 6217, 10940, -6585)   # -6585: a random-suffix item with v unknown
RECIPES = {   # spell -> (profession, definition, first session it is seen)
    2663: ("Blacksmithing", {"product": 6217, "yieldMin": 1, "yieldMax": 1, "reagents": "2840,2"}, 0),
    2660: ("Blacksmithing", {"product": 2862, "yieldMin": 1, "yieldMax": 1, "skill": 1, "cooldown": 0,
                             "reagents": "2835,1"}, 1),
    7418: ("Enchanting", {"yieldMin": 1, "yieldMax": 1, "reagents": "10940,1"}, 0),
    7421: ("Enchanting", {"product": 6218, "yieldMin": 1, "yieldMax": 1, "reagents": "6217,1;10940,1"}, 2),
}


def _lua_key(key: object) -> str:
    """Format a table key.

    Args:
        key: A string or integer key.

    Returns:
        '["name"]' or '[12]'.
    """
    return f"[{key}]" if isinstance(key, int) else f'["{key}"]'


def lua_value(value: object, indent: int = 1) -> str:
    """Format a Python value as Lua, like WoW's SavedVariables writer.

    Args:
        value: bool, int, float, str, list (array) or dict (table); None entries are left out.
        indent: Tab depth of the value's fields.

    Returns:
        Lua source.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    pad = "\t" * indent
    if isinstance(value, list):
        body = "".join(f"{pad}{lua_value(v, indent + 1)},\n" for v in value)
    else:
        items = sorted(value.items(), key=lambda kv: (isinstance(kv[0], str), str(kv[0]) if isinstance(kv[0], str)
                                                      else kv[0]))
        body = "".join(f"{pad}{_lua_key(k)} = {lua_value(v, indent + 1)},\n" for k, v in items if v is not None)
    return "{\n" + body + "\t" * (indent - 1) + "}"


def savedvars_text(db: dict) -> str:
    """Write a whole SavedVariables file.

    Args:
        db: The AHProfitDB table; formatVersion defaults to 1.

    Returns:
        File text.
    """
    return "AHProfitDB = " + lua_value({"formatVersion": 1} | db) + "\n"


def _rows(rng: random.Random, width_fn) -> str:
    """Make a compact row string of random items.

    Args:
        rng: Seeded generator.
        width_fn: (key) -> the row's fields after the key, as a list of ints.

    Returns:
        "key,f1,f2;..." with distinct keys (possibly empty).
    """
    keys = rng.sample(ITEMS, rng.randint(0, 4))
    return ";".join(",".join(str(f) for f in [key, *width_fn(key)]) for key in sorted(keys))


class _Session:
    """The addon's state as it evolves over sessions."""

    def __init__(self, rng: random.Random) -> None:
        """Start with characters but nothing captured.

        Args:
            rng: Seeded generator.
        """
        self.rng = rng
        self.db: dict = {"addonVersion": "0.1.0", "characters": {}, "professions": {}, "recipes": {},
                         "knownRecipes": {}, "inventory": {}, "ownAuctions": {}, "scans": [], "events": [],
                         "rules": {}, "vendorReagents": ""}
        for char_id, (name, realm, faction, cls) in CHARACTERS.items():
            self.db["characters"][char_id] = {"name": name, "realm": realm, "faction": faction, "class": cls,
                                              "level": 10, "gold": 1000, "updated": T0_MS}

    def play(self, index: int) -> None:
        """Play one session on a random character.

        Args:
            index: Session number; session times are T0 + index × 6 h.
        """
        rng, t = self.rng, T0_MS + index * SESSION_MS
        char_id = rng.choice(sorted(CHARACTERS))
        character = self.db["characters"][char_id]
        character.update(gold=character["gold"] + rng.randint(1, 500), updated=t,
                         level=character["level"] + rng.randint(0, 1))
        skills = self.db["professions"].setdefault(char_id, {"Blacksmithing": {"skill": 1, "max": 75}})
        skills["Blacksmithing"]["skill"] = min(75, skills["Blacksmithing"]["skill"] + rng.randint(0, 5))
        for spell, (profession, definition, first) in RECIPES.items():
            if index >= first:
                known = index > first or "skill" not in definition   # first seen without skill/cooldown
                self.db["recipes"].setdefault(profession, {})[spell] = {
                    k: v for k, v in definition.items() if known or k not in ("skill", "cooldown")}
        known = {int(s) for s in self.db["knownRecipes"].get(char_id, "").split(";") if s}
        known.add(rng.choice(sorted(RECIPES)))
        self.db["knownRecipes"][char_id] = ";".join(str(s) for s in sorted(known))
        vendor = {int(k) for k in self.db["vendorReagents"].split(";") if k} | {rng.choice((2320, 2321, 2324))}
        self.db["vendorReagents"] = ";".join(str(k) for k in sorted(vendor))
        self._snapshots(char_id, t)
        self._events(char_id, t, index)
        self._rules(t)

    def _snapshots(self, char_id: str, t: int) -> None:
        """Capture bags (always), bank and mail (sometimes), own auctions and a scan (sometimes).

        Args:
            char_id: The character played.
            t: Session start, ms.
        """
        rng = self.rng
        inventory = self.db["inventory"].setdefault(char_id, {})
        for offset, place in enumerate(("bags", "bank", "mail")):
            if place == "bags" or rng.random() < 0.5:
                inventory[place] = _rows(rng, lambda key: [rng.randint(1, 20), rng.randint(0, 1)])
                inventory[place + "At"] = t + offset * 1000
        if rng.random() < 0.5:
            rows = _rows(rng, lambda key: [rng.randint(10, 99), 5, rng.randint(1, 4), rng.randint(1, 3)])
            self.db["ownAuctions"][char_id] = {"at": t + 5000, "rows": rows}
        if rng.random() < 0.5 and CHARACTERS[char_id][1] in REALMS:
            rows = _rows(rng, lambda key: [rng.randint(10, 99), 20, rng.randint(1, 4), rng.randint(1, 9)])
            self.db["scans"].append({"id": f"{char_id}:{t + 6000}:0", "char": char_id, "at": t + 6000,
                                     "kind": rng.choice(("full", "partial")), "coverage": rng.choice((1, 0.5)),
                                     "rows": rows})

    def _events(self, char_id: str, t: int, index: int) -> None:
        """Confirm the last session's attempts, prune old events, then capture a few new ones.

        Args:
            char_id: The character played.
            t: Session start, ms.
            index: Session number.
        """
        rng = self.rng
        for event in self.db["events"]:
            if event.get("confirmed") is False and rng.random() < 0.8:
                event["confirmed"] = True              # the same row, updated in place (SPEC §2.2.1)
        cutoff = t - 2 * SESSION_MS                    # the pruning handshake removed older rows (SPEC §2.2)
        self.db["events"] = [e for e in self.db["events"] if e["at"] > cutoff or e.get("confirmed") is False]
        self.db["scans"] = [s for s in self.db["scans"] if s["at"] > cutoff]
        for seq in range(rng.randint(1, 5)):
            at = t + 10_000 + (seq // 2) * 1000         # two events per second, so seq matters
            base = {"id": f"{char_id}:{at}:{seq % 2}", "char": char_id, "at": at}
            self.db["events"].append(base | self._event_fields(index))

    def _event_fields(self, index: int) -> dict:
        """Fields of one random event.

        Args:
            index: Session number (for skip action ids).

        Returns:
            type plus the fields of that type.
        """
        rng, key = self.rng, self.rng.choice(ITEMS)
        kind = rng.choice(("post", "purchase", "craft", "mail_sent", "auction_mail", "skip"))
        if kind == "post":
            return {"type": kind, "key": key, "stack": 5, "price": rng.randint(10, 99), "durationH": 24,
                    "deposit": 60, "confirmed": rng.random() < 0.6}
        if kind == "purchase":
            return {"type": kind, "key": key, "qty": 5, "price": rng.randint(10, 99),
                    "source": rng.choice(("ah", "vendor")), "confirmed": rng.random() < 0.6}
        if kind == "craft":
            return {"type": kind, "spell": rng.choice(sorted(RECIPES)), "count": rng.randint(1, 3)}
        if kind == "mail_sent":
            return {"type": kind, "to": "Brugan-Grommash", "key": key, "qty": 3}
        if kind == "auction_mail":
            outcome = rng.choice(("sold", "expired", "cancelled", "won"))
            price = rng.randint(10, 99) if outcome in ("sold", "won") else None
            return {"type": kind, "outcome": outcome, "key": key, "qty": 5, "price": price}
        return {"type": kind, "action": f"act-{index}"}

    def _rules(self, t: int) -> None:
        """Set or clear a rule on a random item, sometimes.

        Args:
            t: Session start, ms.
        """
        if self.rng.random() < 0.6:
            key = self.rng.choice(ITEMS[:4])
            self.db["rules"][key] = {"rule": self.rng.choice(("no_buy", "ignore", "keep", "none")),
                                     "setAt": t + 20_000}


def generate_sessions(seed: int, count: int = 4) -> list[str]:
    """Make a series of SavedVariables files from one evolving addon state.

    Args:
        seed: Seed for random.Random; the same seed gives the same files.
        count: Number of sessions (files).

    Returns:
        File texts, oldest first.
    """
    session = _Session(random.Random(seed))
    files = []
    for index in range(count):
        session.play(index)
        files.append(savedvars_text(copy.deepcopy(session.db)))
    return files
