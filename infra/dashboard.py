"""The market's CloudWatch dashboard: the drill-down behind the status page (SPEC §2.4 The status page, CS1, H1).

Configuration only: it reads the alarms, metrics and log group the market stack already has. Its metric bars
miss runs made locally (`python -m ahcloud` writes its metric lines to stdout); the status page reads S3.
"""
from __future__ import annotations

from aws_cdk import Duration, aws_cloudwatch as cloudwatch, aws_lambda as lambda_, aws_logs as logs, aws_sqs as sqs
from constructs import Construct

METRIC_NAMESPACE = "WowAuction"      # ahcloud.aws.NAMESPACE, as in market_stack
WIDTH = 24                           # a dashboard row's width in grid units
LOG_QUERY = ("fields @timestamp, @message", "filter @message like /WARNING|ERROR/", "sort @timestamp desc",
             "limit 50")


def dashboard_name(market: str) -> str:
    """The dashboard's name for a market: dashboard names allow no dots.

    Args:
        market: Market id, e.g. "forever.normal.horde.us".

    Returns:
        E.g. "WowAuction-forever-normal-horde-us".
    """
    return "WowAuction-" + market.replace(".", "-")


def market_metric(market: str, name: str) -> cloudwatch.Metric:
    """One of the functions' count metrics for a market, summed per hour.

    Args:
        market: Market id (the metric's dimension).
        name: Metric name, e.g. "snapshot_written".

    Returns:
        The metric, labelled with its name.
    """
    return cloudwatch.Metric(namespace=METRIC_NAMESPACE, metric_name=name, dimensions_map={"market": market},
                             period=Duration.hours(1), statistic="Sum", label=name)


def function_widgets(title: str, function: lambda_.Function, timeout: Duration) -> list[cloudwatch.IWidget]:
    """Two graphs for one function: its runs (errors, invocations, throttles) and its longest run.

    Args:
        title: The function's display name, e.g. "Collector".
        function: The Lambda function.
        timeout: Its timeout, drawn as a line on the duration graph.

    Returns:
        [runs graph, duration graph], each half a row wide.
    """
    hour = Duration.hours(1)
    runs = cloudwatch.GraphWidget(
        title=f"{title}: runs per hour", width=WIDTH // 2,
        left=[function.metric_errors(period=hour, statistic="Sum", label="errors"),
              function.metric_invocations(period=hour, statistic="Sum", label="invocations"),
              function.metric_throttles(period=hour, statistic="Sum", label="throttles")])
    duration = cloudwatch.GraphWidget(
        title=f"{title}: longest run (ms)", width=WIDTH // 2,
        left=[function.metric_duration(period=hour, statistic="Maximum", label="duration max")],
        left_annotations=[cloudwatch.HorizontalAnnotation(value=timeout.to_milliseconds(), label="timeout")])
    return [runs, duration]


def add_dashboard(scope: Construct, market: str, alarms: list[cloudwatch.Alarm],
                  functions: list[tuple[str, lambda_.Function, Duration]], dead_letters: sqs.Queue,
                  log_groups: list[logs.LogGroup]) -> cloudwatch.Dashboard:
    """The dashboard: alarm states, publishes, each function's runs and durations, the queue, warnings and logs.

    Args:
        scope: The market stack.
        market: Market id.
        alarms: Every alarm of the stack, in display order.
        functions: (display name, function, timeout) per function.
        dead_letters: The analyzer's dead-letter queue.
        log_groups: The analyzer's and Ingest's log groups, for the warning lines.

    Returns:
        The dashboard (7 days shown by default).
    """
    hour = Duration.hours(1)
    rows: list[list[cloudwatch.IWidget]] = [
        [cloudwatch.AlarmStatusWidget(title="Alarms", alarms=alarms, width=WIDTH)],
        [cloudwatch.GraphWidget(title="New snapshots per hour", width=WIDTH // 2,
                                left=[market_metric(market, "snapshot_written")]),
         cloudwatch.GraphWidget(title="Summaries published per hour", width=WIDTH // 2, stacked=True,
                                left=[market_metric(market, "hourly_published"),
                                      market_metric(market, "daily_published")])],
    ]
    rows += [function_widgets(title, function, timeout) for title, function, timeout in functions]
    rows.append([
        cloudwatch.GraphWidget(title="Dead-letter queue", width=WIDTH // 2, left=[
            dead_letters.metric_approximate_number_of_messages_visible(period=hour, statistic="Maximum",
                                                                       label="waiting")]),
        cloudwatch.GraphWidget(title="Warnings per hour", width=WIDTH // 2, left=[
            market_metric(market, name) for name in ("ingest_refused", "hourly_no_base", "mismatch")])])
    rows.append([cloudwatch.LogQueryWidget(title="Analyzer and Ingest warnings and errors", width=WIDTH, height=8,
                                           log_group_names=[group.log_group_name for group in log_groups],
                                           query_lines=list(LOG_QUERY))])
    rows.append([cloudwatch.TextWidget(width=WIDTH, height=3, markdown=(
        "**Recovering the cloud state**: see the section \"Recovering the cloud state (rebuild)\" in `infra/README.md`. "
        "The public status page (`status.html`) reads S3, so it also counts runs made locally; "
        "these metric bars do not."))])
    dashboard = cloudwatch.Dashboard(scope, "Dashboard", dashboard_name=dashboard_name(market),
                                     default_interval=Duration.days(7))
    for row in rows:
        dashboard.add_widgets(*row)
    return dashboard
