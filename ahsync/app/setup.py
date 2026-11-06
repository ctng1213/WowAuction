"""First-run setup's checks and writes: `config.toml` and the key file (SPEC §3.7.4; DR6–DR8, FM50).

Every field is checked before anything is written. Then, in order: `Interface/AddOns` when the
client has `Interface` without it, `config.toml` (temp file + rename), and the key file, made
readable by the user only. The key never appears in a message, a log line or a response.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ahsync.app.wow_paths import WowCheck, check
from ahsync.config import RULESETS
from ahsync.engine.app_view import FACTION_CHOICES, wow_check

log = logging.getLogger("ahsync.setup")

FACTIONS = tuple(code for code, _label in FACTION_CHOICES)
CONFIG_HEADER = ("# AHSync settings, written by the setup page (SPEC §3.7.4).\n"
                 "# Every other setting, with its default and meaning: docs/config.toml.\n")
KEY_FILE_MODE = 0o600
KEY_PATTERN = re.compile(r"[\x21-\x7e]{1,256}")  # one line of printable ASCII: it travels in an HTTP header
KEY_MESSAGE = "api_key must be one line of letters, digits and punctuation, at most 256 characters"


@dataclass(frozen=True)
class SetupRequest:
    """A checked `POST /api/setup` body."""

    wow_path: str
    ruleset: str
    faction: str
    autostart: bool
    api_key: str = field(default="", repr=False)    # stripped; "" when none was given; never printed

    @property
    def market(self) -> str:
        """The market id the choices make, e.g. "forever.pvp.horde.us".

        Returns:
            The id.
        """
        return f"forever.{self.ruleset}.{self.faction}.us"


class SetupError(ValueError):
    """A field of the setup body is wrong; the message says which, and never quotes the key."""


class KeyNotSaved(OSError):
    """config.toml was written but the key file could not be (FM50)."""

    def __init__(self, reason: str) -> None:
        """Keep the reason.

        Args:
            reason: Lower-case, e.g. "permission denied"; never a path or the key.
        """
        super().__init__(reason)
        self.reason = reason


def read_body(body: bytes) -> dict:
    """Decode a setup body.

    Args:
        body: The request body.

    Returns:
        The JSON object.

    Raises:
        SetupError: When it is not a JSON object.
    """
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        request = None
    if not isinstance(request, dict):
        raise SetupError("the body must be a JSON object")
    return request


def folder_check(wow_path: object) -> dict:
    """The Check button's answer for a typed folder (DR6): reads only.

    Args:
        wow_path: The folder from the body.

    Returns:
        {"ok", "text"}.
    """
    path = typed_folder(wow_path)
    found = check(path) if path and Path(path).is_absolute() else WowCheck(False, False, 0)
    result = wow_check(path, found.has_interface, found.has_addons, found.accounts, creatable=True,
                       is_forever=found.is_forever)
    return {"ok": result["ok"], "text": result["text"]}


def typed_folder(wow_path: object) -> str:
    """The folder as the user meant it: trimmed, one pair of surrounding quotes removed (Windows' "Copy as
    path" adds them), a leading ~ expanded. A relative folder never passes the check: a login-started app
    runs from another working folder.

    Args:
        wow_path: The body's wow_path.

    Returns:
        The folder text; "" when it is not a string.
    """
    if not isinstance(wow_path, str):
        return ""
    path = wow_path.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        path = path[1:-1].strip()
    return str(Path(path).expanduser()) if path.startswith("~") else path


def parse_setup(request: dict) -> SetupRequest:
    """Check every field of a save (DR8), before anything is written.

    Args:
        request: The decoded body: wow_path, ruleset, faction, api_key (optional), autostart.

    Returns:
        The checked request, the key stripped of whitespace.

    Raises:
        SetupError: With the first wrong field's message, e.g. "ruleset must be normal, pvp or rp".
    """
    answer = folder_check(request.get("wow_path"))
    if not answer["ok"]:
        raise SetupError(answer["text"])
    if request.get("ruleset") not in RULESETS:
        raise SetupError("ruleset must be normal, pvp or rp")
    if request.get("faction") not in FACTIONS:
        raise SetupError("faction must be alliance or horde")
    key = request.get("api_key", "")
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key.strip() or "-"):
        raise SetupError(KEY_MESSAGE)                  # never quotes the value (it may be a real key)
    autostart = request.get("autostart")
    if not isinstance(autostart, bool):
        raise SetupError("autostart must be true or false")
    return SetupRequest(typed_folder(request["wow_path"]), request["ruleset"], request["faction"], autostart,
                        key.strip())


def config_text(request: SetupRequest) -> str:
    """The config.toml setup writes: the market and the folder, and a pointer to the rest (SPEC §3.7.4).

    Args:
        request: The checked request.

    Returns:
        TOML text that reads back to the same market and folder.
    """
    # A JSON string is a valid TOML basic string: backslashes and quotes in a Windows path are escaped.
    return f'{CONFIG_HEADER}markets = ["{request.market}"]\nwow_path = {json.dumps(request.wow_path, ensure_ascii=False)}\n'


def write_config(path: Path, request: SetupRequest) -> None:
    """Write config.toml atomically: temp file, check it reads back, rename (SPEC §5.2).

    Args:
        path: config.toml.
        request: The checked request.

    Raises:
        OSError: When the file can't be written; nothing is left half-written.
    """
    text = config_text(request)
    try:
        reads_back = tomllib.loads(text) == {"markets": [request.market], "wow_path": request.wow_path}
    except tomllib.TOMLDecodeError:                 # a control character TOML refuses in the folder name
        reads_back = False
    if not reads_back:
        raise OSError("config.toml would not read back")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(temp, path)
    except (OSError, ValueError) as error:          # ValueError: a folder name UTF-8 can't encode
        temp.unlink(missing_ok=True)
        raise OSError(f"config.toml not written: {type(error).__name__}") from None


def write_key_file(path: Path, key: str) -> None:
    """Write the key file: a new temp file readable by the user only, then renamed over it (SPEC §3.7.4, §5.2).

    A failed write leaves any earlier key file as it was; the rename replaces a link at the path, never
    its target.

    Args:
        path: ~/.ahsync/api_key.
        key: The stripped key.

    Raises:
        OSError: When the folder or the file can't be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    handle = os.open(temp, flags, KEY_FILE_MODE)      # a new file: the mode holds from its first byte
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def save_setup(request: SetupRequest, config_file: Path, home: Path, restrict_file: Callable[[Path], None],
               write_key: Callable[[Path, str], None] = write_key_file) -> None:
    """Write what setup chose, in order: AddOns when missing, config.toml, the key file (DR6, DR7, FM50).

    Args:
        request: The checked request.
        config_file: The app's config.toml.
        home: The user's home folder (the key file is ~/.ahsync/api_key, SPEC §3.6).
        restrict_file: The platform's restrict_file (0600 on POSIX).
        write_key: Writes the key file (write_key_file; a failing fake in tests).

    Raises:
        OSError: When AddOns or config.toml can't be written (nothing after it is attempted).
        KeyNotSaved: When config.toml was written but the key file wasn't; the config is kept.
    """
    addons = Path(request.wow_path) / "Interface" / "AddOns"
    if not addons.is_dir():
        addons.mkdir(parents=True)         # a fresh client has no addon folder until an addon is installed (DR6)
    write_config(config_file, request)
    log.info("setup: config.toml written for %s", request.market)
    if not request.api_key:
        return
    key_file = home / ".ahsync" / "api_key"
    try:
        write_key(key_file, request.api_key)
        restrict_file(key_file)
    except OSError as error:
        reason = (error.strerror or type(error).__name__).lower()
        log.warning("setup: the key file could not be written: %s", reason)
        raise KeyNotSaved(reason) from None
    log.info("setup: key file written")
