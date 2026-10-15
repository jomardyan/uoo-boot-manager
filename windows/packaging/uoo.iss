#ifndef AppVersion
  #define AppVersion "2.2.0"
#endif
#ifndef PayloadDir
  #error PayloadDir must point to a validated self-contained publish directory
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\windows"
#endif
#ifndef CpuArch
  #define CpuArch "x64"
#endif

[Setup]
AppId={{1179B417-6013-4CFA-92E3-41C9F5FC07F5}
AppName=Uoo Boot Manager
AppVersion={#AppVersion}
AppPublisher=Hayk Jomardyan
AppCopyright=Copyright (c) 2026 Hayk Jomardyan
AppPublisherURL=https://github.com/jomardyan/uoo-boot-manager
DefaultDirName={autopf}\Uoo Boot Manager
DefaultGroupName=Uoo Boot Manager
UninstallDisplayIcon={app}\Uoo.BootManager.exe
SetupIconFile=..\Uoo.Desktop\uoo.ico
OutputDir={#OutputDir}
OutputBaseFilename=Uoo-Boot-Manager-{#AppVersion}-windows-{#CpuArch}-setup
SetupArchitecture=x64
#if CpuArch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
#endif
MinVersion=10.0.22000
PrivilegesRequired=admin
DisableProgramGroupPage=yes
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
SetupLogging=yes

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Icons]
Name: "{autoprograms}\Uoo Boot Manager"; Filename: "{app}\Uoo.BootManager.exe"
Name: "{autodesktop}\Uoo Boot Manager"; Filename: "{app}\Uoo.BootManager.exe"; Tasks: desktopicon

[Code]
procedure RunAcl(Arguments: String);
var
  ResultCode: Integer;
begin
  if not Exec(ExpandConstant('{sys}\icacls.exe'), Arguments, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    RaiseException('Windows could not protect the Uoo installation. Installation cannot continue.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppPath: String;
begin
  if CurStep = ssPostInstall then begin
    AppPath := ExpandConstant('{app}');
    RunAcl('"' + AppPath + '" /reset');
    RunAcl('"' + AppPath + '" /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX"');
    RunAcl('"' + AppPath + '\*" /reset /T /C');
    RunAcl('"' + AppPath + '" /setowner *S-1-5-32-544 /T /C');
  end;
end;

// No BCD, firmware, boot-file, BitLocker, or ProgramData cleanup runs during install/uninstall.
