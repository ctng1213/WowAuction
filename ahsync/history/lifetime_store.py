"""The carried auction lifetimes and the per-gap ledger in the Historical Store (SPEC §2.4 "One snapshot at a time").

Mixed into db.sqlite.Database. The state is `engine.lifetimes.LifeState` as rows (`lifetime_state`, `open_auctions`,
`leave_tallies`); the ledger is what each gap learned, per item (`gap_flows`, `gap_seen`), kept with exact floats so
a day folded from it equals the batch pass bit for bit. Every write runs inside the caller's transaction.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ahsync.engine.expiry import LeaveWindow
from ahsync.engine.lifetimes import LifeFlow, LifeState

FLOW_FIELDS = ("sold", "expired", "listed", "ended", "expired_listings", "matched", "cancelled", "cancels", "t1_ended",
               "t1_left")


@dataclass(frozen=True)
class GapSummary:
    """One gap's ledger entry: its flows per seen item, new listings by position, and sold units by price (§2.4)."""

    gap: tuple[int, int]                               # (earlier ms, later ms)
    flows: dict[int, LifeFlow]                         # every item listed in either snapshot, zeros included
    positions: dict[int, dict[int, float]]             # key -> position (thousandths) -> units (BZ29)
    prices: dict[int, dict[int, float]]                # key -> unit price copper -> units (BZ52)


def encode_units(by_value: Mapping[int, float]) -> str:
    """Units by position or price as ledger text, exactly.

    Args:
        by_value: Position or price -> units.

    Returns:
        "<value>:<units>,..." by value, the units as `repr` floats (which read back bit for bit); "" when empty.
    """
    return ",".join(f"{value}:{units!r}" for value, units in sorted(by_value.items()))


def decode_units(text: str) -> dict[int, float]:
    """Ledger text back to units by position or price.

    Args:
        text: From `encode_units`.

    Returns:
        Position or price -> units.
    """
    return {int(value): float(units) for value, units in (pair.split(":") for pair in text.split(",") if pair)}


class LifetimeStore:
    """The lifetimes' state and ledger; needs `self.conn`. Writes run inside the caller's transaction."""

    conn: sqlite3.Connection

    def load_life_state(self, market: str, src: str) -> LifeState | None:
        """The carried state of one market and source.

        Args:
            market: Market id.
            src: Source name.

        Returns:
            The state, or None when none is stored (a history the batch pass wrote, or a new one) or its rows don't
            add up to the counts stored with it (damaged, FM86).
        """
        row = self.conn.execute("SELECT prev_at, auctions, tally_rows FROM lifetime_state WHERE market = ? AND src = ?",
                                (market, src)).fetchone()
        if row is None:
            return None
        live = {auction: (key, qty, (start, end), price, bool(commodity), None if tier1 is None else bool(tier1),
                          None if posted_start is None else (posted_start, posted_end))
                for auction, key, qty, start, end, price, commodity, tier1, posted_start, posted_end in self.conn.execute(
                    "SELECT auction, key, qty, win_start, win_end, price, commodity, tier1, posted_start, posted_end "
                    "FROM open_auctions WHERE market = ? AND src = ? ORDER BY auction", (market, src))}
        tallies: dict[int, dict[int, tuple[int, int, bool]]] = {}
        for gap_end, key, exposed, leaves, commodity in self.conn.execute(
                "SELECT gap_end, key, exposed_ms, leaves, commodity FROM leave_tallies WHERE market = ? AND src = ? "
                "ORDER BY gap_end, key", (market, src)):
            tallies.setdefault(gap_end, {})[key] = (exposed, leaves, bool(commodity))
        if (len(live), sum(map(len, tallies.values()))) != (row[1], row[2]):
            return None
        return LifeState(row[0], live, _window(tallies))

    def save_life_state(self, market: str, src: str, old: LifeState | None, new: LifeState) -> None:
        """Store a state, writing only what changed since the stored one.

        Args:
            market: Market id.
            src: Source name.
            old: The state as stored (from `load_life_state`), or None to replace whatever is stored whole.
            new: The state to store.
        """
        if old is None:
            self.conn.execute("DELETE FROM open_auctions WHERE market = ? AND src = ?", (market, src))
            self.conn.execute("DELETE FROM leave_tallies WHERE market = ? AND src = ?", (market, src))
        stored = old if old is not None else LifeState()
        self.conn.execute(
            "INSERT INTO lifetime_state(market, src, prev_at, auctions, tally_rows) VALUES (?, ?, ?, ?, ?) ON CONFLICT"
            "(market, src) DO UPDATE SET prev_at = excluded.prev_at, auctions = excluded.auctions, tally_rows = "
            "excluded.tally_rows", (market, src, new.prev_at, len(new.live), sum(len(r) for _, r in new.leaves.tallies)))
        self.conn.executemany("DELETE FROM open_auctions WHERE market = ? AND src = ? AND auction = ?",
                              [(market, src, a) for a in sorted(set(stored.live) - set(new.live))])
        self.conn.executemany(
            "INSERT OR REPLACE INTO open_auctions(market, src, auction, key, qty, win_start, win_end, price, commodity, "
            "tier1, posted_start, posted_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(market, src, a, *_auction_row(s)) for a, s in sorted(new.live.items()) if stored.live.get(a) != s])
        kept = {at for at, _ in new.leaves.tallies}
        self.conn.executemany("DELETE FROM leave_tallies WHERE market = ? AND src = ? AND gap_end = ?",
                              [(market, src, at) for at, _ in stored.leaves.tallies if at not in kept])
        held = {at for at, _ in stored.leaves.tallies}
        self.conn.executemany(
            "INSERT INTO leave_tallies(market, src, gap_end, key, exposed_ms, leaves, commodity) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(market, src, at, key, exposed, leaves, int(commodity)) for at, rows in new.leaves.tallies if at not in held
             for key, (exposed, leaves, commodity) in sorted(rows.items())])

    def clear_life_state(self, market: str, src: str) -> None:
        """Forget the carried state of one market and source (a replay found no snapshot to rebuild it from).

        Args:
            market: Market id.
            src: Source name.
        """
        for table in ("lifetime_state", "open_auctions", "leave_tallies"):
            self.conn.execute(f"DELETE FROM {table} WHERE market = ? AND src = ?", (market, src))

    def newest_scan_at(self, market: str, src: str) -> int | None:
        """The newest stored book of one market and source: the history's newest snapshot (FM86's check).

        Args:
            market: Market id.
            src: Source name.

        Returns:
            Its time, UTC ms, or None when there is none.
        """
        return self.conn.execute("SELECT MAX(at) FROM scans WHERE market = ? AND src = ?", (market, src)).fetchone()[0]

    def write_gaps(self, market: str, src: str, summaries: Iterable[GapSummary]) -> None:
        """Add gaps to the ledger, replacing any stored under the same end.

        Args:
            market: Market id.
            src: Source name.
            summaries: The gaps.
        """
        for s in summaries:
            end = s.gap[1]
            self.conn.execute("DELETE FROM gap_flows WHERE market = ? AND src = ? AND gap_end = ?", (market, src, end))
            self.conn.execute("INSERT OR REPLACE INTO gap_seen(market, src, gap_end, gap_start, keys) VALUES (?, ?, ?, ?, ?)",
                              (market, src, end, s.gap[0], ",".join(map(str, sorted(s.flows)))))
            self.conn.executemany(
                f"INSERT INTO gap_flows(market, src, gap_end, key, {', '.join(FLOW_FIELDS)}, commodity, unseen, positions, "
                f"prices) VALUES ({', '.join('?' * (len(FLOW_FIELDS) + 8))})",
                [(market, src, end, key, *(getattr(f, n) for n in FLOW_FIELDS), int(f.commodity), f.unseen,
                  encode_units(s.positions.get(key, {})), encode_units(s.prices.get(key, {})))
                 for key, f in sorted(s.flows.items()) if f != LifeFlow() or key in s.positions or key in s.prices])

    def delete_gaps_from(self, market: str, src: str, from_end_ms: int) -> list[tuple[int, int]]:
        """Remove the ledger's gaps ending at or after a time (a replay rewrites them).

        Args:
            market: Market id.
            src: Source name.
            from_end_ms: The earliest gap end removed, UTC ms.

        Returns:
            The gaps removed, (start, end) ascending.
        """
        gone = [(start, end) for end, start in self.conn.execute(
            "SELECT gap_end, gap_start FROM gap_seen WHERE market = ? AND src = ? AND gap_end >= ? ORDER BY gap_end",
            (market, src, from_end_ms))]
        for table in ("gap_flows", "gap_seen"):
            self.conn.execute(f"DELETE FROM {table} WHERE market = ? AND src = ? AND gap_end >= ?", (market, src, from_end_ms))
        return gone

    def read_gaps(self, market: str, src: str, from_end_ms: int, to_end_ms: int, units: bool = True) -> list[GapSummary]:
        """The ledger's gaps ending in a time range.

        Args:
            market: Market id.
            src: Source name.
            from_end_ms: The earliest gap end, inclusive, UTC ms.
            to_end_ms: The latest gap end, inclusive, UTC ms.
            units: Also read the positions and prices (False leaves them empty: the hours need only the flows).

        Returns:
            The gaps by end, each with a flow for every seen item (zeros where the ledger has no row).
        """
        out: dict[int, GapSummary] = {}
        for end, start, keys in self.conn.execute(
                "SELECT gap_end, gap_start, keys FROM gap_seen WHERE market = ? AND src = ? AND gap_end BETWEEN ? AND ? "
                "ORDER BY gap_end", (market, src, from_end_ms, to_end_ms)):
            out[end] = GapSummary((start, end), {int(k): LifeFlow() for k in keys.split(",") if k}, {}, {})
        for end, key, *values, commodity, unseen, positions, prices in self.conn.execute(
                f"SELECT gap_end, key, {', '.join(FLOW_FIELDS)}, commodity, unseen, positions, prices FROM gap_flows "
                "WHERE market = ? AND src = ? AND gap_end BETWEEN ? AND ? ORDER BY gap_end, key",
                (market, src, from_end_ms, to_end_ms)):
            summary = out[end]
            summary.flows[key] = LifeFlow(*values, commodity=bool(commodity), unseen=unseen)
            if units and positions:
                summary.positions[key] = decode_units(positions)
            if units and prices:
                summary.prices[key] = decode_units(prices)
        return [out[end] for end in sorted(out)]


def _auction_row(state: tuple) -> tuple:
    """One live auction as `open_auctions` columns.

    Args:
        state: (key, units, window, price, commodity, tier1, posting gap), as `LifeState.live`.

    Returns:
        (key, qty, win_start, win_end, price, commodity 0/1, tier1 0/1/None, posted_start, posted_end).
    """
    key, units, window, price, commodity, tier1, posted = state
    return (key, units, window[0], window[1], price, int(commodity), None if tier1 is None else int(tier1),
            *(posted if posted is not None else (None, None)))


def _window(tallies: dict[int, dict[int, tuple[int, int, bool]]]) -> LeaveWindow:
    """A leave window rebuilt from stored tallies, its sums added up again (integers: exact).

    Args:
        tallies: Gap end ms -> key -> (exposure ms, leaves, commodity flag).

    Returns:
        The window, its tallies in time order.
    """
    window = LeaveWindow()
    for at in sorted(tallies):          # all ended within a day of the newest, so adding them in order drops none
        window = window.advance(at, tallies[at])
    return window
