"""The object store the cloud job reads and writes: S3 in Lambda, a folder on a laptop (SPEC §2.4).

Keys are S3-style ("raw/<market>/2026/11/06/140240.json.gz"), always with "/". `LocalStore` maps them under a
root folder and refuses any key that could leave it. A write can be conditional on the object's current
ETag (`if_match`), so a run that lost a race fails instead of overwriting (BZ11); every write is atomic.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

KEY = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9._\-/]+$")   # no absolute path, no "." or ".." part


class PreconditionFailed(Exception):
    """A conditional write found the object changed since it was read."""


@dataclass(frozen=True)
class StoredObject:
    """An object's bytes and its ETag."""

    data: bytes
    etag: str


class Store(Protocol):
    """What the cloud job needs from an object store."""

    def get(self, key: str) -> StoredObject | None:
        """Read an object.

        Args:
            key: The object's key.

        Returns:
            Its bytes and ETag; None when there is no such object.
        """
        ...

    def put(self, key: str, data: bytes, if_match: str | None = None) -> str:
        """Write an object atomically.

        Args:
            key: The object's key.
            data: Its bytes.
            if_match: Write only if the object's ETag is still this one; None writes unconditionally.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: if_match no longer matches (or the object is gone).
        """
        ...

    def delete(self, key: str) -> None:
        """Delete an object; deleting a missing one is not an error.

        Args:
            key: The object's key.
        """
        ...

    def list(self, prefix: str, start_after: str = "") -> list[str]:
        """The keys under a prefix, sorted.

        Args:
            prefix: Key prefix, e.g. "raw/forever.normal.horde.us/".
            start_after: Only keys sorting after this one.

        Returns:
            Keys in ascending order.
        """
        ...


def check_key(key: str) -> str:
    """Refuse a key that is empty, absolute, or has a "." or ".." part.

    Args:
        key: An object key.

    Returns:
        The key.

    Raises:
        ValueError: The key is not allowed.
    """
    if not KEY.match(key) or "//" in key:
        raise ValueError(f"object key not allowed: {key!r}")
    return key


def etag_of(data: bytes) -> str:
    """A local ETag: the content's sha256 (S3's are its own; callers only compare them).

    Args:
        data: The bytes.

    Returns:
        Hex digest.
    """
    return hashlib.sha256(data).hexdigest()


class LocalStore:
    """A Store over a folder: key "a/b.json" is the file <root>/a/b.json."""

    def __init__(self, root: Path) -> None:
        """Use a folder (created on the first write).

        Args:
            root: The store's folder.
        """
        self.root = root

    def _path(self, key: str) -> Path:
        """The file of a key.

        Args:
            key: An object key.

        Returns:
            Its path under the root.

        Raises:
            ValueError: The key is not allowed.
        """
        return self.root.joinpath(*check_key(key).split("/"))

    def get(self, key: str) -> StoredObject | None:
        """Read an object (see Store.get).

        Args:
            key: The object's key.

        Returns:
            Its bytes and ETag, or None.
        """
        path = self._path(key)
        if not path.is_file():
            return None
        data = path.read_bytes()
        return StoredObject(data, etag_of(data))

    def put(self, key: str, data: bytes, if_match: str | None = None) -> str:
        """Write an object through a temporary file and a rename (see Store.put).

        Args:
            key: The object's key.
            data: Its bytes.
            if_match: The ETag it must still have, or None.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: if_match no longer matches.
        """
        path = self._path(key)
        if if_match is not None:
            current = self.get(key)
            if current is None or current.etag != if_match:
                raise PreconditionFailed(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        return etag_of(data)

    def delete(self, key: str) -> None:
        """Delete an object (see Store.delete).

        Args:
            key: The object's key.
        """
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str, start_after: str = "") -> list[str]:
        """The keys under a prefix (see Store.list); temporary files are not keys.

        Args:
            prefix: Key prefix.
            start_after: Only keys sorting after this one.

        Returns:
            Keys in ascending order.
        """
        if not self.root.is_dir():
            return []
        keys = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith(".tmp-"):
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix) and key > start_after:
                    keys.append(key)
        return sorted(keys)


class OverlayStore:
    """Reads from an overlay first, then a base; writes and deletes touch only the overlay (plan decision 17).

    So a laptop can reproduce a cloud run with `--store s3://<bucket> --out data/cloud`: the raw comes from the
    bucket, and the state and the documents the run writes stay in the folder, never in the bucket.
    """

    def __init__(self, base: Store, overlay: Store) -> None:
        """Layer an overlay over a base.

        Args:
            base: Read only.
            overlay: Read first; every write goes here.
        """
        self.base, self.overlay = base, overlay

    def get(self, key: str) -> StoredObject | None:
        """The overlay's object, else the base's (see Store.get).

        Args:
            key: The object's key.

        Returns:
            Its bytes and ETag, or None.
        """
        return self.overlay.get(key) or self.base.get(key)

    def put(self, key: str, data: bytes, if_match: str | None = None) -> str:
        """Write to the overlay (see Store.put). A conditional write on a key only the base has copies it first.

        Args:
            key: The object's key.
            data: Its bytes.
            if_match: The ETag it must still have, or None.

        Returns:
            The new ETag.
        """
        if if_match is not None and self.overlay.get(key) is None and (base := self.base.get(key)) is not None:
            if base.etag != if_match:
                raise PreconditionFailed(key)
            if_match = None
        return self.overlay.put(key, data, if_match)

    def delete(self, key: str) -> None:
        """Delete from the overlay only (see Store.delete).

        Args:
            key: The object's key.
        """
        self.overlay.delete(key)

    def list(self, prefix: str, start_after: str = "") -> list[str]:
        """The keys of both, merged (see Store.list).

        Args:
            prefix: Key prefix.
            start_after: Only keys sorting after this one.

        Returns:
            Keys in ascending order.
        """
        return sorted(set(self.base.list(prefix, start_after)) | set(self.overlay.list(prefix, start_after)))
