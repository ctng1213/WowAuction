"""Tests for ahcloud/credentials.py: where the Blizzard credentials come from (plan decision 17)."""
from __future__ import annotations

import json

import pytest

from ahcloud.credentials import CredentialsMissing, from_secret, key_file, lookup

ENV = {"BLIZZARD_CLIENT_ID": "env-id", "BLIZZARD_CLIENT_SECRET": "env-secret"}


def write_key_file(home, text):
    key_file(home).parent.mkdir(parents=True, exist_ok=True)
    key_file(home).write_text(text, encoding="utf-8")


def test_the_environment_then_the_key_file_then_the_secret(tmp_path):
    write_key_file(tmp_path, "file-id\nfile-secret\n")
    calls = []

    def secret(name):
        calls.append(name)
        return json.dumps({"client_id": "sm-id", "client_secret": "sm-secret"})

    assert lookup(ENV, tmp_path, "wowauction/blizzard-api", secret).client_id == "env-id"
    assert lookup({}, tmp_path, "wowauction/blizzard-api", secret).client_id == "file-id"
    key_file(tmp_path).unlink()
    found = lookup({}, tmp_path, "wowauction/blizzard-api", secret)
    assert (found.client_id, found.secret) == ("sm-id", "sm-secret") and calls == ["wowauction/blizzard-api"]


def test_nothing_found_says_where_and_shows_no_value(tmp_path):
    write_key_file(tmp_path, "only-one-line\n")
    with pytest.raises(CredentialsMissing) as error:
        lookup({"BLIZZARD_CLIENT_ID": "half"}, tmp_path)
    text = str(error.value)
    assert "BLIZZARD_CLIENT_SECRET" in text and str(key_file(tmp_path)) in text
    assert "only-one-line" not in text and "half" not in text


def test_a_malformed_secret_names_the_secret_not_its_value():
    with pytest.raises(CredentialsMissing, match="wowauction/blizzard-api must be JSON") as error:
        from_secret("wowauction/blizzard-api", lambda name: '{"client_id": "leaky"}')
    assert "leaky" not in str(error.value)


def test_the_credentials_repr_hides_both_values():
    found = lookup(ENV, __import__("pathlib").Path("/nonexistent"))
    assert "env-id" not in repr(found) and "env-secret" not in repr(found)
