#!/usr/bin/env python3
"""Check an archived Debian package without installing or executing its contents."""

import argparse
import io
from pathlib import Path
import subprocess
import tarfile


class PackageError(ValueError):
    pass


def expected_files(source):
    mapping = {
        "usr/bin/uoo-boot-manager": (source / "packaging/uoo-boot-manager", 0o755),
        "usr/bin/uoo-boot-helper": (source / "packaging/uoo-boot-helper", 0o755),
        "usr/bin/uoo-boot-inspect": (source / "packaging/uoo-boot-inspect", 0o755),
        "usr/lib/uoo-boot-manager/launch.py": (source / "packaging/launch.py", 0o644),
        "usr/lib/uoo-boot-manager/helper.py": (source / "helper.py", 0o755),
        "usr/lib/uoo-boot-manager/inspector.py": (source / "inspector.py", 0o644),
        "usr/share/applications/uoo-boot-manager.desktop": (source / "packaging/uoo-boot-manager.desktop", 0o644),
        "usr/share/icons/hicolor/scalable/apps/org.uoo.BootManager.svg": (source / "packaging/uoo-boot-manager.svg", 0o644),
        "usr/share/icons/hicolor/symbolic/apps/org.uoo.BootManager-symbolic.svg": (source / "assets/icons/uoo-symbolic.svg", 0o644),
        "usr/share/doc/uoo-boot-manager/copyright": (source / "LICENSE", 0o644),
        "usr/share/doc/uoo-boot-manager/README.md": (source / "README.md", 0o644),
        "usr/share/doc/uoo-boot-manager/docs/PRODUCTION.md": (source / "docs/PRODUCTION.md", 0o644),
        "usr/share/doc/uoo-boot-manager/docs/VALIDATION.md": (source / "docs/VALIDATION.md", 0o644),
        "usr/share/doc/uoo-boot-manager/docs/PACKAGING.md": (source / "docs/PACKAGING.md", 0o644),
        "usr/share/polkit-1/actions/org.uoo.boot-manager.policy": (source / "packaging/org.uoo.boot-manager.policy", 0o644),
    }
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        mapping[f"usr/share/icons/hicolor/{size}x{size}/apps/org.uoo.BootManager.png"] = (source / f"assets/icons/png/uoo-{size}.png", 0o644)
    for asset in sorted((source / "assets/icons").rglob("*")):
        if asset.is_file():
            mapping["usr/share/doc/uoo-boot-manager/" + asset.relative_to(source).as_posix()] = (asset, 0o644)
    for module in sorted((source / "uoo").glob("*.py")):
        mapping["usr/lib/uoo-boot-manager/uoo/" + module.name] = (module, 0o644)
    if (source / "docs/images/grub.png").is_file():
        mapping["usr/share/doc/uoo-boot-manager/docs/images/grub.png"] = (source / "docs/images/grub.png", 0o644)
    return mapping


def validate_data(source, archive):
    expected = expected_files(source)
    directories = {"."}
    for name in expected:
        directories.update(str(parent) for parent in Path(name).parents if str(parent) != ".")
    found = set()
    for member in archive:
        name = member.name.removeprefix("./").rstrip("/") or "."
        if member.uid != 0 or member.gid != 0:
            raise PackageError(f"Archive member is not root-owned: {name}")
        if member.mode & 0o7022:
            raise PackageError(f"Unsafe archive permissions on {name}: {member.mode:o}")
        if member.isdir():
            if name not in directories:
                raise PackageError(f"Unexpected archive directory: {name}")
            if member.mode != 0o755:
                raise PackageError(f"Unexpected directory permissions: {name}")
            continue
        if not member.isfile():
            raise PackageError(f"Only regular files and directories are permitted: {name}")
        if name not in expected or name in found:
            raise PackageError(f"Unexpected or duplicate archive file: {name}")
        path, mode = expected[name]
        if member.mode != mode:
            raise PackageError(f"Unexpected file permissions on {name}: {member.mode:o}")
        if archive.extractfile(member).read() != path.read_bytes():
            raise PackageError(f"Archive differs from reviewed source: {name}")
        found.add(name)
    missing = expected.keys() - found
    if missing:
        raise PackageError("Missing package files: " + ", ".join(sorted(missing)))
    return len(found)


def check_package(package, source):
    data = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(package)], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        count = validate_data(source, archive)
    control = subprocess.run(["dpkg-deb", "--ctrl-tarfile", str(package)], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(control), mode="r:") as archive:
        members = {}
        for item in archive:
            name = item.name.removeprefix("./").rstrip("/") or "."
            if item.uid or item.gid or item.mode & 0o7022:
                raise PackageError("Unsafe control archive ownership or permissions")
            if item.isdir() and name == "." and item.mode == 0o755:
                continue
            if not item.isfile() or name in members:
                raise PackageError("Unexpected control archive member")
            members[name] = item
        if members.keys() != {"control"}:
            raise PackageError("Package must have control metadata only, without installation/removal scripts")
        item = members["control"]
        if item.uid or item.gid or item.mode != 0o644:
            raise PackageError("Unsafe control metadata ownership or permissions")
        if archive.extractfile(item).read() != (source / "packaging/control").read_bytes():
            raise PackageError("Package control metadata differs from reviewed source")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        count = check_package(args.package.resolve(), args.source.resolve())
    except (PackageError, OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        parser.exit(1, f"Package check failed: {exc}\n")
    print(f"Package verified: {count} source-matching files, root ownership, safe permissions, no maintainer scripts")


if __name__ == "__main__":
    main()
