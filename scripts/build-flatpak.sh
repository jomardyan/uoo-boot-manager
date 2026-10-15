#!/usr/bin/env bash
set -euo pipefail
project_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_version="$(python3 "$project_root/scripts/package-version.py")"
architecture="$(flatpak --default-arch)"
mkdir -p "$project_root/build" "$project_root/dist"
stage="$(mktemp -d "$project_root/build/flatpak.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
flatpak-builder --user --force-clean --disable-rofiles-fuse --repo="$stage/repo" \
    "$stage/app" "$project_root/packaging/org.uoo.BootManager.json"
flatpak build-bundle --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo \
    "$stage/repo" "$project_root/dist/uoo-boot-manager-${package_version}-${architecture}.flatpak" org.uoo.BootManager
