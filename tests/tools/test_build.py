"""Tests for tools/build.py: the signing policy, the build commands and the release (SPEC §3.7.12; DR17-DR19, DR28)."""
from __future__ import annotations

import base64
import calendar
import hashlib
import sys
from pathlib import Path

import pytest

import tools.build as build

NOW_S = calendar.timegm((2026, 11, 2, 12, 0, 0))
TEAM = "TEAM123456"
IDENTITY = f"Developer ID Application: Test Dev ({TEAM})"
FAKE_SECRETS = {"MACOS_CERT_P12": base64.b64encode(b"fake-p12").decode(), "MACOS_CERT_PASSWORD": "fake-cert-password",
                "APPLE_TEAM_ID": TEAM, "NOTARY_KEY_ID": "FAKEKEYID", "NOTARY_ISSUER_ID": "fake-issuer",
                "NOTARY_KEY_P8": "-----fake-p8-----"}
VERSION_PY = 'VERSION = "0.0.0"      # dev\nBUILT = ""             # date\n'


class FakeRunner:
    """Records every command; answers find-identity, find-certificate, openssl x509 and git tag; fails on demand."""

    def __init__(self, tags: str = "", identity_team: str = TEAM, expires: str = "Nov 12 00:00:00 2027 GMT",
                 fail: str | None = None, untrusted: bool = False, kind: str = "Developer ID Application",
                 fail_times: int = 0, fail_code: int = 1) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.timeouts: list = []
        self.tags, self.identity_team, self.expires, self.fail = tags, identity_team, expires, fail
        self.untrusted = untrusted        # the chain to Apple's root is missing: -v lists nothing
        self.kind = kind                  # the certificate the .p12 holds
        self.fail_times = fail_times      # with fail: only the first N matching commands fail (0: every one)
        self.fail_code = fail_code
        self.failed = 0

    def __call__(self, args, *, input_text=None, extra_env=None, capture=False, timeout_s=None) -> build.Result:
        """Record and answer one command."""
        self.calls.append(list(args))
        self.timeouts.append(timeout_s)
        self.envs.append(dict(extra_env or {}))
        line = " ".join(args)
        if self.fail and self.fail in line and (not self.fail_times or self.failed < self.fail_times):
            self.failed += 1
            return build.Result(self.fail_code)
        if args[:2] == ["git", "tag"]:
            return build.Result(0, self.tags)
        if args[:2] == ["security", "find-identity"]:
            name = f"{self.kind}: Test Dev ({self.identity_team})"
            if "-v" not in args:          # like the real tool: every identity, then the valid ones again
                status = " (CSSMERR_TP_NOT_TRUSTED)" if self.untrusted else ""
                valid = "" if self.untrusted else f'  1) {"A" * 40} "{name}"\n'
                return build.Result(0, f'Policy: Code Signing\n  Matching identities\n  1) {"A" * 40} "{name}"'
                                       f'{status}\n     1 identities found\n\n  Valid identities only\n{valid}')
            if self.untrusted:
                return build.Result(0, "     0 valid identities found\n")
            return build.Result(0, f'  1) {"A" * 40} "{name}"\n     1 valid identities found\n')
        if args[:2] == ["security", "find-certificate"]:
            return build.Result(0, "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
        if args[1:3] == ["x509", "-noout"]:
            return build.Result(0, f"notAfter={self.expires}\n")
        return build.Result(0)

    def heads(self) -> list[str]:
        """Each command's program and first argument, e.g. "security import"."""
        return [" ".join(Path(c[0]).name if i == 0 else c[i] for i in range(min(2, len(c)))) for c in self.calls]


def make_world(tmp_path: Path, runner: FakeRunner, os_name: str, env: dict[str, str] | None = None):
    """A World over the fake runner, a temp root holding version.py and dist/, and a captured log."""
    (tmp_path / "ahsync").mkdir(exist_ok=True)
    (tmp_path / "ahsync" / "version.py").write_text(VERSION_PY, encoding="utf-8")
    (tmp_path / "dist").mkdir(exist_ok=True)
    lines: list[str] = []
    world = build.World(runner, dict(env or {}), os_name, lambda: NOW_S, lines.append, tmp_path,
                        python="python", which=lambda name: None)
    return world, lines


def assert_no_credential(runner: FakeRunner, lines: list[str]) -> None:
    """No command line and no log line holds a credential's value."""
    text = "\n".join([" ".join(c) for c in runner.calls] + lines)
    for name in build.CREDENTIALS:
        assert FAKE_SECRETS[name] not in text, name


# ---------------------------------------------------------------- DR17

def test_DR17_one_recipe_and_one_packaging_step_per_os_with_fixed_names(tmp_path):
    runner = FakeRunner()
    world, lines = make_world(tmp_path, runner, "windows")
    assert build.build_command(world, "0.4.0", "unsigned") == 0
    assert runner.calls == [["python", "-m", "PyInstaller", "--noconfirm", "packaging/ahsync.spec"],
                            [build.ISCC_DEFAULT, "/DAppVersion=0.4.0", "packaging/windows/ahsync.iss"]]
    assert "built dist/AHSync-0.4.0-windows-x64-setup.exe" in lines

    runner = FakeRunner()
    world, lines = make_world(tmp_path, runner, "macos")
    assert build.build_command(world, "0.4.0", "unsigned") == 0
    assert runner.calls == [["python", "-m", "PyInstaller", "--noconfirm", "packaging/ahsync.spec"],
                            ["packaging/macos/make_dmg.sh", "0.4.0"],
                            ["ditto", "-c", "-k", "--keepParent", "dist/AHSync.app",
                             "dist/AHSync-0.4.0-macos-arm64.zip"]]
    assert lines[-2:] == ["built dist/AHSync-0.4.0-macos-arm64.dmg", "built dist/AHSync-0.4.0-macos-arm64.zip"]


def test_DR17_the_version_is_stamped_for_the_build_and_the_source_file_put_back(tmp_path):
    seen: list[str] = []

    class Peek(FakeRunner):
        """Reads version.py while PyInstaller 'runs'."""

        def __call__(self, args, **kwargs):
            if "PyInstaller" in args:
                seen.append((tmp_path / "ahsync" / "version.py").read_text(encoding="utf-8"))
            return super().__call__(args, **kwargs)

    world, _ = make_world(tmp_path, Peek(), "windows")
    build.build_command(world, "0.4.0", "unsigned")
    assert seen == ['VERSION = "0.4.0"      # dev\nBUILT = "2026-11-02"             # date\n']
    assert (tmp_path / "ahsync" / "version.py").read_text(encoding="utf-8") == VERSION_PY


def test_DR17_a_failed_step_or_a_bad_version_fails_the_build(tmp_path):
    world, lines = make_world(tmp_path, FakeRunner(fail="ISCC"), "windows")
    assert build.main(["build", "0.4.0"], world) == 1
    assert lines[-1] == "::error::Inno Setup failed (exit 1)"
    assert (tmp_path / "ahsync" / "version.py").read_text(encoding="utf-8") == VERSION_PY
    world, lines = make_world(tmp_path, FakeRunner(), "windows")
    assert build.main(["build", "0.4"], world) == 1
    assert lines == ["::error::the version must be MAJOR.MINOR.PATCH"]


def test_DR17_iscc_from_the_environment_or_path(tmp_path):
    runner = FakeRunner()
    world, _ = make_world(tmp_path, runner, "windows", {"ISCC": r"D:\inno\ISCC.exe"})
    build.build_command(world, "0.4.0", "sign")          # the Windows build is the same in every mode
    assert runner.calls[1][0] == r"D:\inno\ISCC.exe"
    runner = FakeRunner()
    world, _ = make_world(tmp_path, runner, "windows")
    world.which = lambda name: r"C:\bin\ISCC.exe"
    build.build_command(world, "0.4.0", "unsigned")
    assert runner.calls[1][0] == r"C:\bin\ISCC.exe"


# ---------------------------------------------------------------- DR18

SIGNED_ORDER = ["security create-keychain", "security import", "PyInstaller", "codesign --verify",
                "ditto -c", "notarytool submit", "stapler staple", "make_dmg.sh", "codesign --sign",
                "notarytool submit", "stapler staple", "ditto -c", "security delete-keychain"]


def in_order(runner: FakeRunner, steps: list[str]) -> bool:
    """Whether the steps appear, each matched by substring, in this order among the commands."""
    lines = iter(" ".join(c) for c in runner.calls)
    return all(any(step in line for line in lines) for step in steps)


def test_DR18_the_signed_build_signs_notarizes_staples_and_cleans_up(tmp_path):
    runner = FakeRunner()
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    assert build.build_command(world, "0.4.0", "sign") == 0
    assert in_order(runner, SIGNED_ORDER)
    assert runner.calls[-1][:2] == ["security", "delete-keychain"]
    pyi = next(i for i, c in enumerate(runner.calls) if "PyInstaller" in c)
    assert runner.envs[pyi] == {"AHSYNC_CODESIGN_IDENTITY": IDENTITY,
                                "AHSYNC_ENTITLEMENTS": str(tmp_path / "packaging/macos/entitlements.plist")}
    notary = [c for c in runner.calls if "notarytool" in c]
    assert [c[3] for c in notary] == ["dist/notarize.zip", "dist/AHSync-0.4.0-macos-arm64.dmg"]
    assert notary[0][4:] == ["--wait", "--key", notary[0][6], "--key-id", "FAKEKEYID", "--issuer", "fake-issuer"]
    assert ["xcrun", "stapler", "staple", "dist/AHSync.app"] in runner.calls
    assert ["xcrun", "stapler", "staple", "dist/AHSync-0.4.0-macos-arm64.dmg"] in runner.calls
    assert runner.calls[-2] == ["ditto", "-c", "-k", "--keepParent", "dist/AHSync.app",
                                "dist/AHSync-0.4.0-macos-arm64.zip"]
    assert not Path(notary[0][6]).exists()               # the notary key's temp file is gone
    assert_no_credential(runner, lines)
    openssl = [env for c, env in zip(runner.calls, runner.envs) if c[0] == build.OPENSSL and "pkcs12" in c]
    assert openssl and all(env["MACOS_CERT_PASSWORD"] == "fake-cert-password" for env in openssl)


def test_DR18_apples_developer_id_intermediates_join_the_keychain_before_the_identity_is_looked_up(tmp_path):
    # The first real signed run imported the certificate and then found no valid identity: a runner may lack the
    # Developer ID CA that chains it to Apple's root, and find-identity -v hides an untrusted identity.
    runner = FakeRunner()
    world, _ = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    assert build.build_command(world, "0.4.0", "sign") == 0
    cas = [str(tmp_path / cert) for cert in build.APPLE_CAS]
    assert ["security", "import", cas[0], "-k", runner.calls[0][-1]] in runner.calls
    assert in_order(runner, ["security import", "DeveloperIDG2CA.cer", "DeveloperIDCA.cer", "security find-identity"])
    for cert in build.APPLE_CAS:
        assert (build.ROOT / cert).is_file()


def test_DR18_ids_pasted_with_whitespace_still_match(tmp_path):
    runner = FakeRunner()
    env = {**FAKE_SECRETS, "APPLE_TEAM_ID": f" {TEAM}\n", "NOTARY_KEY_ID": "FAKEKEYID\n", "NOTARY_ISSUER_ID": "fake-issuer "}
    world, _ = make_world(tmp_path, runner, "macos", env)
    assert build.build_command(world, "0.4.0", "sign") == 0
    notary = next(c for c in runner.calls if "notarytool" in c)
    assert notary[-4:] == ["--key-id", "FAKEKEYID", "--issuer", "fake-issuer"]
    world, lines = make_world(tmp_path, FakeRunner(), "macos", {**FAKE_SECRETS, "APPLE_TEAM_ID": "  \n"})
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 1
    assert lines[-1].endswith("missing APPLE_TEAM_ID")


def test_DR18_an_untrusted_identity_is_logged_by_kind_team_and_status_never_by_name(tmp_path):
    runner = FakeRunner(untrusted=True)
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 1
    assert lines[-1] == f"::error::certificate invalid: no Developer ID Application identity for team {TEAM}"
    assert "find-identity: 1 code-signing identities in the keychain" in lines
    assert f"  Developer ID Application · team {TEAM} · CSSMERR_TP_NOT_TRUSTED" in lines
    assert not any("Test Dev" in line for line in lines)
    assert runner.calls[-1][:2] == ["security", "delete-keychain"]


def test_DR18_an_apple_development_certificate_is_named_with_what_to_export_instead(tmp_path):
    # The user's first signed runs uploaded Xcode's Apple Development certificate; find-identity listed it
    # twice and the error only said no Developer ID identity was found.
    runner = FakeRunner(kind="Apple Development", identity_team="3FMPA4A4T9")
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 1
    assert [line for line in lines if line.startswith("  ")] == ["  Apple Development · team 3FMPA4A4T9 · valid"]
    assert "find-identity: 1 code-signing identities in the keychain" in lines
    assert lines[-1] == ("::error::certificate invalid: MACOS_CERT_P12 holds Apple Development, not a Developer ID "
                         "Application certificate: in Keychain Access (login, My Certificates) export the one "
                         "named \"Developer ID Application: …\" and set MACOS_CERT_P12 and MACOS_CERT_PASSWORD again")
    assert runner.calls[-1][:2] == ["security", "delete-keychain"]


def test_DR18_the_keychain_is_deleted_also_when_notarization_fails(tmp_path):
    runner = FakeRunner(fail="notarytool")
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    world.sleep = lambda s: None                                           # the retry's wait (FM73)
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 1
    assert runner.calls[-1][:2] == ["security", "delete-keychain"]
    assert lines[-1] == "::error::notarytool submit failed (exit 1) after 2 attempts"
    assert not any("make_dmg" in " ".join(c) for c in runner.calls)
    assert_no_credential(runner, lines)


def test_DR18_the_unsigned_build_needs_no_secret_and_signs_nothing(tmp_path):
    runner = FakeRunner()
    world, _ = make_world(tmp_path, runner, "macos")
    assert build.build_command(world, "0.4.0", "unsigned") == 0
    assert not any(c[0] in ("security", "xcrun") or "codesign" in c for c in runner.calls)


def test_DR18_a_certificate_that_will_not_import_fails_as_invalid(tmp_path):
    runner = FakeRunner(fail="security import")
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 1
    assert lines[-1] == "::error::certificate invalid: could not import MACOS_CERT_P12"
    assert runner.calls[-1][:2] == ["security", "delete-keychain"]
    world, lines = make_world(tmp_path, FakeRunner(), "macos", {**FAKE_SECRETS, "MACOS_CERT_P12": "not base64!"})
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 1
    assert lines[-1] == "::error::certificate invalid: MACOS_CERT_P12 is not base64"


def test_DR18_a_pasted_certificate_with_line_breaks_still_decodes(tmp_path):
    # `base64` ends its output with a newline, and a paste into GitHub's form can carry it or wrap the text:
    # the first real signed run failed "MACOS_CERT_P12 is not base64" on such a value.
    p12 = bytes(range(256)) * 3
    encoded = base64.b64encode(p12).decode()
    wrapped = "\r\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76)) + "\n"
    assert build.decode_certificate(encoded + "\n") == p12
    assert build.decode_certificate(" " + wrapped) == p12
    world, lines = make_world(tmp_path, FakeRunner(), "macos", {**FAKE_SECRETS, "MACOS_CERT_P12": wrapped})
    assert build.main(["build", "0.4.0", "--mode", "sign"], world) == 0
    with pytest.raises(build.BuildError, match="is not base64"):
        build.decode_certificate("bad*chars")


# ---------------------------------------------------------------- DR19

@pytest.mark.parametrize("event, ref, sign_input, has_tag, expected", [
    ("push", "refs/tags/v0.1.0", False, True, build.Policy("sign", True, "0.1.0")),
    ("push", "refs/heads/main", False, False, build.Policy("unsigned", False, "0.0.0")),
    ("push", "refs/heads/main", False, True, build.Policy("preflight", False, "0.0.0")),
    ("workflow_dispatch", "refs/heads/feature", True, True, build.Policy("sign", False, "0.0.0")),
    ("workflow_dispatch", "refs/heads/main", False, True, build.Policy("unsigned", False, "0.0.0")),
    ("push", "refs/heads/feature", False, True, build.Policy("unsigned", False, "0.0.0")),
    ("pull_request", "refs/pull/3/merge", False, True, build.Policy("unsigned", False, "0.0.0")),
    ("push", "refs/tags/v0.4.0-rc1", False, True, build.Policy("unsigned", False, "0.0.0")),
    ("push", "refs/tags/vfoo", False, True, build.Policy("unsigned", False, "0.0.0")),
])
def test_DR19_the_mode_of_each_kind_of_run(event, ref, sign_input, has_tag, expected):
    assert build.decide(event, ref, sign_input, has_tag) == expected


def run_policy(tmp_path, os_name="macos", env=None, **runner_args):
    """Run `policy` with GITHUB_OUTPUT in tmp_path; returns (exit, runner, log lines, outputs text)."""
    runner = FakeRunner(**runner_args)
    output = tmp_path / "github_output"
    output.unlink(missing_ok=True)
    world, lines = make_world(tmp_path, runner, os_name, {"GITHUB_OUTPUT": str(output), **(env or {})})
    code = build.main(["policy"], world)
    return code, runner, lines, output.read_text(encoding="utf-8") if output.exists() else ""


def test_DR19_a_release_tag_without_the_secrets_fails_before_pyinstaller(tmp_path):
    code, runner, lines, _ = run_policy(tmp_path, env={"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v0.1.0"},
                                        tags="v0.1.0\n")
    assert code == 1
    assert lines[-1] == ("::error::macOS signing secrets are required for a release (SPEC §3.7): missing "
                         "MACOS_CERT_P12, MACOS_CERT_PASSWORD, APPLE_TEAM_ID, NOTARY_KEY_ID, NOTARY_ISSUER_ID, "
                         "NOTARY_KEY_P8")
    world, lines = make_world(tmp_path, FakeRunner(), "macos")
    assert build.main(["build", "0.1.0", "--mode", "sign"], world) == 1          # build refuses it too
    assert lines[-1].startswith("::error::macOS signing secrets are required")


def test_DR19_a_release_tag_with_the_secrets_signs_and_releases(tmp_path):
    code, runner, lines, outputs = run_policy(
        tmp_path, env={"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v0.1.0", **FAKE_SECRETS}, tags="v0.1.0\n")
    assert code == 0
    assert outputs == "mode=sign\nrelease=true\nversion=0.1.0\n"
    assert runner.calls == [["git", "tag", "--list", "v*"]]        # the signing itself is the build's


def test_DR19_main_before_the_first_tag_builds_unsigned_with_no_preflight(tmp_path):
    code, runner, _, outputs = run_policy(tmp_path, env={"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"})
    assert code == 0 and outputs.startswith("mode=unsigned\n")
    assert not any(c[0] == "security" for c in runner.calls)


def test_DR19_main_after_a_tag_runs_the_preflight(tmp_path):
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", **FAKE_SECRETS}
    code, runner, lines, outputs = run_policy(tmp_path, env=env, tags="v0.1.0\n")
    assert code == 0 and outputs.startswith("mode=preflight\n")
    assert in_order(runner, ["security create-keychain", "security import", "security find-identity",
                             "security delete-keychain"])
    assert not any("notarytool" in c or "PyInstaller" in c for c in runner.calls)
    assert "signing preflight passed" in lines
    assert not any("expires" in line for line in lines)
    assert_no_credential(runner, lines)


@pytest.mark.parametrize("env_change, runner_args, message", [
    ({"NOTARY_KEY_ID": ""}, {}, "signing preflight failed: secret NOTARY_KEY_ID is not set"),
    ({}, {"identity_team": "OTHERTEAM1"},
     f"signing preflight failed: certificate invalid: no Developer ID Application identity for team {TEAM}"),
])
def test_DR19_a_failed_preflight_fails_the_job_naming_why(tmp_path, env_change, runner_args, message):
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", **FAKE_SECRETS, **env_change}
    code, runner, lines, _ = run_policy(tmp_path, env=env, tags="v0.1.0\n", **runner_args)
    assert code == 1 and lines[-1] == f"::error::{message}"
    if runner_args:
        assert runner.calls[-1][:2] == ["security", "delete-keychain"]


def test_DR19_a_certificate_expiring_soon_passes_with_a_warning(tmp_path):
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", **FAKE_SECRETS}
    code, _, lines, _ = run_policy(tmp_path, env=env, tags="v0.1.0\n", expires="Nov 12 13:00:00 2026 GMT")
    assert code == 0
    assert "::warning::signing certificate expires in 10 days" in lines


def test_DR19_an_unreadable_expiry_is_an_invalid_certificate(tmp_path):
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", **FAKE_SECRETS}
    code, _, lines, _ = run_policy(tmp_path, env=env, tags="v0.1.0\n", expires="garbage")
    assert code == 1 and lines[-1] == "::error::signing preflight failed: certificate invalid: no expiry date"


def test_DR19_a_manual_signed_run_does_not_release_and_windows_needs_no_secret(tmp_path):
    code, _, _, outputs = run_policy(tmp_path, env={"GITHUB_EVENT_NAME": "workflow_dispatch", "SIGN_INPUT": "true",
                                                    "GITHUB_REF": "refs/heads/feature", **FAKE_SECRETS})
    assert code == 0 and outputs == "mode=sign\nrelease=false\nversion=0.0.0\n"
    code, runner, _, outputs = run_policy(tmp_path, os_name="windows",
                                          env={"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v0.1.0"})
    assert code == 0 and outputs == "mode=sign\nrelease=true\nversion=0.1.0\n"
    assert runner.calls == [["git", "tag", "--list", "v*"]]


# ---------------------------------------------------------------- DR28

ASSETS = ["AHSync-0.4.0-windows-x64-setup.exe", "AHSync-0.4.0-macos-arm64.dmg", "AHSync-0.4.0-macos-arm64.zip"]


def test_DR28_the_release_writes_sha256sums_and_creates_a_pre_release(tmp_path):
    runner = FakeRunner()
    world, lines = make_world(tmp_path, runner, "windows")
    for i, name in enumerate(ASSETS):
        (tmp_path / "dist" / name).write_bytes(bytes([i]) * 100)
    assert build.release_command(world, "v0.4.0") == 0
    expected = "".join(f"{hashlib.sha256(bytes([ASSETS.index(n)]) * 100).hexdigest()}  {n}\n" for n in sorted(ASSETS))
    assert (tmp_path / "dist" / "SHA256SUMS").read_bytes() == expected.encode("ascii")
    assert runner.calls == [["gh", "release", "create", "v0.4.0", "--prerelease", "--generate-notes",
                             "--notes-file", "dist/RELEASE_NOTES.md", "--title", "AHSync 0.4.0",
                             *[f"dist/{n}" for n in ASSETS], "dist/SHA256SUMS"]]
    notes = (tmp_path / "dist" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert notes.startswith("How to install: [INSTALL.md](https://github.com/ctng1213/WowAuction/blob/v0.4.0/INSTALL.md)")


def test_DR28_a_suffixed_tag_or_a_missing_asset_is_refused_before_gh(tmp_path):
    runner = FakeRunner()
    world, lines = make_world(tmp_path, runner, "windows")
    assert build.main(["release", "v0.4.0-rc1"], world) == 1
    assert lines[-1] == "::error::release tags are vMAJOR.MINOR.PATCH"
    (tmp_path / "dist" / ASSETS[0]).write_bytes(b"x")
    assert build.main(["release", "v0.4.0"], world) == 1
    assert lines[-1] == "::error::dist/ lacks AHSync-0.4.0-macos-arm64.dmg, AHSync-0.4.0-macos-arm64.zip"
    assert runner.calls == []


def test_DR28_gh_failing_fails_the_release(tmp_path):
    world, lines = make_world(tmp_path, FakeRunner(fail="gh release"), "windows")
    for name in ASSETS:
        (tmp_path / "dist" / name).write_bytes(b"x")
    assert build.main(["release", "v0.4.0"], world) == 1
    assert lines[-1] == "::error::gh release create failed (exit 1)"


def test_real_run_reports_a_missing_program():
    assert build.real_run(["ahsync-no-such-program-xyz"]).code == 127


def test_DR18_no_child_process_inherits_a_credential(monkeypatch):
    for name in build.SECRETS:
        monkeypatch.setenv(name, FAKE_SECRETS[name])
    script = "import os, sys; print(sorted(n for n in os.environ if n.startswith(('MACOS', 'NOTARY', 'APPLE', 'X_'))))"
    result = build.real_run([sys.executable, "-c", script], extra_env={"X_PASS": "p"}, capture=True)
    assert result.out.strip() == "['APPLE_TEAM_ID', 'NOTARY_ISSUER_ID', 'NOTARY_KEY_ID', 'X_PASS']"


def test_FM64_a_staple_that_fails_once_is_retried_and_the_build_succeeds(tmp_path):
    dmg_staple = "stapler staple dist/AHSync-0.4.0-macos-arm64.dmg"
    runner = FakeRunner(fail=dmg_staple, fail_times=1, fail_code=68)
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    waits: list[float] = []
    world.sleep = waits.append
    assert build.build_command(world, "0.4.0", "sign") == 0
    assert sum(" ".join(c).endswith(dmg_staple) for c in runner.calls) == 2 and waits == [15]
    assert "stapler staple failed (exit 68); retry 1 of 3 in 15 s" in lines


def test_FM64_a_staple_that_always_fails_stops_the_build_after_four_attempts(tmp_path):
    dmg_staple = "stapler staple dist/AHSync-0.4.0-macos-arm64.dmg"
    runner = FakeRunner(fail=dmg_staple, fail_code=68)
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    waits: list[float] = []
    world.sleep = waits.append
    with pytest.raises(build.BuildError, match=r"stapler staple failed \(exit 68\) after 4 attempts"):
        build.build_command(world, "0.4.0", "sign")
    assert sum(" ".join(c).endswith(dmg_staple) for c in runner.calls) == 4 and waits == [15, 30, 60]
    assert runner.calls[-1][:2] == ["security", "delete-keychain"]



def test_FM73_a_stalled_notarization_is_stopped_after_20_minutes_and_tried_once_more(tmp_path):
    runner = FakeRunner()
    world, lines = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    waits: list[float] = []
    world.sleep = waits.append
    answers = iter([build.Result(124), build.Result(0)])                   # the first submission stalls (CI 42314968556)

    def stalls_once(args, **kwargs):
        runner(args, **kwargs)
        return next(answers) if "notarytool" in args else build.Result(0)

    world.run = stalls_once
    build.notarize(world, "dist/AHSync-0.1.1-macos-arm64.dmg")
    notary = [i for i, c in enumerate(runner.calls) if "notarytool" in c]
    assert len(notary) == 2 and [runner.timeouts[i] for i in notary] == [build.NOTARY_ATTEMPT_S] * 2
    assert waits == [build.NOTARY_RETRY_WAIT_S] and any("retry 1 of 1 in 30 s" in line for line in lines)
    assert_no_credential(runner, lines)


def test_FM73_a_notarization_that_stalls_twice_stops_the_build(tmp_path):
    runner = FakeRunner()
    world, _ = make_world(tmp_path, runner, "macos", FAKE_SECRETS)
    world.sleep = lambda s: None
    world.run = lambda args, **kwargs: build.Result(124)
    with pytest.raises(build.BuildError, match=r"notarytool submit failed \(exit 124\) after 2 attempts"):
        build.notarize(world, "dist/AHSync-0.1.1-macos-arm64.dmg")


def _pascal_body(source: str, header: str) -> str:
    """The text of one Inno Setup [Code] routine, from its header line to its closing `end;` at column 0."""
    start = source.index(header)
    return source[start:source.index("\nend;", start)]


def test_FM85_the_installer_refuses_while_the_assistant_bridge_runs_before_anything_is_touched():
    root = Path(__file__).resolve().parents[2]
    iss = (root / "packaging" / "windows" / "ahsync.iss").read_text(encoding="utf-8")
    recipe = (root / "packaging" / "ahsync.spec").read_text(encoding="utf-8")
    assert 'name="ahsync-mcp"' in recipe                        # the bridge the recipe builds is the one the check names
    assert "ProcessCount('ahsync-mcp.exe') > 0" in _pascal_body(iss, "function BridgeRunning: Boolean;")
    # Inno Setup runs PrepareToInstall before [InstallDelete] and [Files]: refusing there touches nothing.
    for header in ("function PrepareToInstall(", "function InitializeUninstall: Boolean;"):
        body = _pascal_body(iss, header)
        assert "BridgeRunningText" in body
        assert body.index("BridgeRunning") < body.index("StopAHSync")    # a refused install leaves AHSync running
    assert "Quit Claude (or the app using the assistant), then run the installer again." in iss
    code = iss[iss.index("\n[Code]\n") + 1:].splitlines()[1:]
    assert not [line for line in code if line.lstrip().startswith("[")]   # ISCC reads such a line as a section, even in a comment
