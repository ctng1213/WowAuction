"""Tests for infra/market_stack.py: the template's security boundaries and wiring (SPEC §2.4, SEC17, SEC22).

Skipped where aws-cdk-lib is not installed (it is an infra-only dependency, requirements-infra.txt), so the
regular CI test job is unchanged.
"""
from __future__ import annotations

import json

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Match, Template  # noqa: E402

from ahcloud.status import ALARM_IDS, DAILY_HOUR_UTC, TIMEOUTS_S  # noqa: E402
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
        {"BudgetLimit": {"Amount": 20, "Unit": "USD"}, "TimeUnit": "MONTHLY"})})
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
    variables = props["Environment"]["Variables"]
    assert set(variables) == {"DATA_BUCKET", "INTEL_BUCKET", "MARKET", "ALARM_PREFIX", "COLLECTOR_FUNCTION",
                              "INGEST_FUNCTION"}                                                 # no secret (SEC22)
    assert variables["ALARM_PREFIX"] == "Test-"
    assert (props["ReservedConcurrentExecutions"], props["Timeout"], props["MemorySize"]) == (1, 900, 8192)
    assert props["EphemeralStorage"] == {"Size": 4096} and "TargetArn" in json.dumps(props["DeadLetterConfig"])


def test_BZ34_the_analyzer_runs_daily_hourly_and_on_every_new_raw_object(template):
    rules = [r["Properties"] for r in template.find_resources("AWS::Events::Rule").values()]
    inputs = {r["ScheduleExpression"]: json.loads(r["Targets"][0]["Input"]) for r in rules if "Input" in r["Targets"][0]}
    assert inputs == {"cron(0 9 * * ? *)": {"mode": "daily"}, "rate(1 hour)": {"mode": "hourly"}}
    notification = json.dumps(template.find_resources("Custom::S3BucketNotifications"))
    assert '"prefix"' in notification.lower() and "raw/" in notification and "s3:ObjectCreated:*" in notification
    template.has_resource_properties("AWS::Lambda::EventInvokeConfig", {"MaximumRetryAttempts": 1})


def alarms_of(template):
    """The stack's alarms by description."""
    return {a["Properties"]["AlarmDescription"]: a["Properties"] for a in template.find_resources("AWS::CloudWatch::Alarm").values()}


def test_FM70_failures_and_stale_baselines_alarm(template):
    alarms = alarms_of(template)
    assert len(alarms) == 10
    assert any("dead-letter" in d for d in alarms) and any("analyzer failed" in d for d in alarms)


def test_FM79_no_daily_baselines_for_27_hours_alarms(template):
    baselines = next(a for d, a in alarms_of(template).items() if "No daily baselines" in d)
    assert (baselines["MetricName"], baselines["Period"], baselines["EvaluationPeriods"]) == ("daily_published", 3600, 27)
    assert (baselines["Statistic"], baselines["Threshold"], baselines["ComparisonOperator"], baselines["TreatMissingData"]) == (
        "Sum", 1, "LessThanThreshold", "breaching")
    assert "27 hours" in next(d for d in alarms_of(template) if "No daily baselines" in d)
    assert baselines["AlarmActions"] and baselines["OKActions"]


def test_FM80_an_analyzer_run_over_12_minutes_alarms(template):
    slow = next(a for d, a in alarms_of(template).items() if "An analyzer run took over 12 minutes" in d)
    assert (slow["Namespace"], slow["MetricName"], slow["Statistic"]) == ("AWS/Lambda", "Duration", "Maximum")
    assert (slow["Threshold"], slow["ComparisonOperator"], slow["TreatMissingData"]) == (
        720000, "GreaterThanThreshold", "notBreaching")
    assert slow["Dimensions"][0]["Name"] == "FunctionName" and "Analyzer" in json.dumps(slow["Dimensions"])
    assert slow["AlarmActions"] and slow["OKActions"]


def test_FM77_no_published_summary_for_3_hours_alarms(template):
    alarms = {a["Properties"]["AlarmDescription"]: a["Properties"] for a in template.find_resources("AWS::CloudWatch::Alarm").values()}
    alarm = next(a for d, a in alarms.items() if "No market summary published" in d)
    assert (alarm["EvaluationPeriods"], alarm["Threshold"], alarm["ComparisonOperator"], alarm["TreatMissingData"]) == (
        3, 1, "LessThanThreshold", "breaching")
    expression = next(m for m in alarm["Metrics"] if "Expression" in m)
    assert expression["Expression"].replace(" ", "") == "FILL(hourly,0)+FILL(daily,0)" and expression["ReturnData"]
    inputs = {m["Id"]: m["MetricStat"] for m in alarm["Metrics"] if "MetricStat" in m}
    assert {i: (s["Metric"]["MetricName"], s["Metric"]["Namespace"], s["Period"], s["Stat"]) for i, s in inputs.items()} == {
        "hourly": ("hourly_published", "WowAuction", 3600, "Sum"), "daily": ("daily_published", "WowAuction", 3600, "Sum")}
    assert all(s["Metric"]["Dimensions"] == [{"Name": "market", "Value": "forever.normal.horde.us"}] for s in inputs.values())
    assert alarm["AlarmActions"] and alarm["OKActions"]


def function_named(template, handler):
    """A function's properties by its handler."""
    [function] = [f for f in template.find_resources("AWS::Lambda::Function").values()
                  if f["Properties"].get("Handler") == handler]
    return function["Properties"]


def statements_of(template, handler):
    """Every IAM statement on the role of the function with this handler."""
    role = function_named(template, handler)["Role"]["Fn::GetAtt"][0]
    return [s for p in template.find_resources("AWS::IAM::Policy").values()
            if {"Ref": role} in p["Properties"]["Roles"] for s in p["Properties"]["PolicyDocument"]["Statement"]]


def actions(statement):
    """A statement's actions as a list."""
    action = statement["Action"]
    return action if isinstance(action, list) else [action]


def writes(statements):
    """The resources of every S3 write or delete, as JSON text."""
    return [json.dumps(s["Resource"]) for s in statements
            if any(a.startswith(("s3:PutObject", "s3:DeleteObject", "s3:Abort")) for a in actions(s))]


def bucket_id(template, prefix):
    """The logical id of the bucket whose id starts with prefix ("Data" or "Intel")."""
    return next(k for k in template.find_resources("AWS::S3::Bucket") if k.startswith(prefix))


def test_SEC22_only_ingest_writes_the_history_and_the_analyzer_holds_no_secret(template):
    data, intel = bucket_id(template, "Data"), bucket_id(template, "Intel")
    ingest = statements_of(template, "ahcloud.ingest.handler")
    analyzer = statements_of(template, "ahcloud.analyzer.handler")
    assert any("secretsmanager:GetSecretValue" in actions(s) for s in ingest)
    assert not any(a.startswith("secretsmanager:") for s in analyzer for a in actions(s))
    assert all(data in w and "/state/*" in w for w in writes(ingest)) and writes(ingest)
    assert all((intel in w and data not in w) or (data in w and w.endswith('/state/analyzer.json"]]}'))
               for w in writes(analyzer)) and writes(analyzer)
    props = function_named(template, "ahcloud.ingest.handler")
    assert set(props["Environment"]["Variables"]) == {"DATA_BUCKET", "MARKET", "SECRET_NAME"}
    assert (props["ReservedConcurrentExecutions"], props["Timeout"], props["MemorySize"]) == (1, 900, 4096)
    assert props["EphemeralStorage"] == {"Size": 4096} and "DeadLetterConfig" not in props


def test_FM85_ingest_runs_at_10_and_40_past_and_never_retries(template):
    template.has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "cron(10,40 * * * ? *)",
        "Targets": Match.array_with([Match.object_like({"RetryPolicy": {"MaximumRetryAttempts": 0}})])})
    configs = [c["Properties"] for c in template.find_resources("AWS::Lambda::EventInvokeConfig").values()]
    assert sorted(c["MaximumRetryAttempts"] for c in configs) == [0, 1]                 # Ingest 0, the analyzer 1
    failed = next(a for d, a in alarms_of(template).items() if "Ingest run failed" in d)
    assert (failed["MetricName"], failed["Period"], failed["Threshold"]) == ("Errors", 3600, 1)
    slow = next(a for d, a in alarms_of(template).items() if "Ingest run took over 12 minutes" in d)
    assert (slow["MetricName"], slow["Threshold"]) == ("Duration", 720000) and "Ingest" in json.dumps(slow["Dimensions"])


def test_FM84_the_history_three_hours_behind_for_three_hours_alarms(template):
    behind = next(a for d, a in alarms_of(template).items() if "History more than 3 hours behind" in d)
    assert (behind["Namespace"], behind["MetricName"], behind["Statistic"], behind["Period"]) == (
        "WowAuction", "history_lag_minutes", "Maximum", 3600)
    assert (behind["Threshold"], behind["EvaluationPeriods"], behind["ComparisonOperator"], behind["TreatMissingData"]) == (
        180, 3, "GreaterThanThreshold", "breaching")
    assert behind["Dimensions"] == [{"Name": "market", "Value": "forever.normal.horde.us"}]
    assert behind["AlarmActions"] and behind["OKActions"]


def test_SEC21_the_analyzer_reads_only_its_stacks_alarms_and_writes_its_own_record(template):
    data = bucket_id(template, "Data")
    analyzer = statements_of(template, "ahcloud.analyzer.handler")
    cloudwatch = {a: s["Resource"] for s in analyzer for a in actions(s) if a.startswith("cloudwatch:")}
    assert set(cloudwatch) == {"cloudwatch:DescribeAlarms", "cloudwatch:GetMetricData"}
    assert cloudwatch["cloudwatch:GetMetricData"] == "*"               # no resource-level scoping exists for it
    assert "alarm:Test-*" in json.dumps(cloudwatch["cloudwatch:DescribeAlarms"])
    data_writes = [w for w in writes(analyzer) if data in w]
    assert len(data_writes) == 1 and "/state/analyzer.json" in data_writes[0]
    [record] = [s for s in analyzer if "/state/analyzer.json" in json.dumps(s["Resource"])]
    assert actions(record) == ["s3:PutObject"]                          # nothing else on its one key
    names = set(template.find_resources("AWS::CloudWatch::Alarm"))      # every alarm has an id the status knows
    assert all(any(name.startswith(known) for known in ALARM_IDS) for name in names) and len(names) == len(ALARM_IDS)


def test_CS6_the_status_timeouts_and_daily_hour_are_the_stacks(template):
    timeouts = {handler.split(".")[1]: function_named(template, handler)["Timeout"]
                for handler in ("ahcloud.collector.handler", "ahcloud.ingest.handler", "ahcloud.analyzer.handler")}
    assert timeouts == TIMEOUTS_S
    template.has_resource_properties("AWS::Events::Rule", {"ScheduleExpression": f"cron(0 {DAILY_HOUR_UTC} * * ? *)"})
