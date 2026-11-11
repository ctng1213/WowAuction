# Plan: carry-forward lifetimes (CF0–CF3)

Written for: the user and the Opus session that runs this batch. Brief by Opus and review by Fable, 2026-11-11; every decision approved by the user on 2026-11-11. Rewritten into plan shape on 2026-11-11 (the brief's §9 decisions are now §1; its design and findings are §3 and §4).

**Why.** Each run of the cloud's Ingest recomputes auction lifetimes from scratch. For every day its snapshots touch, `ahsync/collect/observations.py` `recompute_id_days` runs one `engine/volume.lifetimes` pass over the compact snapshots from 66 h before the day to 6 h after it: about 96 snapshots of about 30k auctions each. That pass is 325 of a 1-snapshot run's 379 s and its whole 1,770 MB high-water mark. This batch replaces it with state carried from snapshot to snapshot. A run compares each new snapshot only with the one before it, and the daily analysis reads per-day summaries, as it already does.

## 1. What is decided (do not re-litigate)

### The user's direction (2026-11-11)

> "every snapshot carries over what it learned so a single run just needs to refer to the snapshot from the hour prior to do it's job to extend or finish items auctions; then the analyze step goes over the summaries to figure out all the data"

Rejected by the user: lowering Ingest's caps to 3 as a stopgap ("the runs aren't too bad right now"); deferring the recompute to once a day ("dirty days"); a faster batch pass alone.

The user, on what a run reads: **the 66 h is the repair path's, never the daily one** (the table in §3).

### Decisions (the brief's D1–D8, all agreed by the user 2026-11-11)

1. **D1, a per-gap ledger.** Each gap's results are stored as a record (§3 item 3). The day and hour rows the analysis reads are re-folded from it and are unchanged. Rejected: accumulating straight into the day rows with exact-sum side tables (still needs three ad-hoc tables, "add to a stored row" instead of "replace whole", harder to replay).
2. **D2, repair by replay from 66 h before the point** (§3 item 5): one primitive, no checkpoints. Rejected: daily state checkpoints (about 2 MB a day more, a second thing to keep right). **Condition, a working rule (§2): the normal path reads only the snapshot before.** A scheduled run compares each new snapshot with the carried state and nothing older. The 66 h replay runs only for a late snapshot, a missing or damaged state, or the cutover, and logs that it ran.
3. **D3, `lifetimes()` becomes the fold of `step`** (§3 item 2): one implementation, so the existing scenarios and the four tools exercise `step`, and identity holds by construction. The invariant VI8 checks it as well. CF1 is judgement work (a subtle 100-line function is rewritten). Rejected: keeping both implementations, with only a property test as the guard.
4. **D4, the cutover is the first cloud run with the new code**: it bootstraps by replaying from `last − 66 h` (about 70 steps, about 4 min by today's cost, inside 900 s). Before deploying, a local `--out` dry run must show that the bootstrap reproduces yesterday's rows byte for byte (CF2's gate). **Fallback** (agreed, the brief's open question 2): the long-rebuild routine. Pause `IngestSchedule`, run `python -m ahcloud ingest` locally once, resume (infra/README.md).
5. **D5, caps and memory.** Scheduled runs keep the cap of 12. The rebuild cap is set from CF0's measurement of the `ingest` stage (about 20 expected). Memory is sized after a week of runs, as in the split plan's decision 4. Profiling the 27 s `ingest` stage is out of scope; it is a separate small item if a faster rebuild is wanted.
6. **D6, the profile stage keeps the name `recompute`** and now times the step and the fold. BZ64 and the dashboard's "Run profiles" widget stay unchanged; only BZ64's meaning is restated.
7. **D7, the commodity pool uses the flags known at the time** (§4 item 2), stated in SPEC §2.4 item 4. CF0 checks on the real data that no key's flag flips. Rejected: carrying a "keys ever seen as commodity" set.
8. **D8, measure first** (CF0, §5).

### Answers to the brief's open questions

9. **Verified 2026-11-11:** the user's machine reads the live data bucket with its current AWS credentials. The compact store holds 116 snapshot files, 2026-11-06 18:21 to 2026-11-11 16:22 UTC (4.9 days), so CF0 and CF2's gate run locally with `--out`.
10. **Still open, with a default:** whether the per-gap records are kept longer than 2 days. They are the finest sales history the cloud has, but nothing reads them in this design. **Default: 2 days**, pruned with the rest, unless the user says otherwise. Raising it later changes one `PRUNABLE` entry.

### Decided by Opus while planning (2026-11-11)

11. **Scenario IDs re-checked against `main` 58dddb3 and every remote branch**: BZ66, BZ67, FM86, SM19 and VI8 are free (the highest in use are BZ65, FM85, SEC22, SM18 and VI7). Re-check again just before each merge.
12. **`pwsh` is not installed on the user's Mac**, so each milestone runs `tools/verify.ps1`'s steps directly: `python tools/gen_lua_fixtures.py`, pytest with engine coverage, `lua5.1 tests/support/lua/runner.lua tests/addon` (from `~/tools/lua51`), then `python tools/snapshot.py`. Python is 3.12 (`~/.pyenv/versions/3.12.16`). The Windows run is CI's.

### Facts measured (2026-11-11)

**The profile line** (15:46 UTC, 1 snapshot, 4,096 MB):

| Stage | Time | Memory high-water after it |
|---|---|---|
| state | 0.1 s | |
| download | 14 s | 461 MB |
| ingest | 27 s | 567 MB |
| **recompute** | **325 s** | **1,770 MB** |
| catalog | 8 s | |
| prune | 0.2 s | |
| upload | 5.6 s | |
| **total** | **379 s** | |

`download` is the database (311 MB) plus the 66 h compact window (`download_compact`, about 70 files). `ingest` (27 s for one snapshot) is that snapshot's book, prices, hours and book volume. It is paid per snapshot and this batch doesn't touch it. Read more with Logs Insights or the dashboard's "Run profiles" widget (`profile` lines, BZ64, BZ65).

- **Run times.** The :40 runs (1 snapshot each) took 228–391 s; the :10 runs exit in under 1 s. The 09:00 analysis took 138 s at 1,503 of 8,192 MB.
- **Cost.** About $13 a month, accepted by the user; the stack's budget was raised to $20.
- **Why 66 h** (`engine/volume.py:304`). 24 h (the longest auction) + 3 × `MAX_GAP_MS` (6 h each: the vanish gap, the posting gap and the band-narrowing gap; the worst case is reserved even though snapshots are about 1 h apart) + `RATE_WINDOW_MS` (24 h: an item's early-leave rate over the gaps of the day before an ending, BZ44).
- **Days touched** (`observations.id_days`): from the day 2 h before the snapshot to the day of its last later snapshot within 66 h. That is 1 day in steady state, and 2 just after midnight.
- **Current risk** (assumed from the arithmetic, not observed):
  - A catch-up run of 12 snapshots spanning midnight costs about 1,000 s, over the 900 s timeout. It would retry the same batch every run (FM85), so the history stalls after an outage, and `HistoryBehind` (FM84) would report the stall.
  - The rebuild cap of 60 always times out.
  - The midnight runs (2 days, about 710 s) sit near `IngestSlowRun`'s 720 s.
- **State of the system.** `main` 4933274 deployed at 15:35 UTC: Ingest at `cron(10,40)`, 4,096 MB, caps 12 / 60 (provisional), profiling on; the analyzer at 8,192 MB (sized after a week, `cloud-ingest-split.md` §1 item 4); budget $20; all 10 alarms OK. `docs/plans/cloud-ingest-split.md` §1 items 1–25 explain the cloud's current design.

### CF0's measurements

*(Recorded by CF0. CF1 does not start before they are here.)*

## 2. Working rules

- **Scenarios first.** Rows go into SCENARIOS.md (§6 below) before code, and IDs not yet built go to `tests/pending_scenarios.txt`. SPEC §2.4, ARCHITECTURE and LESSONS change in the milestone that makes them true.
- **One worktree, one PR and one verification per milestone**, targeting `main` with no stacked PRs. The user merges. A milestone starts from a `main` that has the previous one merged; if it can't wait, its PR carries the earlier commits and still targets `main`.
- **Verification** is `tools/verify.ps1` (or its steps, §1 item 12). The whole-plan snapshot does not move: the desktop is untouched.
- **The normal path reads only the snapshot before** (D2's condition). A scheduled Ingest run reads the database (which holds the state) and the new raw snapshots. It downloads no compact files and reads no older snapshot. Only a replay (a late snapshot, a missing or damaged state, the cutover) reads the compact store, and it logs one line saying why.
- **Byte-identity gates hold throughout:**
  - **SM8**: the document after any sequence of capped Ingest runs and an analysis equals the one from a single uncapped run over the same raw.
  - **FM72**: a rebuild from raw gives the byte-identical document to the incremental history.
  - **BZ57**: capped runs equal an uncapped run.
  - **CF1**: every existing lifetime scenario keeps its numbers, and VI8 passes.
  - **CF2**: the real-data gate (the new code reproduces a closed day's stored rows byte for byte).
  - `ID_METHOD_VERSION` stays 8. That is what makes CF2's gate possible.
- **Engine rules.** `ahsync/engine/` stays pure (no I/O, no clock), with 100% branch coverage, invariants (SPEC §8.7) and full sort tie-breaks. A new formula gets its invariants. Lifetimes have no Lua twin (`addon/AHProfit/Engine/` has no volume module).
- **Size.** `engine/volume.py` is 970 lines; the lifetimes family moves out before anything is added (§4 item 9).
- **Failure handling.** Writes are idempotent upserts and only complete results are published. Ingest uploads the whole database with `If-Match`, then the compact index, then `state/ingest.json` (FM83). Empty houses (BZ62) and refused snapshots (FM74) stay excluded. Step, fold and state advance commit in one transaction.
- **Several writers** (cloud Ingest, a local `python -m ahcloud ingest|rebuild`, the desktop as a reader of its own file): the schema migration is judgement work. Old code refuses a newer schema.
- **IDs re-checked against `main` just before each merge** (LESSONS 2026-11-11: FM82 collided).
- **Coordination.** The status-page session (H2/H3) merges in parallel and touches `ahcloud/` and `infra/dashboard.py`. Whichever lands second merges `main` and resolves.

## 3. The design

The user's words, made precise: **every snapshot extends or finishes the auctions it sees against the one before, writes what that gap learned as a record, and the day's and hours' rows are folded from the records.**

1. **The carried state**, per market and source, in `history.db`. It must travel with the database: the `If-Match` upload keeps them consistent (FM83), and a rebuild gets both or neither.
   - `lifetime_state(market, src, prev_at)`: the newest snapshot the state has seen. After a run it always equals `meta.last_ingested_at_ms`; that is the damage check (FM86).
   - `open_auctions(market, src, auction, key, qty, win_start, win_end, price, commodity, tier1, posted_start, posted_end)`: `lifetimes`' `live` dict plus the posting gap. The posting gap is NULL when the auction was first seen at a hole (no unseen credit, as `first_seen` has no such row today). About 30k rows, rewritten whole each snapshot, since every window narrows anyway.
   - `leave_tallies(market, src, gap_end, gap_start, key, exposed_ms, leaves, commodity)`: `tally_early_leaves`' output per gap (BZ44), kept for gaps ending in the last 24 h. `rate` reads `(end − 24 h, end]`, so rows with `gap_end ≤ S − 24 h` are dropped at each step. Pooled by the row's flag (D7).
   - The previous snapshot's key set is derived from the keys of `open_auctions`, not stored.
2. **The engine** (`ahsync/engine/lifetimes.py`, new; `tests/ahsync/engine/test_lifetimes.py`).
   - The lifetimes family moves out of `volume.py`: `Ending`, `Lifetimes`, `LifeFlow`, `DURATION_MS`, `band_window`, `narrow`, `match_relists`, `lowest_two`, `lowest_other`, `vanished_in`, `lifetimes`, `life_flows`, `gap_hours` and `sold_by_hour`. This is a mechanical move; imports are updated in `observations.py`, the four tools and the tests.
   - Added: `LifeState` (frozen: `prev_at`, the live auctions, the tallies in the window), `GapRecord` (one linked gap's endings with their final expired share, fills, listings with the lowest other price, first-sight credits, seen keys, commodity keys and clashes) and `step(state, book) -> (LifeState, GapRecord | None)`, which returns None at a hole or the first snapshot.
   - **`lifetimes(books, keep_from_ms)` is reimplemented as the fold of `step`** (D3), so BZ22–BZ52, VI5, VI7, the report tool and the simulator's fits all exercise `step`. VI8 (§6) checks the fold on generated worlds anyway. Pure, with 100% branch coverage and today's sorted tie-breaks.
3. **The ledger** (the per-gap summaries, D1).
   - `gap_flows(market, src, gap_end, gap_start, key, sold, expired, listed, unseen, ended, expired_listings, matched, cancels, cancelled, t1_ended, t1_left, commodity, positions, prices)`: nonzero rows only. `positions` and `prices` are `"<position>:<units>"` text with **exact** `repr` floats (internal, never published).
   - `gap_seen(market, src, gap_end, gap_start, keys)`: the gap's seen keys as a blob, so the zero rows `volume_d1` and `sales_h1` need can be reconstructed.
   - Both are kept 2 days and pruned with the rest (§1 item 10). That is long enough for every re-fold and replay below.
4. **The fold** (`collect/observations.py`): it replaces `recompute_id_days`'s inner loop with the same arithmetic in the same order. After each step:
   - (a) day `D(S)` is replaced whole from the ledger's gaps ending in D: `volume_d1` + cover (`covered_seconds` of those gaps), `lifetimes_d1`, `new_listings_d1` and `sold_prices_d1`;
   - (b) each hour in `[floor(S) − 6 h, floor(S)]` is rewritten from the gaps ending in `(H − 6 h, H + 7 h]` whose `gap_hours` hold H (§4 item 4).
   - Days and hours outside those bounds are final (§4 item 5).
5. **One repair primitive**, `replay(db, store, market, src, from_ms, write_from_ms)`. The state is set to empty and `step` runs over the compact snapshots from `from_ms` in order. Ledger rows with `gap_end ≥ write_from_ms` are deleted first, then written for gaps ending at or after it, and every day and hour those gaps credit is re-folded. Three callers:
   - **The cutover, or a missing or damaged state**: `from = last − 66 h`, `write_from = min(day(last) start, last − 7 h)`. That covers the open day and hours; older rows are the batch pass's and already right.
   - **A late snapshot L**: `from = L − 66 h`, `write_from = L`. The gap `(A, C)` becomes `(A, L)` and `(L, C)`; gaps before A are untouched (§4 item 1).
   - **A rebuild from nothing** is the ordinary path (its first snapshot is a hole).
   - No checkpoints: a replay costs at most about 90 steps, and only in a rare case.
6. **Idempotency.** In `ingest_snapshot`, a snapshot with `obs.at_ms ≤ prev_at` and a `scans` row is already applied, so there is nothing to do; without a `scans` row it is late (item 5). Step, fold and state advance happen in the one transaction `ingest_snapshot` already opens, so FM32 (a crash between the file and the commit) still mends by re-delivery. `id_volume=False`, `id_days` and `recompute_id_days` are removed.
7. **The cloud** (`ahcloud/ingest.py`).
   - A steady-state run downloads the database and the new raw only. `download_compact` runs only for a replay, and only from its `from_ms`.
   - `MAX_SNAPSHOTS_PER_RUN` stays 12; `REBUILD_SNAPSHOTS_PER_RUN` is set from CF0's measured `ingest` stage (D5).
   - `REBUILD_REPLAY_MS` and `COMPACT_DAYS` are unchanged (the same 66 h and 116 h arguments hold).
   - The stage `recompute` times the step and the fold (D6).
8. **Schema 31.** It adds the five tables and their `PRUNABLE` entries. The migration adds empty tables: a schema migration has no store to read, so the first run's replay builds the state (item 5). Writers: cloud Ingest, the local `python -m ahcloud ingest|rebuild` (same code), and the desktop only as a reader of its own file (never the cloud's). Old code refuses a newer schema (`migrate`), so a stale local run can't write a state it doesn't maintain.
9. **Unchanged**: the analysis, the five `*_d1` tables, `sales_h1`, the document and its schema, `ID_METHOD_VERSION` 8, the hourly mode (`estimate_volume_ids` on raw) and the collector.

**What a run reads, by kind of run:**

| Run | How often | Reads | Never reads |
|---|---|---|---|
| Scheduled Ingest | every 30 min, about 24 real runs a day | the database (the state is in it) and the new raw snapshots | any older snapshot; no compact files are downloaded |
| Repair: a late snapshot, or a missing or damaged state | rare, logged | the above plus the compact snapshots from 66 h before the point repaired | raw older than the compact store's 5 days |
| Cutover to this code | once | as a repair, from 66 h before the history's newest snapshot | |
| Rebuild from raw | on demand | every raw snapshot of the retention window, in order, through the same step | |
| The 09:00 analysis | daily | the five `*_d1` tables and `sales_h1` | the state, the gap records, the snapshots |

**Expected effect** (assumed until CF0 measures): a 1-snapshot run drops from 379 s to about 60 s, and a 12-snapshot run takes about 6 min. Download drops from 14 s to the database alone. The recompute's 1,770 MB goes away, so the function can shrink after a week.

## 4. Findings from the code (Fable's review, 2026-11-11)

Each item says what the code does today and what it means for carrying state. "Confirmed" means read in the code; "assumed" means inferred.

1. **The batch pass is already an exact evaluation of an infinite-history pass** (confirmed). Everything a gap writes depends only on snapshots up to that gap's end:
   - an auction's expiry window is narrowed by its own sightings so far (`narrow`);
   - the cancel rule reads the two snapshots of the gap (`vanished_in`);
   - the relist match reads the gap's own new listings (`match_relists`);
   - the early-leave rate reads only tallies of gaps ending in `(end − 24 h, end]` (`EarlyLeaveRates.rate`, `bisect_right`);
   - the unseen estimate is credited to a listing's first observed gap from the posting gap carried in its state (`unseen_sales`);
   - tier 1 is fixed at first sight.

   `LIFETIME_LOOKBACK_MS` (66 h) exists only to make the day's pass equal that infinite pass. So carry-forward is **the same computation evaluated one snapshot at a time**, and byte identity (FM72, SM8, BZ57) can be made structural (§3 item 2) rather than only tested.
2. **One non-causal detail** (confirmed). `pooled_commodities` and `Lifetimes.commodities` union the commodity flag over every snapshot of the pass, later ones included. The flag is constant per item (Blizzard's commodities endpoint is per item; keys with a bonus list are never commodities), so pooling by the flag known at the time gives the same rates (D7). CF0 checks that no key flips over the 5 days of compact snapshots. One existing test (M33b-1's "a snapshot stored before M33b reads False") may depend on the pass-wide union; CF1 checks it.
3. **The day and hour rows are float folds in gap order from 0.0** (confirmed: `recompute_id_days`, `merge_positions`, `sold_by_hour`). `new_listings_d1.positions` and `sold_prices_d1.prices` store units **rounded to 3 decimals**, and `sales_h1` stores a rate (units ÷ pooled time), not its sums. So a partial sum can never be rebuilt from the stored rows. The ledger keeps each gap's exact contributions (§3 item 3), and the fold re-folds them.
4. **Each hourly row has two writers** (confirmed). `ingest_snapshot` first runs `recompute_sales_hours`, which rewrites `[floor(S) − 2 h, floor(S)]` with book-pair rates; then the id pass rewrites every hour the day's gaps credit. Book pairs credit the earlier book's hour, and id gaps credit from the earlier snapshot's hour for `round(gap)` hours. So the id hours are a superset, and the ids must be written after the pairs, every snapshot. The gaps that credit hour H end in `(H − 6 h, H + 7 h]`. The hours a snapshot S can change are `[floor(S) − 6 h, floor(S)]`: the new gap's hours plus what the pair writer touched.
5. **When rows become final** (confirmed). A gap belongs to the day of its later snapshot, so day D is final once a snapshot at or after D+1 00:00 is ingested. Hour H is final once the newest snapshot is at or after H + 1 h. Nothing earlier is ever revised (item 1). So a day's rows are re-folded at every snapshot of the day and are final at its first snapshot of the next day.
6. **Late snapshots are bounded** (confirmed). `_pending` lists raw only from `last − 1 day` (BZ14: older raw is ingested or pruned), so the cloud inserts a snapshot at most a day late. A replay from 66 h before it needs compact snapshots from about 90 h back, which `COMPACT_DAYS` = 5 already covers (the same "116 h at worst" arithmetic).
7. **Memory and time** (confirmed from the profile; the split is assumed). The 1,770 MB high-water mark is the pass holding 96 snapshots' kept records. The per-snapshot step holds one snapshot and the state (about 30k live auctions plus 24 h of tallies). The pass costs about 3.4 s per snapshot loaded (325 s ÷ 96). So a steady-state run should take about download 10 s + ingest 27 s + step 4 s + catalog 8 s + upload 6 s ≈ 1 min, and 4,096 MB is oversized (measure, then shrink).
8. **The 27 s `ingest` stage becomes the bound** (confirmed per snapshot; the cause is assumed to be `store_book`, `store_scan_prices`, `recompute_sales_hours` and `recompute_book_volume` over 30k auctions). 12 snapshots take about 6 min, which is fine. The rebuild cap of 60 at about 30 min still times out, so that cap is set from this stage (about 20) unless the stage is profiled and cut (out of scope, D5).
9. **`engine/volume.py` is 970 lines** (confirmed). The 1,000-line limit forces the lifetimes family into its own module before anything is added (§3 item 2).
10. **Nothing changes for the analysis** (confirmed: `analysis/market.py` and `history/reads.py` read only `volume_d1`, `volume_cover`, `sales_h1`, `lifetimes_d1`, `new_listings_d1` and `sold_prices_d1`). The user's "the analyze step goes over the summaries" is already true. The carried state and the ledger belong to Ingest alone.
11. **The profile's stage names are a contract** (confirmed: BZ64 lists them, and the dashboard's Logs Insights widget reads `stages_ms`). Hence D6.
12. **Some tests embody the batch design** (confirmed, `tests/ahsync/collect/test_observations.py`): "a day's lifetimes do not depend on which other days are recomputed", "a late snapshot recomputes every day its lifetimes reach", the two pooled-hour tests, and the BZ44/BZ45 order tests. They become fold and replay tests (§6) and keep their numbers.
13. **Decision 24 of `cloud-ingest-split.md`** ("Ingest's real cost is accepted"; the per-run fixed cost was "likely the lifetimes recompute, not yet measured") is now measured: 325 of 379 s. A LESSONS entry lands with CF3 and marks item 24 superseded.

## 5. Milestones

### CF0: measure (Opus: the caps, the cutover estimate and D7 rest on these numbers)

On the user's machine against the live store, read only (`--out`; nothing is uploaded):

1. Load plus `lifetimes` cost per snapshot over 66 h of compact files, and the high-water memory of that pass.
2. The `ingest` stage of one snapshot.
3. Whether any key's commodity flag differs between snapshots over the compact store's 5 days.

The numbers go into §1 ("CF0's measurements"), with D5's rebuild cap and D4's cutover estimate derived from them. No code changes.

### CF1: the engine (Opus, judgement: formula identity)

1. `engine/lifetimes.py`: the mechanical move out of `volume.py` first, then `LifeState`, `GapRecord`, `step`, the windowed rates, and `lifetimes` as the fold (§3 item 2).
2. `tests/ahsync/engine/test_lifetimes.py`: the moved tests, plus VI8.
3. The imports in the four tools and `observations.py` (mechanical).
4. Check M33b-1's test against the causal commodity pool (§4 item 2).

Gate: every existing lifetime scenario unchanged, VI8 passing, 100% engine branch coverage, and the whole-plan snapshot byte-identical.

### CF2: the history (Opus, judgement: the migration and byte identity)

1. Schema 31 and `history/writes.py` + `reads.py` for the five tables; `prune`.
2. `ingest_snapshot` = step + fold; `replay`.
3. `id_days` and `recompute_id_days` removed.
4. `test_observations.py` rewritten as fold and replay tests (BZ66, BZ67, FM86; the existing order and independence tests kept with their numbers).
5. SPEC §2.4 "Auction lifetimes", SCENARIOS and ARCHITECTURE for the history half.
6. **The real-data gate**: locally, with `--out`, a bootstrap from `last − 66 h` and a replay through yesterday reproduce the stored `*_d1`, `volume_cover` and `sales_h1` rows of a closed day byte for byte (a `tools/` script or a pytest marked slow). The diff is recorded here.

The whole-plan snapshot does not move (the desktop is untouched).

### CF3: the cloud (Opus; SM19 and the README are routine)

1. `ahcloud/ingest.py`: no compact download in steady state, replay on demand, the bootstrap, the caps (D5).
2. SM19 and the SM8 extension; BZ56, BZ57 and BZ64's meaning restated.
3. `IngestSlowRun` and `HistoryBehind` thresholds revisited; `infra/README.md`.
4. LESSONS (2026-11-11: the pass was the run; the split plan's item 24 marked superseded) and ARCHITECTURE's Ingest flow (raw → step → ledger → fold → `*_d1`; the compact store as the replay's input only; the change log).
5. `/security-review` on the branch (IAM is unchanged, but a trust boundary is touched).
6. Deploy with the user; watch the bootstrap run and the next 09:00 analysis; record the numbers here.
7. A week later, one small PR: memory and the rebuild cap.

**Docs, in the milestone that makes them true**: SPEC §2.4 "Auction lifetimes" (the carried state, the ledger, when a day and an hour are final, the replay; the Ingest paragraph's stages; the rebuild paragraph), SCENARIOS (§6), ARCHITECTURE, LESSONS.

## 6. Scenario rows (IDs free on `main` 58dddb3, re-checked 2026-11-11)

New:

| ID | Given / When | Then | Why |
|---|---|---|---|
| VI8 | generated worlds (fixed seed, printed on failure) with holes, missed refreshes, band clashes, commodities and gear, cancels, relists, partial fills and a listing first seen after a hole; `step` folded over the snapshots, and `lifetimes()` over the same list | the same endings (with their expired shares), fills, listings, first sightings, seen keys and commodity keys for every kept gap; the days' and hours' rows folded from the records equal the batch pass's, including `positions` and `prices` texts | carried lifetimes are the batch pass, one snapshot at a time |
| BZ66 | the state is at C; raw B, older than C and newer than A, arrives (BZ13) | the state is rebuilt from the compact snapshots from B − 66 h, the records of gaps ending at or after B are replaced ((A, C) becomes (A, B) and (B, C)) and the days and hours they credit re-folded; every row equals a rebuild's; a snapshot older than a day is still not pending (BZ14) | a late snapshot is replayed, never refused and never double-counted |
| BZ67 | a history the batch pass wrote (no state), the first Ingest run with this code, 3 snapshots pending | the state is bootstrapped from the compact store from 66 h before the history's newest snapshot, records written from the open day's first gap on, then the 3 ingested as usual; yesterday's rows untouched, today's re-folded rows byte-identical to the pass's, the run inside the time limit | the cutover is a run, not a migration |
| FM86 | `lifetime_state.prev_at` differs from `meta.last_ingested_at_ms`, or the state tables are empty with a history present | the run logs one line, rebuilds the state as BZ67 and continues; the same snapshot delivered twice (FM32) changes nothing the second time | a damaged state repairs itself; a repeat is harmless |
| SM19 | SM8's world and faults, plus a snapshot stored a day late after newer ones, through capped runs (12, 5, 1) and a rebuild | byte-identical documents throughout | the carried state keeps FM72, BZ57 and SM8 |

Changed:

| ID | Change |
|---|---|
| BZ13 | its "then" gains "the late snapshot is linked where it belongs (BZ66)" |
| BZ56, BZ57 | an Ingest run downloads the database and the new raw only; compact files only for a replay; the rebuild cap from CF0 |
| BZ64 | the stage list is unchanged (D6); `recompute` now means the step and the fold |
| FM72, FM76, BZ61 | a rebuild replays in order from an empty state; the 66 h before the oldest kept day are still replayed (unchanged constant, restated) |
| the `test_observations.py` tests without an ID (day independence, pooled hours, the late snapshot's days) | restated as fold and replay tests with the same numbers; "which days are recomputed" no longer exists |

## 7. Files

- `ahsync/engine/volume.py`: `lifetimes` (433), `life_flows`, `match_relists`, `narrow`, `band_window`, `sold_by_hour`, `LIFETIME_LOOKBACK_MS` (304).
- `ahsync/engine/lifetime_prices.py`: `sold_prices`, `new_listing_positions`.
- `ahsync/collect/observations.py`: `id_days` (58), `recompute_id_days` (77).
- `ahsync/collect/snapshots.py`: the compact snapshot store.
- `ahcloud/ingest.py`: Ingest, its stages and caps. `ahcloud/profile.py`: the profile lines.
- `tools/sim/runner.py`: the simulator through the real cloud job. `tests/tools/sim/test_runner.py`: SM8.
- SPEC §2.4 (Ingest, Auction lifetimes) and §3.3.1. SCENARIOS: BZ13, BZ44, BZ52, BZ56–BZ65, FM72, FM83–FM85, SM8.
- The explainer the user reviewed (figures of the chain, one auction's fate, the rate, the step and the fold, the tables and the processes): https://claude.ai/artifact/1eGtE6SXcPgGK97JEHrbEZ (private). Its section 11 is the proposed schema in DDL form.
