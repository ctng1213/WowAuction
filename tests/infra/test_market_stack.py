"""Tests for infra/market_stack.py: the template's security boundaries and wiring (SPEC §2.4, SEC17).

Skipped where aws-cdk-lib is not installed (it is an infra-only dependency, requirements-infra.txt), so the
regular CI test job is unchanged.
"""
from __future__ import annotations

import json

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Match, Template  # noqa: E402

from infra.market_stack import MarketConfig, MarketStack  # noqa: E402

CONFIG = MarketConfig(market="forever.normal.horde.us", ruleset=2, house=113, alert_email="alerts@example.com")


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """The synthesized template, with an empty folder as the code asset."""
    asset = tmp_path_factory.mktemp("lambda")
    (asset / "placeholder.py").write_text("")
    app = cdk.App()
    stack = MarketStack(app, "Test", MarketConfig(**{**CONFIG.__dict__, "asset_path": str(asset)}),
                        env=cdk.Environment(account="111111111111", region="us-east-1"))
    return Template.from_stack(stack)


def test_SEC17_both_buckets_block_public_access_and_require_tls(template):
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 2
    for bucket in buckets.values():
        assert bucket["Properties"]["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True}
        assert bucket["DeletionPolicy"] == "Retain"
    policies = [p["Properties"]["PolicyDocument"]["Statement"] for p in template.find_resources("AWS::S3::BucketPolicy").values()]
    assert all(any(s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false" and s["Effect"] == "Deny"
                   for s in statements) for statements in policies)


def test_SEC17_only_cloudfront_reads_and_only_the_intel_bucket(template):
    reads = [(policy["Properties"]["Bucket"], s) for policy in template.find_resources("AWS::S3::BucketPolicy").values()
             for s in policy["Properties"]["PolicyDocument"]["Statement"] if s["Effect"] == "Allow"]
    assert len(reads) == 1
    bucket, statement = reads[0]
    assert bucket == {"Ref": next(k for k in template.find_resources("AWS::S3::Bucket") if k.startswith("Intel"))}
    assert statement["Principal"] == {"Service": "cloudfront.amazonaws.com"} and statement["Action"] == "s3:GetObject"
    assert "AWS:SourceArn" in json.dumps(statement["Condition"])
    template.has_resource_properties("AWS::CloudFront::Distribution", {"DistributionConfig": Match.object_like(
        {"DefaultCacheBehavior": Match.object_like({"ViewerProtocolPolicy": "https-only"})})})


def test_SEC17_the_collector_environment_holds_names_only_and_its_role_names_one_secret(template):
    [function] = [f for f in template.find_resources("AWS::Lambda::Function").values()
                  if f["Properties"].get("Handler") == "ahcloud.collector.handler"]
    props = function["Properties"]
    assert set(props["Environment"]["Variables"]) == {"BUCKET", "MARKET", "RULESET", "HOUSE", "SECRET_NAME"}
    assert props["Environment"]["Variables"]["SECRET_NAME"] == "wowauction/blizzard-api"
    assert (props["Runtime"], props["Architectures"], props["ReservedConcurrentExecutions"]) == ("python3.12", ["arm64"], 1)
    text = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "secret:wowauction/blizzard-api-??????" in text and '"secretsmanager:*"' not in text
    assert '"s3:*"' not in text and "secretsmanager:GetSecretValue" in text


def test_the_collector_runs_every_15_minutes_without_retries(template):
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "rate(15 minutes)",
        "Targets": Match.array_with([Match.object_like({"RetryPolicy": {"MaximumRetryAttempts": 0}})])})


def test_alarms_and_the_budget_email_the_user(template):
    template.has_resource_properties("AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "alerts@example.com"})
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    stale = next(a["Properties"] for a in alarms.values() if a["Properties"].get("MetricName") == "snapshot_written")
    assert (stale["Namespace"], stale["EvaluationPeriods"], stale["Period"], stale["TreatMissingData"]) == (
        "WowAuction", 3, 3600, "breaching")
    assert all(a["Properties"]["AlarmActions"] for a in alarms.values())
    template.has_resource_properties("AWS::Budgets::Budget", {"Budget": Match.object_like(
        {"BudgetLimit": {"Amount": 5, "Unit": "USD"}, "TimeUnit": "MONTHLY"})})
    [budget] = template.find_resources("AWS::Budgets::Budget").values()
    services = budget["Properties"]["Budget"]["FilterExpression"]["Dimensions"]
    assert services["Key"] == "SERVICE" and "AWS Lambda" in services["Values"] and "Amazon Elastic Compute Cloud - Compute" not in services["Values"]


def analyzer_of(template):
    """The analyzer function's properties."""
    [function] = [f for f in template.find_resources("AWS::Lambda::Function").values()
                  if f["Properties"].get("Handler") == "ahcloud.analyzer.handler"]
    return function["Properties"]


def test_SEC17_the_analyzer_environment_holds_names_only(template):
    props = analyzer_of(template)
    assert set(props["Environment"]["Variables"]) == {"DATA_BUCKET", "INTEL_BUCKET", "MARKET", "SECRET_NAME"}
    assert (props["ReservedConcurrentExecutions"], props["Timeout"], props["MemorySize"]) == (1, 900, 2048)
    assert props["EphemeralStorage"] == {"Size": 4096} and "TargetArn" in json.dumps(props["DeadLetterConfig"])


def test_BZ34_the_analyzer_runs_daily_hourly_and_on_every_new_raw_object(template):
    rules = [r["Properties"] for r in template.find_resources("AWS::Events::Rule").values()]
    inputs = {r["ScheduleExpression"]: json.loads(r["Targets"][0]["Input"]) for r in rules if "Input" in r["Targets"][0]}
    assert inputs == {"cron(0 9 * * ? *)": {"mode": "daily"}, "rate(1 hour)": {"mode": "hourly"}}
    notification = json.dumps(template.find_resources("Custom::S3BucketNotifications"))
    assert '"prefix"' in notification.lower() and "raw/" in notification and "s3:ObjectCreated:*" in notification
    template.has_resource_properties("AWS::Lambda::EventInvokeConfig", {"MaximumRetryAttempts": 1})


def test_FM70_failures_and_stale_baselines_alarm(template):
    alarms = {a["Properties"]["AlarmDescription"]: a["Properties"] for a in template.find_resources("AWS::CloudWatch::Alarm").values()}
    assert len(alarms) == 5
    baselines = next(a for d, a in alarms.items() if "36 hours" in d)
    assert (baselines["MetricName"], baselines["Period"] * baselines["EvaluationPeriods"]) == ("daily_published", 36 * 3600)
    assert any("dead-letter" in d for d in alarms)
