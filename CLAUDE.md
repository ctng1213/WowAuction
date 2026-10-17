# CLAUDE.md

WowAuction: **AHSync** (Python companion app: sync + engine + dashboard) and **AHProfit** (WoW: Forever addon). The spec is [docs/SPEC.md](docs/SPEC.md), and its test scenarios are in [docs/SCENARIOS.md](docs/SCENARIOS.md). Read them before changing behaviour, and update them in the same change when behaviour changes.

## Commands

```
python -m pip install -r requirements-dev.txt     # once: pytest + coverage (dev-only)
tools/test.ps1                 # all tests: regenerates Lua scenario fixtures, then pytest + Lua runner (-NoLua skips Lua)
python -m pytest tests/ahsync  # Python tests only
python tools/gen_lua_fixtures.py                  # tests/scenarios/*.json -> Lua (run before the Lua runner alone)
lua5.1 tests/support/lua/runner.lua tests/addon   # Lua tests only
python -m ahsync               # run the app
python tools/deploy.py         # copy addon/* into the WoW AddOns folder (never overwrites a generated Data.lua)
python tools/visual.py         # UI layout snapshots vs golden (--update to accept, --html to view)
```

`lua5.1` is a standalone Lua 5.1 interpreter that must be on PATH. Use 5.1, not 5.4: WoW runs 5.1, and the two differ in integer and `#` behaviour.

The source layout is in SPEC §5. It is not repeated here, so it can't drift.

## Structure rules

- **Tests mirror source one-to-one.** `ahsync/engine/volume.py` ↔ `tests/ahsync/engine/test_volume.py`; `addon/AHProfit/Engine/SellProb.lua` ↔ `tests/addon/Engine/SellProb_spec.lua`. A new module gets its test file in the same change.
- **`ahsync/engine/` is pure.** No I/O, no clock, no network: data in, numbers out.
- **`addon/AHProfit/Engine/` ports a subset of it** (the modules listed in SPEC §5). Those must stay formula-for-formula identical, including the numeric rules in SPEC §3.3: rounding, even-count medians, percentiles and weekday. A change to a ported Python module changes its Lua twin in the same change.
- **I/O lives at the edges**: `api/`, `store/`, `ingest/`, `export/`, `web/`. They take a `transport`, `clock`, or `db` argument so tests can inject fakes.
- **Every WoW API call goes through `addon/AHProfit/Core/Adapter.lua`.** Forever's API is partly unverified, so this keeps fixes to one file. Mark guesses with `-- UNVERIFIED:`.

## Size limits

- **Function over ~200 lines** → refactor it before adding more. Most functions should fit on one screen (under 50 lines).
- **File over 1,000 lines** → split it by responsibility.
- Also stop and refactor when a function needs a comment to explain its sections, takes more than about 5 parameters, or nests deeper than 3 levels.

## Docstrings (required)

Enforced by `tests/test_conventions.py`, which fails on a missing docstring, a function over 200 lines, a file over 1,000 lines, or a source module without a mirrored test file (Python via `ast`; Lua by scanning for `---` blocks above each `function`).

Exempt from the convention checks:

- **Mirroring**: `__init__.py`, `__main__.py`, `store/schema.sql`, `web/static/*`, `UI/Demo.lua`, and `tests/visual/` (scenario-based, like `tests/scenarios/`).
- **Mirroring and size**: data files `addon/AHProfit/Data/*` and the generated `addon/AHProfit_Data/Data.lua`.
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
- **Determinism** (SPEC §8.6). Pass `now_ms` in, use no randomness, and give every sort a full tie-break ending in a unique key. Never rely on dict, set or `pairs` order. The whole-plan snapshot (`w1.expected.json`) must be byte-identical across runs. Update it only on purpose, and review the diff.
- **Invariants** (SPEC §8.7). A change to `sellprob`, `volume`, `craft`, `plan` or `views` keeps its invariants passing. A new formula gets its invariants in the same change. Generated cases use a fixed seed, and failures print it.
- **UI**: logic goes in `UI/ViewModel.lua` and is tested there; frame code only draws. A UI change updates the golden snapshots (`tools/visual.py --update`) in the same change, and the diff is reviewed against [docs/mockups/addon.html](docs/mockups/addon.html). See SPEC §8.4. Dashboard changes follow [docs/mockups/dashboard.html](docs/mockups/dashboard.html). A UI change that departs from a mockup updates the mockup in the same change.

## Before finishing a change

1. Tests pass (`tools/test.ps1`), including invariants, the whole-plan snapshot and 100% engine branch coverage.
2. No function or file is over the size limits, and every non-test function has a docstring with its inputs and output.
3. Source and test trees still mirror each other.
4. SPEC.md is updated if behaviour or a formula changed, and SCENARIOS.md lists every scenario the change added or altered.
5. `git diff` reviewed, including golden snapshots and `w1.expected.json`; commit only when asked.
