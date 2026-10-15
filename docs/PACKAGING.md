# Linux package builds

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
flatpak install --user ./uoo-boot-manager-2.1.2-x86_64.flatpak
flatpak run org.uoo.BootManager
sudo snap install --dangerous --classic ./uoo-boot-manager_2.1.2_amd64.snap
uoo-boot-manager
```

The Snap reuses the already tested AppImage desktop, with Snapcraft producing
a classic package. It uses the native host helper for real changes. The
downloadable Snap is unsigned for local installation; publishing classic snaps
to the Snap Store requires a separate store review and is not performed by CI.
These builds do not publish to Flathub, the AUR, or any distro package repository.

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

Set the same three-part version in `uoo/__init__.py`, `packaging/control`,
and `windows/Directory.Build.props`,
commit the release sources, then push the matching tag:

```sh
git tag v2.1.2
git push origin v2.1.2
```

Alternatively, push a commit on `main` whose first line is `release: v2.1.2`
after synchronizing those version files. **Create requested release tag**
checks the requested version, creates the tag at that exact commit, and
dispatches both package workflows on it. It refuses to move an existing tag
to another commit. This uses the repository token without additional secrets.

The workflow rejects a mismatched tag or metadata version. All tests and all
package builds and architecture checks must pass before the workflow creates a **draft** GitHub
release and attaches the packages and checksums. Review and publish the draft
from GitHub Releases. For a combined release, wait for both the Linux and
Windows package workflows to succeed before publishing. Only tag-creation and
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
  APPIMAGETOOL_RUNTIME_FILE="$PWD/build/tools/runtime-x86_64" \
  bash scripts/build-appimage.sh
```

Build an Arch package as an ordinary user on Arch with `dpkg`, `base-devel`,
Python, and zstd installed:

```sh
bash scripts/build-arch.sh dist/uoo-boot-manager_2.1.2_all.deb
```

Flatpak builds require `flatpak-builder` plus the GNOME 50 platform and SDK in
the user's Flatpak installation. Snap builds require Snapcraft on Ubuntu 24.04
and a matching-architecture AppImage:

```sh
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50
bash scripts/build-flatpak.sh
bash scripts/build-snap.sh dist/Uoo_Boot_Manager-2.1.2-x86_64.AppImage
```

Snapcraft's destructive mode installs its build prerequisites on the build
machine; the CI uses disposable Ubuntu runners for it. Native package and
Flatpak/AppImage staging scripts write to `build/` and `dist/` and do not install packages or
modify boot settings. To run an AppImage where FUSE is unavailable:

```sh
chmod +x dist/*.AppImage
APPIMAGE_EXTRACT_AND_RUN=1 ./dist/Uoo_Boot_Manager-2.1.2-x86_64.AppImage --demo
```
