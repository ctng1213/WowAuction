"""All SQLite access for AHSync (SPEC §3.2, §5.2).

- Opening checks the file with `PRAGMA quick_check`. A damaged file is moved
  aside and the newest healthy daily backup is restored (FM10).
- Migrations run on open; `meta.schema_version` records the version.
- Staging tables of an interrupted full refresh are dropped on open (FM7).
- Every write is an idempotent upsert; `transaction()` groups one commit unit.
- Up to 3 daily backups are kept in `data/backups/`, made with the SQLite backup API.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
# version -> SQL that upgrades version-1 to version. Version 1 is schema.sql itself.
MIGRATIONS: dict[int, str] = {}
BACKUP_KEEP = 3
BACKUP_PREFIX = "ahsync-"
STAGING_TABLES = ("item_stats_new", "plan_new")
CANDLE_TABLES = ("candles_h1", "candles_d1")
# Tables and time columns prune may delete from (SPEC §3.2 retention). Guards the SQL built in delete_before.
PRUNABLE = {
    "candles_h1": "t", "candles_d1": "t", "volume_d1": "day", "posts": "at", "purchases": "at",
    "crafts": "at", "auction_mail": "at", "mail_sent": "at", "actions": "run_at", "fetch_failures": "hour",
}


class DatabaseError(Exception):
    """The database can't be used: damaged with no backup to restore, or from a newer AHSync."""


@dataclass(frozen=True)
class SyncState:
    """Where catch-up left off for one market (SPEC §3.1)."""

    market: str
    last_hour: int | None = None     # newest hour handled, floor(ms/3600000)
    last_d1: int | None = None       # newest sealed day whose day candles are stored
    last_volume: int | None = None   # newest sealed day whose volume file was fetched
    live_since: int | None = None    # ms of the last live-table fetch; None without Premium
    last_run: int | None = None      # ms of the last run that reached this market


@dataclass(frozen=True)
class StatusMessage:
    """One line on the Status page, as structured fields; `views` renders it (SPEC §3.2)."""

    topic: str
    level: str       # 'info' | 'warn' | 'error'
    kind: str        # what happened, e.g. 'lost', 'bad_file', 'format_changed'
    data: dict       # its fields: ms times, counts, hour numbers, market and endpoint ids
    at: int          # ms


@dataclass(frozen=True)
class BookScan:
    """The scans row of one order book (SPEC §3.2)."""

    scan_id: str            # "ahf:<market>:<at>" for AHF books, the event id for own scans
    market: str
    src: str                # 'ahf' | 'local'
    char_id: str | None     # scanning character; None for AHF
    at: int                 # scan time, ms
    kind: str = "full"      # 'full' | 'partial'
    coverage: float = 1.0   # 0..1 share of the AH covered


@dataclass(frozen=True)
class CurrentPrice:
    """The newest lowest buyout for an item, whichever source saw it last (SPEC §3.3 `cur`)."""

    key: int
    price: int | None   # copper; None = nothing listed
    qty: int
    at: int             # observation time, ms
    src: str            # 'ahf' | 'local'


def is_healthy(path: Path) -> bool:
    """Run `PRAGMA quick_check` on a database file.

    Args:
        path: The database file.

    Returns:
        True when the check returns "ok"; False when it reports problems or the
        file isn't a database at all.
    """
    conn = None
    try:
        conn = sqlite3.connect(path)
        return conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    except sqlite3.DatabaseError:
        return False
    finally:
        if conn is not None:
            conn.close()


def backup_files(backup_dir: Path) -> list[Path]:
    """List daily backups, newest first.

    Args:
        backup_dir: data/backups.

    Returns:
        Paths named ahsync-YYYY-MM-DD.db, sorted newest first by name.
    """
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{BACKUP_PREFIX}????-??-??.db"), reverse=True)


def _copy_database(source: Path, target: Path) -> None:
    """Copy a database with the SQLite backup API.

    Args:
        source: Database to read.
        target: File to (over)write.
    """
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _move_aside(path: Path, now_ms: int) -> None:
    """Rename a damaged database (and its -wal/-shm files) out of the way, keeping it for inspection.

    Args:
        path: The damaged database.
        now_ms: Used in the new name, `<name>.corrupt-<ms>`.
    """
    for suffix in ("", "-wal", "-shm"):
        part = path.with_name(path.name + suffix)
        if part.exists():
            part.replace(path.with_name(f"{path.name}{suffix}.corrupt-{now_ms}"))


def restore_newest_backup(path: Path, backup_dir: Path, now_ms: int) -> Path | None:
    """Replace a damaged database with the newest healthy backup (SPEC §5.2, FM10).

    Args:
        path: The damaged database; it is moved aside first.
        backup_dir: data/backups.
        now_ms: For the moved-aside file's name.

    Returns:
        The backup restored, or None when there was none (path then doesn't exist,
        so a fresh database is created).
    """
    _move_aside(path, now_ms)
    for backup in backup_files(backup_dir):
        if is_healthy(backup):
            _copy_database(backup, path)
            return backup
    return None


class Database:
    """A connection to data/ahsync.db with AHSync's queries and upserts."""

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        """Wrap an open connection. Use Database.open instead.

        Args:
            conn: Connection in autocommit mode (isolation_level=None).
            path: The database file.
        """
        self.conn = conn
        self.path = path
        self._depth = 0

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def open(cls, path: Path, now_ms: int, backup_dir: Path | None = None) -> Database:
        """Open (or create) the database: check, restore if damaged, migrate, drop staging.

        Args:
            path: data/ahsync.db.
            now_ms: Current time, for status messages and moved-aside names.
            backup_dir: data/backups; None disables restore.

        Returns:
            The open database. A restore leaves a 'restore' status message.

        Raises:
            DatabaseError: The file is damaged and there's no backup dir, or it
                was written by a newer AHSync.
        """
        restored: Path | None = None
        damaged = path.exists() and not is_healthy(path)
        if damaged and backup_dir is None:
            raise DatabaseError(f"{path.name} is damaged and there is no backup folder")
        if damaged:
            restored = restore_newest_backup(path, backup_dir, now_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)
        db = cls(conn, path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            db.migrate()
            db.drop_staging()
        except BaseException:
            conn.close()
            raise
        if damaged:
            db.set_status("restore", "warn", "restored", {"backup": restored.name if restored else None}, now_ms)
        return db

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()

    def migrate(self) -> int:
        """Bring the schema to SCHEMA_VERSION (SPEC §3.2 "Format version").

        Returns:
            The schema version now in place.

        Raises:
            DatabaseError: The file has a newer version than this AHSync knows.
        """
        has_meta = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        version = int(self.get_meta("schema_version") or 0) if has_meta else 0
        if version > SCHEMA_VERSION:
            raise DatabaseError(f"database schema {version} is newer than this AHSync ({SCHEMA_VERSION}); update AHSync")
        if version == 0:
            self.conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            return SCHEMA_VERSION
        for step in range(version + 1, SCHEMA_VERSION + 1):
            with self.transaction():
                for statement in MIGRATIONS[step].split(";"):
                    if statement.strip():
                        self.conn.execute(statement)
                self.set_meta("schema_version", str(step))
        return SCHEMA_VERSION

    def drop_staging(self) -> None:
        """Empty the staging tables an interrupted full refresh may have left (SPEC §5.2, FM7)."""
        with self.transaction():
            for table in STAGING_TABLES:
                self.conn.execute(f"DELETE FROM {table}")

    def quick_check(self) -> bool:
        """Run `PRAGMA quick_check` on the open database.

        Returns:
            True when it reports "ok".
        """
        return self.conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes into one commit unit; nested calls join the outer one.

        Yields:
            Nothing. On any exception (including a simulated crash) the whole unit
            is rolled back and the exception re-raised.
        """
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        self._depth = 0
        self.conn.execute("COMMIT")

    def daily_backup(self, backup_dir: Path, now_ms: int) -> Path | None:
        """Make today's backup if there isn't one, keeping the newest 3 (SPEC §5.2).

        Args:
            backup_dir: data/backups (created if missing).
            now_ms: Current time; the UTC date names the file.

        Returns:
            The new backup, or None when today's already exists.
        """
        backup_dir.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime(now_ms // 1000))
        target = backup_dir / f"{BACKUP_PREFIX}{day}.db"
        if target.exists():
            return None
        partial = target.with_suffix(".db.tmp")
        partial.unlink(missing_ok=True)       # left by an interrupted backup
        dst = sqlite3.connect(partial)
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        partial.replace(target)
        for old in backup_files(backup_dir)[BACKUP_KEEP:]:
            old.unlink()
        return target

    def vacuum(self) -> None:
        """Reclaim free space (weekly, SPEC §3.1)."""
        self.conn.execute("VACUUM")

    # ------------------------------------------------------------ meta, status, sync state

    def get_meta(self, name: str) -> str | None:
        """Read a meta value.

        Args:
            name: Meta name, e.g. "schema_version".

        Returns:
            The value, or None when unset.
        """
        row = self.conn.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_meta(self, name: str, value: str | None) -> None:
        """Upsert a meta value.

        Args:
            name: Meta name.
            value: New value; None stores NULL.
        """
        with self.transaction():
            self.conn.execute("INSERT INTO meta(name, value) VALUES(?, ?) "
                              "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, value))

    def set_status(self, topic: str, level: str, kind: str, data: dict, at: int) -> None:
        """Set the Status line for a topic, replacing any earlier one.

        Args:
            topic: Stable id, e.g. "format:snapshot".
            level: 'info', 'warn' or 'error'.
            kind: Message kind, e.g. "lost".
            data: Structured fields (JSON-serializable, no secrets, no pre-formatted times).
            at: Time, ms.
        """
        payload = json.dumps(data, sort_keys=True)
        with self.transaction():
            self.conn.execute("INSERT INTO status(topic, level, kind, data, at) VALUES(?, ?, ?, ?, ?) "
                              "ON CONFLICT(topic) DO UPDATE SET level = excluded.level, kind = excluded.kind, "
                              "data = excluded.data, at = excluded.at", (topic, level, kind, payload, at))

    def clear_status(self, topic: str) -> None:
        """Remove a topic's Status line, if any.

        Args:
            topic: The topic to clear.
        """
        with self.transaction():
            self.conn.execute("DELETE FROM status WHERE topic = ?", (topic,))

    def statuses(self) -> list[StatusMessage]:
        """All Status lines.

        Returns:
            Messages sorted by topic.
        """
        rows = self.conn.execute("SELECT topic, level, kind, data, at FROM status ORDER BY topic").fetchall()
        return [StatusMessage(topic, level, kind, json.loads(data), at) for topic, level, kind, data, at in rows]

    def status(self, topic: str) -> StatusMessage | None:
        """One topic's Status line.

        Args:
            topic: The topic.

        Returns:
            The line, or None when there is none.
        """
        return next((s for s in self.statuses() if s.topic == topic), None)

    def get_sync_state(self, market: str) -> SyncState:
        """Read a market's sync state.

        Args:
            market: AHF market id.

        Returns:
            The state; all fields None for a market never synced.
        """
        row = self.conn.execute("SELECT market, last_hour, last_d1, last_volume, live_since, last_run "
                                "FROM sync_state WHERE market = ?", (market,)).fetchone()
        return SyncState(*row) if row else SyncState(market)

    def update_sync_state(self, market: str, **fields: int | None) -> None:
        """Upsert some sync-state fields, leaving the others as they are.

        Args:
            market: AHF market id.
            **fields: Any of last_hour, last_d1, last_volume, live_since, last_run.

        Raises:
            ValueError: An unknown field name.
        """
        allowed = {"last_hour", "last_d1", "last_volume", "live_since", "last_run"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"bad sync_state fields: {sorted(fields)}")
        names = sorted(fields)
        with self.transaction():
            self.conn.execute("INSERT INTO sync_state(market) VALUES(?) ON CONFLICT(market) DO NOTHING", (market,))
            self.conn.execute(f"UPDATE sync_state SET {', '.join(f'{n} = ?' for n in names)} WHERE market = ?",
                              [fields[n] for n in names] + [market])

    def record_fetch_failure(self, market: str, hour: int, error: str, at: int) -> int:
        """Count one more failed fetch of an hour's file (SPEC §5.2, FM2/FM3).

        Args:
            market: AHF market id.
            hour: The hour whose file failed.
            error: Short reason for the log and Status (no secrets).
            at: Time, ms.

        Returns:
            Failures in a row for that hour, including this one.
        """
        with self.transaction():
            self.conn.execute("INSERT INTO fetch_failures(market, hour, failures, last_error, last_at) "
                              "VALUES(?, ?, 1, ?, ?) ON CONFLICT(market, hour) DO UPDATE SET "
                              "failures = failures + 1, last_error = excluded.last_error, last_at = excluded.last_at",
                              (market, hour, error, at))
            row = self.conn.execute("SELECT failures FROM fetch_failures WHERE market = ? AND hour = ?",
                                    (market, hour)).fetchone()
        return row[0]

    def clear_fetch_failure(self, market: str, hour: int) -> None:
        """Forget an hour's failures once it is stored, or once it can no longer be fetched.

        Args:
            market: AHF market id.
            hour: The hour.
        """
        with self.transaction():
            self.conn.execute("DELETE FROM fetch_failures WHERE market = ? AND hour = ?", (market, hour))

    def fetch_failures(self, market: str) -> list[tuple[int, int]]:
        """Hours waiting to be re-fetched.

        Args:
            market: AHF market id.

        Returns:
            (hour, failures in a row), oldest hour first.
        """
        return self.conn.execute("SELECT hour, failures FROM fetch_failures WHERE market = ? ORDER BY hour",
                                 (market,)).fetchall()

    # ------------------------------------------------------------ prices

    def upsert_candles(self, table: str, market: str, rows: Iterable[tuple], src: str) -> int:
        """Upsert candles into candles_h1 or candles_d1.

        Args:
            table: "candles_h1" or "candles_d1".
            market: AHF market id.
            rows: (key, t ms, open, high, low, close, qty), prices in copper or None.
            src: 'ahf' or 'local'.

        Returns:
            Number of rows written.

        Raises:
            ValueError: Unknown table.
        """
        if table not in CANDLE_TABLES:
            raise ValueError(f"not a candle table: {table}")
        data = [(market, key, t, o, h, low, c, q, src) for key, t, o, h, low, c, q in rows]
        with self.transaction():
            self.conn.executemany(
                f"INSERT INTO {table}(market, key, t, open, high, low, close, qty, src) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(market, key, t, src) DO UPDATE SET "
                "open = excluded.open, high = excluded.high, low = excluded.low, "
                "close = excluded.close, qty = excluded.qty", data)
        return len(data)

    def replace_current(self, market: str, src: str, rows: Iterable[tuple[int, int | None, int, int]]) -> int:
        """Replace one source's whole current-price table for a market (a full snapshot).

        Args:
            market: AHF market id.
            src: 'ahf' or 'local'.
            rows: (key, price copper or None, qty, at ms).

        Returns:
            Number of rows written.
        """
        with self.transaction():
            self.conn.execute("DELETE FROM current WHERE market = ? AND src = ?", (market, src))
            return self.upsert_current(market, src, rows)

    def upsert_current(self, market: str, src: str, rows: Iterable[tuple[int, int | None, int, int]]) -> int:
        """Upsert some current prices (live changes, or one scan's items).

        Args:
            market: AHF market id.
            src: 'ahf' or 'local'.
            rows: (key, price copper or None, qty, at ms).

        Returns:
            Number of rows written.
        """
        data = [(market, key, price, qty, at, src) for key, price, qty, at in rows]
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO current(market, key, price, qty, at, src) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(market, key, src) DO UPDATE SET price = excluded.price, qty = excluded.qty, "
                "at = excluded.at", data)
        return len(data)

    def current_price(self, market: str, key: int) -> CurrentPrice | None:
        """`cur` for an item: the newest observation across sources, keeping its src (S7).

        Args:
            market: AHF market id.
            key: AHF item key.

        Returns:
            The newest row (ties go to 'local', the exact observation), or None when unseen.
        """
        row = self.conn.execute(
            "SELECT key, price, qty, at, src FROM current WHERE market = ? AND key = ? "
            "ORDER BY at DESC, CASE src WHEN 'local' THEN 0 ELSE 1 END, src LIMIT 1", (market, key)).fetchone()
        return CurrentPrice(*row) if row else None

    def hourly_closes(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """One close per item-hour for statistics; a local close beats AHF's (SPEC §2.2, S9).

        Args:
            market: AHF market id.
            key: AHF item key.
            from_ms: First hour start to include, ms.
            to_ms: Last hour start to include, ms.

        Returns:
            (hour start ms, close copper), ascending; hours with no listed close are left out.
        """
        rows = self.conn.execute(
            "SELECT t, close FROM candles_h1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? "
            "AND close IS NOT NULL ORDER BY t, CASE src WHEN 'local' THEN 0 ELSE 1 END, src",
            (market, key, from_ms, to_ms)).fetchall()
        closes: list[tuple[int, int]] = []
        for t, close in rows:
            if not closes or closes[-1][0] != t:
                closes.append((t, close))
        return closes

    def upsert_items(self, rows: Iterable[tuple], updated_ms: int) -> int:
        """Upsert item metadata from /items.

        Args:
            rows: (key, item_id, name, quality, ilvl, class, subclass, vendor_buy, vendor_sell, stack).
            updated_ms: Time of the refresh.

        Returns:
            Number of rows written.
        """
        data = [(*row, updated_ms) for row in rows]
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO items(key, item_id, name, quality, ilvl, class, subclass, vendor_buy, vendor_sell, "
                "stack, updated) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "item_id = excluded.item_id, name = excluded.name, quality = excluded.quality, "
                "ilvl = excluded.ilvl, class = excluded.class, subclass = excluded.subclass, "
                "vendor_buy = excluded.vendor_buy, vendor_sell = excluded.vendor_sell, "
                "stack = excluded.stack, updated = excluded.updated", data)
        return len(data)

    def store_book(self, scan: BookScan, entries: Iterable[tuple[int, int, int, int, int]]) -> int:
        """Store an order book: its scan row and entries, replacing any earlier copy of the same scan.

        Args:
            scan: The scans row.
            entries: (key, unit price copper, stack, band, count).

        Returns:
            Number of entries written.
        """
        data = [(scan.scan_id, *entry) for entry in entries]
        with self.transaction():
            self.conn.execute("INSERT INTO scans(scan_id, market, src, char_id, at, kind, coverage) "
                              "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(scan_id) DO UPDATE SET market = excluded.market, "
                              "src = excluded.src, char_id = excluded.char_id, at = excluded.at, kind = excluded.kind, "
                              "coverage = excluded.coverage",
                              (scan.scan_id, scan.market, scan.src, scan.char_id, scan.at, scan.kind, scan.coverage))
            self.conn.execute("DELETE FROM book_entries WHERE scan_id = ?", (scan.scan_id,))
            self.conn.executemany("INSERT INTO book_entries(scan_id, key, price, stack, band, count) "
                                  "VALUES(?, ?, ?, ?, ?, ?)", data)
        return len(data)

    def has_scan(self, scan_id: str) -> bool:
        """Whether an order book is already stored.

        Args:
            scan_id: The scan's id.

        Returns:
            True if a scans row exists.
        """
        return self.conn.execute("SELECT 1 FROM scans WHERE scan_id = ?", (scan_id,)).fetchone() is not None

    # ------------------------------------------------------------ pruning primitives (policy in prune.py)

    def delete_before(self, table: str, cutoff: int) -> int:
        """Delete rows whose time column is before a cutoff.

        Args:
            table: One of PRUNABLE; its time column is PRUNABLE[table].
            cutoff: Rows with column < cutoff go (same unit as the column: ms, day or hour).

        Returns:
            Rows deleted.

        Raises:
            ValueError: Table not prunable.
        """
        if table not in PRUNABLE:
            raise ValueError(f"not prunable: {table}")
        with self.transaction():
            return self.conn.execute(f"DELETE FROM {table} WHERE {PRUNABLE[table]} < ?", (cutoff,)).rowcount

    def delete_scans_before(self, cutoff_ms: int) -> int:
        """Delete order books (scans and their entries) taken before a cutoff.

        Args:
            cutoff_ms: Scans with at < cutoff_ms go.

        Returns:
            Scans deleted.
        """
        with self.transaction():
            self.conn.execute("DELETE FROM book_entries WHERE scan_id IN (SELECT scan_id FROM scans WHERE at < ?)",
                              (cutoff_ms,))
            return self.conn.execute("DELETE FROM scans WHERE at < ?", (cutoff_ms,)).rowcount

    def delete_rule_tombstones_before(self, cutoff_ms: int) -> int:
        """Delete cleared item rules ('none') set before a cutoff (SPEC §3.2).

        Args:
            cutoff_ms: Tombstones with set_at < cutoff_ms go; real rules are kept.

        Returns:
            Rows deleted.
        """
        with self.transaction():
            return self.conn.execute("DELETE FROM item_rules WHERE rule = 'none' AND set_at < ?",
                                     (cutoff_ms,)).rowcount

    def count(self, table: str, where: str = "", params: tuple = ()) -> int:
        """Count rows, for reports and tests.

        Args:
            table: Table name (trusted, from code).
            where: Optional SQL condition without "WHERE".
            params: Values for the condition's placeholders.

        Returns:
            Row count.
        """
        sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return self.conn.execute(sql, params).fetchone()[0]

    # ---- SavedVariables ingest (SPEC §2.2.1, §3.2). Each write reports whether it changed a row, so reading
    # the same file again changes nothing (FM12) and the caller knows which items went dirty (§3.1.1).
    # The merge rules make the result independent of the order files are read in (FI2).

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Make the writes inside one row's unit: on an exception only they are undone (SPEC §5.2 rule 6).

        Yields:
            Nothing. Use inside transaction(); the exception is re-raised.
        """
        self.conn.execute("SAVEPOINT ingest_row")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK TO ingest_row")
            self.conn.execute("RELEASE ingest_row")
            raise
        self.conn.execute("RELEASE ingest_row")

    def _upsert(self, table: str, row: dict[str, object], key: tuple[str, ...], merge: dict[str, str],
                when: str = "1") -> bool:
        """Insert a row, or merge it into the row with the same key.

        Args:
            table: Table name (trusted, from code).
            row: Column -> value.
            key: The conflict columns.
            merge: Column -> SQL expression for its merged value (may use `excluded.`); empty keeps the old row.
            when: SQL condition under which the merge is applied.

        Returns:
            True when a row was inserted or changed.
        """
        columns = list(row)
        action = f"UPDATE SET {', '.join(f'{c} = {e}' for c, e in merge.items())} WHERE {when}" if merge else "NOTHING"
        sql = (f"INSERT INTO {table}({', '.join(columns)}) VALUES({', '.join('?' * len(columns))}) "
               f"ON CONFLICT({', '.join(key)}) DO {action}")
        return self.conn.execute(sql, [row[c] for c in columns]).rowcount > 0

    def upsert_character(self, row: dict[str, object]) -> bool:
        """Store a character: the newest `updated` wins, level only rises, market follows the config.

        Args:
            row: characters columns (id, name, realm, faction, market, class, level, gold, updated).

        Returns:
            True when the row was inserted or changed.
        """
        newer = "excluded.updated > characters.updated"
        merge = {c: f"CASE WHEN {newer} THEN excluded.{c} ELSE {c} END"
                 for c in ("name", "realm", "faction", "class", "gold", "updated")}
        merge |= {"level": "MAX(level, excluded.level)", "market": "excluded.market"}
        when = f"{newer} OR excluded.level > characters.level OR excluded.market IS NOT characters.market"
        return self._upsert("characters", row, ("id",), merge, when)

    def upsert_profession(self, char_id: str, profession: str, skill: int, max_skill: int) -> bool:
        """Store a profession's skill; skill and maximum only rise, so file order doesn't matter (FI2).

        Args:
            char_id: "Name-Realm".
            profession: enUS profession name.
            skill: Current skill.
            max_skill: Current maximum.

        Returns:
            True when the row was inserted or changed.
        """
        row = {"char_id": char_id, "profession": profession, "skill": skill, "max_skill": max_skill}
        merge = {"skill": "MAX(skill, excluded.skill)", "max_skill": "MAX(max_skill, excluded.max_skill)"}
        return self._upsert("professions", row, ("char_id", "profession"), merge,
                            "excluded.skill > professions.skill OR excluded.max_skill > professions.max_skill")

    def upsert_recipe(self, row: dict[str, object], reagents: dict[int, int]) -> bool:
        """Store a recipe definition and its reagents; a known skill or cooldown is never replaced by unknown.

        Args:
            row: recipes columns (profession, spell_id, product_key, yield_min, yield_max, skill_req, cooldown_s);
                skill_req and cooldown_s are None when unknown.
            reagents: Item key -> count, replacing the recipe's stored reagents.

        Returns:
            True when the recipe or its reagents were inserted or changed.
        """
        merge = {c: f"excluded.{c}" for c in ("product_key", "yield_min", "yield_max")}
        merge |= {c: f"COALESCE(excluded.{c}, {c})" for c in ("skill_req", "cooldown_s")}
        when = " OR ".join(f"{e} IS NOT recipes.{c}" for c, e in merge.items())
        changed = self._upsert("recipes", row, ("profession", "spell_id"), merge, when)
        recipe = {"profession": row["profession"], "spell_id": row["spell_id"]}
        return bool(self._replace_rows("reagents", recipe, ("key", "count"), reagents)) or changed

    def add_known_recipe(self, char_id: str, spell_id: int) -> bool:
        """Record that a character knows a recipe; add-only, like the addon.

        Args:
            char_id: "Name-Realm".
            spell_id: The recipe's spell id.

        Returns:
            True when it was new.
        """
        return self._upsert("known_recipes", {"char_id": char_id, "spell_id": spell_id}, ("char_id", "spell_id"), {})

    def add_vendor_reagent(self, key: int) -> bool:
        """Record an item a merchant was seen selling; add-only.

        Args:
            key: AHF item key.

        Returns:
            True when it was new.
        """
        return self._upsert("vendor_reagents", {"key": key}, ("key",), {})

    def _replace_rows(self, table: str, scope: dict[str, object], columns: tuple[str, ...],
                      rows: dict[tuple, int], extra: dict[str, object] | None = None) -> set[int]:
        """Replace every row of one scope (a character's place, a recipe) with new rows.

        Args:
            table: Table name (trusted, from code).
            scope: Column -> value that selects the rows replaced.
            columns: The row columns; the first is the item key and the last the quantity.
            rows: Row key (every column but the last) -> quantity.
            extra: Column -> value written on every new row but not compared (e.g. `seen`).

        Returns:
            Item keys whose rows were added, removed or changed.
        """
        where, params = " AND ".join(f"{c} = ?" for c in scope), list(scope.values())
        old = {tuple(r[:-1]): r[-1]
               for r in self.conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", params)}
        if old == rows:
            return set()
        extra = extra or {}
        names = [*scope, *extra, *columns]
        self.conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        self.conn.executemany(f"INSERT INTO {table}({', '.join(names)}) VALUES({', '.join('?' * len(names))})",
                              [(*params, *extra.values(), *k, n) for k, n in sorted(rows.items())])
        return {k[0] for k, _ in old.items() ^ rows.items()}

    def replace_snapshot(self, char_id: str, location: str, at_ms: int, rows: dict[tuple, int]) -> set[int] | None:
        """Replace one place's snapshot, unless the stored one is as new or newer (SPEC §2.2.1).

        Args:
            char_id: "Name-Realm".
            location: 'bags', 'bank' or 'mail' (table inventory; row key (key, bound 0/1)) or 'auctions'
                (table own_auctions; row key (key, price, stack, band)).
            at_ms: When the snapshot was captured.
            rows: Row key -> units (inventory qty, or auction count).

        Returns:
            Item keys whose rows changed (possibly none), or None when the snapshot was older and not applied.
        """
        if not self._upsert("snapshots", {"char_id": char_id, "location": location, "at": at_ms},
                            ("char_id", "location"), {"at": "excluded.at"}, "excluded.at > snapshots.at"):
            return None
        if location == "auctions":
            return self._replace_rows("own_auctions", {"char_id": char_id},
                                      ("key", "price", "stack", "band", "count"), rows, {"seen": at_ms})
        return self._replace_rows("inventory", {"char_id": char_id, "location": location},
                                  ("key", "bound", "qty"), rows)

    def upsert_event(self, table: str, row: dict[str, object]) -> bool:
        """Store a captured event by its id (SPEC §5.2 rule 2).

        Posts and purchases carry `confirmed`: an attempt is stored with 0, and a later copy with 1 upgrades it
        with that copy's fields; a confirmed row is never downgraded. Other events never change once stored.

        Args:
            table: 'posts', 'purchases', 'crafts', 'auction_mail' or 'mail_sent'.
            row: The table's columns, including event_id (and post_id for posts).

        Returns:
            True when the row was inserted or changed.

        Raises:
            ValueError: Not an event table.
        """
        if table not in ("posts", "purchases", "crafts", "auction_mail", "mail_sent"):
            raise ValueError(f"not an event table: {table}")
        key = ("post_id",) if table == "posts" else ("event_id",)
        merge = {c: f"excluded.{c}" for c in row if c not in key} if "confirmed" in row else {}
        return self._upsert(table, row, key, merge, f"excluded.confirmed > {table}.confirmed")

    def record_skip(self, event_id: str, char_id: str, action_id: str, at_ms: int) -> int | None:
        """Keep a skip event, and mark its action skipped if it exists and is still open (SPEC §3.4.1).

        Args:
            event_id: The skip event's id.
            char_id: "Name-Realm".
            action_id: The action skipped, from Data.lua.
            at_ms: When it was skipped.

        Returns:
            The action's item key when an action changed to skipped now, else None.
        """
        self._upsert("skips", {"event_id": event_id, "char_id": char_id, "action_id": action_id, "at": at_ms},
                     ("event_id",), {})
        row = self.conn.execute("SELECT key FROM actions WHERE action_id = ? AND status IN "
                                "('pending', 'late', 'partial', 'missed')", (action_id,)).fetchone()
        if row is None:
            return None
        self.conn.execute("UPDATE actions SET status = 'skipped' WHERE action_id = ?", (action_id,))
        return row[0]

    def upsert_item_rule(self, key: int, rule: str, set_at_ms: int, source: str) -> bool:
        """Store an item rule if it is newer than the stored one; 'none' is a clear (SPEC §3.4.2, O10, O18).

        Args:
            key: AHF item key.
            rule: 'no_buy', 'ignore', 'keep' or 'none'.
            set_at_ms: When it was set.
            source: 'addon' or 'dashboard'.

        Returns:
            True when the row was inserted or changed. Equal times are settled by the rule name, so the
            result doesn't depend on which side was read first.
        """
        row = {"key": key, "rule": rule, "set_at": set_at_ms, "source": source}
        return self._upsert("item_rules", row, ("key",), {c: f"excluded.{c}" for c in ("rule", "set_at", "source")},
                            "excluded.set_at > item_rules.set_at OR "
                            "(excluded.set_at = item_rules.set_at AND excluded.rule > item_rules.rule)")

    def character_market(self, char_id: str) -> str | None:
        """A stored character's market.

        Args:
            char_id: "Name-Realm".

        Returns:
            The AHF market id, or None when the character or its market is unknown.
        """
        row = self.conn.execute("SELECT market FROM characters WHERE id = ?", (char_id,)).fetchone()
        return row[0] if row else None

    def recipe_item_keys(self, spell_id: int) -> set[int]:
        """Every item a recipe makes or uses, in any profession.

        Args:
            spell_id: The recipe's spell id.

        Returns:
            Product and reagent keys (empty when the recipe is unknown).
        """
        rows = self.conn.execute("SELECT product_key FROM recipes WHERE spell_id = ? AND product_key IS NOT NULL "
                                 "UNION SELECT key FROM reagents WHERE spell_id = ?", (spell_id, spell_id))
        return {r[0] for r in rows}
