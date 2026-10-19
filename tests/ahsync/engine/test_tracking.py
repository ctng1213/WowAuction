"""Tests for ahsync/engine/tracking.py: events matched to actions, posts resolved by mail (SPEC §3.4.1)."""
from __future__ import annotations

from ahsync.engine.tracking import AuctionMail, DoneEvent, OpenAction, OpenPost, match, resolve_posts

HOUR, DAY = 3_600_000, 86_400_000
T = 1_760_000_000_000
LINEN, ROD = 2589, 6217
AMAYA = "Amaya-G"


def buy(action_id: str = "b", qty: int = 20, price: int = 45, slot: int = T, done: int = 0) -> OpenAction:
    """A pending buy of Linen at or below a price."""
    return OpenAction(action_id, AMAYA, "buy", LINEN, qty, done, price, slot, None)


def bought(event_id: str = "e", qty: int = 20, price: int = 40, at: int = T + HOUR, key: int = LINEN) -> DoneEvent:
    """A confirmed purchase."""
    return DoneEvent(event_id, AMAYA, "buy", key, qty, price, at)


def test_T1_a_buy_of_the_full_quantity_at_or_below_the_price_marks_the_action_done():
    matches, status = match([bought()], [buy()])
    assert [(m.event_id, m.action_id, m.qty) for m in matches] == [("e", "b", 20)] and status == {"b": (20, "done")}


def test_T2_a_buy_of_part_of_it_marks_it_partial_and_the_rest_completes_it():
    matches, status = match([bought(qty=10)], [buy()])
    assert status == {"b": (10, "partial")}
    _, status = match([bought("e2", qty=15, at=T + 2 * HOUR)], [buy(done=10)])
    assert status == {"b": (20, "done")}                         # only the 10 still to do are counted


def test_T3_a_buy_above_the_planned_price_matches_nothing():
    assert match([bought(price=50)], [buy()]) == ([], {})
    assert match([bought(price=None)], [buy()]) == ([], {})


def test_events_match_the_earliest_open_action_within_24_hours_of_its_slot_and_only_their_own():
    early, late = buy("a", slot=T), buy("z", slot=T + 2 * HOUR)
    matches, _ = match([bought(at=T + HOUR)], [late, early])
    assert matches[0].action_id == "a"
    assert match([bought(at=T + 25 * HOUR)], [buy()]) == ([], {})                 # 25 h after the slot
    assert match([bought(key=ROD)], [buy()]) == ([], {})                          # another item
    other = DoneEvent("o", "Brugan-G", "buy", LINEN, 20, 40, T)
    assert match([other], [buy()]) == ([], {})                                    # another character
    assert match([bought()], [buy(done=20)]) == ([], {})                          # nothing left to do
    craft = OpenAction("c", AMAYA, "craft", ROD, 3, 0, None, T, None)
    assert match([DoneEvent("k", AMAYA, "craft", ROD, 1, None, T + HOUR)], [craft])[1] == {"c": (1, "partial")}


def test_T16_a_post_matches_from_a_day_before_its_post_time_to_its_windows_end_at_any_price():
    post = OpenAction("p", AMAYA, "post", LINEN, 20, 0, 55, T, T + 11 * HOUR)
    posted = DoneEvent("q", AMAYA, "post", LINEN, 20, 70, T + 9 * HOUR + 30 * 60_000)   # late, at another price
    assert match([posted], [post])[1] == {"p": (20, "done")}
    assert match([DoneEvent("r", AMAYA, "post", LINEN, 20, 55, T + 12 * HOUR)], [post]) == ([], {})   # after the window
    assert match([DoneEvent("s", AMAYA, "post", LINEN, 20, 55, T - 25 * HOUR)], [post]) == ([], {})   # too early
    now_post = OpenAction("n", AMAYA, "post", LINEN, 20, 0, 55, T, None)          # posting now: 24 h to match
    assert match([DoneEvent("t", AMAYA, "post", LINEN, 20, 55, T + 23 * HOUR)], [now_post])[1] == {"n": (20, "done")}


def post(post_id: str, at: int, price: int = 48, stack: int = 20) -> OpenPost:
    """An open post of Linen."""
    return OpenPost(post_id, AMAYA, LINEN, stack, price, at)


def mail(outcome: str, price: int | None = None, qty: int = 20, at: int = T + 3 * DAY, event_id: str = "m") -> AuctionMail:
    """An auction mail for Linen."""
    return AuctionMail(event_id, AMAYA, LINEN, qty, price, outcome, at)


def test_T4_an_expired_mail_resolves_the_oldest_open_post_of_that_stack_and_only_one():
    out = resolve_posts([post("tue", T + DAY), post("mon", T)], [mail("expired")])
    assert [(r.post_id, r.outcome, r.mail_id) for r in out] == [("mon", "expired", "m")]
    assert resolve_posts([post("mon", T, stack=5)], [mail("cancelled")]) == []    # another stack
    assert resolve_posts([post("later", T + 4 * DAY)], [mail("expired")]) == []   # posted after the mail


def test_T5_a_sold_mail_resolves_the_oldest_open_post_at_its_price():
    out = resolve_posts([post("mon", T, price=50), post("tue", T + DAY, price=48)], [mail("sold", price=48)])
    assert [(r.post_id, r.outcome) for r in out] == [("tue", "sold")]


def test_won_mail_resolves_no_post_and_each_mail_resolves_one():
    assert resolve_posts([post("mon", T)], [mail("won", price=48)]) == []
    out = resolve_posts([post("mon", T), post("tue", T + DAY)],
                        [mail("expired", event_id="a"), mail("expired", event_id="b", at=T + 4 * DAY)])
    assert [(r.mail_id, r.post_id) for r in out] == [("a", "mon"), ("b", "tue")]
