[Setup]
AppName=BESS Tracker
AppVersion=1.0
AppPublisher=Field Service Team
DefaultDirName={autopf}\BESS Tracker
DefaultGroupName=BESS Tracker
OutputDir=installer
OutputBaseFilename=BESS_Tracker_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\BESS Tracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\pv_bess_tracker.db"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{group}\BESS Tracker"; Filename: "{app}\BESS Tracker.exe"
Name: "{group}\Uninstall BESS Tracker"; Filename: "{uninstallexe}"
Name: "{autodesktop}\BESS Tracker"; Filename: "{app}\BESS Tracker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BESS Tracker.exe"; Description: "Launch BESS Tracker"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: dirifempty; Name: "{app}"
