"""Blizzard client credentials for the cloud job, wherever it runs (SPEC §2.4, plan decision 17).

In order: the environment (BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET), the key file ~/.ahsync/blizzard (two
lines: the id, then the secret), or a Secrets Manager secret (JSON with client_id and client_secret) read
through an injected function. Never a command-line value; a value is never logged, echoed or put in an
exception's text.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ENV_ID, ENV_SECRET = "BLIZZARD_CLIENT_ID", "BLIZZARD_CLIENT_SECRET"
WHERE = f"set {ENV_ID} and {ENV_SECRET}, or ~/.ahsync/blizzard"


class CredentialsMissing(Exception):
    """No complete pair of credentials was found; the message says where to put them and holds no value."""


@dataclass(frozen=True)
class Credentials:
    """The Battle.net client id and secret. Their repr hides both."""

    client_id: str = field(repr=False)
    secret: str = field(repr=False)


def key_file(home: Path) -> Path:
    """The key file's path.

    Args:
        home: The user's home folder.

    Returns:
        <home>/.ahsync/blizzard.
    """
    return home / ".ahsync" / "blizzard"


def from_environment(env: Mapping[str, str]) -> Credentials | None:
    """Credentials from the environment.

    Args:
        env: The environment.

    Returns:
        Both values when both are set and non-empty; else None.
    """
    client_id, secret = env.get(ENV_ID, "").strip(), env.get(ENV_SECRET, "").strip()
    return Credentials(client_id, secret) if client_id and secret else None


def from_key_file(home: Path) -> Credentials | None:
    """Credentials from ~/.ahsync/blizzard.

    Args:
        home: The user's home folder.

    Returns:
        The first two non-blank lines, stripped; None when the file is missing or has fewer.
    """
    path = key_file(home)
    if not path.is_file():
        return None
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return Credentials(lines[0], lines[1]) if len(lines) >= 2 else None


def from_secret(name: str, get_secret: Callable[[str], str]) -> Credentials:
    """Credentials from a Secrets Manager secret.

    Args:
        name: The secret's name, e.g. "wowauction/blizzard-api".
        get_secret: Returns the secret's string value (ahcloud.aws.get_secret, or a fake).

    Returns:
        Its client_id and client_secret.

    Raises:
        CredentialsMissing: The secret is not JSON or lacks a key (its name in the message, never a value).
    """
    try:
        data = json.loads(get_secret(name))
        return Credentials(str(data["client_id"]).strip(), str(data["client_secret"]).strip())
    except (ValueError, KeyError, TypeError):
        raise CredentialsMissing(f"secret {name} must be JSON with client_id and client_secret") from None


def lookup(env: Mapping[str, str], home: Path, secret_name: str | None = None,
           get_secret: Callable[[str], str] | None = None) -> Credentials:
    """The credentials, from the first place that has them.

    Args:
        env: The environment.
        home: The user's home folder.
        secret_name: A Secrets Manager secret to use when the environment and key file have none.
        get_secret: Reads a secret's value; required with secret_name.

    Returns:
        The credentials.

    Raises:
        CredentialsMissing: None of the places has a complete pair.
    """
    found = from_environment(env) or from_key_file(home)
    if found:
        return found
    if secret_name and get_secret:
        return from_secret(secret_name, get_secret)
    raise CredentialsMissing(f"no Blizzard credentials: {WHERE} ({key_file(home)})")
