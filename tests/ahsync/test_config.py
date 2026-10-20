"""config.toml loading (SPEC §3.6) and FM16."""
from __future__ import annotations

import pytest

from ahsync.config import (Config, ConfigError, load_config, market_settings, parse_config, plan_settings,
                           portfolio_settings, resolve_api_key, setting_line, with_risk_tolerance,
                           write_risk_tolerance)
from ahsync.engine.portfolio import PortfolioSettings

EXAMPLE = '''api_key = ""
markets = ["forever.pvp.horde.us"]
wow_path = "C:/Program Files (x86)/World of Warcraft/_classic_beta_"
retention_days = "ninety"
'''


def test_FM16_config_with_text_retention_days_refuses_to_start_with_line_and_reason(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE, encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(path, {}, tmp_path)
    assert str(error.value) == "config.toml line 4: retention_days must be a number"


def test_missing_file_gives_spec_defaults(tmp_path):
    config = load_config(tmp_path / "config.toml", {}, tmp_path)
    assert config == Config()
    assert config.markets == ("forever.pvp.horde.us",) and config.retention_days == 90
    assert config.daily_refresh_time == "06:00" and config.live_refresh_minutes == 15


def test_values_and_tables_are_read(tmp_path):
    text = ('markets = ["forever.normal.alliance.us"]\nretention_days = 60\nmin_flip_margin = 1\n'
            'launch_wow_after_catchup = true\n[realms]\n"Grommash" = "pvp"\n[budgets]\n"Amaya-Grommash" = 150000\n')
    config = parse_config(text, {}, tmp_path)
    assert config.markets == ("forever.normal.alliance.us",)
    assert config.retention_days == 60 and config.min_flip_margin == 1 and config.launch_wow_after_catchup
    assert config.realms == {"Grommash": "pvp"} and config.budgets == {"Amaya-Grommash": 150000}


@pytest.mark.parametrize("text, message", [
    ("retention_days = 90.5\n", "config.toml line 1: retention_days must be a whole number"),
    ("dashboard_port = true\n", "config.toml line 1: dashboard_port must be a number"),
    ("retention_dayz = 9\n", "config.toml line 1: retention_dayz is not a known setting"),
    ('markets = ["forever.pve.horde.us"]\n', "config.toml line 1: markets has 'forever.pve.horde.us'"),
    ("markets = []\n", "config.toml line 1: markets must list at least one market"),
    ("retention_days = 0\n", "config.toml line 1: retention_days must be greater than 0"),
    ('daily_refresh_time = "6am"\n', 'config.toml line 1: daily_refresh_time must be a time like "06:00"'),
    ('week_start = "Monday"\n', "config.toml line 1: week_start must be one of Mon"),
    ('[realms]\n"Grommash" = "pve"\n', "config.toml [realms]: Grommash must be one of normal, pvp, rp"),
    ('[budgets]\n"A-B" = -1\n', "config.toml [budgets]: A-B must be a whole number of copper"),
    ("retention_days = \n", "config.toml line 1: "),
    ("[realms]\nretention_days = 1\n", "config.toml [realms]"),
])
def test_bad_values_name_the_line_and_reason(tmp_path, text, message):
    with pytest.raises(ConfigError) as error:
        parse_config(text, {}, tmp_path)
    assert str(error.value).startswith(message)


def test_non_utf8_file_is_refused(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"wow_path = \"\xff\"\n")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(path, {}, tmp_path)


def test_api_key_lookup_order_env_then_file_then_config(tmp_path):
    key_file = tmp_path / ".ahsync" / "api_key"
    assert resolve_api_key({}, tmp_path, " from-config ") == "from-config"
    key_file.parent.mkdir()
    key_file.write_text("from-file\n", encoding="utf-8")
    assert resolve_api_key({}, tmp_path, "from-config") == "from-file"
    assert resolve_api_key({"AUCTIONHOUSE_APIKEY": "from-env"}, tmp_path, "from-config") == "from-env"
    key_file.write_text("  \n", encoding="utf-8")
    assert resolve_api_key({}, tmp_path, "from-config") == "from-config"


def test_key_never_in_repr_or_errors(tmp_path):
    config = parse_config('api_key = "ak_secret_value"\n', {}, tmp_path)
    assert config.api_key == "ak_secret_value"
    assert "ak_secret_value" not in repr(config)
    with pytest.raises(ConfigError) as error:
        parse_config('api_key = "ak_secret_value"\nretention_days = "x"\n', {}, tmp_path)
    assert "ak_secret_value" not in str(error.value)


def test_setting_line_ignores_tables():
    assert setting_line('a = 1\n[realms]\nretention_days = 2\n', "retention_days") is None
    assert setting_line('a = 1\nretention_days = 2\n', "retention_days") == 2


def test_SRC4_sources_map_a_market_to_its_providers_and_bad_tables_are_refused(tmp_path):
    from ahsync.config import MarketSources, market_sources
    text = ('markets = ["forever.pvp.horde.us"]\n[sources."forever.pvp.horde.us"]\nprimary = "blizzard"\n'
            'rank = ["blizzard", "ahf"]\n[sources."forever.pvp.horde.us".addresses]\nblizzard = "us/4728/2"\n')
    config = parse_config(text, {}, tmp_path)
    assert market_sources(config, "forever.pvp.horde.us") == MarketSources(
        "blizzard", ("blizzard", "ahf"), {"ahf": "forever.pvp.horde.us", "blizzard": "us/4728/2"})
    assert market_sources(Config(), "forever.pvp.horde.us") == MarketSources(addresses={"ahf": "forever.pvp.horde.us"})
    for bad, message in (('[sources."eu-x"]\nprimary = "a"\n', "expected a market id"),
                         ('[sources."forever.pvp.horde.us"]\nmain = "a"\n', "main is not a known setting"),
                         ('[sources."forever.pvp.horde.us"]\nrank = "ahf"\n', "rank must be a list of source names"),
                         ('[sources."forever.pvp.horde.us"]\nprimary = ""\n', "primary must be a source name"),
                         ('[sources."forever.pvp.horde.us".addresses]\nahf = 3\n', "addresses must be a table of strings"),
                         ('sources = 3\n', "config.toml line 1: sources must be a table")):
        with pytest.raises(ConfigError) as error:
            parse_config(bad, {}, tmp_path)
        assert message in str(error.value), bad
    with pytest.raises(ConfigError, match="must be a table"):
        parse_config('[sources]\n"forever.pvp.horde.us" = 3\n', {}, tmp_path)


def test_the_volume_prior_and_the_cheapest_default_are_settings(tmp_path):
    config = parse_config("default_sell_prob_cheapest = 0.6\nvolume_prior_per_day = 2\nvolume_prior_hours = 4\n", {},
                          tmp_path)
    market = market_settings(config)
    assert (market.default_sell_prob_cheapest, market.volume_prior.per_day, market.volume_prior.hours) == (0.6, 2, 4)
    assert plan_settings(config).default_sell_prob_cheapest == 0.6
    defaults = market_settings(Config())
    assert (defaults.default_sell_prob_cheapest, defaults.volume_prior.per_day, defaults.volume_prior.hours) == (0.5, 1.0, 2)
    for text, message in (("default_sell_prob_cheapest = 1.5\n", "must be between 0 and 1"),
                          ("volume_prior_hours = -1\n", "must be 0 or more")):
        with pytest.raises(ConfigError) as error:
            parse_config(text, {}, tmp_path)
        assert str(error.value).endswith(message)


def test_R26_TT7_the_window_margin_and_the_trend_clamp_are_settings(tmp_path):
    config = parse_config("window_margin = 0.1\npattern_full_weeks = 4\nmax_trend_total = 0.2\n", {}, tmp_path)
    s = plan_settings(config)
    assert (s.window_margin, s.pattern_full_weeks, s.max_trend_total) == (0.1, 4, 0.2)
    defaults = plan_settings(Config())
    assert (defaults.window_margin, defaults.pattern_full_weeks, defaults.max_trend_total) == (0.15, 6, 0.15)
    for text, message in (("pattern_full_weeks = 0\n", "must be greater than 0"),
                          ("window_margin = -0.1\n", "must be 0 or more"),
                          ("max_trend_total = -1\n", "must be 0 or more")):
        with pytest.raises(ConfigError) as error:
            parse_config(text, {}, tmp_path)
        assert str(error.value).endswith(message)


def test_the_portfolio_settings_are_read_with_their_defaults(tmp_path):
    assert portfolio_settings(Config()) == PortfolioSettings()
    assert Config().risk_tolerance == 0.30 and not hasattr(Config(), "max_risk_pct")   # the per-buy cap is gone
    with pytest.raises(ConfigError, match="max_risk_pct was removed: risk_tolerance caps all the gold tied up"):
        parse_config("max_risk_pct = 25\n", {}, tmp_path)
    text = ("risk_tolerance = 0.4\npremium_base = 0\npremium_per_day = 0.1\nimmediate_hours = 24\n"
            "top_buy_windows = 10\ntop_sell_windows = 1\ntoday_limit = 100\nweek_limit = 1\n"
            "candidate_export = 20\nhorizon_days = 7\n")
    assert portfolio_settings(parse_config(text, {}, tmp_path)) == PortfolioSettings(0.4, 0, 0.1, 24, 10, 1, 100, 1, 20, 7)


@pytest.mark.parametrize("text, message", [
    ("risk_tolerance = 0\n", "risk_tolerance must be more than 0 and at most 1"),
    ("risk_tolerance = 1.5\n", "risk_tolerance must be more than 0 and at most 1"),
    ('risk_tolerance = "high"\n', "risk_tolerance must be a number"),
    ("premium_base = -0.1\n", "premium_base must be 0 or more"),
    ("premium_per_day = -1\n", "premium_per_day must be 0 or more"),
    ("immediate_hours = 12\n", "immediate_hours must be 8 or 24"),
    ("immediate_hours = 8.5\n", "immediate_hours must be a whole number"),
    ("top_buy_windows = 0\n", "top_buy_windows must be between 1 and 10"),
    ("top_sell_windows = 11\n", "top_sell_windows must be between 1 and 10"),
    ("today_limit = 0\n", "today_limit must be between 1 and 100"),
    ("week_limit = 101\n", "week_limit must be between 1 and 100"),
    ("candidate_export = 19\n", "candidate_export must be 20 or more"),
    ("horizon_days = 14\n", "horizon_days must be 7"),
])
def test_bad_portfolio_settings_are_refused_with_the_line_and_reason(tmp_path, text, message):
    with pytest.raises(ConfigError) as error:
        parse_config(text, {}, tmp_path)
    assert str(error.value).startswith(f"config.toml line 1: {message}")


def test_the_candidate_pool_and_swap_bound_are_read_and_checked(tmp_path):
    assert (portfolio_settings(Config()).candidate_pool, portfolio_settings(Config()).swap_candidates) == (400, 50)
    s = portfolio_settings(parse_config("candidate_pool = 200\nswap_candidates = 10\n", {}, tmp_path))
    assert (s.candidate_pool, s.swap_candidates) == (200, 10)
    with pytest.raises(ConfigError, match="candidate_pool must be at least candidate_export"):
        parse_config("candidate_pool = 100\n", {}, tmp_path)
    with pytest.raises(ConfigError, match="swap_candidates must be 1 or more"):
        parse_config("swap_candidates = 0\n", {}, tmp_path)


RISK_CONFIG = ('# my settings\r\nmarkets = ["forever.pvp.horde.us"]\r\n'
               'risk_tolerance = 0.30                         # share of the capital base (§3.4D.1)\r\n'
               'premium_base = 0.15\r\n\r\n[realms]\r\n"RealmName" = "pvp"   # keep me\r\n')


def test_SEC11_only_the_risk_tolerance_line_changes_and_its_comment_and_line_endings_stay():
    new = with_risk_tolerance(RISK_CONFIG, 0.4)
    assert new == RISK_CONFIG.replace("risk_tolerance = 0.30 ", "risk_tolerance = 0.40 ")
    assert with_risk_tolerance("risk_tolerance=0.3\n", 0.1) == "risk_tolerance=0.10\n"


def test_SEC11_a_config_without_the_line_gets_it_before_its_first_table_or_at_the_end():
    assert with_risk_tolerance('markets = ["x"]\n[realms]\n"R" = "pvp"\n', 0.5) == \
        'markets = ["x"]\nrisk_tolerance = 0.50\n[realms]\n"R" = "pvp"\n'
    assert with_risk_tolerance('markets = ["x"]', 0.2) == 'markets = ["x"]\nrisk_tolerance = 0.20\n'
    assert with_risk_tolerance("", 0.2) == "risk_tolerance = 0.20\n"
    assert with_risk_tolerance('[realms]\nrisk_tolerance = 0.3\n', 0.2).startswith("risk_tolerance = 0.20\n[realms]")


def test_SEC11_the_rewrite_goes_through_a_temp_file_and_reads_back(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_bytes(RISK_CONFIG.encode("utf-8"))
    write_risk_tolerance(path, 0.4)
    assert path.read_bytes() == RISK_CONFIG.replace("= 0.30 ", "= 0.40 ").encode("utf-8")
    assert load_config(path, {}, tmp_path).risk_tolerance == 0.4 and not (tmp_path / "config.toml.tmp").exists()
    fresh = tmp_path / "new.toml"
    write_risk_tolerance(fresh, 0.1)
    assert fresh.read_text(encoding="utf-8") == "risk_tolerance = 0.10\n"
    import ahsync.config as config_module
    monkeypatch.setattr(config_module, "with_risk_tolerance", lambda text, value: text + "risk_tolerance = [\n")
    with pytest.raises(ConfigError):
        write_risk_tolerance(path, 0.2)
    assert load_config(path, {}, tmp_path).risk_tolerance == 0.4                   # left as it was
