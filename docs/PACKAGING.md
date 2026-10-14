# Linux package builds

The **Build Linux packages** workflow in `.github/workflows/verify.yml` runs on
pushes, pull requests, and manual dispatches. It builds these outputs:

| Format | Architecture | Runtime |
| --- | --- | --- |
| `.deb` | `all` | Ubuntu 24.04+ system Python, GTK4, and libadwaita |
| `.rpm` | `noarch` | Fedora 44 system Python, GTK4, and libadwaita |
| AppImage | `x86_64` | Bundled Python, GTK4, and libadwaita; glibc 2.39+ |

The workflow installs the RPM and tests its desktop on Fedora. The GRUB modification
and bootloader repair backend currently requires Ubuntu commands and filesystem
layouts. The RPM does not add Fedora GRUB or repair support.

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

## Download a build

Open **Actions → Build Linux packages → a successful run → Artifacts** and
download `linux-packages`. It contains all three package formats and
`SHA256SUMS`. Individual formats are also available as `linux-deb`, `linux-rpm`,
and `linux-appimage`. Artifacts are retained for 30 days.

After extracting the artifact, verify it with:

```sh
sha256sum --check SHA256SUMS
```

## Release a version

Set the same three-part version in `uoo/__init__.py` and `packaging/control`,
commit the release sources, then push the matching tag:

```sh
git tag v2.0.0
git push origin v2.0.0
```

The workflow rejects a mismatched tag or metadata version. All tests and all
three package builds must pass before the workflow creates a **draft** GitHub
release and attaches the packages and checksums. Review and publish the draft
from GitHub Releases. Only the release job receives `contents: write`; the
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

The build scripts write to `build/` and `dist/` and do not install packages or
modify boot settings. To run an AppImage where FUSE is unavailable:

```sh
chmod +x dist/*.AppImage
APPIMAGE_EXTRACT_AND_RUN=1 ./dist/Uoo_Boot_Manager-2.0.0-x86_64.AppImage --demo
```
