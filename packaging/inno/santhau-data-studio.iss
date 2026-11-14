; G0-A / A8 packaging spike - Inno Setup 6 script.
; UTF-8 BOM preserves Vietnamese text in both ISPP and installer dialogs.
;
; Layout follows Master Spec v1.3 §2.2: the program installs per-user under
; %LocalAppData%\Programs so no elevation is required, and the data root is chosen by
; the user at first run and is never inside Program Files.
;
; Build:  iscc /DStageDir=<absolute path to build\stage> packaging\inno\santhau-data-studio.iss

#define MyAppName      "Săn Thầu — Kho dữ liệu"
#define MyAppVersion   "0.0.1-RC3-10"
#define MyWindowsVersion "0.0.1.10"
#define MyAppPublisher "Gia Su AI"
#define MyAppExeName   "SanThauDataStudio.exe"

#ifndef StageDir
  #error StageDir must be defined: iscc /DStageDir=D:\path\to\build\stage
#endif

[Setup]
AppId={{7C1F4D9E-8A2B-4F6C-9D31-0E5B7A2C4F88}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyWindowsVersion}
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductVersion={#MyWindowsVersion}
VersionInfoProductTextVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://tutor.ai.vn
DefaultDirName={localappdata}\Programs\SanThauDataStudio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: no UAC prompt, matching "không cần admin" in §0.1.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\build\inno
OutputBaseFilename=SanThau-Data-Studio-Setup-0.0.1-RC3-10
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; The spike must not pretend to be a signed release.
; Authenticode is mandatory at G4, not here.
DisableWelcomePage=no
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Never delete the user's data root on uninstall (a §11 hard fail condition).
UninstallFilesDir={app}
; Without these, uninstalling while the app or collector is running leaves every
; locked DLL behind in {app} (observed in the G0-A smoke run: 37 files remained).
CloseApplications=yes
RestartApplications=no
; Both GUI and worker register a runtime mutex. [Code] waits for graceful shutdown.

[Languages]
Name: "vietnamese"; MessagesFile: "Vietnamese.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên màn hình"; GroupDescription: "Tùy chọn thêm:"; Flags: unchecked

[Files]
; Whole Nuitka standalone folder: exe + Qt runtime + Python runtime + QML modules.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Licensing material shipped next to the binaries, per §2.2 topology.
Source: "..\..\LICENSES\*"; DestDir: "{app}\LICENSES"; Flags: ignoreversion recursesubdirs
Source: "..\..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Gỡ {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Mở {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Intentionally empty. Removing user data during uninstall is forbidden; the data root
; is created by the application under a user-chosen path and is left untouched.

[Code]
// The installer owns the signal until file operations finish. Every GUI/worker observes it.
// Never kill a writer mid-commit: refuse installation if graceful shutdown times out.
function ShutdownMarker: String;
begin
  Result := ExpandConstant('{localappdata}\SanThauDataStudio\shutdown.flag');
end;

function SignalAppShutdown: Boolean;
var
  Attempt: Integer;
begin
  Result := False;
  ForceDirectories(ExtractFileDir(ShutdownMarker));
  if not SaveStringToFile(ShutdownMarker, '1', False) then exit;
  Sleep(1000);
  for Attempt := 1 to 120 do
  begin
    if not CheckForMutexes('Local\SanThauDataStudio-Runtime') then
    begin
      Result := True;
      exit;
    end;
    Sleep(250);
  end;
  DeleteFile(ShutdownMarker);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not SignalAppShutdown then
    Result := 'Bộ thu thập đang lưu dữ liệu. Hãy dừng tại màn Tác vụ thu thập rồi thử lại.';
end;

function InitializeUninstall: Boolean;
begin
  Result := SignalAppShutdown;
  if not Result then
    MsgBox('Bộ thu thập đang lưu dữ liệu. Hãy dừng tại màn Tác vụ thu thập rồi thử lại.', mbError, MB_OK);
end;

procedure DeinitializeSetup;
begin
  DeleteFile(ShutdownMarker);
end;

procedure DeinitializeUninstall;
begin
  DeleteFile(ShutdownMarker);
end;

// Guard against the one uninstall mistake that would violate §11: deleting collected data.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Log('Uninstall removed program files only. Data root is preserved by design.');
  end;
end;
