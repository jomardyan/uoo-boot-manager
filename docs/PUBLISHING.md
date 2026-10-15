# Publish Uoo on Ubuntu: APT, Snap, and App Center

Maintainer instructions for distributing Uoo Boot Manager. Examples use app
version **2.2.0** and Ubuntu **24.04 LTS (`noble`)**. Replace versions, account
names, signing-key fingerprints, and revision numbers with your own values.

This guide documents publication; the repository's build workflows do not
publish to Launchpad or the Snap Store automatically. A GitHub release does
not create an APT repository or an App Center listing.

For the Windows edition, see [Microsoft Store publishing](WINDOWS-STORE.md).

## Choose a distribution route

| Goal | Route | What users need |
| --- | --- | --- |
| Install and update with APT | Launchpad Personal Package Archive (PPA) | Add your PPA, then install `uoo-boot-manager` |
| Install and update with Snap | Public Snap Store listing | Install the approved Snap and matching native helper package |
| Find Uoo in Ubuntu App Center | Publish the public Snap Store listing | Find the app in App Center; follow the native-helper setup instructions |
| Include Uoo in Ubuntu's default repositories | Ubuntu archive contribution and review | Separate acceptance process; a PPA does not provide this |

**Current repository status:** CI produces a binary `.deb` and classic `.snap`
packages for `amd64` and `arm64`. Debian source packaging (`debian/`) still needs
to be added before a PPA upload. The Snap supplies the desktop; authenticated
boot changes require the matching native host package and its polkit helper.

See [Linux packaging](PACKAGING.md) for build prerequisites, architecture
coverage, and supported boot-management operations.

## 1. Publish an APT package through Launchpad

### Create your account, signing key, and PPA

1. Create a [Launchpad account](https://launchpad.net/).
2. Create or choose an OpenPGP signing key. Register its public key in
   [Launchpad's OpenPGP settings](https://launchpad.net/~/+editpgpkeys).
3. Create a PPA named `uoo` from your Launchpad profile. Use your actual
   Launchpad ID wherever `YOUR_LAUNCHPAD_ID` appears below.
4. Install the packaging tools on Ubuntu:

   ```sh
   sudo apt update
   sudo apt install devscripts dpkg-dev debhelper dh-python dput gnupg lintian
   ```

Set the maintainer identity to match your signing key:

```sh
export DEBFULLNAME="Hayk Jomardyan"
export DEBEMAIL="YOUR_EMAIL_ADDRESS"
gpg --list-secret-keys --keyid-format long
```

Keep the private signing key private. Launchpad needs the public key and
signed uploads. [Launchpad account and build prerequisites](https://ubuntu.com/docs/launchpad/user/how-to/packaging/build-deb-packages-in-launchpad/)

### If `gpg --fingerprint` prints nothing

This command lists keys; it does not create one. Empty output usually means
there are no public keys in the current user's keyring. Use your normal user
account for every command below, without `sudo`. If your key is stored under
another account or GnuPG home, use that existing keyring instead of creating a
duplicate.

Create a key interactively if you do not already have one:

```sh
gpg --full-generate-key
```

Select **RSA and RSA**, **4096** bits, and an expiration such as **1y**. Enter
**Hayk Jomardyan** and an email address registered and verified on your
Launchpad account. Set the passphrase in GnuPG's prompt; keep it private.
The encryption subkey is needed to read Launchpad's confirmation message.

Now display the new key:

```sh
gpg --list-secret-keys --keyid-format long --with-fingerprint
gpg --fingerprint "YOUR_LAUNCHPAD_EMAIL"
```

Copy the **full primary-key fingerprint**, the long hexadecimal line associated
with `pub`/`sec`, not the short key ID after the slash or a subkey fingerprint.
Replace the placeholder below with that value:

```sh
gpg --keyserver hkps://keyserver.ubuntu.com --send-keys YOUR_FULL_PRIMARY_FINGERPRINT
```

This publishes the public key, including its name/email identity, to Ubuntu's
keyserver. Keep the private key and its recovery material backed up securely.
Do not publish a private-key export or the `.gnupg` directory.

Return to [Launchpad's OpenPGP settings](https://launchpad.net/~/+editpgpkeys).
Close the fingerprint help popup, paste the full fingerprint into the actual
**Fingerprint** field, and choose **Import Key**. If Launchpad cannot find it,
allow time for the public-key upload to become available and try again.

Launchpad sends an encrypted confirmation email. Save the encrypted message
part as `launchpad-confirmation.asc` on the same computer and decrypt it with:

```sh
gpg --decrypt launchpad-confirmation.asc
```

Open the confirmation link from the decrypted message while signed in to
Launchpad. The key is registered only after you confirm it. If GnuPG reports
`No secret key`, decrypt using the account/computer where you generated the
key, or securely restore your own private-key backup. Downloading the public
key again cannot decrypt that email.

See [Launchpad's OpenPGP import guide](https://ubuntu.com/docs/launchpad/user/how-to/import-openpgp-key/)
and [GnuPG key-generation commands](https://www.gnupg.org/documentation/manuals/gnupg/OpenPGP-Key-Management.html).

### Add Debian source packaging first

Launchpad builds binaries from source; it does not accept the `.deb` produced
by `scripts/build-deb.sh` as an upload. Prepare a source package with at least:

| File | Purpose for Uoo |
| --- | --- |
| `debian/control` | Source/build dependencies and binary runtime dependencies; replace the placeholder maintainer email |
| `debian/changelog` | Package version, target suite (`noble`), changes, and maintainer identity |
| `debian/rules` | Executable debhelper rules that stage Uoo's application and assets |
| `debian/source/format` | Source format, normally `3.0 (quilt)` for a separate upstream release |
| `debian/copyright` | Copyright (c) 2026 Hayk Jomardyan, MIT terms, and applicable third-party notices |
| `debian/install` or equivalent rules | Application modules, launchers, icons, desktop entry, documentation, and fixed polkit/helper paths |

Use [the current binary staging script](../scripts/build-deb.sh) and
[package control metadata](../packaging/control) as the payload reference.
Preserve the root-owned helper and its protected permissions. A debhelper
build must stage files into its package directory; calling the existing binary
builder alone does not implement Debian source packaging.

For `3.0 (quilt)`, prepare an upstream source archive named
`uoo-boot-manager_2.2.0.orig.tar.gz` beside the source directory. It must match
the upstream version and include the release sources, license, and required
assets, excluding local build outputs. Keep Debian packaging changes under
`debian/`, and record upstream modifications as appropriate for that format.

Set the example package version to `2.2.0-0ubuntu1~noble1` and the changelog
suite to `noble`. The suite selects the Ubuntu release; a version suffix alone
does not. This preparation is required before the following commands work.
[Ubuntu's new-package guide](https://ubuntu.com/project/docs/contributors/new-package/create-a-new-package/)

### Build, sign, upload, and check

Run from the prepared source-package directory:

```sh
debuild -S -sa -kYOUR_GPG_KEY_FINGERPRINT

lintian ../uoo-boot-manager_2.2.0-0ubuntu1~noble1_source.changes

dput ppa:YOUR_LAUNCHPAD_ID/uoo \
  ../uoo-boot-manager_2.2.0-0ubuntu1~noble1_source.changes
```

`-S` builds source only; `-sa` includes the upstream source archive. Inspect the
source package before uploading, resolve packaging errors, then monitor the
PPA's build logs. Confirm the published package installs and runs on a clean
Ubuntu 24.04 system. Increment the Debian revision for a corrected upload;
Launchpad will not replace an accepted version. Test each additional Ubuntu
series and use its actual changelog suite and a distinct version.
[Launchpad source upload instructions](https://ubuntu.com/docs/launchpad/user/how-to/packaging/ppa-package-upload/)

### Give users these installation commands

After the PPA package has built and published:

```sh
sudo apt install software-properties-common
sudo add-apt-repository ppa:YOUR_LAUNCHPAD_ID/uoo
sudo apt update
sudo apt install uoo-boot-manager
```

Subsequent PPA versions are delivered through normal APT updates. To seek
inclusion in Ubuntu's default archive, follow the
[Ubuntu package contribution guide](https://ubuntu.com/project/docs/contributors/new-package/create-a-new-package/)
and [sponsorship process](https://wiki.ubuntu.com/Sponsorship_process).

## 2. Publish the Snap

### Register a public package name

Create an Ubuntu One account and sign in to the
[Snap Store publisher dashboard](https://dashboard.snapcraft.io/). Then run:

```sh
sudo snap install snapcraft --classic
snapcraft login
snapcraft register uoo-boot-manager
```

Name availability is not guaranteed. If you register a different name, update
`name` in [the Snap template](../packaging/snapcraft.yaml.in), adjust the
instructions, and rebuild. A newly registered snap is public by default.
[Snap registration instructions](https://ubuntu.com/docs/snapcraft/latest/how-to/publishing/register-a-snap/)

### Request classic-confinement approval

Uoo currently declares `confinement: classic`. Request review in the Snapcraft
forum's [store-requests category](https://forum.snapcraft.io/c/store-requests),
following the [classic-confinement review process](https://snapcraft.io/docs/reference/administration/reviewing-classic-confinement-snaps/).

Include the registered name, publisher identity, source URL, and technical
reasons for the requested access. Explain that:

- The Snap bundles the GTK desktop and uses the host graphics stack.
- Boot inspection and writes use fixed commands from the separately installed
  native package; writes authenticate through polkit.
- The native helper validates requests and maintains protected recovery data.
- The current design needs the matching host package for real changes; demo
  mode works independently.

Describe why strict confinement and available interfaces do not meet the
actual design requirements. Reviewers may request changes; approval is not
automatic. Do not present boot-management privileges alone as proof of
eligibility.

### Obtain and upload both architecture builds

Download `linux-packages` from a successful Linux workflow run at the intended
release commit, or use the corresponding published GitHub release assets.
After extracting the complete Linux artifact, check its manifest:

```sh
sha256sum --check SHA256SUMS
```

The manifest expects all listed Linux assets in the current directory. Confirm
the app version and commit, then upload the two Snap files:

```sh
snapcraft upload --release=edge uoo-boot-manager_2.2.0_amd64.snap
snapcraft upload --release=edge uoo-boot-manager_2.2.0_arm64.snap
snapcraft revisions uoo-boot-manager
```

An upload can remain held for review until classic confinement is approved.
Record the separate numeric revision for each architecture. The app version
`2.2.0` is not a Snap revision number.

If building locally, use the [packaging guide](PACKAGING.md). The current
Snap builder uses `snapcraft --destructive-mode`; use a disposable Ubuntu
build environment. CI already builds each architecture on native runners.

### Test, then promote to stable

Once the reviewed edge revisions are available, install the matching native
DEB/PPA package on test systems first. The native desktop and Snap share a
command name, so use the explicit Snap launcher:

```sh
sudo apt install uoo-boot-manager
sudo snap install uoo-boot-manager --channel=edge --classic
snap run uoo-boot-manager --demo
snap run uoo-boot-manager
```

If the Snap is already installed, switch it with
`sudo snap refresh uoo-boot-manager --channel=edge`. Test startup, desktop
integration, native-helper detection, and authentication on both architectures.
Validate actual boot changes and recovery only in suitable disposable test
systems, following [production requirements](PRODUCTION.md).

After approval and testing, replace the placeholders with the revision numbers:

```sh
snapcraft release uoo-boot-manager AMD64_REVISION stable
snapcraft release uoo-boot-manager ARM64_REVISION stable
snapcraft status uoo-boot-manager
```

Check that `latest/stable` has the intended revision for each architecture.
Future updates follow the same edge-test-promote process.
[Snap revisions and channel management](https://ubuntu.com/docs/snapcraft/9/how-to/publishing/manage-revisions-and-releases/)

## 3. Make Uoo available in Ubuntu App Center

The Snap Store supplies the Snap listings shown in Ubuntu App Center. Complete
the public Snap publication above; there is no separate App Center binary
upload. A PPA does not automatically become a public Snap listing.
[App Center](https://snapcraft.io/snap-store) ·
[Snap naming and graphical frontends](https://ubuntu.com/docs/snapcraft/latest/how-to/publishing/register-a-snap/)

In the publisher dashboard, complete the store listing:

- Title: **Uoo Boot Manager**; use a matching `title` in the Snap template when
  rebuilding if needed.
- Icon: [512 px application icon](../assets/icons/png/uoo-512.png).
- Screenshots: [boot menu](images/grub.png), [firmware](images/firmware.png), and
  [backups](images/backups.png); identify demo screenshots accurately.
- Summary and description: explain supported platforms, boot operations, and
  the native-helper installation requirement.
- License: **MIT**; retain **Copyright (c) 2026 Hayk Jomardyan** and license
  notices in distributed packages.
- Suitable category, project website, source repository, support/issue links,
  and an accurate privacy statement.

Confirm the listing is public, reviews are resolved, and both architecture
revisions are available in stable. Check discovery and installation through
App Center on a clean supported Ubuntu desktop. Search indexing and featured
placement are separate from publishing; do not promise immediate placement.

The current Snap does not install the native helper itself. Provide the PPA
setup steps prominently in the listing; installing only the Snap leaves
system changes unavailable. For a complete App Center installation experience,
resolve that dependency through a reviewed packaging design before describing
the app as self-contained.

Once available, the command-line equivalent is:

```sh
sudo snap install uoo-boot-manager --classic
```

Users still need the matching native host package for authenticated changes.

## Release checklist

- [ ] Commit the intended sources, license, icons, and packaging metadata.
- [ ] Confirm Linux/Windows version metadata and release tag agree.
- [ ] Run applicable build, package, and platform checks.
- [ ] Build and test Debian source packaging before uploading to the PPA.
- [ ] Confirm the PPA publishes the intended version for each supported suite.
- [ ] Complete classic-confinement review and store metadata.
- [ ] Test matching native helper + Snap on amd64 and arm64.
- [ ] Promote the correct Snap revision for each architecture to stable.
- [ ] Verify App Center listing, installation, and helper setup instructions.
- [ ] Publish the GitHub draft only after the intended package workflows pass.
- [ ] Update download links and user instructions with the real PPA/store URLs.

Publication requires your Launchpad/Snap Store accounts and signing setup.
Adding this guide does not register names, upload packages, or publish a release.
