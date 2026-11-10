"""Find Claude Desktop's config and read what it says about AHSync, read-only (SPEC §3.8.1, MCP18-MCP23).

The Status card tells the user which file to change and what to put in it; the user makes the change. This module
never creates, writes or renames anything of Claude's. It reads one file, at most 1 MB, and keeps only
`mcpServers.ahsync`: other servers' entries can hold keys, so nothing else leaves this module.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from ahsync.engine.mcp_view import ClaudeConfig

CONFIG_NAME = "claude_desktop_config.json"
MAX_BYTES = 1_000_000                    # SPEC §3.8.1: never read more than 1 MB of Claude's file
STORE_PACKAGE = re.compile(r"Claude_[a-z0-9]{13}")    # the Microsoft Store's package folder for Claude Desktop
# The Store build's data folder, %LOCALAPPDATA%\Claude-Data\claude_<13>: its log loads the sign-in from there and
# its README calls it "the data Claude keeps" (found on 4.23118, 2026-11-09; SPEC §3.8.1). Its case varies.
DATA_FOLDER = re.compile(r"claude_[a-z0-9]{13}", re.IGNORECASE)


def find_claude_config(env: Mapping[str, str], home: Path, platform: str) -> ClaudeConfig:
    """Where Claude Desktop's config is on this computer and what it says about AHSync (SPEC §3.8.1).

    Args:
        env: The environment (APPDATA, LOCALAPPDATA on Windows).
        home: The user's home folder (macOS).
        platform: Python's name for the OS, passed in by ahsync/__main__.py: "win32", "darwin" or anything else.

    Returns:
        The config's state; "not_found" when no Claude folder exists. Never raises: a file that can't be read is
        the "unreadable" state.
    """
    system = "windows" if platform == "win32" else "macos" if platform == "darwin" else "other"
    data = [f for f in data_folders(env, system) if is_dir(f)]
    folders = data + [f for f in candidate_folders(env, home, system) if is_dir(f)]
    if not folders:
        return ClaudeConfig(system=system, state="not_found")
    chosen = choose_folder(data or folders)          # Claude's data folder, when there is one, is the one it reads
    others = [f for f in folders if f != chosen]
    found = read_config(chosen / CONFIG_NAME)
    return ClaudeConfig(system=system, state=found.state, path=str(chosen / CONFIG_NAME), entry=found.entry,
                        empty=found.empty, error_line=found.error_line, reason=found.reason,
                        section=found.section, other_folder=str(others[0]) if others else None)


def data_folders(env: Mapping[str, str], system: str) -> list[Path]:
    """The Store build's data folders, which Claude reads its config from whenever one exists (SPEC §3.8.1, MCP22).

    Args:
        env: The environment (LOCALAPPDATA).
        system: "windows", "macos" or "other".

    Returns:
        %LOCALAPPDATA%\\Claude-Data\\claude_<13 letters or digits> on Windows (either case), each still under
        Claude-Data once links are resolved, by name; [] elsewhere or when there is none.
    """
    if system != "windows" or not env.get("LOCALAPPDATA"):
        return []
    return linked_inside(Path(env["LOCALAPPDATA"]) / "Claude-Data", DATA_FOLDER, ())


def candidate_folders(env: Mapping[str, str], home: Path, system: str) -> list[Path]:
    """The other folders Claude Desktop keeps its config in, used when there is no data folder (SPEC §3.8.1, MCP18).

    Args:
        env: The environment.
        home: The home folder.
        system: "windows", "macos" or "other".

    Returns:
        Windows: %APPDATA%\\Claude, then each Store package's LocalCache\\Roaming\\Claude, by package name;
        macOS: ~/Library/Application Support/Claude; anything else: none. They may not exist.
    """
    if system == "macos":
        return [home / "Library" / "Application Support" / "Claude"]
    if system != "windows":
        return []
    folders = [Path(env["APPDATA"]) / "Claude"] if env.get("APPDATA") else []
    if env.get("LOCALAPPDATA"):
        folders += linked_inside(Path(env["LOCALAPPDATA"]) / "Packages", STORE_PACKAGE,
                                 ("LocalCache", "Roaming", "Claude"))
    return folders


def linked_inside(root: Path, pattern: re.Pattern, tail: tuple[str, ...]) -> list[Path]:
    """The folders root/<name>/<tail> whose name matches, and that stay under root once links are resolved.

    Args:
        root: The folder to list (%LOCALAPPDATA%\\Packages or Claude-Data).
        pattern: The whole name a subfolder must have.
        tail: The path inside each matching subfolder.

    Returns:
        The folders, sorted by subfolder name; [] when root can't be listed. They may not exist.
    """
    try:
        resolved = root.resolve()
        names = sorted(entry.name for entry in os.scandir(root) if entry.is_dir())
    except OSError:
        return []
    folders = []
    for name in names:
        if not pattern.fullmatch(name):
            continue
        folder = root.joinpath(name, *tail)
        try:
            if folder.resolve().is_relative_to(resolved):
                folders.append(folder)
        except OSError:
            continue
    return folders


def choose_folder(folders: list[Path]) -> Path:
    """The folder Claude uses: the one holding the config, else the one used last (SPEC §3.8.1).

    Args:
        folders: Existing candidate folders, in candidate_folders' order.

    Returns:
        The folder with claude_desktop_config.json; among several (or none), the one whose newest entry changed
        last; on a tie, the earliest in the list.
    """
    def rank(index_folder: tuple[int, Path]) -> tuple[bool, int, int]:
        """Sort key of one folder (higher wins).

        Args:
            index_folder: (its place in the list, the folder).

        Returns:
            (holds the config, newest change in ns, minus its place).
        """
        index, folder = index_folder
        return (is_file(folder / CONFIG_NAME), newest_change_ns(folder), -index)
    return max(enumerate(folders), key=rank)[1]


def is_dir(path: Path) -> bool:
    """Whether a folder exists, never raising (pathlib lets a permission error through, MC3b review).

    Args:
        path: The folder.

    Returns:
        True when it is a folder AHSync may look at.
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def is_file(path: Path) -> bool:
    """Whether a file exists, never raising.

    Args:
        path: The file.

    Returns:
        True when it is a file AHSync may look at.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def newest_change_ns(folder: Path) -> int:
    """When anything directly in a folder last changed.

    Args:
        folder: The folder.

    Returns:
        The newest modification time of its entries, ns since the epoch; 0 when it is empty or can't be listed.
    """
    try:
        with os.scandir(folder) as entries:
            return max((entry.stat(follow_symlinks=False).st_mtime_ns for entry in entries), default=0)
    except OSError:
        return 0


def read_config(path: Path) -> ClaudeConfig:
    """What one claude_desktop_config.json says about AHSync, read-only (SPEC §3.8.1, MCP19, MCP20).

    Args:
        path: The file.

    Returns:
        A ClaudeConfig with only state, entry, empty, error_line, reason and section set: "no_file" (missing, or blank with
        empty), "unreadable" (over 1 MB, or it can't be opened or decoded), "invalid" (not JSON, too deep or too big
        a number to parse, or a top level or mcpServers that isn't an object), "nested" (no top-level mcpServers,
        but one inside the section named by section), "no_servers" (empty: the file is `{}`), "no_entry" (empty:
        mcpServers is `{}`), or "entry" with the ahsync entry's command and args (an empty tuple when they aren't a
        string and a list of strings that can be shown).
    """
    try:
        if not path.is_file():
            return ClaudeConfig(system="", state="no_file")
        with path.open("rb") as file:
            raw = file.read(MAX_BYTES + 1)          # capped: never more than 1 MB in memory, however it grows
        if len(raw) > MAX_BYTES:
            return ClaudeConfig(system="", state="unreadable", reason="it's over 1 MB")
        text = raw.decode("utf-8-sig")
    except OSError:
        return ClaudeConfig(system="", state="unreadable", reason="it couldn't be opened")
    except UnicodeDecodeError:
        return ClaudeConfig(system="", state="unreadable", reason="it isn't UTF-8 text")
    if not text.strip():
        return ClaudeConfig(system="", state="no_file", empty=True)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        return ClaudeConfig(system="", state="invalid", error_line=error.lineno)
    except (ValueError, RecursionError):          # a 5,000-digit number, or "[" nested 200,000 deep (MC3b review)
        return ClaudeConfig(system="", state="invalid")
    if not isinstance(document, dict):
        return ClaudeConfig(system="", state="invalid")
    servers = document.get("mcpServers")
    if servers is None:
        section = nested_in(document)
        if section is not None:
            return ClaudeConfig(system="", state="nested", section=section)
        return ClaudeConfig(system="", state="no_servers", empty=not document)
    if not isinstance(servers, dict):
        return ClaudeConfig(system="", state="invalid")
    if "ahsync" not in servers:
        return ClaudeConfig(system="", state="no_entry", empty=not servers)
    return ClaudeConfig(system="", state="entry", entry=entry_command(servers["ahsync"]))


def nested_in(document: dict) -> str | None:
    """The top-level section an "mcpServers" was put inside by mistake, where Claude doesn't look (MCP23).

    Args:
        document: The parsed config, which has no top-level mcpServers.

    Returns:
        The first such section's name, in file order; None when there is none, or its name can't be shown as UTF-8.
        Only the name is kept, nothing in the section.
    """
    for name, value in document.items():
        if isinstance(value, dict) and "mcpServers" in value:
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:
                return None
            return name
    return None


def entry_command(entry: object) -> tuple[str, ...]:
    """The program and arguments of the ahsync entry, and nothing else of it (MCP20: env stays unread).

    Args:
        entry: mcpServers.ahsync as parsed.

    Returns:
        (command, *args); () when command isn't a string, args isn't a list of strings, or a part can't be shown
        as UTF-8 (a lone surrogate such as "\\ud800" is valid JSON but would break the page's answer).
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
        return ()
    args = entry.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return ()
    parts = (entry["command"], *args)
    try:
        for part in parts:
            part.encode("utf-8")
    except UnicodeEncodeError:
        return ()
    return parts
