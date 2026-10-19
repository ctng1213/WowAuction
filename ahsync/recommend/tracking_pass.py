"""The tracking pass (SPEC §3.4.1): stored events matched to plan actions, and posts resolved by mail.

It runs after every SavedVariables read and before the hourly status pass. Only confirmed
purchases and posts match; an attempt waits for its confirmation. Crafts match by their
recipe's product. engine.tracking decides; this module only reads and writes the rows.
"""
from __future__ import annotations

from ahsync.db.sqlite import Database
from ahsync.engine.tracking import AuctionMail, DoneEvent, OpenAction, OpenPost, match, resolve_posts

# event table -> (action kind, SQL for its unmatched rows as event_id, char_id, key, qty, price, at)
_EVENTS = {
    "purchases": ("buy", "SELECT event_id, char_id, key, qty, price, at FROM purchases "
                         "WHERE action_id IS NULL AND confirmed = 1"),
    "posts": ("post", "SELECT post_id, char_id, key, stack, price, at FROM posts WHERE action_id IS NULL AND confirmed = 1"),
    "crafts": ("craft", "SELECT c.event_id, c.char_id, MIN(r.product_key), c.count, NULL, c.at FROM crafts c "
                        "JOIN recipes r ON r.spell_id = c.spell_id AND r.product_key IS NOT NULL "
                        "WHERE c.action_id IS NULL GROUP BY c.event_id"),
    "mail_sent": ("mail", "SELECT event_id, char_id, key, qty, NULL, at FROM mail_sent WHERE action_id IS NULL"),
}
_ID_COLUMN = {"purchases": "event_id", "posts": "post_id", "crafts": "event_id", "mail_sent": "event_id"}


def _open_actions(db: Database) -> list[OpenAction]:
    """Every action still open for matching.

    Args:
        db: Database.

    Returns:
        Pending, late and partial actions.
    """
    rows = db.conn.execute("SELECT action_id, char_id, kind, key, qty, COALESCE(done_qty, 0), price, slot_ms, "
                           "window_end_ms FROM actions WHERE status IN ('pending', 'late', 'partial') ORDER BY action_id")
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
        for event_id, char_id, key, qty, price, at in db.conn.execute(sql):
            if key is not None and qty:
                events.append(DoneEvent(event_id, char_id, kind, key, qty, price, at))
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
        Posts resolved.
    """
    posts = [OpenPost(*r) for r in db.conn.execute(
        "SELECT post_id, char_id, key, stack, price, at FROM posts WHERE outcome IS NULL AND confirmed = 1")]
    mails = [AuctionMail(*r) for r in db.conn.execute(
        "SELECT event_id, char_id, key, qty, price, outcome, at FROM auction_mail WHERE post_id IS NULL")]
    resolutions = resolve_posts(posts, mails)
    for r in resolutions:
        db.conn.execute("UPDATE posts SET outcome = ?, resolved_at = ? WHERE post_id = ?", (r.outcome, r.at_ms, r.post_id))
        db.conn.execute("UPDATE auction_mail SET post_id = ? WHERE event_id = ?", (r.post_id, r.mail_id))
    return len(resolutions)


def tracking_pass(db: Database) -> dict[str, int]:
    """Match events to actions and resolve posts, in one transaction (SPEC §3.4.1).

    Args:
        db: Database (not inside a transaction).

    Returns:
        {"matched": n, "resolved": n}.

    Side effects:
        Sets action_id on matched events, done_qty, status and done_at on their actions, and outcome
        and resolved_at on resolved posts (with the mail's post_id).
    """
    with db.transaction():
        return {"matched": _match(db), "resolved": _resolve(db)}
