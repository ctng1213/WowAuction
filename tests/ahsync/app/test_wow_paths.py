"""Tests for ahsync/app/wow_paths.py: detecting and checking the WoW folder (SPEC §3.7.3, DR6)."""
from __future__ import annotations

from pathlib import Path

from ahsync.app import wow_paths


def client(root: Path, accounts: tuple[str, ...] = ("A1", "A2"), addons: bool = True) -> Path:
    """A fake WoW client folder under root."""
    (root / "Interface" / ("AddOns" if addons else "")).mkdir(parents=True, exist_ok=True)
    for name in accounts:
        (root / "WTF" / "Account" / name).mkdir(parents=True)
    return root


def test_DR6_the_first_candidate_with_addons_is_detected_and_counted(tmp_path):
    beta = client(tmp_path / "World of Warcraft" / "_classic_beta_")
    client(tmp_path / "World of Warcraft" / "_classic_", accounts=())
    candidates = [tmp_path / "World of Warcraft" / "_missing_", beta, tmp_path / "World of Warcraft" / "_classic_"]
    assert wow_paths.detect(candidates) == beta
    assert wow_paths.check(beta) == wow_paths.WowCheck(True, True, 2)


def test_DR6_nothing_detected_and_a_check_only_reads(tmp_path):
    assert wow_paths.detect([tmp_path / "none", tmp_path / "other"]) is None
    fresh = client(tmp_path / "fresh", accounts=(), addons=False)
    found = wow_paths.check(fresh)
    assert found == wow_paths.WowCheck(True, False, 0) and found.usable
    assert not (fresh / "Interface" / "AddOns").exists() and not (fresh / "WTF").exists()
    assert not wow_paths.check(tmp_path / "Games" / "WoW").usable


def test_a_folder_that_cannot_be_read_checks_as_empty(tmp_path, monkeypatch):
    def refuse(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "is_dir", refuse)
    assert wow_paths.check(tmp_path) == wow_paths.WowCheck(False, False, 0)



def forever(root: Path, addons: bool = False) -> Path:
    """A fresh WoW: Forever install: Battle.net's .flavor.info, no WTF; an addon folder only once one is installed."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n", encoding="utf-8")
    if addons:
        (root / "Interface" / "AddOns").mkdir(parents=True)
    return root


def test_DR6_a_fresh_forever_install_is_detected_before_the_betas_folder_and_is_usable(tmp_path):
    wow = tmp_path / "World of Warcraft"
    beta = client(wow / "_classic_beta_")                                   # the beta, with AddOns
    (beta / ".flavor.info").write_text("Product Flavor!STRING:0\nwow_classic_beta\n", encoding="utf-8")
    release = forever(wow / "_forever_")                                    # no Interface yet
    assert wow_paths.detect([release, beta]) == release and wow_paths.detect([beta, release]) == release
    found = wow_paths.check(release)
    assert found == wow_paths.WowCheck(False, False, 0, "wowf") and found.is_forever and found.usable
    assert wow_paths.check(beta).flavor == "wow_classic_beta" and not wow_paths.check(beta).is_forever


def test_DR6_without_a_forever_install_the_first_folder_with_addons_is_still_taken(tmp_path):
    classic = client(tmp_path / "_classic_beta_")
    assert wow_paths.detect([tmp_path / "missing", classic]) == classic
    (tmp_path / "odd").mkdir()
    (tmp_path / "odd" / ".flavor.info").write_bytes(b"\xff\xfe")             # unreadable flavor: none
    assert wow_paths.flavor_of(tmp_path / "odd") == "" and wow_paths.flavor_of(tmp_path / "missing") == ""
