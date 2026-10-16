# Windows code signing

Current Windows installers and executables are **unsigned**, so Windows
SmartScreen warns about an unknown publisher. Signing is also required for the
[Microsoft Store EXE/MSI route](WINDOWS-STORE.md#2-close-the-current-packaging-gaps)
and may resolve the x64 install hang tracked in [WINGET.md](WINGET.md). This page
compares the options; it is a plan, not a record of a signed release. Prices
and eligibility were checked in October 2026 and can change.

## What must be signed

Sign the desktop and helper executables, then the Inno Setup installer and its
generated uninstaller, for both x64 and ARM64. Signing changes the file bytes,
so it requires a **new app version** and new WinGet manifests; never replace an
existing release's installers.

## Options

| Option | Cost | Who can use it | Notes |
| --- | --- | --- | --- |
| [SignPath Foundation](https://signpath.org/) | Free for qualifying open source | OSI-licensed, public repository, actively maintained, already released | The certificate names SignPath Foundation as publisher. Needs fully automated builds, origin verification, and a published code-signing policy on the project page. **Forbids commercial dual-licensing and proprietary components**, which would conflict with a proprietary paid tier. |
| [Azure Artifact Signing](https://azure.microsoft.com/products/artifact-signing) (formerly Trusted Signing) | About $9.99/month (Basic, 5,000 signatures) | Individuals in the USA and Canada; organizations in the USA, Canada, EU, and UK | Identity validation takes a few business days. SmartScreen reputation still builds over time. |
| Traditional OV/EV certificate | Paid yearly, varies by vendor | Anyone the vendor validates | Vendors increasingly require hardware-protected keys, which complicates CI. Not evaluated here. |

Sources: [SignPath for open source](https://about.signpath.io/product/open-source),
[Microsoft code-signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options).

## Suggested path

1. Decide the monetization model first. A proprietary Pro tier rules out
   SignPath Foundation; Azure Artifact Signing or a paid certificate then fit
   better.
2. Complete identity validation with the chosen provider.
3. Add a signing step to `windows/scripts/build.ps1` that signs payload files
   before Inno Setup runs and signs the installer and uninstaller afterwards.
4. Store provider credentials in the `release` environment (see
   [automated publishing](PUBLISH-AUTOMATION.md)); never in the repository.
5. Release a new version, verify with `Get-AuthenticodeSignature`, and test the
   WinGet and Store flows with the signed installers.
6. Publish a short code-signing policy on the website if the provider requires it.
