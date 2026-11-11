# Plan: the cloud status page (H1–H3)

Written for: the user (approving the mockup and §1) and the Opus session that runs this batch. Drafted 2026-11-10 from the strategic planner's review of `infra/market_stack.py`, `ahcloud/*` and SPEC §2.4.

**Why.** The only cloud health signals today are six alarm emails and the cloud lines on the local Status page (S21, S23, S24, D99, D104). Nothing in one place answers: are both Lambdas healthy, which UTC hours have a snapshot and which were analysed, and are the SLAs met. The 2026-11-07 stall went 9 h unalarmed (M39). The page must work away from the PC.

## 1. What is decided (do not re-litigate)

The user, 2026-11-10:

1. **The landing page is public**: a static `status.html` on the summaries' CloudFront distribution, reading `status/<market>.json` from the same origin. A card on the local Status page may follow later (H4, not planned). The JSON holds no bucket name, account id, ARN, function name, log text or credential.
2. **NoDailyBaselines tightens from 36 h to 27 h** ("daily analysis by 12:00 UTC"). S24's desktop 36 h is a separate rule and stays.
3. **A new alarm: the analyzer's Duration over 12 min** (its timeout is 15 min). `daily_refused` (FM74) gets no alarm: it is amber on the page.
4. **The collector writes its state file on every run**, not only on a new snapshot, adding `last_checked_ms` and `last_outcome`, so the page can tell "Blizzard hasn't refreshed" from "the collector is dead". `last_modified` keeps its meaning, and the raw envelope is still written before the state (BZ8).
5. **One page-side calculation is allowed**: the status's own age (`now − written_ms`) and its colour, compared against limits the JSON publishes (amber > 70 min, red > 3 h). SPEC records it as the one exception to "every displayed number is engine-made".
6. **No cost panel.** Billing metrics need a console setting outside the stack; the budget emails stay the cost signal.

Taken from the planner's report, unless the user changes them at mockup approval:

7. **The SLAs and colours** are the mockup's §2 table (`docs/mockups/cloud-status.html`).
8. **The grid is 7 days × 24 UTC hours**: analysed (inside the document's `coverage`), collected (≥1 raw key that hour, from the S3 listing the hourly run already makes), missing, this hour, future. The document's `coverage` stays daily-only (D104); the grid reads it and does not change it.
   **A week picker** (the user, 2026-11-10): 7-day blocks ending today (the last 7 days by default), **12 blocks** (84 days), inside the document's 90-day `coverage` (13 blocks would be 91 days, one past it; changed at the spec merge); each option's label carries that week's verdict ("complete", "9 h missing"); ‹ › step a week. The JSON carries every week's cells (one character per hour, about 2 KB), label and missing-hours text, so choosing a week only redraws. The picker stops at the first week with a snapshot. Older raw exists, but without "analysed" it would need a second kind of grid; not planned.
9. **Step 1 is a CloudWatch dashboard defined in CDK** (`infra/dashboard.py`), kept as the drill-down after the page ships.

Decided at the spec merge (2026-11-10), within the approved design:

10. **The SLA rules are SPEC §2.4's "The status page"**, scenarios CS1–CS12, BZ55, FM79–FM81, SEC21. Two details the mockup didn't fix: errors in the last 24 h with every alarm back to OK are **amber** ("2 analyzer errors in 24 h, none now"); run-time shares are whole percent, floored, so 90.5% is still amber.
11. **Error counts and `mismatch` come from `GetMetricData`** (24 h sums), alongside `DescribeAlarms`: a crashed run writes no state, so only CloudWatch counts it. `GetMetricData` has no resource-level scoping (SEC21).
12. **Run durations come from the state files** (each run measures itself), not CloudWatch; a run that times out shows as an error instead.

## 2. Working rules

- CLAUDE.md's batch rules: one worktree and PR per milestone, scenario rows before code, docs in the same commit, `tools/verify.ps1`'s steps run by hand on macOS.
- Every account change goes through the stack (`docs/plans/blizzard-cloud.md` §2). boto3 only in `ahcloud/aws.py` (AR9).
- `ahcloud/status.py` is pure: keys, pointer, state files, alarm states and `now_ms` in, a `CloudStatus` out. Every value and colour on the page is made there and tested; `status.html` only draws (plus decision 5).
- A status write never fails the analyzer's run: it is the last step, logged and skipped on error, and the run's own outcome stands. A failed status write shows up as a stale page (decision 5's red), which is the intended signal.

## 3. Milestones

### H1: the CloudWatch dashboard and the alarm changes (config only; CS1, FM79, FM80) — built 2026-11-10

Built as planned. The alarm-window check: CloudWatch allows alarms over a day long (evaluated once an hour, `docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/alarm-evaluation.html`), so 27 one-hour periods; the slow-run alarm is `AnalyzerSlowRun` (Duration maximum per 15 min over 720,000 ms).


- `infra/dashboard.py` + `tests/infra/test_dashboard.py`: the alarm-status widget (all 7); `snapshot_written` per hour over 7 d; `hourly_published + daily_published` per hour; per function Errors, Invocations, Throttles, Duration max with the timeout annotated; DLQ depth; `daily_refused`, `hourly_no_base`, `mismatch`; a Logs Insights widget of the analyzer's WARNING/ERROR lines; a text widget linking `infra/README.md`'s recovery section.
- `market_stack.py`: NoDailyBaselines at 27 h (decision 2); AnalyzerDuration > 12 min (decision 3). **Check first**: CloudWatch's limit on period × evaluation periods (today's alarm uses 6 h × 6, possibly for this reason); 1 h × 27 or 3 h × 9.
- SPEC §2.4 Alerts; the FM/S rows touched; `infra/README.md` "Reading the dashboard"; ARCHITECTURE change log.
- The user runs `cdk deploy`.

### H2: the status document (code; CS2–CS11, BZ55, FM81, SEC21)

- `ahcloud/status.py` (pure) + test: the SLA colours, headline, the 13 weeks of grid with each week's label and missing-hours text (decision 8), freshness, Blizzard cadence (median and longest interval between the last 24 h's snapshots), document facts, next daily run; `to_json` with a `schema` field and the page's age limits.
- `ahcloud/aws.py`: `alarm_states` (DescribeAlarms, scoped to the stack's alarm names); IAM grant in the stack.
- `ahcloud/collector.py`: decision 4. `ahcloud/analyzer.py`: writes `state/analyzer.json` (last hourly and daily outcome) and `status/<market>.json` (intel bucket, `Cache-Control: max-age=60`) at the end of every run.
- **The intel bucket's 7-day expiry must be scoped to `intel/`**: it applies to the whole bucket today and would delete `status.html` after a week. The status JSON is rewritten hourly but goes under the same rule.
- SPEC §2.4 "The status document"; ARCHITECTURE §1 cloud subgraph.

### H3: the page (CS12)

- `infra/status/status.html` (draw-only JS, no libraries), deployed by the stack (`aws_s3_deployment.BucketDeployment`, already in `aws-cdk-lib`; it adds a small custom-resource Lambda, to note in the budget's service list if needed).
- Loading, couldn't load (10 s timeout, 404, bad schema), first deploy with no data, stale and normal states, as in the mockup §3.
- A headless render against a fixture JSON: no console errors, no `undefined` or `NaN`, no sideways scroll at 390 px.

## 4. The user's actions, in order

1. ~~Approve the mockup and §1 items 7–9~~ (approved 2026-11-10).
2. Approve the spec merge (SPEC §2.4, SCENARIOS, `tests/pending_scenarios.txt`) and a standing approval for H1–H3.
3. `cdk deploy` after H1, and again after H2 + H3.

## 5. Risks and open questions

- The CloudWatch alarm-window limit (H1 check).
- The daily run's duration today is unknown; H1's Duration widget measures it. If it is already near 12 min, decision 3's alarm fires at once and the run time needs its own fix.
- Metrics under-count after a local `python -m ahcloud` run (EMF goes to stdout). The page reads S3, so it is right; the dashboard's metric bars are not.
