#define MyAppName "INDUS TRANSPORTS LLC Dispatch Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "INDUS TRANSPORTS LLC"
#define MyAppExeName "IndusDispatchConsole.exe"

[Setup]
AppId={{58C2541E-1C02-44EE-9F81-8B8D7F92D1B1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Indus Dispatch Agent
DefaultGroupName=Indus Dispatch Agent
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=IndusDispatchConsoleSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Indus Dispatch Agent"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Indus Dispatch Agent"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Indus Dispatch Agent"; Flags: nowait postinstall skipifsilent
