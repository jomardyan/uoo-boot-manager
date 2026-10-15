# Linux package builds

<img src="../assets/icons/png/uoo-64.png" width="64" height="64" alt="Uoo Boot Manager icon">

Every Linux format uses the `org.uoo.BootManager` icon family. Native packages
and Flatpak include the scalable, symbolic, and 16–512 px raster icons.
AppImage and Snap carry the same icon inside the desktop bundle; Snap also
uses the 512 px PNG for its package metadata. Sources and platform exports
are documented in [the icon guide](../assets/icons/README.md).

The **Build Linux packages** workflow in `.github/workflows/verify.yml` runs on
pushes, pull requests, and manual dispatches. It builds these outputs:

| Format | Architecture | Runtime |
| --- | --- | --- |
| `.deb` | `all` | Ubuntu 24.04+ system Python, GTK4, and libadwaita |
| `.rpm` | `noarch` | Fedora 44 system Python, GTK4, and libadwaita |
| openSUSE `.rpm` | `noarch` | Tumbleweed native Python/GI and typelib dependencies |
| `.pkg.tar.zst` | `any` | Arch Linux / compatible Manjaro Python, GTK4, and libadwaita |
| AppImage | `x86_64`, `aarch64` | Bundled Python, GTK4, and libadwaita; glibc 2.39+ |
| `.flatpak` | `x86_64`, `aarch64` | GNOME 50 runtime; matching native host companion for system mode |
| `.snap` | `amd64`, `arm64` | Classic confinement, core24, and the validated AppImage desktop payload |

The architecture-independent Debian package is installed and tested on both
x86-64 and ARM64 Ubuntu runners. AppImage, Flatpak, and Snap use native runners
for each architecture. The workflow installs RPMs on Fedora and openSUSE and
the Arch package on Arch Linux. Manjaro compatibility follows the Arch package
format and dependencies; a separate Manjaro runtime is not claimed.

The GRUB modification
and bootloader repair backend currently requires Ubuntu commands and filesystem
layouts. The RPM does not add Fedora GRUB or repair support.

Packaging availability does not establish bootloader support on another distro
or CPU. Ubuntu remains the verified boot-management backend, and repair is
limited to supported x86-64 plain-disk layouts. Other distro packages provide
the desktop, demo, inspection where available, and the installed helper; only
features whose live prerequisites are satisfied are enabled.

The AppImage runs the unprivileged desktop and demo. Real changes require the
matching system package to provide the fixed, root-owned helper and polkit
policy. The AppImage never elevates bundled code or installs its own helper.
Use the `.deb` for supported Ubuntu system management. The workflow includes an
AppImage startup test in an Ubuntu 24.04 container without GTK4 or PyGObject installed.

The AppImage uses the host graphics stack: OpenGL/EGL and the Wayland client,
cursor, and EGL libraries. PyInstaller deliberately excludes these libraries
to avoid conflicts with the target machine's graphics drivers. The clean test
container installs `libgl1`, `libegl1`, `libwayland-client0`,
`libwayland-cursor0`, and `libwayland-egl1`, while GTK4, libadwaita, and
PyGObject must come from the bundle. The build also requires `python3-gi-cairo`
for PyGObject's Cairo conversion binding and checks it before packaging.

## Flatpak and Snap

Flatpak grants `org.freedesktop.Flatpak` host-spawn access so that Uoo can run
two fixed installed host commands: the read-only `uoo-boot-inspect`, and
`pkexec /usr/bin/uoo-boot-helper --stdin`. This permission allows user-level
host execution; administrative changes still require polkit authentication
and the native helper's request validation. No writable host filesystem grant
or privileged helper is embedded in the Flatpak.

Install the matching native `.deb`, distro `.rpm`, or Arch package before
using Flatpak system mode. It supplies the read-only inspector and root-owned
helper. Explicit `--demo` works without it and never contacts the host helper.
For custom boot entries in Flatpak, enter filesystem-relative boot paths
manually; other file choices use the desktop portal.

```sh
flatpak install --user ./uoo-boot-manager-2.2.0-x86_64.flatpak
flatpak run org.uoo.BootManager
sudo snap install --dangerous --classic ./uoo-boot-manager_2.2.0_amd64.snap
uoo-boot-manager
```

The Snap reuses the already tested AppImage desktop, with Snapcraft producing
a classic package. It uses the native host helper for real changes. The
downloadable Snap is unsigned for local installation; publishing classic snaps
to the Snap Store requires a separate store review and is not performed by CI.
These builds do not publish to Flathub, the AUR, or any distro package repository.

## Ubuntu release compatibility

One architecture-independent DEB is used across compatible Ubuntu 24.04+
releases. The workflow installs and starts that same artifact in Ubuntu 24.04,
26.04, and 26.10 containers before collecting release assets. These checks
cover package installation and demo startup, not every firmware/storage layout.

For APT, each Ubuntu codename still needs its own published PPA entry. Use
`python3 scripts/build-ppa-source.py --list-series` to discover current targets
and `--all-supported` to prepare suite-specific uploads while reusing the same
upstream archive. See [multi-release PPA publishing](PUBLISHING.md#build-for-every-compatible-supported-ubuntu-release).

## Download a build

Open **Actions → Build Linux packages → a successful run → Artifacts** and
download `linux-packages`. It contains every built package and `SHA256SUMS`.
Individual artifacts use `linux-deb`, `linux-rpm`, `linux-opensuse`,
`linux-arch`, `linux-appimage-<arch>`, `linux-flatpak-<arch>`, and
`linux-snap-<arch>`. Artifacts are retained for 30 days.

After extracting the artifact, verify it with:

```sh
sha256sum --check SHA256SUMS
```

## Release a version

For APT repositories, Snap Store publication, and Ubuntu App Center listings,
follow the [Ubuntu publishing guide](PUBLISHING.md). The steps below create
GitHub release assets.

Set the same three-part version in `uoo/__init__.py`, `packaging/control`,
`windows/Directory.Build.props`, `windows/packaging/uoo.iss`, the
`debian/changelog` entry, and a new `<release>` entry in
`packaging/org.uoo.BootManager.metainfo.xml`. Use the four-part form (for
example `2.2.0.0`) in `windows/Uoo.Desktop/app.manifest` and
`windows/Uoo.Helper/app.manifest`. Also update the version shown by the
website (`website/index.html` and `website/app.js`) and the README badge.
`tests/test_packaging.py` checks that the Windows props, manifests, Inno Setup
script, metainfo release, and changelog match the package version. Commit the
release sources, then push the matching tag:

```sh
git tag v2.2.0
git push origin v2.2.0
```

Alternatively, push a commit on `main` whose first line is `release: v2.2.0`
after synchronizing those version files. **Create requested release tag**
checks the requested version, creates the tag at that exact commit, and
dispatches both package workflows on it. It refuses to move an existing tag
to another commit. This uses the repository token without additional secrets.

For release-request commits, the publishing job waits for successful Linux
and Windows builds at the same immutable tag and commit. It requires the
complete package set, downloads every asset, verifies both SHA256 manifests,
and then publishes the release with the checked-in versioned release notes.
Directly pushed tags still create drafts for manual publication.
To publish a release whose tag already exists, push a commit on `main` whose
first line is `publish: v2.2.0`, or run **Publish an existing verified
release** (`publish-release.yml`) manually with that version; it verifies the
packages from the existing tag and publishes them with its release notes.

The workflow rejects a mismatched tag or metadata version. All tests and all
package builds and architecture checks must pass before the workflow creates a **draft** GitHub
release and attaches the packages and checksums. For directly pushed tags,
review and publish that draft from GitHub Releases after both platform
workflows succeed. Release-request commits use the verified publishing job
described above. Only tag-creation, publishing, and
release jobs receive `contents: write`; the
normal build jobs use read access. No additional repository secrets are needed.
Rerunning a tag build can update its draft, but cannot overwrite a published
release's assets.

The unit tests, GTK interaction tests, and package archive validation run before
the native desktop smoke tests. Archive checks compare source contents and
verify root ownership, protected file permissions, and absence of package
installation/removal scripts. Builds use the commit timestamp as
`SOURCE_DATE_EPOCH`; downloaded AppImage tools and GitHub Actions are pinned.
This does not claim byte-identical builds across dependency updates.
The AppImage uses [PyInstaller's GI hooks](https://pyinstaller.org/en/stable/hooks-config.html#gi-hook-options)
with explicit GTK4 and libadwaita versions. Release creation uses the
[GitHub CLI](https://cli.github.com/manual/gh_release_create).

For the existing disposable boot integration matrix, choose **Run workflow**
and enable `run_vm`. It exercises UEFI, Secure Boot, and BIOS inside disposable
Ubuntu VMs. Ordinary CI uses demos and temporary fixtures. The VM checks are
optional and are not part of the automatic draft-release gate; hardware
validation requirements remain documented in `PRODUCTION.md`.

## Build locally on Ubuntu

The root `Makefile` wraps the existing scripts. Run targets from the repository
root, or use `make -C /path/to/uoo-boot-manager <target>`. Plain `make` shows
help. GNU Make and Bash are required; each format still needs its native tools
and runtime prerequisites described below.

Make prints cyan task headings, green `[OK]` results, and red `[FAIL]` results
with the failing command's exit status. Colors are enabled automatically in
terminals and omitted from redirected output. Use `make COLOR=always help`
to force colors, or `make COLOR=never check` to disable them. A nonempty
`NO_COLOR` environment variable also disables colors, including forced colors.

Install missing build dependencies with the matching endpoint:

```sh
make deps DEPS_ARGS=--dry-run         # Preview all host-supported dependencies
make deps                           # Install them (aliases: deps-all, install-deps)
make deps-windows                    # PowerShell 7 and .NET 10 SDK for ZIP builds
make deps-test-gtk                   # GTK4/libadwaita and headless display tools
make deps-appimage                   # Bundler venv and checksum-verified tools
make deps-flatpak                    # Flatpak tools and manifest-matched GNOME SDK
make deps-snap                       # AppImage prerequisites and classic Snapcraft
make deps-vm-test                    # Debian tools, QEMU, firmware, ISO and SSH tools
make deps-packages PACKAGE_FORMATS="deb rpm rpm-opensuse"
make deps DEPS_FOR="windows test-gtk" # Combine operations in one transaction
```

Every build/check operation has a `deps-<target>` endpoint, including
`deps-deb`, `deps-rpm`, `deps-rpm-opensuse`, `deps-arch`, `deps-check`,
`deps-smoke`, and `deps-windows-test`. `deps-all` means all host-supported
operations. The installer requires Python 3, Make, Bash, and sudo to start;
run it as your normal user. It supports apt on Ubuntu/Debian and pacman on
Arch/Manjaro, queries installed packages first, and installs only missing
requested packages. Package-manager dependencies may also be installed.
On Arch, keep the system and package databases up to date through the normal
full system upgrade procedure before installing dependencies.

`DEPS_ARGS=--dry-run` probes existing packages/tools and prints commands without
installing, downloading, or writing files. Real runs serialize dependency
installations with a user lock, use sudo for system packages and Snapcraft,
and keep other tooling under your account. Network access is needed for
missing packages, SDKs, and runtimes; Flatpak SDK downloads can be large.
Flatpak setup adds the Flathub user remote and installs the runtime and SDK
versions from the checked-in manifest. Snap setup installs classic Snapcraft
through an operational snapd service.

The combined endpoint skips native Arch packaging on apt hosts and Snapcraft
on Arch hosts. Explicit requests for those unsupported combinations fail
before installation. `deps-packages` follows `PACKAGE_FORMATS`, including its
platform restrictions. Other Linux distributions retain the manual build
instructions and CI environments described here. Dependency installation
prepares build tools; it does not install Uoo or run boot-management actions.

`deps-windows` uses [Microsoft's .NET installer](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-install-script)
for missing .NET 10 SDKs and a SHA256-verified official PowerShell archive when
PowerShell 7 is missing. Launchers are created in `~/.local/bin`; ensure that
directory is on your shell's PATH. Existing working installations are reused.
On Linux, `make windows` automatically builds the Windows ZIP and checksums;
`WINDOWS_BUILD_ARGS=-SkipInstaller` is also accepted. Windows installer creation
and native Windows tests require a Windows host. The dependency installer
does not provide Windows or Inno Setup on Linux.

```sh
make build                          # Debian package in dist/
make check                          # Python tests + Debian archive validation
make test-gtk                       # Native interaction tests under Xvfb
make smoke                          # Source demo startup under Xvfb
make -j2 packages PACKAGE_FORMATS="deb rpm rpm-opensuse"
make verify-checksums
```

`packages` builds the selected formats before writing `dist/SHA256SUMS`.
Its default selection includes every Linux format and requires all their
prerequisites; select formats for your host with `PACKAGE_FORMATS`. Debian
payload construction is shared between RPM and Arch targets, including with
parallel Make. `snap` first builds the matching native AppImage, then runs
Snapcraft in destructive mode as described below. `checksums` generates a
manifest from existing Linux packages without rebuilding them.

After `make deps-appimage`, `make appimage` and `make snap` use the managed
`build/appimage-venv` and `build/tools` paths automatically. The downloaded
AppImage tool/runtime versions and SHA256 checksums match CI. Explicit
`BUILD_PYTHON`, `APPIMAGETOOL`, and `APPIMAGETOOL_RUNTIME_FILE` overrides still
take precedence. The manual environment setup below is also supported.
`PYTHON=/path/to/python3` selects the interpreter
for Make's checks; packaging scripts still use their existing `python3` and
`BUILD_PYTHON` conventions. `make test` permits GTK tests to skip when a display
or dependencies are absent; `make test-gtk` requires PyGObject, GTK4,
libadwaita 1.5+, `dbus-run-session`, and `xvfb-run`.

`make vm-test` builds and validates Debian, then runs the disposable Ubuntu
guest exercise, including its bootloader operations and reboot. It downloads
the guest image and requires the QEMU/firmware tools documented in
[VALIDATION.md](VALIDATION.md#reproduce-on-current-sources). Pass options with `VM_ARGS`, for example
`make vm-test VM_ARGS="--boot-mode bios"` or
`make vm-test VM_ARGS="--secure-boot"`.

`make clean` removes `build/`, including any bundler virtual environment and
downloaded tools stored there. `make distclean` also removes `dist/` packages.

```sh
sudo apt install dpkg-dev rpm python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
  binutils patchelf squashfs-tools desktop-file-utils
bash scripts/build-deb.sh
python3 scripts/check-package.py dist/uoo-boot-manager_*_all.deb
bash scripts/build-rpm.sh dist/uoo-boot-manager_*_all.deb
bash scripts/build-rpm.sh --opensuse dist/uoo-boot-manager_*_all.deb
```

For the AppImage, use Ubuntu 24.04 to retain the glibc 2.39 baseline. Create a
venv with access to system PyGObject, install the pinned bundler requirements,
and download and verify `appimagetool` and the runtime using the exact commands
in the workflow. Then run:

```sh
/usr/bin/python3 -m venv --system-site-packages build/appimage-venv
build/appimage-venv/bin/python -m pip install -r packaging/appimage-requirements.txt
BUILD_PYTHON="$PWD/build/appimage-venv/bin/python" \
  APPIMAGETOOL="$PWD/build/tools/appimagetool.AppImage" \
  APPIMAGETOOL_RUNTIME_FILE="$PWD/build/tools/runtime" \
  bash scripts/build-appimage.sh
```

Build an Arch package as an ordinary user on Arch with `dpkg`, `base-devel`,
Python, and zstd installed:

```sh
bash scripts/build-arch.sh dist/uoo-boot-manager_2.2.0_all.deb
```

Flatpak builds require `flatpak-builder` plus the GNOME 50 platform and SDK in
the user's Flatpak installation. Snap builds require Snapcraft on Ubuntu 24.04
and a matching-architecture AppImage:

```sh
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50
bash scripts/build-flatpak.sh
bash scripts/build-snap.sh dist/Uoo_Boot_Manager-2.2.0-x86_64.AppImage
```

Snapcraft's destructive mode installs its build prerequisites on the build
machine; the CI uses disposable Ubuntu runners for it. Native package and
Flatpak/AppImage staging scripts write to `build/` and `dist/` and do not install packages or
modify boot settings. To run an AppImage where FUSE is unavailable:

```sh
chmod +x dist/*.AppImage
APPIMAGE_EXTRACT_AND_RUN=1 ./dist/Uoo_Boot_Manager-2.2.0-x86_64.AppImage --demo
```
