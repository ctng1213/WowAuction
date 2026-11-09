"""Load and validate `config.toml` (SPEC §3.6) and find the API key.

A bad config refuses to start with the line and the reason (SPEC §5.2, FM16).
The API key is looked up in this order: env `AUCTIONHOUSE_APIKEY`, then
`%USERPROFILE%/.ahsync/api_key`, then the config's `api_key` value. It never
appears in the `Config` repr or in any error message.
"""
from __future__ import annotations

import dataclasses
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import PlanSettings
from ahsync.engine.portfolio import PortfolioSettings
from ahsync.engine.sell_record import RecordSettings
from ahsync.engine.sellprob import MarketSettings
from ahsync.engine.sessions import SessionSettings
from ahsync.engine.volume import VolumePrior

API_KEY_ENV = "AUCTIONHOUSE_APIKEY"
MARKET_PATTERN = re.compile(r"^forever\.(normal|pvp|rp)\.(alliance|horde)\.us$")
CLOUD_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")   # an origin only: SEC16
RULESETS = ("normal", "pvp", "rp")
CHANNELS = ("stable", "beta")                 # update_channel (SPEC §3.7.10)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class MarketSources:
    """Where a market's data comes from (SPEC §3.6 [sources], §5.0): the market id stays AHSync's own key."""

    primary: str = "ahledger"                  # the remote source Status and freshness follow (SRC10)
    rank: tuple[str, ...] = ("ahf", "ahledger")  # remote sources for prices, most trusted first (your scans come first)
    addresses: Mapping[str, str] = field(default_factory=dict)   # provider -> this market's id there ("" = not used)


@dataclass(frozen=True)
class CloudConfig:
    """Where the published market summaries are (SPEC §2.4, §3.6 [cloud]); empty intel_url = no cloud market."""

    intel_url: str = ""                       # the summaries' origin, https://<distribution>.cloudfront.net
    poll_minutes: int = 15                    # how often AHSync asks for a newer summary, 5-60


class ConfigError(Exception):
    """A config.toml problem, worded for the user: "config.toml line 4: retention_days must be a number"."""


@dataclass(frozen=True)
class Config:
    """Every setting from SPEC §3.6, with its default. Money in copper, shares as 0..1 fractions."""

    markets: tuple[str, ...] = ("forever.pvp.horde.us",)
    wow_path: str = "C:/Program Files (x86)/World of Warcraft/_forever_"
    retention_days: int = 90
    dashboard_port: int = 8765
    mcp_enabled: bool = False                   # the AI assistant server runs (SPEC §3.8.1); set by the Status card
    mcp_port: int = 8766                        # its 127.0.0.1 port, 1024-65535, not dashboard_port (SPEC §3.8.2)
    live_refresh_minutes: int = 15
    min_flip_margin: float = 0.20
    default_sell_prob: float = 0.25
    default_sell_prob_cheapest: float = 0.5
    volume_prior_per_day: float = 1.0
    volume_prior_hours: float = 2
    default_undercut_share: float = 0.5
    own_record_days: int = 30
    own_record_weight: float = 4
    struggle_streak: int = 3
    reprice_step: float = 0.05
    keep_retry_streak: int = 1
    min_sell_prob: float = 0.5
    high_risk_pct: float = 25
    daily_refresh_time: str = "06:00"
    post_lead_hours: int = 8
    sell_window_hours: int = 8
    pattern_half_life_weeks: float = 2           # 2 while Forever's economy settles; back to 4 around Jan 2027 (SPEC §3.3.4, M38)
    window_threshold: float = 0.90
    peak_tolerance: float = 0.10
    week_start: str = "Mon"
    max_craft_depth: int = 3
    max_supply_share: float = 0.5
    max_supply_share_gear: float = 1.0
    min_gear_units: int = 2
    window_margin: float = 0.15
    pattern_full_weeks: int = 6
    max_trend_total: float = 0.15
    max_actions_per_day: int = 0
    risk_tolerance: float = 0.30               # the portfolio optimizer's exposure budget (§3.4D.1)
    premium_base: float = 0.15
    premium_per_day: float = 0.05
    immediate_hours: int = 8
    top_buy_windows: int = 3
    top_sell_windows: int = 3
    today_limit: int = 20
    week_limit: int = 20
    candidate_export: int = 150
    horizon_days: int = 7
    candidate_pool: int = 400                   # buys kept past their first batch (§3.4D.3), ≥ candidate_export
    swap_candidates: int = 50                   # excluded batches the swap pass tries (§3.4D.4)
    session_confirm_units: int = 20             # gathered units that confirm a farming session, 1-1000 (§3.4E.4)
    session_gap_minutes: int = 15               # minutes without loot that end a farming session, 1-120
    timing_edge_min_strength: int = 40          # Gathering shows a sell window from this seasonality strength, 0-100
    launch_wow_after_catchup: bool = False
    ahledger_backfill: bool = True              # fill missing hours from AHledger's per-item history (SPEC §2.3, M21; SRC10, SRC15)
    update_check: bool = True                   # ask GitHub Releases for a newer AHSync once a day (SPEC §3.7.8)
    auto_update: bool = True                    # download, verify and install it; false: only say it is out (§3.7.9)
    update_channel: str = "stable"              # "stable", or "beta" to take pre-releases too (§3.7.10)
    realms: Mapping[str, str] = field(default_factory=dict)
    budgets: Mapping[str, int] = field(default_factory=dict)
    sources: Mapping[str, MarketSources] = field(default_factory=dict)   # market id -> its sources (SRC1-SRC4)
    cloud: CloudConfig = field(default_factory=CloudConfig)               # the cloud market's summaries (SPEC §2.4)
    api_key: str = field(default="", repr=False)   # resolved key; never printed


# Setting name -> (accepted Python types, wording for the error message).
_INT = ((int,), "a number")
_NUMBER = ((int, float), "a number")
_TYPES: dict[str, tuple[tuple[type, ...], str]] = {
    "api_key": ((str,), "a string"),
    "markets": ((list,), "a list of market ids"),
    "wow_path": ((str,), "a string"),
    "daily_refresh_time": ((str,), 'a time like "06:00"'),
    "week_start": ((str,), "one of Mon, Tue, Wed, Thu, Fri, Sat, Sun"),
    "launch_wow_after_catchup": ((bool,), "true or false"),
    "ahledger_backfill": ((bool,), "true or false"),
    "update_check": ((bool,), "true or false"),
    "mcp_enabled": ((bool,), "true or false"),
    "auto_update": ((bool,), "true or false"),
    "update_channel": ((str,), "stable or beta"),
    "realms": ((dict,), "a table"),
    "budgets": ((dict,), "a table"),
    "sources": ((dict,), "a table"),
    "cloud": ((dict,), "a table"),
}
for _name in ("retention_days", "dashboard_port", "mcp_port", "live_refresh_minutes", "own_record_days",
              "struggle_streak", "keep_retry_streak", "post_lead_hours", "sell_window_hours",
              "max_craft_depth", "max_actions_per_day", "min_gear_units", "pattern_full_weeks",
              "immediate_hours", "top_buy_windows", "top_sell_windows", "today_limit", "week_limit",
              "candidate_export", "horizon_days", "candidate_pool", "swap_candidates", "session_confirm_units",
              "session_gap_minutes", "timing_edge_min_strength"):
    _TYPES[_name] = _INT
for _name in ("min_flip_margin", "default_sell_prob", "default_sell_prob_cheapest", "volume_prior_per_day",
              "volume_prior_hours", "default_undercut_share", "own_record_weight",
              "reprice_step", "min_sell_prob", "high_risk_pct", "pattern_half_life_weeks",
              "window_threshold", "peak_tolerance", "max_supply_share", "max_supply_share_gear",
              "window_margin", "max_trend_total", "risk_tolerance", "premium_base", "premium_per_day"):
    _TYPES[_name] = _NUMBER


def setting_line(text: str, name: str) -> int | None:
    """Find the line that sets a top-level key, for error messages.

    Args:
        text: The config file's contents.
        name: A top-level setting name such as "retention_days".

    Returns:
        1-based line number of `name = ...` before the first [table], or None if absent.
    """
    pattern = re.compile(rf'^\s*"?{re.escape(name)}"?\s*=')
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("["):
            return None
        if pattern.match(line):
            return number
    return None


def _fail(text: str, name: str, reason: str) -> ConfigError:
    """Build a ConfigError that names the setting's line.

    Args:
        text: The config file's contents.
        name: The setting at fault.
        reason: What is wrong, e.g. "must be a number".

    Returns:
        "config.toml line N: name reason", or without the line when it can't be found.
    """
    line = setting_line(text, name)
    where = f"config.toml line {line}" if line else "config.toml"
    return ConfigError(f"{where}: {name} {reason}")


# Settings that were removed -> what to say when an old config.toml still has one.
REMOVED = {"max_risk_pct": "was removed: risk_tolerance caps all the gold tied up at once instead (SPEC §3.4D.1)"}


def _check_type(text: str, name: str, value: object) -> None:
    """Check one top-level setting's type.

    Args:
        text: The config file's contents (for the line number).
        name: Setting name.
        value: Parsed TOML value.

    Raises:
        ConfigError: Unknown setting or wrong type ("must be a number"; a fraction
            where a count is needed gives "must be a whole number"). bool is not a number.
    """
    if name not in _TYPES:
        raise _fail(text, name, REMOVED.get(name, "is not a known setting"))
    types, wording = _TYPES[name]
    if types == (int,) and isinstance(value, float):
        raise _fail(text, name, "must be a whole number")
    if isinstance(value, bool) and bool not in types:
        raise _fail(text, name, f"must be {wording}")
    if not isinstance(value, types):
        raise _fail(text, name, f"must be {wording}")


def _check_values(text: str, values: dict) -> None:
    """Check ranges and formats that a type check can't catch.

    Args:
        text: The config file's contents (for line numbers).
        values: Parsed top-level settings, types already checked.

    Raises:
        ConfigError: The first invalid value found.
    """
    for market in values.get("markets", []):
        if not isinstance(market, str) or not MARKET_PATTERN.match(market):
            raise _fail(text, "markets", f"has {market!r}; expected forever.<normal|pvp|rp>.<alliance|horde>.us")
    if "markets" in values and not values["markets"]:
        raise _fail(text, "markets", "must list at least one market")
    for name in ("retention_days", "dashboard_port", "live_refresh_minutes", "post_lead_hours", "sell_window_hours",
                 "pattern_full_weeks"):
        if name in values and values[name] <= 0:
            raise _fail(text, name, "must be greater than 0")
    for name in ("default_sell_prob", "default_sell_prob_cheapest"):
        if name in values and not 0 <= values[name] <= 1:
            raise _fail(text, name, "must be between 0 and 1")
    for name in ("volume_prior_per_day", "volume_prior_hours", "window_margin", "max_trend_total", "premium_base",
                 "premium_per_day"):
        if name in values and values[name] < 0:
            raise _fail(text, name, "must be 0 or more")
    _check_portfolio(text, values)
    if "daily_refresh_time" in values and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", values["daily_refresh_time"]):
        raise _fail(text, "daily_refresh_time", 'must be a time like "06:00"')
    if "week_start" in values and values["week_start"] not in WEEKDAYS:
        raise _fail(text, "week_start", "must be one of " + ", ".join(WEEKDAYS))
    if "mcp_port" in values and not 1024 <= values["mcp_port"] <= 65535:
        raise _fail(text, "mcp_port", "must be between 1024 and 65535")
    if values.get("mcp_port", 8766) == values.get("dashboard_port", 8765):
        if "mcp_port" in values:
            raise _fail(text, "mcp_port", "must differ from dashboard_port")
        raise _fail(text, "dashboard_port", "must differ from mcp_port (8766 unless set)")
    if "update_channel" in values and values["update_channel"] not in CHANNELS:
        raise _fail(text, "update_channel", "must be stable or beta")
    for realm, ruleset in values.get("realms", {}).items():
        if ruleset not in RULESETS:
            raise ConfigError(f"config.toml [realms]: {realm} must be one of normal, pvp, rp")
    for char_id, budget in values.get("budgets", {}).items():
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ConfigError(f"config.toml [budgets]: {char_id} must be a whole number of copper, 0 or more")
    for market, table in values.get("sources", {}).items():
        _check_sources(market, table)
    if "cloud" in values:
        _check_cloud(values["cloud"])


# Whole-number portfolio settings -> (lowest, highest) allowed (SPEC §3.6); None = no upper bound.
_PORTFOLIO_RANGES: dict[str, tuple[int, int | None]] = {
    "top_buy_windows": (1, 10), "top_sell_windows": (1, 10), "today_limit": (1, 100), "week_limit": (1, 100),
    "candidate_export": (20, None), "swap_candidates": (1, None),
    "session_confirm_units": (1, 1000), "session_gap_minutes": (1, 120),     # Gathering (§3.4E.4), not configVersion
    "timing_edge_min_strength": (0, 100),                                    # Gathering (§3.4E.7), not configVersion

}


def _check_portfolio(text: str, values: dict) -> None:
    """Check the portfolio optimizer's settings (SPEC §3.6, §3.4D).

    Args:
        text: The config file's contents (for line numbers).
        values: Parsed top-level settings, types already checked.

    Raises:
        ConfigError: risk_tolerance outside (0, 1], immediate_hours other than 8 or 24, horizon_days
            other than 7 (fixed for now), candidate_pool below candidate_export, or a count outside its range.
    """
    if "risk_tolerance" in values and not 0 < values["risk_tolerance"] <= 1:
        raise _fail(text, "risk_tolerance", "must be more than 0 and at most 1")
    if "immediate_hours" in values and values["immediate_hours"] not in (8, 24):
        raise _fail(text, "immediate_hours", "must be 8 or 24")
    if "horizon_days" in values and values["horizon_days"] != 7:
        raise _fail(text, "horizon_days", "must be 7 (the horizon is fixed for now)")
    pool = values.get("candidate_pool", Config.candidate_pool)
    if pool < values.get("candidate_export", Config.candidate_export):
        raise _fail(text, "candidate_pool", "must be at least candidate_export")
    for name, (low, high) in _PORTFOLIO_RANGES.items():
        if name in values and high is None and values[name] < low:
            raise _fail(text, name, f"must be {low} or more")
        if name in values and high is not None and not low <= values[name] <= high:
            raise _fail(text, name, f"must be between {low} and {high}")


def _check_cloud(table: dict) -> None:
    """Check the [cloud] table (SPEC §3.6, SEC16).

    Args:
        table: Its parsed value.

    Raises:
        ConfigError: An unknown key, an intel_url that is neither "" nor https:// (no path, query or credentials),
            or poll_minutes outside 5-60.
    """
    where = "config.toml [cloud]"
    for name, value in table.items():
        if name == "intel_url":
            if not isinstance(value, str) or (value and not CLOUD_URL.match(value)):
                raise ConfigError(f"{where}: intel_url must be \"\" or https://<host> with nothing after the host")
        elif name == "poll_minutes":
            if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 60:
                raise ConfigError(f"{where}: poll_minutes must be a whole number between 5 and 60")
        else:
            raise ConfigError(f"{where}: {name} is not a known setting (intel_url, poll_minutes)")


def _check_sources(market: str, table: object) -> None:
    """Check one market's [sources] table (SRC4).

    Args:
        market: The table's market id.
        table: Its parsed value.

    Raises:
        ConfigError: A bad market id, an unknown key, or a value of the wrong type.
    """
    where = f"config.toml [sources.\"{market}\"]"
    if not MARKET_PATTERN.match(market):
        raise ConfigError(f"{where}: expected a market id like forever.pvp.horde.us")
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: must be a table")
    for name, value in table.items():
        ok = {"primary": lambda v: isinstance(v, str) and v,
              "rank": lambda v: isinstance(v, list) and all(isinstance(s, str) and s for s in v),
              "addresses": lambda v: isinstance(v, dict) and all(isinstance(s, str) for s in v.values())}.get(name)
        if ok is None:
            raise ConfigError(f"{where}: {name} is not a known setting (primary, rank, addresses)")
        if not ok(value):
            raise ConfigError(f"{where}: {name} must be " + {"primary": "a source name", "rank": "a list of source names",
                                                             "addresses": "a table of strings"}[name])


def resolve_api_key(env: Mapping[str, str], home: Path, config_value: str) -> str:
    """Find the API key in SPEC §3.6 order: env var, key file, config value.

    Args:
        env: Environment variables (os.environ in the app).
        home: The user's home folder (%USERPROFILE%).
        config_value: `api_key` from config.toml, "" when unset.

    Returns:
        The key with surrounding whitespace removed, or "" when there is none.
    """
    key = env.get(API_KEY_ENV, "").strip()
    if key:
        return key
    key_file = home / ".ahsync" / "api_key"
    if key_file.is_file():
        key = key_file.read_text(encoding="utf-8").strip()
        if key:
            return key
    return config_value.strip()


def parse_config(text: str, env: Mapping[str, str], home: Path) -> Config:
    """Validate config.toml contents and build a Config.

    Args:
        text: The file's contents ("" means all defaults).
        env: Environment variables, for the API key.
        home: The user's home folder, for the key file.

    Returns:
        The validated Config, with `api_key` resolved.

    Raises:
        ConfigError: Bad TOML, unknown setting, wrong type or invalid value,
            with the line and the reason. The key's value is never included.
    """
    try:
        values = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        match = re.search(r"at line (\d+)", str(error))
        where = f"config.toml line {match.group(1)}" if match else "config.toml"
        reason = re.sub(r"\s*\(at line \d+, column \d+\)", "", str(error))
        raise ConfigError(f"{where}: {reason}") from None
    for name, value in values.items():
        _check_type(text, name, value)
    _check_values(text, values)
    settings = {k: v for k, v in values.items() if k != "api_key"}
    if "markets" in settings:
        settings["markets"] = tuple(settings["markets"])
    if "cloud" in settings:
        settings["cloud"] = CloudConfig(**settings["cloud"])
    if "sources" in settings:
        settings["sources"] = {market: MarketSources(**{k: tuple(v) if k == "rank" else v for k, v in table.items()})
                               for market, table in settings["sources"].items()}
    api_key = resolve_api_key(env, home, values.get("api_key", ""))
    return dataclasses.replace(Config(), **settings, api_key=api_key)


def load_config(path: Path, env: Mapping[str, str], home: Path) -> Config:
    """Read config.toml; a missing file means all defaults.

    Args:
        path: Location of config.toml.
        env: Environment variables, for the API key.
        home: The user's home folder, for the key file.

    Returns:
        The validated Config.

    Raises:
        ConfigError: As for parse_config, or when the file isn't UTF-8.
    """
    if not path.exists():
        return parse_config("", env, home)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ConfigError("config.toml: the file must be saved as UTF-8") from None
    return parse_config(text, env, home)


def with_setting(text: str, name: str, formatted: str) -> str:
    """config.toml's text with only one top-level setting changed (SPEC §3.5; SEC11, SEC15).

    Every other line, comment and line ending is kept; the line's own comment and spacing too.

    Args:
        text: The file's contents.
        name: The top-level key, e.g. "risk_tolerance".
        formatted: Its new value as TOML, e.g. "0.40", "false" or '"beta"'.

    Returns:
        The new contents: the top-level `name = …` line rewritten, or, when there is none, the line added before the
        first [table] (top-level keys must come first in TOML), else at the end.
    """
    pattern = re.compile(rf'^(\s*"?{re.escape(name)}"?\s*=\s*)([^#]*?)(\s*(?:#.*)?)$')
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if body.lstrip().startswith("["):
            lines.insert(i, f"{name} = {formatted}{newline}")
            return "".join(lines)
        match = pattern.match(body)
        if match:
            lines[i] = match.group(1) + formatted + match.group(3) + line[len(body):]
            return "".join(lines)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    return "".join(lines) + f"{name} = {formatted}{newline}"


def with_risk_tolerance(text: str, value: float) -> str:
    """config.toml's text with only its risk_tolerance changed (SPEC §3.5 POST /api/risk_tolerance, SEC11).

    Args:
        text: The file's contents.
        value: The new share in (0, 1], written with 2 decimals ("0.40").

    Returns:
        with_setting()'s text.
    """
    return with_setting(text, "risk_tolerance", f"{value:.2f}")


def write_setting(path: Path, name: str, value: object, formatted: str) -> None:
    """Rewrite one top-level line of config.toml atomically: temp file, check, rename (SPEC §3.5, §5.2; SEC11, SEC15).

    Args:
        path: config.toml (a missing file is created with just this line).
        name: The key.
        value: The value it must read back as.
        formatted: The value as TOML.

    Raises:
        OSError: The file couldn't be read or replaced; it is left as it was.
        ConfigError: The rewritten text wouldn't read back with this value (the file is left as it was).
    """
    text = ""
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            text = f.read()
    new = with_setting(text, name, formatted)
    try:
        written = tomllib.loads(new).get(name)
    except tomllib.TOMLDecodeError:
        written = None
    if written != value or type(written) is not type(value):
        raise ConfigError(f"config.toml: {name} could not be rewritten")
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as f:
        f.write(new)
    os.replace(temp, path)


def write_risk_tolerance(path: Path, value: float) -> None:
    """Rewrite config.toml's risk_tolerance line atomically (SPEC §3.5, §5.2, SEC11).

    Args:
        path: config.toml (a missing file is created with just this line).
        value: The new share in (0, 1].

    Raises:
        OSError: The file couldn't be read or replaced; it is left as it was.
        ConfigError: The rewritten text wouldn't read back with this value (the file is left as it was).
    """
    write_setting(path, "risk_tolerance", float(f"{value:.2f}"), f"{value:.2f}")


def market_sources(config: Config, market: str) -> MarketSources:
    """A market's sources, with defaults: AHF and AHledger under the market's own id, AHledger primary (SRC1, SRC10).

    Args:
        config: The config.
        market: Market id.

    Returns:
        Its MarketSources. `addresses` always says where AHF and AHledger are ("" when this market doesn't
        use that provider). When the configured primary's address is "", the primary falls back to the first
        ranked source with an address, so Status never follows a source that is never polled.
    """
    own = config.sources.get(market, MarketSources())
    addresses = {"ahf": market, "ahledger": market, **own.addresses}
    primary = own.primary
    if not addresses.get(primary, primary):
        primary = next((s for s in own.rank if addresses.get(s, s)), primary)
    return dataclasses.replace(own, primary=primary, addresses=addresses)


def is_cloud_market(config: Config, market: str) -> bool:
    """Whether a market's intelligence comes from the cloud's published summary (SPEC §2.4, §3.1).

    Args:
        config: Settings.
        market: Market id.

    Returns:
        True when [cloud] intel_url is set and the market ranks the blizzard source.
    """
    return bool(config.cloud.intel_url) and "blizzard" in market_sources(config, market).rank


# Analysis settings the plan also reads (plan_settings, portfolio_settings): a cloud document's values win for
# its market, so the plan and the windows it was built on agree (SPEC §2.4 "settings", BZ17).
SHARED_SETTINGS = ("default_sell_prob", "default_sell_prob_cheapest", "default_undercut_share", "own_record_weight",
                   "sell_window_hours", "max_supply_share", "max_supply_share_gear", "min_gear_units",
                   "max_trend_total", "top_buy_windows", "top_sell_windows")


def with_document_settings(config: Config, settings: Mapping[str, object]) -> tuple[Config, dict[str, tuple]]:
    """The config with a cloud document's analysis settings for the keys the plan shares with analysis (BZ17).

    Args:
        config: The local settings.
        settings: The document's `settings` ({} for a desktop-built document).

    Returns:
        (the config to plan with, key -> (document's value, local value) for each key that differs).
    """
    shared = {k: settings[k] for k in SHARED_SETTINGS if k in settings}
    differs = {k: (v, getattr(config, k)) for k, v in shared.items() if getattr(config, k) != v}
    return (dataclasses.replace(config, **shared) if shared else config), differs


def market_settings(config: Config) -> MarketSettings:
    """The settings market-wide analysis needs, taken from the config (analysis never reads config itself, AR4).

    Args:
        config: Settings.

    Returns:
        The engine's MarketSettings.
    """
    return MarketSettings(config.default_sell_prob, config.own_record_weight, config.default_undercut_share,
                          config.sell_window_hours, config.pattern_half_life_weeks, config.window_threshold,
                          config.default_sell_prob_cheapest,
                          VolumePrior(config.volume_prior_per_day, config.volume_prior_hours),
                          config.max_supply_share, config.max_supply_share_gear, config.min_gear_units,
                          config.max_trend_total, config.top_buy_windows, config.top_sell_windows)



def plan_settings(config: Config) -> PlanSettings:
    """The settings the weekly plan needs (SPEC §3.4C), from the config.

    Args:
        config: Settings.

    Returns:
        The engine's PlanSettings.
    """
    return PlanSettings(config.post_lead_hours, config.max_supply_share, config.max_supply_share_gear,
                        config.min_gear_units, config.sell_window_hours, 7, config.default_undercut_share,
                        config.default_sell_prob, config.reprice_step, config.own_record_weight,
                        config.default_sell_prob_cheapest, config.window_margin, config.pattern_full_weeks,
                        config.max_trend_total)


def session_settings(config: Config) -> SessionSettings:
    """How farming sessions are cut (SPEC §3.4E.4, §3.6); not part of configVersion.

    Args:
        config: Settings.

    Returns:
        The engine's SessionSettings.
    """
    return SessionSettings(config.session_confirm_units, config.session_gap_minutes)


def portfolio_settings(config: Config) -> PortfolioSettings:
    """The settings the portfolio optimizer needs (SPEC §3.4D, §3.6), from the config.

    Args:
        config: Settings.

    Returns:
        The engine's PortfolioSettings.
    """
    return PortfolioSettings(config.risk_tolerance, config.premium_base, config.premium_per_day,
                             config.immediate_hours, config.top_buy_windows, config.top_sell_windows,
                             config.today_limit, config.week_limit, config.candidate_export, config.horizon_days,
                             config.candidate_pool, config.swap_candidates)


def record_settings(config: Config) -> RecordSettings:
    """The settings your own sell record needs (SPEC §3.3.3), from the config.

    Args:
        config: Settings.

    Returns:
        The engine's RecordSettings.
    """
    return RecordSettings(config.own_record_days, config.struggle_streak, config.keep_retry_streak)


def flip_settings(config: Config) -> FlipSettings:
    """The settings flips and buys need (SPEC §3.4C.2), from the config.

    Args:
        config: Settings.

    Returns:
        The engine's FlipSettings.
    """
    return FlipSettings(config.min_flip_margin)
