"""The Status page, built when it is asked for (SPEC §3.5 Status).

Unlike the other pages it is not staged by the full refresh: sync runs, SavedVariables
reads and failures change it between refreshes. It reads only small tables (status
lines, sync state, current prices, scans, characters, resolved posts) and the database file's size,
never history or the intelligence; engine.status_view makes every text. The expected-against-realized sums (K7)
are recomputed from the journal and the positions over 90 days (the stored History view covers only 30), by the
same engine.tracking.profit_sums over the same judged rows the History page uses.
"""
from __future__ import annotations

from ahsync.config import Config, is_cloud_market, market_sources
from ahsync.db.sqlite import Database
from ahsync.engine.sell_record import calibration
from ahsync.engine.history_view import judged_rows
from ahsync.engine.tracking import ProfitSums, profit_sums
from ahsync.engine.volume import backtest
from ahsync.engine.status_view import CharacterFacts, MarketFacts, StatusFacts, StatusRow, status_view
from ahsync.recommend.positions_pass import current

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
COVERAGE_HOURS = 2_160           # SPEC §3.5 Data coverage: the last 90 days
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
        (source, Backtest) per id-diff source (cover kind "ids") with a row in the window, by source. The book
        estimate is scaled to a whole day by the hours it observed (V30); your mail is no comparison, since it
        sees only your own auctions.
    """
    ratios: dict[str, list[float]] = {}
    for src, estimated, covered, confirmed in db.conn.execute(
            "SELECT c.src, e.sold, ec.covered, c.sold FROM volume_d1 e "
            "JOIN volume_d1 c ON c.market = e.market AND c.key = e.key AND c.day = e.day "
            "JOIN volume_cover cc ON cc.market = c.market AND cc.day = c.day AND cc.src = c.src AND cc.kind = 'ids' "
            "LEFT JOIN volume_cover ec ON ec.market = e.market AND ec.day = e.day AND ec.src = e.src "
            "WHERE e.market = ? AND e.src = 'books' AND e.day >= ? AND e.sold IS NOT NULL AND c.sold > 0 "
            "ORDER BY c.src, e.key, e.day", (market, (now_ms - BACKTEST_DAYS * DAY_MS) // DAY_MS)):
        share = sum(int(s) for s in covered.split(",")) / 86_400 if covered else 1.0
        if share > 0:
            ratios.setdefault(src, []).append(estimated / share / confirmed)
    return tuple((src, backtest(r)) for src, r in sorted(ratios.items()))


def _profit(db: Database, now_ms: int) -> ProfitSums:
    """Expected against realized profit over the last 90 days of every character's events (SPEC §8.8, K7).

    Args:
        db: Database.
        now_ms: Now.

    Returns:
        The sums, as the History page builds them (engine.tracking.profit_sums over history_view.judged_rows: the
        events' own rows, less those counting on a position, and the positions', PO9).
    """
    since = now_ms - CALIBRATION_DAYS * DAY_MS
    rows = db.journal([row[0] for row in db.characters()], since)
    return profit_sums(judged_rows(rows, current(db, now_ms), since))


def _coverage_sources(cfg: Config, market: str) -> tuple[str, ...]:
    """The sources the Data coverage card lists for a market (SPEC §3.5).

    Args:
        cfg: Config.
        market: Market id.

    Returns:
        "local", then each ranked remote source whose address isn't "", then the primary when it is missing.
    """
    own = market_sources(cfg, market)
    used = ["local"] + [s for s in own.rank if own.addresses.get(s, s)]
    if own.primary not in used:
        used.append(own.primary)
    return tuple(used)


def _cloud_facts(db: Database, cfg: Config, market: str) -> dict:
    """A cloud market's summary facts (S21, S23, S24).

    Args:
        db: Database.
        cfg: Config ([cloud] poll_minutes).
        market: A cloud market.

    Returns:
        MarketFacts' cloud fields: cloud_on, the published document's built and prices times, the next poll.
    """
    intel = db.read_intel(market)
    _etag, _updated, fetched, _held = db.source_state(market, "cloud")
    return {"cloud_on": True, "cloud_built_ms": intel.built_ms if intel else None,
            "cloud_prices_ms": (intel.observations_until_ms or intel.built_ms) if intel else None,
            "cloud_next_ms": None if fetched is None else fetched + cfg.cloud.poll_minutes * 60_000}


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
        primary = market_sources(cfg, market).primary                                     # SRC2
        newest, priced, oldest, latest, local, books = db.sync_facts(market, now_ms - 7 * DAY_MS, primary)
        on = bool(market_sources(cfg, market).addresses["ahledger"])                      # S18
        _etag, updated_ms, _fetched, _checkpoint = db.source_state(market, "ahledger")
        items = 0 if updated_ms is None else db.ahledger_item_count(market, updated_ms // 1000)
        end = now_ms // HOUR_MS                                                           # the coverage window, M20
        hours = db.observed_hours(market, end - COVERAGE_HOURS, end - 1)
        partial = db.newest_partial_scan(market, (end - COVERAGE_HOURS) * HOUR_MS, now_ms)
        cloud = _cloud_facts(db, cfg, market) if is_cloud_market(cfg, market) else {}
        if cloud:
            newest = cloud["cloud_prices_ms"]                     # the summary's prices are the market's (D100)
        markets.append(MarketFacts(market, state.last_hour, state.last_run, newest, priced, oldest, latest, local, books,
                                   _backtests(db, market, now_ms), primary, on, updated_ms, items,
                                   hours, _coverage_sources(cfg, market), partial, **cloud))
    characters = tuple(CharacterFacts(*row) for row in db.characters())
    results = [(p, outcome == "sold") for p, outcome in db.conn.execute(
        "SELECT p_at_post, outcome FROM posts WHERE outcome IN ('sold', 'expired') AND p_at_post IS NOT NULL "
        "AND at >= ? ORDER BY post_id", (now_ms - CALIBRATION_DAYS * DAY_MS,))]
    return StatusFacts(tuple(markets), characters, bool(cfg.api_key), _db_bytes(db), cfg.retention_days,
                       calibration(results), _profit(db, now_ms))


def status_page(db: Database, cfg: Config, now_ms: int, offset_ms: int, app: dict | None = None) -> dict:
    """The Status payload (SPEC §3.5 dashboard data).

    Args:
        db: Database.
        cfg: Config.
        now_ms: Now.
        offset_ms: Local time minus UTC.
        app: GET /api/app's payload from the app shell, for the "AHSync app" card; None: no card.

    Returns:
        {"alerts": [{"level", "title", "text"}], "cards": [{"title", "badge", "badge_class", "lines"}]}.
    """
    rows = [StatusRow(s.topic, s.level, s.kind, s.data, s.at) for s in db.statuses()]
    return status_view(rows, status_facts(db, cfg, now_ms), now_ms, offset_ms, app)
