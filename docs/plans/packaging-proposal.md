# Proposal: AHSync as a standalone app for Windows and macOS (stage 1, for approval)

Written 2026-11-01 by Fable for the user's approval. Nothing here is merged into SPEC.md or SCENARIOS.md yet. After approval, the spec merge adds:

- SPEC §3.7 "Distribution and runtime";
- the §5 layout;
- milestones M22–M26 in §7;
- the DR scenario family;
- `docs/plans/packaging.md`, the handoff plan.

Model for structure: `docs/plans/ahledger-coverage.md`.

## 0. Decided by the user (2026-11-01; not re-litigated here)

1. **Audience: the user and a few friends.** First run: choose the WoW folder, enter the optional auctionhouse.fyi key, pick a market. Clear instructions for the unsigned-app warnings.
2. **Unsigned builds on both platforms.** The workflow must let signing be added later without restructuring. *Superseded for macOS by decision 9: the Mac app is signed; Windows stays unsigned.*
3. **A tray / menu-bar icon** with Open dashboard, Status and Quit. A small third-party tray library is acceptable as a packaging-only exception; `ahsync/` stays standard-library only.
4. **Updates notify only.** The app checks GitHub Releases and shows "a new version is available". Nothing installs itself.

Answered on review (2026-11-01):

5. **The GitHub repo is public,** created under the user's account `ctng1213` as `ctng1213/WowAuction`.
6. **The user has an Apple Silicon Mac to test on.** The macOS build is arm64 only, and M26 is not done until it has passed on that Mac.
7. **"Start at login" is ticked by default** on the setup page.
8. **Windows gets an installer,** not a plain zip (1.1, 1.14). macOS stays a `.dmg`.
9. **The macOS app is signed and notarized** (changed 2026-11-01; this replaces decision 2 for macOS). The user has an Apple Developer membership. The workflow signs `AHSync.app` with a Developer ID Application certificate and the hardened runtime, notarizes and staples the dmg, using GitHub Actions secrets (1.15). Windows stays unsigned. The user's reason is to make automatic updates easier later; v1 still only notifies (decision 4) unless the user changes that.
10. **One shared core, a thin layer per OS** (1.13). Nearly all logic, tests and the build recipe are shared. Each OS adds only a small adapter module and its final packaging step. A convention test enforces the boundary.

## 1. Recommended approach per topic

Each topic: the recommendation, then the alternatives rejected and why (one or two lines each).

### 1.1 Packaging tool: PyInstaller 6.x, onedir, windowed

- **Support:** PyInstaller 6.22.3 (Sep 2026) supports Python 3.8–3.15 and macOS x86_64 and arm64. It builds `.app` bundles (`BUNDLE` in the spec), ad-hoc signs them by default, and takes `--codesign-identity` for a real identity later ([PyPI](https://pypi.org/project/pyinstaller/), [PR 5581](https://github.com/pyinstaller/pyinstaller/pull/5581)).
- **Build setup:**
  - One `packaging/ahsync.spec` with two platform branches.
  - `onedir`: faster start, no temp extraction, and easier to debug than onefile.
  - `console=False`.
  - On macOS, `info_plist={"LSUIElement": True}`, so there is no Dock icon, only the menu bar.
- **Addon:** `addon/` is bundled as data (`datas`), so the app installs the addon it was built with.
- **Gotcha:** `.gitignore` ignores `*.spec`, `build/` and `dist/`. Add `!packaging/*.spec`.
- **Artifacts:** `AHSync-<ver>-windows-x64-setup.exe` (an Inno Setup installer, 1.14) and `AHSync-<ver>-macos-arm64.dmg`. PyInstaller makes the same `dist/AHSync` app folder (or `AHSync.app`) on both; only the step that wraps it into an installer or a dmg differs.
- **Rejected:**
  - **Nuitka:** a C compile per build, minutes on each runner, and MSVC/clang toolchain quirks. Nothing here needs native speed.
  - **Briefcase:** BeeWare's layout and templates are aimed at Toga/GUI apps, with their own `pyproject` metadata and MSI/DMG opinions. More to learn than it saves for a tray app.

### 1.2 App shell and tray: `ahsync_app/` outside `ahsync/`, pystray on the main thread

- **Package:** a new top-level package `ahsync_app/`, with tests in `tests/ahsync_app/` and the convention checks extended to it. Its modules:
  - `__main__.py`: the entry point.
  - `shell.py`: the lifecycle.
  - `tray.py`: the menu model (pure) plus a thin pystray adapter.
  - `autostart.py`: the shared autostart decisions (when to enable, self-heal, refuse a bad location). The OS writers live in `platform/`.
  - `platform/`: the thin per-OS layer (1.13).
- **Dependencies:** the only third-party imports in the repo live here: `pystray`, `Pillow` (pystray needs a PIL image for the icon), and on macOS `pyobjc-core`/`pyobjc-framework-Cocoa` (pystray's backend). They are listed in `requirements-app.txt`, never in `ahsync/`. **Record as a decision in SPEC §5.0.1**: "packaging-only exception; `ahsync/` stays stdlib."
- **Threading:**
  - pystray's `Icon.run()` blocks and must run on the main thread. On macOS that is strictly required; on Windows a thread would do ([pystray usage](https://pystray.readthedocs.io/en/stable/usage.html), [FAQ](https://pystray.readthedocs.io/en/stable/_sources/faq.rst.txt)).
  - So the shell runs the tray on the main thread, and `ahsync.__main__.run(data_dir, config_path, Runtime(..., should_stop=stop_event.is_set))` on a worker thread. `Runtime.should_stop` already exists for this.
  - Quit sets the event, `run()` returns at the next 5 s tick, and the shell exits.
- **Menu:**
  - **Open dashboard** (`webbrowser.open`).
  - **Status** (opens `/#status`).
  - **Start at login**, a checkbox (1.6).
  - **Update available: download**, only when the check in 1.9 found one.
  - **Quit**.
- **Menu model:** the menu is built from a pure `TrayState` dataclass (`tray.menu(state) -> list[MenuItem]`), so tests check items and enabled flags without pystray.
- **Tray menu sketch:**

  ```
  [AHSync icon in the tray / menu bar]
  ┌──────────────────────────────────┐
  │ Open dashboard                   │   -> http://localhost:8765
  │ Status                           │   -> http://localhost:8765/#status
  │ ✓ Start at login                 │   checkbox; same function as the Status toggle
  │ AHSync 0.4.0 available · download│   only when the daily check found one; opens the release page
  │ ───────────────────────────────  │
  │ Quit AHSync                      │   sets the stop event; run() ends at its next tick
  └──────────────────────────────────┘
  Hover text: "AHSync 0.3.1 · synced 14:06" (Windows tooltip; macOS shows the icon only).
  ```

- **Rejected:**
  - A Tk/Qt window: heavier, and the dashboard already is the UI.
  - `infi.systray`: Windows only.
  - Writing the tray in `ahsync/web`: it would put a dependency inside the stdlib boundary.

### 1.3 API key storage: the existing key file, written by setup

- **Lookup:** `config.resolve_api_key` already reads env `AUCTIONHOUSE_APIKEY`, then `~/.ahsync/api_key`, then `api_key` in config.toml (SPEC §3.6). No new lookup rule, and the env var keeps working for the developer.
- **Writing:** the setup screen writes `~/.ahsync/api_key`, with `chmod 0600` on POSIX. On Windows the profile folder is already ACL'd to the user.
- **Display:** Status shows "API key: set" or "none", never the value. The Redactor already scrubs it from logs.
- **Rejected:**
  - **macOS Keychain / Windows Credential Manager:** no stdlib API. It would take `security`/`cmdkey` subprocess round-trips, `cmdkey` cannot read a secret back, and Keychain ACL prompts on every read for an unsigned app. The key gates a free community API, not money.
  - **Config.toml only:** already "discouraged" in §3.6, and config is also what the dashboard rewrites.

### 1.4 Paths: per-user app folders; source runs unchanged

- **Packaged:** `%LOCALAPPDATA%\AHSync\` on Windows and `~/Library/Application Support/AHSync/` on macOS.
  - Each holds `config.toml` and `data/` (db, backups, log, lock, stop file).
  - `platform.current().app_dirs()` decides them (1.13), and the shell passes `--data`/`--config` to `run()`.
  - Not Roaming: the database is large and machine-local.
- **From source:**
  - `python -m ahsync` keeps `./data` and `./config.toml`, so the developer workflow, `tools/deploy.py --restart` and the snapshot tools are untouched.
  - `python -m ahsync_app` runs the shell from source against the per-user folders, so it can be tried without a build.
- **WoW folder:** `ahsync/app/wow_paths.py` (shared) probes the candidates `platform.current().wow_candidates()` lists.
  - Windows: `C:\Program Files (x86)\World of Warcraft\<name>`.
  - macOS: `/Applications/World of Warcraft/<name>`.
  - `<name>` comes from a list, `_classic_beta_` first, since the Nov 4 launch may rename it (SPEC §1).
  - A folder is valid when `Interface/AddOns` exists. If `Interface` exists but `AddOns` does not, as in a fresh client, `AddOns` is created.
  - The macOS path is **UNVERIFIED**. It is inferred from a Blizzard forum post naming `/Applications/World of Warcraft/_classic_beta_/WTF/` ([OSXDaily on installing Forever on Mac](https://osxdaily.com/2026/09/20/how-to-install-world-of-warcraft-forever-beta-now-on-mac-or-pc/)).
- **Rejected:** moving the developer's `data/` too. It breaks every tool and the plan's byte-identity routine for no gain.

### 1.5 First-run setup: a page served by the dashboard server in setup mode

- **Setup mode:** when `config.toml` is missing, or `wow_path` has no `Interface/AddOns`, the shell starts `web/server.py` in setup mode on `dashboard_port`.
  - `GET /` redirects to `GET /setup`.
  - The token is embedded as on the main page.
  - `GET /api/app` returns the detected WoW folder and its check text, the market options, `api_key_set`, the `autostart` state and the `version`.
  - The browser opens automatically.
- **Saving:** `POST /api/setup {"wow_path", "ruleset", "faction", "api_key", "autostart"}` follows the §3.5 rules: Host, Origin and token (SEC13).
  - **Validation:** the folder has `Interface/AddOns`; the ruleset is normal, pvp or rp; the faction is alliance or horde; the key is optional and stripped.
  - **Writes:** `config.toml` (`markets`, `wow_path`, and a comment pointing at docs/config.toml), temp plus rename; then the key file (1.3).
  - **Then:** sets autostart (1.6) and installs the addon (1.8).
  - **Answer:** `200 {"set": true, "restart_wow": true|false}`, or `400` with the reason for any bad field.
  - **Hand-over:** the shell stops the setup server and starts the normal run. The page polls `GET /api/markets` and reloads into the dashboard when it answers ("Starting AHSync: first sync and refresh, about a minute").
- **Folder field:** the browser cannot pick a folder, so the field is text, pre-filled with the detection. A **Check** button sends `POST /api/setup` with `"check_only": true`, which answers the folder's text: "found Interface/AddOns · 2 accounts" or "no Interface/AddOns under …".
- **Texts:** engine-made (`engine/app_view.py`, SPEC §8.5); the page only draws.
- **Re-running setup later:** a tray **Setup…** item is a non-goal for v1. The user edits config.toml or deletes it.
- **Rejected:**
  - A native dialog: `tkinter` is in stdlib, but PyInstaller plus Tk on macOS is a recurring source of breakage, and the look would not match.
  - A terminal wizard: not for friends.

### 1.6 Autostart: Run key on Windows, LaunchAgent on macOS, toggle via POST

- **Windows:** `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `AHSync` = `"<exe>" --autostart`, through stdlib `winreg`.
  - Rejected: a Startup-folder `.lnk` (needs COM/pywin32 or a hand-rolled shortcut format, and a `.cmd` flashes a console).
  - Rejected: Task Scheduler (admin confusion, and more power than needed).
- **macOS:** `~/Library/LaunchAgents/com.wowauction.ahsync.plist`.
  - Keys: `Label`, `ProgramArguments` [`/Applications/AHSync.app/Contents/MacOS/AHSync`, `--autostart`], `RunAtLoad` true, and no `KeepAlive`, so Quit stays quit.
  - Written with stdlib `plistlib`, loaded with `launchctl bootstrap gui/<uid> <plist>`, and removed with `launchctl bootout gui/<uid>/<label>` (the modern subcommands; see the launchctl man page).
  - Rejected: `SMAppService` (needs a signed app).
  - Rejected: Login Items via AppleScript (fragile, and it prompts).
- **Testability:** the writers are the platform modules (1.13). Each takes injected fakes (a registry-like object, a plist folder, a `run` callable), so both are tested on either OS (DR9–DR11).
- **Self-heal:** when autostart is on, each start rewrites the entry with the current executable path, so a moved app keeps starting.
  - On macOS a path under `/private/var/folders/.../AppTranslocation/` is refused, with the Status alert "Move AHSync to Applications, then turn Start at login on again" (see 1.11).
- **Dashboard:** a Status page card "AHSync app" with a **Start at login** toggle.
  - The toggle sends `POST /api/autostart {"enabled": true|false}` (SEC14). `GET /api/autostart` is 405.
  - The tray checkbox calls the same function in-process.
- **Launch modes:** `--autostart` launches do not open the browser; a manual launch does.

### 1.7 Single instance: the lock moves with `data/`; a second launch opens the dashboard

- `InstanceLock` on `<data>/ahsync.lock` stays.
- When the packaged shell finds the lock held, it opens `http://localhost:<port>` in the browser and exits 0 (FM48). The port comes from the config file in the same folder.
- `python -m ahsync` keeps printing "AHSync is already running" (FM15 unchanged).
- Rejected: a named mutex or socket (another mechanism for the same guarantee).

### 1.8 Addon install: at every start, from the bundle, never over the generated files

- **Code:** move `tools/deploy.py`'s `Plan`, `plan_mirror`, `plan_data_addon` and `apply` into `ahsync/export/addon_install.py` (stdlib). `deploy.py` imports them, and its CLI is unchanged.
- **When:** the shell calls `install_addon(bundle_addon_dir, wow_path)` at each start, before `run()`.
- **Rules kept:**
  - `AHProfit/` is mirrored, with stale files inside it removed.
  - `AHProfit_Data/` gets the `.toc`, and the placeholders only when none exists.
- **Status lines:**
  - Files added or removed: info line "Addon updated · restart WoW to load it". WoW loads only files present at client start.
  - Existing files changed only: "Addon updated · /reload in game".
  - AddOns missing or unwritable: warn "Addon not installed: no Interface/AddOns under <wow_path>" or "…not writable", and the app runs on (FM49).
- **Rejected:** installing only at setup. Friends would never get addon updates.

### 1.9 Update check: GitHub Releases, once a day, notify only

- **Request:** `ahsync/app/update_check.py`, using the existing `ApiClient`/`Transport`.
  - It sends `GET https://api.github.com/repos/<owner>/<repo>/releases/latest` with `If-None-Match` and `Accept: application/vnd.github+json`, at start and daily. The unauthenticated limit of 60 an hour is ample.
  - The result is stored in `meta`: `tag_name`, `html_url`, `checked_ms`.
- **Comparison and texts:** `engine/updates.py` (pure) parses `vX.Y.Z` and compares it to the running version.
  - Texts: "AHSync 0.4.0 is available · you run 0.3.1", and the pill "update available".
  - Shown as a Status alert (info, linking `html_url`), the top-bar pill (from `GET /api/app`, on every page) and the tray item.
- **Switches:**
  - Dev builds (`0.0.0`) skip the check.
  - Config `update_check = true`, a bool. A non-bool refuses to start (FM16).
- **Failures:** offline, 404 (no release yet), 403 (rate limit) or a bad body: nothing shown, one log line, and the next try is tomorrow (FM52).
- **No download and no install** (decision 4).
- **Rejected:**
  - A version file hosted elsewhere: another thing to maintain.
  - Checking on every page load: the rate limit, and `web/` must not compute.

### 1.10 GitHub Actions and the release flow

- **Workflow file:** `.github/workflows/ci.yml`.
- **Job `test`:** runs on push and pull request, on `windows-latest` and `macos-15`.
  - **macOS runner:** `macos-15` is arm64. `macos-latest` now points at macOS 26, macOS 14 retires 2 Nov 2026, and Intel stays only as `macos-15-intel` until Fall 2027 ([macOS 14 retirement](https://github.blog/changelog/2026-10-01-github-actions-macos-14-runner-image-retirement), [macOS 13 retirement](https://github.blog/changelog/2025-09-19-github-actions-macos-13-runner-image-is-closing-down/)).
  - **Steps:** `actions/checkout`; `actions/setup-python` 3.12; `pip install -r requirements-dev.txt`.
  - **Lua 5.1:** via `leafo/gh-actions-lua@v13` with `luaVersion: "5.1.5"`. Its README says Windows needs an MSVC dev-cmd action first. macOS fallback: `brew install lua@5.1` and a `lua5.1` symlink.
  - **Tests:** `pwsh -File tools/test.ps1`. PowerShell 7 is on every GitHub-hosted image. `test.ps1` must stay 5.1/7-compatible, and the Lua runner's output must not assume `\`.
- **Job `build`:** needs `test`, and runs only on tags `v*` and on `workflow_dispatch`.
  1. `pip install -r requirements-app.txt pyinstaller==6.22.*`.
  2. Write the tag into `ahsync/version.py`.
  3. `pyinstaller packaging/ahsync.spec`.
  4. Package, the one per-OS step (`tools/build.py` calls it). Windows: Inno Setup compiles `packaging/windows/ahsync.iss` over `dist/AHSync/` → `AHSync-<ver>-windows-x64-setup.exe` (1.14). macOS: `packaging/macos/make_dmg.sh` runs `hdiutil create` of a folder holding `AHSync.app` and an `Applications` symlink → `AHSync-<ver>-macos-arm64.dmg`.
  5. `actions/upload-artifact`.
- **Job `release`:** needs `build`, with `permissions: contents: write`. It runs `gh release create "$TAG" --generate-notes <assets>`. gh is preinstalled, so no third-party release action is needed.
- **Signing hook:**
  - `build` has steps `sign-windows` and `sign-macos`, between PyInstaller and packaging, plus a `notarize` step after packaging on macOS.
  - They are guarded by `if: env.HAVE_SIGNING == 'true'`, with env mapped from secrets at job level.
  - Today they are no-ops. Adding certificates later touches only those steps.
- **Cost:**
  - Standard runners are free on public repositories.
  - Private repos get a monthly included amount, currently $12. macOS costs about 10x Linux per minute: Linux $0.006, Windows $0.010, macOS $0.062 ([GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)).
  - A tests plus build run on macOS takes about 10–15 minutes, so a private repo would allow only a handful of releases a month.
  - Also, a **private repo's release assets and `/releases/latest` need a token**, which would break the update check and friends' downloads. **The repo is public (decision 5).**
- **Rejected:**
  - Self-hosted runners: a Mac to keep online.
  - A separate repo for the addon: the plan keeps one repo and one tag.

### 1.11 Unsigned-app instructions (go into `INSTALL.md`, linked from the release)

- **Windows:** SmartScreen shows "Windows protected your PC". Choose **More info → Run anyway**, once, on the downloaded installer. The download's mark-of-the-web is the cause.
- **macOS:** the app is signed and notarized (1.15), so it opens without warnings. The steps below apply only to a build made without the signing secrets (a fork or a local build).
- **macOS 15+, unsigned builds:** right-click → Open no longer bypasses Gatekeeper.
  - Open the app once (it is blocked), then go to **System Settings → Privacy & Security → Open Anyway** and authenticate ([Apple: open a Mac app from an unknown developer](https://support.apple.com/ta-in/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/15.0/mac/15.0), [Macworld](https://www.macworld.com/article/2457844/what-to-do-when-you-cant-open-an-app-you-just-installed-in-macos-sequoia.html)).
  - Alternative: `xattr -dr com.apple.quarantine /Applications/AHSync.app`.
- **Drag AHSync.app to Applications first;** the dmg shows the Applications shortcut.
  - A quarantined app launched where it landed runs translocated, under a random path, which would break Start at login ([App Translocation](https://eclecticlight.co/2023/05/09/what-causes-app-translocation)).
  - The shell detects a translocated path, and the setup page says so.

### 1.12 Versioning

- **Tags:** git tags `vMAJOR.MINOR.PATCH`, the first being `v0.1.0`.
- **Version file:** `ahsync/version.py` holds `VERSION = "0.0.0"` in source, shown as "dev build" on Status, with no update check.
  - The workflow writes the tag into it before PyInstaller runs, so a release needs no version-bump commit.
- **Addon format version:** `EXPORT_VERSION` stays separate.
- **Status card:** shows "AHSync 0.1.0 · built 2026-11-02".

### 1.13 Repository structure: one shared core, a thin layer per OS

The goal: everything that decides what AHSync does is written and tested once. An OS only answers a few narrow questions (where folders live, how to start at login, where WoW usually is) and wraps the built app for download.

- **Shared, OS-free (almost all of the code):**
  - All of `ahsync/`: sync, engine, database, dashboard, setup logic, addon install, update check.
  - `ahsync_app/shell.py` (lifecycle, threads, single instance), `ahsync_app/tray.py` (the menu model and its pystray adapter; pystray already hides the OS), and the setup and autostart *decisions* (when to enable, self-heal, refuse a translocated path).
  - One PyInstaller recipe, `packaging/ahsync.spec`, with at most a few lines that differ by OS (the icon file and macOS's `BUNDLE`/`LSUIElement`).
  - One CI workflow: a single job matrix over `windows-latest` and `macos-15`, with the same steps. Only the final "package" step calls a per-OS script.
- **Per OS (small, and only facts):** `ahsync_app/platform/`:
  - `base.py`: a `Platform` protocol with about six members: `app_dirs()` (config and data folders), `wow_candidates()` (likely WoW install folders, as data), `autostart_enable(exe)`, `autostart_disable()`, `autostart_enabled()`, `restrict_file(path)` (the key file's permissions), and `run_location_problem(exe)` (macOS translocation; Windows always None).
  - `windows.py`: `%LOCALAPPDATA%`, `winreg` for the Run key, Windows WoW folder candidates.
  - `macos.py`: `~/Library/Application Support`, `plistlib` and `launchctl` for the LaunchAgent, macOS WoW folder candidates, translocation detection.
  - `__init__.py`: `current()`, the single place that reads `sys.platform` and picks the module.
  - Each module is a few dozen lines with no business logic. Both are tested on every OS by injecting fakes: a fake registry, a temp plist folder, a recorded `launchctl` call. So a Windows-only CI run still tests the macOS adapter, and vice versa.
- **Per OS packaging (recipes, not code):** `packaging/windows/ahsync.iss` (the Inno Setup script) and `packaging/macos/make_dmg.sh` (`hdiutil`). `tools/build.py` runs PyInstaller, then the current OS's packaging step, so one command builds on either machine.
- **Guardrail:** a new check in `tests/test_conventions.py`. `sys.platform`, `os.name`, `winreg`, `plistlib`, `launchctl` and `platform.system` may appear only under `ahsync_app/platform/` (and `tools/build.py`'s choice of packaging step). Any other use fails the build, so OS branches can't creep into the shared code.
- **Rejected:**
  - Separate Windows and Mac entry points or app packages: they would duplicate the lifecycle and drift.
  - Scattered `if sys.platform` checks: the current codebase has none in `ahsync/`, and it should stay that way.

### 1.14 Windows installer: Inno Setup, per-user, no admin

- **Tool:** Inno Setup 6. It's free, scriptable, the common choice for PyInstaller apps, and is expected to be preinstalled on `windows-latest` (verify on the first run; fallback `choco install innosetup`).
- **What it does:**
  - Installs per user, without admin rights (`PrivilegesRequired=lowest`), into `%LOCALAPPDATA%\Programs\AHSync`.
  - Adds a Start menu shortcut and an optional desktop shortcut.
  - Offers "Launch AHSync" on the last page, which opens the setup page on first run.
  - Installs over an existing version as an upgrade, keeping config and data, which live outside the install folder (1.4).
- **Start at login stays in the app.** The installer doesn't set it, so there is one source of truth: the setup page and the tray.
- **Uninstall:** quits AHSync if it is running, removes the Run entry, and leaves `%LOCALAPPDATA%\AHSync` (your data) unless you tick "also remove my AHSync data".
- **Still unsigned:** SmartScreen still warns once on the installer (decision 2); INSTALL.md shows how to get past it.
- **Rejected:** MSI/WiX (heavier tooling for no gain at this size); NSIS (fine, but a less readable script).

### 1.15 macOS signing and notarization

- **What:** sign `AHSync.app` with the user's **Developer ID Application** certificate, with the hardened runtime and a small entitlements file, then submit the dmg with `xcrun notarytool submit --wait` and staple it (`xcrun stapler staple`). Gatekeeper then opens it without warnings.
- **Where:** in the `build` job's macOS step only, between PyInstaller and `make_dmg.sh`, plus notarize after it. These are the signing hooks from 1.10, made active for macOS. The Windows hooks stay no-ops.
- **Secrets** (GitHub repository secrets, never in the repo, logs or artifacts):
  - `MACOS_CERT_P12` (the exported certificate, base64) and `MACOS_CERT_PASSWORD`, imported into a temporary keychain that the job deletes at the end.
  - `APPLE_TEAM_ID`.
  - Notary credentials: an App Store Connect API key (`NOTARY_KEY_ID`, `NOTARY_ISSUER_ID`, `NOTARY_KEY_P8`), preferred over an Apple ID with an app-specific password.
- **PyInstaller details:** pass `codesign_identity` and `entitlements_file` in the spec, so every bundled binary is signed. Python needs `com.apple.security.cs.allow-unsigned-executable-memory` and possibly `com.apple.security.cs.disable-library-validation` under the hardened runtime (verify on the first notarization; the notary log names any binary it rejects).
- **Pull requests and forks:** secrets are absent there, so the build falls back to PyInstaller's ad-hoc signature and skips notarization. Releases come only from tags in this repo.
- **What it changes elsewhere:**
  - INSTALL.md's macOS part shrinks to "open the dmg, drag AHSync to Applications". App Translocation still applies to an app run straight from the dmg or Downloads, so the nudge and its check stay (1.6, 1.11).
  - A signed app could use `SMAppService` for start-at-login instead of the LaunchAgent. The LaunchAgent stays in v1, as it is simpler and fully testable with fakes.
  - Signing is a prerequisite for a later auto-update on macOS (for example the Sparkle framework); out of scope for v1.
- **Rejected:** signing locally on the user's Mac and uploading by hand (releases would depend on one machine, and the notarize step would be manual).

## 2. Proposed milestones (after M21)

| # | Deliverable | Done when | Who leads |
|---|---|---|---|
| M22 | **Runtime foundations.** `ahsync/version.py`; the platform layer `ahsync_app/platform/` (`base.py` protocol, `windows.py` and `macos.py` with `app_dirs`, `wow_candidates`, `restrict_file`, `run_location_problem`; autostart members stubbed until M24) and its convention guardrail (DR16); `ahsync/export/addon_install.py` extracted from `tools/deploy.py`, with the Status lines and FM49; `ahsync_app/shell.py` and `__main__.py` running `run()` on a worker thread with a stop event (no tray yet); a second launch opens the dashboard (FM48); `test_conventions.py` covers `ahsync_app/`; `.github/workflows/ci.yml`, tests only, on both OSes; `.gitignore` gets `!packaging/*.spec`. User: create the repo, push, confirm green on both OSes. | DR1–DR4, DR16, FM48 and FM49 pass; `python -m ahsync_app` runs from source with per-user folders; WP1 byte-identical; CI green on Windows and macOS | Opus: the platform protocol and guardrail, lifecycle, threading, stop semantics, CI. Sonnet: the deploy extraction with tests, `version.py`, the folder members of the platform modules, SPEC marks |
| M23 | **First-run setup.** `app/wow_paths.py` (detection, check text); `app/setup.py` (config and key file writes; the key file locked down by `platform.current().restrict_file`); server setup mode; the `GET /setup` page in `web/static/`; `GET /api/app`; `POST /api/setup` (plus `check_only`); `engine/app_view.py` texts | DR5–DR8, SEC13, FM50 and D97 pass; a fresh profile reaches the dashboard through the page alone; the headless render matches the mockup | Opus: the write path, security, mode switch. Sonnet: detection list, page JS that only draws, texts with given strings |
| M24 | **Tray and autostart.** `ahsync_app/tray.py` (menu model plus pystray adapter, main thread); `ahsync_app/autostart.py` (the shared decisions: self-heal, translocation refusal) over the platform modules' autostart members (winreg; plistlib and launchctl); `POST /api/autostart`; the Status card toggle; the `--autostart` flag (no browser) | DR9–DR12, SEC14 and FM51 pass; manual test: Windows login starts it; the toggle round-trips on the Status page | Opus: autostart writers, platform branches, tray threading. Sonnet: the Status card JS, SPEC marks |
| M25 | **Update check.** `engine/updates.py`; `app/update_check.py` (daily, ETag, degrade); the `update_check` config key; Status alert, top-bar pill, tray item | DR13–DR15, FM52 and D98 pass; engine branch coverage 100% | Sonnet (given texts and a documented JSON), with Opus review of the scheduler hook |
| M26 | **Build and release.** macOS signing and notarization (1.15, DR18); `packaging/ahsync.spec` (onedir, windowed, BUNDLE with `LSUIElement`, hidden imports for pystray backends, `addon/` as data, icon); `tools/build.py` (local build and package); the workflow's `build` and `release` jobs with the signing hooks; the per-OS packaging steps `packaging/windows/ahsync.iss` (Inno Setup, per-user, upgrade and uninstall, 1.14) and `packaging/macos/make_dmg.sh` (dmg with an Applications symlink); `INSTALL.md`; SPEC §3.7, §5 and §7; ARCHITECTURE.md (app shell, build pipeline); CLAUDE.md Commands. User: tag `v0.1.0`, test on Windows and on the Apple Silicon Mac | A tag produces a Release with both assets (DR17); the Windows installer installs without admin, runs through setup to a dashboard on a clean user profile, upgrades over itself keeping data, and uninstalls cleanly; the macOS dmg passes the same run on the user's Apple Silicon Mac, including Forever's install folder there | Opus: spec file, workflow, docs that change meaning. Sonnet: `INSTALL.md` from this section's texts, `tools/build.py` packaging commands, SPEC built marks |

Order rationale:

- **CI first (M22),** so the macOS run exposes POSIX issues in the suite early.
- **Setup before the tray,** because the tray's Open dashboard needs a configured app.
- **Build last,** so every artifact test exercises the finished shell.

## 3. Scenario IDs (new family "Distribution and runtime", plus FM/SEC/D rows)

| ID | Given / Then (one line; the merge expands them) |
|---|---|
| DR1 | packaged shell on Windows and on macOS (platform injected) → data and config under `%LOCALAPPDATA%\AHSync` / `~/Library/Application Support/AHSync`; `python -m ahsync` still `./data`, `./config.toml` |
| DR2 | `VERSION = "0.0.0"` → Status "AHSync dev build", no update request; `"0.3.1"` → "AHSync 0.3.1" |
| DR3 | fresh AddOns folder → `AHProfit/` copied whole, `AHProfit_Data` .toc + placeholders, Status "Addon updated · restart WoW"; run again → nothing copied, no line |
| DR4 | AddOns already holds generated `MarketIntelligence.lua`, `PortfolioPlan.lua` and a stale `AHProfit/Old.lua` → generated files untouched, `Old.lua` removed, Status "/reload" when only existing files changed |
| DR5 | no config.toml → shell serves `/setup`, `GET /` redirects there, `GET /api/views/*` 404 until setup is done |
| DR6 | detection: a fake tree with `World of Warcraft/_classic_beta_/Interface/AddOns` → pre-filled, check text "found Interface/AddOns · 2 accounts"; none → empty field, "not found: enter the folder that holds Interface and WTF" |
| DR7 | `POST /api/setup` valid (pvp, horde, key "abc", autostart true) → config.toml holds `markets = ["forever.pvp.horde.us"]` and the path; key file written (mode 0600 on POSIX) and never in the response or log; autostart set; addon installed; normal run starts and the dashboard answers |
| DR8 | the same with `ruleset = "hardcore"`, then a folder without `Interface/AddOns`, then an empty key → 400 with the reason; 400; 200 with no key file written |
| DR9 | Windows fake registry, enable → Run value `AHSync` = `"<exe>" --autostart`; disable → removed; enable twice → one value |
| DR10 | macOS fake plist dir and launchctl log, enable → plist with Label, ProgramArguments, RunAtLoad true, `bootstrap gui/<uid>` called; disable → `bootout` and the file gone |
| DR11 | exe under `/private/var/folders/x/AppTranslocation/…` → enable refused, Status "Move AHSync to Applications…", nothing written |
| DR12 | tray menu model: configured, autostart on, update available → items Open dashboard, Status, Start at login (checked), "AHSync 0.4.0 available · download", Quit, in that order; Quit sets the stop event |
| DR13 | releases/latest `tag_name` `v0.4.0`, running `0.3.1` → `update_available`, text "AHSync 0.4.0 is available · you run 0.3.1", pill "update available", link = `html_url` |
| DR14 | `v0.3.1` or `v0.3.0` → nothing shown; `0.3.1-rc1`-style tags ignored |
| DR15 | second check within 24 h → no request; after 24 h → request with `If-None-Match`; 304 → nothing changes |
| DR16 | the convention test: `sys.platform`, `os.name`, `winreg`, `plistlib`, `launchctl` or `platform.system` anywhere outside `ahsync_app/platform/` (and the packaging choice in `tools/build.py`) → the test fails and names the file and line; the current tree passes |
| DR18 | the macOS build step with the signing secrets present → `codesign` with the Developer ID identity and hardened runtime, `notarytool submit --wait`, `stapler staple`, in that order, and the keychain deleted at the end; without the secrets (pull request) → ad-hoc signature, no notarize, the build still passes (command lines checked with a fake runner) |
| DR17 | `tools/build.py` on each OS → the same PyInstaller recipe, then exactly one packaging step (Inno Setup on Windows, `make_dmg.sh` on macOS); artifact names `AHSync-<ver>-windows-x64-setup.exe` and `AHSync-<ver>-macos-arm64.dmg` (the command lines checked with a fake runner, no real build in tests) |
| FM48 | packaged shell starts while the lock is held → opens `http://localhost:8765`, exits 0, no second run; `python -m ahsync` still prints "already running" (FM15) |
| FM49 | `wow_path` without `Interface/AddOns`, then an unwritable AddOns → Status warn text per case; the run continues; nothing half-copied |
| FM50 | config.toml written but the key file write fails → 500 `{"error": "key_not_saved"}`, config kept, Status line, no key in any message |
| FM51 | registry/plist write raises → `POST /api/autostart` 500 `autostart_not_set`, Status "Start at login not set", toggle shows the real state |
| FM52 | update check offline, then 404, then 403, then a body without `tag_name` → nothing shown, one log line each, next check tomorrow |
| SEC13 | `POST /api/setup` right token and Origin → accepted; wrong Origin → 403 with no files written; no token → 403; `GET /api/setup` → 405; the key never appears in a response |
| SEC14 | `POST /api/autostart {"enabled": true}` right token → 200 `{"set": true, "enabled": true}`; wrong Origin → 403, unchanged; `"enabled": "yes"` → 400; GET → 405 |
| D97 | `GET /api/app` payload: `version_text`, `update` (null or `{text, url, pill}`), `autostart` `{enabled, available, note}`, `api_key_set`, `wow_path`, `wow_check` — every text engine-made |
| D98 | Status "AHSync app" card lines: version, Start at login, Update, Data folder (path inside the app folder only), API key set/none |

## 4. Risks

- **macOS is untested until a real Mac runs it.** That covers pystray plus pyobjc inside a PyInstaller bundle, hidden imports, `LSUIElement`, hardened-runtime entitlements and notarization, and LaunchAgent behaviour.
  - Mitigation: the user has an Apple Silicon Mac (decision 6). M26 is done only after the dmg passes there, and the first Mac release goes to friends only after that test.
- **App Translocation breaks path-based autostart.** Mitigated by the dmg's Applications nudge and the detection in 1.6.
- **SmartScreen/Gatekeeper fatigue for friends.** Without signing, INSTALL.md with screenshots is the only mitigation.
- **Runner drift.** `macos-latest` moved to macOS 26 in June 2026; pin `macos-15` and revisit yearly. The Lua 5.1 install on runners is unverified; fallbacks are listed.
- **Two processes writing the same files.** The shell installs the addon while a running AHSync could be exporting. The lock check comes first, so only the lock owner installs.
- **PowerShell differences.** `tools/test.ps1` runs under pwsh 7 on macOS. Path separators and `Get-Command lua5.1` behave the same, but the first CI run may need small fixes (Opus, M22).
- **The key sits in a plain file.** Accepted for a friends-only tool; documented in SPEC §3.6 and INSTALL.md.

## 5. Open questions

Answered 2026-11-01 (decisions 5–10 in §0). The repo is `ctng1213/WowAuction`, public. Its history was rewritten to the GitHub noreply address before the first push. Still open:

1. **Forever's macOS install folder** (`/Applications/World of Warcraft/_classic_beta_`, unverified): checked on the user's Mac during M23 or M26.
2. **Hardened-runtime entitlements** the bundled Python needs: settled by the first notarization (1.15).

## 6. The user's manual steps

1. **Scan the history for secrets before any push.**
   - The working tree is clean: `tests/fixtures` has no key strings, `docs/config.toml` holds only `[realms]`, and `.gitignore` covers `/data/`, `config.toml` and `*.key`.
   - The history scan runs in the orchestrating session before the push (key-shaped strings; added files named `config.toml`, `data/` or `*.key`).
   - If a key ever appears, rotate it at auctionhouse.fyi and rewrite history before pushing.
2. **Create the GitHub repository.** Done on 2026-11-01: `ctng1213/WowAuction`, public, pushed.
3. **After M22 lands,** check the Actions tab: `test` green on Windows and macOS.
4. **Add the macOS signing secrets before the first tag** (1.15): export the Developer ID Application certificate as a .p12 from Keychain Access, create an App Store Connect API key for notarization, and add the six secrets under the repo's Settings → Secrets and variables → Actions.
5. **Tag the first release after M26.** Run `git tag v0.1.0 && git push origin v0.1.0`, wait for the Release, and download both assets.
6. **Test the Windows installer** on a clean user account: SmartScreen → Run anyway → install → Launch AHSync → setup → dashboard → log off and on → the tray icon appears.
7. **Test the dmg on the Mac:** drag to Applications → opens without a warning → setup → dashboard → log out and in.
8. **Send friends** the Release link and `INSTALL.md`.

## 7. Non-goals (v1)

- Auto-install of updates.
- Windows code signing.
- Automatic updates (the macOS signing makes them possible later).
- Linux.
- An Intel macOS build.
- A Settings page beyond setup.
- A tray "Setup…" item.
- Migrating a developer's `./data` into the per-user folder.
- An MSI installer, or putting the Windows installer on winget or another store.

## 8. Mockup

The setup page, the top-bar update pill and the Status page's "AHSync app" card are in `docs/mockups/dashboard.html`:

- **Setup page:** in the real app it shows alone at `/setup`, with no nav.
- **"AHSync app" card:** includes its states.
