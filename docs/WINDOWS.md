# Uoo Boot Manager for Windows 11

<img src="../assets/icons/png/uoo-64.png" width="64" height="64" alt="Uoo Boot Manager icon">

The Windows edition is a native C#/.NET 10 WPF desktop with the system Fluent
theme, light/dark appearance, keyboard navigation, and administrator actions
through Windows UAC. It manages Windows Boot Manager's BCD store and local UEFI
registrations. It does not edit Ubuntu's GRUB configuration from Windows.

## Install and start

Download the matching **windows-x64-setup.exe** (Intel/AMD PCs) or
**windows-arm64-setup.exe** (Windows on Arm). Run the installer and open **Uoo
Boot Manager** from Start. The installer includes the .NET runtime. Windows 11
build 22000 or later is required. The current packages are unsigned; no signing
certificate is configured for this project.

Installation puts the desktop and administrator helper in the protected
`C:\Program Files\Uoo Boot Manager` directory. Installation and uninstallation
do not change BCD, firmware settings, or boot files. Removing the app preserves
its recovery backups under `%ProgramData%\Uoo\BootManager` and any boot settings
previously applied through the app.

The ZIP contains the same self-contained application. It supports the demo
and available read-only inspection from a user folder. Install the application
before using administrator actions; user-writable elevated application code is
rejected. The desktop stays under your normal account, and each live operation
uses the fixed installed helper through an authenticated local pipe.

```powershell
& 'C:\Program Files\Uoo Boot Manager\Uoo.BootManager.exe'
& 'C:\Program Files\Uoo Boot Manager\Uoo.BootManager.exe' --demo
```

The complete demo uses in-memory boot configuration. Only explicitly chosen
JSON import and export files are read or written; demo actions never contact
the live boot provider.

## Windows features

| Page | Windows behavior |
| --- | --- |
| Windows boot | Default Windows installation, boot order, timeout, menu visibility, one-time selection, rename/copy/remove loader entries |
| Entry options | Safe mode, alternate command shell, boot log, driver names, base video, recovery, graphical or legacy Windows boot menu |
| Firmware | UEFI order and explicit inclusion/exclusion, one-time selection, registration of an existing local EFI loader, removal of restorable local registrations |
| Backups | Manual and automatic private BCD/firmware backups, integrity inspection, restore, delete, typed configuration import/export |
| Diagnostics | Running Windows loader, administrator access, boot mode, Secure Boot, BitLocker, and repair prerequisites |
| Repair | Rebuild the running Windows installation's UEFI boot files with BCDBoot on a verified local GPT/FAT32 system ESP |

Windows Boot Manager cannot load Linux kernels or customize its background like
GRUB. Use a local Linux EFI loader registration to select an installed UEFI
Linux bootloader. Windows kernel options are presented as the typed Windows
options above; arbitrary BCDEdit commands and raw scripts are not exposed.

The running/default/last Windows loader and Windows Boot Manager firmware
registration are protected from deletion. One-time choices are guarded until
cleared. Firmware order changes preserve all existing registrations. Existing
EFI applications must be present on a verified writable local GPT ESP; Uoo does
not install another operating system or download a bootloader.

Changes require fresh boot-state revisions and an automatic backup. A failed
change triggers rollback; an interrupted operation retains a private journal
for recovery when reopened with administrator access. Raw BCD and EFI recovery
data stays in administrator-owned storage. Portable JSON contains validated
menu settings, names, and typed loader options for existing local entry IDs;
it does not execute scripts or import another machine's firmware inventory.

Uoo reports BitLocker protection before boot changes and requires an explicit
recovery-key acknowledgment when protection is on or its state cannot be read.
It does not suspend encryption, disable Secure Boot, enable test signing, or
reboot automatically.

UEFI repair uses the currently running Windows directory and an explicitly
verified system ESP. It backs up BCD plus the Microsoft/fallback EFI files it
may touch, preserves other vendors' directories, and retains menu settings and
firmware order. It requires typing the displayed confirmation. Legacy BIOS,
recovery-environment/offline Windows repair, and unsupported storage layouts
are not presented as verified UEFI repair targets.

## Build

On Windows with the .NET 10 SDK and Inno Setup 7:

```powershell
dotnet run --project windows/Uoo.Tests -c Release
dotnet run --project windows/Uoo.Platform.Tests -c Release
./windows/scripts/build.ps1 -Runtime win-x64
./windows/scripts/build.ps1 -Runtime win-arm64
```

The default native platform tests require an elevated disposable Windows
machine. They create an isolated BCD file for WMI mutation tests, validate
private recovery backups, and inspect the running system read-only. They
verify that the system BCD inventory is unchanged.
Never run experimental live boot tests on a machine without recovery access.

If GNU Make, Bash, and PowerShell 7 are available on the Windows build host,
the root Makefile also provides `make windows-test` for the portable
model/protocol suite and `make windows WINDOWS_RUNTIME=win-arm64` for packaging
(the default runtime is `win-x64`). Pass script options with
`WINDOWS_BUILD_ARGS`, for example
`make windows WINDOWS_BUILD_ARGS="-SkipInstaller"` to produce only the ZIP.
On a non-Windows host, ZIP-only mode is selected automatically. On Windows,
the default still builds both the ZIP and installer, and checks for the Inno
compiler before publishing or replacing any build outputs.
These targets require the same .NET SDK and installer prerequisites as the
commands above. The platform and live integration suites remain explicit
commands for a suitable disposable Windows machine.

On Ubuntu/Debian or Arch/Manjaro, `make deps-windows` installs missing
PowerShell 7 and .NET 10 SDK dependencies for cross-building the Windows ZIP.
Use `make deps-windows DEPS_ARGS=--dry-run` to preview. Then run:

```sh
make windows-test
make windows
```

These commands run the portable model/protocol tests and build the Windows
payload; they do not run the Windows desktop on Linux. Creating an installer
and running native platform/live tests still requires a Windows host.

The .NET runtime is included in the ZIP and installer. `build.ps1` stages under
`build/windows` and writes packages and checksums under `dist/windows`.
`install-build-tools.ps1` downloads the official Inno Setup 7.1.0 compiler with
its SHA256 pinned. The compiler installer is used only on the build machine.

## GitHub builds and releases

For Microsoft Store distribution, follow the [Store publishing guide](WINDOWS-STORE.md).
Store submission requires a separate signing and certification process; the
GitHub packages described below are currently unsigned.

**Actions → Build Windows 11 packages** builds both architectures. The x64 job
uses Windows Server 2025; the ARM64 job uses the actual Windows 11 ARM runner.
Each builds the native desktop/helper, runs model/protocol and real offline-store
WMI tests, and exercises live change/restore transactions inside its disposable
Windows guest. The live suite verifies boot menu and loader operations, UEFI
choices where available, typed imports, reconstruction of a missing EFI loader
through BCDBoot and EFI-file restoration where the
system ESP is supported, and interrupted-write recovery. It restores the
original BCD/firmware inventory and EFI file hashes. The app is then installed,
its native demo pages rendered, and uninstalled. Desktop PNGs, installed-helper
inspection, and transaction reports are uploaded separately as
`windows-verification-<architecture>`.

Download `windows-packages` for both installers, both ZIPs, and
`SHA256SUMS-Windows`. Individual packages are in `windows-build-x64` and
`windows-build-arm64`. Actions downloads expire after 30 days. Pushing the
matching `v2.2.0` version tag attaches Windows downloads to a draft GitHub
release, alongside the Linux assets. Publish the reviewed draft to make those
files available on the Releases page.

The live suite requires explicit `UOO_DISPOSABLE_WINDOWS=1` and a GitHub-hosted
runner environment; it is not enabled by the default test command. It changes
the disposable Windows guest's boot state and restores it, while the workspace
host is untouched. CI does not reboot Windows. These results do not establish
recovery from power loss, every physical firmware implementation, or repair
of an offline or unbootable Windows installation.

The backend uses Microsoft's [BCD WMI provider](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/bcd/about-bcd)
and [BCDBoot](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/bcdboot-command-line-options-techref-di).
The desktop uses the [built-in WPF Fluent theme](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/whats-new/net90).
