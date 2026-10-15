#!/usr/bin/env python3
"""Create a checksum manifest covering every supported release package."""

import argparse
import hashlib
from pathlib import Path

SUFFIXES = (".deb", ".rpm", ".AppImage", ".pkg.tar.zst", ".flatpak", ".snap")


def write_checksums(directory):
    directory = Path(directory)
    packages = sorted(path for path in directory.iterdir() if path.name.endswith(SUFFIXES))
    if not packages:
        raise ValueError("No release packages found")
    lines = []
    for path in packages:
        if path.is_symlink() or not path.is_file() or any(c in path.name for c in "\n\r\\"):
            raise ValueError("Unsafe release artifact")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {path.name}\n")
    (directory / "SHA256SUMS").write_text("".join(lines))
    return len(packages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(f"Checksums written for {write_checksums(args.directory)} release packages.")
