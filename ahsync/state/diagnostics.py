"""The addon's diagnostics block in AHProfitDB (SPEC §2.2.1, §6): what it saw last session.

Read leniently, because it exists to explain failures; not ingested into the database.
"""
from __future__ import annotations

from dataclasses import dataclass


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
    scan: str | None = None                # last full-scan read "calls=..|n=..|skipped=..|steps=..|maxStepMs=..|kind=..|rows=..|dropped=.."
    traces: tuple[str, ...] = ()           # last capture decisions, oldest first ("take 1 money=.. -> payout")
    own_auctions: str | None = None        # owned-auction probe "timeLeftSeconds=number|nil" (A70); None: none read



def read_diagnostics(value: object) -> Diagnostics | None:
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
        namespaces=tuple(namespaces), professions=text("professions"), trade_skill=text("tradeSkill"), scan=text("scan"),
        traces=tuple(t for t in (text("traces") or "").split(";") if t), own_auctions=text("ownAuctions"),
    )
