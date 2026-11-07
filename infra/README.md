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

Rebuild the asset before every deploy that changes `ahsync/` or `ahcloud/`; `cdk diff` shows what a deploy would change. To remove the stack, run `cdk destroy`. The buckets are kept (`RETAIN`), so the raw snapshots survive; delete them by hand only if you mean to lose the history.

## Running the job locally

The command line runs the same `run()` the Lambda calls (plan decision 17):

```sh
python -m ahcloud collect --house <id> --store data/cloud        # into a folder
python -m ahcloud collect --house <id> --store s3://<data-bucket> # into the real bucket, with your AWS credentials
```

Blizzard credentials are read from, in order: `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET` in the environment, `~/.ahsync/blizzard` (two lines: the client id, then the secret; `chmod 600`), or `--secret wowauction/blizzard-api` through Secrets Manager. A credential is never accepted on the command line.

To reproduce a cloud run, point `--store` at a folder holding the same `raw/` and `state/` objects, or read the real buckets and write elsewhere with `--out <folder>`.

The `s3://` stores need boto3 on your machine, in its own environment (botocore needs `python-dateutil` < 3, which the infra environment's pin rules out):

```sh
python3.12 -m venv .venv-cloud && .venv-cloud/bin/pip install -r requirements-cloud.txt
aws login                                                     # your own AWS credentials
```

## Recovering the cloud state (rebuild)

A rebuild throws `state/history.db` and the compact snapshots away and replays the raw of the retention window (the last 90 days plus 2.5 days, FM76) into a fresh database, then uploads it and publishes. Use it when the daily run fails with `DatabaseError` (FM72), or to apply a changed volume method to the stored days at once; day to day the daily run keeps everything current.

1. **The Lambda first**, while the raw is short (by an estimate from today's runs a day of raw takes the analyzer about 5 minutes; it stops at 15): invoke the analyzer with `{"mode": "rebuild"}`. Its reserved concurrency keeps it from overlapping a daily or hourly run.
2. **On your machine when the Lambda times out** (no time limit there; about 80 s per day of raw on a recent Mac):

   ```sh
   .venv-cloud/bin/python -m ahcloud rebuild --store s3://<data-bucket> --intel s3://<intel-bucket>
   ```

   It uses Blizzard's credentials for the catalog (above) and publishes to the real intel bucket. Start it well away from the daily run (09:00 UTC). If a daily run still uploads while it runs, the rebuild's upload is refused (`PreconditionFailed`: it is conditional on the ETag it saw at its start) and nothing is published; run it again. Hourly runs don't touch the database and can carry on.
3. **Check** the new pointer through CloudFront (`/intel/<market>/latest-<schema>.json`): its `built_ms` is the newest raw snapshot's time.

To try a rebuild without touching the cloud, add `--out data/rebuild-check`: the buckets are only read and every write lands in the folder.

## Tests

`tests/infra/` synthesizes the stack and checks its security boundaries (SEC17). It is skipped where `aws-cdk-lib` isn't installed, so run it from the infra environment:

```sh
.venv-infra/bin/python -m pytest tests/infra
```
