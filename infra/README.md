# The cloud job's infrastructure

The CDK app for the market's cloud job (SPEC §2.4, plan `docs/plans/blizzard-cloud.md`): two buckets (data, private; intel, served by CloudFront), the collector Lambda on a 15-minute schedule, CloudFront for the public summaries, an SNS topic for alerts, two alarms and a monthly budget. Region `us-east-1`, where the Blizzard secret `wowauction/blizzard-api` lives.

## Prerequisites

- Node.js 18 or newer and the CDK CLI (`npm install -g aws-cdk`).
- Python 3.12 with the infra requirements in their own environment:

  ```sh
  python3.12 -m venv .venv-infra
  .venv-infra/bin/pip install -r requirements-infra.txt -r requirements-dev.txt
  ```

- AWS credentials for the account in your shell (`aws login`, or a profile).
- The secret `wowauction/blizzard-api` in `us-east-1`, a JSON object with `client_id` and `client_secret`.

## Deploy

From the repository root:

```sh
python tools/build_lambda.py                     # stage ahsync/ + ahcloud/ into build/lambda
source .venv-infra/bin/activate
cd infra
cdk bootstrap                                    # once per account and region
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

To reproduce a cloud run, point `--store` at a folder holding the same `raw/` and `state/` objects. M31 adds `analyze` and `rebuild`.

## Tests

`tests/infra/` synthesizes the stack and checks its security boundaries (SEC17). It is skipped where `aws-cdk-lib` isn't installed, so run it from the infra environment:

```sh
.venv-infra/bin/python -m pytest tests/infra
```
