# The cloud job's infrastructure

The CDK app for the market's cloud job (SPEC §2.4, plan `docs/plans/blizzard-cloud.md`): two buckets (data, private; intel, served by CloudFront), the collector Lambda on a 15-minute schedule, CloudFront for the public summaries, an SNS topic for alerts, two alarms and a monthly budget. Region `us-east-1`, where the Blizzard secret `wowauction/blizzard-api` lives.

## Prerequisites

- Node.js 18 or newer and the CDK CLI (`npm install -g aws-cdk`).
- Python 3.12 with the infra requirements in their own environment, from the repository root (with pyenv,
  use the 3.12 interpreter's full path: `python3.12` is usually not on the PATH):

  ```sh
  ~/.pyenv/versions/3.12.16/bin/python -m venv .venv-infra
  .venv-infra/bin/pip install -r requirements-infra.txt -r requirements-dev.txt
  ```

  `cdk` runs `python3 app.py`, so activate the environment (`source .venv-infra/bin/activate`) in the shell that
  runs `cdk`; otherwise it fails with `No module named 'aws_cdk'`.

- AWS credentials for the account in your shell (`aws login`, or a profile).
- The secret `wowauction/blizzard-api` in `us-east-1`, a JSON object with `client_id` and `client_secret`.

## Deploy

From the repository root:

```sh
python tools/build_lambda.py                     # stage ahsync/ + ahcloud/ into build/lambda
source .venv-infra/bin/activate
cd infra
cdk bootstrap -c house=<id> -c alert_email=<you@example.com>   # once per account and region
cdk deploy -c house=<id> -c alert_email=<you@example.com>
```

`house` is the auction house id (SPEC §6 item 23), and `alert_email` receives the alarms and the budget warnings. AWS sends a confirmation email for the SNS subscription: click it, or no alert arrives. Optional context: `market` (default `forever.normal.horde.us`), `ruleset` (2).

Rebuild the asset before every deploy that changes `ahsync/` or `ahcloud/`; `cdk diff` shows what a deploy would change.

**Before deploying a change to the lifetimes or the history** (SPEC §2.4 "One snapshot at a time"), dry-run the cutover against the live history: copy it read-only, then let `tools/carry_check.py` rebuild the carried state in a copy and compare every row the analysis reads. Run it in a Linux container, not on a Mac: the stored rows were written on Lambda, and macOS's `math.exp` can differ from glibc's in the last bit (LESSONS 2026-11-11). It must print `"differences": 0`.

```sh
aws s3 cp s3://<data-bucket>/state/history.db check/history.db
aws s3 sync s3://<data-bucket>/state/snapshots/ check/snapshots/
docker run --rm -v "$PWD":/src:ro -v "$PWD/check":/check:ro -w /src python:3.12-slim \
  python tools/carry_check.py --db /check/history.db --snapshots /check/snapshots
```

The first Ingest run with the carried state then rebuilds it the same way (about 4 minutes on Lambda) and logs one line saying so; every later run reads only the database and the new raw. If that first run times out, pause `IngestSchedule`, run `python -m ahcloud ingest --store s3://<data-bucket>` once on your machine, and resume (as for a long rebuild below). To remove the stack, run `cdk destroy`. The buckets are kept (`RETAIN`), so the raw snapshots survive; delete them by hand only if you mean to lose the history.

## Running the job locally

The command line runs the same `run()` the Lambda calls (plan decision 17):

```sh
python -m ahcloud collect --house <id> --store data/cloud        # into a folder
python -m ahcloud collect --house <id> --store s3://<data-bucket> # into the real bucket, with your AWS credentials
```

Blizzard credentials are read from, in order: `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET` in the environment, `~/.ahsync/blizzard` (two lines: the client id, then the secret; `chmod 600`), or `--secret wowauction/blizzard-api` through Secrets Manager. A credential is never accepted on the command line.

The history has one writer, the Ingest function; the analysis only reads it (SPEC §2.4 Ingest). Locally:

```sh
python -m ahcloud ingest --store s3://<data-bucket>                                    # every pending snapshot, no cap
python -m ahcloud analyze --daily --store s3://<data-bucket> --intel s3://<intel-bucket>   # the baselines; no credentials
```

To catch the history up by hand (Ingest stalled, FM84), run `ingest` then `analyze --daily`; start well away from :10 and :40, or a scheduled run's upload may refuse yours (`PreconditionFailed`, FM83): run it again.

To reproduce a cloud run, point `--store` at a folder holding the same `raw/` and `state/` objects, or read the real buckets and write elsewhere with `--out <folder>`.

The `s3://` stores need boto3 on your machine, in its own environment (botocore needs `python-dateutil` < 3, which the infra environment's pin rules out):

```sh
python3.12 -m venv .venv-cloud && .venv-cloud/bin/pip install -r requirements-cloud.txt
aws login                                                     # your own AWS credentials
```

## Reading the dashboard

The stack creates a CloudWatch dashboard, `WowAuction-<market with dashes>` (e.g. `WowAuction-forever-normal-horde-us`), in the console under CloudWatch → Dashboards. It shows 7 days by default:

- **Alarms**: all ten, each emailing on ALARM and again on OK. `NoDailyBaselines` fires after 27 hours without a daily publish (09:00 UTC run, so about 13:00 the next day); `AnalyzerSlowRun` fires on any analyzer run over 12 of its 15 minutes, a warning before runs start timing out. `IngestErrors`, `IngestSlowRun` and `HistoryBehind` (the history 3 hours behind the newest raw, for 3 hours) are Ingest's.
- **New snapshots** and **summaries published** per hour: a gap in the first is Blizzard or the collector; a gap in the second while snapshots arrive is the analyzer.
- Per function: **runs** (errors, invocations, throttles) and the **longest run**, with the timeout drawn as a line.
- The **dead-letter queue**, the **warnings** (`ingest_refused`, `hourly_no_base`, `mismatch`), and the analyzer's and Ingest's last 50 WARNING or ERROR log lines.

The metric graphs count only runs in AWS: `python -m ahcloud` on your machine writes its metric lines to the terminal.

## The status document

After every run the analyzer writes `status/<market>.json` to the intel bucket (SPEC §2.4 The status page): the five SLAs, the 12 weeks of hours, the functions' last runs, freshness and the document. It is public through CloudFront (`https://<distribution>/status/<market>.json`) and holds no bucket, function or alarm name (SEC21); the page that draws it is H3. It reads `state/collector.json`, `state/ingest.json` and the analyzer's own `state/analyzer.json`, the stack's alarms (`DescribeAlarms` by name: each alarm is named `<stack>-<id>`, since a request by prefix is refused under the role's grant) and the last day's `Errors`, `mismatch` and `ingest_refused` (`GetMetricData`). A failed status write never fails the run: it logs one line and emits `status_failed` (FM81), and the status's age on the page is the signal. Collector and Ingest runs on your machine show up in it, because they write the same state files; a local `analyze` does not write it.

## Recovering the cloud state (rebuild)

A rebuild throws `state/history.db` and the compact snapshots away and replays the raw of the retention window (the last 90 days plus 1 day plus 66 h, FM76) into a fresh database. Use it when a run fails with `DatabaseError` (FM72), or to apply a changed volume method to the stored days at once; day to day Ingest keeps the history current.

1. **In the cloud**: invoke the **Ingest** function (not the analyzer) with `{"mode": "rebuild"}`. It starts a fresh history marked rebuilding until the newest raw, and its scheduled runs carry on at the rebuild cap (`REBUILD_SNAPSHOTS_PER_RUN`) until the history reaches the mark; meanwhile the daily analysis publishes nothing (`daily_rebuilding`) and the hourly prices carry on (BZ61). `NoDailyBaselines` may fire if it takes over a day, and `HistoryBehind` fires while the history is days behind the raw (about 90 days at first, 30 snapshots an hour at the rebuild cap of 15 a run); both clear once it is done.
2. **On your machine, to be done sooner** (no time limit there; about 80 s per day of raw on a recent Mac, so a full window takes about 2 hours). Ingest uploads the history about every hour, which would refuse a 2-hour rebuild's upload every time, so pause its schedule first and resume it afterwards (`HistoryBehind` may email while it is paused):

   ```sh
   aws events disable-rule --name <the stack's IngestSchedule rule>     # the rule name is in the CloudFormation stack's resources
   .venv-cloud/bin/python -m ahcloud rebuild --store s3://<data-bucket> --intel s3://<intel-bucket>
   aws events enable-rule --name <the stack's IngestSchedule rule>
   ```

   The rebuild uses Blizzard's credentials for the catalog (above), then runs the analysis and publishes to the real intel bucket. If an Ingest run uploads while it runs anyway, the rebuild's upload is refused (it is conditional on the ETag it saw at its start), the command says so and exits 1, and nothing is published; run it again. The analyzer never writes the database, so its runs can carry on.
3. **Check** the new pointer through CloudFront (`/intel/<market>/latest-<schema>.json`): its `built_ms` is the newest raw snapshot's time.

To try a rebuild without touching the cloud, add `--out data/rebuild-check`: the buckets are only read and every write lands in the folder.

## Tests

`tests/infra/` synthesizes the stack and checks its security boundaries (SEC17). It is skipped where `aws-cdk-lib` isn't installed, so run it from the infra environment:

```sh
.venv-infra/bin/python -m pytest tests/infra
```
