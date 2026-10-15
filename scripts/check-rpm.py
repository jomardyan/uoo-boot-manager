#!/usr/bin/env python3
"""Verify RPM file hashes, permissions, ownership, and absence of scriptlets."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import stat
import subprocess


SOURCE = Path(__file__).resolve().parents[1]
REQUIRED_RUNTIME_DEPENDENCIES = {
    "python3 >= 3.10",
    "python3-gobject",
    "gobject-introspection",
    "gtk4 >= 4.6",
    "libadwaita >= 1.5",
    "polkit",
}
OPENSUSE_RUNTIME_DEPENDENCIES = {
    "python3 >= 3.10", "python3-gobject", "python3-gobject-Gdk",
    "typelib-1_0-Gtk-4_0 >= 4.6", "typelib-1_0-Adw-1 >= 1.5",
    "girepository-1_0", "polkit",
}
spec = importlib.util.spec_from_file_location("deb_check", SOURCE / "scripts/check-package.py")
deb_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deb_check)


def query(package, *args):
    return subprocess.run(["rpm", "-qp", *args, str(package)], check=True,
                          capture_output=True, text=True).stdout


def check_package(package, variant="fedora"):
    if variant not in ("fedora", "opensuse"):
        raise ValueError("Unknown RPM variant")
    subprocess.run(["rpmkeys", "--checksig", "--nosignature", str(package)], check=True,
                   capture_output=True, text=True)
    if query(package, "--scripts").strip() or query(package, "--triggers").strip():
        raise ValueError("RPM must not contain installation/removal scriptlets or triggers")
    if query(package, "--qf", "%{NAME} %{ARCH} %{FILEDIGESTALGO}") != "uoo-boot-manager noarch 8":
        raise ValueError("Unexpected RPM name, architecture, or file digest algorithm")
    requirements = {" ".join(line.split()) for line in query(package, "--requires").splitlines()}
    expected_requirements = OPENSUSE_RUNTIME_DEPENDENCIES if variant == "opensuse" else REQUIRED_RUNTIME_DEPENDENCIES
    missing = expected_requirements - requirements
    if missing:
        raise ValueError("Missing RPM runtime dependencies: " + ", ".join(sorted(missing)))
    expected = deb_check.expected_files(SOURCE)
    directories = {str(parent) for name in expected for parent in Path(name).parents}
    found = set()
    listing = query(package, "--qf", "[%{FILENAMES}\t%{FILEMODES}\t%{FILEUSERNAME}\t%{FILEGROUPNAME}\t%{FILEDIGESTS}\t%{FILELINKTOS}\n]")
    for line in listing.splitlines():
        name, mode, user, group, digest, link = line.split("\t")
        name = name.lstrip("/")
        mode = int(mode)
        if user != "root" or group != "root" or mode & 0o7022 or link:
            raise ValueError(f"Unsafe RPM ownership, permissions, or link: {name}")
        if stat.S_ISDIR(mode):
            if name not in directories or stat.S_IMODE(mode) != 0o755:
                raise ValueError(f"Unexpected RPM directory: {name}")
            continue
        if not stat.S_ISREG(mode) or name not in expected or name in found:
            raise ValueError(f"Unexpected RPM file: {name}")
        source, permissions = expected[name]
        if stat.S_IMODE(mode) != permissions or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError(f"RPM contents or permissions differ from source: {name}")
        found.add(name)
    if found != expected.keys():
        raise ValueError("Missing RPM files: " + ", ".join(sorted(expected.keys() - found)))
    return len(found)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--variant", choices=("fedora", "opensuse"), default="fedora")
    args = parser.parse_args()
    try:
        count = check_package(args.package, args.variant)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"RPM check failed: {exc}\n")
    print(f"RPM verified: {count} source-matching files, runtime dependencies, root ownership, safe permissions, no scriptlets")
