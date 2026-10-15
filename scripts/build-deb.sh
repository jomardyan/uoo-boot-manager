#!/usr/bin/env bash
set -euo pipefail

project_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_name="uoo-boot-manager"
package_version="$(python3 "$project_root/scripts/package-version.py")"

if ! command -v dpkg-deb >/dev/null 2>&1; then
    printf 'dpkg-deb is required. Install the dpkg package on Ubuntu.\n' >&2
    exit 1
fi

for source_file in LICENSE uoo/__init__.py uoo/__main__.py uoo/app.py uoo/core.py uoo/system.py helper.py docs/PRODUCTION.md docs/VALIDATION.md docs/PACKAGING.md; do
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
    "$stage/usr/share/polkit-1" \
    "$stage/usr/share/polkit-1/actions" \
    "$stage/usr/share/doc/$package_name"

install -m 0644 "$project_root/packaging/control" "$stage/DEBIAN/control"
install -m 0755 "$project_root/packaging/uoo-boot-manager" "$stage/usr/bin/$package_name"
install -m 0755 "$project_root/packaging/uoo-boot-helper" "$stage/usr/bin/uoo-boot-helper"
install -m 0755 "$project_root/packaging/uoo-boot-inspect" "$stage/usr/bin/uoo-boot-inspect"
install -m 0644 "$project_root/packaging/org.uoo.boot-manager.policy" "$stage/usr/share/polkit-1/actions/"
install -m 0644 "$project_root/packaging/launch.py" "$stage/usr/lib/$package_name/launch.py"
install -m 0755 "$project_root/helper.py" "$stage/usr/lib/$package_name/helper.py"
install -m 0644 "$project_root/inspector.py" "$stage/usr/lib/$package_name/inspector.py"
for module in "$project_root"/uoo/*.py; do
    install -m 0644 "$module" "$stage/usr/lib/$package_name/uoo/"
done
install -m 0644 "$project_root/packaging/uoo-boot-manager.desktop" "$stage/usr/share/applications/"
install -m 0644 "$project_root/packaging/uoo-boot-manager.svg" "$stage/usr/share/icons/hicolor/scalable/apps/org.uoo.BootManager.svg"
install -d -m 0755 "$stage/usr/share/icons/hicolor/symbolic" "$stage/usr/share/icons/hicolor/symbolic/apps"
install -m 0644 "$project_root/assets/icons/uoo-symbolic.svg" "$stage/usr/share/icons/hicolor/symbolic/apps/org.uoo.BootManager-symbolic.svg"
for size in 16 24 32 48 64 128 256 512; do
    install -d -m 0755 "$stage/usr/share/icons/hicolor/${size}x${size}" "$stage/usr/share/icons/hicolor/${size}x${size}/apps"
    install -m 0644 "$project_root/assets/icons/png/uoo-${size}.png" \
        "$stage/usr/share/icons/hicolor/${size}x${size}/apps/org.uoo.BootManager.png"
done
install -m 0644 "$project_root/LICENSE" "$stage/usr/share/doc/$package_name/copyright"
install -m 0644 "$project_root/README.md" "$stage/usr/share/doc/$package_name/README.md"
install -d -m 0755 "$stage/usr/share/doc/$package_name/docs"
for document in PRODUCTION.md VALIDATION.md PACKAGING.md; do
    install -m 0644 "$project_root/docs/$document" "$stage/usr/share/doc/$package_name/docs/"
done
while IFS= read -r -d '' asset; do
    relative="${asset#"$project_root/"}"
    install -D -m 0644 "$asset" "$stage/usr/share/doc/$package_name/$relative"
done < <(find "$project_root/assets/icons" -type f -print0)
if [[ -f "$project_root/docs/images/grub.png" ]]; then
    install -d -m 0755 "$stage/usr/share/doc/$package_name/docs" "$stage/usr/share/doc/$package_name/docs/images"
    install -m 0644 "$project_root/docs/images/grub.png" "$stage/usr/share/doc/$package_name/docs/images/"
fi

output="$project_root/dist/${package_name}_${package_version}_all.deb"
dpkg-deb --root-owner-group --build "$stage" "$output"
printf 'Built %s\n' "$output"
