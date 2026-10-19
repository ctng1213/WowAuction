"""Summarize what the addon recorded in game, to check the unverified WoW APIs (SPEC §6).

Usage: python tools/savedvars_report.py [--wow-path PATH] [--file PATH]

Finds every `WTF/Account/*/SavedVariables/AHProfit.lua` in the client folder
(or reads --file), parses it with AHSync's own reader and prints, per character,
what was captured and what is still missing, with a hint for each gap. WoW
writes the file at logout or /reload, so do one of those first.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from ahsync.state.savedvars import AHProfitData, SavedVarsError, is_unknown_suffix, read_ahprofit_file  # noqa: E402
from deploy import wow_path_from_config  # noqa: E402

LOCATIONS = ("bags", "bank", "mail")
SUFFIX_BASE = 2_147_483_648   # AHF key = item + 2^31 x v (SPEC §2.1)
HINTS = {
    "professions": "open the Skills tab with every header expanded (Adapter: GetSkillLineInfo)",
    "recipes": "open each profession window (Adapter: ReadModernWindow, C_TradeSkillUI)",
    "bags": "move an item in your bags; if still missing, check C_Container.GetContainerItemInfo",
    "bank": "open your bank (Adapter: bank containers -1 and 5-10)",
    "mail": "open your mailbox; with more than 50 mails it is skipped on purpose (GetInboxNumItems)",
    "vendor": "open a merchant that sells reagents (Adapter: GetMerchantItemInfo)",
}


def gold(copper: int) -> str:
    """Format copper as WoW money.

    Args:
        copper: Amount in copper.

    Returns:
        e.g. "12g 34s 56c".
    """
    return f"{copper // 10000}g {copper // 100 % 100}s {copper % 100}c"


def local_time(ms: int) -> str:
    """Format a UTC ms timestamp in the machine's local time.

    Args:
        ms: UTC Unix time in ms.

    Returns:
        e.g. "2026-10-16 21:05".
    """
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def report_character(data: AHProfitData, char_id: str, gaps: list[str]) -> None:
    """Print one character's captured data and note what is missing.

    Args:
        data: The parsed file.
        char_id: "Name-Realm".
        gaps: Collects (hint key) for each missing piece; printed once at the end.
    """
    char = next(c for c in data.characters if c.char_id == char_id)
    print(f"\n{char_id}  level {char.level} {char.class_token} {char.faction}  gold {gold(char.gold_copper)}"
          f"  (updated {local_time(char.updated_ms)})")
    professions = [p for p in data.professions if p.char_id == char_id]
    known = sum(1 for k in data.known_recipes if k.char_id == char_id)
    print("  professions: " + (", ".join(f"{p.profession} {p.skill}/{p.max_skill}" for p in professions) or "none"))
    print(f"  known recipes: {known}")
    if not professions:
        gaps.append("professions")
    if professions and not known:
        gaps.append("recipes")
    for location in LOCATIONS:
        snapshot = next((i for i in data.inventories if i.char_id == char_id and i.location == location), None)
        if snapshot is None:
            print(f"  {location:5}: not captured")
            gaps.append(location)
            continue
        units = sum(r.qty for r in snapshot.rows)
        bound = sum(1 for r in snapshot.rows if r.bound)
        suffixed = [r.key for r in snapshot.rows if r.key >= SUFFIX_BASE]
        unknown = sum(1 for r in snapshot.rows if is_unknown_suffix(r.key))
        print(f"  {location:5}: {len(snapshot.rows)} stacks, {units} items, {bound} bound, "
              f"{len(suffixed)} random-suffix (keyed), {unknown} unknown-suffix  (at {local_time(snapshot.at_ms)})")
        for key in suffixed:
            print(f"         suffix item {key % SUFFIX_BASE} v={key // SUFFIX_BASE} key={key}")


def report_diagnostics(data: AHProfitData, verbose: bool) -> None:
    """Print what the addon saw last session: client, missing APIs, errors, raw skill and merchant rows.

    Args:
        data: The parsed file; data.diagnostics is None for files from before diagnostics existed.
        verbose: Also print the API lists, namespace function lists and the Classic Skills-tab dump.
    """
    d = data.diagnostics
    if d is None:
        print("\ndiagnostics: none saved (redeploy the addon, restart WoW, /reload)")
        return
    when = local_time(d.at_ms) if d.at_ms else "?"
    print(f"\ndiagnostics (saved {when}, client {d.client}):")
    if d.setup_error:
        print(f"  DATABASE SETUP FAILED: {d.setup_error}")
    if verbose:
        missing = [name for name, kind in d.apis if kind == "nil"]
        present = [name for name, kind in d.apis if kind != "nil"]
        print(f"  APIs present: {', '.join(present) or 'none'}")
        print(f"  APIs MISSING: {', '.join(missing) or 'none'}")
    print(f"  errors caught: {len(d.errors)}")
    for error in d.errors:
        print(f"    - {error}")
    classic_skills = d.skill_lines and not d.skill_lines.startswith("error")
    if verbose or classic_skills:   # Forever has no Classic Skills tab; its dump is only an error there
        print("  Skills tab (name | header | expanded | rank | max):")
        rows = d.skill_lines.split(";") if classic_skills else []
        if not rows:
            print(f"    {d.skill_lines or 'nothing read'}")
        for row in rows:
            print(f"    {row.replace('|', ' | ')}")
    print("  professions, modern API (slot | spellbook index | name | rank | max):")
    for row in (d.professions or "nothing read").split(";"):
        print(f"    {row.replace('|', ' | ')}")
    for namespace, functions in d.namespaces:
        if functions is None:
            print(f"  {namespace}: MISSING")
        elif verbose:
            print(f"  {namespace}: {len(functions)} functions: {' '.join(functions)}")
    print(f"  last trade skill window: {(d.trade_skill or 'none opened this session').replace('|', '  ')}")
    print(f"  last full scan read: {(d.scan or 'no scan this session').replace('|', '  ')}")
    print("  last merchant (item key | available, -1 = unlimited | extended cost):")
    for row in (d.merchant or "no merchant opened this session").split(";"):
        print(f"    {row.replace('|', ' | ')}")


def report(path: Path, verbose: bool = False) -> int:
    """Print the report for one SavedVariables file.

    Args:
        path: An AHProfit.lua SavedVariables file.
        verbose: Include the full API and namespace dumps.

    Returns:
        0 when it parsed, 1 when it couldn't be read.
    """
    print(f"== {path}")
    try:
        data = read_ahprofit_file(path)
    except SavedVarsError as error:
        print(f"  cannot read it: {error}")
        return 1
    print(f"format {data.format_version}, addon {data.addon_version}; {len(data.characters)} character(s)")
    gaps: list[str] = []
    for char in data.characters:
        report_character(data, char.char_id, gaps)
    by_profession = Counter(r.profession for r in data.recipes)
    print("\nrecipe definitions (account-wide): "
          + (", ".join(f"{name} {count}" for name, count in sorted(by_profession.items())) or "none"))
    no_reagents = [r.spell_id for r in data.recipes if not r.reagents]
    if no_reagents:
        print(f"  {len(no_reagents)} recipe(s) with no reagents (suspicious): {no_reagents[:10]}")
    print(f"vendor reagents seen: {len(data.vendor_reagents)}")
    if not data.vendor_reagents:
        gaps.append("vendor")
    events = Counter(e.type for e in data.events)
    print("events: " + (", ".join(f"{t} {n}" for t, n in sorted(events.items())) or "none"))
    names = {n.key: n.name for n in data.item_names}
    for e in data.events:
        if e.type == "payout" or (e.type == "auction_mail" and e.outcome == "sold"):
            what = f"{e.money_copper}c taken" if e.type == "payout" else f"sold at {e.price_copper}c each"
            print(f"  {e.type:<12} {e.char_id}  {names.get(e.key, e.key)} x{e.qty}  {what}")
    report_diagnostics(data, verbose)
    if data.problems:
        print(f"\nPROBLEMS ({len(data.problems)} rows set aside):")
        for problem in data.problems[:20]:
            print(f"  - {problem}")
    if gaps:
        print("\nstill missing (do this in game, then /reload and re-run):")
        for key in dict.fromkeys(gaps):
            print(f"  - {key}: {HINTS[key]}")
    return 0


def main() -> int:
    """Find the SavedVariables file(s) and report on each.

    Returns:
        Process exit code: 0 when every file found parsed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wow-path", help="WoW client folder (default: config.toml or the SPEC §3.6 default)")
    parser.add_argument("--file", help="a specific AHProfit.lua to read instead")
    parser.add_argument("--verbose", action="store_true", help="include the API and namespace function dumps")
    args = parser.parse_args()
    if args.file:
        paths = [Path(args.file)]
    else:
        wow = wow_path_from_config(args.wow_path)
        paths = sorted((wow / "WTF" / "Account").glob("*/SavedVariables/AHProfit.lua"))
        if not paths:
            print(f"No AHProfit.lua under {wow / 'WTF' / 'Account'}. Log in with the addon enabled, "
                  "then /reload or log out so WoW writes it.")
            return 1
    return max(report(p, args.verbose) for p in paths)


if __name__ == "__main__":
    sys.exit(main())
