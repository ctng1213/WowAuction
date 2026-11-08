"""Publishing and reading the market summary in the intel store (SPEC §2.4 "The published document", BZ15).

A document is `intel.contract.to_json` gzipped (no gzip time, so the same intelligence gives the same bytes) at
`intel/<market>/<sha256>.json.gz`, named by its own hash so a republish never overwrites a key CloudFront may
still serve; then the pointer `intel/<market>/latest-<schema>.json` is written last, so a reader never sees a
half-published document. The settings the baselines were built with travel in the document (`settings`).
"""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
from dataclasses import dataclass

from ahcloud.store import Store
from ahsync.engine.sellprob import MarketSettings
from ahsync.intel.contract import INTEL_SCHEMA_VERSION, MarketIntelligence, from_json, to_json


@dataclass(frozen=True)
class Pointer:
    """What `latest-<schema>.json` says."""

    schema: int
    built_ms: int
    observations_until_ms: int | None
    key: str                          # the document's key
    bytes: int                        # its gzipped size
    sha256: str                       # of the gzipped bytes


def pointer_key(market: str, schema: int = INTEL_SCHEMA_VERSION) -> str:
    """The pointer's key.

    Args:
        market: Market id.
        schema: Schema version.

    Returns:
        "intel/<market>/latest-<schema>.json".
    """
    return f"intel/{market}/latest-{schema}.json"


def settings_to_dict(settings: MarketSettings) -> dict[str, object]:
    """MarketSettings as the document's `settings` (MarketSettings.to_dict, shared with the desktop).

    Args:
        settings: The analysis settings.

    Returns:
        Field name -> value.
    """
    return settings.to_dict()


def settings_from_dict(values: dict[str, object]) -> MarketSettings:
    """The document's `settings` back as MarketSettings (MarketSettings.from_dict, shared with the desktop).

    Args:
        values: From settings_to_dict.

    Returns:
        The settings; the defaults when empty.
    """
    return MarketSettings.from_dict(values)


# The schemas published side by side, oldest first (SPEC §2.4, BZ37): the current one, and every older one a release in
# use still reads. Only 13 since SM2a (BZ46): the releases that read 10 to 12 (v0.1.1 to v0.1.5) refuse a document over
# 64 MiB, and those documents passed 79 MiB on 2026-11-08, so they served no one. Drop a schema once every release in
# use reads a newer one.
OLDEST_PUBLISHED = 13
PUBLISHED_SCHEMAS = tuple(range(OLDEST_PUBLISHED, INTEL_SCHEMA_VERSION + 1))
# What read_latest tries, newest first: the published ones, then the one before them, which is what the last daily run
# wrote just after a bump (FM75).
_READ_SCHEMAS = (*reversed(PUBLISHED_SCHEMAS), OLDEST_PUBLISHED - 1)


def publish(store: Store, intel: MarketIntelligence, schemas: tuple[int, ...] = PUBLISHED_SCHEMAS) -> list[Pointer]:
    """Write the document in each schema asked for, then each pointer (SPEC §2.4, BZ15, BZ18).

    Args:
        store: The intel store.
        intel: The intelligence.
        schemas: The schemas to publish: its own, plus the previous one during a transition.

    Returns:
        The pointers written, in the order given.
    """
    pointers = []
    for schema in schemas:
        data = gzip.compress(to_json(intel, schema).encode("utf-8"), mtime=0)
        digest = hashlib.sha256(data).hexdigest()
        key = f"intel/{intel.market}/{digest}.json.gz"
        store.put(key, data)                 # named by its hash: rewriting it writes the same bytes
        pointers.append(Pointer(schema, intel.built_ms, intel.observations_until_ms, key, len(data), digest))
    for pointer in pointers:
        store.put(pointer_key(intel.market, pointer.schema),
                  json.dumps(dataclasses.asdict(pointer), sort_keys=True).encode("utf-8"))
    return pointers


def read_latest(store: Store, market: str) -> tuple[MarketIntelligence, Pointer] | None:
    """The newest published document, checked against its pointer: this schema's, else the newest older one's.

    Right after a schema bump only the older pointers exist until a daily run publishes the new one; reading one
    (with `from_json`'s defaults for the new fields) keeps the hourly prices moving meanwhile (FM75).

    Args:
        store: The intel store.
        market: Market id.

    Returns:
        (the intelligence, lifted to this schema, and its pointer); None before the first publish.

    Raises:
        ValueError: The document is missing or its hash differs from the pointer's (a torn or tampered write).
    """
    found = next((f for f in (store.get(pointer_key(market, s)) for s in _READ_SCHEMAS) if f), None)
    if found is None:
        return None
    pointer = Pointer(**json.loads(found.data))
    document = store.get(pointer.key)
    if document is None or hashlib.sha256(document.data).hexdigest() != pointer.sha256:
        raise ValueError(f"{pointer.key}: missing, or not the document the pointer names")
    intel = from_json(gzip.decompress(document.data).decode("utf-8"))
    # additive schemas: an older document is a current one with the new fields at their defaults (FM75)
    return dataclasses.replace(intel, schema_version=max(intel.schema_version, INTEL_SCHEMA_VERSION)), pointer
