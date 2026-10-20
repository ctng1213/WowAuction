"""The journal read behind the History page (SPEC §3.5 History): every captured event of the last days, one
shape for all of them, mixed into db.sqlite.Database.

Each row: (at ms, character id, kind, item key, units, price each copper or None, plan, a, b, expected, facts),
where plan is (status, done units, planned units) of the action the event matched, or None, and a / b depend on
the kind: posted (duration h, deposit), sold / expired / cancelled / won (its post's time or None, that post's
deposit or None), bought and bought_vendor (-, -), crafted (-, -), disenchanted (results "key,qty;...", -),
paid_out (money taken, profit realized or None), mailed (to character, -), missed and skipped (action kind, -),
untracked (units, + gained or − lost, -). The last two set the row against its plan (SPEC §3.4.1, D68-D70):
expected is the expected net copied when the event matched an action (float copper, or None), and facts is a
post's engine.tracking.PostResult, a chain carrier's ChainFacts (a buy or craft whose action carries its chain's
profit), or None. A 12th column is the row's event id (a post's post id, a missed or skipped action's id; None for an
untracked change), so History can tell which events count on a position (SPEC §3.4D.9, PO9).
"""
from __future__ import annotations

import sqlite3

from ahsync.engine.tracking import ChainFacts, PostResult

JournalRow = tuple

_PLAN = "a.status, COALESCE(a.done_qty, 0), a.qty"
_NO_FACTS = "NULL, NULL, NULL, NULL"
_PAID = "(SELECT o.realized FROM auction_mail m JOIN payouts o ON o.mail_id = m.event_id WHERE m.post_id = p.post_id)"
# A buy or craft whose action carries its chain's profit (SPEC §3.4 expected net): its chain id, its action's net.
_CARRIER = ("CASE WHEN a.kind IN ('buy', 'craft') AND a.expected_net <> 0 THEN COALESCE(a.parent_id, a.action_id) END, "
            "a.expected_net")
_OPEN = ("pending", "late")      # a post action still to come (a partial one has posted; it may never finish)
_CHUNK = 500                    # chain ids per query, well inside SQLite's variable limit
# (select list and joins, the table alias whose char_id and time filter the rows, its time column). After the 11
# row columns come 4 more, (expected, x, y, z), then the event id: posted (expected, outcome, its payout's realized, its action's
# parent), bought and crafted (expected, chain id when a carrier, the action's expected net, -), disenchanted
# (expected, -, -, -), the others none.
_QUERIES = (
    (f"p.at, p.char_id, 'posted', p.key, p.stack, p.price, {_PLAN}, p.duration_h, p.deposit, p.expected_net, "
     f"p.outcome, {_PAID}, a.parent_id, p.post_id FROM posts p LEFT JOIN actions a ON a.action_id = p.action_id "
     "WHERE p.confirmed = 1", "p", "at"),
    (f"m.at, m.char_id, m.outcome, m.key, m.qty, m.price, NULL, NULL, NULL, p.at, p.deposit, {_NO_FACTS}, m.event_id "
     "FROM auction_mail m LEFT JOIN posts p ON p.post_id = m.post_id WHERE 1 = 1", "m", "at"),
    (f"u.at, u.char_id, CASE u.source WHEN 'vendor' THEN 'bought_vendor' ELSE 'bought' END, u.key, u.qty, u.price, "
     f"{_PLAN}, NULL, NULL, u.expected_net, {_CARRIER}, NULL, u.event_id FROM purchases u "
     "LEFT JOIN actions a ON a.action_id = u.action_id WHERE u.confirmed = 1", "u", "at"),
    (f"c.at, c.char_id, 'crafted', (SELECT MIN(r.product_key) FROM recipes r WHERE r.spell_id = c.spell_id), c.count, "
     f"NULL, {_PLAN}, NULL, NULL, c.expected_net, {_CARRIER}, NULL, c.event_id FROM crafts c "
     "LEFT JOIN actions a ON a.action_id = c.action_id WHERE 1 = 1", "c", "at"),
    (f"d.at, d.char_id, 'disenchanted', d.key, 1, NULL, {_PLAN}, d.results, NULL, d.expected_net, NULL, NULL, NULL, d.event_id "
     "FROM disenchants d LEFT JOIN actions a ON a.action_id = d.action_id WHERE 1 = 1", "d", "at"),
    (f"o.at, o.char_id, 'paid_out', o.key, o.qty, NULL, NULL, NULL, NULL, o.money, o.realized, {_NO_FACTS}, o.event_id "
     "FROM payouts o WHERE 1 = 1", "o", "at"),
    (f"s.at, s.char_id, 'mailed', s.key, s.qty, NULL, {_PLAN}, s.to_char, NULL, {_NO_FACTS}, s.event_id FROM mail_sent s "
     "LEFT JOIN actions a ON a.action_id = s.action_id WHERE s.confirmed = 1", "s", "at"),
    (f"a.slot_ms, a.char_id, a.status, a.key, a.qty, a.price, a.status, COALESCE(a.done_qty, 0), a.qty, a.kind, NULL, "
     f"{_NO_FACTS}, a.action_id FROM actions a WHERE a.status IN ('missed', 'skipped')", "a", "slot_ms"),
    (f"x.at, x.char_id, 'untracked', x.key, ABS(x.delta), NULL, NULL, NULL, NULL, x.delta, NULL, {_NO_FACTS}, NULL "
     "FROM untracked x WHERE 1 = 1", "x", "at"),
)


def _facts(r: tuple, chains: dict[str, ChainFacts]) -> PostResult | ChainFacts | None:
    """The plan facts of one raw journal row (D68, D69).

    Args:
        r: A raw row: the 11 row columns, then (expected, x, y, z) as _QUERIES describes.
        chains: Chain id -> its facts (share 1).

    Returns:
        A post's PostResult; a carrier's ChainFacts with its share (its copied expected ÷ its action's expected
        net, i.e. matched units ÷ the action's units; 1 when either is missing); else None.
    """
    if r[2] == "posted":
        return PostResult(r[12], r[13], r[10], r[14])
    if r[2] in ("bought", "crafted") and r[12] is not None:
        c = chains[r[12]]
        share = r[11] / r[13] if r[11] is not None and r[13] else 1.0
        return ChainFacts(c.chain_id, c.posts, c.open_posts, c.disenchants, share)
    return None


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
        raw: list[tuple] = []
        for select, alias, time_col in _QUERIES:
            raw += [r for r in self.conn.execute(
                f"SELECT {select} AND {alias}.char_id IN ({marks}) AND {alias}.{time_col} >= ?", (*char_ids, since_ms))
                if r[3] is not None]
        chains = self.chain_facts(sorted({r[12] for r in raw if r[2] in ("bought", "crafted") and r[12] is not None}))
        out = [(r[0], r[1], r[2], r[3], r[4], r[5], (r[6], r[7], r[8]) if r[6] is not None else None, r[9], r[10],
                r[11], _facts(r, chains), r[15]) for r in raw]
        out.sort(key=lambda r: (-r[0], r[2], r[1], r[3], r[4]))
        return out

    def chain_facts(self, chain_ids: list[str]) -> dict[str, ChainFacts]:
        """The chains' posts so far, their open post actions and whether they plan a disenchant (D69).

        Args:
            chain_ids: Chain ids (a chain's first action id).

        Returns:
            Chain id -> ChainFacts (share 1): the confirmed posts matched to its post actions, by time then id;
            its post actions still pending or late; disenchants when it plans one and has no post.
        """
        posts: dict[str, list[PostResult]] = {cid: [] for cid in chain_ids}
        open_posts: dict[str, set[str]] = {cid: set() for cid in chain_ids}
        de: set[str] = set()
        for start in range(0, len(chain_ids), _CHUNK):
            part = chain_ids[start:start + _CHUNK]
            for cid, action_id, kind, status, post_id, outcome, paid, deposit in self.conn.execute(
                    f"SELECT COALESCE(a.parent_id, a.action_id), a.action_id, a.kind, a.status, p.post_id, p.outcome, "
                    f"{_PAID}, p.deposit FROM actions a "
                    "LEFT JOIN posts p ON p.action_id = a.action_id AND p.confirmed = 1 "
                    f"WHERE COALESCE(a.parent_id, a.action_id) IN ({','.join('?' * len(part))}) "
                    "AND a.kind IN ('post', 'de') ORDER BY p.at, p.post_id, a.action_id", part):
                if kind == "de":
                    de.add(cid)
                    continue
                if post_id is not None:
                    posts[cid].append(PostResult(outcome, paid, deposit, cid))
                if status in _OPEN:
                    open_posts[cid].add(action_id)
        return {cid: ChainFacts(cid, tuple(posts[cid]), len(open_posts[cid]), cid in de and not posts[cid])
                for cid in chain_ids}
