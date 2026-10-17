"""config.toml loading (SPEC §3.6) and FM16."""
from __future__ import annotations

import pytest

from ahsync.config import Config, ConfigError, load_config, parse_config, resolve_api_key, setting_line

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
