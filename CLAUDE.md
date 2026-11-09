# CLAUDE.md

WowAuction: **AHSync** (Python companion app: sync + engine + dashboard) and **AHProfit** (WoW: Forever addon). The spec is [docs/SPEC.md](docs/SPEC.md), and its test scenarios are in [docs/SCENARIOS.md](docs/SCENARIOS.md). Read them before changing behaviour, and update them in the same change when behaviour changes.

## Commands

```
python -m pip install -r requirements-dev.txt     # once: pytest + coverage (dev-only)
tools/test.ps1                 # all tests: regenerates Lua scenario fixtures, then pytest + Lua runner (-NoLua skips Lua)
tools/verify.ps1               # the milestone routine: test.ps1, then snapshot.py; -Bench adds bench.py. Puts lua5.1 on PATH from ~/tools/lua51
python -m pytest tests/ahsync  # Python tests only
python tools/gen_lua_fixtures.py                  # tests/scenarios/*.json + tests/properties/*.json -> Lua (run before the Lua runner alone)
python tools/gen_properties.py                    # regenerate LP1 property cases after changing a ported formula
lua5.1 tests/support/lua/runner.lua tests/addon   # Lua tests only
python -m ahsync               # run the app
python tools/deploy.py         # copy addon/* into the WoW AddOns folder (never overwrites generated addon data files; --dry-run)
python tools/deploy.py --restart  # ...then restart AHSync and wait until it has rewritten both addon files; then /reload
python tools/savedvars_report.py  # after /reload in game: what the addon captured, and what is missing
python tools/gen_bonus_suffixes.py  # refresh addon/AHProfit/Data/BonusSuffixes.lua from AHF /items (no key)
python tools/visual.py         # UI layout snapshots vs golden (--update to accept, --html to view)
python tools/bench.py --summary <document.json.gz>   # a cloud market's memory through two hourly summaries (budget 600 MB)
python tools/sim_report.py inference --seeds 1,2,3   # the market simulator's SM rows: engine against truth, per seed
python tools/sim_report.py decision --seeds 1,2      # paper trading: the starter benchmarks and the SD rows (--rows starter,SD28; several minutes)
python tools/snapshot.py       # whole-plan snapshot vs tests/fixtures/worlds/w1.expected.json (--update to accept, prints a diff summary)
python tools/gen_world.py      # regenerate the snapshot's fixture world w1.json (fixed seed), then snapshot.py --update
python tools/lifetimes_report.py --raw <raw/market folder> --out docs/plans/blizzard-cloud-lifetimes.md   # M33 step 1: band, fate and relist distributions
python tools/sales_report.py --raw <raw/market folder> --catalog <document.json.gz> --out docs/plans/blizzard-cloud-sales.md   # before M33b: cheapest-first, cancel floor, listing age, evidence, sold vs ask
python -m ahsync.mcp --data-dir <data folder>   # the AI assistant bridge Claude launches (SPEC §3.8; the app's server must be enabled)
python -m ahsync_app           # the app shell from source: window, tray, setup, per-user folders (needs requirements-app.txt)
python tools/build.py build 0.0.0   # local build: PyInstaller, then Inno Setup (Windows) or the dmg (macOS); needs requirements-app.txt + pyinstaller
python tools/promote.py v0.1.0      # make a tested pre-release the latest release (or the Promote release workflow)
python tools/record_blizzard.py record   # record Blizzard's Forever auction API into fixtures (creds: env or ~/.ahsync/blizzard)
python -m ahcloud collect --house 113 --store data/cloud   # run the cloud collector locally (same code as the Lambda)
python -m ahcloud rebuild --store s3://<data> --intel s3://<intel>   # rebuild the cloud state from raw (needs requirements-cloud.txt; infra/README.md)
python tools/build_lambda.py        # stage ahsync/ + ahcloud/ as the Lambda asset in build/lambda
cd infra && cdk deploy -c house=<id> -c alert_email=<you>   # deploy the cloud job (infra/README.md; needs requirements-infra.txt)
```

`lua5.1` is a standalone Lua 5.1 interpreter that must be on PATH. Use 5.1, not 5.4: WoW runs 5.1, and the two differ in integer and `#` behaviour.

The source layout is in SPEC §5. It is not repeated here, so it can't drift.

## Structure rules

- **Tests mirror source one-to-one.** `ahsync/engine/volume.py` ↔ `tests/ahsync/engine/test_volume.py`; `addon/AHProfit/Engine/SellProb.lua` ↔ `tests/addon/Engine/SellProb_spec.lua`. A new module gets its test file in the same change.
- **`ahsync/engine/` is pure.** No I/O, no clock, no network: data in, numbers out.
- **`addon/AHProfit/Engine/` ports a subset of it** (the modules listed in SPEC §5). Those must stay formula-for-formula identical, including the numeric rules in SPEC §3.3: rounding, even-count medians, percentiles and weekday. A change to a ported Python module changes its Lua twin in the same change.
- **I/O lives at the edges**: `collect/`, `db/`, `history/`, `state/`, `intel/store.py`, `export/`, `web/`. They take a `transport`, `clock`, `db` or folder argument so tests can inject fakes.
- **Every WoW API call goes through `addon/AHProfit/Core/Adapter.lua`** (or `Core/AdapterAH.lua`, its auction house half, `Core/AdapterTooltip.lua`, its tooltip half, and `Core/AdapterDiagnostics.lua`, its diagnostics, split off for size). Forever's API is partly unverified, so this keeps fixes in one place. Mark guesses with `-- UNVERIFIED:`.

## Size limits

- **Function over ~200 lines** → refactor it before adding more. Most functions should fit on one screen (under 50 lines).
- **File over 1,000 lines** → split it by responsibility.
- Also stop and refactor when a function needs a comment to explain its sections, takes more than about 5 parameters, or nests deeper than 3 levels.

## Docstrings (required)

Enforced by `tests/test_conventions.py`, which fails on a missing docstring, a function over 200 lines, a file over 1,000 lines, or a source module without a mirrored test file (Python via `ast`; Lua by scanning for `---` blocks above each `function`).

Exempt from the convention checks:

- **Mirroring**: `__init__.py`, `__main__.py`, `db/schema.sql`, `data/*`, `web/static/*`, `UI/Demo.lua`, and `tests/visual/` (scenario-based, like `tests/scenarios/`).
- **Mirroring and size**: data files `addon/AHProfit/Data/*` and the generated `addon/AHProfit_Data/*.lua` (`MarketIntelligence.lua`, `PortfolioPlan.lua`).
- **Docstrings**: anonymous Lua functions passed inline (`frame:SetScript("OnEvent", function(...) ... end)`). Keep them a few lines long and have them call a named, documented function.

Every non-test function, method and class has a docstring stating its purpose, **inputs** (with units) and **output**. Mention side effects and raised errors when there are any. Test functions are exempt; their scenario name is their documentation.

Python (Google style, with type hints matching the docstring):

```python
def estimate_volume(prev: Book, next_: Book, max_gap_ms: int = 6 * HOUR_MS) -> dict[int, Volume] | None:
    """Estimate units sold, expired and newly listed between two order books (SPEC §3.3.1).

    Args:
        prev: The earlier complete order book.
        next_: The later complete order book of the same market.
        max_gap_ms: Largest allowed time between the books, in ms.

    Returns:
        Item key -> Volume(sold, expired, listed) in units, or None when the
        books are more than max_gap_ms apart.
    """
```

Lua (LuaLS annotations):

```lua
--- Expected fraction of your units that sell within the window (SPEC §3.3.2).
---@param soldPerDay number|nil  Market units sold per day; nil when unknown
---@param windowDays number      Listing window in days
---@param ahead number           Units listed at or below your price, not counting yours
---@param qty number             Units you post
---@param defaultP number        P to use when soldPerDay is nil
---@return number                P in [0, 1]
local function SellProbability(soldPerDay, windowDays, ahead, qty, defaultP) ... end
```

## Code style

- Clear and concise beats clever. Name things after the domain (`sold_per_day`, `deposit_copper`), not the mechanism (`tmp`, `data2`).
- **Units in names when ambiguous**: money is integer **copper** (`*_copper` or documented), time is **UTC Unix ms** (`*_ms`), hours and days follow AHF (`floor(ms/3600000)`).
- **Money is an integer except for expected values.** Prices, costs, deposits and budgets are integers. Anything multiplied by a probability or ratio is float copper, rounded with `floor(x + 0.5)` only for display or export (SPEC §3.3). Never use Python's `round()`.
- `None`/`nil` price means "no listing", **never zero**.
- Comments explain *why*, not *what*. Cite the spec section for business rules (`# SPEC §3.3.1`).
- Python: 3.12, **standard library only** in `ahsync/` (`pytest` is dev-only). Type hints on public functions. Dataclasses for records. No global state.
- Lua 5.1: everything `local`; one global namespace table `AHProfit`; no per-frame allocations in hot paths; data files use compact string rows (Lua constant limit).
- **Performance** (SPEC §3.1.1, §3.1.2, §5.1):
  - **Heavy work only in AHSync's full refresh.** History reads and simulations happen nowhere else. Incremental updates touch only dirty items.
  - **The addon only looks up and compares.** No `OnUpdate`, debounce bursty events, nothing in combat, lazy frames and decoding, pooled rows, full scans chunked at 4 ms per frame.
  - A change that could affect a budget runs `python tools/bench.py` (AHSync) or `/ahp perf` (addon).
- **Failure handling** (SPEC §5.2):
  - Commit in small units and make every write an idempotent upsert.
  - Publish only complete results: staging tables plus an atomic swap; temp file plus check plus rename.
  - Keep the last good state and show its age.
  - Never delete an addon event before AHSync has committed it.
  - A new failure path gets an FM scenario, with a deterministic injected fault.
- Errors: fail loudly in `engine/`; at the edges, log and degrade (for example, a missing Premium feature → skip with a status message). In the addon, the UI calls the engine through `pcall` and shows failures as a status line, never as a Lua error popup.
- Secrets: the API key (env `AUCTIONHOUSE_APIKEY`) never goes into logs, URLs, exceptions, fixtures or dashboard responses. `tools/record_fixtures.py` strips it.
- Dashboard writes (SPEC §3.5 Security): `POST` only, with `Host`, `Origin` and the per-run token checked. GET never changes state. No CORS headers. A new endpoint gets a SEC scenario.

## Testing rules

- **Scenario first.** Add the Given / When / Then scenario to [docs/SCENARIOS.md](docs/SCENARIOS.md) before the code. The test name is the scenario's ID plus its name in plain English (`test_P12_long_band_competitor_expires`). `tests/test_conventions.py` fails when the file and the tests disagree. IDs not built yet are listed in `tests/pending_scenarios.txt`: remove an ID there in the same change that adds its test. In Lua, name the test `it("P12 long band competitor expires", ...)`.
- Shared engine scenarios live in `tests/scenarios/*.json` and run in **both** Python and Lua with the same expected numbers.
- Use **real data as the template** (`tests/fixtures/ahf/`, recorded by `tools/record_fixtures.py`), then shape it with the builders in `tests/support/builders.py`. Don't hand-write large fixture blobs.
- No network, real clock or real WoW in tests: use `FakeTransport`, `FakeClock`, `wow_stubs.lua`.
- Expected values are written as numbers a human can check by hand, with a `why` explaining them.
- A bug fix starts with a failing scenario that reproduces it.
- **Every displayed number is engine-made and tested** (SPEC §8.5). No arithmetic in `web/` or UI frame code. Totals, sorting, filters and deltas go in `engine/views.py` (and `UI/ViewModel.lua`). A new tab field needs a scenario in the SPEC §8.5 map. Engine branch coverage stays at 100%.
- **Determinism** (SPEC §8.6). Pass `now_ms` in, use no randomness, and give every sort a full tie-break ending in a unique key. Never rely on dict, set or `pairs` order. The whole-plan snapshot (`w1.expected.json`, checked by `tests/tools/test_snapshot.py`, WP1) must be byte-identical across runs. Update it only on purpose (`python tools/snapshot.py --update`), and review the diff.
- **Invariants** (SPEC §8.7). A change to `sellprob`, `volume`, `craft`, `plan` or `views` keeps its invariants passing. A new formula gets its invariants in the same change. Generated cases use a fixed seed, and failures print it.
- **UI**: logic goes in `UI/ViewModel.lua` and is tested there; frame code only draws. A UI change updates the golden snapshots (`tools/visual.py --update`) in the same change, and the diff is reviewed against [docs/mockups/addon.html](docs/mockups/addon.html). See SPEC §8.4. Dashboard changes follow [docs/mockups/dashboard.html](docs/mockups/dashboard.html). A UI change that departs from a mockup updates the mockup in the same change.

## Before finishing a change

1. Tests pass (`tools/test.ps1`), including invariants, the whole-plan snapshot and 100% engine branch coverage.
2. No function or file is over the size limits, and every non-test function has a docstring with its inputs and output.
3. Source and test trees still mirror each other.
4. SPEC.md is updated if behaviour or a formula changed, and SCENARIOS.md lists every scenario the change added or altered.
5. `git diff` reviewed, including golden snapshots and `w1.expected.json`; commit only when asked, or under a batch's standing approval.

## How work is run

Who plans, who orchestrates, who implements the routine and who reviews is the global policy's call (`~/.claude/CLAUDE.md`, its agents and skills). This section only says what this project's artifacts are, which tasks count as routine here, and when each document changes.

**A batch** is one approved spec merge, one handoff plan, then milestones executed one at a time, each verified and committed before the next.

- **Design-time documents**: a mockup in [docs/mockups/](docs/mockups/) for alignment, then the approved design merged into SPEC.md and SCENARIOS.md with the new scenario IDs added to `tests/pending_scenarios.txt`. These are revised when the user changes a decision between batches.
- **The plan**: `docs/plans/<batch>.md`, with §1 the decisions already made, §2 the working rules, then the tasks per milestone, each tagged with who does it and a one-line reason. A plan never re-litigates a decision recorded in it. A decision made during execution is appended to §1 so the wrap-up can list it.

**Routine here** (delegable): parsers of a documented format, fixtures and recorders, config keys with bounds, dashboard JavaScript that only draws a payload, scenario tests with given numbers, and the mechanical documentation marks: a SPEC "planned" note becoming "built" with module names, removing IDs from `tests/pending_scenarios.txt`, regenerating goldens and the whole-plan snapshot when the plan says so.

**Judgement here** (never delegated): new engine algorithms, merge or precedence logic, migrations with several writers, anything with a byte-identity or cross-module invariant, the final review of every delegated diff, and any documentation change that alters meaning: a bug found mid-execution (its reproducing scenario row first, then the fix, then the SPEC rule it corrects), a formula or rule the implementation had to change, a scenario row whose number proved wrong, a decision the spec didn't cover.

**Approvals.** The user approves the mockup, the spec merge and a standing approval for each batch, and is otherwise not interrupted. Stop only for a decision that is theirs: a departure from the spec, or a rule the spec doesn't cover that changes a displayed number. A departure from the spec goes to the user before it is written anywhere.

**Verifying a milestone**, in its worktree: `tools/verify.ps1`, which runs `tools/test.ps1` then `python tools/snapshot.py` (byte-identical unless the plan says the snapshot moves, and then the diff is reviewed) and with `-Bench` also `python tools/bench.py` (required when a budget could move); plus a headless render of the real dashboard for dashboard changes (no console errors, no `undefined` or `NaN`).

**Git.** One worktree per milestone; never `cd` into it from the main shell (use `git -C` and absolute paths). One commit per verified milestone, with the model's attribution trailer, then fast-forward `main` and remove the worktree.

**Documentation lands in the same commit as the change that makes it true.** Two documents stay current across batches:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the high-level design with Mermaid diagrams of the components and the data flows, linking to SPEC for detail. A milestone that adds, removes or re-routes a component, a table family, a source or a contract updates it in the same commit; planned changes are drawn at the spec merge and marked built in the milestone. Its change log notes the milestone.
- [docs/LESSONS.md](docs/LESSONS.md): the log of wrong assumptions found along the way (**Assumed · True · Found by · Changed**). Whoever discovers one writes the entry in the same commit as the fix; an entry that changes what the system should do also lands in SPEC and SCENARIOS. Append, never delete; mark superseded entries.

**Where the global policy's documents live here.** The global policy names generic paths; this project's equivalents are:

| Global policy | This project |
|---|---|
| Product requirements, approved specification | [docs/SPEC.md](docs/SPEC.md) and [docs/SCENARIOS.md](docs/SCENARIOS.md) |
| Architecture overview | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| ADRs | The plan's §1 decision list; durable ones are restated in SPEC |
| `work/active/<slug>/spec.md`, `plan.md`, `progress.md` | `docs/plans/<batch>.md`; progress is the git log (one commit per milestone) |
| Recurring pitfalls | [docs/LESSONS.md](docs/LESSONS.md) |

## Git branching workflow

- **Never commit or push directly to `main`.** The only exception is when a change has to be on `main` for a specific test. In that case, stop, explain why, and ask me before pushing.
- Before making changes, create a new branch from an up-to-date `main`:
  - Format: `ctn-RCP-000-<dash-delimited-description>`
  - `ctn` = my initials. `RCP-000` is a placeholder ticket ID; always use it until I give you a real ticket number.
  - Description: short, lowercase, words separated by dashes (e.g. `ctn-RCP-000-add-recipe-upload-form`)
- Commit to that branch, push it, and open a merge request (MR) into `main`.
  - MR title: plain-language summary of the change
  - MR description: the same Summary / Next steps wrap-up from the security review section
- Don't merge the MR yourself. I review and merge.
- If I'm already on a `ctn-RCP-...` branch for this work, keep using it. Don't create a new one.