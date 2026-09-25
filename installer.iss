; installer.iss — Inno Setup script for the BESS Tracker desktop app.
;
; Build it with:  python tools\build_release.py
; That script writes installer\version.iss from services\version.py (an .iss
; file cannot import Python), freezes the exe, builds the clean database and
; then calls ISCC on this file.
;
; What it ships, and nothing else: the exe, a pristine schema-only database
; built by tools\make_clean_db.py, and the setup guide. The owner's own data
; files, the sync configuration with its tokens, database backups, field photos
; and the report data sets are never packaged.

#ifexist "installer\version.iss"
  #include "installer\version.iss"
#else
  #error installer\version.iss is missing. Run: python tools\build_release.py  (it writes the version from services\version.py)
#endif

#define AppName "BESS Tracker"

[Setup]
; Fixed AppId: every future version upgrades this installation in place
; instead of appearing a second time in Apps & features. Never change it.
AppId={{515103F8-8CAB-4DAD-899D-CC2E04639D79}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=Field Service Team
; The database lives next to the exe, so the install folder MUST be writable by
; the user who runs the app. That rules out Program Files ({autopf}).
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName} {#AppVersion}
OutputDir=installer
OutputBaseFilename=BESS_Tracker_Setup_{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\BESS Tracker.exe"; DestDir: "{app}"; Flags: ignoreversion
; Schema only, zero records. onlyifdoesntexist so an upgrade never overwrites
; the database in place, uninsneveruninstall so uninstalling never deletes it.
Source: "build\clean\pv_bess_tracker.db"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "docs\SETUP_FOR_A_NEW_TEAM.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\BESS Tracker.exe"
; No shortcut for the guide: Windows has no default handler for .md, so the
; shortcut would open an "how do you want to open this file" dialog. The file
; sits in the installation folder.
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\BESS Tracker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BESS Tracker.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only an empty folder is removed. The database, the photos the app wrote and
; the sync configuration are left exactly where they are.
Type: dirifempty; Name: "{app}"
