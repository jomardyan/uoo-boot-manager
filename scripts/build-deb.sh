#!/usr/bin/env bash
set -euo pipefail

project_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_name="uoo-boot-manager"
package_version="1.0.0"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    printf 'dpkg-deb is required. Install the dpkg package on Ubuntu.\n' >&2
    exit 1
fi

for source_file in uoo/__init__.py uoo/__main__.py uoo/app.py uoo/core.py uoo/system.py helper.py; do
    if [[ ! -f "$project_root/$source_file" ]]; then
        printf 'Missing source file: %s\n' "$source_file" >&2
        exit 1
    fi
done

mkdir -p -- "$project_root/build" "$project_root/dist"
stage="$(mktemp -d "$project_root/build/package.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
chmod 0755 "$stage"

install -d -m 0755 \
    "$stage/DEBIAN" \
    "$stage/usr" \
    "$stage/usr/bin" \
    "$stage/usr/lib" \
    "$stage/usr/lib/$package_name" \
    "$stage/usr/lib/$package_name/uoo" \
    "$stage/usr/share" \
    "$stage/usr/share/applications" \
    "$stage/usr/share/icons" \
    "$stage/usr/share/icons/hicolor" \
    "$stage/usr/share/icons/hicolor/scalable" \
    "$stage/usr/share/icons/hicolor/scalable/apps" \
    "$stage/usr/share/doc" \
    "$stage/usr/share/doc/$package_name"

install -m 0644 "$project_root/packaging/control" "$stage/DEBIAN/control"
install -m 0755 "$project_root/packaging/uoo-boot-manager" "$stage/usr/bin/$package_name"
install -m 0644 "$project_root/packaging/launch.py" "$stage/usr/lib/$package_name/launch.py"
install -m 0755 "$project_root/helper.py" "$stage/usr/lib/$package_name/helper.py"
for module in "$project_root"/uoo/*.py; do
    install -m 0644 "$module" "$stage/usr/lib/$package_name/uoo/"
done
install -m 0644 "$project_root/packaging/uoo-boot-manager.desktop" "$stage/usr/share/applications/"
install -m 0644 "$project_root/packaging/uoo-boot-manager.svg" "$stage/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$project_root/README.md" "$stage/usr/share/doc/$package_name/README.md"
if [[ -f "$project_root/docs/images/grub.png" ]]; then
    install -d -m 0755 "$stage/usr/share/doc/$package_name/docs" "$stage/usr/share/doc/$package_name/docs/images"
    install -m 0644 "$project_root/docs/images/grub.png" "$stage/usr/share/doc/$package_name/docs/images/"
fi

output="$project_root/dist/${package_name}_${package_version}_all.deb"
dpkg-deb --root-owner-group --build "$stage" "$output"
printf 'Built %s\n' "$output"
