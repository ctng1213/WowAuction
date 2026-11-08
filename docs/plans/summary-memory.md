# Plan: the market summary's memory and size (SM0–SM2)

Written for: the Opus session that runs this batch in Claude Code on this repo, and the user who approves it. Tasks are tagged **Opus** (judgement) or **Sonnet** (routine), each with a one-line reason.

**What this batch is.** The cloud market's summary is the largest thing AHSync holds. On 2026-11-08 it reached 88 MB decompressed (16,669 items) and every app refused it until 0.1.8 raised SEC16's bound to 256 MB (LESSONS 2026-11-08). The app now reads it, but AHSync 0.1.8 measured **1,039 MB resident** just after its first start with it. This batch measures where that goes, halves what the app holds without changing any number it shows (SM1), then makes the summary itself smaller (SM2), so a market whose every item has been seen stays well inside the bound.

## 1. What is decided (do not re-litigate)

The user's decisions of 2026-11-08:

1. **Nothing the app shows or plans changes.** Every page, tooltip line, plan and export is byte-identical before and after each milestone: the whole-plan snapshot (WP1), the visual goldens and the addon files do not move.
2. **Measure first (SM0), then the app-only changes (SM1), then the format (SM2).** SM1 needs no cloud change and helps at any summary size; SM2 needs a cloud deploy.
3. **Older apps keep working.** The cloud keeps publishing schemas 10–13 alongside the new one, as it does today (`latest-<schema>.json`).
4. **The plan, the budget and the order are approved** (the user, 2026-11-08): a cloud market's process peak ≤ 600 MB with the 88 MB summary of 2026-11-08, measured by SM0 (2); SM2 is decided after SM1's measurement.

Facts measured on 2026-11-08 (the 88 MB schema 13 document, Python 3.12, `tracemalloc`):

| Stage | Memory | 88 MB summary |
|---|---|---|
| Decompressed bytes, then the decoded text | S + S | 176 MB |
| Parsing (`contract.from_json`), peak | about 2.9 × S | 252 MB |
| The parsed `MarketIntelligence`, kept | about 1.5 × S | 134 MB |
| `to_json` again for `stage_intel` | + 1 × S, briefly | 89 MB |

- **Two parsed copies are kept.** `IntelStore.read_intel` caches the parsed document per connection; the scheduler's connection and the dashboard's (`web/server.py` `open_server_db`) each hold one. The dashboard's callers are `web/server.py` (the cloud freshness pill, which needs only the built time) and `app/status_page.py`.
- **A new summary arrives every hour**, while both old copies are held, so the peak is about base + 6 × S. Python rarely returns freed memory to macOS, so the resident size stays near the peak.
- **Where the 88 MB goes:** the six packed profile strings (`how`, `sales_how`, `supply_how` and their `*_weeks`) are 58 MB; the field names repeated per item 11.5 MB; null or default fields 2 MB.
- **The packed strings are runs.** Each `*_weeks` string has 2 distinct values today; 30,053 of the 50,007 profiles are flat (every slot 1000). Run-length coding takes the six from 58 MB to 3 MB *today*. The market is two days old: as weeks accumulate, shaped profiles vary more and compress less; flat profiles and the week counts stay compact.
- **Only Python reads the packed strings.** `engine.profile.unpack` reads them; the export (`export/market_lua.py`) writes unpacked values, so the addon is unaffected.
- **The SPEC's memory budget** (§5.1, "Memory during a full refresh ≤ 512 MB") was set for a full refresh of a 9k-item AHF market. A cloud market runs no full refresh; no budget covers its process.

## 2. Working rules

- Every milestone: `tools/verify.ps1` (here: pytest with Python 3.12, the Lua runner, `tools/visual.py`, `tools/snapshot.py`), byte-identical snapshot and goldens, plus SM0's memory measurement before and after, recorded in this file.
- One worktree and one PR per milestone; the user merges.
- Scenario rows first (SCENARIOS.md), then code; SPEC in the same change.

## 3. Milestones

### SM0: measure (Opus: what to measure decides what SM1 fixes)

1. **The running app**, sampled every 2 minutes for 4 hours from 2026-11-08 10:55 Pacific (four hourly summaries): does the resident size fall back between summaries, stay at the first start's peak, or climb? Recorded here.
2. **A repeatable measurement**: `tools/bench.py --summary <document.json.gz>` (new option) runs the cloud path in a fresh process on a real document: poll, stage, re-plan, export, then a second poll of the same size, and reports the peak and the resident size after each step. It becomes the check SM1 and SM2 are measured with.
3. **The budget** (approved, §1 item 4): a cloud market's process peak ≤ 600 MB with the 88 MB summary, measured by (2). SPEC §5.1 gains the row.

### SM1: hold one copy, never re-serialize (Opus: cross-connection caching and the staging contract)

1. **One parsed summary per process.** The `read_intel` cache moves from the connection to a process-wide cache keyed by (market, built, schema), so both connections share one frozen record. Safe because `MarketIntelligence` is frozen and callers never change it (its docstring says so); the cache is guarded by a lock for the two threads. The freshness pill reads only `built` from the `intel` row.
2. **Store the document as downloaded.** `poll` keeps the decompressed text and `stage_intel` stores it, instead of `to_json` of the parsed record. The stored body must parse to the same record (a test asserts `from_json(text) == from_json(to_json(from_json(text)))` on the fixture document).
3. **Drop the old record before parsing the new one**, so a new hourly summary never coexists with two old ones.
4. Expected: steady about 1.5 × S + base, peak about 3 × S + base. Measured with SM0 (2) and recorded.

Scenarios to add: a re-plan and a dashboard request in one process parse the summary once; a staged cloud document is stored byte for byte as downloaded; a new summary replaces the cached one.

### SM2: the compact summary, schema 14 (Opus: contract change, cloud and app together)

1. **Run-length profile strings**: a run of n equal values is written `v*n` (`1000*168` for a flat profile). `engine.profile.unpack` reads both forms; the record keeps the compact string, so the memory saving holds after parsing, not only on the wire.
2. **Items as rows**: the document carries its item field list once, and each item is an array in that order.
3. **Leave out null and default fields** in the rows' tail.
4. The cloud publishes 10–14; `ADDED_IN`/the downgrade path writes 10–13 exactly as today. App and cloud ship together; the app is released before the cloud starts publishing 14 (an app that does not know 14 never asks for it).
5. Expected today: 88 MB → about 21 MB decompressed (4.9 → 3.9 MB gzip). Re-measured on the then-current document.

Needs: a `cdk deploy` (the user's approval at the time), and a rebuild is not needed (the format is written per publish).

## 4. Open questions for the user

None: answered 2026-11-08 (§1 item 4).
