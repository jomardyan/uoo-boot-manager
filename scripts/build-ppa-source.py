#!/usr/bin/env python3
"""Build an unsigned Launchpad source bundle from a clean working-tree snapshot."""

import argparse
import csv
from datetime import datetime, timezone
from email.utils import format_datetime
import gzip
import os
from pathlib import Path
import re
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
UBUNTU_SERIES_DATA = Path("/usr/share/distro-info/ubuntu.csv")


def supported_series(data_file=UBUNTU_SERIES_DATA, today=None):
    """Read compatible, released, standard-supported suites from Ubuntu data."""
    today = today or datetime.now(timezone.utc).date()
    releases = []
    with data_file.open(newline="") as stream:
        for row in csv.DictReader(stream):
            version = row["version"].split()[0]
            numbers = tuple(int(part) for part in version.split("."))
            if numbers < (24, 4) or not row.get("release") or not row.get("eol"):
                continue
            release = datetime.strptime(row["release"], "%Y-%m-%d").date()
            end = datetime.strptime(row["eol"], "%Y-%m-%d").date()
            if release <= today <= end:
                releases.append((numbers, row["series"], version))
    return {suite: version for _, suite, version in sorted(releases)}


def command(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def target_version(version, declared, requested, revision=None):
    suite = requested or declared
    if not re.fullmatch(r"[a-z][a-z0-9-]*", suite):
        raise ValueError("Invalid Ubuntu suite name")
    if revision is not None and revision < 1:
        raise ValueError("PPA revision must be a positive integer")
    if suite == declared and revision is None:
        return version, suite
    suffix = re.search(r"~" + re.escape(declared) + r"\d+$", version)
    if not suffix:
        raise ValueError("Changing series requires a ~<series><revision> version suffix")
    return version[:suffix.start()] + f"~{suite}{revision or 1}", suite


def unpack_upstream(archive, work, upstream):
    prefix = f"uoo-boot-manager-{upstream}"
    with tarfile.open(archive) as tar:
        for member in tar:
            parts = Path(member.name).parts
            if (not parts or parts[0] != prefix or ".." in parts
                    or (not member.isfile() and not member.isdir())
                    or (len(parts) > 1 and parts[1] == "debian")):
                raise ValueError(f"Unexpected upstream archive entry: {member.name}")
        tar.extractall(work, filter="data")
    return work / prefix


def build(output, series=None, original_archive=None, revision=None):
    upstream = command("python3", "scripts/package-version.py")
    declared_version = command("dpkg-parsechangelog", "-S", "Version")
    if declared_version.rsplit("-", 1)[0] != upstream:
        raise ValueError("debian/changelog upstream version must match the app version")
    declared = command("dpkg-parsechangelog", "-S", "Distribution")
    version, suite = target_version(declared_version, declared, series, revision)
    if version != declared_version and original_archive is None:
        raise ValueError("Changing Ubuntu series/revision requires --orig with the upstream archive already prepared for this release")
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
        archive = work / f"uoo-boot-manager_{upstream}.orig.tar.gz"
        if original_archive:
            # Launchpad requires identical orig bytes for every upload of the
            # same upstream version. Reuse the accepted archive, not a fresh
            # snapshot containing subsequent local documentation/code edits.
            shutil.copy2(original_archive, archive)
            source = unpack_upstream(archive, work, upstream)
        for relative in DEBIAN_FILES if original_archive else paths:
            original = ROOT / relative
            if not original.is_file() or original.is_symlink():
                raise ValueError(f"Expected regular source file: {relative}")
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)

        if version != declared_version:
            changelog = source / "debian/changelog"
            maintainer = command("dpkg-parsechangelog", "-S", "Maintainer")
            epoch = max(epoch + 1, int(datetime.now(timezone.utc).timestamp()))
            date = format_datetime(datetime.fromtimestamp(epoch, timezone.utc))
            entry = (
                f"uoo-boot-manager ({version}) {suite}; urgency=medium\n\n"
                f"  * Package the existing upstream release for Ubuntu {suite}.\n"
                "  * Preserve reviewed Python shebangs across build-tool versions.\n\n"
                f" -- {maintainer}  {date}\n\n"
            )
            changelog.write_text(entry + changelog.read_text())

        if not archive.exists():
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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--series", nargs="+", help="one or more supported Ubuntu codenames")
    selection.add_argument("--all-supported", action="store_true", help="target all currently supported compatible Ubuntu releases")
    parser.add_argument("--list-series", action="store_true", help="show supported Ubuntu targets without building")
    parser.add_argument("--revision", type=int, help="PPA revision suffix for every selected suite (e.g. 2)")
    parser.add_argument("--orig", type=Path, help="reuse the exact upstream archive already uploaded to the PPA")
    args = parser.parse_args()
    try:
        releases = supported_series()
        if not releases:
            raise ValueError("No compatible supported Ubuntu releases found; update distro-info-data")
        if args.list_series:
            for suite, version in releases.items():
                print(f"{suite}\tUbuntu {version}")
            return
        suites = list(releases) if args.all_supported else (args.series or [command("dpkg-parsechangelog", "-S", "Distribution")])
        for suite in suites:
            if suite not in releases:
                raise ValueError(f"{suite} is not a currently supported Ubuntu 24.04+ target; update distro-info-data or use: {', '.join(releases)}")
        if len(suites) != len(set(suites)):
            raise ValueError("Each Ubuntu suite must be selected only once")
        original = args.orig.resolve() if args.orig else None
        for suite in suites:
            output = args.output.resolve() / suite if args.all_supported or len(suites) > 1 else args.output.resolve()
            build(output, suite, original, args.revision)
            if original is None:
                upstream = command("python3", "scripts/package-version.py")
                original = output / f"uoo-boot-manager_{upstream}.orig.tar.gz"
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Source package build failed: {error}\n")


if __name__ == "__main__":
    main()
