"""The journal read behind the History page (SPEC §3.5 History): every captured event of the last days, one
shape for all of them, mixed into db.sqlite.Database.

Each row: (at ms, character id, kind, item key, units, price each copper or None, plan, a, b), where plan is
(status, done units, planned units) of the action the event matched, or None, and a / b depend on the kind:
posted (duration h, deposit), sold / expired / cancelled / won (its post's time or None, that post's deposit
or None), bought and bought_vendor (-, -), crafted (-, -), disenchanted (results "key,qty;...", -),
paid_out (money taken, profit realized or None), mailed (to character, -), missed and skipped (action kind, -),
untracked (units, + gained or − lost, -).
"""
from __future__ import annotations

import sqlite3

JournalRow = tuple

_PLAN = "a.status, COALESCE(a.done_qty, 0), a.qty"
# (select list and joins, the table alias whose char_id and time filter the rows, its time column)
_QUERIES = (
    (f"p.at, p.char_id, 'posted', p.key, p.stack, p.price, {_PLAN}, p.duration_h, p.deposit FROM posts p "
     "LEFT JOIN actions a ON a.action_id = p.action_id WHERE p.confirmed = 1", "p", "at"),
    ("m.at, m.char_id, m.outcome, m.key, m.qty, m.price, NULL, NULL, NULL, p.at, p.deposit FROM auction_mail m "
     "LEFT JOIN posts p ON p.post_id = m.post_id WHERE 1 = 1", "m", "at"),
    (f"u.at, u.char_id, CASE u.source WHEN 'vendor' THEN 'bought_vendor' ELSE 'bought' END, u.key, u.qty, u.price, "
     f"{_PLAN}, NULL, NULL FROM purchases u LEFT JOIN actions a ON a.action_id = u.action_id WHERE u.confirmed = 1",
     "u", "at"),
    (f"c.at, c.char_id, 'crafted', (SELECT MIN(r.product_key) FROM recipes r WHERE r.spell_id = c.spell_id), c.count, "
     f"NULL, {_PLAN}, NULL, NULL FROM crafts c LEFT JOIN actions a ON a.action_id = c.action_id WHERE 1 = 1", "c", "at"),
    (f"d.at, d.char_id, 'disenchanted', d.key, 1, NULL, {_PLAN}, d.results, NULL FROM disenchants d "
     "LEFT JOIN actions a ON a.action_id = d.action_id WHERE 1 = 1", "d", "at"),
    ("o.at, o.char_id, 'paid_out', o.key, o.qty, NULL, NULL, NULL, NULL, o.money, o.realized FROM payouts o WHERE 1 = 1",
     "o", "at"),
    (f"s.at, s.char_id, 'mailed', s.key, s.qty, NULL, {_PLAN}, s.to_char, NULL FROM mail_sent s "
     "LEFT JOIN actions a ON a.action_id = s.action_id WHERE 1 = 1", "s", "at"),
    ("a.slot_ms, a.char_id, a.status, a.key, a.qty, a.price, a.status, COALESCE(a.done_qty, 0), a.qty, a.kind, NULL "
     "FROM actions a WHERE a.status IN ('missed', 'skipped')", "a", "slot_ms"),
    ("x.at, x.char_id, 'untracked', x.key, ABS(x.delta), NULL, NULL, NULL, NULL, x.delta, NULL FROM untracked x "
     "WHERE 1 = 1", "x", "at"),
)


class JournalReads:
    """Reads of the captured events for the History page; needs `self.conn`."""

    conn: sqlite3.Connection

    def journal(self, char_ids: list[str], since_ms: int) -> list[JournalRow]:
        """Every event and missed or skipped action of the characters since a time (SPEC §3.5 History).

        Args:
            char_ids: Character ids.
            since_ms: Oldest time to include, UTC ms.

        Returns:
            Rows as the module docstring describes, newest first (ties: kind, character, key, units);
            a craft whose recipe makes no item is left out.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        out = []
        for select, alias, time_col in _QUERIES:
            for r in self.conn.execute(f"SELECT {select} AND {alias}.char_id IN ({marks}) AND {alias}.{time_col} >= ?",
                                       (*char_ids, since_ms)):
                if r[3] is not None:
                    out.append((r[0], r[1], r[2], r[3], r[4], r[5], (r[6], r[7], r[8]) if r[6] is not None else None,
                                r[9], r[10]))
        out.sort(key=lambda r: (-r[0], r[2], r[1], r[3], r[4]))
        return out
