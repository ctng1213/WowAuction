"""PortfolioPlan.lua: the desktop's plan, item rules and the pruning handshake (SPEC §3.5, §2.2).

    AHProfitPlan = {
      schemaVersion, exportVersion, generatedAt, checksum, basedOnUntil,
      ingestedUntil = { [charId] = ms },     -- the addon deletes that character's rows at or before it
      actions = { [charId] = { "id;kind;key;qty;price;atMs;net;parentId;windowEndMs;status;chance;peakMs;source;
                               durationH;toChar;paid", ... } },
      rules = { [key] = "rule,setAtMs" },    -- "none" is a clear and travels too (SPEC §3.4.2)
      portfolio, gold, reserved, positions   -- the portfolio snapshot (export/portfolio_lua.py, EX18)
    }

Only open actions (pending, late) are exported, in time order; an empty field is
unavailable. Net is the planned expected net in copper, rounded half up; chance is the
planned chance to sell in whole percent and peakMs the hour a post is for (posts;
empty otherwise); toChar is a mail's recipient (C22, EXPORT_VERSION 2); paid is what net takes off for the
units, copper, empty for 0 (EX22, EXPORT_VERSION 7). The checksum
is "<strings>:<their total length>" over every action, rule, portfolio, gold, reserved (its digits) and
position string, like MarketIntelligence.lua's; the length is in UTF-8 bytes, as Lua's # counts them (a position's
next action may hold "·", M14). It is account-specific: every account on the machine shares
it, which is why ingestedUntil is per character (SPEC §2.2). exportVersion (market_lua.EXPORT_VERSION, EX14) is
outside the checksum.
SPEC GAP: the sell record per item (s, x, streak) joins it with tracking (M6b).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ahsync.engine.views import round_half_up
from ahsync.export.market_lua import EXPORT_VERSION, lua_string

PLAN_SCHEMA_VERSION = 1        # the addon refuses any other (EX4)


@dataclass(frozen=True)
class PortfolioFields:
    """The portfolio part of PortfolioPlan.lua (SPEC §3.5, EX18), formats in export/portfolio_lua.py."""

    portfolio: dict[str, str] = field(default_factory=dict)           # market -> portfolio string
    gold: dict[str, str] = field(default_factory=dict)                # character -> "copper,atMs"
    reserved: dict[str, int] = field(default_factory=dict)            # character -> copper the funded plan reserves
    positions: dict[str, list[str]] = field(default_factory=dict)     # character -> position strings

    def strings(self) -> list[str]:
        """Every string the checksum counts, in the file's order.

        Returns:
            Portfolio strings by market, gold strings by character, reserved amounts as decimal text by
            character, then position strings by character.
        """
        out = [self.portfolio[m] for m in sorted(self.portfolio)] + [self.gold[c] for c in sorted(self.gold)]
        out += [str(self.reserved[c]) for c in sorted(self.reserved)]
        return out + [text for c in sorted(self.positions) for text in self.positions[c]]

    def lines(self) -> list[str]:
        """The Lua lines of the four tables, inside AHProfitPlan.

        Returns:
            Source lines.
        """
        lines = ["  portfolio = {"] + [f"    [{lua_string(m)}] = {lua_string(t)}," for m, t in sorted(self.portfolio.items())]
        lines += ["  },", "  gold = {"] + [f"    [{lua_string(c)}] = {lua_string(t)}," for c, t in sorted(self.gold.items())]
        lines += ["  },", "  reserved = {"] + [f"    [{lua_string(c)}] = {n}," for c, n in sorted(self.reserved.items())]
        lines += ["  },", "  positions = {"]
        for char_id in sorted(self.positions):
            lines += [f"    [{lua_string(char_id)}] = {{", *(f"      {lua_string(t)}," for t in self.positions[char_id]),
                      "    },"]
        return lines + ["  },"]


def _field(value: object) -> str:
    """One action field; "" when unavailable.

    Args:
        value: The value, or None.

    Returns:
        Its text.
    """
    return "" if value is None else str(value)


def action_text(row: tuple) -> str:
    """One open action as the addon reads it (EX3).

    Args:
        row: (action_id, char_id, kind, key, qty, price, slot_ms, expected_net, parent_id, window_end_ms, status,
            chance 0-1 or None, peak_ms or None, source "vendor" | "ah" | None[, duration_h or None[, to_char[,
            paid copper or None]]]).

    Returns:
        "id;kind;key;qty;price;atMs;net;parentId;windowEndMs;status;chance;peakMs;source;durationH;toChar;paid",
        source "v" for a vendor buy and empty otherwise (EX7), durationH a post's auction duration in hours (EX9),
        toChar a mail's recipient and empty otherwise (C22, EX16), paid the copper net takes off for the units and
        empty for 0 (EX22).
    """
    action_id, _, kind, key, qty, price, slot_ms, net, parent, window_end, status, chance, peak, source = row[:14]
    duration = row[14] if len(row) > 14 else None
    to_char = row[15] if len(row) > 15 else None
    paid = row[16] if len(row) > 16 and row[16] else None
    net_copper = None if net is None else round_half_up(net)
    percent = None if chance is None else round_half_up(chance * 100)
    return ";".join(_field(v) for v in (action_id, kind, key, qty, price, slot_ms, net_copper, parent, window_end, status,
                                        percent, peak, "v" if source == "vendor" else None, duration, to_char,
                                        paid))


def plan_lua(actions: Sequence[tuple], rules: Sequence[tuple[int, str, int]], ingested_until: Mapping[str, int],
             generated_ms: int, records: Mapping[int, str] | None = None,
             reserves: Sequence[tuple[str, int, int, int]] = (), portfolio: PortfolioFields | None = None) -> str:
    """The whole PortfolioPlan.lua (SPEC §3.5, EX3).

    Args:
        actions: Open actions as in action_text, in time order.
        rules: (item key, rule, set_at ms), cleared rules ("none") included.
        ingested_until: Character id -> newest committed scan or event time, ms.
        generated_ms: Now, UTC ms.
        records: Item key -> "1;line" (not selling) or "0;line", your sell records (EX8); outside the checksum,
            so an older addon still reads the file.
        reserves: (character id, item key, units kept, set_at ms) with a time, clears (0) included (EX12); outside
            the checksum.
        portfolio: The portfolio snapshot, gold, reservations and positions (EX18); empty tables when None.

    Returns:
        Lua source defining the global AHProfitPlan.
    """
    by_char: dict[str, list[str]] = {}
    for row in actions:
        by_char.setdefault(row[1], []).append(action_text(row))
    rule_texts = {key: f"{rule},{set_at}" for key, rule, set_at in rules}
    fields = portfolio or PortfolioFields()
    strings = [s for texts in by_char.values() for s in texts] + list(rule_texts.values()) + fields.strings()
    based_on = max(ingested_until.values()) if ingested_until else None
    lines = ["-- PortfolioPlan.lua: generated by AHSync (SPEC §3.5). Do not edit; it is replaced whenever the plan changes.",
             "AHProfitPlan = {", f"  schemaVersion = {PLAN_SCHEMA_VERSION},",
             f"  exportVersion = {EXPORT_VERSION},", f"  generatedAt = {generated_ms},",
             f"  checksum = {lua_string(f'{len(strings)}:{sum(len(s.encode()) for s in strings)}')},",
             f"  basedOnUntil = {based_on if based_on is not None else 'nil'},", "  ingestedUntil = {"]
    lines += [f"    [{lua_string(c)}] = {ms}," for c, ms in sorted(ingested_until.items())]
    lines += ["  },", "  actions = {"]
    for char_id in sorted(by_char):
        lines.append(f"    [{lua_string(char_id)}] = {{")
        lines += [f"      {lua_string(text)}," for text in by_char[char_id]]
        lines.append("    },")
    lines += ["  },", "  rules = {"]
    lines += [f"    [{key}] = {lua_string(text)}," for key, text in sorted(rule_texts.items())]
    lines += ["  },", *fields.lines(), "  records = {"]
    lines += [f"    [{key}] = {lua_string(text)}," for key, text in sorted((records or {}).items())]
    kept: dict[str, list[str]] = {}
    for char_id, key, qty, set_at in sorted(reserves):
        kept.setdefault(char_id, []).append(f"{key},{qty},{set_at}")
    lines += ["  },", "  reserves = {"]
    lines += [f"    [{lua_string(c)}] = {lua_string(';'.join(parts))}," for c, parts in sorted(kept.items())]
    lines += ["  },", "}", ""]
    return "\n".join(lines)
