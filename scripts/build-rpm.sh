#!/usr/bin/env bash
set -euo pipefail

project_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_version="$(python3 "$project_root/scripts/package-version.py")"
variant="fedora"
rpm_defines=()
release="1"
if [[ "${1:-}" == --opensuse ]]; then
    variant="opensuse"
    release="1.opensuse"
    rpm_defines=(--define 'uoo_opensuse 1')
    shift
fi
for tool in rpmbuild dpkg-deb; do
    command -v "$tool" >/dev/null || { printf 'Missing build tool: %s\n' "$tool" >&2; exit 1; }
done
if [[ $# -gt 1 ]]; then
    printf 'Usage: %s [--opensuse] [validated-debian-package]\n' "$0" >&2
    exit 1
fi
if [[ $# -eq 0 ]]; then
    bash "$project_root/scripts/build-deb.sh"
fi
deb="${1:-$project_root/dist/uoo-boot-manager_${package_version}_all.deb}"
python3 "$project_root/scripts/check-package.py" "$deb"

mkdir -p "$project_root/build" "$project_root/dist"
stage="$(mktemp -d "$project_root/build/rpm.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$stage"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS,payload}
dpkg-deb --extract "$deb" "$stage/payload"
tar -czf "$stage/SOURCES/payload.tar.gz" -C "$stage/payload" usr
rpmbuild -bb --define "_topdir $stage" --define "uoo_version $package_version" \
    "${rpm_defines[@]}" \
    "$project_root/packaging/uoo-boot-manager.spec"
install -m 0644 "$stage/RPMS/noarch/uoo-boot-manager-${package_version}-${release}.noarch.rpm" "$project_root/dist/"
python3 "$project_root/scripts/check-rpm.py" --variant "$variant" "$project_root/dist/uoo-boot-manager-${package_version}-${release}.noarch.rpm"
