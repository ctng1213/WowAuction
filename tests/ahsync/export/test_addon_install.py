"""Tests for ahsync/export/addon_install.py: the addon installed from the bundle at every start (SPEC §3.7.7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync.export import addon_install

BUNDLE_FILES = {
    "AHProfit/AHProfit.toc": "## Interface: 16001\n",
    "AHProfit/Core/Adapter.lua": "-- adapter v2\n",
    "AHProfit/UI/MainWindow.lua": "-- window\n",
    "AHProfit_Data/AHProfit_Data.toc": "## Interface: 16001\n",
    "AHProfit_Data/MarketIntelligence.lua": "-- placeholder\n",
    "AHProfit_Data/PortfolioPlan.lua": "-- placeholder\n",
}


def write(root: Path, files: dict[str, str]) -> None:
    """Write text files under root, creating folders."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


@pytest.fixture
def bundle(tmp_path) -> Path:
    """The fake addon bundle of the Distribution scenarios."""
    write(tmp_path / "bundle", BUNDLE_FILES)
    return tmp_path / "bundle"


@pytest.fixture
def wow(tmp_path) -> Path:
    """A WoW folder with an empty Interface/AddOns."""
    (tmp_path / "wow" / "Interface" / "AddOns").mkdir(parents=True)
    return tmp_path / "wow"


def files_under(folder: Path) -> dict[str, str]:
    """Every file under folder, by forward-slash relative name."""
    return {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file()}


def test_DR3_a_fresh_addons_folder_gets_the_bundle_and_a_second_start_copies_nothing(bundle, wow):
    first = addon_install.install_addon(bundle, wow)

    assert files_under(wow / "Interface" / "AddOns") == BUNDLE_FILES
    assert (first.outcome, first.added, first.removed, first.changed, first.restart_wow) == ("installed", 6, 0, 0, True)
    assert first.line == {"text": "AHProfit updated · restart WoW to load it", "class": ""}

    second = addon_install.install_addon(bundle, wow)
    assert (second.added, second.removed, second.changed, second.restart_wow) == (0, 0, 0, False)
    assert second.line == {"text": "AHProfit installed · up to date", "class": ""}


def test_DR4_generated_data_is_never_overwritten_and_stale_code_goes(bundle, wow):
    addons = wow / "Interface" / "AddOns"
    write(addons, {"AHProfit/AHProfit.toc": "## Interface: 16001\n", "AHProfit/UI/MainWindow.lua": "-- window\n",
                   "AHProfit/Core/Adapter.lua": "-- adapter v1\n", "AHProfit/Old.lua": "-- old\n",
                   "AHProfit_Data/AHProfit_Data.toc": "## old toc\n",
                   "AHProfit_Data/MarketIntelligence.lua": "generated", "AHProfit_Data/PortfolioPlan.lua": "generated plan"})

    result = addon_install.install_addon(bundle, wow)

    after = files_under(addons)
    assert after["AHProfit_Data/MarketIntelligence.lua"] == "generated"
    assert after["AHProfit_Data/PortfolioPlan.lua"] == "generated plan"
    assert after["AHProfit_Data/AHProfit_Data.toc"] == "## Interface: 16001\n"
    assert "AHProfit/Old.lua" not in after and after["AHProfit/Core/Adapter.lua"] == "-- adapter v2\n"
    assert (result.removed, result.changed, result.restart_wow) == (1, 2, True)
    assert result.line["text"] == "AHProfit updated · restart WoW to load it"

    (addons / "AHProfit" / "Core" / "Adapter.lua").write_text("-- adapter v1\n", encoding="utf-8")
    again = addon_install.install_addon(bundle, wow)
    assert (again.added, again.removed, again.changed) == (0, 0, 1)
    assert again.line == {"text": "AHProfit updated · /reload in game", "class": ""}


def test_FM49_no_addons_folder_or_a_refused_probe_copies_nothing(bundle, tmp_path):
    wow = tmp_path / "Games" / "WoW"
    wow.mkdir(parents=True)
    missing = addon_install.install_addon(bundle, wow)
    assert missing.outcome == "no_addons" and missing.line == {
        "text": f"not installed: no Interface/AddOns under {wow}", "class": "amber"}
    assert files_under(wow) == {}

    (wow / "Interface" / "AddOns").mkdir(parents=True)
    refused = addon_install.install_addon(bundle, wow, probe=lambda folder: False)
    assert refused.outcome == "not_writable"
    assert refused.line == {"text": "not installed: Interface/AddOns not writable", "class": "amber"}
    assert files_under(wow) == {}


def test_FM49_an_interrupted_copy_leaves_no_half_written_file(bundle, wow):
    addons = wow / "Interface" / "AddOns"
    write(addons, {"AHProfit/Core/Adapter.lua": "-- adapter v1\n"})

    def cut_off(src: Path, dst: Path) -> None:
        dst.write_text("-- adap", encoding="utf-8")          # half a file, then the disk goes away
        raise OSError("disk removed")

    result = addon_install.install_addon(bundle, wow, copy=cut_off)

    assert result.outcome == "not_writable"
    assert files_under(addons) == {"AHProfit/Core/Adapter.lua": "-- adapter v1\n"}   # untouched, no temp file left


def test_the_write_probe_leaves_nothing_behind(tmp_path):
    assert addon_install.can_write(tmp_path) is True and list(tmp_path.iterdir()) == []
    assert addon_install.can_write(tmp_path / "missing") is False
