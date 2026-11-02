# Architecture

The high-level design: components, boundaries and data flows, kept current with every milestone. The detail lives in [SPEC.md](SPEC.md) (§5.0 components and contracts, §5.2 failure modes, §3.2 storage, §4.0 the app/addon split); this page is the map. Diagrams are Mermaid.

Maintenance rule (CLAUDE.md "How work is run"): a milestone that adds, removes or re-routes a component, a table family, a source or a contract updates this page in the same commit. The orchestrating model does it, never a routine subagent.

## 1. Components

Two programs and three data sources, talking only through files and versioned contracts.

```mermaid
flowchart LR
  subgraph Sources
    AHF[auctionhouse.fyi API + CDN<br/>hour/day candles, snapshot, Premium books]
    AHL[AHledger API<br/>price table, per-item history]
    Game[WoW: Forever client]
  end

  subgraph Addon["AHProfit (Lua, in game)"]
    Adapter[Core/Adapter*.lua<br/>every WoW API call]
    Capture[Capture/*<br/>events: purchases, posts, mail, crafts, loot, professions, inventory]
    Scan[Scan/Replicate.lua<br/>full AH scan -> order book]
    AEngine[Engine/*<br/>ports of Python formulas]
    Reselect[Recommend/*<br/>live reselection, graph repricing]
    UI[UI/*<br/>Portfolio tab, panel, tooltip]
    SV[(SavedVariables<br/>AHProfit.lua)]
    DataFiles[(AHProfit_Data/<br/>MarketIntelligence.lua, PortfolioPlan.lua)]
  end

  subgraph AHSync["AHSync (Python, desktop)"]
    Collect[collect/<br/>provider adapters, catch-up, scans]
    DB[(SQLite data/ahsync.db<br/>history, state, intelligence, plan)]
    Analysis[analysis/market.py<br/>statistics, patterns, volume]
    Intel[intel/<br/>MarketIntelligence contract + store]
    State[state/<br/>SavedVariables importer, journal, positions, sessions]
    Recommend[recommend/<br/>portfolio plan, pages, passes]
    Engine[engine/<br/>pure formulas and views]
    Export[export/<br/>addon data files]
    Web[web/ + app/<br/>dashboard server, refresh, scheduler]
    Shell[ahsync_app/ shell built M22; M23–M27 planned<br/>window, tray, setup, start at login, updater<br/>around run; SPEC §3.7]
  end

  Dashboard[localhost dashboard<br/>the app window, or a browser]
  GH[GitHub Releases planned M25<br/>update check, assets, SHA256SUMS]

  AHF --> Collect
  AHL --> Collect
  GH -.planned.-> Shell
  Shell -.planned.-> Web
  Shell -- installs the addon, M22 --> DataFiles
  Game --> Adapter --> Capture --> SV
  Game --> Adapter --> Scan --> SV
  SV --> State
  Collect --> DB
  State --> DB
  DB --> Analysis --> Intel --> DB
  DB --> Recommend --> DB
  Engine -.used by.-> Analysis
  Engine -.used by.-> Recommend
  DB --> Export --> DataFiles --> AEngine
  DataFiles --> Reselect --> UI
  DB --> Web --> Dashboard
```

Boundaries that matter:

- `engine/` is pure: data in, numbers out, no I/O or clock. Every displayed number is made there (SPEC §8.5).
- I/O lives at the edges (`collect/`, `db/`, `history/`, `state/`, `intel/store.py`, `export/`, `web/`), each taking a transport, clock or database so tests inject fakes.
- The addon touches WoW only through `Core/Adapter*.lua`; the addon only looks up and compares, never computes history (SPEC §4.0, §5.1).
- Sources are interchangeable behind the source tag (`src`) on every stored row; analysis picks by rule, never by name (SPEC §2.2, §5.0).

## 2. Market data flow: sources to intelligence to the addon

```mermaid
flowchart TD
  AHF[AHF hour files, snapshot, Premium books] -->|src ahf, kind hourly / book| Store
  AHL[AHledger price table every 30 min<br/>per-item history backfill] -->|src ahledger (table), ahledger_h1 (hourly)| Store
  Scans[Your full scans from SavedVariables] -->|src local, kind book| Store
  Store[(candles_h1 / candles_d1 / current<br/>scans + book_entries<br/>volume_d1 + volume_cover<br/>observed_hours)]
  Store --> Reads[history/reads.py<br/>one bulk read per market]
  Reads --> Pick["one close per (item, hour):<br/>kind book > hourly > table,<br/>then local, then config rank"]
  Pick --> Analysis[analysis/market.py<br/>medians, percentiles, 168-slot patterns,<br/>seasonality, sell windows, sales volume]
  Analysis --> Intel[(intel: MarketIntelligence<br/>one ItemIntel per item, versioned)]
  Intel --> Plan[recommend/portfolio.py<br/>candidates, selection, accounting]
  Intel --> Export[export/market_lua.py<br/>MarketIntelligence.lua]
  Plan --> ExportPlan[export/plan_lua.py, portfolio_lua.py<br/>PortfolioPlan.lua]
  Export --> Addon[Addon: tooltip, panel, live reselection]
  ExportPlan --> Addon
```

Rules encoded in this flow (SPEC references): the full refresh is the only heavy pass and runs once a day (§3.1.1); re-plans after a SavedVariables read touch only the plan and pages from the published intelligence, under one second (§3.1.2); publication is staging tables plus an atomic swap (§5.2); the addon files carry a schema and export version and the addon refuses mismatches (§3.5).

## 3. Player data flow: the game to positions, history and rates

```mermaid
flowchart TD
  Game[Game events] --> Capture[Capture/*: purchases, posts, auction mail,<br/>payouts, crafts, disenchants, sent mail,<br/>loot (gather), professions, inventory snapshots]
  Capture --> SV[(SavedVariables AHProfit.lua<br/>events kept until ingested)]
  SV -->|file watcher, 2 s debounce| Importer[state/importer.py<br/>idempotent by event id; ingestedUntil per character]
  Importer --> Journal[(state tables: journal, inventory,<br/>own_auctions, cost_basis, gather_events)]
  Journal --> Tracking[recommend/tracking_pass.py<br/>match events to plan actions]
  Tracking --> Positions[engine/positions.py + state/positions.py<br/>open on a funded buy or craft, replay lots and events,<br/>reconcile against snapshots]
  Journal --> Sessions[engine/sessions.py + state/gathering.py<br/>farming sessions: 20 units confirm, 15 min ends]
  Positions --> Accounting[committed capital = cost left of open positions]
  Accounting --> Plan[the portfolio plan]
  Positions --> History[History page: expected vs realized per position]
  Sessions --> Gathering[Gathering page: items/hr, net gold/hr]
  Importer -->|re-plan| Plan
  Plan --> Export[PortfolioPlan.lua]
  Export --> AddonLocal[Addon: local positions until AHSync reads them]
```

Rules: a sale is never inferred from missing inventory alone (§3.4D.9); events are never deleted before AHSync has stored them (§2.2 pruning handshake); the addon's newer events win over an older snapshot until the next export (§4.0).

## 4. Scheduling and the dashboard

```mermaid
sequenceDiagram
  participant S as app/scheduler.py
  participant C as collect/
  participant R as app/refresh.py
  participant P as recommend passes
  participant W as web/server.py
  S->>C: hourly :06 store hour (AHF), every 30 min AHledger table
  S->>R: daily full refresh (analysis, intelligence, plan, pages, export)
  Note over S,R: SavedVariables changed -> state import -> re-plan -> pages -> export
  S->>P: hourly status pass (positions re-evaluated, Status lines)
  W->>W: GET pages serve stored JSON (<= 50 ms); POST only for risk tolerance and manual rates, Host/Origin/token checked
  Note over S,W: planned M23–M27 (§3.7): GET /setup, GET /api/app; POST /api/setup, /api/autostart, /api/update/{restart,auto,channel}, same checks
```

## 5. Storage families (SQLite)

| Family | Tables | Written by | Read by |
|---|---|---|---|
| Market history | `candles_h1`, `candles_d1`, `current`, `scans`, `book_entries`, `volume_d1`, `volume_cover`, `sales_h1`, `observed_hours`, `ahledger_rows`, `source_state`, `sync_state`, `fetch_failures` | `collect/` | `history/reads.py` |
| Items | `items` | `collect/` | everywhere, via the catalog |
| Intelligence | `intel` (+ `_new` staging) | `analysis/` via `intel/store.py` | `recommend/`, `export/` |
| Player state | inventory, journal (`purchases`, `posts`, `auction_mail`, `payouts`, `crafts`, `disenchants`, `mail_sent`), `professions`, `recipes`, `known_recipes`, `characters`, `cost_basis`, `gather_events`, `farming_sessions`, `manual_rates`, `positions`, `position_lots` | `state/` | `recommend/`, `app/` |
| Plan and pages | `actions`, `alternatives`, `export_candidates`, `portfolio_snapshot`, `views` (+ `_new` staging), `item_rules`, `reserves`, `sell_records` | `recommend/` | `export/`, `web/` |
| Operations | `status`, `meta` (planned M25: the `update.*` keys), backups on disk, raw snapshots on disk, planned `<data>/updates/` (downloaded releases) | all | Status page |

## 6. Distribution and runtime (M22 built; M23–M27 planned, SPEC §3.7)

Each part is marked built in the milestone that lands it; the rest is **planned**. The rule: one shared core, a thin layer per OS. `ahsync/` stays standard-library only; `ahsync_app/` is the one package allowed third-party imports (pystray, Pillow, pyobjc), and `ahsync_app/platform/` is the one place that knows the OS (convention test DR16).

### 6.1 The app shell and tray around `run()`

```mermaid
flowchart TD
  Start([AHSync.exe / AHSync.app<br/>or python -m ahsync_app]) --> Lock{lock<br/>data/ahsync.lock free?}
  Lock -- held --> Show[write data/ahsync.show, exit 0<br/>the running shell shows its window<br/>with --autostart: exit 0, nothing written, FM48]
  Lock -- taken --> Addon[export/addon_install.py<br/>mirror AHProfit/, never the generated files<br/>Addon line, FM49]
  Addon --> Cfg{config.toml exists?<br/>a bad wow_path is the Addon line of FM49, not setup}
  Cfg -- no --> Setup[web/server.py in setup mode<br/>GET /setup, GET /api/app, POST /api/setup<br/>writes config.toml + key file, DR5–DR8]
  Setup --> Cfg
  Cfg -- yes --> Heal[autostart.py self-heal<br/>rewrite the login entry with the current exe<br/>refuse a translocated app, DR11]
  Heal --> Threads
  subgraph Threads["the GUI loop and the worker"]
    Win[main thread: window.py<br/>pywebview loop, one window hidden or shown<br/>browser fallback FM56, DR29]
    Tray[tray.py: pystray Icon.run_detached<br/>menu = tray.menu(TrayState), DR12]
    Run[worker thread: ahsync.__main__.run<br/>data_dir, config_path, Runtime should_stop = stop_event]
  end
  Tray -- Quit / Restart to update --> Stop[stop_event set<br/>run returns at its next 5 s tick]
  Run --> Stop
  Stop --> Pending{verified update<br/>waiting in data/updates/?}
  Pending -- no --> Exit([exit 0])
  Pending -- yes --> Apply[platform.apply_update after the lock is released<br/>Windows: silent Inno Setup, relaunch<br/>macOS: swap helper, roll back on failure<br/>DR22, DR23, FM55]
  Apply --> Exit
```

**Built (M22):** the lock and the show file (FM48), the addon install (`export/addon_install.py`, FM49) and `run()` on a worker thread with the shell ticking on the main thread (`ahsync_app/shell.py`, `__main__.py`); no window or tray yet: a manual launch opens the browser once the dashboard answers, and a missing `config.toml` is refused until setup mode lands (M23). The rest of the chart is planned.

From source `python -m ahsync` is unchanged (`./data`, `./config.toml`, no tray). The per-user folders come from `platform.current().app_dirs()`: `%LOCALAPPDATA%\AHSync` and `~/Library/Application Support/AHSync` (DR1).

### 6.2 The platform layer

```mermaid
flowchart LR
  subgraph Shared["shared, OS-free (tested once)"]
    ShellM[ahsync_app/shell.py]
    AutoM[ahsync_app/autostart.py]
    SetupM[ahsync/app/setup.py<br/>wow_paths.py]
    UpdM[ahsync/app/updater.py]
  end
  subgraph Platform["ahsync_app/platform/ (facts and thin writers)"]
    Base[base.py: Platform protocol, ten members<br/>app_dirs, wow_candidates,<br/>autostart_enable / disable / enabled,<br/>restrict_file, run_location_problem,<br/>update_asset_name, verify_update, apply_update]
    Win[windows.py<br/>LOCALAPPDATA, winreg Run key,<br/>Program Files WoW candidates,<br/>silent installer]
    Mac[macos.py<br/>Application Support, plistlib + launchctl,<br/>/Applications WoW candidates, translocation,<br/>codesign + Team ID, swap helper]
    Cur[__init__.py current<br/>the one sys.platform read]
  end
  ShellM --> Base
  AutoM --> Base
  SetupM --> Base
  UpdM --> Base
  Cur --> Win
  Cur --> Mac
  Win -.implements.-> Base
  Mac -.implements.-> Base
  Fakes[tests: fake registry, plist folder,<br/>recorded run, fake filesystem] -.inject.-> Win
  Fakes -.inject.-> Mac
```

**Built (M22):** `base.py` (the protocol; the start-at-login and update members raise until M24 and M27), `app_dirs`, `wow_candidates`, `restrict_file` and `run_location_problem` in both modules, and `current()`; the guardrail DR16 in `tests/test_conventions.py`.

Both OS modules are tested on every OS by injection (DR9–DR11, DR22, DR23), so a Windows-only CI run still tests the macOS adapter.

### 6.3 Update check, download and install

```mermaid
sequenceDiagram
  participant S as app/scheduler.py
  participant C as app/update_check.py
  participant GH as GitHub Releases
  participant E as engine/updates.py
  participant U as app/updater.py
  participant M as meta (update.*)
  participant Sh as ahsync_app/shell.py
  participant P as platform.current()
  S->>C: at start and daily (not within 24 h; never for 0.0.0 or update_check = false)
  C->>GH: stable: GET releases/latest · beta: GET releases?per_page=20 (If-None-Match)
  GH-->>C: 200 tag_name, html_url, assets · or 304 · or failure (FM52: one log line, "not checked …")
  C->>E: compare vX.Y.Z with the running version (numeric; suffixed tags ignored; never equal or older)
  E-->>C: newer? texts: "AHSync 0.4.0 is available · you run 0.3.1", pill
  C->>M: update.tag, update.url, update.etag, update.checked_ms
  alt auto_update on and newer
    U->>GH: GET SHA256SUMS, GET the OS asset (update_asset_name) into data/updates/*.part, rename
    U->>P: verify_update: hash against SHA256SUMS; macOS codesign --verify --deep --strict + Team ID (DR23)
    U->>M: update.downloaded → "AHSync 0.4.0 downloaded · installs at the next start · you run 0.3.1", pill "update ready · 0.4.0", Restart to update
  else failure (FM53)
    U->>M: delete the file · "Update to 0.4.0 failed: <reason> · still on 0.3.1 · tries again tomorrow"
  end
  Sh->>Sh: next start, or Restart to update (tray, POST /api/update/restart): run() returns, lock released
  Sh->>M: update.pending = 0.4.0
  Sh->>P: apply_update(path, exe): Windows silent Inno Setup + relaunch · macOS swap helper, roll back on failure (FM54, FM55)
  Sh->>Sh: next start: running the pending version → "Updated to AHSync 0.4.0 · today 06:02", else "Update to 0.4.0 failed · still on 0.3.1 · tries again tomorrow"
```

Channel rule (DR25, DR26): the stable channel sees only promoted releases (`releases/latest` skips pre-releases); the beta channel takes the highest `vX.Y.Z` of the list, pre-releases included; switching back never downgrades.

### 6.4 CI, build, release and promote

```mermaid
flowchart LR
  Push[push / pull request] --> Test[job test<br/>windows-latest + macos-15<br/>Python 3.12, Lua 5.1, pwsh tools/test.ps1]
  Test -- main, tag v*, workflow_dispatch --> Policy[tools/build.py policy, DR19<br/>sign: tag or sign:true<br/>preflight: main once a v* tag exists<br/>unsigned: everything else]
  Policy --> Build[tools/build.py build, DR17<br/>write ahsync/version.py<br/>pyinstaller packaging/ahsync.spec]
  Build --> SignMac[macOS, mode sign only, DR18<br/>temp keychain, codesign Developer ID + hardened runtime<br/>notarytool submit --wait, stapler staple<br/>keychain deleted]
  Build --> Pack[one packaging step per OS<br/>Windows: Inno Setup → AHSync-ver-windows-x64-setup.exe<br/>macOS: make_dmg.sh → .dmg, ditto → .zip]
  SignMac --> Pack
  Pack --> Art[upload-artifact]
  Art -- tag v* only --> Rel[job release, DR28<br/>tools/build.py release: SHA256SUMS,<br/>gh release create vX.Y.Z --prerelease --generate-notes]
  Rel --> Beta[(GitHub pre-release = beta<br/>only beta-channel installs update)]
  Beta -- user tests on the Windows PC and the Mac --> Promote[promote.yml, by hand<br/>tools/promote.py vX.Y.Z, DR27<br/>gh release edit --prerelease=false --latest]
  Promote --> GA[(latest release = GA<br/>stable installs update within a day)]
  Secrets[/GitHub secrets: MACOS_CERT_P12, MACOS_CERT_PASSWORD,<br/>APPLE_TEAM_ID, NOTARY_KEY_ID, NOTARY_ISSUER_ID, NOTARY_KEY_P8<br/>values never in the repo, logs or artifacts/] -.-> SignMac
  Secrets -.-> Policy
```

Nothing is rebuilt at promotion: friends get exactly the files the user tested. Windows stays unsigned (SmartScreen once, `INSTALL.md`); the Windows signing steps are no-ops that a certificate can fill later.

## 7. Change log of this page

- 2026-11-01: created at the AHledger batch (SPEC draft v0.7); reflects M1–M18 built and M19–M21 planned (AHledger source, kind-based precedence, coverage card, backfill).
- 2026-11-01: M19 built: the AHledger adapter (`collect/ahledger.py`), schema 24 (`ahledger_rows`, `source_state`, `observed_hours`), kind-based precedence (`collect/provider.py`), the 30-minute poll hook.
- 2026-11-01: M20 built: the Data coverage card (`engine/coverage.py`, read from `observed_hours` by `history/reads.py`, drawn on the Status page); no component or flow changed.
- 2026-11-01: M21 built: the AHledger history backfill (`collect/ahledger_backfill.py`), driven by the scheduler every 5 s and sharing the paced AHledger client with the table poll.
- 2026-11-01: packaging batch spec merge (SPEC §3.7, M22–M27 planned): §6 added with the app shell and tray around `run()`, the platform layer, the update check / download / install flow and the CI build / release / promote pipeline, all marked planned; §1 gains the `ahsync_app/` shell and GitHub Releases as dashed, planned nodes; §4 notes the planned endpoints; §5 the planned `meta` keys and `data/updates/`. Each milestone marks its part built here.
- 2026-11-01: plan review before M22: §6.1 corrected so setup mode is entered only when `config.toml` is missing (a bad `wow_path` is FM49's Addon line) and a `--autostart` launch that finds the lock held exits silently (FM48); no component changed.
- 2026-11-01: plan review: the dashboard shows in the app's own window (`ahsync_app/window.py`, pywebview) with the tray detached under its loop; a second launch signals the running shell through `data/ahsync.show` (§6.1).
- 2026-11-01: M22 built: the shell without window or tray (§6.1's lock, show file, addon install and worker thread), the platform layer's folder members (§6.2) and the CI `test` job (§6.4's first box); §1's shell node and its addon edge drawn built.
