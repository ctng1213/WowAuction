"""Record Blizzard's Forever auction API for the smoke test (SPEC §2.4, §6 items 23-25; plan M28).

Usage:
    python tools/record_blizzard.py record [--houses 113,114,115,120,124] [--ruleset 2]
    python tools/record_blizzard.py find <recording folder> --item 2589 --quantity 7 --price 12345
    python tools/record_blizzard.py watch [--house 113] [--hours 26] [--every-minutes 5]

record: fetches the ruleset and house indexes, both endpoints of every house given and a static item probe.
The full responses go gzipped to data/blizzard/<UTC stamp>/ (gitignored: a house is about 5 MB), with
summary.json (per response: status, Last-Modified, records, bid-only auctions, raw and gzipped bytes and a
hash of the auction list, which tells duplicate houses apart). Trimmed copies (the metadata, the first few
auctions and those of the named items) go to tests/fixtures/blizzard/ to be committed.
find: which recorded houses hold the marker auction you posted (§6 item 23).
watch: polls one house's commodities with If-Modified-Since and logs each new Last-Modified (§6 item 25).

Credentials (the user's Battle.net developer client) come from env BLIZZARD_CLIENT_ID and
BLIZZARD_CLIENT_SECRET, else ~/.ahsync/blizzard (two lines: the id, then the secret; mode 600). They are
never printed, never written, never on a command line; the token lives in memory only and every file is
checked for the id, the secret and the token before it is written.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
TOKEN_URL = "https://oauth.battle.net/token"
API = "https://us.api.blizzard.com"
NAMESPACE = "dynamic-forever-us"
STATIC_NAMESPACES = ("static-forever-us", "static-us", "static-classic1x-us")   # §6 item 24, most likely first
PROBE_ITEM = 2589                                    # Linen Cloth
HORDE_HOUSES = (113, 114, 115, 120, 124)             # launch-day notes, SPEC §2.4
KEPT_HEADERS = ("last-modified", "content-type", "content-encoding", "date")
NAMED_ITEMS = (2589, 2770, 2840, 4306, 2996, 2772, 10940, 8180)   # items the scenarios use
FIRST_KEPT = 20                                      # trimmed fixtures keep the first auctions too
PER_ITEM_KEPT = 5
TIMEOUT_S = 60
USER_AGENT = "AHSync-recorder/0.1"


class CredentialsMissing(Exception):
    """No Blizzard client credentials were found (the message says where to put them)."""


class RecordError(Exception):
    """A request failed or a file would have held a secret. Messages never hold a credential or the token."""


@dataclass(frozen=True)
class Response:
    """One HTTP answer: status, lower-cased headers, the body decompressed."""

    status: int
    headers: dict[str, str]
    body: bytes


Get = Callable[[str, dict[str, str]], Response]      # (url, headers) -> response
Post = Callable[[str, bytes, dict[str, str]], Response]


@dataclass
class Secrets:
    """Strings that must never reach a file or the console."""

    values: list[str] = field(default_factory=list)

    def check(self, data: bytes, what: str) -> None:
        """Refuse data that contains a secret.

        Args:
            data: Bytes about to be written.
            what: The file's name, for the error.

        Raises:
            RecordError: A secret is in it; nothing is written.
        """
        for value in self.values:
            if value and value.encode("utf-8") in data:
                raise RecordError(f"{what} would contain a credential or the token; not written")


def load_credentials(env: dict[str, str], home: Path) -> tuple[str, str]:
    """The Blizzard client id and secret, from the environment, else ~/.ahsync/blizzard (SPEC §2.4).

    Args:
        env: The environment.
        home: The user's home folder.

    Returns:
        (client id, client secret).

    Raises:
        CredentialsMissing: Neither place has both; the message says what to create.
    """
    client_id, secret = env.get("BLIZZARD_CLIENT_ID", "").strip(), env.get("BLIZZARD_CLIENT_SECRET", "").strip()
    if client_id and secret:
        return client_id, secret
    path = home / ".ahsync" / "blizzard"
    if path.is_file():
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
    raise CredentialsMissing(f"no Blizzard credentials: set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET, or put the "
                             f"client id and secret on two lines in {path} (chmod 600)")


def request_token(post: Post, client_id: str, secret: str) -> str:
    """A client-credentials access token from Battle.net.

    Args:
        post: The HTTP POST.
        client_id: The client id.
        secret: The client secret.

    Returns:
        The access token.

    Raises:
        RecordError: The token request failed (its status only, never the body).
    """
    basic = base64.b64encode(f"{client_id}:{secret}".encode("utf-8")).decode("ascii")
    response = post(TOKEN_URL, b"grant_type=client_credentials",
                    {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    if response.status != 200:
        raise RecordError(f"token request answered {response.status}; check the client id and secret")
    token = json.loads(response.body).get("access_token")
    if not token:
        raise RecordError("token response had no access_token")
    return token


def api_get(get: Get, token: str, path: str, namespace: str = NAMESPACE, since: str | None = None) -> Response:
    """GET one API route with the bearer token and gzip.

    Args:
        get: The HTTP GET.
        token: The access token.
        path: The route, e.g. "/data/wow/ruleset/index".
        namespace: The namespace query value.
        since: A Last-Modified value to send as If-Modified-Since.

    Returns:
        The response, body decompressed.
    """
    headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
    if since:
        headers["If-Modified-Since"] = since
    return get(f"{API}{path}?namespace={namespace}", headers)


def summarize(body: bytes) -> dict:
    """What a house response holds: records, bid-only auctions and a hash of its auction list (SPEC §6 item 25).

    Args:
        body: A decompressed auctions or commodities response.

    Returns:
        {"records", "bid_only", "auctions_sha256"}; records 0 and hash None for a metadata-only house.
    """
    data = json.loads(body)
    auctions = data.get("auctions")
    if auctions is None:
        return {"records": 0, "bid_only": 0, "auctions_sha256": None}
    bid_only = sum(1 for a in auctions if "buyout" not in a and "unit_price" not in a)
    canonical = json.dumps(sorted(auctions, key=lambda a: a.get("id", 0)), sort_keys=True, separators=(",", ":"))
    return {"records": len(auctions), "bid_only": bid_only,
            "auctions_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def trim(body: bytes) -> dict:
    """A small fixture from a house response: everything but the auction list, then a few real auctions.

    Args:
        body: A decompressed auctions or commodities response.

    Returns:
        The response with `auctions` cut to the first FIRST_KEPT plus up to PER_ITEM_KEPT of each named item,
        in their original order; a response without auctions unchanged.
    """
    data = json.loads(body)
    auctions = data.get("auctions")
    if auctions is None:
        return data
    per_item: dict[int, int] = {}
    kept = []
    for index, auction in enumerate(auctions):
        item = auction.get("item", {}).get("id")
        if index < FIRST_KEPT:
            kept.append(auction)
        elif item in NAMED_ITEMS and per_item.get(item, 0) < PER_ITEM_KEPT:
            per_item[item] = per_item.get(item, 0) + 1
            kept.append(auction)
    return {**data, "auctions": kept}


def routes(ruleset: int, houses: tuple[int, ...]) -> list[tuple[str, str, str]]:
    """Every request of a recording, in order.

    Args:
        ruleset: The ruleset id (2 = Normal).
        houses: House ids.

    Returns:
        (file stem, path, namespace) per request.
    """
    found = [("ruleset_index", "/data/wow/ruleset/index", NAMESPACE),
             (f"ruleset_{ruleset}", f"/data/wow/ruleset/{ruleset}", NAMESPACE),
             (f"ruleset_{ruleset}_auctions_index", f"/data/wow/ruleset/{ruleset}/auctions/index", NAMESPACE)]
    for house in houses:
        base = f"/data/wow/ruleset/{ruleset}/auctions/{house}"
        found += [(f"house_{house}_auctions", base, NAMESPACE), (f"house_{house}_commodities", f"{base}/commodities", NAMESPACE)]
    found += [(f"item_{PROBE_ITEM}_{ns.replace('-', '_')}", f"/data/wow/item/{PROBE_ITEM}", ns) for ns in STATIC_NAMESPACES]
    return found


def write_checked(path: Path, data: bytes, secrets: Secrets) -> None:
    """Write a file after checking it holds no secret.

    Args:
        path: Target file (its folder is created).
        data: Bytes.
        secrets: What must not be in them.

    Raises:
        RecordError: A secret is in the data; nothing is written.
    """
    secrets.check(data, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def record(get: Get, token: str, raw_dir: Path, fixture_dir: Path, ruleset: int, houses: tuple[int, ...],
           secrets: Secrets) -> dict:
    """Record every route: full responses gzipped to raw_dir, trimmed ones to fixture_dir, and a summary.

    Args:
        get: The HTTP GET.
        token: The access token.
        raw_dir: The recording's folder (gitignored).
        fixture_dir: tests/fixtures/blizzard/.
        ruleset: Ruleset id.
        houses: House ids.
        secrets: Credentials and the token, checked out of every file.

    Returns:
        The summary: per file stem, status, kept headers, raw and gzipped bytes, and for house responses the
        `summarize` fields. Also written as summary.json in both folders.
    """
    summary: dict[str, dict] = {}
    for stem, path, namespace in routes(ruleset, houses):
        response = api_get(get, token, path, namespace)
        secrets.check(response.body, stem)                 # before compressing: gzip would hide a secret
        compressed = gzip.compress(response.body, mtime=0)
        entry = {"path": path, "namespace": namespace, "status": response.status,
                 "headers": {k: response.headers[k] for k in KEPT_HEADERS if k in response.headers},
                 "bytes": len(response.body), "gzip_bytes": len(compressed)}
        if response.status == 200:
            write_checked(raw_dir / f"{stem}.json.gz", compressed, secrets)
            if stem.startswith("house_"):
                entry.update(summarize(response.body))
            fixture = trim(response.body) if stem.startswith("house_") else json.loads(response.body)
            write_checked(fixture_dir / f"{stem}.json",
                          (json.dumps(fixture, indent=1, sort_keys=True) + "\n").encode("utf-8"), secrets)
        summary[stem] = entry
    text = (json.dumps(summary, indent=1, sort_keys=True) + "\n").encode("utf-8")
    write_checked(raw_dir / "summary.json", text, secrets)
    write_checked(fixture_dir / "summary.json", text, secrets)
    return summary


def find_marker(raw_dir: Path, item: int, quantity: int, price: int) -> list[tuple[str, int]]:
    """Which recorded house responses hold an auction of this item, quantity and price (SPEC §6 item 23).

    Args:
        raw_dir: A recording's folder.
        item: Item id.
        quantity: Units in the auction.
        price: Its buyout (regular) or unit price (commodity), copper.

    Returns:
        (file stem, auction id) per match, sorted.
    """
    matches = []
    for path in sorted(raw_dir.glob("house_*.json.gz")):
        for auction in json.loads(gzip.decompress(path.read_bytes())).get("auctions") or []:
            asking = auction.get("unit_price", auction.get("buyout"))
            if auction.get("item", {}).get("id") == item and auction.get("quantity") == quantity and asking == price:
                matches.append((path.name.removesuffix(".json.gz"), auction["id"]))
    return matches


def watch(get: Get, token: Callable[[bool], str], path: str, polls: int, every_s: float, now: Callable[[], float],
          sleep: Callable[[float], None]) -> list[dict]:
    """Poll a route with If-Modified-Since and note every new Last-Modified (SPEC §6 item 25).

    Args:
        get: The HTTP GET.
        token: Returns the access token; called with True for a new one after a 401 (a token lasts about 24 h,
            a watch longer).
        path: The route.
        polls: How many polls.
        every_s: Seconds between polls.
        now: Wall clock, Unix seconds.
        sleep: Waits.

    Returns:
        One row per change: {"seen_at", "last_modified", "status"}, in order.
    """
    changes, last = [], None
    for index in range(polls):
        response = api_get(get, token(False), path, since=last)
        if response.status == 401:
            response = api_get(get, token(True), path, since=last)
        stamp = response.headers.get("last-modified")
        if response.status == 200 and stamp != last:
            changes.append({"seen_at": datetime.fromtimestamp(now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "last_modified": stamp, "status": response.status})
            print(f"{changes[-1]['seen_at']}  new Last-Modified {stamp}", flush=True)
            last = stamp
        if index + 1 < polls:
            sleep(every_s)
    return changes


def urllib_get(url: str, headers: dict[str, str]) -> Response:
    """The real GET: a gzip body is decompressed; error statuses are returned, not raised.

    Args:
        url: The URL.
        headers: Request headers (the Authorization one is not copied onto a redirect).

    Returns:
        The response.

    Raises:
        RecordError: A network failure (its type only).
    """
    request = urllib.request.Request(url, headers={k: v for k, v in headers.items() if k != "Authorization"})
    if "Authorization" in headers:
        request.add_unredirected_header("Authorization", headers["Authorization"])
    return _send(request)


def urllib_post(url: str, data: bytes, headers: dict[str, str]) -> Response:
    """The real POST (the token request).

    Args:
        url: The URL.
        data: The form body.
        headers: Request headers.

    Returns:
        The response.

    Raises:
        RecordError: A network failure (its type only).
    """
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={k: v for k, v in headers.items() if k != "Authorization"})
    request.add_unredirected_header("Authorization", headers["Authorization"])
    return _send(request)


def _send(request: urllib.request.Request) -> Response:
    """Send a request and read its answer.

    Args:
        request: The prepared request.

    Returns:
        The response, body decompressed when it came gzipped.

    Raises:
        RecordError: A network failure (its type only, never the URL's query or a header).
    """
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
            status, headers, body = answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
    except urllib.error.HTTPError as error:
        status, headers, body = error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, error.read() or b""
    except OSError as error:
        raise RecordError(f"network failure: {type(error).__name__}") from None
    if headers.get("content-encoding") == "gzip" and body:
        body = gzip.decompress(body)
    return Response(status, headers, body)


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, home: Path | None = None,
         get: Get = urllib_get, post: Post = urllib_post) -> int:
    """Run a subcommand.

    Args:
        argv: Arguments (sys.argv[1:] when None).
        env: The environment (os.environ when None).
        home: The home folder (Path.home() when None).
        get: The HTTP GET.
        post: The HTTP POST.

    Returns:
        The exit code: 0 done, 1 a missing credential or failed request, 2 no marker found.
    """
    args = _parser().parse_args(argv)
    if args.command == "find":
        matches = find_marker(Path(args.folder), args.item, args.quantity, args.price)
        for stem, auction_id in matches:
            print(f"{stem}: auction {auction_id}")
        print(f"{len(matches)} match(es)")
        return 0 if matches else 2
    try:
        client_id, secret = load_credentials(dict(os.environ) if env is None else env, home or Path.home())
        tokens = TokenSource(post, client_id, secret)
        if args.command == "record":
            _run_record(args, get, tokens)
        else:
            _run_watch(args, get, tokens)
    except (CredentialsMissing, RecordError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


class TokenSource:
    """Holds the current access token and gets a new one on request, remembering every token as a secret."""

    def __init__(self, post: Post, client_id: str, secret: str) -> None:
        """Request the first token.

        Args:
            post: The HTTP POST.
            client_id: The client id.
            secret: The client secret.

        Raises:
            RecordError: The token request failed.
        """
        self._post, self._client_id, self._secret = post, client_id, secret
        self.token = request_token(post, client_id, secret)
        self.secrets = Secrets([client_id, secret, self.token])

    def __call__(self, renew: bool) -> str:
        """The current token, or a new one when renew is True (a token lasts about 24 h).

        Args:
            renew: True after a 401.

        Returns:
            The access token.
        """
        if renew:
            self.token = request_token(self._post, self._client_id, self._secret)
            self.secrets.values.append(self.token)
        return self.token


def _run_record(args: argparse.Namespace, get: Get, tokens: TokenSource) -> None:
    """The record subcommand: record, then print one line per response.

    Args:
        args: The parsed arguments (ruleset, houses).
        get: The HTTP GET.
        tokens: The token source.
    """
    raw_dir = ROOT / "data" / "blizzard" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    houses = tuple(int(h) for h in args.houses.split(","))
    summary = record(get, tokens(False), raw_dir, ROOT / "tests" / "fixtures" / "blizzard", args.ruleset, houses,
                     tokens.secrets)
    for stem, entry in summary.items():
        print(f"{stem}: {entry['status']}  {entry.get('records', '')} records  {entry['gzip_bytes']} B gzipped")
    print(f"full responses in {raw_dir}")


def _run_watch(args: argparse.Namespace, get: Get, tokens: TokenSource) -> None:
    """The watch subcommand: poll, then write data/blizzard/cadence.json.

    Args:
        args: The parsed arguments (ruleset, house, hours, every_minutes).
        get: The HTTP GET.
        tokens: The token source.
    """
    path = f"/data/wow/ruleset/{args.ruleset}/auctions/{args.house}/commodities"
    polls = int(args.hours * 60 / args.every_minutes) + 1
    changes = watch(get, tokens, path, polls, args.every_minutes * 60, time.time, time.sleep)
    out = ROOT / "data" / "blizzard" / "cadence.json"
    write_checked(out, (json.dumps(changes, indent=1) + "\n").encode("utf-8"), tokens.secrets)
    print(f"{len(changes)} refreshes; written to {out}")


def _parser() -> argparse.ArgumentParser:
    """The command-line parser (no option takes a credential).

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(description="Record Blizzard's Forever auction API (SPEC §2.4).")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record the indexes, the houses and the static probe")
    rec.add_argument("--ruleset", type=int, default=2)
    rec.add_argument("--houses", default=",".join(str(h) for h in HORDE_HOUSES))
    find = sub.add_parser("find", help="find the marker auction in a recording")
    find.add_argument("folder")
    find.add_argument("--item", type=int, required=True)
    find.add_argument("--quantity", type=int, required=True)
    find.add_argument("--price", type=int, required=True, help="buyout or unit price in copper")
    wat = sub.add_parser("watch", help="log each new Last-Modified of one house")
    wat.add_argument("--ruleset", type=int, default=2)
    wat.add_argument("--house", type=int, default=113)
    wat.add_argument("--hours", type=float, default=26)
    wat.add_argument("--every-minutes", type=float, default=5)
    return parser


if __name__ == "__main__":
    sys.exit(main())
