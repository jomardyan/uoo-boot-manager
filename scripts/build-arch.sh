#!/usr/bin/env bash
set -euo pipefail
project_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_version="$(python3 "$project_root/scripts/package-version.py")"
if [[ "$EUID" == 0 ]]; then
    printf 'Build Arch packages as an ordinary user (makepkg uses fakeroot).\n' >&2
    exit 1
fi
deb="${1:-$project_root/dist/uoo-boot-manager_${package_version}_all.deb}"
python3 "$project_root/scripts/check-package.py" "$deb"
mkdir -p "$project_root/build" "$project_root/dist"
stage="$(mktemp -d "$project_root/build/arch.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
mkdir "$stage/payload"
dpkg-deb --extract "$deb" "$stage/payload"
tar -czf "$stage/payload.tar.gz" -C "$stage/payload" usr
payload_sha="$(sha256sum "$stage/payload.tar.gz")"
payload_sha="${payload_sha%% *}"
sed -e "s/@VERSION@/$package_version/g" -e "s/@SHA256@/$payload_sha/g" \
    "$project_root/packaging/PKGBUILD.in" > "$stage/PKGBUILD"
(cd "$stage" && PKGEXT='.pkg.tar.zst' makepkg --nodeps --nosign --noconfirm --force)
package="$project_root/dist/uoo-boot-manager-${package_version}-1-any.pkg.tar.zst"
install -m 0644 "$stage/uoo-boot-manager-${package_version}-1-any.pkg.tar.zst" "$package"
python3 "$project_root/scripts/check-arch.py" "$package"
