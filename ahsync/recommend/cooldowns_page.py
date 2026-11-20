"""The Cooldowns page's edge: the curated catalog file and its Status line (SPEC §3.4F.1, §3.5).

This module reads the catalog data file (`ahsync/data/cooldown_catalog.json`), and engine.cooldowns
validates it. A row the catalog can't use is skipped with a Status line (CD2); a catalog that can't be
read leaves the page empty with a Status line, and the rest of the refresh carries on. Built CD1: the
catalog's reading and its Status line. The page itself (prices, ready times, the view) comes with CD3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ahsync.engine.cooldowns import CooldownRecipe, catalog, catalog_text

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "cooldown_catalog.json"
STATUS_TOPIC = "cooldown_catalog"


class StatusWrites(Protocol):
    """Where the catalog's Status line goes (db.sqlite.Database satisfies it)."""

    def set_status(self, topic: str, level: str, kind: str, data: dict, at: int) -> None:
        """Set a topic's Status line (db.sqlite).

        Args:
            topic: Stable id.
            level: 'info', 'warn' or 'error'.
            kind: Message kind.
            data: Structured fields.
            at: Time, ms.
        """


def read_catalog(path: Path = CATALOG_PATH) -> tuple[list[Any], int | None, str]:
    """Read the catalog data file's rows and version (SPEC §3.4F.1).

    Args:
        path: The JSON file: {"_comment", "version", "recipes": [...]}.

    Returns:
        (its "recipes" rows, its version, "") or ([], None, why it couldn't be read). A version that
        isn't a positive int is None.
    """
    unread = "cooldown catalog not read ({}): the Cooldowns page is empty"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document["recipes"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [], None, unread.format(type(error).__name__)
    if not isinstance(rows, list):
        return [], None, unread.format("no recipes list")
    version = document.get("version")
    return rows, version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else None, ""


def load_catalog(db: StatusWrites, now_ms: int, path: Path = CATALOG_PATH) -> tuple[list[CooldownRecipe], int | None]:
    """Read and validate the catalog, and keep its Status line current (CD1, CD2).

    Args:
        db: Where the Status line goes.
        now_ms: Now, for the Status line.
        path: The catalog file.

    Returns:
        (the valid rows by spell id, the file's version). Side effect: sets the `cooldown_catalog` Status line:
        "cooldown catalog: 14 recipes" (info), or the skipped rows or why the file couldn't be read (warn).
    """
    rows, version, unread = read_catalog(path)
    kept, skipped = catalog(rows)
    text = unread or catalog_text(kept, skipped)
    db.set_status(STATUS_TOPIC, "warn" if unread or skipped else "info", "cooldown_catalog", {"text": text}, now_ms)
    return kept, version
