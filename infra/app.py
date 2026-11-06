"""The CDK app: `cdk deploy -c house=113 -c alert_email=you@example.com` from infra/ (infra/README.md).

Context keys: `house` and `alert_email` are required; `market` (forever.normal.horde.us), `ruleset` (2) and
`asset_path` (../build/lambda, from tools/build_lambda.py) have defaults. The account and region come from the
deploying shell's AWS credentials; the region must be us-east-1, where the Blizzard secret lives.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):                       # `cdk` runs this file as a script from infra/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aws_cdk as cdk  # noqa: E402  after the path fix-up

from infra.market_stack import MarketConfig, MarketStack  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def config_from(app: cdk.App) -> MarketConfig:
    """The market's settings from the app's context.

    Args:
        app: The CDK app.

    Returns:
        The config.

    Raises:
        SystemExit: A required context key is missing (house, alert_email).
    """
    missing = [k for k in ("house", "alert_email") if not app.node.try_get_context(k)]
    if missing:
        raise SystemExit("cdk deploy needs " + " ".join(f"-c {k}=..." for k in missing))
    get = app.node.try_get_context
    return MarketConfig(market=get("market") or "forever.normal.horde.us", ruleset=int(get("ruleset") or 2),
                        house=int(get("house")), alert_email=str(get("alert_email")),
                        asset_path=str(get("asset_path") or REPO / "build" / "lambda"))


def build(app: cdk.App) -> MarketStack:
    """Define the stack in an app.

    Args:
        app: The CDK app.

    Returns:
        The stack.
    """
    config = config_from(app)
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-east-1")
    return MarketStack(app, "WowAuctionMarket", config, env=env,
                       description=f"WowAuction cloud job for {config.market} (SPEC §2.4)")


if __name__ == "__main__":
    cdk_app = cdk.App()
    build(cdk_app)
    cdk_app.synth()
