"""The portfolio optimizer's data contracts (SPEC §3.4D, M9): candidates, accounting and the export subset.

A `Candidate` is one marginal batch of one strategy for one character (§3.4D.3). It is
generated independently of risk tolerance (PF1): it carries no risk field, and its id
is built only from its strategy, item, character, route and batch. `Accounting` is
§3.4D.1's capital and risk budget, all in integer copper; found items cost 0, so they
add nothing to it (PF22). `export_subset` keeps the funded candidates first, then the
best alternatives by adjusted profit (PF3).

Selection, the timing premium and validation come with M10; positions with M14 (they
are passed in here as `OpenPosition`, just their remaining cost). Pure: data in,
numbers out; times are UTC ms, money integer copper except expected values.
"""
from __future__ import annotations

import json
import math
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields

HOUR_MS = 3_600_000
CANDIDATE_SCHEMA_VERSION = 2          # 2: window_end_ms, the export's windowEndMs (EX17)
# Strategy code (in ids and records) -> the Strategy column's label (SPEC §3.4D.3).
STRATEGIES: dict[str, str] = {
    "flip": "Flip",
    "buy_later": "Buy for later",
    "craft": "Craft",
    "multistage": "Multistage craft",
    "disenchant": "Disenchant",
    "sell_stock": "Sell stock",
}
# A reagent's source kind -> its letter in a route, written in this order (SPEC §3.4B sources).
SOURCE_LETTERS: dict[str, str] = {"owned": "o", "vendor": "v", "ah": "a", "crafted": "c"}
_ID_FORBIDDEN = (":", ";", ",", "|")       # the id's own separator, the export's separators, WoW's escape


class CandidateVersionError(ValueError):
    """A candidate record written by a newer AHSync than this one."""


@dataclass(frozen=True)
class PortfolioSettings:
    """The config values the portfolio optimizer takes (SPEC §3.6; passed in, no config file in the engine)."""

    risk_tolerance: float = 0.30       # share of the capital base that may be tied up at once (§3.4D.1)
    premium_base: float = 0.15         # timing premium after the immediate window (§3.4D.2)
    premium_per_day: float = 0.05      # added premium per day of wait
    immediate_hours: int = 8           # profit realized within this many hours has no premium (8 or 24)
    top_buy_windows: int = 3           # buying windows kept per item
    top_sell_windows: int = 3          # selling windows kept per item
    today_limit: int = 20              # most funded rows in Today (§3.4D.4)
    week_limit: int = 20               # most funded rows in Next 7 days
    candidate_export: int = 150        # candidates exported to the addon (§3.4D.3)
    horizon_days: int = 7              # the plan's horizon
    candidate_pool: int = 400          # buys (flips, buys for later) kept past their first batch (§3.4D.3)
    swap_candidates: int = 50          # excluded batches the swap pass tries (§3.4D.4)


# ---------------------------------------------------------------- candidates (§3.4D.3)

@dataclass(frozen=True)
class Requirements:
    """What the acting character needs to do a candidate (SPEC §3.4D.3, checked by eligibility in M10)."""

    profession: str | None = None      # e.g. "Enchanting"; None when no profession is needed (a flip, a post)
    skill: int = 0                     # the skill level it needs
    spell_id: int | None = None        # the recipe's spell; None when no recipe is needed
    cooldown_s: int = 0                # the recipe's cooldown in seconds; 0 = none


@dataclass(frozen=True)
class OwnedClaim:
    """Owned units a candidate uses (SPEC §3.4D.3): a unit claimed by a funded candidate is gone for the others."""

    char_id: str
    place: str                         # "bags" | "bank" | "mail" (SPEC §3.2 inventory location)
    key: int                           # item key
    units: int


@dataclass(frozen=True)
class CapacityClaim:
    """Market capacity a candidate takes in the shared ledger (SPEC §3.4 market capacity, PL4)."""

    key: int                           # item key
    window_ms: int | None              # the window's start ms; None for posting now (engine.plan.window_id)
    units: int


@dataclass(frozen=True)
class Candidate:
    """One marginal batch of one opportunity (SPEC §3.4D.3). It carries no risk field: generation ignores it (PF1).

    Money: gold parts and cost basis are integer copper; expected_net is float copper (§3.3).
    """

    strategy: str                                  # a STRATEGIES code
    key: int                                       # the output item's key
    char_id: str                                   # the acting character
    route: str                                     # route_text: each reagent's source, buy and sale timing
    batch: int                                     # 1-based: batch n + 1 is worth less than batch n
    out_qty: int                                   # output units
    inputs: tuple[tuple[int, int], ...] = ()       # (item key, units) it consumes, by key
    gold_buys: int = 0                             # copper spent buying the item itself (flips, buys for later)
    gold_mats: int = 0                             # copper spent buying mats
    gold_deposits: int = 0                         # deposits of its posts
    cost_basis: int = 0                            # what you paid for the bought stock it uses (found stock: 0)
    expected_net: float = 0.0                      # §3.4 expected net, float copper
    chance: float = 0.0                            # P' in [0, 1]
    exec_qty: int = 0                              # executable units: the liquidity room it fits in
    conf: int = 0                                  # confidence %
    needs: Requirements = field(default_factory=Requirements)
    buy_ms: int | None = None                      # when its buys happen; None without buys
    sale_ms: int | None = None                     # expected sale hour: the planned window's start, or now
    owned_claims: tuple[OwnedClaim, ...] = ()      # by (char, place, key)
    capacity_claims: tuple[CapacityClaim, ...] = ()   # by (key, window)
    built_ms: int = 0                              # the intelligence's built_ms
    method_versions: tuple[tuple[str, int], ...] = ()   # (method, version), by name
    window_end_ms: int | None = None               # when the selling window it sells in ends; None selling now (EX17)

    @property
    def cand_id(self) -> str:
        """The stable id (SPEC §3.4D.3).

        Returns:
            candidate_id of this candidate's strategy, key, character, route and batch.
        """
        return candidate_id(self.strategy, self.key, self.char_id, self.route, self.batch)

    @property
    def gold_needed(self) -> int:
        """Gold the acting character must have: its exposure (SPEC §3.4D.1).

        Returns:
            Buys + mats + deposits, copper.
        """
        return self.gold_buys + self.gold_mats + self.gold_deposits

    @property
    def label(self) -> str:
        """The Strategy column's text.

        Returns:
            E.g. "Buy for later".
        """
        return STRATEGIES[self.strategy]


def _check_part(name: str, value: str) -> None:
    """Refuse an id part that would make the id ambiguous.

    Args:
        name: The part's name, for the message.
        value: Its text.

    Raises:
        ValueError: When it is empty or holds a separator.
    """
    if not value or any(c in value for c in _ID_FORBIDDEN):
        raise ValueError(f"candidate {name} {value!r} must be non-empty without : ; , |")


def candidate_id(strategy: str, key: int, char_id: str, route: str, batch: int) -> str:
    """Build a candidate's stable id, `<strategy>:<key>:<charId>:<route>:<batch>` (SPEC §3.4D.3).

    It is unchanged between runs while those parts don't change, and nothing else
    (prices, risk tolerance, gold) goes into it (PF1).

    Args:
        strategy: A STRATEGIES code, e.g. "flip".
        key: The output item's key.
        char_id: The acting character, e.g. "Amaya-Grommash".
        route: From route_text.
        batch: 1-based batch number.

    Returns:
        E.g. "craft:6217:Brugan-Grommash:2840c/-/now:1".

    Raises:
        ValueError: An unknown strategy, a batch below 1, or a part holding a separator.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    if batch < 1:
        raise ValueError(f"batch must be 1 or more, got {batch}")
    _check_part("character", char_id)
    _check_part("route", route)
    return f"{strategy}:{key}:{char_id}:{route}:{batch}"


def timing_tag(at_ms: int | None, now_ms: int) -> str:
    """A buy or sale time as a route part that stays the same between runs.

    Args:
        at_ms: The time, UTC ms; None when there is none (no buys, or no sale).
        now_ms: The run's now.

    Returns:
        "-" for None, "now" when at or before now (so "now" doesn't change every run),
        else "h<hour>" with the hour number floor(ms / 3600000).
    """
    if at_ms is None:
        return "-"
    if at_ms <= now_ms:
        return "now"
    return f"h{at_ms // HOUR_MS}"


def route_text(sources: Mapping[int, Iterable[str]], buy: str, sale: str) -> str:
    """A candidate's route: each reagent's source, then when it buys and when it sells (SPEC §3.4D.3).

    Format `<reagents>/<buy>/<sale>`. Reagents are `<key><letters>` joined by "+", by key,
    the letters being the reagent's source kinds in the order o (owned), v (vendor),
    a (AH), c (crafted by you); "-" when it has no reagents. Buy and sale are timing_tag
    texts. Two routes to the same output (bars bought or smelted; post now or at a
    window) differ, so they are separate candidates (PF4).

    Args:
        sources: Reagent key -> its source kinds ("owned", "vendor", "ah", "crafted").
        buy: timing_tag of its buys.
        sale: timing_tag of its sale.

    Returns:
        E.g. "2840a+3371v/now/h491234", or "-/now/now" for a flip sold now.

    Raises:
        ValueError: An unknown source kind, or a reagent with none.
    """
    parts = []
    for key in sorted(sources):
        kinds = set(sources[key])
        if not kinds or not kinds <= SOURCE_LETTERS.keys():
            raise ValueError(f"reagent {key} has sources {sorted(kinds)}; expected some of {list(SOURCE_LETTERS)}")
        parts.append(f"{key}" + "".join(letter for kind, letter in SOURCE_LETTERS.items() if kind in kinds))
    return f"{'+'.join(parts) or '-'}/{buy}/{sale}"


def candidate_to_json(c: Candidate) -> str:
    """Serialize a candidate deterministically (sorted keys, no spaces), with its id and schema version.

    Args:
        c: The candidate.

    Returns:
        JSON text; the same candidate always gives the same bytes.
    """
    body = asdict(c)
    body["cand_id"] = c.cand_id
    body["method_versions"] = dict(c.method_versions)
    body["v"] = CANDIDATE_SCHEMA_VERSION
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def candidate_from_json(text: str) -> Candidate:
    """Read a candidate back.

    Args:
        text: From candidate_to_json.

    Returns:
        The candidate, equal to the one serialized.

    Raises:
        CandidateVersionError: Written with a newer schema version.
        ValueError: Its stored id doesn't match its parts.
        KeyError, TypeError: When it is malformed.
    """
    body = json.loads(text)
    version = body.pop("v")
    if version > CANDIDATE_SCHEMA_VERSION:
        raise CandidateVersionError(f"candidate from a newer AHSync (schema {version}, "
                                    f"this reads up to {CANDIDATE_SCHEMA_VERSION})")
    stored_id = body.pop("cand_id")
    body["inputs"] = tuple(tuple(pair) for pair in body["inputs"])
    body["needs"] = Requirements(**body["needs"])
    body["owned_claims"] = tuple(OwnedClaim(**claim) for claim in body["owned_claims"])
    body["capacity_claims"] = tuple(CapacityClaim(**claim) for claim in body["capacity_claims"])
    body["method_versions"] = tuple(sorted(body["method_versions"].items()))
    candidate = Candidate(**body)
    if candidate.cand_id != stored_id:
        raise ValueError(f"candidate id {stored_id!r} doesn't match its parts ({candidate.cand_id!r})")
    return candidate


def candidate_fields() -> tuple[str, ...]:
    """The stored fields of a candidate, for contract tests and the export (SPEC §5.0).

    Returns:
        Field names in declaration order.
    """
    return tuple(f.name for f in fields(Candidate))


def export_subset(candidates: Sequence[Candidate], funded_ids: Sequence[str], adjusted: Mapping[str, float],
                  limit: int) -> list[Candidate]:
    """The candidates the export keeps: the funded first, then the best alternatives (SPEC §3.4D.3, PF3, D.10).

    Args:
        candidates: The whole universe, any order.
        funded_ids: The funded candidates' ids in funding order.
        adjusted: Candidate id -> adjusted profit (float copper, §3.4D.2) for every unfunded candidate.
        limit: How many to keep (config candidate_export).

    Returns:
        At most `limit` candidates: the funded in funding order, then the others by adjusted
        profit, best first, ties by candidate id. The rest stay on the desktop.

    Raises:
        ValueError: A funded id that is not in the universe, or listed twice.
        KeyError: An unfunded candidate without an adjusted profit.
    """
    by_id = {c.cand_id: c for c in candidates}
    funded = set(funded_ids)
    if len(funded) != len(funded_ids) or not funded <= by_id.keys():
        raise ValueError("funded ids must be distinct candidates of the universe")
    rest = sorted((c for c in candidates if c.cand_id not in funded), key=lambda c: (-adjusted[c.cand_id], c.cand_id))
    return ([by_id[i] for i in funded_ids] + rest)[:limit]


# A route letter -> the export's source word (SPEC §3.5 `K` inputs).
SOURCE_WORDS: dict[str, str] = {"o": "own", "v": "vendor", "a": "ah", "c": "craft"}
# Strategies without reagents in their route: where their inputs come from.
_PLAIN_SOURCES: dict[str, str] = {"sell_stock": "own", "disenchant": "ah", "flip": "ah", "buy_later": "ah"}


def input_sources(c: Candidate) -> tuple[tuple[int, int, str], ...]:
    """Each input of a candidate with where it comes from (SPEC §3.5 `K` inputs, EX17).

    A craft's sources are its route's reagent letters; a reagent with several sources lists them joined by "+",
    in the order own, vendor, ah, craft. Sell stock uses your own units, a disenchant buys from the AH.

    Args:
        c: The candidate.

    Returns:
        (item key, units, source) per input, in the candidate's order (by key): source "own", "vendor", "ah",
        "craft" or a "+" join of them.
    """
    reagents = c.route.split("/")[0]
    letters: dict[int, str] = {}
    if reagents != "-":
        for part in reagents.split("+"):
            digits = part.rstrip("ovac")
            letters[int(digits)] = "+".join(SOURCE_WORDS[ch] for ch in part[len(digits):])
    plain = _PLAIN_SOURCES.get(c.strategy, "")
    return tuple((key, units, letters.get(key, plain)) for key, units in c.inputs)


# The scoring, eligibility and allocation settings the addon reads from PortfolioPlan.lua (SPEC §3.6 configVersion).
CONFIG_VERSION_KEYS = ("risk_tolerance", "premium_base", "premium_per_day", "immediate_hours", "top_buy_windows",
                       "top_sell_windows", "today_limit", "week_limit", "candidate_export", "horizon_days",
                       "candidate_pool", "swap_candidates")


def config_version(ps: PortfolioSettings) -> int:
    """A stable number that changes when any portfolio setting changes (SPEC §3.6 configVersion, EX18).

    Args:
        ps: The portfolio settings.

    Returns:
        CRC-32 (zlib.crc32, 0 to 2^32 - 1) of "key=value;..." over CONFIG_VERSION_KEYS in that order, each
        value in whole thousandths, floor(x × 1000 + 0.5); the same settings always give the same number.
    """
    text = ";".join(f"{name}={math.floor(getattr(ps, name) * 1000 + 0.5)}" for name in CONFIG_VERSION_KEYS)
    return zlib.crc32(text.encode("ascii"))


# ---------------------------------------------------------------- accounting (§3.4D.1)

@dataclass(frozen=True)
class CharacterGold:
    """One character's gold at its last snapshot, and its [budgets] override (SPEC §3.6)."""

    char_id: str
    gold: int                          # copper in hand
    budget: int | None = None          # config [budgets] copper; None = its gold


@dataclass(frozen=True)
class OpenPosition:
    """What accounting needs of an open position until positions are built (M14, SPEC §3.4D.9)."""

    position_id: str
    char_id: str
    cost_left: int                     # copper paid for the units still held, listed, in the mail or not paid out


@dataclass(frozen=True)
class Accounting:
    """The capital and the risk budget of one allocation run (SPEC §3.4D.1). Integer copper throughout."""

    cash_gold: int                                 # Σ characters' gold
    committed_capital: int                         # Σ open positions' cost left
    capital_base: int                              # cash gold + committed capital
    risk_budget: int                               # the exposure limit: capital base × risk tolerance
    remaining_capacity: int                        # max(0, risk budget − committed capital)
    reserved: int                                  # gold reserved by funded, unexecuted actions this run
    capacity_left: int                             # max(0, remaining capacity − reserved)
    spendable: tuple[tuple[str, int], ...]         # (character, spendable gold), by character
    reserved_by_char: tuple[tuple[str, int], ...]  # (character, reserved gold), by character; 0s left out
    available: int                                 # available new investment, all characters together

    def available_for(self, char_id: str) -> int:
        """Available new investment for one character's next row (SPEC §3.4D.1, PF23).

        Args:
            char_id: The acting character.

        Returns:
            min(capacity left, its spendable gold − its reserved gold), never below 0, copper;
            0 for a character with no gold record.
        """
        return available_investment(self.capacity_left, dict(self.spendable).get(char_id, 0),
                                    dict(self.reserved_by_char).get(char_id, 0))

    def spendable_left(self, char_id: str) -> int:
        """A character's spendable gold not yet reserved this run (SPEC §3.4D.1).

        Args:
            char_id: The character.

        Returns:
            Spendable − reserved, copper (negative only if more was reserved than it holds).
        """
        return dict(self.spendable).get(char_id, 0) - dict(self.reserved_by_char).get(char_id, 0)


def _non_negative(what: str, copper: int) -> int:
    """Refuse negative money (engine errors fail loudly).

    Args:
        what: The amount's name, for the message.
        copper: The amount.

    Returns:
        copper, unchanged.

    Raises:
        ValueError: When it is negative.
    """
    if copper < 0:
        raise ValueError(f"{what} must be 0 or more copper, got {copper}")
    return copper


def cash_gold(chars: Iterable[CharacterGold]) -> int:
    """Cash gold: every character's gold summed (SPEC §3.4D.1).

    Args:
        chars: The characters, each at its last snapshot.

    Returns:
        Copper.

    Raises:
        ValueError: A negative gold amount.
    """
    return sum(_non_negative(f"{c.char_id} gold", c.gold) for c in chars)


def spendable_gold(char: CharacterGold) -> int:
    """A character's spendable gold: its [budgets] value when set, else its gold (SPEC §3.4D.1).

    Args:
        char: The character.

    Returns:
        Copper.

    Raises:
        ValueError: A negative gold or budget.
    """
    if char.budget is not None:
        return _non_negative(f"{char.char_id} budget", char.budget)
    return _non_negative(f"{char.char_id} gold", char.gold)


def committed_capital(positions: Iterable[OpenPosition]) -> int:
    """Committed capital: the remaining cost basis of the open positions (SPEC §3.4D.1).

    Found stock opens no position (or one with cost 0), so it adds nothing (PF22).

    Args:
        positions: The open positions.

    Returns:
        Copper.

    Raises:
        ValueError: A negative cost left.
    """
    return sum(_non_negative(f"position {p.position_id} cost left", p.cost_left) for p in positions)


def risk_budget(capital_base: int, risk_tolerance: float) -> int:
    """The exposure limit: capital base × risk tolerance, rounded down to the copper (SPEC §3.4D.1).

    The tolerance is taken in whole thousandths, floor(x × 1000 + 0.5), and the product is
    integer, so 0.29 of 100 is 29 and not 28 from float error, the same in Lua.

    Args:
        capital_base: Copper.
        risk_tolerance: Share in (0, 1] (config risk_tolerance).

    Returns:
        floor(capital_base × permille ÷ 1000), copper.

    Raises:
        ValueError: A tolerance outside (0, 1], or a negative base.
    """
    if not 0 < risk_tolerance <= 1:
        raise ValueError(f"risk tolerance must be in (0, 1], got {risk_tolerance}")
    permille = math.floor(risk_tolerance * 1000 + 0.5)
    return _non_negative("capital base", capital_base) * permille // 1000


def remaining_capacity(budget: int, committed: int) -> int:
    """What the risk budget leaves for new positions (SPEC §3.4D.1).

    Args:
        budget: The risk budget, copper.
        committed: Committed capital, copper.

    Returns:
        max(0, budget − committed), copper: never negative (PF8).
    """
    return max(0, budget - committed)


def available_investment(capacity_left: int, spendable: int, reserved: int) -> int:
    """Available new investment (SPEC §3.4D.1, PF8, PF23).

    Args:
        capacity_left: The remaining capacity less the gold already reserved this run, copper.
        spendable: Spendable gold (one character's, or all of them together), copper.
        reserved: Of it, the gold reserved by funded actions not yet executed, deposits included, copper.

    Returns:
        max(0, min(capacity_left, spendable − reserved)), copper.
    """
    return max(0, min(capacity_left, spendable - reserved))


def committed_share_pct(a: Accounting) -> int | None:
    """Committed capital as a whole percent of the capital base, half up (PF8: 41,800 ÷ 355,800 → 12).

    Args:
        a: The accounting.

    Returns:
        floor(committed × 100 ÷ base + 0.5) in integer arithmetic; None when the base is 0.
    """
    if a.capital_base == 0:
        return None
    return (a.committed_capital * 200 + a.capital_base) // (2 * a.capital_base)


def cash_of(c: Candidate) -> int:
    """A row's cash for profit %: its gold needed plus the cost basis of bought stock it uses (SPEC §3.4D.8).

    Args:
        c: The candidate.

    Returns:
        Copper; 0 for a row that uses only found stock and needs no gold.
    """
    return c.gold_needed + c.cost_basis


def profit_pct(expected_net: float, cash: int) -> int | None:
    """Profit % = expected net ÷ cash, a whole percent, half up (SPEC §3.4D.8, PF21). Never a ranking key.

    Args:
        expected_net: Expected net, float copper.
        cash: Gold needed + cost basis of bought stock used, copper.

    Returns:
        floor(net × 100 ÷ cash + 0.5); None when the cash is 0 (shown "—").
    """
    if cash <= 0:
        return None
    return math.floor(expected_net * 100 / cash + 0.5)


def pool_profit_pct(rows: Iterable[tuple[float, int]]) -> int | None:
    """Profit % of several rows: their nets ÷ their cash, leaving out rows with no cash (SPEC §3.4D.8, PF21).

    Args:
        rows: (expected net float copper, cash copper) per row.

    Returns:
        profit_pct of the sums over the rows whose cash is above 0; None when there are none.
    """
    net, cash = 0.0, 0
    for row_net, row_cash in rows:
        if row_cash > 0:
            net += row_net
            cash += row_cash
    return profit_pct(net, cash)


def accounting(chars: Sequence[CharacterGold], positions: Sequence[OpenPosition], risk_tolerance: float,
               reserved: Mapping[str, int] | None = None) -> Accounting:
    """The capital and risk budget of one allocation run (SPEC §3.4D.1, PF8, PF22, PF23).

    Args:
        chars: Your characters with their gold and budgets.
        positions: The open positions (their cost left).
        risk_tolerance: Config risk_tolerance, a share in (0, 1].
        reserved: Character id -> copper reserved by its funded, unexecuted actions in this run
            (deposits included); None or empty at the start of a run.

    Returns:
        The Accounting.

    Raises:
        ValueError: Negative money, a tolerance outside (0, 1], or two records for one character.
    """
    if len({c.char_id for c in chars}) != len(chars):
        raise ValueError("each character may have one gold record")
    held = {char_id: _non_negative(f"{char_id} reserved", copper) for char_id, copper in (reserved or {}).items()}
    cash = cash_gold(chars)
    committed = committed_capital(positions)
    base = cash + committed
    budget = risk_budget(base, risk_tolerance)
    capacity = remaining_capacity(budget, committed)
    total_reserved = sum(held.values())
    left = max(0, capacity - total_reserved)
    spendable = tuple(sorted((c.char_id, spendable_gold(c)) for c in chars))
    return Accounting(cash, committed, base, budget, capacity, total_reserved, left, spendable,
                      tuple(sorted((k, v) for k, v in held.items() if v)),
                      available_investment(left, sum(s for _, s in spendable), total_reserved))
