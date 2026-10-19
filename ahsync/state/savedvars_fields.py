"""Field converters for reading the AHProfit SavedVariables (SPEC §2.2.1), shared by savedvars' sections.

Each converter checks one Lua value and returns it typed, or raises _RowError so the caller sets
the row aside (SPEC §5.2 rule 6). Parse only.
"""
from __future__ import annotations

import re
from typing import Callable

from ahsync.state.luadata import LuaTable


class _RowError(ValueError):
    """One row or field doesn't fit the layout; the caller sets it aside."""


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
    """Check an action id from PortfolioPlan.lua.

    Args:
        value: The Lua value, a string or an integer.
        what: Field description for the message.

    Returns:
        The id as text.
    """
    # Action ids are text ("<market>:<run ms>:<n>", ahsync/export/plan_lua.py); numbers are kept as text.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(_int(value, what))
    return _str(value, what)


def _results(value: object, what: str) -> tuple[tuple[int, int], ...]:
    """A disenchant's results, "key,qty;key,qty" (SPEC §2.2.1 disenchant).

    Args:
        value: The field.
        what: Its name, for errors.

    Returns:
        (key, units) pairs by key, units summed per key.

    Raises:
        _RowError: Not a string of positive "key,qty" pairs.
    """
    units: dict[int, int] = {}
    for part in _str(value, what).split(";"):
        key, _, qty = part.partition(",")
        try:
            k, n = int(key), int(qty)
        except ValueError:
            raise _RowError(f"bad {what}: {value!r}") from None
        if n <= 0:
            raise _RowError(f"bad {what}: {value!r}")
        units[k] = units.get(k, 0) + n
    return tuple(sorted(units.items()))
