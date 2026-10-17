"""Load and validate `config.toml` (SPEC §3.6) and find the API key.

A bad config refuses to start with the line and the reason (SPEC §5.2, FM16).
The API key is looked up in this order: env `AUCTIONHOUSE_APIKEY`, then
`%USERPROFILE%/.ahsync/api_key`, then the config's `api_key` value. It never
appears in the `Config` repr or in any error message.
"""
from __future__ import annotations

import dataclasses
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ahsync.engine.sellprob import MarketSettings

API_KEY_ENV = "AUCTIONHOUSE_APIKEY"
MARKET_PATTERN = re.compile(r"^forever\.(normal|pvp|rp)\.(alliance|horde)\.us$")
RULESETS = ("normal", "pvp", "rp")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ConfigError(Exception):
    """A config.toml problem, worded for the user: "config.toml line 4: retention_days must be a number"."""


@dataclass(frozen=True)
class Config:
    """Every setting from SPEC §3.6, with its default. Money in copper, shares as 0..1 fractions."""

    markets: tuple[str, ...] = ("forever.pvp.horde.us",)
    wow_path: str = "C:/Program Files (x86)/World of Warcraft/_classic_beta_"
    retention_days: int = 90
    dashboard_port: int = 8765
    live_refresh_minutes: int = 15
    min_flip_margin: float = 0.20
    default_sell_prob: float = 0.25
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
    pattern_half_life_weeks: float = 4
    window_threshold: float = 0.90
    peak_tolerance: float = 0.10
    week_start: str = "Mon"
    max_craft_depth: int = 3
    max_market_share: float = 0.5
    max_actions_per_day: int = 0
    launch_wow_after_catchup: bool = False
    realms: Mapping[str, str] = field(default_factory=dict)
    budgets: Mapping[str, int] = field(default_factory=dict)
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
    "realms": ((dict,), "a table"),
    "budgets": ((dict,), "a table"),
}
for _name in ("retention_days", "dashboard_port", "live_refresh_minutes", "own_record_days",
              "struggle_streak", "keep_retry_streak", "post_lead_hours", "sell_window_hours",
              "max_craft_depth", "max_actions_per_day"):
    _TYPES[_name] = _INT
for _name in ("min_flip_margin", "default_sell_prob", "default_undercut_share", "own_record_weight",
              "reprice_step", "min_sell_prob", "high_risk_pct", "pattern_half_life_weeks",
              "window_threshold", "peak_tolerance", "max_market_share"):
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
        raise _fail(text, name, "is not a known setting")
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
    for name in ("retention_days", "dashboard_port", "live_refresh_minutes", "post_lead_hours", "sell_window_hours"):
        if name in values and values[name] <= 0:
            raise _fail(text, name, "must be greater than 0")
    if "daily_refresh_time" in values and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", values["daily_refresh_time"]):
        raise _fail(text, "daily_refresh_time", 'must be a time like "06:00"')
    if "week_start" in values and values["week_start"] not in WEEKDAYS:
        raise _fail(text, "week_start", "must be one of " + ", ".join(WEEKDAYS))
    for realm, ruleset in values.get("realms", {}).items():
        if ruleset not in RULESETS:
            raise ConfigError(f"config.toml [realms]: {realm} must be one of normal, pvp, rp")
    for char_id, budget in values.get("budgets", {}).items():
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ConfigError(f"config.toml [budgets]: {char_id} must be a whole number of copper, 0 or more")


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


def market_settings(config: Config) -> MarketSettings:
    """The settings market-wide analysis needs, taken from the config (analysis never reads config itself, AR4).

    Args:
        config: Settings.

    Returns:
        The engine's MarketSettings.
    """
    return MarketSettings(config.default_sell_prob, config.own_record_weight, config.default_undercut_share,
                          config.sell_window_hours, config.pattern_half_life_weeks, config.window_threshold)

