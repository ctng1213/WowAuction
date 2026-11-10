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

## 7. Connecting Claude (AI assistant)

Claude can read your AHSync data and answer questions about it. It can only
read: it can't change your plan, rules or settings.

**Turn it on.** On the Status page, press **Enable** on the
**AI assistant (Claude)** card. The card then shows two tabs, **Claude Desktop**
and **Claude Code**.

**Claude Code** takes one command. Copy it from the card and run it once in a
terminal. In Claude Code, `/mcp` then lists `ahsync`.

**Claude Desktop** reads a settings file, `claude_desktop_config.json`, that you
edit yourself. AHSync never changes Claude's files. The card finds the file and
says exactly what to change:

1. **Quit Claude Desktop completely.** On Windows, right-click its icon by the
   clock and choose Quit; closing the window is not enough. On a Mac, choose
   Claude → Quit Claude. Claude saves its own settings into this same file
   while it runs, so it can save over your change.
2. Open the file at the path the card shows. Copy the path with its **Copy**
   button rather than typing it.
3. Make the change the card describes, exactly where it says. For example,
   "right after the file's very first `{`" means the first character of the
   file. A `{` further down that starts another section, such as
   `"preferences"`, is the wrong place: Claude ignores `mcpServers` there
   without a word.
4. Save, then press **Check again** on the card. It should say
   **✓ Claude Desktop is set up for AHSync**. If it says something else, follow
   what it says now; it reads the file again on every check.
5. Start Claude Desktop again. In a new chat, AHSync is listed among Claude's
   connectors. Ask "Which AHSync tools do you have?" to be sure.
6. Turn on web search in Claude, so it can check Wowhead for fishing levels and
   farming spots.

**Where the file is.** The card works this out for you, but in case you need
it:

| Claude Desktop | File |
|---|---|
| Windows, from the Microsoft Store | `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\claude_desktop_config.json` |
| Windows, from claude.ai/download | `%APPDATA%\Claude\claude_desktop_config.json` |
| Mac | `~/Library/Application Support/Claude/claude_desktop_config.json` |

Settings → Developer → Edit Config, which some guides mention, isn't in every
version of Claude Desktop. The Microsoft Store version has no Developer page.

**If AHSync doesn't show up in Claude**, check these in order:

1. AHSync is running, and the card says **running**. Claude can only reach
   AHSync while both are running.
2. The card says ✓. If it doesn't, follow what it says.
3. Claude was quit completely (step 1 above) and started again after the
   change.
4. Claude reads the file you changed. Claude's own log names the folder it
   uses: search it for `token cache location`. The folder in that line is
   where `claude_desktop_config.json` belongs.
   - Windows (Store): `%LOCALAPPDATA%\Claude\logs\main.log`
   - Mac: `~/Library/Logs/Claude/main.log`

   If that folder isn't the one the card shows, tell us. The card has a fixed
   list of places it looks, and this is how we find a new one.
5. In the same log, search for `ahsync`. When it works, the lines read
   `[LocalMcpServerManager] Connecting to ahsync` and then
   `Connected to ahsync (6 tools)`. No `ahsync` line at all, with
   `[localMcpBridge] no stdio servers connected`, means Claude found no
   `mcpServers` at the top level of the file it reads. `Connecting` with no
   `Connected` means the bridge didn't start: check the path in the entry. A
   `mcp-server-ahsync.log` beside `main.log` holds the bridge's own messages.

## 8. Updates

AHSync checks for a new version once a day. It downloads and verifies the update
by itself, then installs it the next time AHSync starts, or when you click
**Restart to update** (in the tray menu or on the Status page). Start at login
means a PC that is logged off daily updates within a day. A PC left on all the
time waits until AHSync is restarted or you click **Restart to update**.

Turn **Install updates automatically** off if you only want to be told about
new versions. **Get beta updates** is for testers: it also takes pre-releases,
before they are made the normal release.

## 9. Uninstall

- Windows: Settings, Apps, **AHSync**, Uninstall. It asks whether to also remove
  your AHSync data: settings, database, log and your auctionhouse.fyi key file.
  The default keeps them.
- Mac: choose **Quit AHSync** from the menu bar icon, then drag AHSync from Applications
  to the Bin. To remove everything, also delete
  `~/Library/Application Support/AHSync`,
  `~/Library/LaunchAgents/com.wowauction.ahsync.plist` and your key file
  `~/.ahsync/api_key`.

## 10. Building it yourself

- Unsigned macOS builds (forks, local builds): on macOS 15 or later, open the
  app once, then go to System Settings, Privacy & Security, and click **Open
  Anyway**. Or run `xattr -dr com.apple.quarantine /Applications/AHSync.app`.
- Local build: `python -m pip install -r requirements-app.txt "pyinstaller==6.22.*"`, then
  `python tools/build.py build 0.0.0`. On Windows you also need Inno Setup 6.
- Run from source: `python -m ahsync_app`.
