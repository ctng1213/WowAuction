"""Read the AHProfit addon's SavedVariables file into records (SPEC §2.2, §2.2.1).

Two layers:

* ``luadata.parse_lua`` parses the Lua subset WoW writes to SavedVariables
  files (re-exported here with its errors).
* ``read_ahprofit`` converts the ``AHProfitDB`` global (format version 1) into
  dataclasses. A row that doesn't fit the layout is set aside with a message in
  ``AHProfitData.problems`` and everything else carries on (SPEC §5.2 rule 6).

Parse only: storing the records is the database layer's job.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Re-exported so callers need only this module.
from ahsync.ingest.luadata import LuaTable, SavedVarsError, SavedVarsParseError, parse_lua  # noqa: F401

SUPPORTED_FORMAT_VERSION = 1
GLOBAL_NAME = "AHProfitDB"
LOCATIONS = ("bags", "bank", "mail")          # SPEC §3.2 inventory.location
SCAN_KINDS = ("full", "partial")
RULES = ("no_buy", "ignore", "keep", "none")  # SPEC §3.4.2
PURCHASE_SOURCES = ("ah", "vendor")
MAIL_OUTCOMES = ("sold", "expired", "cancelled", "won")


# ---------------------------------------------------------------- errors

class UnsupportedFormatError(SavedVarsError):
    """The file's formatVersion is newer than this AHSync understands (FM13: "Update AHSync")."""


class _RowError(ValueError):
    """One row or field doesn't fit the layout; the caller sets it aside."""


# ---------------------------------------------------------------- records

@dataclass(frozen=True, slots=True)
class Character:
    """One character (SPEC §2.2.1 characters; §3.2 characters)."""

    char_id: str          # "Name-Realm"
    name: str
    realm: str
    faction: str          # "Horde" | "Alliance"
    class_token: str      # WoW class file token, e.g. "MAGE"
    level: int
    gold_copper: int
    updated_ms: int


@dataclass(frozen=True, slots=True)
class Profession:
    """A character's skill in one profession."""

    char_id: str
    profession: str       # enUS client name, e.g. "Enchanting"
    skill: int
    max_skill: int


@dataclass(frozen=True, slots=True)
class Reagent:
    """One reagent of a recipe."""

    key: int              # AHF item key
    count: int


@dataclass(frozen=True, slots=True)
class Recipe:
    """An account-wide recipe definition (SPEC §2.2.1 recipes)."""

    profession: str
    spell_id: int
    product_key: int | None   # None for enchants, which make no item
    yield_min: int
    yield_max: int
    skill_req: int | None     # None = unknown (no Classic API; omitted in the file)
    cooldown_s: int | None    # None = unknown; 0 = no cooldown
    reagents: tuple[Reagent, ...]


@dataclass(frozen=True, slots=True)
class KnownRecipe:
    """A recipe a character knows."""

    char_id: str
    spell_id: int


@dataclass(frozen=True, slots=True)
class InventoryRow:
    """Units of one item in one place, summed per bound flag."""

    char_id: str
    location: str         # "bags" | "bank" | "mail"
    key: int
    qty: int
    bound: bool


@dataclass(frozen=True, slots=True)
class InventoryLocation:
    """The latest complete snapshot of one place of one character (SPEC §2.2.1).

    A place never visited has no InventoryLocation at all, which is different
    from a visited, empty place (no rows).
    """

    char_id: str
    location: str         # "bags" | "bank" | "mail"
    at_ms: int            # when this place was captured
    rows: tuple[InventoryRow, ...]


@dataclass(frozen=True, slots=True)
class AuctionRow:
    """One group of a character's own auctions: "key,price,stack,band,count"."""

    key: int
    price_copper: int     # per unit
    stack: int
    band: int             # 1 short … 4 very long
    count: int


@dataclass(frozen=True, slots=True)
class OwnAuctionSnapshot:
    """A character's own auctions as last seen."""

    char_id: str
    at_ms: int
    rows: tuple[AuctionRow, ...]


@dataclass(frozen=True, slots=True)
class Scan:
    """One in-game AH scan with its order book (SPEC §2.2.1 scans)."""

    scan_id: str
    char_id: str
    at_ms: int
    kind: str             # "full" | "partial"
    coverage: float       # 0..1
    entries: tuple[AuctionRow, ...]


@dataclass(frozen=True, slots=True)
class Event:
    """One captured event (SPEC §2.2.1 event table); fields not used by its type are None."""

    event_id: str
    type: str
    char_id: str
    at_ms: int
    key: int | None = None
    qty: int | None = None
    stack: int | None = None
    price_copper: int | None = None
    duration_h: int | None = None
    deposit_copper: int | None = None
    confirmed: bool | None = None
    source: str | None = None
    spell_id: int | None = None
    count: int | None = None
    to_char_id: str | None = None
    outcome: str | None = None
    action_id: str | None = None


@dataclass(frozen=True, slots=True)
class ItemRule:
    """An item rule set in game (SPEC §3.4.2); "none" is a cleared rule."""

    key: int
    rule: str
    set_at_ms: int


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """What the addon saw last session, to explain missing captures (SPEC §2.2.1, §6).

    Not ingested into the database; read by tools/savedvars_report.py.
    """

    at_ms: int | None
    client: str | None                     # e.g. "1.60.1 70009"
    apis: tuple[tuple[str, str], ...]      # (API name, Lua type: "function", "table", "nil", ...)
    errors: tuple[str, ...]                # handler errors caught this session, oldest first
    skill_lines: str | None                # raw Skills-tab rows "name|isHeader|isExpanded|rank|max;..."
    merchant: str | None                   # raw last-merchant rows "itemKey|numAvailable|extendedCost;..."
    setup_error: str | None                # why AHProfitDB setup failed, if it did
    namespaces: tuple[tuple[str, tuple[str, ...] | None], ...] = ()   # (namespace, its function names; None = missing)
    professions: str | None = None         # raw GetProfessions slots "slot|index|name|rank|max;..."
    trade_skill: str | None = None         # raw last trade skill window "name=..|ids=..|learned=..|first=.."


@dataclass(frozen=True, slots=True)
class AHProfitData:
    """Everything in one AHProfitDB, in a deterministic order.

    Characters, professions, recipes, known recipes, inventories, own auctions
    and rules are sorted by their keys; scans and events keep file order (oldest
    first). ``problems`` lists every row that was set aside, with why.
    """

    format_version: int
    addon_version: str | None
    refresh_requested_at_ms: int | None
    characters: tuple[Character, ...]
    professions: tuple[Profession, ...]
    recipes: tuple[Recipe, ...]
    vendor_reagents: tuple[int, ...]
    known_recipes: tuple[KnownRecipe, ...]
    inventories: tuple[InventoryLocation, ...]
    own_auctions: tuple[OwnAuctionSnapshot, ...]
    scans: tuple[Scan, ...]
    events: tuple[Event, ...]
    rules: tuple[ItemRule, ...]
    problems: tuple[str, ...]
    diagnostics: Diagnostics | None = None


# ---------------------------------------------------------------- item keys

def is_unknown_suffix(key: int) -> bool:
    """Whether an item key stands for some random-suffix variant with `v` unknown (SPEC §2.2.1).

    The addon writes such items as ``-itemId`` until the link -> `v` mapping is
    known (§6 item 11). Callers store them but leave them out of valuation.

    Args:
        key: An item key from the file.

    Returns:
        True for a negative key.
    """
    return key < 0


def base_item_id(key: int) -> int:
    """The base item id of any item key (SPEC §2.1).

    Args:
        key: An AHF item key, or -itemId for an unknown-suffix item.

    Returns:
        The item id.
    """
    return -key if key < 0 else key % 2147483648


# ---------------------------------------------------------------- field helpers

def _int(value: object, what: str) -> int:
    """Check an integer field.

    Args:
        value: The Lua value.
        what: Field description for the message.

    Returns:
        The integer.

    Raises:
        _RowError: When the value is not an integral number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _RowError(f"{what} is not a number: {value!r}")
    if isinstance(value, float):
        if not value.is_integer():
            raise _RowError(f"{what} is not an integer: {value!r}")
        return int(value)
    return value


def _opt_int(value: object, what: str) -> int | None:
    """Check an optional integer field.

    Args:
        value: The Lua value, or None when absent.
        what: Field description for the message.

    Returns:
        The integer, or None when absent.
    """
    return None if value is None else _int(value, what)


def _str(value: object, what: str, allowed: tuple[str, ...] = ()) -> str:
    """Check a string field.

    Args:
        value: The Lua value.
        what: Field description for the message.
        allowed: Permitted values; empty means any string.

    Returns:
        The string.

    Raises:
        _RowError: When it is not a string or not one of the allowed values.
    """
    if not isinstance(value, str):
        raise _RowError(f"{what} is not a string: {value!r}")
    if allowed and value not in allowed:
        raise _RowError(f"{what} is {value!r}, expected one of {', '.join(allowed)}")
    return value


def _table(value: object, what: str) -> LuaTable:
    """Check a table field.

    Args:
        value: The Lua value.
        what: Field description for the message.

    Returns:
        The table.

    Raises:
        _RowError: When it is not a table.
    """
    if not isinstance(value, dict):
        raise _RowError(f"{what} is not a table: {value!r}")
    return value


def _compact_rows(text: object, width: int, what: str) -> list[tuple[int, ...]]:
    """Decode a compact string: rows split by ";", fields by "," (SPEC §2.2.1).

    Args:
        text: The Lua value, e.g. "2840,20,0;6948,1,1"; "" means no rows.
        width: Fields per row.
        what: Field description for the message.

    Returns:
        One tuple of integers per row, in order.

    Raises:
        _RowError: When it is not a string or a row is malformed.
    """
    rows = []
    for row in _str(text, what).split(";") if text != "" else []:
        fields = row.split(",")
        if len(fields) != width or not all(re.fullmatch(r"-?\d+", f) for f in fields):
            raise _RowError(f"{what}: bad row {row!r} (expected {width} integers)")
        rows.append(tuple(int(f) for f in fields))
    return rows


def _key_order(key: object) -> tuple[int, float, str]:
    """Sort key for Lua table keys: booleans, then numbers ascending, then strings ascending.

    Args:
        key: A Lua table key (bool, int, float or str).

    Returns:
        A tuple that orders keys deterministically.
    """
    if isinstance(key, bool):
        return (0, float(key), "")
    if isinstance(key, (int, float)):
        return (1, float(key), "")
    return (2, 0.0, str(key))


def _sorted_items(table: LuaTable) -> list[tuple[object, object]]:
    """List a Lua table's items in a deterministic key order.

    Args:
        table: Any Lua table.

    Returns:
        (key, value) pairs ordered by _key_order.
    """
    return sorted(table.items(), key=lambda item: _key_order(item[0]))


def _each(table: object, what: str, problems: list[str],
          convert: Callable[[object, object], list]) -> list:
    """Convert every item of a section, setting failing items aside.

    Args:
        table: The section's Lua table (absent sections are skipped silently).
        what: Section name for messages.
        problems: Problem list, appended to.
        convert: (key, value) -> records for that item; raises _RowError when it doesn't fit.

    Returns:
        All records, in key order.
    """
    if table is None:
        return []
    if not isinstance(table, dict):
        problems.append(f"{what}: not a table")
        return []
    records = []
    for key, value in _sorted_items(table):
        try:
            records.extend(convert(key, value))
        except _RowError as exc:
            problems.append(f"{what}[{key!r}]: {exc}")
    return records


def _bool(value: object, what: str) -> bool:
    """Check a boolean field.

    Args:
        value: The Lua value.
        what: Field description for the message.

    Returns:
        The boolean.

    Raises:
        _RowError: When it is not a boolean.
    """
    if not isinstance(value, bool):
        raise _RowError(f"{what} is not a boolean: {value!r}")
    return value


def _one_of(allowed: tuple[str, ...]) -> Callable[[object, str], str]:
    """Make a string checker restricted to some values.

    Args:
        allowed: Permitted values.

    Returns:
        A converter (value, what) -> str that raises _RowError otherwise.
    """
    def check(value: object, what: str) -> str:
        """Check the value against the allowed strings.

        Args:
            value: The Lua value.
            what: Field description for the message.

        Returns:
            The string.
        """
        return _str(value, what, allowed)
    return check


def _action_id(value: object, what: str) -> str:
    """Check an action id from Data.lua.

    Args:
        value: The Lua value, a string or an integer.
        what: Field description for the message.

    Returns:
        The id as text.
    """
    # SPEC GAP: the type of a Data.lua action id is not defined yet; numbers are kept as text.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(_int(value, what))
    return _str(value, what)


# ---------------------------------------------------------------- section converters

def _character(char_id: object, row: object) -> list[Character]:
    """Convert one characters entry.

    Args:
        char_id: Its key, "Name-Realm".
        row: Its table.

    Returns:
        One Character.
    """
    row = _table(row, "character")
    return [Character(
        char_id=_str(char_id, "character id"),
        name=_str(row.get("name"), "name"),
        realm=_str(row.get("realm"), "realm"),
        faction=_str(row.get("faction"), "faction"),
        class_token=_str(row.get("class"), "class"),
        level=_int(row.get("level"), "level"),
        gold_copper=_int(row.get("gold"), "gold"),
        updated_ms=_int(row.get("updated"), "updated"),
    )]


def _profession(char_id: str, name: object, row: object) -> list[Profession]:
    """Convert one profession of one character.

    Args:
        char_id: "Name-Realm".
        name: The profession name.
        row: { skill, max }.

    Returns:
        One Profession.
    """
    row = _table(row, "profession")
    return [Profession(char_id, _str(name, "profession"), _int(row.get("skill"), "skill"),
                       _int(row.get("max"), "max"))]


def _professions(char_id: object, skills: object, problems: list[str]) -> list[Profession]:
    """Convert one character's professions; a bad profession row is set aside alone.

    Args:
        char_id: Its key, "Name-Realm".
        skills: Profession name -> { skill, max }.
        problems: Problem list, appended to.

    Returns:
        One Profession per good profession, by name.
    """
    char_id = _str(char_id, "character id")
    return _each(_table(skills, "professions"), f"professions[{char_id!r}]", problems,
                 lambda name, row: _profession(char_id, name, row))


def _recipe(profession: str, spell_id: object, row: object) -> list[Recipe]:
    """Convert one recipe definition.

    Args:
        profession: The profession name.
        spell_id: Its key, the spell id.
        row: { product, yieldMin, yieldMax, skill, cooldown, reagents }; skill and
            cooldown are omitted when unknown.

    Returns:
        One Recipe.
    """
    row = _table(row, "recipe")
    reagents = _compact_rows(row.get("reagents", ""), 2, "reagents")
    return [Recipe(
        profession=profession,
        spell_id=_int(spell_id, "spell id"),
        product_key=_opt_int(row.get("product"), "product"),
        yield_min=_int(row.get("yieldMin"), "yieldMin"),
        yield_max=_int(row.get("yieldMax"), "yieldMax"),
        skill_req=_opt_int(row.get("skill"), "skill"),
        cooldown_s=_opt_int(row.get("cooldown"), "cooldown"),
        reagents=tuple(Reagent(key, count) for key, count in reagents),
    )]


def _recipes(profession: object, definitions: object, problems: list[str]) -> list[Recipe]:
    """Convert one profession's recipe definitions; a bad recipe is set aside alone.

    Args:
        profession: Its key, the profession name.
        definitions: Spell id -> definition table.
        problems: Problem list, appended to.

    Returns:
        One Recipe per good spell id, ascending.
    """
    profession = _str(profession, "profession")
    return _each(_table(definitions, "recipes"), f"recipes[{profession!r}]", problems,
                 lambda spell_id, row: _recipe(profession, spell_id, row))


def _known_recipes(char_id: object, spells: object) -> list[KnownRecipe]:
    """Convert one character's known recipes.

    Args:
        char_id: Its key, "Name-Realm".
        spells: Compact string of spell ids, e.g. "2662;2663".

    Returns:
        One KnownRecipe per spell id, ascending.
    """
    char_id = _str(char_id, "character id")
    return [KnownRecipe(char_id, spell) for (spell,) in sorted(_compact_rows(spells, 1, "spell ids"))]


_TIME_FIELDS = {"bags": "bagsAt", "bank": "bankAt", "mail": "mailAt"}


def _inventory_location(char_id: str, location: str, entry: LuaTable) -> list[InventoryLocation]:
    """Convert one place of a character's inventory, all or nothing.

    Args:
        char_id: "Name-Realm".
        location: "bags", "bank" or "mail".
        entry: The character's inventory table.

    Returns:
        One InventoryLocation, or none when the place was never visited.
    """
    if location not in entry:
        return []
    rows = tuple(InventoryRow(char_id, location, key, qty, bool(bound))
                 for key, qty, bound in _compact_rows(entry[location], 3, location))
    time_field = _TIME_FIELDS[location]
    return [InventoryLocation(char_id, location, _int(entry.get(time_field), time_field), rows)]


def _inventory(char_id: object, entry: object, problems: list[str]) -> list[InventoryLocation]:
    """Convert one character's inventory; a bad place is set aside alone.

    Args:
        char_id: Its key, "Name-Realm".
        entry: { bags, bagsAt, bank, bankAt, mail, mailAt }; a missing place was never visited.
        problems: Problem list, appended to.

    Returns:
        One InventoryLocation per good visited place, in the order bags, bank, mail.
    """
    char_id = _str(char_id, "character id")
    entry = _table(entry, "inventory")
    records = []
    for location in LOCATIONS:
        try:
            records += _inventory_location(char_id, location, entry)
        except _RowError as exc:
            problems.append(f"inventory[{char_id!r}].{location}: {exc}")
    return records


def _auction_rows(text: object, what: str) -> tuple[AuctionRow, ...]:
    """Decode "key,price,stack,band,count" rows.

    Args:
        text: The compact string.
        what: Field description for messages.

    Returns:
        The rows, in order.
    """
    return tuple(AuctionRow(*row) for row in _compact_rows(text, 5, what))


def _own_auctions(char_id: object, entry: object) -> list[OwnAuctionSnapshot]:
    """Convert one character's own-auctions snapshot.

    Args:
        char_id: Its key, "Name-Realm".
        entry: { at, rows }.

    Returns:
        One OwnAuctionSnapshot.
    """
    entry = _table(entry, "ownAuctions")
    return [OwnAuctionSnapshot(_str(char_id, "character id"), _int(entry.get("at"), "at"),
                               _auction_rows(entry.get("rows", ""), "rows"))]


def _scan(_index: object, row: object) -> list[Scan]:
    """Convert one scan.

    Args:
        _index: Its array index (unused; file order is kept).
        row: { id, char, at, kind, coverage, rows }.

    Returns:
        One Scan.

    Raises:
        _RowError: When a field is missing or malformed.
    """
    row = _table(row, "scan")
    coverage = row.get("coverage")
    if isinstance(coverage, bool) or not isinstance(coverage, (int, float)) or not 0 <= coverage <= 1:
        raise _RowError(f"coverage is not a number in 0..1: {coverage!r}")
    return [Scan(_str(row.get("id"), "id"), _str(row.get("char"), "char"), _int(row.get("at"), "at"),
                 _str(row.get("kind"), "kind", SCAN_KINDS), float(coverage),
                 _auction_rows(row.get("rows", ""), "rows"))]


# Lua field -> (Event attribute, converter) for the per-type fields (SPEC §2.2.1 event table).
_EVENT_FIELDS: dict[str, tuple[str, Callable[[object, str], object]]] = {
    "key": ("key", _int),
    "qty": ("qty", _int),
    "stack": ("stack", _int),
    "price": ("price_copper", _int),
    "durationH": ("duration_h", _int),
    "deposit": ("deposit_copper", _int),
    "confirmed": ("confirmed", _bool),
    "source": ("source", _one_of(PURCHASE_SOURCES)),
    "spell": ("spell_id", _int),
    "count": ("count", _int),
    "to": ("to_char_id", _str),
    "outcome": ("outcome", _one_of(MAIL_OUTCOMES)),
    "action": ("action_id", _action_id),
}

# Event type -> (required fields, optional fields).
_EVENT_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "post": (("key", "stack", "price", "durationH", "deposit", "confirmed"), ()),
    "purchase": (("key", "qty", "price", "source", "confirmed"), ()),
    "craft": (("spell", "count"), ()),
    "mail_sent": (("to", "key", "qty"), ()),
    "auction_mail": (("outcome", "key", "qty"), ("price",)),
    "skip": (("action",), ()),
}


def _event(_index: object, row: object) -> list[Event]:
    """Convert one event, checking the fields its type needs.

    Args:
        _index: Its array index (unused; file order is kept).
        row: { id, type, char, at, ...fields of its type }.

    Returns:
        One Event.

    Raises:
        _RowError: On an unknown type, or a missing or malformed field.
    """
    row = _table(row, "event")
    event_type = _str(row.get("type"), "type", tuple(_EVENT_TYPES))
    required, optional = _EVENT_TYPES[event_type]
    fields = {}
    for name in required + optional:
        if row.get(name) is None:
            if name in required:
                raise _RowError(f"{event_type} event has no {name}")
            continue
        attribute, convert = _EVENT_FIELDS[name]
        fields[attribute] = convert(row[name], name)
    if event_type == "auction_mail" and fields["outcome"] in ("sold", "won") and "price_copper" not in fields:
        raise _RowError(f"auction_mail {fields['outcome']} has no price")
    return [Event(event_id=_str(row.get("id"), "id"), type=event_type, char_id=_str(row.get("char"), "char"),
                  at_ms=_int(row.get("at"), "at"), **fields)]


def _rule(key: object, row: object) -> list[ItemRule]:
    """Convert one item rule.

    Args:
        key: Its key, the item key.
        row: { rule, setAt }.

    Returns:
        One ItemRule.
    """
    row = _table(row, "rule")
    return [ItemRule(_int(key, "item key"), _str(row.get("rule"), "rule", RULES), _int(row.get("setAt"), "setAt"))]


def _diagnostics(value: object) -> Diagnostics | None:
    """Read the addon's diagnostics block leniently: it exists to explain failures.

    Args:
        value: AHProfitDB.diagnostics, or None when the addon hasn't saved one.

    Returns:
        The diagnostics, with any malformed part read as None or empty; None when absent.
    """
    if not isinstance(value, dict):
        return None

    def text(name: str) -> str | None:
        """Read one string field of the diagnostics block.

        Args:
            name: Field name in AHProfitDB.diagnostics.

        Returns:
            The value when it is a string, else None.
        """
        item = value.get(name)
        return item if isinstance(item, str) else None

    apis = []
    for pair in (text("apis") or "").split(";"):
        name, sep, kind = pair.partition("=")
        if sep:
            apis.append((name, kind))
    raw_errors = value.get("errors")
    if isinstance(raw_errors, dict):
        raw_errors = [raw_errors[k] for k in sorted(k for k in raw_errors if isinstance(k, int))]
    errors = tuple(e for e in raw_errors if isinstance(e, str)) if isinstance(raw_errors, list) else ()
    namespaces = []
    for pair in (text("namespaces") or "").split(";"):
        name, sep, functions = pair.partition("=")
        if sep:
            namespaces.append((name, None if functions == "nil" else tuple(functions.split())))
    at = value.get("at")
    return Diagnostics(
        at_ms=at if isinstance(at, int) and not isinstance(at, bool) else None,
        client=text("client"), apis=tuple(apis), errors=errors,
        skill_lines=text("skillLines"), merchant=text("merchant"), setup_error=text("setupError"),
        namespaces=tuple(namespaces), professions=text("professions"), trade_skill=text("tradeSkill"),
    )


# ---------------------------------------------------------------- entry points

def _header(db: object, problems: list[str]) -> tuple[int, str | None, int | None]:
    """Check AHProfitDB's header fields.

    Args:
        db: The AHProfitDB value.
        problems: Problem list, appended to.

    Returns:
        (format version, addon version or None, refresh request time in ms or None).
        A malformed refreshRequestedAt is reported in problems and read as None.

    Raises:
        SavedVarsError: When AHProfitDB is missing, not a table, or has no usable formatVersion.
        UnsupportedFormatError: When formatVersion is newer than SUPPORTED_FORMAT_VERSION.
    """
    if not isinstance(db, dict):
        raise SavedVarsError(f"{GLOBAL_NAME} is missing or not a table")
    version = db.get("formatVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SavedVarsError(f"{GLOBAL_NAME}.formatVersion is missing or invalid: {version!r}")
    if version > SUPPORTED_FORMAT_VERSION:
        raise UnsupportedFormatError(
            f"{GLOBAL_NAME} is format {version}; this AHSync reads up to {SUPPORTED_FORMAT_VERSION}. Update AHSync.")
    addon_version = db.get("addonVersion")
    try:
        refresh_ms = _opt_int(db.get("refreshRequestedAt"), "refreshRequestedAt")
    except _RowError as exc:
        problems.append(str(exc))
        refresh_ms = None
    return version, addon_version if isinstance(addon_version, str) else None, refresh_ms


def _vendor_reagents(text: object, problems: list[str]) -> tuple[int, ...]:
    """Convert the vendor-reagent key list.

    Args:
        text: Compact string of item keys, or None when absent.
        problems: Problem list, appended to.

    Returns:
        Item keys, ascending; empty when absent or malformed.
    """
    if text is None:
        return ()
    try:
        return tuple(sorted(key for (key,) in _compact_rows(text, 1, "vendorReagents")))
    except _RowError as exc:
        problems.append(str(exc))
        return ()


def read_ahprofit(text: str) -> AHProfitData:
    """Parse an AHProfit SavedVariables file into records (SPEC §2.2.1, format version 1).

    Args:
        text: Contents of WTF/Account/*/SavedVariables/AHProfit.lua.

    Returns:
        The records. Rows that don't fit the layout are left out and described
        in ``problems``.

    Raises:
        SavedVarsParseError: When the file is not valid SavedVariables Lua (e.g. half-written).
        SavedVarsError: When AHProfitDB is missing or has no usable formatVersion.
        UnsupportedFormatError: When the file is from a newer addon (FM13).
    """
    db = parse_lua(text).get(GLOBAL_NAME)
    problems: list[str] = []
    version, addon_version, refresh_ms = _header(db, problems)
    return AHProfitData(
        format_version=version,
        addon_version=addon_version,
        refresh_requested_at_ms=refresh_ms,
        characters=tuple(_each(db.get("characters"), "characters", problems, _character)),
        professions=tuple(_each(db.get("professions"), "professions", problems,
                                     lambda k, v: _professions(k, v, problems))),
        recipes=tuple(_each(db.get("recipes"), "recipes", problems, lambda k, v: _recipes(k, v, problems))),
        vendor_reagents=_vendor_reagents(db.get("vendorReagents"), problems),
        known_recipes=tuple(_each(db.get("knownRecipes"), "knownRecipes", problems, _known_recipes)),
        inventories=tuple(_each(db.get("inventory"), "inventory", problems,
                                     lambda k, v: _inventory(k, v, problems))),
        own_auctions=tuple(_each(db.get("ownAuctions"), "ownAuctions", problems, _own_auctions)),
        scans=tuple(_each(db.get("scans"), "scans", problems, _scan)),
        events=tuple(_each(db.get("events"), "events", problems, _event)),
        rules=tuple(_each(db.get("rules"), "rules", problems, _rule)),
        problems=tuple(problems),
        diagnostics=_diagnostics(db.get("diagnostics")),
    )


def read_ahprofit_file(path: Path) -> AHProfitData:
    """Read and parse an AHProfit SavedVariables file from disk.

    Args:
        path: Path to AHProfit.lua (or WoW's AHProfit.lua.bak fallback, SPEC §5.2).

    Returns:
        The records, as read_ahprofit.

    Raises:
        OSError: When the file can't be read.
        SavedVarsError: As read_ahprofit (its subclasses included).
    """
    return read_ahprofit(path.read_bytes().decode("utf-8", "surrogateescape"))
