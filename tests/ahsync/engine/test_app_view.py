"""Tests for ahsync/engine/app_view.py: the app's version, hover and Addon texts (SPEC §3.7)."""
from __future__ import annotations

from ahsync.engine import app_view

HOUR, MINUTE = 3_600_000, 60_000
FRI_6_NOV = 1_793_923_200_000          # Fri 6 Nov 2026 00:00 UTC


def test_DR2_a_dev_build_and_a_release_read_their_version_and_hover():
    assert app_view.is_dev("0.0.0") and not app_view.is_dev("0.3.1")
    assert app_view.version_text("0.0.0", "") == "dev build"
    assert app_view.hover_text("0.0.0", FRI_6_NOV, 0) == "AHSync dev build"
    assert app_view.version_text("0.3.1", "2026-11-02") == "0.3.1 · built 2 Nov"
    assert app_view.hover_text("0.3.1", FRI_6_NOV + 14 * HOUR + 6 * MINUTE, 0) == "AHSync 0.3.1 · synced 14:06"


def test_version_pill_on_the_beta_channel_and_without_a_build_date():
    assert app_view.version_text("0.4.0", "2026-11-02", "beta") == "0.4.0 · beta · built 2 Nov"
    assert app_view.version_text("0.4.0", "") == "0.4.0"


def test_hover_before_the_first_sync_and_in_local_time():
    assert app_view.hover_text("0.3.1", None, 0) == "AHSync 0.3.1"
    assert app_view.hover_text("0.3.1", FRI_6_NOV + 13 * HOUR + 6 * MINUTE, HOUR) == "AHSync 0.3.1 · synced 14:06"


def test_DR3_DR4_the_addon_line_tells_a_wow_restart_from_a_reload():
    assert app_view.addon_text("installed", added_or_removed=3) == {
        "text": "AHProfit updated · restart WoW to load it", "class": ""}
    assert app_view.addon_text("installed", changed=1) == {"text": "AHProfit updated · /reload in game", "class": ""}
    assert app_view.addon_text("installed") == {"text": "AHProfit installed · up to date", "class": ""}


def test_FM49_the_addon_line_when_nothing_could_be_installed():
    assert app_view.addon_text("no_addons", r"C:\Games\WoW") == {
        "text": r"not installed: no Interface/AddOns under C:\Games\WoW", "class": "amber"}
    assert app_view.addon_text("not_writable") == {"text": "not installed: Interface/AddOns not writable",
                                                    "class": "amber"}


def test_DR6_the_folder_check_texts():
    assert app_view.wow_check("C:/wow", True, True, 2, creatable=True) == {
        "ok": True, "pill": "found", "text": "found Interface/AddOns · 2 accounts"}
    assert app_view.wow_check("C:/wow", True, True, 1, creatable=False)["text"] == "found Interface/AddOns · 1 account"
    assert app_view.wow_check("", False, False, 0, creatable=True) == {
        "ok": False, "pill": "not found", "text": "not found: enter the folder that holds Interface and WTF"}
    assert app_view.wow_check(r"C:\Games\WoW", False, False, 0, creatable=True) == {
        "ok": False, "pill": "not found", "text": r"no Interface/AddOns under C:\Games\WoW"}
    assert app_view.wow_check("C:/new", True, False, 0, creatable=True) == {
        "ok": True, "pill": "found", "text": "found Interface/AddOns · no accounts yet"}


def test_DR5_a_configured_folder_needs_addons_itself():
    assert app_view.wow_check(r"C:\Games\WoW", True, False, 0, creatable=False) == {
        "ok": False, "pill": "not found", "text": r"no Interface/AddOns under C:\Games\WoW"}


def test_DR2_the_update_line_before_any_check():
    assert app_view.update_not_checked("0.0.0") == {"text": "not checked for a dev build", "url": None, "pill": None,
                                                     "restart": False, "class": "muted"}
    assert app_view.update_not_checked("0.3.1")["text"] == "not checked yet"


WOW = r"C:\Program Files (x86)\World of Warcraft\_classic_beta_"
DOWNLOADED = {"text": "AHSync 0.4.0 downloaded · installs at the next start · you run 0.3.1",
              "url": "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0", "pill": "update ready · 0.4.0",
              "restart": True, "class": "amber"}


def test_D97_the_app_payload_exactly():
    found = app_view.wow_check(WOW, True, True, 2, creatable=False)
    payload = app_view.app_state("0.3.1", "2026-11-02", "stable", False, WOW, found, True,
                                 {"enabled": True, "available": True, "note": ""}, True, DOWNLOADED, False,
                                 {"text": "AHProfit installed · up to date", "class": ""}, "%LOCALAPPDATA%\\AHSync")
    assert payload == {
        "version": "0.3.1", "version_text": "0.3.1 · built 2 Nov", "dev": False, "channel": "stable", "setup": False,
        "wow_path": WOW, "wow_check": {"ok": True, "pill": "found", "text": "found Interface/AddOns · 2 accounts"},
        "markets": {"rulesets": [["normal", "Normal"], ["pvp", "PvP"], ["rp", "RP"]],
                    "factions": [["alliance", "Alliance"], ["horde", "Horde"]]},
        "api_key_set": True, "autostart": {"enabled": True, "available": True, "note": ""}, "auto_update": True,
        "update": DOWNLOADED, "location_problem": None,
        "addon": {"text": "AHProfit installed · up to date", "class": ""}, "data_folder": "%LOCALAPPDATA%\\AHSync"}


def test_D97_in_setup_mode_and_translocated():
    found = app_view.wow_check(WOW, True, True, 2, creatable=True)
    payload = app_view.app_state("0.3.1", "2026-11-02", "stable", True, WOW, found, False,
                                 {"enabled": False, "available": False, "note": ""}, True,
                                 app_view.update_not_checked("0.3.1"), True, None)
    assert payload["setup"] is True and payload["api_key_set"] is False
    assert payload["update"]["text"] == "not checked yet"
    assert payload["location_problem"].startswith("AHSync is running from where it was downloaded.")
    assert payload["addon"] == {"text": "not installed yet", "class": "muted"}


def test_the_ruleset_choices_are_the_config_s():
    from ahsync.config import RULESETS, MARKET_PATTERN
    assert tuple(code for code, _ in app_view.RULESET_CHOICES) == RULESETS
    assert all(MARKET_PATTERN.match(f"forever.{r}.{f}.us") for r, _ in app_view.RULESET_CHOICES
               for f, _ in app_view.FACTION_CHOICES)


def test_DR11_a_translocated_app_s_toggle_is_unavailable_with_its_note():
    assert app_view.autostart_line(True, False) == {"enabled": True, "available": True, "note": ""}
    assert app_view.autostart_line(False, True) == {
        "enabled": False, "available": False, "note": "not set: Move AHSync to Applications, then turn it on again"}


def card_payload(**changes) -> dict:
    """D97's payload (0.3.1, stable, 0.4.0 downloaded), with changes."""
    found = app_view.wow_check(WOW, True, True, 2, creatable=False)
    base = app_view.app_state("0.3.1", "2026-11-02", "stable", False, WOW, found, True,
                              {"enabled": True, "available": True, "note": ""}, True, DOWNLOADED, False,
                              {"text": "AHProfit installed · up to date", "class": ""}, "%LOCALAPPDATA%\\AHSync")
    return {**base, **changes}


def test_D98_the_app_card_s_lines_in_order_with_their_controls():
    card = app_view.app_card(card_payload(), "set · Premium until 5 Nov", "46 MB")
    assert (card["title"], card["pill"]) == ("AHSync app", "0.3.1 · built 2 Nov")
    assert [l["label"] for l in card["lines"]] == ["Update", "Auto-update", "Beta updates", "Start at login", "API key",
                                                    "Data folder", "WoW folder", "Addon"]
    update, auto, beta, login, key, data, wow, addon = card["lines"]
    assert update["parts"][0] == ["AHSync 0.4.0", DOWNLOADED["url"]] and update["class"] == "amber"
    assert update["button"] == {"text": "Restart to update", "enabled": True, "post": "/api/update/restart"}
    assert auto["control"] == {"checked": True, "enabled": True, "text": "install updates automatically",
                               "post": "/api/update/auto", "field": ["enabled", True, False],
                               "title": app_view.AUTO_TITLE}
    assert beta["control"]["field"] == ["channel", "beta", "stable"]
    assert beta["control"]["checked"] is False and beta["control"]["text"] == "get beta updates"
    assert {k: login["control"][k] for k in ("checked", "enabled", "text", "post")} == {
        "checked": True, "enabled": True, "text": "on", "post": "/api/autostart"}
    assert key["parts"] == [["set · Premium until 5 Nov", None]] and key["class"] == ""
    assert data["parts"] == [["%LOCALAPPDATA%\\AHSync · 46 MB", None]] and data["class"] == "muted"
    assert wow["parts"] == [[WOW, None]] and addon["parts"] == [["AHProfit installed · up to date", None]]


def test_D98_on_the_beta_channel_from_source_and_refused_start_at_login():
    up_to_date = {"text": "up to date · checked 06:00", "url": None, "pill": None, "restart": False, "class": ""}
    beta = app_view.app_card(card_payload(channel="beta", version_text="0.4.0 · beta · built 2 Nov",
                                          update=up_to_date), "none · free data only", "46 MB")
    assert beta["pill"] == "0.4.0 · beta · built 2 Nov" and beta["lines"][2]["control"]["checked"] is True
    assert beta["lines"][0]["button"] is None and beta["lines"][4]["class"] == "muted"
    refused = app_view.app_card(card_payload(autostart={"enabled": False, "available": False,
                                                        "note": "not set: Move AHSync to Applications, then turn it "
                                                                "on again"}), "set", "1 MB")
    login = refused["lines"][3]
    assert login["control"]["enabled"] is False and login["control"]["text"] == "off" and login["class"] == "amber"
    assert login["parts"] == [["not set: Move AHSync to Applications, then turn it on again", None]]


def test_DR6_a_fresh_forever_folder_passes_setups_check_and_a_configured_one_does_not():
    assert app_view.wow_check("/Applications/World of Warcraft/_forever_", False, False, 0, creatable=True,
                              is_forever=True) == {"ok": True, "pill": "found", "text": "found WoW: Forever · AddOns will be created"}
    assert app_view.wow_check("/Applications/World of Warcraft/_forever_", False, False, 0, creatable=False,
                              is_forever=True)["ok"] is False       # the addon install needs the folder complete
