#!/usr/bin/env python3
"""Check an Arch package's privileged payload and dependency metadata."""

import argparse
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deb_check", SOURCE / "scripts/check-package.py")
deb_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deb_check)
DEPENDENCIES = {"python>=3.10", "python-gobject", "gobject-introspection-runtime",
                "gtk4>=4.6", "libadwaita>=1.5", "polkit"}


def validate_archive(archive, source=SOURCE):
    payload, metadata, seen = [], {}, set()
    for member in archive:
        name = member.name.removeprefix("./").rstrip("/") or "."
        if name in seen:
            raise ValueError("Duplicate Arch archive member")
        seen.add(name)
        if name.startswith(".") and name != ".":
            if name not in (".PKGINFO", ".BUILDINFO", ".MTREE") or not member.isfile() or member.uid or member.gid or member.mode & 0o7022:
                raise ValueError("Unsupported or unsafe Arch metadata/script")
            if member.size > 2 * 1024 * 1024:
                raise ValueError("Oversized Arch metadata")
            metadata[name] = archive.extractfile(member).read()
        else:
            payload.append(member)
    if ".PKGINFO" not in metadata:
        raise ValueError("Missing Arch package metadata")
    values = {}
    for line in metadata[".PKGINFO"].decode().splitlines():
        key, separator, value = line.partition(" = ")
        if separator:
            values.setdefault(key, []).append(value)
    if values.get("pkgname") != ["uoo-boot-manager"] or values.get("arch") != ["any"]:
        raise ValueError("Unexpected Arch package identity")
    if not DEPENDENCIES <= set(values.get("depend", [])):
        raise ValueError("Missing Arch runtime dependency")
    class Payload:
        def __iter__(self):
            return iter(payload)

        def extractfile(self, member):
            return archive.extractfile(member)
    return deb_check.validate_data(source, Payload())


def check_package(path):
    result = subprocess.run(["zstd", "--decompress", "--stdout", str(path)], check=True, capture_output=True)
    if len(result.stdout) > 64 * 1024 * 1024:
        raise ValueError("Oversized Arch archive")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        return validate_archive(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        count = check_package(args.package)
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        parser.exit(1, f"Arch package check failed: {exc}\n")
    print(f"Arch package verified: {count} source-matching files, root ownership, safe permissions, runtime dependencies")
