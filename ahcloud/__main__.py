"""Run the cloud job on this machine with the same code as the Lambdas (SPEC §2.4, plan decision 17).

    python -m ahcloud collect --house 113 [--store data/cloud]
    python -m ahcloud ingest [--rebuild] [--store data/cloud] [--out <folder>]
    python -m ahcloud analyze --daily | --hourly [--store data/cloud] [--intel <store>] [--out <folder>]
    python -m ahcloud rebuild [--store data/cloud] [--intel <store>] [--out <folder>]

`ingest` takes every pending snapshot (no Lambda time limit here, so no cap); `rebuild` is a fresh uncapped
ingest, then the daily analysis. The analysis needs no credentials; collecting and ingesting do (the catalog).

A store is a folder or `s3://<bucket>` (your own AWS credentials). `--intel` is where the summaries go (the
same store as `--store` when left out). `--out <folder>` reproduces a cloud run without touching the cloud:
every read falls through to the stores, every write lands in the folder. Blizzard credentials come from
BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET, else ~/.ahsync/blizzard, else `--secret <name>` through Secrets
Manager; a credential on the command line is refused.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from ahcloud import aws
from ahcloud.collector import Settings, run
from ahcloud.credentials import WHERE, CredentialsMissing, lookup
from ahcloud.daily import run_daily
from ahcloud.hourly import run_hourly
from ahcloud.ingest import run_ingest
from ahcloud.store import LocalStore, OverlayStore, PreconditionFailed, Store
from ahsync.clock import SystemClock
from ahsync.collect.blizzard import BlizzardClient, BlizzardError
from ahsync.collect.client import Transport, TransportError, UrllibTransport
from ahsync.db.sqlite import DatabaseError

REFUSED = ("--client-id", "--client-secret", "--client_id", "--client_secret", "--token", "--password")


def open_store(where: str, out: str | None = None) -> Store:
    """The store a --store or --intel value names, behind an overlay folder when --out is given.

    Args:
        where: "s3://<bucket>" or a folder.
        out: A folder that takes every write, or None.

    Returns:
        An S3Store or a LocalStore, wrapped in an OverlayStore over `out`.
    """
    base: Store = aws.S3Store(where.removeprefix("s3://").strip("/")) if where.startswith("s3://") else LocalStore(Path(where))
    return OverlayStore(base, LocalStore(Path(out))) if out else base


def parser() -> argparse.ArgumentParser:
    """The command-line parser; no option takes a credential.

    Returns:
        The parser.
    """
    top = argparse.ArgumentParser(prog="python -m ahcloud", description="Run the cloud job locally (SPEC §2.4).")
    sub = top.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect", help="fetch the house and store a new snapshot")
    collect.add_argument("--house", type=int, required=True, help="the auction house id (SPEC §6 item 23)")
    collect.add_argument("--ruleset", type=int, default=2)
    ingest = sub.add_parser("ingest", help="every pending raw snapshot into the history")
    ingest.add_argument("--rebuild", action="store_true", help="into a fresh history, from the retention window (FM72)")
    analyze = sub.add_parser("analyze", help="run the analyzer once")
    which = analyze.add_mutually_exclusive_group(required=True)
    which.add_argument("--daily", action="store_true", help="the baselines from the history")
    which.add_argument("--hourly", action="store_true", help="the newest snapshot's prices over the latest document")
    rebuild = sub.add_parser("rebuild", help="replay every raw snapshot into a fresh history, then analyze (FM72)")
    for command in (collect, ingest, analyze, rebuild):
        command.add_argument("--store", default="data/cloud", help="the data store: a folder, or s3://<bucket>")
        command.add_argument("--market", default="forever.normal.horde.us")
        command.add_argument("--secret", help="a Secrets Manager secret's name, used when no local credentials exist")
    for command in (ingest, analyze, rebuild):
        command.add_argument("--out", help="a folder that takes every write, so the stores are only read")
    for command in (analyze, rebuild):
        command.add_argument("--intel", help="the summaries' store (default: the data store)")
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
    if args.command == "analyze":                                     # the analysis needs no credentials
        try:
            return _analyze(args)
        except DatabaseError as error:
            print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
    try:
        found = lookup(dict(os.environ) if env is None else env, home or Path.home(), args.secret, aws.get_secret)
    except CredentialsMissing as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    clock = SystemClock()
    client = BlizzardClient(transport or UrllibTransport(), clock, found.client_id, found.secret)
    try:
        return _run(args, client, clock)
    except PreconditionFailed:                                        # FM83: an Ingest run uploaded meanwhile
        print("error: another writer uploaded the history meanwhile; run it again "
              "(pause IngestSchedule first for a long rebuild, infra/README.md)", file=sys.stderr)
        return 1
    except (BlizzardError, TransportError, DatabaseError) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _analyze(args: argparse.Namespace) -> int:
    """The analyze subcommand: the daily analysis or the hourly mode.

    Args:
        args: The parsed arguments.

    Returns:
        0.
    """
    data, intel = open_store(args.store, args.out), open_store(args.intel or args.store, args.out)
    if args.hourly:
        outcome = run_hourly(data, intel, args.market)
        print(f"hourly: {outcome.state} (prices to {outcome.observations_until_ms})")
        return 0
    with tempfile.TemporaryDirectory(prefix="ahcloud-") as work:      # the run's scratch folder, like Lambda's /tmp
        result = run_daily(data, intel, args.market, Path(work) / "run")
    print(f"daily: {result.state}, {result.items} items")
    print(result.profile)
    return 0


def _run(args: argparse.Namespace, client: BlizzardClient, clock: SystemClock) -> int:
    """The collect, ingest and rebuild subcommands, with credentials in hand.

    Args:
        args: The parsed arguments.
        client: The Blizzard client.
        clock: The clock.

    Returns:
        0.
    """
    if args.command == "collect":
        outcome = run(open_store(args.store), client, clock, Settings(args.market, args.ruleset, args.house),
                      lambda name, value: print(f"metric {name} = {value}"))
        print(f"{outcome.state}: {outcome.key or 'nothing written'} ({outcome.auctions} auctions)")
        return 0
    data = open_store(args.store, args.out)
    fresh = args.command == "rebuild" or args.rebuild
    with tempfile.TemporaryDirectory(prefix="ahcloud-") as work:      # the run's scratch folder, like Lambda's /tmp
        ingested = run_ingest(data, client, args.market, Path(work) / "ingest", clock, cap=None, fresh=fresh)
        print(f"ingest: {ingested.state}, {ingested.ingested} snapshots ingested, {ingested.refused} refused")
        print(ingested.profile)
        if args.command == "rebuild":
            result = run_daily(data, open_store(args.intel or args.store, args.out), args.market, Path(work) / "run")
            print(f"rebuild: {result.state}, {result.items} items")
            print(result.profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
