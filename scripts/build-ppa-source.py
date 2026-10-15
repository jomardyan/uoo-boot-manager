#!/usr/bin/env python3
"""Build an unsigned Launchpad source bundle from a clean working-tree snapshot."""

import argparse
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEBIAN_FILES = (
    "debian/control", "debian/changelog", "debian/copyright",
    "debian/rules", "debian/source/format", "debian/.gitignore",
    "debian/source/lintian-overrides", "debian/uoo-boot-manager.manpages",
    "debian/uoo-boot-manager.1", "debian/uoo-boot-inspect.1", "debian/uoo-boot-helper.1",
)


def command(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def build(output):
    upstream = command("python3", "scripts/package-version.py")
    version = command("dpkg-parsechangelog", "-S", "Version")
    if version.rsplit("-", 1)[0] != upstream:
        raise ValueError("debian/changelog upstream version must match the app version")
    if command("dpkg-parsechangelog", "-S", "Distribution") != "noble":
        raise ValueError("This source package currently targets Ubuntu 24.04 (noble)")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob(f"uoo-boot-manager_{upstream}*")):
        raise ValueError(f"Existing source bundle in {output}; choose a new --output directory")

    epoch = int(command("dpkg-parsechangelog", "-S", "Timestamp"))
    paths = set(command("git", "ls-files", "-z").split("\0"))
    paths.update(DEBIAN_FILES)
    paths.update(("scripts/build-ppa-source.py", "tests/test_ppa_packaging.py"))
    # Avoid local/editor/hosting metadata. Untracked files outside this explicit
    # packaging set are never swept into an upload (in particular, no keyrings).
    paths = sorted(p for p in paths if p and not p.startswith((
        ".git/", ".vscode/", "website/.openai/", "website/node_modules/",
        "build/", "dist/",
    )))
    # Stay outside the editor workspace: language servers may otherwise build
    # the copied Windows projects and contaminate the source-only snapshot.
    with tempfile.TemporaryDirectory(prefix="uoo-ppa-source.") as temporary:
        work = Path(temporary)
        source = work / f"uoo-boot-manager-{upstream}"
        for relative in paths:
            original = ROOT / relative
            if not original.is_file() or original.is_symlink():
                raise ValueError(f"Expected regular source file: {relative}")
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)

        archive = work / f"uoo-boot-manager_{upstream}.orig.tar.gz"
        with archive.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as tar:
                    for relative in paths:
                        if relative.startswith("debian/"):
                            continue
                        original = source / relative
                        item = tar.gettarinfo(original, arcname=f"{source.name}/{relative}")
                        item.uid = item.gid = 0
                        item.uname = item.gname = "root"
                        item.mtime = epoch
                        item.mode = 0o755 if original.stat().st_mode & 0o111 else 0o644
                        with original.open("rb") as content:
                            tar.addfile(item, content)

        # The snapshot contains no build outputs, so no pre-clean is necessary.
        # Source-only construction needs dpkg-dev, without local debhelper or
        # access to the maintainer's private signing key.
        subprocess.run([
            "dpkg-buildpackage", "-S", "-sa", "-us", "-uc", "-d", "-nc",
        ], cwd=source, env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}, check=True)
        dsc = work / f"uoo-boot-manager_{version}.dsc"
        subprocess.run(["dpkg-source", "-x", str(dsc), str(work / "verified-source")], check=True)
        for artifact in sorted(work.glob("uoo-boot-manager_*")):
            if artifact.is_file():
                shutil.copy2(artifact, output / artifact.name)

    changes = output / f"uoo-boot-manager_{version}_source.changes"
    print(f"Unsigned source bundle verified: {changes}")
    print("Sign with debsign, then upload the signed .changes using dput.")
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/ppa")
    args = parser.parse_args()
    try:
        build(args.output.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Source package build failed: {error}\n")


if __name__ == "__main__":
    main()
