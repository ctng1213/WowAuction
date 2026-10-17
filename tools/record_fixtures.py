"""Record trimmed, redacted auctionhouse.fyi responses as test templates (SPEC §8.1).

Usage: python tools/record_fixtures.py [--market forever.pvp.horde.us]

Fetches /me, /config, /files, the snapshot, public and capability (Premium)
hour and day candles, the newest order book, volume and /items. Each is trimmed
to a small fixed set of real items and written to tests/fixtures/ahf/<market>/,
with manifest.json mapping each fixture to the CDN path it came from.

Secrets never reach disk. The API key is read from env AUCTIONHOUSE_APIKEY,
then %USERPROFILE%/.ahsync/api_key (SPEC §3.6), and is never printed.
Capability tokens become "cap-<day>"-style placeholders, the account's id and
email are replaced, and every output is checked for leftovers before writing.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://auctionhouse.fyi/api/v1"
CDN = "https://cdn.auctionhouse.fyi/"
HOUR_MS = 3_600_000
SUFFIX_BASE = 2_147_483_648

# Items the spec's scenarios and mockups use (SPEC §3.4, SCENARIOS.md), by base item id.
NAMED_ITEMS = {
    2589: "Linen Cloth", 2996: "Bolt of Linen Cloth", 4306: "Silk Cloth", 4305: "Bolt of Silk Cloth",
    2320: "Coarse Thread", 2770: "Copper Ore", 2840: "Copper Bar", 6217: "Copper Rod",
    6218: "Runed Copper Rod", 2772: "Iron Ore", 3575: "Iron Bar", 4234: "Heavy Leather",
    7969: "Mithril Spurs", 10940: "Strange Dust", 10938: "Lesser Magic Essence",
}
TOP_BY_BOOK = 10       # plus the most-listed items in the newest book, for realism
SUFFIX_VARIANTS = 3    # plus a few random-suffix keys (item + 2^31 × v)


class Recorder:
    """Fetches AHF responses with an optional key and remembers secrets to redact."""

    def __init__(self, key: str) -> None:
        """Create a recorder.

        Args:
            key: The API key, or "" to record without one.
        """
        self.key = key
        self.secrets: set[str] = {key} if key else set()

    def get(self, url: str, auth: bool = True) -> dict | None:
        """Fetch and decode one JSON response, honouring 429 Retry-After once.

        Args:
            url: Absolute URL.
            auth: Send the key (when there is one).

        Returns:
            The decoded JSON, or None on 403/404.

        Raises:
            urllib.error.HTTPError: For other HTTP errors. Its message never contains the key.
        """
        for attempt in range(2):
            request = urllib.request.Request(url, headers={"User-Agent": "AHSync-record/0.1",
                                                           "Accept-Encoding": "gzip"})
            if auth and self.key:
                request.add_header("Authorization", "Bearer " + self.key)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read()
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt == 0:
                    time.sleep(int(error.headers.get("Retry-After", "5")))
                    continue
                if error.code in (403, 404):
                    return None
                raise
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return json.loads(body)
        return None


def load_key() -> str:
    """Find the API key the way AHSync does (SPEC §3.6), without printing it.

    Returns:
        The key, or "" when none is configured.
    """
    key = os.environ.get("AUCTIONHOUSE_APIKEY", "").strip()
    if not key:
        key_file = Path(os.environ.get("USERPROFILE", Path.home())) / ".ahsync" / "api_key"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
    return key


def choose_keys(book: dict | None, items: dict | None) -> list[int]:
    """Pick the item keys every fixture is trimmed to.

    Args:
        book: The newest order book ({"entries": [[key, price, stack, band, count], ...]}), or None.
        items: The /items response, or None.

    Returns:
        Sorted item keys: the named items, the most-listed keys in the book
        (ties to the smaller key) and a few random-suffix keys from the book.
    """
    keys = set(NAMED_ITEMS)
    if book:
        listings: dict[int, int] = {}
        for key, _price, _stack, _band, count in book["entries"]:
            listings[key] = listings.get(key, 0) + count
        ranked = sorted(listings, key=lambda k: (-listings[k], k))
        keys.update(ranked[:TOP_BY_BOOK])
        keys.update(sorted(k for k in listings if k >= SUFFIX_BASE)[:SUFFIX_VARIANTS])
    return sorted(keys)


def trim_rows(rows: list, keys: set[int]) -> list:
    """Keep the rows whose first field is one of the chosen keys.

    Args:
        rows: Rows such as [key, ...] (candles, book entries, snapshot rows).
        keys: Item keys to keep.

    Returns:
        The kept rows, in their original order.
    """
    return [row for row in rows if row[0] in keys]


def trim_items(items: dict, keys: list[int]) -> dict:
    """Trim /items to the chosen keys, keeping its structure.

    Args:
        items: The full /items response.
        keys: Chosen item keys (base items and suffix variants).

    Returns:
        A copy with only the chosen items and variants, the suffixes those
        variants could use, and the remaining lookup tables cut to 5 entries.
    """
    base_ids = {str(k % SUFFIX_BASE) for k in keys}
    variant_ids = {str(k) for k in keys if k >= SUFFIX_BASE}
    trimmed = {k: v for k, v in items.items() if not isinstance(v, dict)}
    trimmed["items"] = {k: v for k, v in sorted(items["items"].items()) if k in base_ids}
    trimmed["variants"] = {k: v for k, v in sorted(items.get("variants", {}).items()) if k in variant_ids}
    for table in ("suffixes", "statNames", "bonusLists", "bonusTrees", "classifications"):
        if isinstance(items.get(table), dict):
            trimmed[table] = dict(sorted(items[table].items())[:5])
    if "catalogue" in items:
        trimmed["catalogue"] = items["catalogue"]
    return trimmed


def redact_files(files: dict, recorder: Recorder) -> dict:
    """Replace capability tokens in /files with stable placeholders.

    Args:
        files: The /files response.
        recorder: Collects the real tokens so the final check can find leftovers.

    Returns:
        A copy where caps.h1/m1 tokens are "cap-h1-<day>" / "cap-m1-<day>" and
        every capability URL's token is replaced the same way.
    """
    text = json.dumps(files)
    caps = files.get("caps", {})
    for kind in ("h1", "m1"):
        for day, token in caps.get(kind, {}).items():
            recorder.secrets.add(token)
            text = text.replace(token, f"cap-{kind}-{day}")
    for kind in ("d1", "volume"):
        url = caps.get(kind)
        if isinstance(url, str):
            token = url.split("/")[-2]
            recorder.secrets.add(token)
            text = text.replace(token, f"cap-{kind}")
    # Tokens may contain "-", so cut them by the known filename prefix, not by splitting on "-".
    for book in files.get("books", []):
        token = book["url"].rsplit("/", 1)[-1].removeprefix(f"{book['at']}-").removesuffix(".json.gz")
        recorder.secrets.add(token)
        text = text.replace(token, f"cap-book-{book['at']}")
    checkpoint = (files.get("live") or {}).get("checkpoint") or {}
    if checkpoint.get("url"):
        token = checkpoint["url"].rsplit("/", 1)[-1].removeprefix(f"c{checkpoint['seq']}-").removesuffix(".json.gz")
        recorder.secrets.add(token)
        text = text.replace(token, "cap-live")
    return json.loads(text)


def redact_me(me: dict) -> dict:
    """Replace the account's identity in /me with fixed fake values.

    Args:
        me: The /me response.

    Returns:
        A copy with user.id and user.email replaced; everything else is kept.
    """
    redacted = json.loads(json.dumps(me))
    if isinstance(redacted.get("user"), dict):
        redacted["user"]["id"] = "user_FIXTURE"
        redacted["user"]["email"] = "fixture@example.invalid"
    return redacted


def record(market: str, recorder: Recorder) -> dict[str, tuple[str, object]]:
    """Fetch and trim every fixture for one market.

    Args:
        market: AHF market id, e.g. "forever.pvp.horde.us".
        recorder: The fetcher.

    Returns:
        Fixture name -> (CDN or API path it came from, with placeholders; trimmed JSON).
        Responses that were unavailable (403/404) are left out.
    """
    out: dict[str, tuple[str, object]] = {}
    me = recorder.get(f"{API}/me")
    if me:
        recorder.secrets.update(str(v) for v in (me.get("user") or {}).values())
        out["me"] = ("api/v1/me", redact_me(me))
    config = recorder.get(f"{API}/config", auth=False)
    if config:
        out["config"] = ("api/v1/config", {k: config[k] for k in ("env", "apiBase", "cdnBase") if k in config})
    files = recorder.get(f"{API}/ah/{market}/files")
    if not files:
        sys.exit(f"record_fixtures: no /files for {market}")
    caps = files.get("caps", {})
    newest_book = files["books"][-1] if files.get("books") else None
    book = recorder.get(newest_book["url"]) if newest_book else None
    items = recorder.get(f"{API}/items")
    keys = choose_keys(book, items)
    keyset = set(keys)

    if book:
        trimmed = {**book, "entries": trim_rows(book["entries"], keyset),
                   "bidEntries": trim_rows(book.get("bidEntries", []), keyset)}
        out["book"] = (f"books/v1/{market}/{newest_book['at']}-cap-book-{newest_book['at']}.json.gz", trimmed)
    if items:
        out["items"] = ("api/v1/items", trim_items(items, keys))

    pointer = recorder.get(files["snapshot"], auth=False)
    if pointer:
        out["snapshot_pointer"] = (f"snap/v1/{market}/latest.json", pointer)
        snapshot = recorder.get(CDN + pointer["path"], auth=False)
        if snapshot:
            out["snapshot"] = (pointer["path"], {**snapshot, "rows": trim_rows(snapshot["rows"], keyset)})

    sealed_hour = files["sealed"]["h1"]
    data_hour = newest_book["at"] // HOUR_MS if newest_book else sealed_hour
    for label, hour in (("h1_data", data_hour), ("h1_sealed", sealed_hour)):
        candles = recorder.get(f"{CDN}candles/v1/h1/{market}/{hour}.json.gz", auth=False)
        if candles:
            out[label] = (f"candles/v1/h1/{market}/{hour}.json.gz", {**candles, "items": trim_rows(candles["items"], keyset)})
    day = str(data_hour * HOUR_MS // 86_400_000)
    if day in caps.get("h1", {}):
        candles = recorder.get(f"{CDN}candles/v1/h1c/{market}/{caps['h1'][day]}/{data_hour}.json.gz")
        if candles:
            out["h1c_data"] = (f"candles/v1/h1c/{market}/cap-h1-{day}/{data_hour}.json.gz",
                               {**candles, "items": trim_rows(candles["items"], keyset)})

    d1 = recorder.get(files["d1"], auth=False)
    if d1:
        out["d1"] = (files["d1"].removeprefix(CDN), {**d1, "items": trim_rows(d1["items"], keyset)})
    if isinstance(caps.get("d1"), str):
        d1c = recorder.get(caps["d1"])
        if d1c:
            out["d1c"] = (f"candles/v1/d1c/{market}/cap-d1/{files['sealed']['d1']}.json.gz",
                          {**d1c, "items": trim_rows(d1c["items"], keyset)})
    if isinstance(caps.get("volume"), str):
        volume = recorder.get(caps["volume"])
        if volume:
            out["volume"] = (f"volume/v1/{market}/cap-volume/{files['sealed']['d1']}.json.gz",
                             {**volume, "items": trim_rows(volume["items"], keyset)})

    out["files"] = (f"api/v1/ah/{market}/files", redact_files(files, recorder))
    return out


def check_clean(name: str, text: str, recorder: Recorder) -> None:
    """Stop before writing if a fixture still contains a secret.

    Args:
        name: Fixture name, for the message.
        text: The serialized fixture.
        recorder: Holds the key, capability tokens and account identity seen.

    Raises:
        SystemExit: If any secret appears in text. The secret itself is not printed.
    """
    for secret in recorder.secrets:
        if secret and len(secret) >= 8 and secret in text:
            sys.exit(f"record_fixtures: {name} still contains a secret; nothing written")


def write(market: str, fixtures: dict[str, tuple[str, object]], recorder: Recorder) -> Path:
    """Check every fixture for secrets, then write them and the manifest.

    Args:
        market: AHF market id; the output folder name.
        fixtures: From record().
        recorder: For the secret check.

    Returns:
        The output folder.
    """
    serialized = {name: json.dumps(data, indent=1, sort_keys=True) + "\n" for name, (_, data) in fixtures.items()}
    for name, text in serialized.items():
        check_clean(name, text, recorder)
    folder = ROOT / "tests" / "fixtures" / "ahf" / market
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in serialized.items():
        (folder / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
    manifest = {
        "market": market,
        "recorded_at_ms": int(time.time() * 1000),
        "with_key": bool(recorder.key),
        "fixtures": {name: {"file": f"{name}.json", "source_path": path} for name, (path, _) in sorted(fixtures.items())},
    }
    manifest_text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    check_clean("manifest", manifest_text, recorder)
    (folder / "manifest.json").write_text(manifest_text, encoding="utf-8", newline="\n")
    return folder


def main() -> None:
    """Parse arguments, record one market and report what was written (never the key)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--market", default="forever.pvp.horde.us")
    args = parser.parse_args()
    recorder = Recorder(load_key())
    fixtures = record(args.market, recorder)
    folder = write(args.market, fixtures, recorder)
    print(f"record_fixtures: {len(fixtures)} fixtures -> {folder.relative_to(ROOT).as_posix()} "
          f"({'with' if recorder.key else 'without'} key)")
    for name, (path, data) in sorted(fixtures.items()):
        rows = next((len(data[k]) for k in ("items", "entries", "rows") if isinstance(data, dict) and isinstance(data.get(k), (list, dict))), "")
        print(f"  {name:17} {rows!s:>5}  {path}")


if __name__ == "__main__":
    main()
