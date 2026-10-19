"""The Status page, built when it is asked for (SPEC §3.5 Status).

Unlike the other pages it is not staged by the full refresh: sync runs, SavedVariables
reads and failures change it between refreshes. It reads only small tables (status
lines, sync state, current prices, scans, characters, resolved posts) and the database file's size,
never history or the intelligence; engine.status_view makes every text.
"""
from __future__ import annotations

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.sell_record import calibration
from ahsync.engine.volume import backtest
from ahsync.engine.status_view import CharacterFacts, MarketFacts, StatusFacts, StatusRow, status_view

DAY_MS = 86_400_000
CALIBRATION_DAYS = 90            # SPEC §8.8: the last 90 days of resolved posts
BACKTEST_DAYS = 30               # SPEC §8.8 volume backtest: the last 30 days


def _db_bytes(db: Database) -> int:
    """The database's size on disk, its write-ahead log included.

    Args:
        db: Database.

    Returns:
        Bytes; 0 for a file that can't be read.
    """
    total = 0
    for path in (db.path, db.path.with_name(db.path.name + "-wal")):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _backtests(db: Database, market: str, now_ms: int) -> tuple:
    """Estimated sales against each confirmed source over the last 30 days (SPEC §8.8, K4).

    Args:
        db: Database.
        market: Market id.
        now_ms: Now.

    Returns:
        (source, Backtest) per confirmed source with a row in the window, by source.
    """
    ratios: dict[str, list[float]] = {}
    for src, estimated, confirmed in db.conn.execute(
            "SELECT c.src, e.sold, c.sold FROM volume_d1 e JOIN volume_d1 c ON c.market = e.market AND c.key = e.key "
            "AND c.day = e.day AND c.src NOT IN ('books') WHERE e.market = ? AND e.src = 'books' AND e.day >= ? "
            "AND e.sold IS NOT NULL AND c.sold > 0 ORDER BY c.src", (market, (now_ms - BACKTEST_DAYS * DAY_MS) // DAY_MS)):
        ratios.setdefault(src, []).append(estimated / confirmed)
    return tuple((src, backtest(r)) for src, r in sorted(ratios.items()))


def status_facts(db: Database, cfg: Config, now_ms: int) -> StatusFacts:
    """Gather the Status page's facts.

    Args:
        db: Database.
        cfg: Config: markets, retention, whether a key is set.
        now_ms: Now; scan counts cover the 7 days before it.

    Returns:
        The facts.
    """
    markets = []
    for market in cfg.markets:
        state = db.get_sync_state(market)
        newest, priced, oldest, latest, local, books = db.sync_facts(market, now_ms - 7 * DAY_MS)
        markets.append(MarketFacts(market, state.last_hour, state.last_run, newest, priced, oldest, latest, local, books,
                                   _backtests(db, market, now_ms)))
    characters = tuple(CharacterFacts(*row) for row in db.characters())
    results = [(p, outcome == "sold") for p, outcome in db.conn.execute(
        "SELECT p_at_post, outcome FROM posts WHERE outcome IN ('sold', 'expired') AND p_at_post IS NOT NULL "
        "AND at >= ? ORDER BY post_id", (now_ms - CALIBRATION_DAYS * DAY_MS,))]
    return StatusFacts(tuple(markets), characters, bool(cfg.api_key), _db_bytes(db), cfg.retention_days,
                       calibration(results))


def status_page(db: Database, cfg: Config, now_ms: int, offset_ms: int) -> dict:
    """The Status payload (SPEC §3.5 dashboard data).

    Args:
        db: Database.
        cfg: Config.
        now_ms: Now.
        offset_ms: Local time minus UTC.

    Returns:
        {"alerts": [{"level", "title", "text"}], "cards": [{"title", "badge", "badge_class", "lines"}]}.
    """
    rows = [StatusRow(s.topic, s.level, s.kind, s.data, s.at) for s in db.statuses()]
    return status_view(rows, status_facts(db, cfg, now_ms), now_ms, offset_ms)
