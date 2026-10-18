"""The hourly status pass: late and missed actions, and missed-peak replacements (SPEC §3.1.1, §3.4.0, §3.4.1).

It reads only stored actions, the published intelligence and character state, never
history, and runs no simulation beyond the few candidate windows of a missed post
(IU10). A missed post's replacement carries the original anchor, so a later miss is
still compared with the first peak (M3); it is linked by replaces_id.
"""
from __future__ import annotations

from ahsync.config import Config, plan_settings
from ahsync.db.sqlite import Database
from ahsync.engine.missed import replan_missed, status_now
from ahsync.recommend.plan import de_offer, sell_facts

DAY_MS = 86_400_000


def _replacement(row: tuple, db: Database, market: str, now_ms: int, cfg: Config, enchanters: list) -> tuple | None:
    """The replacement action row for a missed post, or None when it can't be re-planned.

    Args:
        row: The missed post's open-action row.
        db: Database.
        market: Its market.
        now_ms: Now.
        cfg: Config.
        enchanters: (character id, Enchanting skill) on the market, best first.

    Returns:
        An `actions` row (a pending post or hold), or None without a window or intelligence.
    """
    action_id, kind, char_id, key, qty, slot_ms, window_end, anchor, anchor_window, week_start, _ = row
    intel = db.read_intel(market)
    if window_end is None or anchor is None or intel is None or key not in intel.items:
        return None
    items = db.items()
    f = sell_facts(intel.items[key], items.get(key), de_offer(items.get(key), intel.items, enchanters))
    r = replan_missed(anchor, window_end, qty, f, now_ms, week_start + 7 * DAY_MS, plan_settings(cfg),
                      cfg.min_sell_prob, cfg.peak_tolerance)
    start, end, _best = r.window if r.window else (None, None, None)
    return (f"{action_id}:r", now_ms, r.at_ms if r.at_ms is not None else now_ms, char_id, r.kind, key, qty, r.price,
            r.net, None, "pending", 0, None, None, start, end, anchor, anchor_window, action_id, week_start)


def status_pass(db: Database, cfg: Config, now_ms: int) -> dict[str, int]:
    """Bring every open action's status up to date (SPEC §3.4.1), re-planning missed posts (§3.4.0).

    Args:
        db: Database.
        cfg: Config.
        now_ms: Now.

    Returns:
        Counts: {"late": n, "missed": n, "replaced": n}.
    """
    counts = {"late": 0, "missed": 0, "replaced": 0}
    for market in cfg.markets:
        char_ids = [c for c, _ in db.characters_in(market)]
        enchanters = sorted(((c, db.skill_of(c, "Enchanting")) for c in char_ids), key=lambda e: (-(e[1] or -1), e[0]))
        enchanters = [(c, s) for c, s in enchanters if s is not None]
        with db.transaction():
            for row in db.open_actions(char_ids):
                action_id, kind, _, _, _, slot_ms, window_end, *_rest, status = row
                new = status_now(kind, slot_ms, window_end, now_ms)
                if new == status:
                    continue
                db.set_action_status(action_id, new)
                counts[new] = counts.get(new, 0) + 1
                if new == "missed" and kind == "post":
                    replacement = _replacement(row, db, market, now_ms, cfg, enchanters)
                    if replacement is not None:
                        db.add_action(replacement)
                        counts["replaced"] += 1
    return counts
