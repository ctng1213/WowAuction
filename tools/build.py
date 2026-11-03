"""Build, sign and release AHSync (SPEC §3.7.12; DR17-DR19, DR28).

    python tools/build.py policy                     # CI: decide sign / preflight / unsigned, check the secrets
    python tools/build.py build 0.4.0 [--mode sign]  # PyInstaller, then this OS's one packaging step
    python tools/build.py release v0.4.0             # CI, tags only: SHA256SUMS and the GitHub pre-release

The workflow stays thin: every decision (the signing mode, the commands and their order, the artifact names, the
checksums, the release command) is here and tested with a fake runner (tests/tools/test_build.py); no real build
runs in the tests. Run from the repository root.

Secrets (SPEC §3.7.12) arrive only through the environment. The credentials among them (the certificate, its
password, the notary key) never appear in a command line or a log line: the certificate is re-encrypted with a
throwaway password before `security import`, and the notary key is written to a private temp file.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import calendar
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_FILE = "packaging/ahsync.spec"
ISS_FILE = "packaging/windows/ahsync.iss"
DMG_SCRIPT = "packaging/macos/make_dmg.sh"
ENTITLEMENTS = "packaging/macos/entitlements.plist"
APP = "dist/AHSync.app"
NOTARIZE_ZIP = "dist/notarize.zip"
ISCC_DEFAULT = r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
OPENSSL = "/usr/bin/openssl"      # macOS's LibreSSL: reads Keychain-exported .p12 files without OpenSSL 3's -legacy

SECRETS = ("MACOS_CERT_P12", "MACOS_CERT_PASSWORD", "APPLE_TEAM_ID",
           "NOTARY_KEY_ID", "NOTARY_ISSUER_ID", "NOTARY_KEY_P8")
CREDENTIALS = ("MACOS_CERT_P12", "MACOS_CERT_PASSWORD", "NOTARY_KEY_P8")   # never in a command or a log line
EXPIRY_WARN_DAYS = 30
RUN_TIMEOUT_S = 3600              # notarytool --wait is the slow one
VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)", re.ASCII)
TAG_RE = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)", re.ASCII)
IDENTITY_RE = re.compile(r'\d+\)\s+[0-9A-F]{40}\s+"(Developer ID Application: .*\(([A-Z0-9]+)\))"')
# Any identity, for the failure log: (kind, team or "", status or ""); the name between them is not captured.
IDENTITY_ANY_RE = re.compile(r'\d+\)\s+[0-9A-F]{40}\s+"([^":]+):[^"]*?(?:\(([A-Z0-9]+)\))?"(?:[ \t]+\(([^)\n]*)\))?')
# Apple's public Developer ID intermediates (apple.com/certificateauthority): G2 for certificates since 2021, G1 before.
APPLE_CAS = ("packaging/macos/certs/DeveloperIDG2CA.cer", "packaging/macos/certs/DeveloperIDCA.cer")
INSTALL_URL = "https://github.com/ctng1213/WowAuction/blob/{tag}/INSTALL.md"


@dataclass(frozen=True)
class Result:
    """What a command did: its exit code and, when captured, its standard output."""

    code: int
    out: str = ""


Run = Callable[..., Result]   # (args, *, input_text=None, extra_env=None, capture=False) -> Result


class BuildError(Exception):
    """A build, signing or release step that failed; the message is safe to print (it holds no secret)."""


@dataclass
class World:
    """The build's outside world, injected so tests use fakes.

    Attributes:
        run: Runs a command (argument list) in the repo root; see `real_run`.
        env: The environment (the secrets, GITHUB_* variables).
        os_name: "windows" or "macos": which packaging step runs.
        now_s: The clock, UTC Unix seconds.
        out: Where log lines go.
        root: The repository root (version.py, dist/).
        python: The interpreter that runs PyInstaller.
        which: Finds a program on PATH (shutil.which).
        hidden: Values masked in every logged command (the credentials, the throwaway keychain password).
    """

    run: Run
    env: Mapping[str, str]
    os_name: str
    now_s: Callable[[], float]
    out: Callable[[str], None]
    root: Path
    python: str = sys.executable
    which: Callable[[str], str | None] = shutil.which
    hidden: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Mask the credentials that are set."""
        self.hidden |= {self.env[name] for name in CREDENTIALS if self.env.get(name)}

    def redact(self, text: str) -> str:
        """Mask every hidden value in a text.

        Args:
            text: A command line or log line.

        Returns:
            The text with each hidden value replaced by "***".
        """
        for value in sorted(self.hidden, key=len, reverse=True):
            text = text.replace(value, "***")
        return text

    def cmd(self, args: list[str], what: str, *, input_text: str | None = None,
            extra_env: dict[str, str] | None = None, capture: bool = False) -> Result:
        """Log and run a command that must succeed.

        Args:
            args: The argument list (never a shell string).
            what: The step's name for the error message.
            input_text: Standard input, if any.
            extra_env: Variables added to the environment (a way to pass a password without the command line).
            capture: Whether to capture standard output.

        Returns:
            The command's result.

        Raises:
            BuildError: "<what> failed (exit N)" when it exits non-zero.
        """
        self.out(self.redact("+ " + " ".join(args)))
        result = self.run(args, input_text=input_text, extra_env=extra_env, capture=capture)
        if result.code != 0:
            raise BuildError(f"{what} failed (exit {result.code})")
        return result


# ---------------------------------------------------------------- names

def artifact_names(version: str) -> dict[str, str]:
    """The release's files for a version (SPEC §3.7.9, §3.7.12).

    Args:
        version: "MAJOR.MINOR.PATCH".

    Returns:
        {"windows": ..., "dmg": ..., "zip": ...}: file names under dist/.
    """
    return {"windows": f"AHSync-{version}-windows-x64-setup.exe",
            "dmg": f"AHSync-{version}-macos-arm64.dmg",
            "zip": f"AHSync-{version}-macos-arm64.zip"}


def tag_version(tag: str) -> str | None:
    """The version of a release tag.

    Args:
        tag: A git tag, e.g. "v0.4.0".

    Returns:
        "0.4.0", or None for anything but vMAJOR.MINOR.PATCH (a suffix, non-ASCII digits).
    """
    match = TAG_RE.fullmatch(tag)
    return ".".join(match.groups()) if match else None


# ---------------------------------------------------------------- the signing policy (DR19)

@dataclass(frozen=True)
class Policy:
    """What a CI run does: mode "sign", "preflight" or "unsigned"; whether it publishes a Release; the version."""

    mode: str
    release: bool
    version: str


def decide(event: str, ref: str, sign_input: bool, has_v_tag: bool) -> Policy:
    """The signing mode of a CI run (SPEC §3.7.12).

    Args:
        event: GITHUB_EVENT_NAME ("push", "pull_request", "workflow_dispatch").
        ref: GITHUB_REF ("refs/tags/v0.1.0", "refs/heads/main", "refs/pull/3/merge").
        sign_input: The manual run's `sign` input.
        has_v_tag: Whether any v* tag exists in the repository.

    Returns:
        sign for a vMAJOR.MINOR.PATCH tag (which releases) or a manual run asking for it (which doesn't); preflight
        for a push to main once a v* tag exists; unsigned otherwise, a suffixed tag included (never released, so
        never signed). The version is the tag's, else "0.0.0".
    """
    tag = ref.removeprefix("refs/tags/") if ref.startswith("refs/tags/") else None
    version = (tag_version(tag) if tag else None) or "0.0.0"
    if event == "push" and tag is not None and tag_version(tag) is not None:
        return Policy("sign", True, version)
    if event == "workflow_dispatch" and sign_input:
        return Policy("sign", False, version)
    if event == "push" and ref == "refs/heads/main" and has_v_tag:
        return Policy("preflight", False, version)
    return Policy("unsigned", False, version)


def missing_secrets(env: Mapping[str, str]) -> list[str]:
    """The signing secrets that are not set.

    Args:
        env: The environment.

    Returns:
        Their names, in SECRETS order.
    """
    return [name for name in SECRETS if not env.get(name, "").strip()]


def secret_id(world: World, name: str) -> str:
    """An identifier secret (APPLE_TEAM_ID, NOTARY_KEY_ID, NOTARY_ISSUER_ID) without the whitespace a paste adds.

    Args:
        world: The outside world.
        name: The secret's name.

    Returns:
        Its value, stripped; "" when unset.
    """
    return world.env.get(name, "").strip()


def policy_command(world: World) -> int:
    """`build.py policy`: decide the mode; on macOS check the secrets, or run the preflight (DR19).

    Args:
        world: The outside world; GITHUB_EVENT_NAME, GITHUB_REF, SIGN_INPUT and GITHUB_OUTPUT are read from env.

    Returns:
        0; writes mode, release and version to GITHUB_OUTPUT when set.

    Raises:
        BuildError: mode sign on macOS without the secrets; a failed preflight.
    """
    env = world.env
    tags = world.cmd(["git", "tag", "--list", "v*"], "git tag", capture=True).out
    policy = decide(env.get("GITHUB_EVENT_NAME", ""), env.get("GITHUB_REF", ""),
                    env.get("SIGN_INPUT", "").lower() == "true", bool(tags.strip()))
    if world.os_name == "macos" and policy.mode == "sign" and missing_secrets(env):
        raise BuildError("macOS signing secrets are required for a release (SPEC §3.7): missing "
                         + ", ".join(missing_secrets(env)))
    if world.os_name == "macos" and policy.mode == "preflight":
        preflight(world)
    world.out(f"policy: mode={policy.mode} release={str(policy.release).lower()} version={policy.version}")
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as outputs:
            outputs.write(f"mode={policy.mode}\nrelease={str(policy.release).lower()}\n"
                          f"version={policy.version}\n")
    return 0


# ---------------------------------------------------------------- the keychain and the identity

def write_private(path: Path, data: bytes) -> Path:
    """Write a file only its owner can read (mode 0600 where the OS has modes).

    Args:
        path: The new file, inside a private temp folder.
        data: Its bytes.

    Returns:
        The path.
    """
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    return path


@contextmanager
def private_folder() -> Iterator[Path]:
    """A temp folder only the user can read (mkdtemp: 0700), removed with everything in it at the end.

    Returns:
        The folder, as a context manager.
    """
    folder = Path(tempfile.mkdtemp(prefix="ahsync-build-"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def decode_certificate(value: str) -> bytes:
    """Decode MACOS_CERT_P12, ignoring the whitespace a paste or `base64`'s line breaks leave in it.

    Args:
        value: The secret: the .p12 in base64, possibly with spaces, newlines or CRLFs anywhere.

    Returns:
        The .p12's bytes.

    Raises:
        BuildError: "certificate invalid: MACOS_CERT_P12 is not base64" for any other non-base64 character.
    """
    try:
        return base64.b64decode("".join(value.split()), validate=True)
    except (binascii.Error, ValueError):
        raise BuildError("certificate invalid: MACOS_CERT_P12 is not base64") from None


@contextmanager
def signing_keychain(world: World) -> Iterator[str]:
    """Import the Developer ID certificate into a temporary keychain, deleted at the end, also on failure.

    The .p12's password reaches openssl through the environment; the certificate is re-encrypted with a
    throwaway password, which is all `security import` sees on its command line. UNVERIFIED until the first
    signed run on a macOS runner.

    Args:
        world: The outside world (MACOS_CERT_P12 base64, MACOS_CERT_PASSWORD).

    Returns:
        The keychain's path, as a context manager.

    Raises:
        BuildError: "certificate invalid: ..." when the certificate can't be decoded or imported.
    """
    p12_bytes = decode_certificate(world.env.get("MACOS_CERT_P12", ""))
    throwaway = secrets.token_hex(16)
    world.hidden.add(throwaway)
    with private_folder() as folder:
        keychain = str(folder / "ahsync-signing.keychain-db")
        p12, pem, reexported = folder / "cert.p12", str(folder / "cert.pem"), str(folder / "signing.p12")
        write_private(p12, p12_bytes)
        world.cmd(["security", "create-keychain", "-p", throwaway, keychain], "security create-keychain")
        try:
            import_certificate(world, keychain, throwaway, str(p12), pem, reexported)
            yield keychain
        finally:
            world.out(f"+ security delete-keychain {keychain}")
            world.run(["security", "delete-keychain", keychain])


def import_certificate(world: World, keychain: str, throwaway: str, p12: str, pem: str, reexported: str) -> None:
    """Unlock the keychain and import the certificate so codesign can use it without a prompt.

    Args:
        world: The outside world.
        keychain: The temporary keychain's path.
        throwaway: Its password, also the re-encrypted certificate's.
        p12: The decoded MACOS_CERT_P12.
        pem: A temp path for the decrypted-then-re-encrypted PEM.
        reexported: A temp path for the re-exported .p12.

    Raises:
        BuildError: "certificate invalid: could not import MACOS_CERT_P12" when openssl or security refuse it.
    """
    passwords = {"MACOS_CERT_PASSWORD": world.env.get("MACOS_CERT_PASSWORD", ""), "AHSYNC_TEMP_PASS": throwaway}
    world.cmd(["security", "set-keychain-settings", "-lut", "21600", keychain], "security set-keychain-settings")
    world.cmd(["security", "unlock-keychain", "-p", throwaway, keychain], "security unlock-keychain")
    try:
        world.cmd([OPENSSL, "pkcs12", "-in", p12, "-passin", "env:MACOS_CERT_PASSWORD",
                   "-passout", "env:AHSYNC_TEMP_PASS", "-out", pem], "openssl pkcs12", extra_env=passwords)
        world.cmd([OPENSSL, "pkcs12", "-export", "-in", pem, "-out", reexported, "-passin", "env:AHSYNC_TEMP_PASS",
                   "-passout", "env:AHSYNC_TEMP_PASS"], "openssl pkcs12 -export", extra_env=passwords)
        world.cmd(["security", "import", reexported, "-k", keychain, "-P", throwaway, "-T", "/usr/bin/codesign"],
                  "security import")
    except BuildError:
        raise BuildError("certificate invalid: could not import MACOS_CERT_P12") from None
    world.cmd(["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s",
               "-k", throwaway, keychain], "security set-key-partition-list", capture=True)   # its dump is noise
    for cert in APPLE_CAS:   # the chain to Apple's root: without it the runner sees the identity as untrusted
        if world.run(["security", "import", str(world.root / cert), "-k", keychain]).code != 0:
            world.out(f"note: could not import {cert} (already in the keychain?)")
    world.cmd(["security", "list-keychains", "-d", "user", "-s", keychain, "login.keychain-db"],
              "security list-keychains")


def find_identity(world: World, keychain: str) -> str:
    """The Developer ID Application identity for APPLE_TEAM_ID in the keychain.

    Args:
        world: The outside world.
        keychain: The temporary keychain.

    Returns:
        Its name, "Developer ID Application: <name> (<team>)".

    Raises:
        BuildError: "certificate invalid: no Developer ID Application identity for team <team>", after logging
            what the keychain holds (see `log_identities`).
    """
    team = secret_id(world, "APPLE_TEAM_ID")
    listing = world.cmd(["security", "find-identity", "-v", "-p", "codesigning", keychain],
                        "security find-identity", capture=True).out
    for match in IDENTITY_RE.finditer(listing):
        if match.group(2) == team:
            return match.group(1)
    log_identities(world, keychain)
    raise BuildError(f"certificate invalid: no Developer ID Application identity for team {team}")


def log_identities(world: World, keychain: str) -> None:
    """Log every code-signing identity in the keychain, valid or not, by kind, team and status, never by name.

    The names hold the developer's real name, and a public repository's logs are public; the team ID is a
    secret, which GitHub masks.

    Args:
        world: The outside world.
        keychain: The temporary keychain.
    """
    listing = world.run(["security", "find-identity", "-p", "codesigning", keychain], capture=True).out
    found = IDENTITY_ANY_RE.findall(listing)
    world.out(f"find-identity: {len(found)} code-signing identities in the keychain")
    for kind, team, status in found:
        world.out(f"  {kind} · team {team or '?'} · {status or 'valid'}")


def days_left(world: World, keychain: str, identity: str) -> int:
    """Whole days until the identity's certificate expires.

    Args:
        world: The outside world (its clock).
        keychain: The temporary keychain.
        identity: The identity's name.

    Returns:
        floor((notAfter - now) / 1 day).

    Raises:
        BuildError: "certificate invalid: no expiry date" when openssl's answer can't be read.
    """
    pem = world.cmd(["security", "find-certificate", "-c", identity, "-p", keychain],
                    "security find-certificate", capture=True).out
    answer = world.cmd([OPENSSL, "x509", "-noout", "-enddate"], "openssl x509", input_text=pem, capture=True).out
    try:
        not_after = calendar.timegm(time.strptime(answer.strip().removeprefix("notAfter="), "%b %d %H:%M:%S %Y %Z"))
    except ValueError:
        raise BuildError("certificate invalid: no expiry date") from None
    return int((not_after - world.now_s()) // 86400)


def preflight(world: World) -> None:
    """Check that a release could be signed, without signing anything (SPEC §3.7.12; DR19).

    Args:
        world: The outside world.

    Raises:
        BuildError: "signing preflight failed: secret <name> is not set", or "...: certificate invalid: ...".
    """
    missing = missing_secrets(world.env)
    if missing:
        raise BuildError(f"signing preflight failed: secret {missing[0]} is not set")
    try:
        with signing_keychain(world) as keychain:
            identity = find_identity(world, keychain)
            days = days_left(world, keychain, identity)
    except BuildError as error:
        raise BuildError(f"signing preflight failed: {error}") from None
    if days < EXPIRY_WARN_DAYS:
        world.out(f"::warning::signing certificate expires in {days} days")
    world.out("signing preflight passed")


# ---------------------------------------------------------------- the build (DR17, DR18)

@contextmanager
def stamped_version(root: Path, version: str, built: str) -> Iterator[None]:
    """Write the version and build date into ahsync/version.py for the build, and put the file back after.

    Args:
        root: The repository root.
        version: "MAJOR.MINOR.PATCH".
        built: "YYYY-MM-DD".

    Returns:
        A context manager; the source file is unchanged once it exits.
    """
    path = root / "ahsync" / "version.py"
    original = path.read_bytes()
    text = original.decode("utf-8")
    text = re.sub(r'^VERSION = "[^"]*"', f'VERSION = "{version}"', text, count=1, flags=re.M)
    text = re.sub(r'^BUILT = "[^"]*"', f'BUILT = "{built}"', text, count=1, flags=re.M)
    path.write_bytes(text.encode("utf-8"))
    try:
        yield
    finally:
        path.write_bytes(original)


def pyinstaller(world: World, extra_env: dict[str, str] | None = None) -> None:
    """Run the one recipe, packaging/ahsync.spec.

    Args:
        world: The outside world.
        extra_env: The signing identity and entitlements for the spec, in mode sign.
    """
    world.cmd([world.python, "-m", "PyInstaller", "--noconfirm", SPEC_FILE], "PyInstaller", extra_env=extra_env)


def build_windows(world: World, version: str) -> list[str]:
    """PyInstaller, then Inno Setup: the Windows installer.

    Args:
        world: The outside world.
        version: "MAJOR.MINOR.PATCH".

    Returns:
        The artifact paths under dist/.
    """
    pyinstaller(world)
    iscc = world.env.get("ISCC") or world.which("ISCC") or ISCC_DEFAULT
    world.cmd([iscc, f"/DAppVersion={version}", ISS_FILE], "Inno Setup")
    return [f"dist/{artifact_names(version)['windows']}"]


def notarize(world: World, path: str) -> None:
    """Submit a file to Apple's notary service and wait (the key from a private temp file).

    Args:
        world: The outside world (NOTARY_KEY_P8, NOTARY_KEY_ID, NOTARY_ISSUER_ID).
        path: The zip or dmg to submit.
    """
    with private_folder() as folder:
        key = write_private(folder / "notary.p8", world.env.get("NOTARY_KEY_P8", "").encode("utf-8"))
        world.cmd(["xcrun", "notarytool", "submit", path, "--wait", "--key", str(key),
                   "--key-id", secret_id(world, "NOTARY_KEY_ID"), "--issuer", secret_id(world, "NOTARY_ISSUER_ID")],
                  "notarytool submit")


def build_macos_signed(world: World, version: str) -> list[str]:
    """Sign, notarize and staple the app, then make the dmg (signed, notarized, stapled) and the release zip.

    The dmg is signed and notarized itself because stapler looks up a ticket by the file's own signature; the
    app's ticket does not cover it (LESSONS 2026-11-02).

    Args:
        world: The outside world, with the six secrets.
        version: "MAJOR.MINOR.PATCH".

    Returns:
        The dmg and zip paths under dist/.
    """
    names = artifact_names(version)
    dmg, release_zip = f"dist/{names['dmg']}", f"dist/{names['zip']}"
    with signing_keychain(world) as keychain:
        identity = find_identity(world, keychain)
        pyinstaller(world, {"AHSYNC_CODESIGN_IDENTITY": identity, "AHSYNC_ENTITLEMENTS": str(world.root / ENTITLEMENTS)})
        world.cmd(["codesign", "--verify", "--deep", "--strict", APP], "codesign --verify")
        world.cmd(["ditto", "-c", "-k", "--keepParent", APP, NOTARIZE_ZIP], "ditto")
        notarize(world, NOTARIZE_ZIP)
        world.cmd(["xcrun", "stapler", "staple", APP], "stapler staple")
        world.cmd([DMG_SCRIPT, version], "make_dmg.sh")
        world.cmd(["codesign", "--sign", identity, "--timestamp", "--keychain", keychain, dmg], "codesign dmg")
        notarize(world, dmg)
        world.cmd(["xcrun", "stapler", "staple", dmg], "stapler staple")
        world.cmd(["ditto", "-c", "-k", "--keepParent", APP, release_zip], "ditto")
    return [dmg, release_zip]


def build_macos(world: World, version: str, mode: str) -> list[str]:
    """The macOS build: signed in mode sign, ad hoc otherwise (PyInstaller's default).

    Args:
        world: The outside world.
        version: "MAJOR.MINOR.PATCH".
        mode: "sign", "preflight" or "unsigned".

    Returns:
        The dmg and zip paths under dist/.
    """
    if mode == "sign":
        return build_macos_signed(world, version)
    names = artifact_names(version)
    pyinstaller(world)
    world.cmd([DMG_SCRIPT, version], "make_dmg.sh")
    world.cmd(["ditto", "-c", "-k", "--keepParent", APP, f"dist/{names['zip']}"], "ditto")
    return [f"dist/{names['dmg']}", f"dist/{names['zip']}"]


def build_command(world: World, version: str, mode: str) -> int:
    """`build.py build <version>`: stamp the version, PyInstaller, this OS's packaging step (DR17, DR18).

    Args:
        world: The outside world.
        version: "MAJOR.MINOR.PATCH" ("0.0.0" for a build that is not a release).
        mode: "sign", "preflight" or "unsigned" (from `policy`); the Windows build is the same in every mode.

    Returns:
        0 once the artifacts are made.

    Raises:
        BuildError: a bad version, mode sign without the secrets, or a step that failed.
    """
    if not VERSION_RE.fullmatch(version):
        raise BuildError("the version must be MAJOR.MINOR.PATCH")
    if world.os_name == "macos" and mode == "sign" and missing_secrets(world.env):
        raise BuildError("macOS signing secrets are required for a release (SPEC §3.7): missing "
                         + ", ".join(missing_secrets(world.env)))
    built = time.strftime("%Y-%m-%d", time.gmtime(world.now_s()))
    with stamped_version(world.root, version, built):
        artifacts = build_windows(world, version) if world.os_name == "windows" else build_macos(world, version, mode)
    for artifact in artifacts:
        world.out(f"built {artifact}")
    return 0


# ---------------------------------------------------------------- the release (DR28)

def sha256_file(path: Path) -> str:
    """The SHA-256 of a file, lower-case hex.

    Args:
        path: The file.

    Returns:
        64 hex digits.
    """
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_command(world: World, tag: str) -> int:
    """`build.py release <tag>`: write dist/SHA256SUMS and publish the GitHub pre-release (SPEC §3.7.10; DR28).

    Args:
        world: The outside world (gh authenticates through GH_TOKEN in the workflow).
        tag: The release tag, vMAJOR.MINOR.PATCH.

    Returns:
        0 once `gh release create` succeeded.

    Raises:
        BuildError: a suffixed tag (no gh runs), a missing asset, or gh failing.
    """
    version = tag_version(tag)
    if version is None:
        raise BuildError("release tags are vMAJOR.MINOR.PATCH")
    dist = world.root / "dist"
    names = sorted(artifact_names(version).values())
    missing = [name for name in names if not (dist / name).is_file()]
    if missing:
        raise BuildError("dist/ lacks " + ", ".join(missing))
    sums = "".join(f"{sha256_file(dist / name)}  {name}\n" for name in names)
    (dist / "SHA256SUMS").write_bytes(sums.encode("ascii"))
    (dist / "RELEASE_NOTES.md").write_bytes(
        (f"How to install: [INSTALL.md]({INSTALL_URL.format(tag=tag)}). Windows: the -setup.exe. "
         "macOS (Apple Silicon): the .dmg.\n").encode("utf-8"))
    assets = [f"dist/{name}" for name in artifact_names(version).values()] + ["dist/SHA256SUMS"]
    world.cmd(["gh", "release", "create", tag, "--prerelease", "--generate-notes",
               "--notes-file", "dist/RELEASE_NOTES.md", "--title", f"AHSync {version}", *assets], "gh release create")
    world.out(f"released {tag} as a pre-release")
    return 0


# ---------------------------------------------------------------- entry point

def real_run(args: list[str], *, input_text: str | None = None, extra_env: dict[str, str] | None = None,
             capture: bool = False) -> Result:
    """Run a command in the repository root, without the credentials in its environment.

    PyInstaller's hooks and the packaging tools run third-party code, so the certificate, its password and the
    notary key are removed from every child's environment; the one command that needs a value (openssl, the
    password) gets it through extra_env.

    Args:
        args: The argument list (never a shell string).
        input_text: Standard input, if any.
        extra_env: Variables added to the environment.
        capture: Capture standard output (else it streams to the job's log).

    Returns:
        The exit code (124 on a timeout, 127 when the program is missing) and the captured output.
    """
    env = {name: value for name, value in os.environ.items() if name not in CREDENTIALS}
    try:
        proc = subprocess.run(args, cwd=ROOT, input=input_text, text=True, check=False, timeout=RUN_TIMEOUT_S,
                              stdout=subprocess.PIPE if capture else None, env={**env, **(extra_env or {})})
    except subprocess.TimeoutExpired:
        return Result(124)
    except FileNotFoundError:
        return Result(127)
    return Result(proc.returncode, proc.stdout or "")


def main(argv: list[str] | None = None, world: World | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; None reads sys.argv.
        world: The outside world; None builds the real one.

    Returns:
        0 on success, 1 when a step failed (its message printed as a GitHub error line).
    """
    parser = argparse.ArgumentParser(prog="build.py", description="Build, sign and release AHSync (SPEC §3.7.12)")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="PyInstaller, then this OS's packaging step")
    build.add_argument("version", help="MAJOR.MINOR.PATCH, 0.0.0 for a local build")
    build.add_argument("--mode", choices=("sign", "preflight", "unsigned"), default="unsigned")
    commands.add_parser("policy", help="CI: the signing mode, the secrets check or the preflight")
    release = commands.add_parser("release", help="CI: SHA256SUMS and the GitHub pre-release")
    release.add_argument("tag")
    args = parser.parse_args(argv)
    world = world or World(real_run, os.environ, "macos" if sys.platform == "darwin" else "windows",
                           time.time, lambda line: print(line, flush=True), ROOT)
    try:
        if args.command == "build":
            return build_command(world, args.version, args.mode)
        if args.command == "policy":
            return policy_command(world)
        return release_command(world, args.tag)
    except BuildError as error:
        world.out(f"::error::{world.redact(str(error))}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
