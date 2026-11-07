"""The market stack: storage, the collector, the public summary's distribution and the alerts (SPEC §2.4, M30).

Two buckets, so the public/private line is a bucket boundary rather than a prefix condition:
- **data** (private): `raw/` (Blizzard's snapshots, kept forever, Standard-IA after 30 days) and `state/`.
- **intel** (read only by the CloudFront distribution, through origin access control): the published
  summaries; objects expire after 7 days.
The collector runs every 15 minutes; the analyzer runs daily at DAILY_HOUR_UTC for the baselines and on every
new raw object (with an hourly fallback schedule) for the state, failed events going to a dead-letter queue.
Both have reserved concurrency 1, environments of names only, and the one Blizzard secret. Alarms (either
function failing, the dead-letter queue not empty, no new snapshot for 3 hours, no daily baselines for 36
hours) and a monthly budget email the user through one SNS topic.
"""
from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import (
    Duration, RemovalPolicy, Size, Stack,
    aws_budgets as budgets,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as lambda_,
    aws_lambda_event_sources as event_sources,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions,
    aws_sqs as sqs,
)
from constructs import Construct

# The services this stack bills, as Cost Explorer names them, so the budget measures WowAuction alone and other
# resources in the account can't trip it (the user, 2026-11-06; the account's own budget watches everything).
BUDGET_SERVICES = ("AWS Lambda", "Amazon Simple Storage Service", "Amazon CloudFront", "AmazonCloudWatch",
                   "Amazon Simple Notification Service", "Amazon Simple Queue Service", "AWS Secrets Manager")
METRIC_NAMESPACE = "WowAuction"      # ahcloud.aws.NAMESPACE: the functions' metric lines



@dataclass(frozen=True)
class MarketConfig:
    """What one market's stack needs; from `cdk deploy -c ...` context (infra/app.py)."""

    market: str                      # AHSync's market id
    ruleset: int                     # the route's ruleset
    house: int                       # the auction house id (SPEC §6 item 23)
    alert_email: str                 # where alarms and the budget write
    secret_name: str = "wowauction/blizzard-api"
    asset_path: str = "build/lambda"  # tools/build_lambda.py's output
    budget_usd: int = 5
    daily_hour_utc: int = 9           # the daily run (SPEC §2.4)


class MarketStack(Stack):
    """One market's cloud job."""

    def __init__(self, scope: Construct, construct_id: str, config: MarketConfig, **kwargs) -> None:
        """Define the resources.

        Args:
            scope: The CDK app.
            construct_id: The stack's id.
            config: The market's settings.
            **kwargs: Passed to Stack (env, description).
        """
        super().__init__(scope, construct_id, **kwargs)
        self.data, self.intel = self._buckets()
        self.alerts = sns.Topic(self, "Alerts", display_name="WowAuction market alerts")
        self.alerts.add_subscription(subscriptions.EmailSubscription(config.alert_email))
        self.secret = secretsmanager.Secret.from_secret_name_v2(self, "BlizzardSecret", config.secret_name)
        self.collector = self._collector(config)
        self.analyzer, self.dead_letters = self._analyzer(config)
        self.distribution = self._distribution()
        self._alarms(config)
        self._budget(config)

    def _buckets(self) -> tuple[s3.Bucket, s3.Bucket]:
        """The private data bucket and the intel bucket.

        Returns:
            (data, intel).
        """
        common = dict(block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                      encryption=s3.BucketEncryption.S3_MANAGED, versioned=False,
                      object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED, removal_policy=RemovalPolicy.RETAIN)
        data = s3.Bucket(self, "Data", lifecycle_rules=[s3.LifecycleRule(
            id="raw-to-infrequent-access", prefix="raw/",
            transitions=[s3.Transition(storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                                       transition_after=Duration.days(30))])], **common)
        intel = s3.Bucket(self, "Intel", lifecycle_rules=[s3.LifecycleRule(
            id="expire-old-summaries", expiration=Duration.days(7))], **common)
        return data, intel

    def _collector(self, config: MarketConfig) -> lambda_.Function:
        """The collector function, its schedule and its grants.

        Args:
            config: The market's settings.

        Returns:
            The function.
        """
        log_group = logs.LogGroup(self, "CollectorLogs", retention=logs.RetentionDays.ONE_MONTH,
                                  removal_policy=RemovalPolicy.DESTROY)
        function = lambda_.Function(
            self, "Collector", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="ahcloud.collector.handler", code=lambda_.Code.from_asset(config.asset_path),
            timeout=Duration.minutes(2), memory_size=512, reserved_concurrent_executions=1, log_group=log_group,
            environment={"BUCKET": self.data.bucket_name, "MARKET": config.market, "RULESET": str(config.ruleset),
                         "HOUSE": str(config.house), "SECRET_NAME": config.secret_name})
        self.data.grant_read(function, "state/*")
        self.data.grant_put(function, "raw/*")
        self.data.grant_put(function, "state/collector.json")
        self.secret.grant_read(function)
        events.Rule(self, "CollectorSchedule", schedule=events.Schedule.rate(Duration.minutes(15)),
                    targets=[targets.LambdaFunction(function, retry_attempts=0)])
        return function

    def _analyzer(self, config: MarketConfig) -> tuple[lambda_.Function, sqs.Queue]:
        """The analyzer function: its schedules, the S3 event, its dead-letter queue and its grants (M31).

        Args:
            config: The market's settings.

        Returns:
            (the function, its dead-letter queue).
        """
        dead_letters = sqs.Queue(self, "AnalyzerDeadLetters", retention_period=Duration.days(14),
                                 enforce_ssl=True)
        log_group = logs.LogGroup(self, "AnalyzerLogs", retention=logs.RetentionDays.ONE_MONTH,
                                  removal_policy=RemovalPolicy.DESTROY)
        function = lambda_.Function(
            self, "Analyzer", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="ahcloud.analyzer.handler", code=lambda_.Code.from_asset(config.asset_path),
            timeout=Duration.minutes(15), memory_size=2048, ephemeral_storage_size=Size.gibibytes(4),
            reserved_concurrent_executions=1, log_group=log_group, dead_letter_queue=dead_letters,
            retry_attempts=1,
            environment={"DATA_BUCKET": self.data.bucket_name, "INTEL_BUCKET": self.intel.bucket_name,
                         "MARKET": config.market, "SECRET_NAME": config.secret_name})
        self.data.grant_read(function, "raw/*")
        self.data.grant_read_write(function, "state/*")
        self.intel.grant_read_write(function)
        self.secret.grant_read(function)
        function.add_event_source(event_sources.S3EventSource(
            self.data, events=[s3.EventType.OBJECT_CREATED], filters=[s3.NotificationKeyFilter(prefix="raw/")]))
        events.Rule(self, "AnalyzerDaily", schedule=events.Schedule.cron(minute="0", hour=str(config.daily_hour_utc)),
                    targets=[targets.LambdaFunction(function, retry_attempts=0,
                                                    event=events.RuleTargetInput.from_object({"mode": "daily"}))])
        events.Rule(self, "AnalyzerHourlyFallback", schedule=events.Schedule.rate(Duration.hours(1)),
                    targets=[targets.LambdaFunction(function, retry_attempts=0,
                                                    event=events.RuleTargetInput.from_object({"mode": "hourly"}))])
        return function, dead_letters

    def _distribution(self) -> cloudfront.Distribution:
        """CloudFront in front of the intel bucket only, HTTPS only, honouring each object's Cache-Control.

        Returns:
            The distribution.
        """
        cache = cloudfront.CachePolicy(
            self, "SummaryCache", default_ttl=Duration.seconds(60), min_ttl=Duration.seconds(0),
            max_ttl=Duration.minutes(5), enable_accept_encoding_gzip=True)
        return cloudfront.Distribution(
            self, "Summaries", comment="WowAuction public market summaries",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.intel),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD, cache_policy=cache))

    def _alarms(self, config: MarketConfig) -> None:
        """Email on failures, on 3 hours without a new snapshot or a published summary, and on stale baselines.

        Args:
            config: The market's settings.
        """
        action = cw_actions.SnsAction(self.alerts)
        errors = self.collector.metric_errors(period=Duration.minutes(15), statistic="Sum").create_alarm(
            self, "CollectorErrors", threshold=1, evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="The collector failed (FM67, FM68)")
        written = cloudwatch.Metric(namespace=METRIC_NAMESPACE, metric_name="snapshot_written",
                                    dimensions_map={"market": config.market}, period=Duration.hours(1),
                                    statistic="Sum")
        stale = written.create_alarm(
            self, "NoNewSnapshot", threshold=1, evaluation_periods=3,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description="No new snapshot for 3 hours (FM68)")
        analyzer_errors = self.analyzer.metric_errors(period=Duration.hours(1), statistic="Sum").create_alarm(
            self, "AnalyzerErrors", threshold=1, evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="The analyzer failed (FM70, FM72)")
        dead = self.dead_letters.metric_approximate_number_of_messages_visible(
            period=Duration.minutes(15), statistic="Maximum").create_alarm(
            self, "AnalyzerDeadLettersWaiting", threshold=1, evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="An analyzer event failed twice and waits in the dead-letter queue (FM70)")
        daily = cloudwatch.Metric(namespace=METRIC_NAMESPACE, metric_name="daily_published",
                                  dimensions_map={"market": config.market}, period=Duration.hours(6), statistic="Sum")
        baselines = daily.create_alarm(
            self, "NoDailyBaselines", threshold=1, evaluation_periods=6,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description="No daily baselines published for 36 hours (S24)")
        for alarm in (errors, stale, analyzer_errors, dead, baselines, self._no_summary_alarm(config)):
            alarm.add_alarm_action(action)
            alarm.add_ok_action(action)

    def _no_summary_alarm(self, config: MarketConfig) -> cloudwatch.Alarm:
        """The alarm for 3 hours without a published market summary, hourly or daily (SPEC §2.4 Alerts, FM77).

        The two publishes are summed per hour: the hour a daily run publishes, the hourly run may rightly find
        nothing new, so neither metric alone is a stall. Missing data counts as missing publishes.

        Args:
            config: The market's settings.

        Returns:
            The alarm, without actions (the caller adds them).
        """
        total = cloudwatch.MathExpression(expression="FILL(hourly, 0) + FILL(daily, 0)",
                                          using_metrics={"hourly": hourly_sum(config.market, "hourly_published"),
                                                         "daily": hourly_sum(config.market, "daily_published")},
                                          period=Duration.hours(1), label="summaries published")
        return total.create_alarm(
            self, "NoMarketSummary", threshold=1, evaluation_periods=3,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description="No market summary published for 3 hours: snapshots may arrive while the analyzer stalls (FM77)")

    def _budget(self, config: MarketConfig) -> None:
        """A monthly cost budget on this stack's services that emails at 80% actual and 100% forecast.

        Args:
            config: The market's settings.
        """
        subscriber = [budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=config.alert_email)]
        notes = [budgets.CfnBudget.NotificationWithSubscribersProperty(
            notification=budgets.CfnBudget.NotificationProperty(
                notification_type=kind, comparison_operator="GREATER_THAN", threshold=share, threshold_type="PERCENTAGE"),
            subscribers=subscriber) for kind, share in (("ACTUAL", 80), ("FORECASTED", 100))]
        budgets.CfnBudget(self, "MonthlyBudget", budget=budgets.CfnBudget.BudgetDataProperty(
            budget_type="COST", time_unit="MONTHLY", budget_name=f"wowauction-{config.market}",
            budget_limit=budgets.CfnBudget.SpendProperty(amount=config.budget_usd, unit="USD"),
            filter_expression=budgets.CfnBudget.ExpressionProperty(dimensions=budgets.CfnBudget.ExpressionDimensionValuesProperty(
                key="SERVICE", values=list(BUDGET_SERVICES)))),
            notifications_with_subscribers=notes)


def hourly_sum(market: str, name: str) -> cloudwatch.Metric:
    """One of the functions' count metrics for a market, summed per hour.

    Args:
        market: Market id (the metric's dimension).
        name: Metric name, e.g. "hourly_published".

    Returns:
        The metric.
    """
    return cloudwatch.Metric(namespace=METRIC_NAMESPACE, metric_name=name, dimensions_map={"market": market},
                             period=Duration.hours(1), statistic="Sum")

