"""Tests for ahcloud/publish.py: the document by its hash, the pointer written last (SPEC §2.4, BZ15, BZ18)."""
from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from ahcloud.publish import pointer_key, publish, read_latest, settings_from_dict, settings_to_dict
from ahcloud.store import LocalStore
from ahsync.engine.sellprob import MarketSettings
from ahsync.engine.volume import VolumePrior
from ahsync.intel.contract import INTEL_SCHEMA_VERSION, MarketIntelligence, from_json

M = "forever.normal.horde.us"
INTEL = MarketIntelligence(M, 1_000, 2_000, 4, {}, catalog={2589: ("Linen Cloth", 1, 7, 13, None)},
                           coverage=((1, 2),), settings={"default_sell_prob": 0.3})


def test_BZ15_the_document_is_named_by_its_hash_and_the_pointer_says_so(tmp_path):
    store = LocalStore(tmp_path)
    *_, pointer = publish(store, INTEL)                                         # the current schema's is last
    data = store.get(pointer.key).data
    assert pointer.key == f"intel/{M}/{hashlib.sha256(data).hexdigest()}.json.gz" and pointer.bytes == len(data)
    assert json.loads(store.get(pointer_key(M)).data) == {
        "schema": INTEL_SCHEMA_VERSION, "built_ms": 1_000, "observations_until_ms": 2_000, "key": pointer.key, "bytes": len(data),
        "sha256": pointer.sha256}
    assert from_json(gzip.decompress(data).decode()) == INTEL
    assert read_latest(store, M) == (INTEL, pointer)
    assert read_latest(store, "forever.pvp.horde.us") is None


def test_BZ18_a_transition_publishes_the_previous_schema_too(tmp_path):
    store = LocalStore(tmp_path)
    new, old = publish(store, INTEL, schemas=(10, 9))
    assert (new.schema, old.schema) == (10, 9) and new.key != old.key
    body = json.loads(gzip.decompress(store.get(old.key).data))
    assert body["schema_version"] == 9 and "catalog" not in body
    assert json.loads(store.get(pointer_key(M, 9)).data)["key"] == old.key


def test_BZ37_the_cloud_publishes_every_schema_a_release_reads_side_by_side(tmp_path):
    store = LocalStore(tmp_path)
    *older, new = pointers = publish(store, INTEL)
    assert [p.schema for p in pointers] == [10, 11, 12] and len({p.key for p in pointers}) == 3   # v0.1.2 reads 10
    for pointer in pointers:
        assert json.loads(store.get(pointer_key(M, pointer.schema)).data)["key"] == pointer.key
        assert json.loads(gzip.decompress(store.get(pointer.key).data))["schema_version"] == pointer.schema
    assert read_latest(store, M) == (INTEL, new)                                   # AHSync reads its own schema


def test_a_document_that_does_not_match_its_pointer_is_refused(tmp_path):
    store = LocalStore(tmp_path)
    *_, pointer = publish(store, INTEL)
    store.put(pointer.key, gzip.compress(b"{}", mtime=0))                      # torn or tampered
    with pytest.raises(ValueError, match="not the document the pointer names"):
        read_latest(store, M)


def test_settings_travel_in_the_document_and_empty_means_the_defaults():
    settings = MarketSettings(default_sell_prob=0.3, volume_prior=VolumePrior(2.0, 4))
    assert settings_from_dict(json.loads(json.dumps(settings_to_dict(settings)))) == settings
    assert settings_from_dict({}) == MarketSettings()
