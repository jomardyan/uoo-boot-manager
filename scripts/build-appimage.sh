#!/usr/bin/env bash
set -euo pipefail

project_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_version="$(python3 "$project_root/scripts/package-version.py")"
build_python="${BUILD_PYTHON:-python3}"
appimagetool="${APPIMAGETOOL:?Set APPIMAGETOOL to the downloaded appimagetool executable}"
runtime_file="${APPIMAGETOOL_RUNTIME_FILE:?Set APPIMAGETOOL_RUNTIME_FILE to the pinned AppImage runtime}"
architecture="$(uname -m)"
case "$architecture" in x86_64|aarch64) ;; *) printf 'Unsupported AppImage architecture: %s\n' "$architecture" >&2; exit 1 ;; esac
"$build_python" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_foreign("cairo"); from gi.repository import Gtk, Adw; assert Adw.get_major_version() > 1 or Adw.get_minor_version() >= 5'

mkdir -p "$project_root/build" "$project_root/dist"
stage="$(mktemp -d "$project_root/build/appimage.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
"$build_python" -m PyInstaller --noconfirm --clean \
    --distpath "$stage/frozen" --workpath "$stage/work" "$project_root/packaging/appimage.spec"
app_dir="$stage/Uoo.AppDir"
install -d "$app_dir/usr/lib" "$app_dir/usr/share/doc/uoo-boot-manager"
cp -a "$stage/frozen/uoo-boot-manager" "$app_dir/usr/lib/"
install -m 0755 "$project_root/packaging/AppRun" "$app_dir/AppRun"
install -m 0644 "$project_root/packaging/uoo-boot-manager.desktop" "$app_dir/uoo-boot-manager.desktop"
install -m 0644 "$project_root/packaging/uoo-boot-manager.svg" "$app_dir/uoo-boot-manager.svg"
install -m 0644 "$project_root/README.md" "$app_dir/usr/share/doc/uoo-boot-manager/README.md"
ARCH="$architecture" VERSION="$package_version" APPIMAGE_EXTRACT_AND_RUN=1 \
    "$appimagetool" --runtime-file "$runtime_file" "$app_dir" \
    "$project_root/dist/Uoo_Boot_Manager-${package_version}-${architecture}.AppImage"
