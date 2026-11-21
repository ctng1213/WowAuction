"""Which character stored without a realm folds into which real id (SPEC §2.2.1, RL4).

Since a client patch (found 2026-11-20) Forever can report an empty realm name, so the addon saved those
sessions as ``Name-``. The addon now uses the stored realm and folds its own copy at login (RL1, RL2); the
importer then folds what AHSync had stored under ``Name-`` (``Database.move_character``).
"""
from __future__ import annotations

from collections.abc import Collection, Iterable


def realmless_folds(characters: Iterable[tuple[str, str | None, str | None, str | None]],
                    file_char_ids: Collection[str]) -> list[tuple[str, str]]:
    """Which stored realm-less characters fold into which real ids, for one file being read.

    Only the file the addon folded can trigger a fold: it lists the real id and no longer lists ``Name-``.
    A file from another account, which lists neither, folds nothing. The match is the addon's
    (``Characters.StoredRealm``): the same name, class and faction.

    Args:
        characters: Stored (id "Name-Realm", realm, class, faction); WoW names never contain "-".
        file_char_ids: The character ids the file being read lists.

    Returns:
        (realm-less id, real id) pairs, by realm-less id: an id ``Name-`` with an empty realm that the file
        doesn't list, with exactly one stored id of the same name, class and faction that has a realm, and
        that one the file lists.
    """
    rows = sorted(characters, key=lambda r: r[0])
    folds = []
    for char_id, realm, class_token, faction in rows:
        if realm or not char_id.endswith("-") or char_id in file_char_ids:
            continue
        name = char_id[:-1]
        targets = [other for other, other_realm, other_class, other_faction in rows
                   if other_realm and other.split("-", 1)[0] == name
                   and (other_class, other_faction) == (class_token, faction)]
        if len(targets) == 1 and targets[0] in file_char_ids:
            folds.append((char_id, targets[0]))
    return folds
