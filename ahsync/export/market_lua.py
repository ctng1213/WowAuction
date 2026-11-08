"""MarketIntelligence.lua: the intelligence, trimmed to what the addon needs (SPEC §3.5, §5.0 Export).

One compact string per item (Lua constant limits), decoded by the addon only when it
needs an item (SPEC §5.1 lazy decode):

    I[key] = "cur,curQty,curAgeMin,med7,med30,p10,p90,trend10,sold10,listed10,supply,conf,
              seasonality,postPrice,refStack,sellValue,volumeUnknown,chance1000,typicalSupply,suspect,closes30,
              med14,salesDays,supply7,supply14,sellChance1000,st7,st14,st30,stHow,commodity;sellWindows;buyWindows"
    (trailing empty fields are left out: a missing field is unavailable, as an empty one)
    C[key] = the sell-probability curve, one character per point (SPEC §3.1.1)

- Numbers are integers; an empty field is unavailable, never 0. Money is copper,
  ages are minutes before generatedAt, trend is tenths of a % per day, sales and
  listings per day are in tenths, and chance1000 is the chance a reference stack posted now
  sells, in thousandths (the addon's own sell options use it, A15).
- M33b (EXPORT_VERSION 6; SPEC §4.3 TT35-TT40): st7, st14 and st30 are the sell-through at the lowest price in
  thousandths, stHow one letter per lookback (m measured, e est., c class est., - none; "" when all are none),
  sellChance1000 the 24 h posting-now chance at cur (Sell Chance Now), commodity 1 or 0 ("" unknown). Each market
  carries coverage30, the hours observed in the 720 before generatedAt's hour (the tooltip header's coverage).
  The fields a local market never has come last, so the trailing-empty rule keeps the file within its 2 MB.
- A window is "start:hours:best:how1000:weeks[:pct1000]" (UTC slots, how_avg in thousandths; pct1000 a selling
  window's 24 h chance or a buying window's found share, in thousandths, left out when unknown; M33b),
  windows joined by "/", best first. Shortlist items carry every window the intelligence
  holds (top_sell_windows, top_buy_windows); tooltip-only items (not on the shortlist) only
  the top selling and the top buying window (EX19).
- Shortlist items keep the full 24 × 8 curve; tooltip-only items get one row of 3 points:
  the refresh hour's row at 0, 5 and 20 ahead, used at any hour (AR6, EX19). The 168-slot hour-of-week
  profiles are never exported (IU12).
- D[key] = "skill,quality,ilvl,kind" for exported items one could disenchant (kind a armor,
  w weapon), so the addon's Disenchant tab can list targets for an enchanter you don't have
  yet (A23).
- vendor = { [key] = copper }: what a merchant charges for items it sells in unlimited supply,
  so the addon's crafts can buy mats from a vendor (EX6). Not in the checksum.
- K, the exported candidates, and G, the dependency graph (R, U, DE, order), per market: formats in
  export/portfolio_lua.py (EX17, EX18, EXPORT_VERSION 4).
- The checksum is "<item, curve, D, K and G strings>:<their total length>": cheap for the addon to check
  at login, and a truncated or mixed-up file fails it.
- exportVersion (EX14) is outside the checksum: the build of the format, shared with PortfolioPlan.lua.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ahsync.engine import capacity, profile
from ahsync.engine.graph import Graph
from ahsync.export.portfolio_lua import graph_strings
from ahsync.engine.sellprob import CURVE_AHEAD, CURVE_AHEAD_TOOLTIP
from ahsync.engine.views import round_half_up
from dataclasses import replace

from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence, age_now_ms, ages_reference_ms

MARKET_SCHEMA_VERSION = 1      # the addon refuses any other (FM17)
# Bumped on ANY change to either addon file's format, additive or not, so an addon and an AHSync of
# different builds say which side is stale (SPEC §3.5, FM34, FM35). Equals AHProfit.EXPORT_VERSION (Core/Market.lua).
EXPORT_VERSION = 6              # 6: the tooltip's market lines: sell-through, supply 7/14, Sell Chance Now, windows'
                                # percentages, coverage30 (M33b); 5: market items end in salesDays (EX21); 4: K, G and the portfolio snapshot (EX17, EX18); 3: closes30
                                # (TT23, PP4); 2: a mail's recipient (EX16)
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
STALE_PRICE_MS = 7 * 86_400_000   # older prices are left out unless shortlisted (EX5: the 2 MB budget)
CHUNK = 2000                   # items per generated function: each has its own constant table (A3)
TOOLTIP_COLUMNS = tuple(CURVE_AHEAD.index(a) for a in CURVE_AHEAD_TOOLTIP)
EMPTY_GRAPH = Graph((), (), (), (), ())      # a market without known recipes or disenchantable items


def lua_string(text: str) -> str:
    """A Lua string literal.

    Args:
        text: Any text.

    Returns:
        The text in double quotes, with backslash, quote and line breaks escaped.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def item_count(text: str) -> int:
    """How many items a MarketIntelligence.lua holds, for the write's log line (EX15).

    Args:
        text: The file, as market_lua made it (one item per line, each starting "I[").

    Returns:
        The number of item strings, over every market.
    """
    return sum(1 for line in text.splitlines() if line.startswith("I["))


def _num(value: int | None) -> str:
    """An integer field; "" when unavailable.

    Args:
        value: The value, or None.

    Returns:
        Its digits, or "".
    """
    return "" if value is None else str(value)


def _milli(value: float | None) -> str:
    """A share in thousandths, rounded half up; "" when unavailable.

    Args:
        value: 0-1, or None.

    Returns:
        floor(value × 1000 + 0.5) as text, or "".
    """
    return "" if value is None else str(round_half_up(value * 1000))


def _tenths(value: float | None) -> str:
    """A float field in tenths, rounded half up; "" when unavailable.

    Args:
        value: The value, or None.

    Returns:
        floor(value × 10 + 0.5) as text, or "".
    """
    return "" if value is None else str(round_half_up(value * 10))


def windows_text(windows: Sequence[IntelWindow]) -> str:
    """Windows as "start:hours:best:how1000:weeks[:pct1000]", joined by "/".

    Args:
        windows: Best first.

    Returns:
        The text; "" for none.
    """
    return "/".join(f"{w.start_slot}:{w.hours}:{w.best_slot}:{round_half_up(w.how_avg * 1000)}:{w.weeks}"
                    + (f":{_milli(pct)}" if (pct := w.chance24 if w.chance24 is not None else w.found_share) is not None
                       else "") for w in windows)


def item_text(s: ItemIntel, full: bool = True) -> str:
    """One item's string (module docstring, EX2).

    Args:
        s: The item's intelligence.
        full: Whether the item is on the shortlist; a tooltip-only item keeps one window of each kind (EX19).

    Returns:
        "numbers;sellWindows;buyWindows".
    """
    top = None if full else 1
    age = None if s.cur_age_ms is None else s.cur_age_ms // MINUTE_MS
    fields = [_num(s.cur), _num(s.cur_qty), _num(age), _num(s.med7), _num(s.med30), _num(s.p10_30), _num(s.p90_30),
              _tenths(s.trend7), _tenths(s.sold_per_day), _tenths(s.listed_per_day), _num(s.units_on_ah), _num(s.conf),
              _num(s.seasonality), _num(s.post_price), _num(s.ref_stack),
              "" if s.sell_value is None else str(round_half_up(s.sell_value)), "1" if s.volume_unknown else "0",
              str(round_half_up(s.chance * 1000)), _num(s.median_daily_supply),          # EX10
              "1" if s.cur_suspect else "0", _num(s.closes_30d),                      # A58; TT23
              _num(s.med14),                                                          # EX20
              _num(sum(s.days_by_source.values()) if s.days_by_source else None),     # EX21: salesDays
              _num(s.supply7), _num(s.supply14), _milli(s.sell_chance),               # M33b, TT36, TT37
              *(_milli(r) for r in s.sell_through), "" if s.sell_through_how == "---" else s.sell_through_how,
              "" if s.commodity is None else ("1" if s.commodity else "0")]           # M33b, TT37-TT39
    return f"{','.join(fields).rstrip(',')};{windows_text(s.sell_windows[:top])};{windows_text(s.buy_windows[:top])}"


def trim_curve(curve: str, full: bool) -> str:
    """The curve as exported: all of it for the shortlist, else the refresh hour's 3 tooltip points (AR6, EX19).

    Args:
        curve: The intelligence's 24 × 8 curve, one character per point; row 0 is the refresh hour.
        full: Whether the item is on the shortlist.

    Returns:
        24 × 8 characters, or 3 (row 0 at 0, 5 and 20 ahead), which the addon uses at any hour; "" for an item
        without a curve.
    """
    if full or not curve:
        return curve
    return "".join(curve[col] for col in TOOLTIP_COLUMNS)


def wanted(key: int, s: ItemIntel, shortlist: set[int]) -> bool:
    """Whether an item is exported (EX2, EX5).

    Args:
        key: Item key.
        s: Its intelligence.
        shortlist: The market's shortlist.

    Returns:
        True on the shortlist, or with a price at most 7 days old.
    """
    recent = s.cur is not None and (s.cur_age_ms is None or s.cur_age_ms <= STALE_PRICE_MS)
    return recent or key in shortlist


def exported_items(intel: MarketIntelligence, shortlist: set[int]) -> list[tuple[int, str, str]]:
    """The items a market exports: everything with a price seen in the last 7 days, plus the shortlist (EX2, EX5).

    Args:
        intel: The market's intelligence.
        shortlist: Item keys that keep the full curve and are exported even without a (recent) price.

    Returns:
        (key, item string, curve string) by key.
    """
    return [(key, item_text(s, key in shortlist), trim_curve(s.curve, key in shortlist))
            for key, s in sorted(intel.items.items()) if wanted(key, s, shortlist)]


def _supply_rows(intel: MarketIntelligence, keys: set[int], window_hours: int) -> str:
    """The shortlist's supply factor rows as a Lua table body (EX13).

    Args:
        intel: The market's intelligence.
        keys: Shortlisted, exported item keys.
        window_hours: sell_window_hours.

    Returns:
        '[key] = "…", …' for items with a supply pattern, by key.
    """
    parts = []
    for key in sorted(keys):
        s = intel.items[key]
        pattern = profile.unpack(s.supply_how, s.supply_weeks).values if s.supply_how else ()
        row = capacity.supply_factor_row(pattern, intel.built_ms // HOUR_MS, window_hours)
        if row:
            parts.append(f"[{key}] = {lua_string(row)}")
    return ", ".join(parts)


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


def checksum(markets: Iterable[list[tuple[int, str, str]]], des: Iterable[Mapping[int, str]] = (),
             extra: Iterable[str] = ()) -> str:
    """The file's checksum over every item, curve, disenchant, candidate and graph string.

    Args:
        markets: Each market's exported items.
        des: Each market's exported D strings (only those of exported items).
        extra: Every K, R and U string and each market's G.DE and G.order (portfolio_strings); empty ones
            are not counted.

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
    for text in extra:
        if text:                                   # an empty G.DE or G.order counts as no string
            count += 1
            length += len(text)
    return f"{count}:{length}"


def portfolio_strings(candidates: Sequence[str], graph: Graph | None) -> list[str]:
    """One market's K and G strings, as the checksum counts them (EX17, EX18).

    Args:
        candidates: Its K strings.
        graph: Its graph, or None (an empty one).

    Returns:
        The K strings, the R and U strings by key, then G.DE and G.order (both always written, maybe empty;
        the checksum skips an empty one).
    """
    recipes, uses, de, order = graph_strings(graph or EMPTY_GRAPH)
    return [*candidates, *(recipes[k] for k in sorted(recipes)), *(uses[k] for k in sorted(uses)), de, order]


def _portfolio_block(name: str, candidates: Sequence[str], graph: Graph | None) -> list[str]:
    """Lua statements filling one market's K and G tables, in their own function (constant limits, A3).

    Args:
        name: The Lua locals' suffix, e.g. "1" for K1 and G1.
        candidates: Its K strings, in export order.
        graph: Its graph, or None (an empty one).

    Returns:
        Source lines: one `K[n]=`, `R[spell]=` or `U[key]=` per line, then `G.DE=` and `G.order=` (the snapshot
        keeps these lines, tools/snapshot.py).
    """
    recipes, uses, de, order = graph_strings(graph or EMPTY_GRAPH)
    lines = [f"local K{name}, G{name} = {{}}, {{ R = {{}}, U = {{}} }}", "do local fill = function(K, R, U, G)"]
    lines += [f"K[{n}]={lua_string(text)}" for n, text in enumerate(candidates, start=1)]
    lines += [f"R[{spell}]={lua_string(recipes[spell])}" for spell in sorted(recipes)]
    lines += [f"U[{key}]={lua_string(uses[key])}" for key in sorted(uses)]
    lines += [f"G.DE={lua_string(de)}", f"G.order={lua_string(order)}",
              f"end fill(K{name}, G{name}.R, G{name}.U, G{name}) end"]
    return lines


def aged_at(intel: MarketIntelligence, generated_ms: int) -> MarketIntelligence:
    """The intelligence with every price age counted back from the export's time, as the file's ages are (SPEC §3.5).

    A cloud document's ages count back from its prices' time (ages_at_ms), a desktop-built one's from built_ms;
    the addon reads them as minutes before generatedAt.

    Args:
        intel: A market's intelligence.
        generated_ms: The export's time, UTC ms.

    Returns:
        A copy with cur_age_ms as of generated_ms.
    """
    reference = ages_reference_ms(intel)
    if reference == generated_ms:
        return intel
    return replace(intel, items={k: replace(i, cur_age_ms=age_now_ms(i.cur_age_ms, generated_ms, reference))
                                 for k, i in intel.items.items()})


def market_lua(intels: Sequence[MarketIntelligence], shortlists: Mapping[str, set[int]],
               char_markets: Mapping[str, str], generated_ms: int,
               disenchantable: Mapping[int, str] | None = None, vendor: Mapping[int, int] | None = None,
               flips: Mapping[str, Mapping[int, str]] | None = None, window_hours: int = 8,
               candidates: Mapping[str, Sequence[str]] | None = None, graphs: Mapping[str, Graph] | None = None,
               coverage30: Mapping[str, int] | None = None) -> str:
    """The whole MarketIntelligence.lua (SPEC §3.5, EX2).

    Args:
        intels: Each market's intelligence, in config order.
        shortlists: Market -> its shortlist (EX1).
        char_markets: Character id -> market, so the addon knows which market is its own.
        generated_ms: Now, UTC ms; item ages count back from it.
        disenchantable: Item key -> "skill,quality,ilvl,kind" (A23); exported for exported items only.
        vendor: Item key -> vendor buy price in copper, for vendor-sold items (EX6).
        flips: Market -> item key -> "expected,price,slot" flip offers (EX11); exported items only, outside the
            checksum.
        window_hours: sell_window_hours, for the shortlist's supply factors (EX13).
        candidates: Market -> its K strings in export order (portfolio_lua.candidate_text, EX17); none when absent.
        graphs: Market -> its dependency graph (EX18); an empty one when absent.
        coverage30: Market -> hours observed in the last 720 (TT40); 0 when absent.

    Returns:
        Lua source defining the global AHProfitMarket.
    """
    intels = [aged_at(intel, generated_ms) for intel in intels]
    blocks = [exported_items(intel, shortlists.get(intel.market, set())) for intel in intels]
    des = [{key: (disenchantable or {})[key] for key, _, _ in items if key in (disenchantable or {})} for items in blocks]
    lines = ["-- MarketIntelligence.lua: generated by AHSync (SPEC §3.5). Do not edit; it is replaced on every refresh."]
    entries, extra = [], []
    for n, (intel, items, de) in enumerate(zip(intels, blocks, des), start=1):
        lines += _market_block(str(n), items, de)
        k_texts, graph = (candidates or {}).get(intel.market, ()), (graphs or {}).get(intel.market)
        lines += _portfolio_block(str(n), k_texts, graph)
        extra += portfolio_strings(k_texts, graph)
        until = _num(intel.observations_until_ms) or "nil"
        exported = {key for key, _, _ in items}
        offers = ", ".join(f"[{key}] = {lua_string(text)}" for key, text in sorted((flips or {}).get(intel.market, {}).items())
                           if key in exported)
        rows = _supply_rows(intel, shortlists.get(intel.market, set()) & exported, window_hours)
        entries.append(f"  [{lua_string(intel.market)}] = {{ observationsUntil = {until}, "
                       f"curveHour = {intel.built_ms // HOUR_MS}, I = I{n}, C = C{n}, D = D{n}, F = {{ {offers} }}, "
                       f"S = {{ {rows} }}, K = K{n}, G = G{n}, coverage30 = {(coverage30 or {}).get(intel.market, 0)} }},")
    chars = ", ".join(f"[{lua_string(c)}] = {lua_string(m)}" for c, m in sorted(char_markets.items()))
    vendors = ", ".join(f"[{key}] = {price}" for key, price in sorted((vendor or {}).items()))
    lines += ["AHProfitMarket = {", f"  schemaVersion = {MARKET_SCHEMA_VERSION},",
              f"  exportVersion = {EXPORT_VERSION},", f"  generatedAt = {generated_ms},",
              f"  checksum = {lua_string(checksum(blocks, des, extra))},", f"  charMarkets = {{ {chars} }},", f"  vendor = {{ {vendors} }},", "  markets = {",
              *entries, "  },", "}", ""]
    return "\n".join(lines)
