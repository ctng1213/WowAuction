"""MarketIntelligence.lua: the intelligence, trimmed to what the addon needs (SPEC §3.5, §5.0 Export).

One compact string per item (Lua constant limits), decoded by the addon only when it
needs an item (SPEC §5.1 lazy decode):

    I[key] = "cur,curQty,curAgeMin,med7,med30,p10,p90,trend10,sold10,listed10,supply,conf,
              seasonality,postPrice,refStack,sellValue,volumeUnknown,chance1000;sellWindows;buyWindows"
    C[key] = the sell-probability curve, one character per point (SPEC §3.1.1)

- Numbers are integers; an empty field is unavailable, never 0. Money is copper,
  ages are minutes before generatedAt, trend is tenths of a % per day, sales and
  listings per day are in tenths, and chance1000 is the chance a reference stack posted now
  sells, in thousandths (the addon's own sell options use it, A15).
- A window is "start:hours:best:how1000:weeks" (UTC slots, how_avg in thousandths),
  windows joined by "/", best first.
- Shortlist items keep the full 24 × 8 curve; the rest get the 24 × 3 tooltip subset
  (the 0, 5 and 20 ahead columns, unchanged) (AR6). The 168-slot hour-of-week
  profiles are never exported (IU12).
- D[key] = "skill,quality,ilvl,kind" for exported items one could disenchant (kind a armor,
  w weapon), so the addon's Disenchant tab can list targets for an enchanter you don't have
  yet (A23).
- vendor = { [key] = copper }: what a merchant charges for items it sells in unlimited supply,
  so the addon's crafts can buy mats from a vendor (EX6). Not in the checksum.
- The checksum is "<item and D strings>:<their total length>": cheap for the addon to check
  at login, and a truncated or mixed-up file fails it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ahsync.engine.sellprob import CURVE_AHEAD, CURVE_AHEAD_TOOLTIP, CURVE_ROWS
from ahsync.engine.views import round_half_up
from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence

MARKET_SCHEMA_VERSION = 1      # the addon refuses any other (FM17)
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
STALE_PRICE_MS = 7 * 86_400_000   # older prices are left out unless shortlisted (EX5: the 2 MB budget)
CHUNK = 2000                   # items per generated function: each has its own constant table (A3)
TOOLTIP_COLUMNS = tuple(CURVE_AHEAD.index(a) for a in CURVE_AHEAD_TOOLTIP)


def lua_string(text: str) -> str:
    """A Lua string literal.

    Args:
        text: Any text.

    Returns:
        The text in double quotes, with backslash, quote and line breaks escaped.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def _num(value: int | None) -> str:
    """An integer field; "" when unavailable.

    Args:
        value: The value, or None.

    Returns:
        Its digits, or "".
    """
    return "" if value is None else str(value)


def _tenths(value: float | None) -> str:
    """A float field in tenths, rounded half up; "" when unavailable.

    Args:
        value: The value, or None.

    Returns:
        floor(value × 10 + 0.5) as text, or "".
    """
    return "" if value is None else str(round_half_up(value * 10))


def windows_text(windows: Sequence[IntelWindow]) -> str:
    """Windows as "start:hours:best:how1000:weeks", joined by "/".

    Args:
        windows: Best first.

    Returns:
        The text; "" for none.
    """
    return "/".join(f"{w.start_slot}:{w.hours}:{w.best_slot}:{round_half_up(w.how_avg * 1000)}:{w.weeks}"
                    for w in windows)


def item_text(s: ItemIntel) -> str:
    """One item's string (module docstring, EX2).

    Args:
        s: The item's intelligence.

    Returns:
        "numbers;sellWindows;buyWindows".
    """
    age = None if s.cur_age_ms is None else s.cur_age_ms // MINUTE_MS
    fields = [_num(s.cur), _num(s.cur_qty), _num(age), _num(s.med7), _num(s.med30), _num(s.p10_30), _num(s.p90_30),
              _tenths(s.trend7), _tenths(s.sold_per_day), _tenths(s.listed_per_day), _num(s.units_on_ah), _num(s.conf),
              _num(s.seasonality), _num(s.post_price), _num(s.ref_stack),
              "" if s.sell_value is None else str(round_half_up(s.sell_value)), "1" if s.volume_unknown else "0",
              str(round_half_up(s.chance * 1000)), _num(s.median_daily_supply)]          # EX10
    return f"{','.join(fields)};{windows_text(s.sell_windows)};{windows_text(s.buy_windows)}"


def trim_curve(curve: str, full: bool) -> str:
    """The curve as exported: all 8 columns for the shortlist, else the 3 tooltip columns (AR6).

    Args:
        curve: The intelligence's 24 × 8 curve, one character per point.
        full: Whether the item is on the shortlist.

    Returns:
        24 × 8 or 24 × 3 characters; "" for an item without a curve.
    """
    if full or not curve:
        return curve
    width = len(CURVE_AHEAD)
    return "".join(curve[row * width + col] for row in range(CURVE_ROWS) for col in TOOLTIP_COLUMNS)


def exported_items(intel: MarketIntelligence, shortlist: set[int]) -> list[tuple[int, str, str]]:
    """The items a market exports: everything with a price seen in the last 7 days, plus the shortlist (EX2, EX5).

    Args:
        intel: The market's intelligence.
        shortlist: Item keys that keep the full curve and are exported even without a (recent) price.

    Returns:
        (key, item string, curve string) by key.
    """
    def wanted(key: int, s: ItemIntel) -> bool:
        """Whether an item is exported.

        Args:
            key: Item key.
            s: Its intelligence.

        Returns:
            True on the shortlist, or with a price at most 7 days old.
        """
        recent = s.cur is not None and (s.cur_age_ms is None or s.cur_age_ms <= STALE_PRICE_MS)
        return recent or key in shortlist
    return [(key, item_text(s), trim_curve(s.curve, key in shortlist))
            for key, s in sorted(intel.items.items()) if wanted(key, s)]


def _market_block(name: str, items: list[tuple[int, str, str]], de: Mapping[int, str]) -> list[str]:
    """Lua statements filling one market's I, C and D tables, CHUNK items per function (A3).

    Args:
        name: The Lua local's suffix, e.g. "1" for I1, C1 and D1.
        items: From exported_items.
        de: Item key -> "skill,quality,ilvl,kind" for the disenchantable ones.

    Returns:
        Source lines.
    """
    lines = [f"local I{name}, C{name}, D{name} = {{}}, {{}}, {{}}"]
    for start in range(0, len(items), CHUNK):
        lines.append("do local fill = function(I, C, D)")
        for key, text, curve in items[start:start + CHUNK]:
            line = f"I[{key}]={lua_string(text)}" + (f" C[{key}]={lua_string(curve)}" if curve else "")
            lines.append(line + (f" D[{key}]={lua_string(de[key])}" if key in de else ""))
        lines.append(f"end fill(I{name}, C{name}, D{name}) end")
    return lines


def checksum(markets: Iterable[list[tuple[int, str, str]]], des: Iterable[Mapping[int, str]] = ()) -> str:
    """The file's checksum over every item, curve and disenchant string.

    Args:
        markets: Each market's exported items.
        des: Each market's exported D strings (only those of exported items).

    Returns:
        "<number of strings>:<their total length>".
    """
    count = length = 0
    for items in markets:
        for _, text, curve in items:
            count += 1 + bool(curve)
            length += len(text) + len(curve)
    for de in des:
        count += len(de)
        length += sum(len(v) for v in de.values())
    return f"{count}:{length}"


def market_lua(intels: Sequence[MarketIntelligence], shortlists: Mapping[str, set[int]],
               char_markets: Mapping[str, str], generated_ms: int,
               disenchantable: Mapping[int, str] | None = None, vendor: Mapping[int, int] | None = None) -> str:
    """The whole MarketIntelligence.lua (SPEC §3.5, EX2).

    Args:
        intels: Each market's intelligence, in config order.
        shortlists: Market -> its shortlist (EX1).
        char_markets: Character id -> market, so the addon knows which market is its own.
        generated_ms: Now, UTC ms; item ages count back from it.
        disenchantable: Item key -> "skill,quality,ilvl,kind" (A23); exported for exported items only.
        vendor: Item key -> vendor buy price in copper, for vendor-sold items (EX6).

    Returns:
        Lua source defining the global AHProfitMarket.
    """
    blocks = [exported_items(intel, shortlists.get(intel.market, set())) for intel in intels]
    des = [{key: (disenchantable or {})[key] for key, _, _ in items if key in (disenchantable or {})} for items in blocks]
    lines = ["-- MarketIntelligence.lua: generated by AHSync (SPEC §3.5). Do not edit; it is replaced on every refresh."]
    entries = []
    for n, (intel, items, de) in enumerate(zip(intels, blocks, des), start=1):
        lines += _market_block(str(n), items, de)
        until = _num(intel.observations_until_ms) or "nil"
        entries.append(f"  [{lua_string(intel.market)}] = {{ observationsUntil = {until}, "
                       f"curveHour = {intel.built_ms // HOUR_MS}, I = I{n}, C = C{n}, D = D{n} }},")
    chars = ", ".join(f"[{lua_string(c)}] = {lua_string(m)}" for c, m in sorted(char_markets.items()))
    vendors = ", ".join(f"[{key}] = {price}" for key, price in sorted((vendor or {}).items()))
    lines += ["AHProfitMarket = {", f"  schemaVersion = {MARKET_SCHEMA_VERSION},", f"  generatedAt = {generated_ms},",
              f"  checksum = {lua_string(checksum(blocks, des))},", f"  charMarkets = {{ {chars} }},", f"  vendor = {{ {vendors} }},", "  markets = {",
              *entries, "  },", "}", ""]
    return "\n".join(lines)
