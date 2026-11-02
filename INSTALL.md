# Installing AHSync

AHSync is the companion app for the AHProfit addon in WoW: Forever. It keeps
auction house data up to date, works out what is worth doing, and installs the
AHProfit addon into your game folder by itself.

## 1. Download

Open the [Releases page](https://github.com/ctng1213/WowAuction/releases) and
download the one file for your computer:

- Windows: `AHSync-<version>-windows-x64-setup.exe`
- Mac (Apple Silicon only): `AHSync-<version>-macos-arm64.dmg`

Ignore the `.zip` and `SHA256SUMS` files. They are for the automatic updater.

## 2. Install on Windows

1. Run the setup file. Windows may show "Windows protected your PC" because the
   installer is not code-signed. Click **More info**, then **Run anyway**. You
   only do this once.
2. Click through the installer. It needs no administrator rights and installs
   into your own user folder.
3. Leave **Launch AHSync** ticked on the last page and click Finish.
4. The setup page opens (see section 4). After it, the dashboard shows in the
   AHSync window.
5. AHSync also puts an icon in the system tray (near the clock). Closing the
   window keeps AHSync running in the tray. To stop it, right-click the tray
   icon and choose **Quit AHSync**.

The window needs Microsoft's WebView2 runtime. Windows 11 and an up-to-date
Windows 10 already have it. If yours does not, the dashboard opens in your web
browser instead. To get the window, install the Evergreen WebView2 Runtime from
https://developer.microsoft.com/en-us/microsoft-edge/webview2/ .

## 3. Install on a Mac

1. Open the `.dmg`.
2. Drag **AHSync** onto the **Applications** shortcut in that window.
3. Open AHSync from the Applications folder. It is signed and notarized by
   Apple, so there is no warning.

Do not run it straight from the dmg or from Downloads. macOS then runs it from a
temporary location (App Translocation), which breaks **Start at login**. AHSync
shows an alert if it finds itself there. Move it to Applications and open it
again.

AHSync lives in the menu bar and has no Dock icon. Use the menu bar icon to open
the dashboard or to **Quit AHSync**.

## 4. First-run setup

The setup page asks for:

1. **Your WoW: Forever folder.** The folder that holds `Interface` and `WTF`.
   AHSync fills in what it finds. Press **Check** to confirm it.
2. **Your market.** Ruleset (Normal, PvP, RP) and Faction (Alliance, Horde).
3. **auctionhouse.fyi key.** Optional.

There is also a **Start AHSync when I log in** checkbox, on by default.
Press **Finish and start**. The first sync takes about a minute.

## 5. Your auctionhouse.fyi key

The key is stored in a plain file: `~/.ahsync/api_key` (on Windows,
`%USERPROFILE%\.ahsync\api_key`). Where your OS supports it, only you can read
that file. This is accepted for a friends-only tool. AHSync never shows the key
on the page and never writes it to its log.

## 6. Where your data lives

- Windows: `%LOCALAPPDATA%\AHSync`
- Mac: `~/Library/Application Support/AHSync`

Each holds `config.toml` and a `data` folder (database, backups, log).

## 7. Updates

AHSync checks for a new version once a day. It downloads and verifies the update
by itself, then installs it the next time AHSync starts, or when you click
**Restart to update** (in the tray menu or on the Status page). Start at login
means a PC that is logged off daily updates within a day. A PC left on all the
time waits until AHSync is restarted or you click **Restart to update**.

Turn **Install updates automatically** off if you only want to be told about
new versions. **Get beta updates** is for testers: it also takes pre-releases,
before they are made the normal release.

## 8. Uninstall

- Windows: Settings, Apps, **AHSync**, Uninstall. It asks whether to also remove
  your AHSync data. The default keeps it.
- Mac: choose **Quit AHSync** from the menu bar icon, then drag AHSync from Applications
  to the Bin. To remove everything, also delete
  `~/Library/Application Support/AHSync` and
  `~/Library/LaunchAgents/com.wowauction.ahsync.plist`.

## 9. Building it yourself

- Unsigned macOS builds (forks, local builds): on macOS 15 or later, open the
  app once, then go to System Settings, Privacy & Security, and click **Open
  Anyway**. Or run `xattr -dr com.apple.quarantine /Applications/AHSync.app`.
- Local build: `python -m pip install -r requirements-app.txt "pyinstaller==6.22.*"`, then
  `python tools/build.py build 0.0.0`. On Windows you also need Inno Setup 6.
- Run from source: `python -m ahsync_app`.
