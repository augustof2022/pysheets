; Inno Setup script: wraps dist\PySheets into PySheets-Setup.exe
;   iscc /DAppVersion=0.5.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.5.0"
#endif

[Setup]
AppId={{6E7F2D1C-5B9A-4B7E-9C51-2F3A9D8E4C11}
AppName=PySheets
AppVersion={#AppVersion}
AppPublisher=PySheets
DefaultDirName={autopf}\PySheets
DefaultGroupName=PySheets
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PySheets-Setup-{#AppVersion}
SetupIconFile=pysheets.ico
UninstallDisplayIcon={app}\PySheets.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
; in-app updates run this installer silently while PySheets is open: close it, then relaunch
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "assoc"; Description: "Open .pysheets workbooks with PySheets"; GroupDescription: "File associations:"

[Files]
Source: "..\dist\PySheets\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PySheets"; Filename: "{app}\PySheets.exe"
Name: "{autodesktop}\PySheets"; Filename: "{app}\PySheets.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.pysheets"; ValueType: string; ValueName: ""; ValueData: "PySheets.Workbook"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\PySheets.Workbook"; ValueType: string; ValueName: ""; ValueData: "PySheets workbook"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\PySheets.Workbook\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\PySheets.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\PySheets.Workbook\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PySheets.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\PySheets.exe"; Description: "Launch PySheets"; Flags: nowait postinstall skipifsilent
; in-app update (setup.exe /SILENT /relaunch=1): start the new version when done
Filename: "{app}\PySheets.exe"; Flags: nowait runasoriginaluser; Check: RelaunchAfterUpdate

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
