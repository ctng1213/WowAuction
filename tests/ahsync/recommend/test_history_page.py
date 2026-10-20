"""Tests for ahsync/recommend/history_page.py: the History payload (SPEC §3.5, D47)."""
from __future__ import annotations

from ahsync.history.reads import ItemInfo
from ahsync.recommend.history_page import history_page

NOW = 1_760_000_000_000
CINCH, DUST = 4239, 10940
ITEMS = {CINCH: ItemInfo(CINCH, "Bandit Cinch", 2, 20, 4, None, 100, 1), DUST: ItemInfo(DUST, "Strange Dust", 1, 20, 7, None, 20, 20)}


class FakeJournal:
    """One disenchant within the last 30 days."""

    def __init__(self) -> None:
        """No call yet."""
        self.since = None

    def journal(self, char_ids, since_ms):
        """The disenchant; remembers the window asked for."""
        self.since = since_ms
        return [(NOW - 3_600_000, "Amaya-G", "disenchanted", CINCH, 1, None, None, f"{DUST},2", None, 150.0, None)]

    def position_seeds(self):
        """No positions."""
        return []


def test_the_page_reads_30_days_and_names_the_disenchant_results():
    db = FakeJournal()
    page = history_page(db, ["Amaya-G"], {}, ITEMS, NOW, 0)
    assert db.since == NOW - 30 * 86_400_000
    [row] = page["rows"]
    assert (row["item"], row["detail"], row["realized_note"]) == ("Bandit Cinch", "gave 2 Strange Dust", "mats sold separately")
    assert page["open"] == "nothing open" and page["help"]["Realized"].startswith("What it actually made")
    assert page["tiles"][0] == ["Sold (30 d)", "0 auctions", "0c after the cut"]
