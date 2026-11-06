"""Run the cloud job on this machine: `python -m ahcloud collect --store data/cloud --house 113` (plan decision 17).

The same `run()` the Lambda calls, against a folder (`--store data/cloud`) or the real bucket
(`--store s3://<bucket>`, with your own AWS credentials). Blizzard credentials come from BLIZZARD_CLIENT_ID and
BLIZZARD_CLIENT_SECRET, else ~/.ahsync/blizzard, else `--secret <name>` through Secrets Manager; a credential
on the command line is refused. `analyze` and `rebuild` arrive with M31.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ahcloud import aws
from ahcloud.collector import Settings, run
from ahcloud.credentials import WHERE, CredentialsMissing, lookup
from ahcloud.store import LocalStore, Store
from ahsync.clock import SystemClock
from ahsync.collect.blizzard import BlizzardClient, BlizzardError
from ahsync.collect.client import Transport, TransportError, UrllibTransport

REFUSED = ("--client-id", "--client-secret", "--client_id", "--client_secret", "--token", "--password")


def open_store(where: str) -> Store:
    """The store a --store value names.

    Args:
        where: "s3://<bucket>" or a folder.

    Returns:
        An S3Store or a LocalStore.
    """
    if where.startswith("s3://"):
        return aws.S3Store(where.removeprefix("s3://").strip("/"))
    return LocalStore(Path(where))


def parser() -> argparse.ArgumentParser:
    """The command-line parser; no option takes a credential.

    Returns:
        The parser.
    """
    top = argparse.ArgumentParser(prog="python -m ahcloud", description="Run the cloud job locally (SPEC §2.4).")
    sub = top.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="fetch the house and store a new snapshot")
    collect.add_argument("--store", default="data/cloud", help="a folder, or s3://<bucket>")
    collect.add_argument("--market", default="forever.normal.horde.us")
    collect.add_argument("--ruleset", type=int, default=2)
    collect.add_argument("--house", type=int, required=True, help="the auction house id (SPEC §6 item 23)")
    collect.add_argument("--secret", help="a Secrets Manager secret's name, used when no local credentials exist")
    return top


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, home: Path | None = None,
         transport: Transport | None = None) -> int:
    """Run a subcommand.

    Args:
        argv: Arguments (sys.argv[1:] when None).
        env: The environment (os.environ when None).
        home: The home folder (Path.home() when None).
        transport: The HTTP transport (UrllibTransport when None).

    Returns:
        0 done, 1 a failed run, 2 a refused argument or missing credentials.
    """
    argv = sys.argv[1:] if argv is None else argv
    if any(a.split("=")[0] in REFUSED for a in argv):
        print(f"error: credentials are never taken on the command line: {WHERE}", file=sys.stderr)
        return 2
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        found = lookup(dict(os.environ) if env is None else env, home or Path.home(), args.secret, aws.get_secret)
    except CredentialsMissing as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    clock = SystemClock()
    client = BlizzardClient(transport or UrllibTransport(), clock, found.client_id, found.secret)
    try:
        outcome = run(open_store(args.store), client, clock, Settings(args.market, args.ruleset, args.house),
                      lambda name, value: print(f"metric {name} = {value}"))
    except (BlizzardError, TransportError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(f"{outcome.state}: {outcome.key or 'nothing written'} ({outcome.auctions} auctions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
