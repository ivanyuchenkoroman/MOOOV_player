; Inno Setup — установщик MOOOV_player для Windows.
; Собирается автоматически из build_windows.bat (нужен Inno Setup 6).
#define AppName "MOOOV_player"
#define AppVersion "1.1.1"

[Setup]
AppId={{6F2B8E51-3C1A-4D7E-9B0A-4D4F4F4F5601}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ivaniuchenko Roman for mooov.team
AppPublisherURL=https://mooov.team
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-win-setup
SetupIconFile=..\MOOOV_player.ico
; оформление в стиле плеера: тёмное окно, лого M▶ (картинки — make_installer_art.py, нужен Inno Setup 6.6+)
WizardStyle=modern dark includetitlebar
WizardBackColor=$121212
WizardImageFile=wizard_large.png
WizardSmallImageFile=wizard_small.png
DisableWelcomePage=no
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Registry]
; «Открыть с помощью → MOOOV_player» для видео и кадров (не перехватывает приложение по умолчанию)
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppName}.exe"" ""%1"""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".mov"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".mp4"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".m4v"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".avi"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".mkv"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".exr"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".png"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".jpg"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".tif"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppName}.exe\SupportedTypes"; ValueType: string; ValueName: ".tiff"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\.exr\OpenWithProgids"; ValueType: string; ValueName: "MOOOV_player.file"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.mov\OpenWithProgids"; ValueType: string; ValueName: "MOOOV_player.file"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "MOOOV_player.file"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\MOOOV_player.file"; ValueType: string; ValueData: "MOOOV_player media"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\MOOOV_player.file\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppName}.exe,0"
Root: HKA; Subkey: "Software\Classes\MOOOV_player.file\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppName}.exe"" ""%1"""
; правый клик по папке → «Open in MOOOV_player» (для секвенций)
Root: HKA; Subkey: "Software\Classes\Directory\shell\MOOOV_player"; ValueType: string; ValueData: "Open in MOOOV_player"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Directory\shell\MOOOV_player"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#AppName}.exe,0"
Root: HKA; Subkey: "Software\Classes\Directory\shell\MOOOV_player\command"; ValueType: string; ValueData: """{app}\{#AppName}.exe"" ""%1"""

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Launch MOOOV_player"; Flags: nowait postinstall skipifsilent
