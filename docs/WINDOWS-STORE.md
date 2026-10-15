# Publish Uoo Boot Manager in the Microsoft Store

Maintainer instructions for the Windows 11 edition. Examples use **2.2.1**;
replace versions, certificate thumbprints, timestamp services, download hosts,
and account details with your release values.

For the current WPF application and Inno Setup installer, start with the
**EXE/MSI desktop-app submission route**. This recommendation follows the
existing packaging design; Microsoft still decides certification eligibility.
The repository does not currently produce a signed Store submission or an
MSIX package, and GitHub Actions does not submit apps to Partner Center.

For Linux distribution, see [Ubuntu publishing](PUBLISHING.md).

## 1. Prepare the publisher account

1. Register or complete a Windows developer account in
   [Microsoft Partner Center](https://partner.microsoft.com/dashboard).
   Complete the identity and account verification requested for your account.
2. Open **Apps and games → New product → EXE or MSI app**.
3. Check availability and reserve **Uoo Boot Manager**, or choose an available
   name. Keep the display name consistent with the application and installer.
4. Start a submission from the product's overview page.

Check the current account terms during registration rather than assuming a
particular fee or verification timeline.
[Name reservation](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/reserve-your-apps-name)
· [Submission checklist](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/create-app-submission)

## 2. Close the current packaging gaps

Microsoft accepts standalone EXE/MSI desktop installers. For this route, the
installer and its PE payload need trusted code signatures, the installer must
work silently, and the submitted HTTPS URL must identify immutable bytes.
An installation UAC prompt is permitted. The installer must work offline.
[EXE/MSI package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements)

| Requirement | Current Uoo state | Work before submission |
| --- | --- | --- |
| Offline installer | Self-contained x64/ARM64 Inno Setup EXEs include .NET | Verify installation without network access |
| Code signing | No release signing configured | Sign the payload, installer, and generated uninstaller |
| Silent setup | Inno Setup supports silent switches | Test the exact Store parameters and exit codes |
| Stable download URLs | GitHub workflows create draft assets | Host the final signed EXEs at public versioned HTTPS URLs |
| Store metadata | No Partner Center listing in this repository | Prepare listing, images, privacy information, and certification notes |
| Administrator operations | Desktop uses `asInvoker`; helper uses `requireAdministrator` | Explain and test UAC, protected installation paths, and consent |

Uoo installs to `Program Files` and rejects administrator actions from a
user-writable installation. Preserve that design. Installation and removal do
not change boot configuration; authenticated operations run only when requested
inside the application. See [Windows behavior and limits](WINDOWS.md).

## 3. Build and sign the release

### Build the payloads on Windows

Use the .NET 10 SDK, PowerShell 7, Inno Setup 7, and Windows SDK SignTool.
Run the applicable tests from the [Windows build guide](WINDOWS.md#build),
then stage both architectures from the repository root:

```powershell
./windows/scripts/build.ps1 -Runtime win-x64 -SkipInstaller
./windows/scripts/build.ps1 -Runtime win-arm64 -SkipInstaller
```

These commands create unsigned payloads under `build/windows/<runtime>/payload`
and preliminary ZIPs/checksums. Those ZIPs are not the Store installer.

### Sign and verify the payload before packaging it

Obtain a code-signing identity accepted under Microsoft's package requirements.
A self-signed development certificate is not a substitute. Configure your
certificate provider's protected signing workflow; keep private keys and
passwords out of the repository and command logs.

The following example assumes SignTool can access the certificate in your
Windows certificate store. Adapt it for a hardware or managed signing service:

```powershell
$thumbprint = 'YOUR_CODE_SIGNING_CERTIFICATE_THUMBPRINT'
$timestampUrl = 'https://YOUR_RFC3161_TIMESTAMP_SERVICE'
$ownedFiles = @(
    'Uoo.BootManager.exe', 'Uoo.BootManager.dll',
    'Uoo.BootHelper.exe', 'Uoo.BootHelper.dll',
    'Uoo.Core.dll', 'Uoo.Platform.dll'
)
foreach ($runtime in @('win-x64', 'win-arm64')) {
    $payload = Join-Path $PWD "build/windows/$runtime/payload"
    foreach ($name in $ownedFiles) {
        $file = Join-Path $payload $name
        & signtool.exe sign /sha1 $thumbprint /fd SHA256 /tr $timestampUrl /td SHA256 $file
        if ($LASTEXITCODE -ne 0) { throw "Signing failed: $file" }
        & signtool.exe verify /pa /all /v $file
        if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $file" }
    }
}
```

This signs Uoo's own files, not the entire dependency inventory. Inspect every
PE in the self-contained payload, including runtime and third-party files;
preserve valid vendor signatures and resolve unsigned or invalid files before
submission. For a useful first inventory on Windows:

```powershell
Get-ChildItem build/windows/*/payload -Recurse -File |
    Where-Object { $_.Extension -in '.exe', '.dll' } |
    Get-AuthenticodeSignature |
    Select-Object Path, Status, StatusMessage
```

Check other PE file types too, and verify final trust on a clean supported
Windows system. [SignTool signing and verification](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)

### Produce a signed installer and uninstaller

The current installer script has no signing hook. Add a release signing
configuration before creating a Store package. In the `[Setup]` section of
[the Inno script](../windows/packaging/uoo.iss), configure:

```ini
SignTool=storeSign
SignedUninstaller=yes
```

Define the `storeSign` tool using Inno's configuration or compiler `/S` option.
Its command must invoke your signing provider, timestamp the binary, and fail
if signing fails. Inno's `$f` placeholder supplies the file being signed.
[Inno SignTool directive](https://jrsoftware.org/ishelp/topic_setup_signtool.htm)
· [Signed uninstaller](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm)

For example, after adding those directives, compile an already signed x64
payload with a certificate-store signing command:

```powershell
$compiler = Join-Path $env:ProgramFiles 'Inno Setup 7/ISCC.exe'
$payload = (Resolve-Path 'build/windows/win-x64/payload').Path
$output = Join-Path $PWD 'dist/windows-store'
New-Item $output -ItemType Directory -Force | Out-Null
$signCommand = 'signtool.exe sign /sha1 YOUR_CERTIFICATE_THUMBPRINT /fd SHA256 /tr https://YOUR_RFC3161_TIMESTAMP_SERVICE /td SHA256 $f'
& $compiler "/SstoreSign=$signCommand" '/DAppVersion=2.2.1' '/DCpuArch=x64' "/DPayloadDir=$payload" "/DOutputDir=$output" 'windows/packaging/uoo.iss'
if ($LASTEXITCODE -ne 0) { throw 'Signed installer build failed.' }
```

Repeat with `win-arm64` and `/DCpuArch=arm64`. These commands require the signing
configuration described above; this guide does not add it automatically.
Do not rerun `build.ps1` after signing the payload: it recreates that directory.
Verify signatures on both final setup EXEs and the installed `unins*.exe`.
Generate final SHA256 checksums **after** signing, since signing changes bytes.

## 4. Test the exact Store installation experience

Use clean, disposable Windows 11 test systems for x64 and ARM64. Test the signed
installer in its normal interactive mode and with these Store parameters:

```text
/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-
```

Example for a disposable x64 test system:

```powershell
$installer = (Resolve-Path 'dist/windows-store/Uoo-Boot-Manager-2.2.1-windows-x64-setup.exe').Path
$install = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-' -Wait -PassThru
if ($install.ExitCode -ne 0) { throw "Install failed with exit code $($install.ExitCode)" }
& 'C:\Program Files\Uoo Boot Manager\Uoo.BootManager.exe' --demo
```

Confirm there is no setup wizard or automatic reboot. UAC may appear for the
administrator installation. Test launch from Start, light/dark appearance,
helper authentication, upgrade from the previous version, and uninstall. Check
that installation/uninstallation preserve boot state and that the documented
backups remain after removal. Do not perform experimental live boot changes
on a personal machine as a Store-installation test.

Inno exit code `0` means success. Inspect other results and map their actual
meaning in Partner Center's installer handling fields; do not classify all
nonzero codes as success. Test uninstall silently with the installed
`unins*.exe` using `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`.
[Setup switches](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)
· [Exit codes](https://jrsoftware.org/ishelp/topic_setupexitcodes.htm)
· [Uninstall switches](https://jrsoftware.org/ishelp/topic_uninstcmdline.htm)

## 5. Host the final installers

Provide a direct public HTTPS download for each signed EXE. Use immutable,
version-specific paths, for example:

```text
https://YOUR_DOWNLOAD_HOST/uoo/2.2.1/Uoo-Boot-Manager-2.2.1-windows-x64-setup.exe
https://YOUR_DOWNLOAD_HOST/uoo/2.2.1/Uoo-Boot-Manager-2.2.1-windows-arm64-setup.exe
```

A draft GitHub release, Actions artifact, authenticated link, temporary signed
URL, or `/releases/latest/download/...` link is unsuitable. Use hosting/CDN
storage under your control and verify downloads work without signing in.
Download each file again and compare its SHA256 to the signed release output.
Never replace bytes at a submitted URL; publish a new version at a new URL.
[Hosting and immutable-package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements)

## 6. Complete the Partner Center submission

Add both installers in **Packages**:

| Field | x64 installer | ARM64 installer |
| --- | --- | --- |
| App type | EXE | EXE |
| Package URL | Final signed x64 HTTPS URL | Final signed ARM64 HTTPS URL |
| Architecture | x64 | arm64 |
| Installer parameters | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-` | Same |
| Language | Only languages the app actually supports | Same |

Declare Windows 11 build **22000 or later** in the available requirements fields
and store description. Keep the installer's `MinVersion` enforcement. Do not
claim Windows 10 support simply because the EXE submission route supports it.
[Package fields and installer handling](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/upload-app-packages)

Complete availability, markets, pricing, category, privacy/data declarations,
and age-rating questions using accurate information. Provide a public privacy
policy when required and explain diagnostic exports and local recovery data.

For each listing language, add a description, supported features, release
notes, website/support links, MIT license terms, and
**Copyright (c) 2026 Hayk Jomardyan**. Capture actual **Windows** screenshots;
the Ubuntu images in the README do not represent the WPF application.
Use `windows-verification-<architecture>` CI artifacts as a source of demo
captures, and confirm image dimensions meet the current dashboard requirements.
Start with the [application artwork](../assets/icons/README.md) for Store logos.
[Store listing fields and images](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/add-and-edit-store-listing-info)

### Explain the administrator helper in certification notes

Give reviewers a reproducible demo path, supported test configuration, and
instructions for requested administrator operations. Suggested content to
adapt to the final submitted build:

> Uoo is a Windows boot-configuration utility. Start the installed application
> with --demo to inspect its UI without changing boot configuration. The desktop
> runs as the user; system operations start the fixed installed helper through
> UAC. Live changes require explicit user action and create recovery backups.
> Supported UEFI repair targets the running Windows installation on a verified
> local system ESP. Installation and removal do not modify BCD or EFI files.
> The app does not automatically reboot, disable Secure Boot, or suspend
> BitLocker. Use a disposable supported test machine for live changes.

Disclose that uninstall preserves recovery backups and previously applied boot
settings; link to [restoration and removal behavior](WINDOWS.md#install-and-start).
Ensure the submitted build uses supported Windows APIs and consent for system
changes. [Microsoft Store policies](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies)

Submit for certification after validation, address review findings, and confirm
the public product page and installation on both architectures after approval.
Do not announce Store availability before the listing is actually live.

## 7. Publish updates

Increase the app version, rebuild and sign, repeat installation/upgrade tests,
and host new immutable URLs. Update the existing product's package entries and
release notes in Partner Center, then submit the update.

For EXE/MSI listings, Microsoft states that the Store does **not** deliver
updates automatically or manually to existing installations. Uoo currently
has no Store-specific updater. Document manual signed-installer upgrades or
implement and validate an update mechanism before promising automatic updates.
[EXE/MSI update behavior](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/publish-update-to-your-app-on-store)

## MSIX is a separate packaging project

MSIX is supported by the Store, but Uoo has no MSIX project or package manifest.
It needs a package identity, package assets, lifecycle testing, and validation
of helper paths, IPC, recovery storage, and elevation under that deployment
model. Renaming an EXE or ZIP to `.msix` does not create such a package.

`runFullTrust` is not administrator elevation. If an MSIX design needs
`allowElevation`, Microsoft documents a separate restricted-capability approval
process with strict criteria. Follow the current guidance and obtain the
required approval before depending on it.
[Packaged app capabilities](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/app-capability-declarations)

## Submission checklist

- [ ] Publisher account verified and product name reserved.
- [ ] Final source version, manifests, installer, and release tag agree.
- [ ] All payload PE signatures and both installer signatures verified.
- [ ] Generated uninstallers signed; final checksums recorded.
- [ ] Offline silent install, upgrade, launch, and removal tested on x64/ARM64.
- [ ] Boot state preserved during install/uninstall; UAC and consent documented.
- [ ] Public immutable HTTPS installer URLs verified.
- [ ] Package fields, listing, Windows screenshots, and privacy details complete.
- [ ] Certification notes explain demo, helper, repair limits, and recovery data.
- [ ] Certification completed and public Store installation verified.
- [ ] Existing-user update behavior described accurately.

Adding this guide does not create an account, buy a certificate, sign binaries,
change the installer configuration, or publish an application.
