# Publish Uoo Boot Manager with WinGet

Prepared package ID: **HaykJomardyan.UooBootManager**. The checked-in manifests
target **2.2.0**, Windows 11 build 22000+, and native **x64 / ARM64** installers.
The package is **not yet published in the WinGet community catalog**. Preparing
these files or publishing a GitHub release does not submit it to Microsoft.

## Prepare a release

The installers use Inno Setup, require machine-wide administrator installation,
and include the .NET runtime. Their uninstall registration is
`{1179B417-6013-4CFA-92E3-41C9F5FC07F5}_is1`, matching
[`uoo.iss`](../windows/packaging/uoo.iss). WinGet supplies the standard Inno
silent switches. Installation and uninstallation do not modify boot settings.
The current 2.2.0 binaries are unsigned; Microsoft performs its own validation
and decides whether to accept the package. Do not bypass hash or malware checks.

From the repository root on Linux or Windows with Python 3.10+:

```sh
python3 scripts/build-winget.py --version 2.2.0
```

On a Make/Bash host, `make winget` uses the current app version;
`make winget WINGET_VERSION=2.2.0` chooses a published version explicitly.
No third-party Python packages or signing credentials are needed.

The generator downloads both published installer EXEs, computes their SHA256,
compares them with `SHA256SUMS-Windows` and the GitHub asset digest, and then
writes three YAML files into:

```text
packaging/winget/manifests/h/HaykJomardyan/UooBootManager/2.2.0/
```

The version, default-locale, and installer manifests use Microsoft's 1.6.0
schemas for compatibility with existing WinGet clients. The generator rejects drafts, prereleases, missing/duplicate assets,
unexpected download URLs, and hash mismatches. Re-running the same release is
safe; it refuses to replace different manifests for an existing version.
Review all three files before submission. Never replace published installer
bytes at an existing versioned URL. For a new app release, generate a new
version directory and retain previously accepted versions.
[Microsoft manifest documentation](https://learn.microsoft.com/en-us/windows/package-manager/package/manifest)

## Validate and test on Windows

Use disposable Windows 11 systems for **both x64 and ARM64** with App Installer /
WinGet installed. Start PowerShell as administrator, enter this repository, and
run:

```powershell
$manifest = '.\packaging\winget\manifests\h\HaykJomardyan\UooBootManager\2.2.0'
winget validate --manifest $manifest
winget settings --enable LocalManifestFiles
winget install --manifest $manifest --silent --accept-package-agreements --accept-source-agreements
```

All commands must succeed. Confirm WinGet selected the matching native
architecture, the version is 2.2.0, and the app opens from Start. Exercise the
demo without applying boot changes:

```powershell
& 'C:\Program Files\Uoo Boot Manager\Uoo.BootManager.exe' --demo
winget list --name 'Uoo Boot Manager' --exact
winget uninstall --name 'Uoo Boot Manager' --exact --silent
winget settings --disable LocalManifestFiles
```

Verify the app and helper have been removed. Local-manifest support is an
administrator setting; disabling it afterward is optional if you already use
it for other packages. The repository's **Validate WinGet package** workflow
performs schema/install/uninstall checks on disposable native x64 and ARM64
runners, without submitting a community PR or changing boot configuration.
[Microsoft local-manifest instructions](https://learn.microsoft.com/en-us/windows/package-manager/winget/install#local-install)

## Submit to Microsoft's community repository

After both native test jobs pass, install Microsoft's manifest submission tool:

```powershell
winget install --id Microsoft.WingetCreate --exact --source winget
wingetcreate token -s
wingetcreate submit --prtitle 'New package: HaykJomardyan.UooBootManager version 2.2.0' .\packaging\winget\manifests\h\HaykJomardyan\UooBootManager\2.2.0
```

Complete the interactive GitHub authentication flow. Do not paste access tokens
into shell arguments, source files, or issue comments. The submit command opens
a pull request against `microsoft/winget-pkgs`; review it in your browser.
Only the three YAML files belong in
`manifests/h/HaykJomardyan/UooBootManager/2.2.0/` in that repository. Installer
binaries remain hosted in the public Uoo GitHub release. If a reviewer requires
a package-ID change, update all three manifests and the public instructions
before retrying.
[WinGetCreate submission and authentication](https://github.com/microsoft/winget-create/blob/main/doc/submit.md)
· [WinGetCreate token management](https://github.com/microsoft/winget-create#github-personal-access-token-classic-permissions)

Monitor the PR's schema, installer, security, and review checks. Address feedback
on the same PR. Acceptance and catalog propagation are controlled by Microsoft;
do not announce catalog availability until the following command finds Uoo:

```powershell
winget source update --name winget
winget show --id HaykJomardyan.UooBootManager --exact --source winget
```

Then remove the pending-publication notice from the README and landing page.
Users can install or update through the catalog:

```powershell
winget install --id HaykJomardyan.UooBootManager --exact --source winget
winget upgrade --id HaykJomardyan.UooBootManager --exact --source winget
```

For later releases, repeat generation and native tests, and submit a PR for the
new version. GitHub release publication does not automatically update WinGet.
[Microsoft submission and review process](https://learn.microsoft.com/en-us/windows/package-manager/package/repository)
