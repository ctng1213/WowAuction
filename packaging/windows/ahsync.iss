; AHSync Windows installer, Inno Setup 6 (SPEC §3.7.12).
; Build from the repo root, after PyInstaller has produced dist\AHSync\:
;   ISCC /DAppVersion=0.4.0 packaging/windows/ahsync.iss
; Relative paths below resolve against this script's folder.

#ifndef AppVersion
  #error AppVersion is not defined. Pass /DAppVersion=<version> to ISCC.
#endif

[Setup]
; Fixed AppId so an upgrade is recognised as the same application.
; The doubled opening brace is Inno's escape for a literal brace.
AppId={{7D3F5A2E-9B41-4C8E-A6D2-3E5F1B9C0A47}
AppName=AHSync
AppPublisher=WowAuction
AppVersion={#AppVersion}
; Per user, no admin prompt; deliberately no PrivilegesRequiredOverridesAllowed.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\AHSync
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=AHSync
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\AHSync.exe
WizardStyle=modern
; CloseApplications would hang: it asks the app's windows to close and the
; AHSync window cancels its close (SPEC §3.7.1). The [Code] section quits the
; app through its stop file instead.
CloseApplications=no
RestartApplications=no
SetupLogging=yes
OutputDir=..\..\dist
OutputBaseFilename=AHSync-{#AppVersion}-windows-x64-setup

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\..\dist\AHSync\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; No stale PyInstaller file may survive an upgrade.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\AHSync"; Filename: "{app}\AHSync.exe"
Name: "{autodesktop}\AHSync"; Filename: "{app}\AHSync.exe"; Tasks: desktopicon

; There is no [Registry] section on purpose: the Run value for "Start at login"
; is written by the app itself (SPEC §3.7.5), never by the installer, so the
; uninstaller removes it in [Code] below.

[Run]
; Interactive install: the checkbox on the last page.
Filename: "{app}\AHSync.exe"; Description: "Launch AHSync"; Flags: postinstall nowait skipifsilent
; Silent upgrade (the app's own update): relaunch it, since the page above is
; skipped. The two entries never both run, so AHSync is launched once (SPEC §3.7.9).
Filename: "{app}\AHSync.exe"; Flags: nowait; Check: WizardSilent

[Code]
const
  StopFailedText = 'AHSync did not stop. Quit it from the tray icon, then run the installer again.';

{ Returns the folder holding the lock and stop files. }
function DataDir: String;
begin
  Result := ExpandConstant('{localappdata}\AHSync\data');
end;

{ True when no AHSync holds the lock: the app keeps ahsync.lock open without
  delete sharing, so a lock file that can be deleted is stale. }
function LockReleased(LockFile: String): Boolean;
begin
  Result := (not FileExists(LockFile)) or DeleteFile(LockFile);
end;

{ How many AHSync.exe processes run (WMI); 0 when WMI can't answer. }
function AHSyncProcessCount: Integer;
var
  Locator, Service, Processes: Variant;
begin
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := Service.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name = ''AHSync.exe''');
    Result := Processes.Count;
  except
    Result := 0;
  end;
end;

{ Quits a running AHSync the way tools/deploy.py --restart does: write
  ahsync.stop, which the app answers within seconds, and wait for the lock.
  Returns True when AHSync is stopped (or was never running). }
function StopAHSync: Boolean;
var
  LockFile, StopFile: String;
  Waited: Integer;
begin
  LockFile := DataDir + '\ahsync.lock';
  StopFile := DataDir + '\ahsync.stop';
  Result := LockReleased(LockFile);
  if not Result then
  begin
    SaveStringToFile(StopFile, 'stop requested by the AHSync installer', False);
    Waited := 0;
    while (not Result) and (Waited < 60000) do
    begin
      Sleep(500);
      Waited := Waited + 500;
      Result := LockReleased(LockFile);
    end;
    if not Result then
      DeleteFile(StopFile);
  end;
  { The app releases its lock about a second before its process ends (and an
    update's installer starts while the old AHSync is exiting): wait up to 10 s
    more for AHSync.exe to be gone, so no file in the install folder is still in use. }
  Waited := 0;
  while Result and (Waited < 10000) and (AHSyncProcessCount > 0) do
  begin
    Sleep(500);
    Waited := Waited + 500;
  end;
end;

{ Runs before files are copied; a non-empty result aborts with that message. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopAHSync then
    Result := StopFailedText;
end;

{ The uninstaller quits AHSync the same way; False aborts the uninstall. }
function InitializeUninstall: Boolean;
begin
  Result := StopAHSync;
  if not Result then
  begin
    if not UninstallSilent then
      MsgBox(StopFailedText, mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataRoot: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Remove the "Start at login" value the app wrote. }
    if RegValueExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AHSync') then
      RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AHSync');
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    { Default is to keep the user's data. }
    if not UninstallSilent then
    begin
      DataRoot := ExpandConstant('{localappdata}\AHSync');
      if MsgBox('Also remove my AHSync data (settings, database and log in ' + DataRoot + ')?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataRoot, True, True, True);
    end;
  end;
end;
