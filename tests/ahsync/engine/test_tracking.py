"""Tests for ahsync/engine/tracking.py: events matched to actions, posts resolved by mail (SPEC §3.4.1)."""
from __future__ import annotations

from ahsync.engine.tracking import (OPEN_CHAIN, OPEN_POST, AuctionMail, ChainFacts, DoneEvent, Judged, OpenAction,
                                    OpenPost, Payout, PostResult, ProfitSums, SoldMail, judge, match, post_value,
                                    profit_sums, realize, resolve_posts)

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


def test_T21_mail_to_the_planned_recipient_marks_the_mail_done_or_partial_and_mail_to_anyone_else_nothing():
    brugan, bar = "Brugan-G", 2840
    action = OpenAction("m", brugan, "mail", bar, 6, 0, None, T, None, to_char=AMAYA)

    def sent(qty: int, to: str = AMAYA, at: int = T + 20 * HOUR) -> DoneEvent:
        """A mail_sent event from Brugan."""
        return DoneEvent("s", brugan, "mail", bar, qty, None, at, to)
    assert match([sent(6)], [action])[1] == {"m": (6, "done")}
    assert match([sent(4)], [action])[1] == {"m": (4, "partial")}
    assert match([sent(6, to="Cara-G")], [action]) == ([], {})                  # another recipient
    assert match([sent(6, at=T + 25 * HOUR)], [action]) == ([], {})              # more than 24 h after its slot


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


def test_T17_a_payout_realizes_its_profit_once_from_the_sold_mail_it_pays():
    sold = [SoldMail("sold", AMAYA, ROD, 1, T + DAY, 20.0), SoldMail("sold2", AMAYA, ROD, 1, T + 2 * DAY, 20.0)]
    assert realize([], sold, {ROD: 400.0}) == []                       # sold, not paid out: nothing realized
    paid = realize([Payout("pay", AMAYA, ROD, 1, 970, T + 3 * DAY)], sold, {ROD: 400.0})
    # 9s 70c taken − 4s cost − its 20c deposit = 5s 50c; it pays the oldest sold mail
    assert [(r.payout_id, r.mail_id, r.profit) for r in paid] == [("pay", "sold", 550.0)]


def test_T17_a_payout_with_its_invoices_deposit_uses_it_even_without_the_post():
    sold = [SoldMail("sold", AMAYA, ROD, 1, T, 0.0)]                         # its post was never captured
    paid = realize([Payout("pay", AMAYA, ROD, 1, 195, T + HOUR, 100)], sold, {})
    assert [(r.mail_id, r.profit) for r in paid] == [("sold", 95.0)]         # 1s − 5c cut; the 1s deposit came back


def test_T17_a_payout_without_its_sold_mail_realizes_without_a_deposit():
    other = [SoldMail("five", AMAYA, ROD, 5, T, 20.0), SoldMail("brugan", "Brugan-G", ROD, 1, T, 20.0)]
    paid = realize([Payout("pay", AMAYA, ROD, 1, 970, T + HOUR), Payout("loot", AMAYA, LINEN, 2, 90, T)], other, {ROD: 400.0})
    assert [(r.payout_id, r.mail_id, r.profit) for r in paid] == [("loot", None, 90.0), ("pay", None, 570.0)]


# ---- Expected against realized (D68-D70) ----

def test_D68_a_post_is_judged_by_its_payout_its_lost_deposit_or_not_at_all_while_open():
    paid = judge("posted", 447.0, PostResult("sold", 410.0, 3))
    assert paid == Judged(447.0, 410.0, "", None, None)
    assert judge("posted", 120.0, PostResult("expired", None, 3)) == Judged(120.0, -3.0, "expired")
    assert judge("posted", 250.0, PostResult(None, None, 5)) == Judged(250.0, None, "not sold yet", OPEN_POST)
    assert judge("posted", 250.0, PostResult("sold", None, 5)).note == "sold · not paid out yet"   # gold still in the mailbox
    assert judge("posted", None, PostResult("cancelled", None, 5)) == Judged(None, -5.0, "cancelled")
    assert post_value(PostResult("expired", None, None)) == 0.0                                    # deposit not captured
    assert judge("disenchanted", 150.0, None) == Judged(150.0, None, "mats sold separately")
    assert judge("crafted", None, None) == Judged(None, None, "")                                # no chain: no numbers
    assert [judge(k, None, None) for k in ("bought", "bought_vendor", "paid_out", "mailed")] == [None] * 4



def test_T24_a_cancelled_post_loses_its_deposit_as_an_expired_one_does():
    cancelled = judge("posted", 250.0, PostResult("cancelled", None, 5))
    assert cancelled == Judged(250.0, -5.0, "cancelled")
    assert post_value(PostResult("cancelled", None, None)) == 0.0                                  # deposit not captured
    assert post_value(PostResult("cancelled", None, 5)) == post_value(PostResult("expired", None, 5))


def test_D69_a_chain_carrier_realizes_what_its_posts_made_so_far():
    posts = (PostResult("sold", 300.0, 2, "c"), PostResult("sold", 350.0, 2, "c"), PostResult(None, None, 2, "c"))
    craft = judge("crafted", 971.0, ChainFacts("c", posts))
    assert craft == Judged(971.0, 650.0, "2 of 3 posts paid", OPEN_CHAIN, "c", True)
    done = judge("crafted", 971.0, ChainFacts("c", posts[:2] + (PostResult("sold", 321.0, 2, "c"),)))
    assert (done.realized, done.open, done.note) == (971.0, None, "3 of 3 posts paid")
    waiting = judge("crafted", 971.0, ChainFacts("c", posts[:2], open_posts=1))           # a post action still to come
    assert (waiting.realized, waiting.open) == (650.0, OPEN_CHAIN)
    expired = judge("bought", 400.0, ChainFacts("f", (PostResult("expired", None, 4, "f"),)))
    assert (expired.realized, expired.note, expired.open) == (-4.0, "0 of 1 posts paid", None)
    assert judge("bought", 210.0, ChainFacts("f", ())) == Judged(210.0, None, "nothing sold yet", OPEN_CHAIN, "f", True)
    assert judge("bought", 500.0, ChainFacts("d", (), disenchants=True)) == Judged(500.0, None, "mats sold separately",
                                                                                  None, "d", True)
    half = judge("bought", 105.0, ChainFacts("f", (PostResult("sold", 300.0, 0, "f"),), share=0.5))
    assert half.realized == 150.0                                                          # one of two buys: half of it


def test_D70_the_sums_count_resolved_plan_events_once():
    rows = [Judged(447.0, 410.0, ""), Judged(120.0, -3.0, "expired"), Judged(250.0, None, "not sold yet", OPEN_POST),
            Judged(None, 80.0, ""), Judged(150.0, None, "mats sold separately"),
            Judged(971.0, 650.0, "2 of 3 posts paid", OPEN_CHAIN, "c", True),
            Judged(300.0, 300.0, "", None, "c"), Judged(250.0, None, "not sold yet", OPEN_POST, "c"),   # its follow-ups
            Judged(210.0, None, "nothing sold yet", OPEN_CHAIN, "f", True),
            Judged(90.0, 95.0, "", None, "gone")]                    # a follow-up whose carrier is not among the rows
    assert profit_sums(rows) == ProfitSums(657.0, 502.0, 3, 1, 2)
    assert profit_sums([]) == ProfitSums(0.0, 0.0, 0, 0, 0)


def test_T25_a_commodity_post_resolves_by_units_whatever_order_its_mails_arrive_in():
    mon, tue = post("big", T, price=139, stack=110), post("small", T + DAY, price=139, stack=20)
    mails = [mail("sold", price=139, qty=30, at=T + DAY + HOUR, event_id="s30"),
             mail("expired", qty=70, at=T + 2 * DAY + 8 * HOUR, event_id="x70"),
             mail("sold", price=139, qty=10, at=T + 2 * DAY + 8 * HOUR + 40 * 60_000, event_id="s10")]
    out = resolve_posts([mon, tue], mails)
    assert [(r.mail_id, r.post_id, r.outcome, r.sold_units, r.returned_units) for r in out] == [
        ("s30", "big", None, 30, 0), ("x70", "big", None, 30, 70), ("s10", "big", "expired", 40, 70)]
    assert out[-1].at_ms == T + 2 * DAY + 8 * HOUR + 40 * 60_000                 # resolved by its last mail
    # a return goes to the post with exactly its units left before the oldest with enough
    out = resolve_posts([mon, tue], [mail("expired", qty=20, at=T + 2 * DAY, event_id="x20")])
    assert [(r.post_id, r.outcome) for r in out] == [("small", "expired")]
    # all sold in parts: sold; a sale too big for what is left resolves nothing
    out = resolve_posts([tue], [mail("sold", price=139, qty=12, event_id="a"), mail("sold", price=139, qty=8, event_id="b",
                                                                                   at=T + 4 * DAY)])
    assert [(r.post_id, r.outcome, r.sold_units) for r in out] == [("small", None, 12), ("small", "sold", 20)]
    assert resolve_posts([tue], [mail("sold", price=139, qty=21)]) == []
    # a post already partly resolved carries its units in
    half = OpenPost("half", AMAYA, LINEN, 20, 48, T, sold_units=5, returned_units=0)
    assert [(r.outcome, r.sold_units, r.returned_units) for r in resolve_posts([half], [mail("cancelled", qty=15)])] == [
        ("cancelled", 5, 15)]
    back = OpenPost("back", AMAYA, LINEN, 20, 48, T, sold_units=0, returned_units=15, returned_as="cancelled")
    assert [r.outcome for r in resolve_posts([back], [mail("sold", price=48, qty=5)])] == ["cancelled"]   # an earlier pass


def test_T26_a_partly_sold_post_realizes_its_payouts_less_the_deposit_share_of_what_came_back():
    from ahsync.engine.tracking import PostResult, judge, post_value
    paid = PostResult("expired", 3_500.0 + 1_170.0, 814, None, 110, 70, 0)           # both sold mails paid
    assert post_value(paid) == 4_670.0 - 518.0                                       # 814 × 70 ÷ 110 lost
    waiting = PostResult("expired", 3_500.0, 814, None, 110, 70, 1)                  # one sale still in the mailbox
    assert post_value(waiting) is None
    assert judge("posted", 4_000.0, waiting).note == "sold · not paid out yet"
    assert post_value(PostResult("expired", None, 814, None, 110, 110, 0)) == -814.0     # none sold: all of it
    assert post_value(PostResult("expired", None, 20, None)) == -20.0                    # before units were counted



def test_T25_an_expiry_goes_to_the_post_that_ran_out_and_one_purchases_mails_fill_the_older_post_first():
    # M2: A 110 (Mon, 24 h) has sold 30 and B 70 (Tue, 24 h) none; A's 70 come back Tuesday as A runs out
    a = OpenPost("A", AMAYA, LINEN, 110, 139, T, sold_units=30, duration_h=24)
    b = OpenPost("B", AMAYA, LINEN, 70, 150, T + DAY, duration_h=24)
    out = resolve_posts([a, b], [mail("expired", qty=70, at=T + DAY + 600_000, event_id="x")])
    assert [(r.post_id, r.returned_units) for r in out] == [("A", 70)]               # not B, though B has exactly 70
    # M1: one purchase of 18 takes A's last 10 and 8 of B's: both mails at the same time
    a, b = post("A", T, price=139, stack=10), post("B", T + HOUR, price=139, stack=30)
    same = T + 2 * DAY
    out = resolve_posts([a, b], [mail("sold", price=139, qty=8, at=same, event_id="a8"),
                                 mail("sold", price=139, qty=10, at=same, event_id="b10"),
                                 mail("expired", qty=22, at=same + DAY, event_id="x22")])
    assert [(r.mail_id, r.post_id, r.outcome) for r in out] == [("b10", "A", "sold"), ("a8", "B", None), ("x22", "B", "expired")]
