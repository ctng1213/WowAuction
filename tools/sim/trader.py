"""Our characters inside the simulated market: they follow AHSync's plan through the real addon import (SPEC §8.10).

A `Trader` holds synthetic characters (gold, bags, a mailbox) that buy and post in the `Market` like any seller,
so their posts appear in later snapshots and competitors react. Everything they do is captured twice: as the
addon would write it (SavedVariables events, bags, own auctions, gold; `tests/support/savedvars_builder.py`) and in
the `Ledger` (`scorers.py`), whose identity is checked after every event (SU12).

Each simulated day the characters log in at LOGIN_HOUR_UTC, an hour after the cloud's daily run: they collect the
mail that has arrived, then AHSync runs as in production (plan §1.5): the SavedVariables file is parsed and
applied (`state/importer.apply_data`), the tracking pass matches events to actions and rebuilds the cost basis,
and `replan_all` plans with the latest document. The characters then carry out the plan's pending actions: buys
and vendor sales now, posts now or at their window's start, mail now, crafts now with the recipes their spec
knows (MS4b-2: the reagents' cost goes with the product; a fixed yield). Disenchants and holds are counted, not
carried out (SD24 needs disenchants).

Mail as in the game: a sale's gold arrives SALE_MAIL_DELAY_MS after the sale, an expired or cancelled auction's
items at once. Postage is not modelled.
"""
from __future__ import annotations

import heapq
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ahsync.app.refresh import replan_all
from ahsync.config import CloudConfig, Config, MarketSources
from ahsync.db.sqlite import Database
from ahsync.engine.fees import AH_CUT, deposit_copper
from ahsync.export.addon_files import vendor_prices
from ahsync.recommend.plan import flip_offers
from ahsync.recommend.tracking_pass import tracking_pass
from ahsync.state.importer import apply_data
from ahsync.state.savedvars import read_ahprofit
from sim.market import DAY_MS, HOUR_MS, Live, Market, World
from sim.scorers import Ledger

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support.savedvars_builder import savedvars_text  # noqa: E402

REALM = "Simulated"                    # our characters' realm; its ruleset is the world market's
LOGIN_HOUR_UTC = 10                    # an hour after the cloud's daily run (DAILY_HOUR_UTC 9)
SALE_MAIL_DELAY_MS = HOUR_MS           # a sale's gold reaches the mailbox an hour later
DUE_SLACK_MS = HOUR_MS                 # a buy whose slot is within this of now is done now
OUR_PREFIX = "we:"                     # our auctions' seller names on the market
CLIENT_BANDS = ((30 * 60_000, 1), (2 * HOUR_MS, 2), (8 * HOUR_MS, 3))   # the game client's time-left bands
UNDONE_KINDS = ("de", "hold")


@dataclass(frozen=True)
class Recipe:
    """A recipe a character knows, as the addon writes its definition (SPEC §2.2.1 recipes)."""

    spell: int
    profession: str
    product: int
    reagents: tuple[tuple[int, int], ...]   # (key, units) a craft uses
    yield_min: int = 1
    yield_max: int = 1


@dataclass(frozen=True)
class CharacterSpec:
    """One of our characters at the start."""

    name: str
    gold: int                          # copper
    bags: tuple[tuple[int, int], ...] = ()   # (key, units) held at the start, found stock at cost 0
    faction: str = "Horde"
    cls: str = "MAGE"
    recipes: tuple[Recipe, ...] = ()   # what it can craft, its professions at skill 300 (MS4b-2)

    @property
    def char_id(self) -> str:
        """The addon's character id.

        Returns:
            "Name-Realm".
        """
        return f"{self.name}-{REALM}"


@dataclass(frozen=True)
class Login:
    """What AHSync showed at one login, after its re-plan: what the decision rows read."""

    at_ms: int
    actions: tuple[dict, ...]          # the pending and late actions, as `Session.pending` reads them
    offers: dict[int, str]             # the market file's flip offers (EX11): item key -> its offer
    prices: dict[int, int] = field(default_factory=dict)   # the planned document's cheapest price per item, copper


@dataclass(order=True)
class Mail:
    """A letter on its way to a character's mailbox."""

    deliver_ms: int
    char: str = field(compare=False)
    outcome: str = field(compare=False)      # "sold", "expired", "cancelled", or "mail" from one of our characters
    key: int = field(compare=False)
    units: int = field(compare=False)
    unit_price: int | None = field(compare=False, default=None)   # sold only
    money: int = field(compare=False, default=0)                   # sold only: the gold in it, copper
    deposit: int = field(compare=False, default=0)                 # sold only: the deposit returned in it


def market_config(world: World) -> Config:
    """AHSync's config for the world's market as a cloud market, our realm on its ruleset.

    Args:
        world: The scenario.

    Returns:
        The Config.
    """
    ruleset = world.market.split(".")[1]
    return Config(markets=(world.market,), cloud=CloudConfig("https://sim.invalid"),
                  sources={world.market: MarketSources("blizzard", ("blizzard",), {})}, realms={REALM: ruleset})


class Trader:
    """Our characters, their ledger and the addon's view of them, acting on one market."""

    def __init__(self, world: World, characters: tuple[CharacterSpec, ...], folder: Path | None = None,
                 login_hours: tuple[int, ...] = (LOGIN_HOUR_UTC,), held_prices: dict[int, int] | None = None,
                 first_login_ms: int | None = None) -> None:
        """Characters at the world's start; with a folder, AHSync's database there and logins every day.

        Args:
            world: The scenario.
            characters: Our characters.
            folder: An empty folder for AHSync's database; None for a trader driven by hand (SU10, SU11).
            login_hours: UTC hours the characters log in each day.
            held_prices: Item key -> the price every post of it uses, whatever the plan says (a benchmark that
                holds to its target, SD28); None follows the plan.
            first_login_ms: No login before this time: AHSync installed on a market the cloud has watched for a
                while (SD5); None logs in from the world's start.
        """
        self.login_hours = login_hours
        self.first_login_ms = first_login_ms
        self.held_prices = held_prices or {}
        self.world, self.items = world, {item.key: item for item in world.items}
        self.specs = {c.char_id: c for c in characters}
        self.ledger = Ledger(0)
        for spec in characters:
            self.ledger.fund(spec.char_id, spec.gold)
            for key, units in spec.bags:
                self.ledger.buy(spec.char_id, key, units, 0)
        self.auctions: dict[int, tuple[str, int]] = {}         # our live auction id -> (char, key)
        self.mails: list[Mail] = []                            # heap by delivery time
        self.events: list[dict] = []                           # SavedVariables events not yet imported
        self.counts: dict[str, int] = {}                       # what was done or left undone, by kind
        self._truth_seen = 0
        self._ids: dict[tuple[str, int], int] = {}             # (char, second) -> events so far, for ids
        self._wakes: list[tuple[int, int, str, str]] = []       # (at, seq, kind, ref)
        self._seq = 0
        self.samples: list[tuple[int, int, int]] = []        # (at, copper tied up, realized copper) per wake
        self.truth = None                                      # the run's truth log, once a decision row ran it
        self.session = Session(self, folder) if folder is not None else None
        if self.session is not None:
            self.schedule_logins()

    def schedule_logins(self) -> None:
        """Queue a login at each of login_hours on every day of the world, after its start and first_login_ms."""
        midnight = self.world.start_ms // DAY_MS * DAY_MS
        for day in range(self.world.days + 1):
            for hour in sorted(self.login_hours):
                at = midnight + day * DAY_MS + hour * HOUR_MS
                if at > self.world.start_ms and (self.first_login_ms is None or at >= self.first_login_ms):
                    self._wake(at, "login", "")

    # ------------------------------------------------------------- the runner's hook

    def close(self) -> None:
        """Close AHSync's database, if this trader has one (an open SQLite file can't be deleted on Windows)."""
        if self.session is not None:
            self.session.db.close()

    def next_wake(self) -> int | None:
        """When the trader next wants to act.

        Returns:
            UTC ms, or None.
        """
        return self._wakes[0][0] if self._wakes else None

    def act(self, market: Market, documents: list, now_ms: int) -> None:
        """Do what is due at a wake: settle what the market did to our auctions, then log in or post.

        Args:
            market: The market, run up to now_ms.
            documents: The run's daily documents so far.
            now_ms: The wake's time.
        """
        _, _, kind, ref = heapq.heappop(self._wakes)
        self.observe(market)
        if kind == "login":
            self.login(market, documents, now_ms)
        elif kind == "post" and self.session is not None:
            self.session.post_due(market, ref, now_ms)
        self.sample(now_ms)

    def login(self, market: Market, documents: list, now_ms: int) -> None:
        """A day's login; the plan's trader hands it to AHSync's session.

        Args:
            market: The market.
            documents: The run's daily documents so far.
            now_ms: Now.
        """
        if self.session is not None:
            self.session.login(market, documents, now_ms)

    def sample(self, now_ms: int) -> None:
        """Record the capital tied up and the realized result now (for gold-days and drawdown).

        Args:
            now_ms: Now.
        """
        t, l = self.ledger.totals(), self.ledger
        tied = t["bags"] + t["listed"] + t["mail_items"] + t["deposits"]
        self.samples.append((now_ms, tied, l.gains - l.cuts - l.deposits_lost))

    def _wake(self, at_ms: int, kind: str, ref: str) -> None:
        """Queue a wake.

        Args:
            at_ms: When.
            kind: "login" or "post".
            ref: The post's action id; "" for a login.
        """
        self._seq += 1
        heapq.heappush(self._wakes, (at_ms, self._seq, kind, ref))

    # ------------------------------------------------------------- the market's side

    def observe(self, market: Market) -> None:
        """Settle our auctions from the truth log: sales send gold, expiries and cancels send the items back.

        Args:
            market: The market.
        """
        events = market.truth.events
        for e in events[self._truth_seen:]:
            owner = self.auctions.get(e.auction)
            if owner is None or e.kind == "post":
                continue
            char, key = owner
            if e.kind == "buy":
                deposit_before = self.ledger.deposits[e.auction]
                money = self.ledger.sale(char, e.auction, e.units, e.price)
                returned = deposit_before - self.ledger.deposits.get(e.auction, 0)
                heapq.heappush(self.mails, Mail(e.at_ms + SALE_MAIL_DELAY_MS, char, "sold", key, e.units, e.price,
                                                money, returned))
            else:
                self.ledger.returned(char, key, e.auction)
                heapq.heappush(self.mails, Mail(e.at_ms, char, "expired" if e.kind == "expire" else "cancelled",
                                                key, e.units))
            if e.left == 0:
                del self.auctions[e.auction]
        self._truth_seen = len(events)

    def buy(self, market: Market, char: str, key: int, units: int, max_price: int) -> list[tuple[int, int]]:
        """Buy up to `units` from other sellers' auctions, cheapest first, at no more than max_price a unit.

        Args:
            market: The market.
            char: Who buys.
            key: Item key.
            units: Units wanted.
            max_price: The most to pay a unit, copper.

        Returns:
            (units, unit price) per auction bought from, cheapest first.
        """
        bought = []
        book = sorted((a for a in market.by_key[key].values() if a.id not in self.auctions and a.price <= max_price),
                      key=lambda a: (a.price, a.id))
        for auction in book:
            take = min(units, auction.units if auction.commodity else 1, self.ledger.cash[char] // auction.price)
            if take <= 0 or (not auction.commodity and take < auction.units):
                break
            market.take(auction.id, take, OUR_PREFIX + char)
            self.ledger.buy(char, key, take, take * auction.price)
            self._event(char, market.now_ms, "purchase", key=key, qty=take, price=auction.price, source="ah",
                        confirmed=True)
            bought.append((take, auction.price))
            units -= take
            if units == 0:
                break
        self.observe(market)
        return bought

    def post(self, market: Market, char: str, key: int, units: int, price: int, hours: int,
             cancel_at_ms: int | None = None) -> list[Live]:
        """Post units: one auction of a commodity, one auction a unit of gear.

        Args:
            market: The market.
            char: Who posts; must hold the units.
            key: Item key.
            units: Units to post.
            price: Copper a unit.
            hours: 2, 8 or 24.
            cancel_at_ms: When we cancel them, or None.

        Returns:
            The new auctions.
        """
        item = self.items[key]
        stacks = [units] if item.commodity else [1] * units
        posted = []
        for stack in stacks:
            deposit = deposit_copper(item.vendor_sell, stack, hours)
            if self.ledger.cash[char] < deposit:
                break
            auction = market.post(key, stack, price, hours, OUR_PREFIX + char, cancel_at_ms)
            self.ledger.post(char, key, stack, auction.id, deposit)
            self.auctions[auction.id] = (char, key)
            self._event(char, market.now_ms, "post", key=key, stack=stack, price=price, durationH=hours,
                        deposit=deposit, confirmed=True)
            posted.append(auction)
        return posted

    def collect(self, char: str, now_ms: int) -> None:
        """Take every letter that has arrived for a character, as the addon records it.

        Args:
            char: Who collects.
            now_ms: Now.
        """
        waiting = [m for m in self.mails if m.char == char and m.deliver_ms <= now_ms]
        if not waiting:
            return
        self.mails = [m for m in self.mails if not (m.char == char and m.deliver_ms <= now_ms)]
        heapq.heapify(self.mails)
        gold, items = 0, {}
        for m in sorted(waiting):
            if m.outcome == "sold":
                self._event(char, now_ms, "auction_mail", outcome="sold", key=m.key, qty=m.units, price=m.unit_price)
                self._event(char, now_ms, "payout", key=m.key, qty=m.units, money=m.money, deposit=m.deposit)
                gold += m.money
            else:
                if m.outcome != "mail":                      # another of our characters' letter: no auction mail
                    self._event(char, now_ms, "auction_mail", outcome=m.outcome, key=m.key, qty=m.units)
                items[m.key] = items.get(m.key, 0) + m.units
        self.ledger.collect(char, gold, items)

    def vendor_sell(self, char: str, key: int, units: int) -> None:
        """Sell units to a vendor at the item's vendor price.

        Args:
            char: Who sells; must hold the units.
            key: Item key.
            units: Units sold.
        """
        self.ledger.vendor_sell(char, key, units, self.items[key].vendor_sell)

    def craft(self, char: str, product: int, crafts: int, now_ms: int) -> int:
        """Craft an item with a recipe the character knows, as many times as asked and its bags allow.

        Args:
            char: Who crafts.
            product: The item to make.
            crafts: Crafts asked for.
            now_ms: Now (the craft event's time).

        Returns:
            Crafts done: 0 without a recipe for it or its reagents. Each makes the recipe's yield_min units (a fixed
            yield, so a run is deterministic); the addon's "craft" event records the spell and the count.
        """
        recipe = next((r for r in sorted(self.specs[char].recipes, key=lambda r: r.spell) if r.product == product), None)
        if recipe is None:
            return 0
        done = min([crafts] + [self.held(char, key) // n for key, n in recipe.reagents])
        if done <= 0:
            return 0
        self.ledger.craft(char, tuple((key, n * done) for key, n in recipe.reagents), product, done * recipe.yield_min)
        self._event(char, now_ms, "craft", spell=recipe.spell, count=done)
        return done

    def held(self, char: str, key: int) -> int:
        """Units of an item in a character's bags.

        Args:
            char: Character id.
            key: Item key.

        Returns:
            Units.
        """
        lot = self.ledger.bags.get((char, key))
        return lot.units if lot else 0

    def _event(self, char: str, at_ms: int, kind: str, **fields: object) -> None:
        """Record an event as the addon writes it: id "char:at:n", times in whole seconds.

        Args:
            char: Character id.
            at_ms: When, UTC ms.
            kind: The event type.
            **fields: Its fields (SPEC §2.2.1).
        """
        at = at_ms // 1000 * 1000
        n = self._ids.get((char, at), 0)
        self._ids[(char, at)] = n + 1
        self.events.append({"id": f"{char}:{at}:{n}", "type": kind, "char": char, "at": at} | fields)
        self.counts[kind] = self.counts.get(kind, 0) + 1


class Session:
    """AHSync's side of a trader: its database, the daily import, the tracking pass and the re-plan."""

    def __init__(self, trader: Trader, folder: Path) -> None:
        """A fresh AHSync database for the trader's market.

        Args:
            trader: The trader it serves.
            folder: An empty folder for the database.
        """
        self.trader, self.cfg = trader, market_config(trader.world)
        self.db = Database.open(folder / "ahsync.db", trader.world.start_ms, None)
        self.document = None
        self.replans = 0
        self.logins: list[Login] = []

    def login(self, market: Market, documents: list, now_ms: int) -> None:
        """A day's login: collect mail, let AHSync import and re-plan, then do the plan's due actions.

        Args:
            market: The market.
            documents: The run's daily documents so far; the newest is planned with.
            now_ms: Now.

        Raises:
            RuntimeError: The re-plan failed (`replan_all` swallows its errors and returns no market).
        """
        t = self.trader
        for char in sorted(t.specs):
            t.collect(char, now_ms)
        data = read_ahprofit(savedvars_text(self.savedvars(market, now_ms)))
        apply_data(self.db, data, self.cfg.realms, now_ms, self.cfg.markets)
        t.events = []
        if not documents:
            return
        tracking_pass(self.db, now_ms)
        if replan_all(self.db, now_ms, self.cfg, documents={t.world.market: documents[-1]}) != [t.world.market]:
            raise RuntimeError(f"the re-plan failed at {now_ms}")
        self.replans += 1
        actions = self.pending()
        offers = flip_offers(documents[-1].items, self.db.items(), set(vendor_prices(self.db)), now_ms, self.cfg)
        prices = {key: i.cur for key, i in sorted(documents[-1].items.items()) if i.cur is not None}
        self.logins.append(Login(now_ms, tuple(actions), offers, prices))
        for action in actions:
            self._carry_out(market, action, now_ms)

    def pending(self) -> list[dict]:
        """The plan's pending and late actions for our characters, by time then id.

        Returns:
            Rows as dicts of the actions table's columns.
        """
        chars = sorted(self.trader.specs)
        cursor = self.db.conn.execute(
            f"SELECT action_id, kind, char_id, key, qty, price, slot_ms, window_start_ms, window_end_ms, duration_h, "
            f"source, to_char FROM actions WHERE status IN ('pending', 'late') AND char_id IN "
            f"({','.join('?' * len(chars))}) ORDER BY slot_ms, action_id", chars)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def post_due(self, market: Market, action_id: str, now_ms: int) -> None:
        """A post whose window has opened, if the plan still has it.

        Args:
            market: The market.
            action_id: The action.
            now_ms: Now.
        """
        for action in self.pending():
            if action["action_id"] == action_id:
                self._post(market, action)

    def _carry_out(self, market: Market, action: dict, now_ms: int) -> None:
        """Do one pending action, or schedule it, or count it as left undone.

        Args:
            market: The market.
            action: Its row.
            now_ms: Now.
        """
        t, kind = self.trader, action["kind"]
        due = (action["window_start_ms"] or action["slot_ms"] or now_ms) <= now_ms + DUE_SLACK_MS
        if kind == "buy" and due and action["source"] != "vendor":
            t.buy(market, action["char_id"], action["key"], action["qty"], action["price"])
        elif kind == "post" and due:
            self._post(market, action)
        elif kind == "post":
            t._wake(action["window_start_ms"], "post", action["action_id"])
        elif kind == "craft" and due:
            if not t.craft(action["char_id"], action["key"], action["qty"], now_ms):
                t.counts["undone:craft"] = t.counts.get("undone:craft", 0) + 1
        elif kind == "vendor" and t.held(action["char_id"], action["key"]):
            t.vendor_sell(action["char_id"], action["key"], min(action["qty"], t.held(action["char_id"], action["key"])))
        elif kind == "mail" and t.held(action["char_id"], action["key"]):
            units = min(action["qty"], t.held(action["char_id"], action["key"]))
            t.ledger.mail(action["char_id"], action["to_char"], action["key"], units)
            t._event(action["char_id"], now_ms, "mail_sent", to=action["to_char"], key=action["key"], qty=units)
            heapq.heappush(t.mails, Mail(now_ms, action["to_char"], "mail", action["key"], units))
        else:
            t.counts[f"undone:{kind}"] = t.counts.get(f"undone:{kind}", 0) + 1

    def _post(self, market: Market, action: dict) -> None:
        """Post what the action asks, as far as the bags hold it.

        Args:
            market: The market.
            action: The post's row.
        """
        t = self.trader
        units = min(action["qty"], t.held(action["char_id"], action["key"]))
        price = t.held_prices.get(action["key"], action["price"])
        if units > 0 and price:
            t.post(market, action["char_id"], action["key"], units, price, action["duration_h"] or 24)

    def savedvars(self, market: Market, now_ms: int) -> dict:
        """The AHProfitDB table the addon would have written at this login.

        Args:
            market: The market.
            now_ms: Now.

        Returns:
            The table: characters with their gold, bags, own auctions and the events since the last import.
        """
        t = self.trader
        at = now_ms // 1000 * 1000
        db: dict = {"addonVersion": "0.1.0", "characters": {}, "inventory": {}, "ownAuctions": {}, "events": t.events}
        for char, spec in sorted(t.specs.items()):
            db["characters"][char] = {"name": spec.name, "realm": REALM, "faction": spec.faction, "class": spec.cls,
                                      "level": 60, "gold": t.ledger.cash[char], "updated": at}
            bags = [f"{key},{lot.units},0" for (who, key), lot in sorted(t.ledger.bags.items()) if who == char and lot.units]
            db["inventory"][char] = {"bags": ";".join(bags), "bagsAt": at}
            mine = [market_auction(market, a) for a, (who, _) in sorted(t.auctions.items()) if who == char]
            rows = [f"{a.key},{a.price},{a.units},{client_band(a.expires_ms - now_ms)},1" for a in mine if a]
            ends = [f"{a.key},{a.units},{a.expires_ms // 1000 * 1000},{a.price}" for a in mine if a]
            db["ownAuctions"][char] = {"at": at, "rows": ";".join(rows), "ends": ";".join(ends)}
        self._recipes(db)
        return db

    def _recipes(self, db: dict) -> None:
        """The characters' professions, the recipes' definitions and who knows them, as the addon writes them
        (SPEC §2.2.1; MS4b-2).

        Args:
            db: The AHProfitDB table; gains "professions", "recipes" and "knownRecipes".
        """
        db["professions"], db["recipes"], db["knownRecipes"] = {}, {}, {}
        for char, spec in sorted(self.trader.specs.items()):
            for r in sorted(spec.recipes, key=lambda r: r.spell):
                db["professions"].setdefault(char, {})[r.profession] = {"skill": 300, "max": 300}
                db["recipes"].setdefault(r.profession, {})[r.spell] = {
                    "product": r.product, "yieldMin": r.yield_min, "yieldMax": r.yield_max, "skill": 1, "cooldown": 0,
                    "reagents": ";".join(f"{key},{n}" for key, n in r.reagents)}
            if spec.recipes:
                db["knownRecipes"][char] = ";".join(str(r.spell) for r in sorted(spec.recipes, key=lambda r: r.spell))


def market_auction(market: Market, auction_id: int) -> Live | None:
    """A live auction by id.

    Args:
        market: The market.
        auction_id: The id.

    Returns:
        The auction, or None once it has ended.
    """
    return next((live[auction_id] for live in market.by_key.values() if auction_id in live), None)


def client_band(remaining_ms: int) -> int:
    """The game client's time-left band for an auction (SHORT 1 to VERY_LONG 4; the addon reads the client).

    Args:
        remaining_ms: Time left, ms.

    Returns:
        1 under 30 min, 2 under 2 h, 3 under 8 h, else 4.
    """
    return next((band for limit, band in CLIENT_BANDS if remaining_ms < limit), 4)


class NaiveTrader(Trader):
    """The naive benchmark (plan §1.6): at each login it buys the cheapest other listing of an item when reposting
    it at the document's med7 would pay after the cut and the deposit, and posts everything it holds at med7 for
    24 h."""

    def __init__(self, world: World, characters: tuple[CharacterSpec, ...],
                 login_hours: tuple[int, ...] = (LOGIN_HOUR_UTC,)) -> None:
        """Characters with logins every day and no AHSync.

        Args:
            world: The scenario.
            characters: Our characters.
            login_hours: UTC hours they log in each day.
        """
        super().__init__(world, characters, login_hours=login_hours)
        self.schedule_logins()

    def login(self, market: Market, documents: list, now_ms: int) -> None:
        """Collect mail, buy the profitable cheapest listings, post the bags at med7.

        Args:
            market: The market.
            documents: The run's daily documents so far.
            now_ms: Now.
        """
        for char in sorted(self.specs):
            self.collect(char, now_ms)
        if not documents:
            return
        doc = documents[-1]
        for char in sorted(self.specs):
            for key in sorted(self.items):
                med7 = doc.items[key].med7 if key in doc.items else None
                if med7:
                    self._naive_buy(market, char, key, med7)
                    if self.held(char, key):
                        self.post(market, char, key, self.held(char, key), med7, 24)

    def _naive_buy(self, market: Market, char: str, key: int, med7: int) -> None:
        """Buy the cheapest other listing of an item whole when it pays at med7.

        Args:
            market: The market.
            char: Who buys.
            key: Item key.
            med7: The document's 7-day median, copper.
        """
        others = sorted((a for a in market.by_key[key].values() if a.id not in self.auctions),
                        key=lambda a: (a.price, a.id))
        if not others:
            return
        cheapest = others[0]
        deposit = deposit_copper(self.items[key].vendor_sell, cheapest.units, 24) / cheapest.units
        if cheapest.price < med7 * (1 - AH_CUT) - deposit:
            self.buy(market, char, key, cheapest.units, cheapest.price)
