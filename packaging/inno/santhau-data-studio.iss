; G0-A / A8 packaging spike - Inno Setup 6 script.
; ASCII-only source: ISPP reads .iss as ANSI unless a BOM is present, and Vietnamese
; strings here would be mis-decoded. Product-facing Vietnamese text lives in the QML.
;
; Layout follows Master Spec v1.3 §2.2: the program installs per-user under
; %LocalAppData%\Programs so no elevation is required, and the data root is chosen by
; the user at first run and is never inside Program Files.
;
; Build:  iscc /DStageDir=<absolute path to build\stage> packaging\inno\santhau-data-studio.iss

#define MyAppName      "San Thau Data Studio"
#define MyAppVersion   "0.0.1"
#define MyAppPublisher "Gia Su AI"
#define MyAppExeName   "SanThauDataStudio.exe"

#ifndef StageDir
  #error StageDir must be defined: iscc /DStageDir=D:\path\to\build\stage
#endif

[Setup]
AppId={{7C1F4D9E-8A2B-4F6C-9D31-0E5B7A2C4F88}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://tutor.ai.vn
DefaultDirName={localappdata}\Programs\SanThauDataStudio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: no UAC prompt, matching "không cần admin" in §0.1.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\build\inno
OutputBaseFilename=SanThau-Data-Studio-Setup-0.0.1-spike
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; The spike must not pretend to be a signed release.
; Authenticode is mandatory at G4, not here.
DisableWelcomePage=no
UninstallDisplayName={#MyAppName}
; Never delete the user's data root on uninstall (a §11 hard fail condition).
UninstallFilesDir={app}
; Without these, uninstalling while the app or collector is running leaves every
; locked DLL behind in {app} (observed in the G0-A smoke run: 37 files remained).
CloseApplications=yes
RestartApplications=no
; The GUI does not create a named mutex yet, so Inno falls back to its "files in use"
; detection. Establishing an app-wide mutex is a G2 item (worker/controller lifecycle).

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional:"; Flags: unchecked

[Files]
; Whole Nuitka standalone folder: exe + Qt runtime + Python runtime + QML modules.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Licensing material shipped next to the binaries, per §2.2 topology.
Source: "..\..\LICENSES\*"; DestDir: "{app}\LICENSES"; Flags: ignoreversion recursesubdirs
Source: "..\..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Intentionally empty. Removing user data during uninstall is forbidden; the data root
; is created by the application under a user-chosen path and is left untouched.

[Code]
// Ask a running GUI to exit before files are replaced/deleted. Restart Manager did not close
// this Qt app during silent runs, so the app polls this named event instead (see
// platform_win/shutdown_signal.py). The wait gives Qt time to tear the window down.
procedure SignalAppShutdown;
var
  Marker: String;
begin
  Marker := ExpandConstant('{localappdata}\SanThauDataStudio\shutdown.flag');
  ForceDirectories(ExtractFileDir(Marker));
  SaveStringToFile(Marker, '1', False);
  Sleep(4000);
  DeleteFile(Marker);
end;

procedure InitializeUninstall;
begin
  SignalAppShutdown;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    SignalAppShutdown;
end;

// Guard against the one uninstall mistake that would violate §11: deleting collected data.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Log('Uninstall removed program files only. Data root is preserved by design.');
  end;
end;
