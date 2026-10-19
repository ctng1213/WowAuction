"""Store a parsed AHProfit SavedVariables file in the database (SPEC §2.2, §3.1, §5.2).

* ``apply_data`` writes one parsed file (``savedvars.AHProfitData``) in one
  transaction. Every write is an idempotent upsert on a natural key, and the
  merge rules (newest snapshot, newest rule, confirmed beats attempt, the newest
  character's professions and known recipes) make the result independent of the order files are
  read in (FM12, FI2). It returns the item keys that changed, for the
  incremental update (SPEC §3.1.1).
* ``read_and_apply`` reads one file from disk: only once it has been unchanged
  for 2 s, falling back to WoW's ``AHProfit.lua.bak`` when it won't parse (FM11),
  refusing a newer format (FM13), and writing each character's ingestedUntil
  (``meta.savedvars_ingested_until:<charId>``) only after the commit (§2.2, §5.2 rule 5).

Not here: cost basis, matching events to actions (engine/tracking), untracked
changes (FM21), and the incremental update itself (``incremental_update`` is
its hook).
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ahsync.clock import Clock
from ahsync.state import savedvars as sv
from ahsync.collect.local_scans import derive_from_scans
from ahsync.db.sqlite import BookScan, Database
from ahsync.app.scheduler import STABLE_MS, file_mtime_ms

log = logging.getLogger("ahsync.state")

INGESTED_UNTIL_PREFIX = "savedvars_ingested_until:"   # + charId; meta values exported as ingestedUntil (M5)
SET_ASIDE_MARGIN_MS = 1000                     # ingestedUntil stops 1 s before a set-aside row (SPEC §2.2)
BACKUP_SUFFIX = ".bak"                         # WoW keeps the previous file as AHProfit.lua.bak
MAX_STATUS_PROBLEMS = 10                       # row problems listed on Status; the log has them all
FACTIONS = ("Horde", "Alliance")


@dataclass(frozen=True)
class ApplyResult:
    """What one file changed."""

    dirty_keys: frozenset[int]        # item keys whose data changed (unknown-suffix keys left out)
    problems: tuple[str, ...]         # rows set aside, from parsing and from storing
    ingested_until: dict[str, int]    # characters whose ingestedUntil this file moved -> new value, ms


@dataclass(frozen=True)
class IngestResult:
    """The outcome of reading one SavedVariables file."""

    path: Path
    ingested: bool
    reason: str | None = None         # why not: 'missing', 'unstable', 'newer_format', 'unparsable'
    read_from: Path | None = None     # the file actually read: path, or its .bak (FM11)
    mtime_ms: int | None = None       # path's modification time when read, UTC ms
    dirty_keys: frozenset[int] = frozenset()
    problems: tuple[str, ...] = ()
    ingested_until: dict[str, int] | None = None      # every character's ingestedUntil after this read, ms
    refresh_requested_at_ms: int | None = None        # /ahp refresh request in the file (SPEC §3.1.1)


# ---------------------------------------------------------------- markets

def market_for(faction: str, realm: str, realms: Mapping[str, str], markets: Sequence[str] = ()) -> str | None:
    """A character's AHF market from its faction and realm (SPEC §2.1, §2.2.1, S12).

    WoW reports a realm's name but not its ruleset, so the ruleset comes from the config:
    the [realms] entry when there is one, else the only market you sync for that faction.

    Args:
        faction: "Horde" or "Alliance".
        realm: Realm name, e.g. "Grommash".
        realms: Config [realms]: realm name -> ruleset ('normal', 'pvp' or 'rp').
        markets: Config markets, e.g. ("forever.pvp.horde.us",).

    Returns:
        "forever.<ruleset>.<faction lower>.us"; None when the faction isn't one of the two,
        or the realm isn't in [realms] and you sync no market, or several, for the faction.
    """
    if faction not in FACTIONS:
        return None
    ruleset = realms.get(realm)
    if ruleset is not None:
        return f"forever.{ruleset}.{faction.lower()}.us"
    mine = [m for m in markets if m.startswith("forever.") and m.endswith(f".{faction.lower()}.us")]
    return mine[0] if len(mine) == 1 else None


# ---------------------------------------------------------------- pruning handshake

class _Pruning:
    """Per character: the newest scan or event time stored, and the oldest row set aside (SPEC §2.2)."""

    def __init__(self) -> None:
        """Start with nothing seen."""
        self.newest: dict[str, int] = {}
        # charId (None = unreadable) -> oldest set-aside time, ms (None = unreadable time)
        self.caps: dict[str | None, int | None] = {}

    def stored(self, char_id: str, at_ms: int) -> None:
        """Note a stored (or already stored) scan or event.

        Args:
            char_id: Its character.
            at_ms: Its time.
        """
        self.newest[char_id] = max(at_ms, self.newest.get(char_id, at_ms))

    def set_aside(self, char_id: str | None, at_ms: int | None) -> None:
        """Note a scan or event that was not stored.

        Args:
            char_id: Its character; None when unreadable, which holds back every character in the file.
            at_ms: Its time; None when unreadable, which holds that character where it is.
        """
        if char_id in self.caps and (self.caps[char_id] is None or at_ms is None):
            self.caps[char_id] = None
        else:
            self.caps[char_id] = at_ms if char_id not in self.caps else min(at_ms, self.caps[char_id])

    def candidates(self) -> dict[str, int]:
        """The ingestedUntil each character may move to, before the never-backwards rule.

        Returns:
            charId -> the newest stored time, capped 1 s before that character's oldest set-aside row;
            characters held back by a row of unknown time are left out.
        """
        result = {}
        for char_id, newest in sorted(self.newest.items()):
            caps = [self.caps[k] for k in (char_id, None) if k in self.caps]
            if None in caps:
                continue
            result[char_id] = min([newest] + [cap - SET_ASIDE_MARGIN_MS for cap in caps])
        return result


# ---------------------------------------------------------------- sections

class _Apply:
    """Writes one file's records inside the caller's transaction, collecting dirty keys and problems."""

    def __init__(self, db: Database, realms: Mapping[str, str], now_ms: int, markets: Sequence[str] = ()) -> None:
        """Start an apply.

        Args:
            db: The database, inside a transaction.
            realms: Config [realms].
            now_ms: Current time, for status rows.
            markets: Config markets, for realms not in [realms] (S12).
        """
        self.db, self.realms, self.now_ms, self.markets = db, realms, now_ms, markets
        self.dirty: set[int] = set()
        self.problems: list[str] = []
        self.pruning = _Pruning()
        self.follows: set[str] = set()     # characters whose professions and known recipes follow this file
        self.new_scans: list[tuple[str, str, int, bool]] = []   # (market, scan id, at, complete) stored now

    def guarded(self, what: str, write: Callable[[], None]) -> bool:
        """Run one row's writes; a failure undoes only that row and is recorded (SPEC §5.2 rule 6).

        Args:
            what: Row description for the problem message.
            write: The row's writes.

        Returns:
            True when the row was stored.
        """
        try:
            with self.db.savepoint():
                write()
            return True
        except (sqlite3.IntegrityError, ValueError) as exc:
            self.problems.append(f"{what}: {exc}")
            return False

    def mark(self, keys: Iterable[int | None]) -> None:
        """Add item keys to the dirty set, leaving out unknown-suffix keys (SPEC §2.2.1) and None.

        Args:
            keys: Item keys.
        """
        self.dirty.update(k for k in keys if k is not None and not sv.is_unknown_suffix(k))

    def characters(self, characters: Iterable[sv.Character]) -> None:
        """Store characters with their market; an unknown realm gets a Status line (SPEC §3.2).

        Args:
            characters: From the file.
        """
        for c in characters:
            stored_updated = self.db.character_updated(c.char_id)
            if stored_updated is None or c.updated_ms >= stored_updated:     # SPEC §2.2.1 "follow the file"
                self.follows.add(c.char_id)
            market = market_for(c.faction, c.realm, self.realms, self.markets)
            row = {"id": c.char_id, "name": c.name, "realm": c.realm, "faction": c.faction, "market": market,
                   "class": c.class_token, "level": c.level, "gold": c.gold_copper, "updated": c.updated_ms}
            self.guarded(f"characters[{c.char_id!r}]", lambda row=row: self.db.upsert_character(row))
            if market is None:
                self.db.set_status(f"realm:{c.realm}", "warn", "unknown_realm",
                                   {"realm": c.realm, "faction": c.faction}, self.now_ms)
            else:
                self.db.clear_status(f"realm:{c.realm}")

    def recipes(self, data: sv.AHProfitData) -> None:
        """Store recipe definitions, professions, known recipes and vendor reagents.

        A character's professions and known recipes are replaced with the file's when the
        file's character is at least as new as the stored one (SPEC §2.2.1). Only characters
        with entries in that section are replaced: an empty entry reads the same as a missing one.

        Args:
            data: The parsed file.
        """
        for r in data.recipes:
            self.guarded(f"recipes[{r.profession!r}][{r.spell_id}]", lambda r=r: self._recipe(r))
        skills: dict[str, dict[str, tuple[int, int]]] = {}
        for p in data.professions:
            skills.setdefault(p.char_id, {})[p.profession] = (p.skill, p.max_skill)
        known: dict[str, set[int]] = {}
        for k in data.known_recipes:
            known.setdefault(k.char_id, set()).add(k.spell_id)
        for char_id in sorted(self.follows & skills.keys()):
            self.guarded(f"professions[{char_id!r}]", lambda c=char_id: self.db.replace_professions(c, skills[c]))
        for char_id in sorted(self.follows & known.keys()):
            self.guarded(f"knownRecipes[{char_id!r}]", lambda c=char_id: self._known(c, known[c]))
        for key in data.vendor_reagents:
            if self.db.add_vendor_reagent(key):
                self.mark([key])

    def _recipe(self, r: sv.Recipe) -> None:
        """Store one recipe; a change dirties its product and reagents.

        Args:
            r: The recipe.
        """
        reagents: dict[tuple, int] = {}
        for reagent in r.reagents:
            reagents[(reagent.key,)] = reagents.get((reagent.key,), 0) + reagent.count
        row = {"profession": r.profession, "spell_id": r.spell_id, "product_key": r.product_key,
               "yield_min": r.yield_min, "yield_max": r.yield_max, "skill_req": r.skill_req,
               "cooldown_s": r.cooldown_s}
        if self.db.upsert_recipe(row, reagents):
            self.mark([r.product_key, *(k for (k,) in reagents)])

    def _known(self, char_id: str, spell_ids: set[int]) -> None:
        """Replace a character's known recipes; each recipe gained or lost dirties its items.

        Args:
            char_id: "Name-Realm".
            spell_ids: The file's known recipes for that character.
        """
        for spell_id in sorted(self.db.replace_known_recipes(char_id, spell_ids)):
            self.mark(self.db.recipe_item_keys(spell_id))

    def snapshots(self, data: sv.AHProfitData) -> None:
        """Replace inventory places and own auctions with newer snapshots (SPEC §2.2.1).

        Args:
            data: The parsed file.
        """
        for place in data.inventories:
            rows: dict[tuple, int] = {}
            for r in place.rows:                       # the addon sums per key and bound; sum again to be safe
                rows[(r.key, int(r.bound))] = rows.get((r.key, int(r.bound)), 0) + r.qty
            self.guarded(f"inventory[{place.char_id!r}].{place.location}",
                         lambda p=place, rows=rows: self.mark(
                             self.db.replace_snapshot(p.char_id, p.location, p.at_ms, rows) or ()))
        for snap in data.own_auctions:
            groups = _group_counts(snap.rows)
            self.guarded(f"ownAuctions[{snap.char_id!r}]",
                         lambda s=snap, g=groups: self.mark(
                             self.db.replace_snapshot(s.char_id, "auctions", s.at_ms, g) or ()))

    def scans(self, scans: Iterable[sv.Scan]) -> None:
        """Store own scans as local order books; a scan never changes once stored.

        Args:
            scans: From the file, oldest first.
        """
        for scan in scans:
            if self.guarded(f"scans[{scan.scan_id!r}]", lambda s=scan: self._scan(s)):
                self.pruning.stored(scan.char_id, scan.at_ms)
            else:
                self.pruning.set_aside(scan.char_id, scan.at_ms)

    def _scan(self, scan: sv.Scan) -> None:
        """Store one scan in the scanning character's market.

        Args:
            scan: The scan.

        Raises:
            ValueError: When the character's market is unknown (scans.market can't be empty).
        """
        if self.db.has_scan(scan.scan_id):
            return
        market = self.db.character_market(scan.char_id)
        if market is None:
            # SPEC GAP: a scan by a character whose market is unknown can't be stored (scans.market NOT NULL).
            raise ValueError(f"market of {scan.char_id} is unknown (realm not in config [realms])")
        groups = _group_counts(scan.entries)
        self.db.store_book(BookScan(scan.scan_id, market, "local", scan.char_id, scan.at_ms, scan.kind,
                                    scan.coverage), [(*k, n) for k, n in sorted(groups.items())])
        self.new_scans.append((market, scan.scan_id, scan.at_ms, scan.kind == "full" and scan.coverage >= 1))
        self.mark(k[0] for k in groups)

    def events(self, events: Iterable[sv.Event]) -> None:
        """Store events by id, noting each one for the pruning handshake (SPEC §2.2).

        Args:
            events: From the file, oldest first.
        """
        for e in events:
            if self.guarded(f"events[{e.event_id!r}]", lambda e=e: self._event(e)):
                self.pruning.stored(e.char_id, e.at_ms)
            else:
                self.pruning.set_aside(e.char_id, e.at_ms)

    def _event(self, e: sv.Event) -> None:
        """Store one event in its table; a change dirties its items.

        Args:
            e: The event.
        """
        if e.type == "skip":
            self.mark([self.db.record_skip(e.event_id, e.char_id, e.action_id, e.at_ms)])
            return
        table, row = _event_row(e, self.db.character_market(e.char_id) if e.type == "post" else None)
        if self.db.upsert_event(table, row):
            self.mark(self.db.recipe_item_keys(e.spell_id) if e.type == "craft" else [e.key])

    def rules(self, rules: Iterable[sv.ItemRule]) -> None:
        """Store item rules set in game: the newer `set_at` wins, 'none' included (SPEC §3.4.2).

        Args:
            rules: From the file.
        """
        for r in rules:
            if self.db.upsert_item_rule(r.key, r.rule, r.set_at_ms, "addon"):
                self.mark([r.key])


    def reserves(self, data: sv.AHProfitData) -> None:
        """Replace each followed character's reserves with the file's (SPEC §3.4.3, S14).

        A character missing from the file's reserves table keeps nothing; one whose entry was
        set aside keeps what is stored. A file without the table (an older addon) changes nothing.

        Args:
            data: The parsed file.
        """
        if data.reserves is None:
            return
        by_char = {r.char_id: dict(r.items) for r in data.reserves}
        for char_id in sorted(self.follows - set(data.reserves_set_aside)):
            items = by_char.get(char_id, {})
            self.guarded(f"reserves[{char_id!r}]", lambda c=char_id, i=items: self.mark(self.db.replace_reserves(c, i)))


def _group_counts(rows: Iterable[sv.AuctionRow]) -> dict[tuple, int]:
    """Sum auction or book rows that share key, price, stack and band.

    Args:
        rows: "key,price,stack,band,count" rows.

    Returns:
        (key, price, stack, band) -> count.
    """
    groups: dict[tuple, int] = {}
    for r in rows:
        group = (r.key, r.price_copper, r.stack, r.band)
        groups[group] = groups.get(group, 0) + r.count
    return groups


def _event_row(e: sv.Event, market: str | None) -> tuple[str, dict[str, object]]:
    """Map an event to its table and columns (SPEC §2.2.1 event table, §3.2).

    Args:
        e: A post, purchase, craft, disenchant, mail_sent, auction_mail or payout event.
        market: The character's market, for posts; None when unknown.

    Returns:
        (table, column -> value).
    """
    base = {"event_id": e.event_id, "char_id": e.char_id, "at": e.at_ms}
    if e.type == "post":
        return "posts", base | {"post_id": e.event_id, "market": market, "key": e.key, "stack": e.stack,
                                "price": e.price_copper, "duration_h": e.duration_h,
                                "deposit": e.deposit_copper, "confirmed": int(e.confirmed)}
    if e.type == "purchase":
        return "purchases", base | {"key": e.key, "qty": e.qty, "price": e.price_copper, "source": e.source,
                                    "confirmed": int(e.confirmed)}
    if e.type == "craft":
        return "crafts", base | {"spell_id": e.spell_id, "count": e.count}
    if e.type == "mail_sent":
        return "mail_sent", base | {"to_char": e.to_char_id, "key": e.key, "qty": e.qty}
    if e.type == "disenchant":
        return "disenchants", base | {"key": e.key, "results": ";".join(f"{k},{n}" for k, n in e.results)}
    if e.type == "payout":
        return "payouts", base | {"key": e.key, "qty": e.qty, "money": e.money_copper}
    return "auction_mail", base | {"key": e.key, "qty": e.qty, "price": e.price_copper, "outcome": e.outcome}


# ---------------------------------------------------------------- entry points

def apply_data(db: Database, data: sv.AHProfitData, realms: Mapping[str, str], now_ms: int,
               markets: Sequence[str] = ()) -> ApplyResult:
    """Store one parsed SavedVariables file in one transaction, then advance ingested-until.

    Args:
        db: The database (not inside a transaction).
        data: The parsed file.
        realms: Config [realms]: realm name -> ruleset.
        now_ms: Current time, UTC ms, for status rows.
        markets: Config markets, for characters on realms not in [realms] (S12).

    Returns:
        The dirty item keys, the problems (the parser's and any row that failed to
        store) and the characters whose ingestedUntil moved.

    Side effects:
        Each character's ingestedUntil moves to the newest scan or event time stored for
        it, but stops 1 s before its oldest row set aside (by the parser or on storing),
        only after the commit, and never backwards (SPEC §2.2, §5.2 rule 5).
    """
    work = _Apply(db, realms, now_ms, markets)
    for row in data.rejected:
        work.pruning.set_aside(row.char_id, row.at_ms)
    with db.transaction():
        work.characters(data.characters)       # first: scans and posts look up the market
        work.recipes(data)
        work.snapshots(data)
        work.scans(data.scans)
        work.mark(derive_from_scans(db, work.new_scans))     # local prices and book volume (SPEC §4.2)
        work.events(data.events)
        work.rules(data.rules)
        work.reserves(data)
        db.add_item_names((n.key, sv.base_item_id(n.key), n.name, n.quality) for n in data.item_names)
    advanced = _advance_ingested_until(db, work.pruning.candidates())
    return ApplyResult(frozenset(work.dirty), data.problems + tuple(work.problems), advanced)


def ingested_until(db: Database) -> dict[str, int]:
    """Each character's ingestedUntil for the pruning handshake (SPEC §2.2); exported in PortfolioPlan.lua.

    Args:
        db: The database.

    Returns:
        charId -> UTC ms: the addon may delete that character's scans and events at or before it.
        Characters with nothing committed yet are absent.
    """
    return {char_id: int(value) for char_id, value in db.meta_with_prefix(INGESTED_UNTIL_PREFIX).items()}


def _advance_ingested_until(db: Database, candidates: Mapping[str, int]) -> dict[str, int]:
    """Move characters' ingestedUntil forward, never back, in one commit.

    Args:
        db: The database (after the file's commit).
        candidates: charId -> the value this file allows.

    Returns:
        charId -> new value, for the characters that moved.
    """
    current = ingested_until(db)
    moved = {c: ms for c, ms in candidates.items() if c not in current or ms > current[c]}
    with db.transaction():
        for char_id, at_ms in sorted(moved.items()):
            db.set_meta(INGESTED_UNTIL_PREFIX + char_id, str(at_ms))
    return moved


def _status_topic(path: Path, what: str) -> str:
    """Status topic for one account's SavedVariables.

    Args:
        path: WTF/Account/<account>/SavedVariables/AHProfit.lua.
        what: 'savedvars' (read state) or 'savedvars_rows' (rows set aside).

    Returns:
        "<what>:<account>".
    """
    return f"{what}:{path.parent.parent.name}"


def _read(path: Path) -> tuple[sv.AHProfitData | None, Path | None, str | None]:
    """Parse a file, falling back to WoW's .bak when it won't parse (FM11); a newer format is refused (FM13).

    Args:
        path: AHProfit.lua.

    Returns:
        (data, file read, None) on success, else (None, None, 'newer_format' or 'unparsable').
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    for candidate in (path, backup):
        try:
            return sv.read_ahprofit_file(candidate), candidate, None
        except sv.UnsupportedFormatError as exc:
            log.warning("%s: %s", candidate.name, exc)
            return None, None, "newer_format"
        except (sv.SavedVarsError, OSError) as exc:
            log.warning("%s can't be read: %s", candidate.name, exc)
    return None, None, "unparsable"


def _report(db: Database, path: Path, result: IngestResult, now_ms: int) -> None:
    """Set or clear the Status lines for one read (SPEC §3.2 status rules).

    Args:
        db: The database.
        path: The SavedVariables file.
        result: The read's outcome.
        now_ms: Current time, UTC ms.
    """
    topic = _status_topic(path, "savedvars")
    account = path.parent.parent.name
    if result.reason == "newer_format":
        db.set_status(topic, "error", "update_ahsync", {"account": account}, now_ms)
    elif result.reason == "unparsable":
        db.set_status(topic, "error", "savedvars_unreadable", {"account": account}, now_ms)
    elif result.ingested and result.read_from != path:
        db.set_status(topic, "warn", "savedvars_backup_used", {"account": account}, now_ms)
    elif result.ingested:
        db.clear_status(topic)
    if not result.ingested:
        return
    rows_topic = _status_topic(path, "savedvars_rows")
    if result.problems:
        for problem in result.problems:
            log.warning("%s: row set aside: %s", path.name, problem)
        db.set_status(rows_topic, "warn", "savedvars_rows_skipped",
                      {"account": account, "count": len(result.problems),
                       "rows": list(result.problems[:MAX_STATUS_PROBLEMS])}, now_ms)
    else:
        db.clear_status(rows_topic)


def read_and_apply(path: Path, db: Database, clock: Clock, realms: Mapping[str, str],
                   stat_mtime_ms: Callable[[Path], int | None] = file_mtime_ms,
                   markets: Sequence[str] = ()) -> IngestResult:
    """Read one AHProfit SavedVariables file and store it (SPEC §2.2 reading rules, §5.2).

    Args:
        path: WTF/Account/<account>/SavedVariables/AHProfit.lua.
        db: The database.
        clock: Time source.
        realms: Config [realms]: realm name -> ruleset.
        stat_mtime_ms: Returns a file's modification time in UTC ms, or None when missing.
        markets: Config markets, for characters on realms not in [realms] (S12).

    Returns:
        The outcome. A file changed less than 2 s ago, or changed while being read,
        is not ingested ('unstable'); the caller reads it again later.

    Side effects:
        Writes the file's records, the characters' ingestedUntil and Status lines.
    """
    now = clock.now_ms()
    mtime = stat_mtime_ms(path)
    if mtime is None:
        return IngestResult(path, False, "missing")
    if now - mtime < STABLE_MS:
        return IngestResult(path, False, "unstable", mtime_ms=mtime)
    data, read_from, reason = _read(path)
    if stat_mtime_ms(path) != mtime:
        return IngestResult(path, False, "unstable", mtime_ms=mtime)
    if data is None:
        result = IngestResult(path, False, reason, mtime_ms=mtime, ingested_until=ingested_until(db))
    else:
        applied = apply_data(db, data, realms, now, markets)
        result = IngestResult(path, True, None, read_from, mtime, applied.dirty_keys, applied.problems,
                              ingested_until(db), data.refresh_requested_at_ms)
    _report(db, path, result, now)
    return result


def incremental_update(db: Database, dirty_keys: frozenset[int], now_ms: int) -> None:
    """Hook for work on just the items a SavedVariables read changed (SPEC §3.1.1).

    A no-op: the incremental update is app.refresh.replan_all, run by the app after the tracking pass,
    which re-plans from the published intelligence (IU13). The dirty keys are kept for a narrower one.

    Args:
        db: The database.
        dirty_keys: Item keys whose inventory, events, rules, recipes or scans changed.
        now_ms: Current time, UTC ms.
    """


def ingest_file(path: Path, db: Database, clock: Clock, realms: Mapping[str, str],
                stat_mtime_ms: Callable[[Path], int | None] = file_mtime_ms,
                markets: Sequence[str] = ()) -> IngestResult:
    """Read one SavedVariables file, store it, and run the incremental update on what changed.

    Args:
        path: AHProfit.lua of one account.
        db: The database.
        clock: Time source.
        realms: Config [realms].
        stat_mtime_ms: As read_and_apply.
        markets: Config markets, for characters on realms not in [realms] (S12).

    Returns:
        The outcome, as read_and_apply.
    """
    result = read_and_apply(path, db, clock, realms, stat_mtime_ms, markets)
    if result.dirty_keys:
        incremental_update(db, result.dirty_keys, clock.now_ms())
    return result
