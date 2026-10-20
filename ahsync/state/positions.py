"""The positions store (SPEC §3.2 positions and position_lots, §3.4D.9), mixed into db.sqlite.Database.

It reads what engine.positions needs (the matched purchases and crafts that are no lot yet, the stored positions with
their lots, every event that moves units, the newest holdings and snapshot times) and writes what it decides. Every
write is an idempotent upsert: a lot is keyed by (position, event id), so reading one SavedVariables file twice never
adds a lot twice (PO8).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from ahsync.engine.positions import COMPLETED, Existing, Lot, Move, Opening, Position, Seed, World
from ahsync.engine.position_views import PositionView

MATCH_WINDOW_MS = 24 * 3_600_000          # a buy is open for matching 24 h after its slot (SPEC §3.4.1)
_CHAIN = "COALESCE(a.parent_id, a.action_id)"
_CARRIER = ("SELECT expected_net FROM actions WHERE COALESCE(parent_id, action_id) = ? AND kind IN ('buy', 'craft') "
            "AND expected_net <> 0 ORDER BY slot_ms, action_id LIMIT 1")
_WINDOW = "SELECT MIN(window_start_ms) FROM actions WHERE COALESCE(parent_id, action_id) = ? AND kind = 'post'"


class PositionStore:
    """Reads and writes of positions; needs `self.conn`."""

    conn: sqlite3.Connection

    def _recipes(self) -> tuple[dict[int, tuple[int, int, int]], dict[int, list[tuple[int, int]]]]:
        """Every recipe's product, yields and reagents.

        Returns:
            (spell -> (product key, yield min, yield max), spell -> [(reagent key, count)] by key).
        """
        recipes = {spell: (product, ymin or 1, ymax or ymin or 1) for spell, product, ymin, ymax in self.conn.execute(
            "SELECT spell_id, MIN(product_key), MIN(yield_min), MIN(yield_max) FROM recipes "
            "WHERE product_key IS NOT NULL GROUP BY spell_id")}
        reagents: dict[int, list[tuple[int, int]]] = {}
        for spell, key, count in self.conn.execute(
                "SELECT spell_id, key, MIN(count) FROM reagents GROUP BY spell_id, key ORDER BY spell_id, key"):
            reagents.setdefault(spell, []).append((key, count))
        return recipes, reagents

    def _opening_facts(self, chain_id: str, action_qty: int, units: int, own_net: float | None) -> tuple[float, int | None]:
        """An opening's share of its opportunity's expected net and the chain's first selling window.

        Args:
            chain_id: The matched action's chain.
            action_qty: The matched action's units.
            units: The event's units.
            own_net: The expected net copied into the event when it matched (T23), or None.

        Returns:
            (its own copied net, else the chain carrier's net × units ÷ the action's units (a mat buy carries none);
            the chain's first post window start or None).
        """
        net = own_net or 0.0
        if not net:
            row = self.conn.execute(_CARRIER, (chain_id,)).fetchone()
            net = row[0] * units / action_qty if row and action_qty else 0.0
        return net, self.conn.execute(_WINDOW, (chain_id,)).fetchone()[0]

    def position_openings(self) -> list[Opening]:
        """Confirmed purchases and crafts that matched a funded opportunity's buy or craft action and are no lot yet.

        A craft's units are its count × the recipe's average yield, half up; its unit cost is its reagents' average
        cost basis now, per unit made, half up (the lot keeps it from then on).

        Returns:
            Openings, by time then event id.
        """
        out = []
        for event_id, char_id, key, qty, price, at, chain, cand, own, a_qty in self.conn.execute(
                f"SELECT u.event_id, u.char_id, u.key, u.qty, u.price, u.at, {_CHAIN}, a.cand_id, u.expected_net, a.qty "
                "FROM purchases u JOIN actions a ON a.action_id = u.action_id WHERE u.confirmed = 1 AND u.qty > 0 "
                "AND a.kind = 'buy' AND a.cand_id IS NOT NULL "
                "AND u.event_id NOT IN (SELECT event_id FROM position_lots) ORDER BY u.at, u.event_id").fetchall():
            net, window = self._opening_facts(chain, a_qty, qty, own)
            out.append(Opening(event_id, char_id, key, qty, price or 0, at, chain, cand, net, window))
        return out + self._craft_openings()

    def _craft_openings(self) -> list[Opening]:
        """The craft half of position_openings.

        Returns:
            Openings of matched crafts, by time then event id.
        """
        recipes, reagents = self._recipes()
        avg = {k: total / qty for k, qty, total in self.conn.execute(
            "SELECT key, qty, total_cost FROM cost_basis WHERE qty > 0")}
        out = []
        for event_id, char_id, spell, count, at, chain, cand, own, a_qty in self.conn.execute(
                f"SELECT c.event_id, c.char_id, c.spell_id, c.count, c.at, {_CHAIN}, a.cand_id, c.expected_net, a.qty "
                "FROM crafts c JOIN actions a ON a.action_id = c.action_id WHERE a.kind = 'craft' "
                "AND a.cand_id IS NOT NULL AND c.count > 0 "
                "AND c.event_id NOT IN (SELECT event_id FROM position_lots) ORDER BY c.at, c.event_id").fetchall():
            if spell not in recipes:
                continue
            product, ymin, ymax = recipes[spell]
            units = (count * (ymin + ymax) + 1) // 2
            paid = sum(avg.get(k, 0.0) * n * count for k, n in reagents.get(spell, ()))
            net, window = self._opening_facts(chain, a_qty, count, own)
            out.append(Opening(event_id, char_id, product, units, int(paid / units + 0.5), at, chain, cand, net, window,
                               True))
        return out

    def existing_positions(self) -> list[Existing]:
        """The stored positions with the chain of their first lot's action.

        Returns:
            Existing records, oldest first (ties: id).
        """
        return [Existing(*r) for r in self.conn.execute(
            f"SELECT p.position_id, p.char_id, p.key, COALESCE((SELECT {_CHAIN} FROM position_lots l "
            "LEFT JOIN purchases u ON u.event_id = l.event_id LEFT JOIN crafts c ON c.event_id = l.event_id "
            "JOIN actions a ON a.action_id = COALESCE(u.action_id, c.action_id) WHERE l.position_id = p.position_id "
            "ORDER BY l.at, l.event_id LIMIT 1), ''), p.state FROM positions p ORDER BY p.opened_at, p.position_id")]

    def add_position(self, o: Opening, market: str | None) -> None:
        """Store a new position from its opening event (upsert: an existing id is left alone).

        Args:
            o: The opening (its event id is the position id).
            market: Its character's market.
        """
        self.conn.execute(
            "INSERT OR IGNORE INTO positions(position_id, char_id, market, cand_id, strategy, key, state, opened_at, units, "
            "units_left, cost_total, cost_left, expected_net_open, target_window_ms, realized) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Holding', ?, 0, 0, 0, 0, 0, ?, 0)",
            (o.event_id, o.char_id, market, o.cand_id, o.cand_id.split(":", 1)[0], o.key, o.at_ms, o.target_window_ms))

    def add_lot(self, position_id: str, o: Opening) -> bool:
        """Add a lot (upsert by event id, PO8) and its share of the expected net at open.

        Args:
            position_id: Its position.
            o: The event.

        Returns:
            True when the lot was new.
        """
        added = self.conn.execute(
            "INSERT OR IGNORE INTO position_lots(position_id, event_id, units, unit_cost, at) VALUES (?, ?, ?, ?, ?)",
            (position_id, o.event_id, o.units, o.unit_cost, o.at_ms)).rowcount > 0
        if added:
            self.conn.execute("UPDATE positions SET expected_net_open = expected_net_open + ? WHERE position_id = ?",
                              (o.expected_net, position_id))
        return added

    def position_seeds(self) -> list[Seed]:
        """Every stored position's opening facts with its lots, and whether its buy is still being filled.

        Returns:
            Seeds, oldest first: the opening character and item are its first lot event's; acquiring_until is set
            while that event's buy action is partial (open for matching until 24 h after its slot).
        """
        lots: dict[str, list[Lot]] = {}
        first: dict[str, tuple] = {}
        for pid, event_id, units, cost, at, char_u, key_u, char_c, spell, status, slot, qty, done in self.conn.execute(
                "SELECT l.position_id, l.event_id, l.units, l.unit_cost, l.at, u.char_id, u.key, c.char_id, c.spell_id, "
                "a.status, a.slot_ms, a.qty, COALESCE(a.done_qty, 0) FROM position_lots l "
                "LEFT JOIN purchases u ON u.event_id = l.event_id LEFT JOIN crafts c ON c.event_id = l.event_id "
                "LEFT JOIN actions a ON a.action_id = COALESCE(u.action_id, c.action_id) "
                "ORDER BY l.position_id, l.at, l.event_id"):
            lots.setdefault(pid, []).append(Lot(event_id, units, cost, at))
            first.setdefault(pid, (char_u or char_c, key_u, spell, status, slot, qty, done))
        recipes, _ = self._recipes()
        out = []
        for pid, char_id, market, cand, key, opened, net, window in self.conn.execute(
                "SELECT position_id, char_id, market, cand_id, key, opened_at, expected_net_open, target_window_ms "
                "FROM positions ORDER BY opened_at, position_id").fetchall():
            if pid not in lots:
                continue
            char0, bought_key, spell, status, slot, qty, done = first[pid]
            acquiring = bought_key is not None and status == "partial"
            key0 = bought_key if bought_key is not None else recipes.get(spell, (key,))[0]
            out.append(Seed(pid, char0 or char_id, market, cand or "", key0, opened, tuple(lots[pid]), net or 0.0,
                            window, slot + MATCH_WINDOW_MS if acquiring else None, (qty - done) if acquiring else 0))
        return out

    def position_moves(self, since_ms: int) -> list[Move]:
        """Every event since a time that moves units: posts, auction mail, payouts, disenchants, crafts, sent mail.

        Args:
            since_ms: Oldest event to include, UTC ms.

        Returns:
            Moves (unsorted; engine.positions.track orders them). A craft's reagents are its recipe's × its count.
        """
        q = self.conn.execute
        moves = [Move(at, pid, "post", c, k, n, pid, None, None, dep) for at, pid, c, k, n, dep in q(
            "SELECT at, post_id, char_id, key, stack, deposit FROM posts WHERE confirmed = 1 AND at >= ?", (since_ms,))]
        moves += [Move(at, e, outcome, c, k, n, pid, e, price) for at, e, outcome, c, k, n, pid, price in q(
            "SELECT at, event_id, outcome, char_id, key, qty, post_id, price FROM auction_mail "
            "WHERE outcome IN ('sold', 'expired', 'cancelled') AND at >= ?", (since_ms,))]
        moves += [Move(at, e, "payout", c, k, n, None, mail, money, dep) for at, e, c, k, n, mail, money, dep in q(
            "SELECT at, event_id, char_id, key, qty, mail_id, money, deposit FROM payouts WHERE at >= ?", (since_ms,))]
        moves += [Move(at, e, "de", c, k, 1) for at, e, c, k in q(
            "SELECT at, event_id, char_id, key FROM disenchants WHERE at >= ?", (since_ms,))]
        moves += [Move(at, e, "mail", c, k, n, to_char=to) for at, e, c, k, n, to in q(
            "SELECT at, event_id, char_id, key, qty, to_char FROM mail_sent WHERE confirmed = 1 AND at >= ?",
            (since_ms,))]
        recipes, reagents = self._recipes()
        for at, e, c, spell, count in q("SELECT at, event_id, char_id, spell_id, count FROM crafts WHERE at >= ? "
                                        "AND count > 0", (since_ms,)).fetchall():
            if spell in recipes:
                product, ymin, ymax = recipes[spell]
                moves.append(Move(at, e, "craft", c, product, (count * (ymin + ymax) + 1) // 2,
                                  reagents=tuple((k, n * count) for k, n in reagents.get(spell, ()))))
        return moves

    def position_world(self) -> World:
        """Every character's unbound holdings and snapshot times.

        Returns:
            World: (character, key) -> units in bags, bank and mail; (character, place) -> capture time.
        """
        holdings: dict[tuple[str, int], int] = {}
        for c, k, n in self.conn.execute("SELECT char_id, key, SUM(qty) FROM inventory WHERE bound = 0 "
                                         "GROUP BY char_id, key"):
            holdings[(c, k)] = n
        snapshots = {(c, loc): at for c, loc, at in self.conn.execute("SELECT char_id, location, at FROM snapshots")}
        return World(holdings, snapshots)

    def write_positions(self, positions: Sequence[Position]) -> None:
        """Store what the facts say of each position (SPEC §3.2 positions).

        Args:
            positions: engine.positions.track's records.
        """
        self.conn.executemany(
            "UPDATE positions SET char_id = ?, key = ?, state = ?, units = ?, units_left = ?, cost_total = ?, "
            "cost_left = ?, realized = ?, closed_at = ?, next_action = ? WHERE position_id = ?",
            [(p.char_id, p.key, p.state, p.units, p.units_left, p.cost_total, p.cost_left, p.realized, p.closed_at,
              p.next_action, p.position_id) for p in positions])

    def write_position_views(self, views: Sequence[PositionView]) -> None:
        """Store each position's latest evaluation: state, est. net now, latest window and next action.

        Args:
            views: position_views.evaluate's rows (found stock rows match no position and change nothing).
        """
        self.conn.executemany(
            "UPDATE positions SET state = ?, expected_net_now = ?, latest_window_ms = ?, next_action = ? "
            "WHERE position_id = ?", [(v.state, v.net_now, v.window_ms, v.next_action, v.position_id) for v in views])

    def replace_snapshot_positions(self, market: str, rows: list[list]) -> bool:
        """Swap the published portfolio snapshot's positions for re-evaluated ones (the hourly status pass).

        Args:
            market: Market id.
            rows: recommend.portfolio.position_json rows.

        Returns:
            True when the market had a snapshot.
        """
        row = self.conn.execute("SELECT data FROM portfolio_snapshot WHERE market = ?", (market,)).fetchone()
        if row is None:
            return False
        data = json.loads(row[0])
        data["positions"] = rows
        self.conn.execute("UPDATE portfolio_snapshot SET data = ? WHERE market = ?",
                          (json.dumps(data, sort_keys=True, separators=(",", ":")), market))
        return True

    def delete_closed_positions_before(self, cutoff_ms: int) -> int:
        """Delete positions closed before a time, with their lots (SPEC §3.2: kept 90 days after they close).

        Args:
            cutoff_ms: Positions with closed_at < cutoff_ms go.

        Returns:
            Positions deleted.
        """
        with self.transaction():
            gone = self.conn.execute("DELETE FROM positions WHERE closed_at IS NOT NULL AND closed_at < ?",
                                     (cutoff_ms,)).rowcount
            self.conn.execute("DELETE FROM position_lots WHERE position_id NOT IN (SELECT position_id FROM positions)")
        return gone
