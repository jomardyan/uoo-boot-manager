#!/usr/bin/env python3
"""Prepare WinGet manifests from published installers after verifying their hashes."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REPO = "jomardyan/uoo-boot-manager"
PACKAGE_ID = "HaykJomardyan.UooBootManager"
SCHEMA = "1.6.0"
PRODUCT_CODE = "{1179B417-6013-4CFA-92E3-41C9F5FC07F5}_is1"


def fetch(url, limit=2 * 1024 * 1024):
    request = Request(url, headers={"User-Agent": "Uoo-WinGet-manifest-builder"})
    with urlopen(request, timeout=120) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Release metadata exceeds the size limit")
    return data


def installer_hash(url, expected_size):
    value = hashlib.sha256()
    size = 0
    with urlopen(Request(url, headers={"User-Agent": "Uoo-WinGet-manifest-builder"}), timeout=120) as response:
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 512 * 1024 * 1024:
                raise ValueError("Installer exceeds the size limit")
            value.update(block)
    if size != expected_size or size == 0:
        raise ValueError("Installer download size does not match the published asset")
    return value.hexdigest().upper()


def verified_installers(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable release version such as 2.2.0")
    tag = "v" + version
    release = json.loads(fetch(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"))
    if release["tag_name"] != tag or release["draft"] or release["prerelease"]:
        raise ValueError("WinGet manifests require a published stable release")
    assets = release["assets"]
    base = f"https://github.com/{REPO}/releases/download/{tag}/"

    def asset(name):
        matches = [item for item in assets if item["name"] == name]
        if len(matches) != 1 or matches[0]["browser_download_url"] != base + name:
            raise ValueError("Missing, duplicated, or unexpected release asset: " + name)
        if matches[0]["state"] != "uploaded":
            raise ValueError("Release asset upload is incomplete: " + name)
        return matches[0]

    manifest = asset("SHA256SUMS-Windows")
    checksums = {}
    for line in fetch(manifest["browser_download_url"]).decode().splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in checksums:
            raise ValueError("Invalid or duplicated Windows checksum entry")
        checksums[match[2]] = match[1].upper()
    installers = []
    for arch in ("x64", "arm64"):
        name = f"Uoo-Boot-Manager-{version}-windows-{arch}-setup.exe"
        item = asset(name)
        print("Verifying published " + arch + " installer...", flush=True)
        digest = installer_hash(item["browser_download_url"], item["size"])
        if checksums.get(name) != digest:
            raise ValueError("Installer does not match SHA256SUMS-Windows: " + name)
        if item.get("digest") and item["digest"].lower() != "sha256:" + digest.lower():
            raise ValueError("Installer does not match the GitHub asset digest: " + name)
        installers.append({"Architecture": arch, "InstallerUrl": item["browser_download_url"], "InstallerSha256": digest})
    return installers


def yaml_lines(value, indent=0):
    """Emit the plain mappings/lists/scalars used by WinGet without dependencies."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                yield " " * indent + key + ":"
                yield from yaml_lines(item, indent + 2)
            else:
                yield " " * indent + key + ": " + json.dumps(item, ensure_ascii=False)
    else:
        for item in value:
            lines = list(yaml_lines(item, indent + 2)) if isinstance(item, dict) else [" " * (indent + 2) + json.dumps(item)]
            yield " " * indent + "- " + lines[0].lstrip()
            yield from lines[1:]


def documents(version, installers):
    common = {"PackageIdentifier": PACKAGE_ID, "PackageVersion": version}
    return {
        "version": dict(common, DefaultLocale="en-US", ManifestType="version", ManifestVersion=SCHEMA),
        "defaultLocale": dict(common, PackageLocale="en-US", Publisher="Hayk Jomardyan",
            PublisherUrl="https://github.com/jomardyan",
            PublisherSupportUrl=f"https://github.com/{REPO}/issues",
            PackageName="Uoo Boot Manager", PackageUrl=f"https://github.com/{REPO}",
            License="MIT", LicenseUrl=f"https://github.com/{REPO}/blob/v{version}/LICENSE",
            ShortDescription="Native boot configuration and recovery for Windows 11.",
            Description="Manage Windows Boot Manager, UEFI entries, and configuration backups from a native desktop. System changes use a separately authenticated administrator helper.",
            Tags=["boot", "boot-manager", "bcd", "uefi", "recovery"],
            ReleaseNotesUrl=f"https://github.com/{REPO}/releases/tag/v{version}",
            ManifestType="defaultLocale", ManifestVersion=SCHEMA),
        "installer": dict(common, InstallerLocale="en-US", MinimumOSVersion="10.0.22000.0",
            InstallerType="inno", Scope="machine", UpgradeBehavior="install",
            ProductCode=PRODUCT_CODE, ElevationRequirement="elevationRequired",
            AppsAndFeaturesEntries=[{"DisplayName": f"Uoo Boot Manager version {version}",
                "Publisher": "Hayk Jomardyan", "DisplayVersion": version, "ProductCode": PRODUCT_CODE}],
            Installers=installers, ManifestType="installer", ManifestVersion=SCHEMA),
    }


def write_manifests(output, version, installers):
    directory = output / "manifests/h/HaykJomardyan/UooBootManager" / version
    files = {}
    for kind, document in documents(version, installers).items():
        suffix = {"version": "", "defaultLocale": ".locale.en-US", "installer": ".installer"}[kind]
        schema = f"https://aka.ms/winget-manifest.{kind}.{SCHEMA}.schema.json"
        files[PACKAGE_ID + suffix + ".yaml"] = "# yaml-language-server: $schema=" + schema + "\n\n" + "\n".join(yaml_lines(document)) + "\n"
    if directory.exists():
        if set(p.name for p in directory.iterdir()) != set(files) or any((directory / name).read_text() != text for name, text in files.items()):
            raise ValueError("Refusing to replace different manifests for an existing version")
        return directory
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".uoo-winget-", dir=directory.parent) as temporary:
        staging = Path(temporary) / version
        staging.mkdir()
        for name, text in files.items():
            (staging / name).write_text(text, encoding="utf-8")
        staging.rename(directory)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "packaging/winget")
    args = parser.parse_args()
    directory = write_manifests(args.output, args.version, verified_installers(args.version))
    print("WinGet submission prepared: " + str(directory))
    print("Package ID: " + PACKAGE_ID + " (requires community repository acceptance)")


if __name__ == "__main__":
    main()
