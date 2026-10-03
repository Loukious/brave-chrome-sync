; SPDX-License-Identifier: MPL-2.0
#ifndef PayloadDir
  #error PayloadDir is required
#endif

[Setup]
AppId={{8B8E92D1-6597-4B94-A8E7-D891D7774315}-{#Channel}
AppName=Brave Chrome Sync
AppVersion={#AppVersion}
AppVerName=Brave Chrome Sync {#AppVersion}
AppPublisher=Loukious
AppPublisherURL=https://github.com/Loukious/brave-chrome-sync
AppUpdatesURL=https://github.com/Loukious/brave-chrome-sync/releases
DefaultDirName={localappdata}\Programs\BraveChromeSync-{#Channel}
DefaultGroupName=Brave Chrome Sync
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir={#OutputDir}
OutputBaseFilename=brave-chrome-sync-{#ReleaseTag}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\versions\{#ReleaseTag}\{#BrowserExe}
LicenseFile={#LicenseFile}

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}\versions\{#ReleaseTag}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\SyncUpdater.exe"; DestDir: "{app}"; DestName: "SyncBrowser.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\Brave Chrome Sync"; Filename: "{app}\SyncBrowser.exe"; IconFilename: "{app}\versions\{#ReleaseTag}\{#BrowserExe}"; AppUserModelID: "Loukious.BraveChromeSync.{#Channel}"
Name: "{autodesktop}\Brave Chrome Sync"; Filename: "{app}\SyncBrowser.exe"; IconFilename: "{app}\versions\{#ReleaseTag}\{#BrowserExe}"; Tasks: desktopicon; AppUserModelID: "Loukious.BraveChromeSync.{#Channel}"

[Run]
Filename: "{app}\SyncBrowser.exe"; Description: "Launch Brave Chrome Sync"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\SyncBrowser.exe"; Parameters: "--uninstall --root ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveGitHubUpdateTask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\versions"
Type: files; Name: "{app}\installation.json"
Type: files; Name: "{app}\current.json"
Type: files; Name: "{app}\pending.json"
Type: files; Name: "{app}\previous.json"
Type: files; Name: "{app}\update.lock"
Type: files; Name: "{app}\background-check.txt"
Type: files; Name: "{app}\last-check.json"
Type: files; Name: "{app}\updater-error.txt"
Type: files; Name: "{app}\scheduled-task-warning.txt"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Args: String;
begin
  if CurStep = ssPostInstall then begin
    Args := '--initialize --root "' + ExpandConstant('{app}') + '" --tag {#ReleaseTag} --channel {#Channel} --browser "versions/{#ReleaseTag}/{#BrowserExe}" --published {#PublishedAt}';
    if not Exec(ExpandConstant('{app}\SyncBrowser.exe'), Args, '', SW_HIDE,
                ewWaitUntilTerminated, ResultCode) then
      RaiseException('Could not initialize the browser installation.');
    if ResultCode <> 0 then
      RaiseException('Browser initialization failed. See updater-error.txt in the installation folder.');
  end;
end;
