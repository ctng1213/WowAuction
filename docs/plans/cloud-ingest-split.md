# Plan: split the cloud's ingestion from its analysis (IS0–IS3)

Written for: the user (approving §1 and the scenario rows in §4) and the Opus session that runs this batch. Drafted 2026-11-11 after the 2026-11-10 daily-run timeout (LESSONS 2026-11-11).

**Why.** Today one analyzer run does two jobs: it ingests every pending raw snapshot into `state/history.db`, then analyses the history and publishes the baselines. It uploads only at its very end, so a run that times out keeps nothing, and the next run has more to do. On 2026-11-10 that stranded the history for 44 h (two 900 s timeouts, the dead-letter queue); SPEC FM70's "the next snapshot's run ingests both" is false once a backlog exceeds one run. Splitting the two jobs gives each its own 15-minute limit and memory, makes every ingest run small, and lets a backlog drain on a schedule instead of blocking the baselines.

## 1. What is decided (do not re-litigate)

The user's decisions of 2026-11-11:

1. **Two functions, not one with a time budget** (the user: "can we just separate these lambdas? the raw run should be its own thing"). A new **Ingest** function owns raw → `history.db`; the **Analyzer** keeps the daily analysis and the hourly mode and only reads the history.
2. **Catch-up by schedule**, not self-invocation: Ingest runs on a fixed schedule and each run takes the oldest pending snapshots; a backlog drains over successive runs. No IAM permission to invoke itself.
3. **The cap is a count of raw snapshots**: at most **12 raw objects** (`raw/<market>/…json.gz`) per Ingest run. A snapshot is one picture of the house at one moment, every live auction as Blizzard published it at one refresh (about 1.4 MB gzipped); it holds no sales. Sales, expiries and new listings come from comparing a snapshot with the one before it, so each snapshot ingested adds the interval since the previous one. Blizzard refreshes about hourly (measured: 104 objects from 2026-11-06 18:57 to 11-11 04:32), so 12 snapshots are about 12 h of market time, more when a refresh was skipped; at two runs an hour a day's backlog drains in about an hour.
4. **Measure the analyzer's memory first**: it stays at 8,192 MB (deployed 2026-11-11, PR #96) until a week of split runs shows its real peak; it is then sized in a small follow-up.

Decided by Opus while planning (2026-11-11), each open to the user's veto at approval:

5. **One writer for the history.** Only Ingest writes `state/history.db` and `state/snapshots/`; the analyzer's grant on `state/` becomes read-only (plus `state/analyzer.json` if H2 lands). The `If-Match` upload stays as the backstop against a local `python -m ahcloud rebuild`.
6. **The catalog's item search moves to Ingest** (`fill_catalog`: new keys appear at ingestion). The analyzer then needs no Blizzard secret: least privilege.
7. **Ingest runs at :10 and :40** (`cron(10,40 * * * ? *)`): the collector saves each refresh around :30, so the 08:40 run has the morning's last snapshot in the history before the 09:00 analysis.
8. **A cheap early exit.** After each successful upload Ingest writes `state/ingest.json` (`last_ingested_at_ms`, the newest pending count). A run whose listing has nothing newer stops before downloading the database, so the 24 or so empty runs a day cost about a second each. `meta.last_ingested_at_ms` in the database stays the truth; the file is written only after the upload succeeds, so it can lag the database but never lead it.
9. **No dead-letter queue and no retries for Ingest** (`retry_attempts=0`): the next scheduled run is the retry. Its errors alarm and the new "history behind" alarm (FM84) are the signal.
10. **A rebuild is Ingest from a fresh database.** It marks the database `rebuilding_until_ms` = the newest raw at its start; while the mark is set each run takes the **rebuild cap** (sized in IS0: as many snapshots as fit in 10 min) instead of 12, and the daily analysis skips its publish (the hourly prices carry on). The mark clears when the history reaches it. `python -m ahcloud rebuild` on your machine stays, uncapped.
11. **Ingest starts at 4,096 MB** (its peak is measured in IS3 and sized with the analyzer's).

Facts measured on 2026-11-11:

| What | Value | Source |
|---|---|---|
| Daily run, 2026-11-09 (24 snapshots) | 815 s, 2,044 of 2,048 MB | the analyzer's REPORT line |
| Daily run, 2026-11-10 (24 snapshots) | timed out at 900 s twice | REPORT, dead-letter message |
| Analysis alone (0 snapshots ingested), on the user's Mac | analyze 54 s, publish 22 s; peak 1.94 GB resident | `cProfile` + `/usr/bin/time -l`, `analyze --daily --out` |
| `history.db` | 178 MB (11-09) → 311 MB (11-11) | S3 |

The analysis alone uses about 1.9 GB, so at 2 GB every snapshot was ingested at the memory ceiling (the SQLite file in `/tmp` counts against it too); that is the likely cause of the slow run, inferred, not measured. The split makes the time split visible: IS0 measures it.

**Cost** (estimate, GB-seconds a month; Lambda's free tier is 400k): Ingest about 24 real runs a day × 60 s × 4 GB ≈ 173k (empty runs negligible); the analyzer's hourly mode at 8 GB ≈ 259k, its daily ≈ 36k; the collector ≈ 29k. About 500k, roughly $1.70 a month over the free tier, inside the stack's $5 budget. Decision 4's sizing brings the analyzer's share down.

## 2. Working rules

- Scenario rows first (SCENARIOS.md, §4 below), then code; SPEC §2.4 in the same change. IDs not yet built go to `tests/pending_scenarios.txt`.
- One worktree and one PR per milestone; the user merges. Every milestone: `tools/verify.ps1` (the whole-plan snapshot does not move: the desktop is untouched).
- **SM8 holds throughout**: the document after any sequence of capped Ingest runs and an analysis equals the one from a single uncapped run over the same raw, byte for byte.
- **Coordination with the status page** (`docs/plans/cloud-status.md`, another session): H1 is on `main`. H2 (`state/analyzer.json`, `ahcloud/status.py`) and H3 touch `analyzer.py`, `market_stack.py` and `infra/dashboard.py`, as IS1 and IS2 do. Whichever lands second merges `main` and resolves; the status page treats Ingest as a third function and reads `state/ingest.json` for the history's freshness (decision 8).

## 3. Milestones

### IS0: measure (Opus: the numbers set the rebuild cap and check the count cap)

1. Today's 09:00 UTC daily run, the first at 8 GB: duration, peak memory, snapshots ingested (about 5).
2. The per-snapshot ingest cost on Lambda = (duration − the analysis's share) ÷ snapshots, from (1) and the profile above.
3. Check: 12 × that cost + download, VACUUM and upload ≤ 10 min at 4 GB, with margin. Set the rebuild cap = ⌊(10 min − fixed) ÷ per-snapshot cost⌋. Recorded here.

### IS1: the code split (Opus: merge logic, the byte-identity invariant)

1. `ahcloud/ingest.py` + `tests/ahcloud/test_ingest.py`: `run_ingest(data, client, market, workdir, cap, fresh)` (the early exit, download, ingest the oldest `cap` pending, refusals, catalog, prune, compact upload, `VACUUM INTO`, conditional put, then `state/ingest.json`; the rebuild mark) and its `handler`. Moved from `daily.py`: `ingest_pending` and the ingest half of `_ingest_and_publish`.
2. `ahcloud/daily.py`: the analysis only. Download the history read-only; skip with `rebuilding` while the mark is set; `analyze_market` at `now_ms` = `meta.last_ingested_at_ms`; carry, advance, publish. No database upload.
3. `ahcloud/analyzer.py`: modes `daily` and `hourly`; `rebuild` moves to Ingest. `python -m ahcloud ingest [--rebuild]`; `analyze --daily` is the analysis only; `rebuild` = an uncapped fresh ingest, then the analysis (same command as today).
4. Tests: §4's rows; SM8 extended with capped runs (12, then 5, then 1) against one uncapped run.

### IS2: the infrastructure (Opus: IAM and alarms; security-sensitive, `/security-review` before done)

1. `infra/market_stack.py`: the Ingest function (same asset, `ahcloud.ingest.handler`; 15 min, 4,096 MB, 4 GB disk, reserved concurrency 1, `retry_attempts=0`), its schedule (decision 7), grants: `raw/*` read, `state/*` read-write, the Blizzard secret. The analyzer: `state/*` read-only, no secret.
2. Alarms: `IngestErrors` (any error in an hour), `HistoryBehind` (FM84), `IngestSlowRun` (over 12 min, as H1's `AnalyzerSlowRun`). `infra/dashboard.py`: Ingest in the per-function widgets.
3. `tests/infra/`: SEC17 updated, SEC22.
4. `infra/README.md`: "Recovering the cloud state" rewritten (a rebuild is an Ingest invoke; the local command for speed).

### IS3: deploy and watch (Opus, with the user's approval at the time)

1. `tools/build_lambda.py`, `cdk diff` reviewed with the user, `cdk deploy`. The history is current, so the cutover needs no migration: the first Ingest run (:10 or :40) finds a few pending snapshots or exits early.
2. Watch the first Ingest runs and the next 09:00 analysis: durations, peaks, `state/ingest.json`, the document's `built_ms`. Recorded here.
3. A week later: size both functions' memory from the measured peaks (decision 4), a small PR.

Docs in the milestone that makes them true: SPEC §2.4 (daily mode becomes "Ingest" and "The daily analysis"; the rebuild paragraph; the budget line), SCENARIOS, ARCHITECTURE (the Ingest component and the history's one writer), LESSONS 2026-11-11 ("Open" → done).

## 4. Scenario rows (proposed, for approval)

New:

| ID | Given / When | Then | Why |
|---|---|---|---|
| BZ56 | the history holds everything through A; `raw/` holds B, C, D; an Ingest run | B, C, D ingested in order, books and history pruned, the catalog filled, the compact files then a `VACUUM INTO` copy uploaded with `If-Match`, then `state/ingest.json` = D; nothing published | ingestion is its own job |
| BZ57 | 30 snapshots pending (a day and a quarter's backlog); Ingest runs at :10, :40, :10 | 12, 12, then 6 ingested, oldest first, each run's upload kept; the analysis after the third run publishes the byte-identical document a single uncapped run gives (SM8) | a backlog drains, it never times out |
| BZ58 | `state/ingest.json` = D and the newest raw key is D | the run ends "up_to_date" without downloading the database | an empty run costs nothing |
| BZ59 | the 09:00 analysis; the history's newest snapshot is 08:22 | the history downloaded and not written back; `analyze_market` at 08:22; the document published with `built_ms` 08:22, the hourly fields carried and advanced as today | the analysis only reads |
| BZ60 | the history is 30 h behind the newest raw (Ingest stalled) at 09:00 | the analysis still publishes, `built_ms` = the history's newest snapshot; the hourly runs layer the newer prices over it; FM84 has alarmed | stale baselines beat none |
| BZ61 | a rebuild is invoked; the newest raw is R; the 09:00 analysis falls during it | the first run starts a fresh history marked rebuilding until R and uploads with `If-Match` on the ETag seen at its start (FM76); runs continue at the rebuild cap; the analysis skips its publish ("rebuilding") while the mark is set; when the history reaches R the mark clears and the next analysis publishes the document an incremental history gives (FM72) | a rebuild runs in the cloud and never publishes half a history |
| FM82 | an Ingest run times out or crashes mid-batch | nothing uploaded, `state/ingest.json` unchanged; the next scheduled run starts from the same history; `IngestErrors` emails | a failed ingest loses one run, not a day |
| FM83 | an Ingest upload finds the ETag changed (a local rebuild uploaded meanwhile) | `PreconditionFailed`, `state/ingest.json` not written; the next run starts from the live history | the history is never overwritten blind |
| FM84 | the history's newest snapshot is more than 3 h older than the newest raw, for 3 h | "History behind" emails; clears when Ingest catches up | a stalled ingest is noticed, not only a failing one |
| SEC22 | the synthesized stack | Ingest may read `raw/*`, read and write `state/*` and read the Blizzard secret; the analyzer may read `raw/*` and `state/*` and write only the intel bucket (and `state/analyzer.json` with H2), with no secret | one writer for the history; least privilege |

Changed:

| ID | Change |
|---|---|
| BZ10 | the daily run becomes BZ56 (ingest) + BZ59 (analysis); the row is restated as the two |
| BZ12 | the crash between the upload and the pointer no longer exists (the analysis uploads no database); restated: a crash between the analysis's document and its pointer leaves yesterday's pointer, and the next analysis republishes |
| FM70 | "the analyzer runs out of time or memory: nothing published, the history untouched, the dead-letter queue, the alarm; the next analysis publishes" (no longer "ingests both") |
| FM72, FM76 | a rebuild is an Ingest invoke (BZ61); the local command remains |
| SM8 | adds capped Ingest runs to the sequences compared |

## 5. Open

- The rebuild cap and the check of decision 3's 12 (IS0).
- Ingest's and the analyzer's memory after a week (decision 4, IS3).
- Whether H2 lands before IS1 (§2's coordination): the user's call.
