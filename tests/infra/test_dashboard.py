"""Tests for infra/dashboard.py: the market's CloudWatch dashboard (SPEC §2.4 The status page, CS1).

Skipped where aws-cdk-lib is not installed (requirements-infra.txt), like test_market_stack.py.
"""
from __future__ import annotations

import json

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Template  # noqa: E402

from infra.dashboard import dashboard_name  # noqa: E402
from infra.market_stack import MarketConfig, MarketStack  # noqa: E402

CONFIG = MarketConfig(market="forever.normal.horde.us", ruleset=2, house=113, alert_email="alerts@example.com")


@pytest.fixture(scope="module")
def stack_and_body(tmp_path_factory):
    """The synthesized template and the dashboard body resolved to text (tokens left as their JSON)."""
    asset = tmp_path_factory.mktemp("lambda")
    (asset / "placeholder.py").write_text("")
    app = cdk.App()
    stack = MarketStack(app, "Test", MarketConfig(**{**CONFIG.__dict__, "asset_path": str(asset)}),
                        env=cdk.Environment(account="111111111111", region="us-east-1"))
    template = Template.from_stack(stack)
    [dashboard] = template.find_resources("AWS::CloudWatch::Dashboard").values()
    return template, dashboard["Properties"]


def body_text(props: dict) -> str:
    """The dashboard body's literal parts joined, with each token as its JSON, for substring checks."""
    parts = props["DashboardBody"]["Fn::Join"][1]
    return "".join(p if isinstance(p, str) else json.dumps(p) for p in parts)


def test_CS1_one_dashboard_shows_alarms_publishes_functions_queue_warnings_and_logs(stack_and_body):
    template, props = stack_and_body
    assert props["DashboardName"] == "WowAuction-forever-normal-horde-us"
    body = body_text(props)
    alarm_ids = [key for key in template.find_resources("AWS::CloudWatch::Alarm")]
    assert len(alarm_ids) == 7 and all(alarm_id in body for alarm_id in alarm_ids)
    for name in ("snapshot_written", "hourly_published", "daily_published", "daily_refused", "hourly_no_base",
                 "mismatch"):
        assert f'\\"{name}\\"' in body or f'"{name}"' in body, name
    for metric in ("Errors", "Invocations", "Throttles", "Duration", "ApproximateNumberOfMessagesVisible"):
        assert metric in body, metric
    assert '"value":120000' in body and '"value":900000' in body          # the two timeouts annotated
    assert "filter @message like /WARNING|ERROR/" in body and "AnalyzerLogs" in body
    assert "infra/README.md" in body and '"start":"-P7D"' in body


def test_CS1_the_dashboard_name_has_no_dots():
    assert dashboard_name("forever.pvp.alliance.us") == "WowAuction-forever-pvp-alliance-us"
