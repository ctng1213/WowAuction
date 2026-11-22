"""The object store the cloud job reads and writes: S3 in Lambda, a folder on a laptop (SPEC §2.4).

Keys are S3-style ("raw/<market>/2026/11/06/140240.json.gz"), always with "/". `LocalStore` maps them under a
root folder and refuses any key that could leave it. A write can be conditional on the object's current
ETag (`if_match`), so a run that lost a race fails instead of overwriting (BZ11); every write is atomic. A large
object (the history) moves between a file and the store in chunks (`get_file`, `put_file`), never whole in memory
and never as a second file (FM91).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CHUNK_BYTES = 8 * 1024 * 1024              # a streamed read or hash takes this much at a time
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

    def etag(self, key: str) -> str | None:
        """An object's ETag without reading it.

        Args:
            key: The object's key.

        Returns:
            Its ETag (the one `get` and `get_file` give); None when there is no such object.
        """
        ...

    def get_file(self, key: str, path: Path) -> str | None:
        """Read an object into a file, a chunk at a time (FM91).

        Args:
            key: The object's key.
            path: The file to write; replaced whole, atomically. Left untouched when there is no object.

        Returns:
            Its ETag; None when there is no such object.
        """
        ...

    def put_file(self, key: str, path: Path, if_match: str | None = None) -> str:
        """Write a file as an object, a chunk at a time, atomically (FM91).

        Args:
            key: The object's key.
            path: The file; it must not change while it is written.
            if_match: Write only if the object's ETag is still this one; None writes unconditionally.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: if_match no longer matches (or the object is gone); nothing was written.
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


def file_etag(path: Path) -> str:
    """A local ETag of a file, read a chunk at a time: the same value `etag_of` gives its bytes.

    Args:
        path: The file.

    Returns:
        Hex sha256 digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def copy_atomically(source: Path, target: Path) -> None:
    """Copy a file through a temporary file beside the target and a rename.

    Args:
        source: The file to copy.
        target: Replaced whole; its folder is created.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    os.close(handle)
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


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

    def etag(self, key: str) -> str | None:
        """An object's ETag (see Store.etag).

        Args:
            key: The object's key.

        Returns:
            Its ETag, or None.
        """
        path = self._path(key)
        return file_etag(path) if path.is_file() else None

    def get_file(self, key: str, path: Path) -> str | None:
        """Copy an object into a file (see Store.get_file).

        Args:
            key: The object's key.
            path: The file to write.

        Returns:
            Its ETag, or None.
        """
        source = self._path(key)
        if not source.is_file():
            return None
        copy_atomically(source, path)
        return file_etag(path)

    def put_file(self, key: str, path: Path, if_match: str | None = None) -> str:
        """Copy a file in as an object (see Store.put_file).

        Args:
            key: The object's key.
            path: The file.
            if_match: The ETag it must still have, or None.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: if_match no longer matches.
        """
        if if_match is not None and self.etag(key) != if_match:
            raise PreconditionFailed(key)
        target = self._path(key)
        copy_atomically(path, target)
        return file_etag(target)

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

    def etag(self, key: str) -> str | None:
        """The overlay's ETag, else the base's (see Store.etag).

        Args:
            key: The object's key.

        Returns:
            Its ETag, or None.
        """
        return self.overlay.etag(key) or self.base.etag(key)

    def get_file(self, key: str, path: Path) -> str | None:
        """The overlay's object, else the base's, into a file (see Store.get_file).

        Args:
            key: The object's key.
            path: The file to write.

        Returns:
            Its ETag, or None.
        """
        found = self.overlay.get_file(key, path)
        return found if found is not None else self.base.get_file(key, path)

    def put_file(self, key: str, path: Path, if_match: str | None = None) -> str:
        """Write a file to the overlay (see Store.put_file), checked against the base as `put` is.

        Args:
            key: The object's key.
            path: The file.
            if_match: The ETag it must still have, or None.

        Returns:
            The new ETag.

        Raises:
            PreconditionFailed: if_match no longer matches.
        """
        if if_match is not None and self.overlay.etag(key) is None and (base := self.base.etag(key)) is not None:
            if base != if_match:
                raise PreconditionFailed(key)
            if_match = None
        return self.overlay.put_file(key, path, if_match)

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
