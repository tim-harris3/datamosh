; Inno Setup 6 script for the datamosh desktop app.
; Build dist\datamosh\ first (packaging\build.ps1), then compile this;
; build.ps1 -Installer does both and injects the version from pyproject.toml.

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppName "datamosh"
#define MyAppExeName "datamosh.exe"
#define MyAppURL "https://github.com/tim-harris3/datamosh"

[Setup]
; Never change AppId between releases -- it is what makes an install an upgrade.
AppId={{8C1B7A64-2E5D-4F1B-9A3E-D47C60A2B9E1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Tim Harris
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
; Per-user install works without UAC (renders/media live in the user's
; Videos\datamosh anyway); the dialog still offers an all-users install.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=..\datamosh\ui\static\datamosh.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist\installer
OutputBaseFilename=datamosh-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\datamosh\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
