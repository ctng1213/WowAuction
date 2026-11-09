"""The tracking pass (SPEC §3.4.1): stored events matched to plan actions, and posts resolved by mail.

It runs after every SavedVariables read and before the hourly status pass. Only confirmed
purchases and posts match; an attempt waits for its confirmation. Crafts match by their
recipe's product. A matched purchase or craft of a funded opportunity opens or adds to a position, and every
position is replayed from its lots and the journal (recommend.positions_pass, SPEC §3.4D.9). engine.tracking decides;
this module only reads and writes the rows.
"""
from __future__ import annotations

from ahsync.db.sqlite import Database
from ahsync.engine.cost_basis import Bought, Disenchanted, Made, cost_basis
from ahsync.recommend.positions_pass import track_positions
from ahsync.engine.tracking import (AuctionMail, DoneEvent, OpenAction, OpenPost, Payout, SoldMail, match, realize,
                                    resolve_posts)

# event table -> (action kind, SQL for its unmatched rows as event_id, char_id, key, qty, price, at, recipient)
_EVENTS = {
    "purchases": ("buy", "SELECT event_id, char_id, key, qty, price, at, NULL FROM purchases "
                         "WHERE action_id IS NULL AND confirmed = 1"),
    "posts": ("post", "SELECT post_id, char_id, key, stack, price, at, NULL FROM posts "
                      "WHERE action_id IS NULL AND confirmed = 1"),
    "crafts": ("craft", "SELECT c.event_id, c.char_id, MIN(r.product_key), c.count, NULL, c.at, NULL FROM crafts c "
                        "JOIN recipes r ON r.spell_id = c.spell_id AND r.product_key IS NOT NULL "
                        "WHERE c.action_id IS NULL GROUP BY c.event_id"),
    "mail_sent": ("mail", "SELECT event_id, char_id, key, qty, NULL, at, to_char FROM mail_sent "
                          "WHERE action_id IS NULL AND confirmed = 1"),
    "disenchants": ("de", "SELECT event_id, char_id, key, 1, NULL, at, NULL FROM disenchants WHERE action_id IS NULL"),
}
_ID_COLUMN = {"purchases": "event_id", "posts": "post_id", "crafts": "event_id", "mail_sent": "event_id",
              "disenchants": "event_id"}
_EXPECTED = ("purchases", "posts", "crafts", "disenchants")   # tables that keep the matched action's expected net


def _open_actions(db: Database) -> list[OpenAction]:
    """Every action still open for matching.

    Args:
        db: Database.

    Returns:
        Pending, late and partial actions.
    """
    rows = db.conn.execute("SELECT action_id, char_id, kind, key, qty, COALESCE(done_qty, 0), price, slot_ms, "
                           "window_end_ms, to_char FROM actions WHERE status IN ('pending', 'late', 'partial') "
                           "ORDER BY action_id")
    return [OpenAction(*r) for r in rows]


def _events(db: Database) -> tuple[list[DoneEvent], dict[str, str]]:
    """Every unmatched event, as the action kind it can match.

    Args:
        db: Database.

    Returns:
        (the events, event id -> its table).
    """
    events, tables = [], {}
    for table, (kind, sql) in _EVENTS.items():
        for event_id, char_id, key, qty, price, at, to_char in db.conn.execute(sql):
            if key is not None and qty:
                events.append(DoneEvent(event_id, char_id, kind, key, qty, price, at, to_char))
                tables[event_id] = table
    return events, tables


def _match(db: Database) -> int:
    """Match unmatched events and update their actions.

    Args:
        db: Database (inside a transaction).

    Returns:
        Events matched.
    """
    events, tables = _events(db)
    matches, status = match(events, _open_actions(db))
    at = {e.event_id: e.at_ms for e in events}
    last: dict[str, int] = {}
    for m in matches:
        table = tables[m.event_id]
        db.conn.execute(f"UPDATE {table} SET action_id = ? WHERE {_ID_COLUMN[table]} = ?", (m.action_id, m.event_id))
        if table == "posts":           # the P' it was planned with, for calibration (SPEC §8.8, K5)
            db.conn.execute("UPDATE posts SET p_at_post = (SELECT conf FROM actions WHERE action_id = ?) "
                            "WHERE post_id = ?", (m.action_id, m.event_id))
        if table in _EXPECTED:         # its part of the plan's expected net, for History (SPEC §3.4.1, T23)
            db.conn.execute(f"UPDATE {table} SET expected_net = (SELECT expected_net * ? / qty FROM actions "
                            f"WHERE action_id = ? AND qty > 0) WHERE {_ID_COLUMN[table]} = ?",
                            (m.qty, m.action_id, m.event_id))
        last[m.action_id] = max(last.get(m.action_id, 0), at[m.event_id])
    for action_id, (done_qty, state) in status.items():
        db.conn.execute("UPDATE actions SET done_qty = ?, status = ?, done_at = ? WHERE action_id = ?",
                        (done_qty, state, last[action_id], action_id))
    return len(matches)


def _resolve(db: Database) -> int:
    """Resolve open posts by unused auction mail.

    Args:
        db: Database (inside a transaction).

    Returns:
        Mails that took a post's units (a post resolves once its units make its stack, T25).
    """
    posts = [OpenPost(*r) for r in db.conn.execute(
        "SELECT p.post_id, p.char_id, p.key, p.stack, p.price, p.at, p.sold_units, p.returned_units, "
        "COALESCE((SELECT MAX(m.outcome) FROM auction_mail m WHERE m.post_id = p.post_id "
        "AND m.outcome IN ('expired', 'cancelled')), 'expired'), p.duration_h FROM posts p "
        "WHERE p.outcome IS NULL AND p.confirmed = 1")]
    mails = [AuctionMail(*r) for r in db.conn.execute(
        "SELECT event_id, char_id, key, qty, price, outcome, at FROM auction_mail WHERE post_id IS NULL")]
    resolutions = resolve_posts(posts, mails)
    for r in resolutions:                                                     # T25: units first, outcome once whole
        db.conn.execute("UPDATE posts SET sold_units = ?, returned_units = ?, outcome = ?, "
                        "resolved_at = CASE WHEN ? IS NULL THEN NULL ELSE ? END WHERE post_id = ?",
                        (r.sold_units, r.returned_units, r.outcome, r.outcome, r.at_ms, r.post_id))
        db.conn.execute("UPDATE auction_mail SET post_id = ? WHERE event_id = ?", (r.post_id, r.mail_id))
    return len(resolutions)


def _made(db: Database) -> list[Made]:
    """Every craft with the units it made and the mats it used, from its recipe.

    Args:
        db: Database.

    Returns:
        Made, one per craft event whose recipe makes an item.
    """
    recipes = {spell: (product, ymin or 1, ymax or ymin or 1) for spell, product, ymin, ymax in db.conn.execute(
        "SELECT spell_id, MIN(product_key), MIN(yield_min), MIN(yield_max) FROM recipes WHERE product_key IS NOT NULL "
        "GROUP BY spell_id")}
    reagents: dict[int, list[tuple[int, int]]] = {}
    for spell, key, count in db.conn.execute(
            "SELECT spell_id, key, MIN(count) FROM reagents GROUP BY spell_id, key ORDER BY spell_id, key"):
        reagents.setdefault(spell, []).append((key, count))
    out = []
    for event_id, spell, count, at in db.conn.execute("SELECT event_id, spell_id, count, at FROM crafts"):
        if spell in recipes and count:
            product, ymin, ymax = recipes[spell]
            units = (count * (ymin + ymax) + 1) // 2        # count × average yield, half up
            out.append(Made(event_id, product, units, tuple((k, n * count) for k, n in reagents.get(spell, ())), at))
    return out


def _disenchanted(db: Database) -> list[Disenchanted]:
    """Every disenchant with what it gave (T18).

    Args:
        db: Database.

    Returns:
        Disenchanted, one per event; results as stored ("key,qty;..." by key).
    """
    out = []
    for event_id, key, results, at in db.conn.execute("SELECT event_id, key, results, at FROM disenchants"):
        pairs = (part.split(",") for part in results.split(";"))
        out.append(Disenchanted(event_id, key, tuple((int(k), int(n)) for k, n in pairs), at))
    return out


def _cost_basis(db: Database) -> int:
    """Replace the cost basis table from purchases, crafts, disenchants and what you hold now (SPEC §3.2, T9-T13, T18).

    Args:
        db: Database (inside a transaction).

    Returns:
        Items with a basis.
    """
    bought = [Bought(*r) for r in db.conn.execute(
        "SELECT event_id, key, qty, price, at FROM purchases WHERE confirmed = 1 AND qty > 0 AND price IS NOT NULL")]
    held: dict[int, int] = {}
    for key, units in db.conn.execute(
            "SELECT key, SUM(qty) FROM inventory WHERE bound = 0 GROUP BY key UNION ALL "
            "SELECT key, SUM(stack * COALESCE(count, 1)) FROM own_auctions GROUP BY key"):
        held[key] = held.get(key, 0) + units
    disenchanted = _disenchanted(db)
    results = sorted({k for d in disenchanted for k, _ in d.results})
    prices: dict[int, int] = {}
    for key in results:                                  # each result's lowest price now, any source (T20)
        row = db.conn.execute("SELECT MIN(price) FROM current WHERE key = ? AND price > 0", (key,)).fetchone()
        if row[0] is not None:
            prices[key] = row[0]
    basis = cost_basis(bought, _made(db), held, disenchanted, prices)
    db.conn.execute("DELETE FROM cost_basis")
    db.conn.executemany("INSERT INTO cost_basis(key, qty, total_cost, untracked_qty) VALUES (?, ?, ?, ?)",
                        [(k, b.qty, int(b.total_cost + 0.5), b.untracked) for k, b in basis.items()])
    return len(basis)


def _realize(db: Database) -> int:
    """Link new payouts to their sold mail and store the profit each realized (SPEC §3.4.1, T17).

    Args:
        db: Database (inside a transaction, after the cost basis is rebuilt).

    Returns:
        Payouts realized.
    """
    payouts = [Payout(*r) for r in db.conn.execute(
        "SELECT event_id, char_id, key, qty, money, at, deposit FROM payouts WHERE realized IS NULL AND qty > 0")]
    if not payouts:
        return 0
    sold = [SoldMail(*r) for r in db.conn.execute(
        "SELECT m.event_id, m.char_id, m.key, m.qty, m.at, COALESCE(p.deposit * 1.0 * m.qty / NULLIF(p.stack, 0), 0) "
        "FROM auction_mail m LEFT JOIN posts p ON p.post_id = m.post_id WHERE m.outcome = 'sold' "
        "AND m.event_id NOT IN (SELECT mail_id FROM payouts WHERE mail_id IS NOT NULL)")]
    cost_each = {key: total / qty for key, qty, total in db.conn.execute("SELECT key, qty, total_cost FROM cost_basis WHERE qty > 0")}
    done = realize(payouts, sold, cost_each)
    db.conn.executemany("UPDATE payouts SET mail_id = ?, realized = ? WHERE event_id = ?",
                        [(r.mail_id, r.profit, r.payout_id) for r in done])
    return len(done)


def tracking_pass(db: Database, now_ms: int | None = None) -> dict[str, int]:
    """Match events to actions, resolve posts, rebuild the cost basis and keep the positions, in one transaction
    (SPEC §3.4.1, §3.2, §3.4D.9).

    Args:
        db: Database (not inside a transaction).
        now_ms: Now, UTC ms, for the positions' time rules (an unfinished buy's window, the 31-day loss); None skips
            them.

    Returns:
        {"matched": n, "resolved": n, "costed": items with a basis, "paid": payouts realized, "opened": positions
        opened}.

    Side effects:
        Sets action_id and expected_net (the action's expected net × matched units ÷ its units) on matched
        events, done_qty, status and done_at on their actions, outcome
        and resolved_at on resolved posts (with the mail's post_id), replaces cost_basis, sets mail_id
        and realized on new payouts, and opens, adds lots to and updates positions (recommend.positions_pass).
    """
    with db.transaction():
        out = {"matched": _match(db), "resolved": _resolve(db), "costed": _cost_basis(db), "paid": _realize(db)}
        return out | {"opened": track_positions(db, now_ms)["opened"]}
