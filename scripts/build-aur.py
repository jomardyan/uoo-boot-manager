#!/usr/bin/env python3
"""Render the AUR PKGBUILD and .SRCINFO for a published release."""

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def deb_sha256(manifest, version):
    name = f"uoo-boot-manager_{version}_all.deb"
    matches = [line.split()[0] for line in manifest.read_text().splitlines()
               if len(line.split()) == 2 and line.split()[1].lstrip("*") == name]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-f]{64}", matches[0]):
        raise ValueError(f"SHA256SUMS must list {name} exactly once")
    return matches[0]


def render(version, sha256, output):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable release version such as 2.2.1")
    output.mkdir(parents=True, exist_ok=True)
    for template, target in (("PKGBUILD.in", "PKGBUILD"), ("SRCINFO.in", ".SRCINFO")):
        text = (ROOT / "packaging/aur" / template).read_text()
        (output / target).write_text(text.replace("@VERSION@", version).replace("@SHA256@", sha256))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha256sums", type=Path, required=True, help="verified SHA256SUMS release manifest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        render(args.version, deb_sha256(args.sha256sums, args.version), args.output)
    except ValueError as error:
        parser.exit(1, f"AUR package preparation failed: {error}\n")


if __name__ == "__main__":
    main()
