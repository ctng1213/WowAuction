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

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Re-exported so callers need only this module.
from ahsync.state.diagnostics import Diagnostics, read_diagnostics  # noqa: F401
from ahsync.state.luadata import LuaTable, SavedVarsError, SavedVarsParseError, parse_lua  # noqa: F401
from ahsync.state.savedvars_fields import (_action_id, _bool, _compact_rows, _each, _int, _one_of, _opt_int,
                                          _results, _RowError, _str, _table)

SUPPORTED_FORMAT_VERSION = 1
GLOBAL_NAME = "AHProfitDB"
LOCATIONS = ("bags", "bank", "mail")          # SPEC §3.2 inventory.location
SCAN_KINDS = ("full", "partial")
RULES = ("no_buy", "ignore", "keep", "none")  # SPEC §3.4.2
RESERVE_ALL = -1                              # SPEC §3.4.3: a reserve that keeps every unit
PURCHASE_SOURCES = ("ah", "vendor")
MAIL_OUTCOMES = ("sold", "expired", "cancelled", "won")
GATHER_SOURCES = ("mining", "herbalism", "skinning", "fishing", "monster")   # SPEC §2.2.1 gather


# ---------------------------------------------------------------- errors

class UnsupportedFormatError(SavedVarsError):
    """The file's formatVersion is newer than this AHSync understands (FM13: "Update AHSync")."""


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
    ends: tuple[tuple[int, int, int], ...] = ()   # (key, stack, end ms) per auction (S16); () from an older addon


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
    money_copper: int | None = None
    results: tuple[tuple[int, int], ...] | None = None     # disenchant: (key, units) by key
    login_ms: int | None = None                            # gather: the character's initial login, ms
    loot: tuple[tuple[int, int], ...] | None = None        # gather: (key, units) by key


@dataclass(frozen=True, slots=True)
class ItemName:
    """An item's name and quality as the addon read them from its link (SPEC §2.2.1 itemNames)."""

    key: int
    name: str
    quality: int          # 0 poor ... 7


@dataclass(frozen=True, slots=True)
class ItemRule:
    """An item rule set in game (SPEC §3.4.2); "none" is a cleared rule."""

    key: int
    rule: str
    set_at_ms: int


@dataclass(frozen=True, slots=True)
class Reserves:
    """One character's reserves: items kept for yourself (SPEC §3.4.3)."""

    char_id: str
    items: tuple[tuple[int, int], ...]     # (item key, units kept; RESERVE_ALL keeps all), by key
    times: tuple[tuple[int, int], ...] | None = None   # (item key, set or cleared at ms), by key; None: an older addon


@dataclass(frozen=True, slots=True)
class RejectedRow:
    """A scan or event set aside by the parser, as far as it can be identified (SPEC §2.2 pruning handshake).

    AHSync keeps that character's ingestedUntil below it, so the addon keeps the row.
    """

    char_id: str | None   # None when the row has no readable "char"
    at_ms: int | None     # None when the row has no readable integral "at"


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
    rejected: tuple[RejectedRow, ...] = ()   # scans and events set aside, oldest first per section
    item_names: tuple[ItemName, ...] = ()    # names of items the addon has seen in your inventory
    reserves: tuple[Reserves, ...] | None = None   # by character; None when the file has no reserves table
    reserves_set_aside: tuple[str, ...] = ()       # characters whose reserves were malformed


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
        entry: { at, rows, ends }; ends "key,units,end ms,unit price;…" (the price may be missing).

    Returns:
        One OwnAuctionSnapshot.
    """
    entry = _table(entry, "ownAuctions")
    ends = []
    for part in (entry.get("ends") or "").split(";") if isinstance(entry.get("ends"), str) else ():
        fields = part.split(",")
        if len(fields) >= 3 and all(f.lstrip("-").isdigit() for f in fields[:3]):
            ends.append((int(fields[0]), int(fields[1]), int(fields[2])))
    return [OwnAuctionSnapshot(_str(char_id, "character id"), _int(entry.get("at"), "at"),
                               _auction_rows(entry.get("rows", ""), "rows"), tuple(sorted(ends)))]


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
    "money": ("money_copper", _int),
    "results": ("results", _results),
    "login": ("login_ms", _int),
    "loot": ("loot", _results),
}
# Fields whose meaning depends on the event type: a gather event's source is what opened the loot window.
_TYPE_FIELDS: dict[str, dict[str, tuple[str, Callable[[object, str], object]]]] = {
    "gather": {"source": ("source", _one_of(GATHER_SOURCES))},
}

# Event type -> (required fields, optional fields).
_EVENT_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "post": (("key", "stack", "price", "durationH", "deposit", "confirmed"), ()),
    "purchase": (("key", "qty", "price", "source", "confirmed"), ()),
    "craft": (("spell", "count"), ()),
    "mail_sent": (("to", "key", "qty"), ("confirmed",)),   # confirmed since A75; missing = confirmed
    "auction_mail": (("outcome", "key", "qty"), ("price",)),
    "skip": (("action",), ()),
    "payout": (("key", "qty", "money"), ("deposit",)),
    "disenchant": (("key", "results"), ()),
    "gather": (("source", "login", "loot"), ()),
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
        attribute, convert = _TYPE_FIELDS.get(event_type, {}).get(name) or _EVENT_FIELDS[name]
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


def _reserves(char_id: object, value: object) -> list[Reserves]:
    """Convert one character's reserves (SPEC §2.2.1, §3.4.3).

    Args:
        char_id: Its key, "Name-Realm".
        value: "key,qty;key,qty", qty -1 for all; "" for none.

    Returns:
        One Reserves.

    Raises:
        _RowError: When a row is malformed, a quantity is below 1 (other than -1) or a key repeats.
    """
    rows = _compact_rows(value, 2, "reserves")
    if any(qty < 1 and qty != RESERVE_ALL for _, qty in rows) or len({k for k, _ in rows}) != len(rows):
        raise _RowError(f"reserves {value!r}: each item once, keeping at least 1 unit or -1 (all)")
    return [Reserves(_str(char_id, "character id"), tuple(sorted(rows)))]


def _item_name(key: object, value: object) -> list[ItemName]:
    """Convert one itemNames entry.

    Args:
        key: Its key, the item key.
        value: "quality,name"; the name may itself contain commas.

    Returns:
        One ItemName.

    Raises:
        _RowError: When the quality isn't 0-7 or the name is empty.
    """
    quality, _, name = _str(value, "item name").partition(",")
    if not quality.isdigit() or not 0 <= int(quality) <= 7 or not name:
        raise _RowError(f"item name {value!r} is not \"quality,name\"")
    return [ItemName(_int(key, "item key"), name, int(quality))]


def _rejected_row(row: object) -> RejectedRow:
    """Identify a scan or event row that failed to convert.

    Args:
        row: Its raw Lua value.

    Returns:
        Its character and time, each None when not readable.
    """
    if not isinstance(row, dict):
        return RejectedRow(None, None)
    char, at = row.get("char"), row.get("at")
    readable_at = isinstance(at, (int, float)) and not isinstance(at, bool) and float(at).is_integer()
    return RejectedRow(char if isinstance(char, str) else None, int(at) if readable_at else None)


def _tracking(convert: Callable[[object, object], list],
              rejected: list[RejectedRow]) -> Callable[[object, object], list]:
    """Wrap a scan or event converter so the rows it rejects are also recorded as RejectedRow.

    Args:
        convert: (index, row) -> records; raises _RowError when the row doesn't fit.
        rejected: Rejected rows, appended to.

    Returns:
        The wrapped converter.
    """
    def run(index: object, row: object) -> list:
        """Convert one row, recording it when it is rejected.

        Args:
            index: Its array index.
            row: The raw row.

        Returns:
            Its records.
        """
        try:
            return convert(index, row)
        except _RowError:
            rejected.append(_rejected_row(row))
            raise
    return run


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
    rejected: list[RejectedRow] = []
    version, addon_version, refresh_ms = _header(db, problems)
    item_names = tuple(_each(db.get("itemNames"), "itemNames", problems, _item_name))
    reserves_problems: list[str] = []
    reserves = _each(db.get("reserves"), "reserves", reserves_problems, _reserves)
    problems.extend(reserves_problems)
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
        scans=tuple(_each(db.get("scans"), "scans", problems, _tracking(_scan, rejected))),
        events=tuple(_each(db.get("events"), "events", problems, _tracking(_event, rejected))),
        rules=tuple(_each(db.get("rules"), "rules", problems, _rule)),
        problems=tuple(problems),
        diagnostics=read_diagnostics(db.get("diagnostics")),
        rejected=tuple(rejected),
        item_names=item_names,
        reserves=None if db.get("reserves") is None else tuple(_with_times(reserves, db.get("reserveTimes"), problems)),
        reserves_set_aside=_reserves_set_aside(db.get("reserves"), reserves),
    )


def _with_times(reserves: list[Reserves], table: object, problems: list[str]) -> list[Reserves]:
    """Attach each character's reserve times (SPEC §3.4.3, S15); a character listed only there gets an empty entry.

    Args:
        reserves: The parsed reserves.
        table: AHProfitDB.reserveTimes, or None from an older addon.
        problems: Receives one message per malformed entry (its character then keeps no times).

    Returns:
        The reserves with times, by character id.
    """
    if not isinstance(table, dict):
        return reserves
    by_char = {r.char_id: r for r in reserves}
    out = []
    for char_id in sorted(set(by_char) | {c for c in table if isinstance(c, str)}):
        times: tuple | None = None
        if char_id in table:
            try:
                times = tuple(sorted(dict(_compact_rows(table[char_id], 2, "reserveTimes")).items()))
            except _RowError as error:
                problems.append(f"reserveTimes[{char_id!r}]: {error}")
        base = by_char.get(char_id, Reserves(char_id, ()))
        out.append(Reserves(base.char_id, base.items, times))
    return out


def _reserves_set_aside(table: object, kept: list[Reserves]) -> tuple[str, ...]:
    """The characters whose reserves entry was set aside, so the importer leaves their stored ones alone.

    Args:
        table: AHProfitDB.reserves.
        kept: The reserves that parsed.

    Returns:
        Character ids, sorted; () when the table is absent or not a table.
    """
    if not isinstance(table, dict):
        return ()
    good = {r.char_id for r in kept}
    return tuple(sorted(str(c) for c in table if str(c) not in good))


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
