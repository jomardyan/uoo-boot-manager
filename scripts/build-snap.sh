#!/usr/bin/env bash
set -euo pipefail
project_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_version="$(python3 "$project_root/scripts/package-version.py")"
architecture="$(uname -m)"
case "$architecture" in
    x86_64) snap_arch=amd64 ;;
    aarch64) snap_arch=arm64 ;;
    *) printf 'Unsupported Snap architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
appimage="${1:-$project_root/dist/Uoo_Boot_Manager-${package_version}-${architecture}.AppImage}"
appimage="$(realpath "$appimage")"
mkdir -p "$project_root/build" "$project_root/dist"
stage="$(mktemp -d "$project_root/build/snap.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
install -m 0755 "$appimage" "$stage/desktop.AppImage"
(cd "$stage" && ./desktop.AppImage --appimage-extract >/dev/null)
mv "$stage/squashfs-root" "$stage/desktop"
mkdir -p "$stage/snap/gui"
sed -e "s/@VERSION@/$package_version/g" -e "s/@SNAP_ARCH@/$snap_arch/g" \
    "$project_root/packaging/snapcraft.yaml.in" > "$stage/snap/snapcraft.yaml"
install -m 0644 "$project_root/assets/icons/png/uoo-512.png" "$stage/snap/gui/uoo-boot-manager.png"
sed 's|^Icon=.*|Icon=${SNAP}/meta/gui/uoo-boot-manager.png|' \
    "$project_root/packaging/uoo-boot-manager.desktop" > "$stage/snap/gui/uoo-boot-manager.desktop"
(cd "$stage" && snapcraft --destructive-mode)
install -m 0644 "$stage/uoo-boot-manager_${package_version}_${snap_arch}.snap" "$project_root/dist/"
