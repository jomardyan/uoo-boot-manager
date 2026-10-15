#!/usr/bin/env python3
"""Publish a requested release only after matching Linux/Windows CI and hashes pass."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time


def api(path):
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def find_release(repo, tag):
    # The tag endpoint resolves published releases; list releases also includes
    # drafts when authenticated with repository write access.
    page = 1
    while True:
        releases = api(f"repos/{repo}/releases?per_page=100&page={page}")
        matches = [release for release in releases if release["tag_name"] == tag]
        if len(matches) > 1:
            raise ValueError("Multiple releases found for " + tag)
        if matches:
            return api(f"repos/{repo}/releases/{matches[0]['id']}")
        if len(releases) < 100:
            raise ValueError("No draft or published release found for " + tag)
        page += 1


def expected_packages(version):
    linux = {
        f"uoo-boot-manager_{version}_all.deb",
        f"uoo-boot-manager-{version}-1.noarch.rpm",
        f"uoo-boot-manager-{version}-1.opensuse.noarch.rpm",
        f"uoo-boot-manager-{version}-1-any.pkg.tar.zst",
        *[f"Uoo_Boot_Manager-{version}-{arch}.AppImage" for arch in ("x86_64", "aarch64")],
        *[f"uoo-boot-manager-{version}-{arch}.flatpak" for arch in ("x86_64", "aarch64")],
        *[f"uoo-boot-manager_{version}_{arch}.snap" for arch in ("amd64", "arm64")],
    }
    windows = {f"Uoo-Boot-Manager-{version}-windows-{arch}{suffix}"
               for arch in ("x64", "arm64") for suffix in (".zip", "-setup.exe")}
    return linux, windows


def check_workflow(runs, sha, tag):
    matching = [run for run in runs if run["head_sha"] == sha and run["head_branch"] == tag
                and run["event"] == "workflow_dispatch"]
    if not matching:
        return False
    latest = max(matching, key=lambda run: run["id"])
    if latest["status"] != "completed":
        return False
    if latest["conclusion"] != "success":
        raise ValueError("Release build did not succeed: " + latest["html_url"])
    return True


def verify_manifest(directory, name, expected):
    found = set()
    for line in (directory / name).read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] not in expected or match[2] in found:
            raise ValueError("Invalid or unexpected checksum entry in " + name)
        path = directory / match[2]
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or unsafe release package: " + match[2])
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        if value.hexdigest() != match[1]:
            raise ValueError("Checksum mismatch: " + match[2])
        found.add(match[2])
    if found != expected:
        raise ValueError("Incomplete checksum manifest: " + name)


def publish(repo, version, sha, notes, timeout=3600):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a release version and a full commit SHA")
    tag = "v" + version
    reference = api(f"repos/{repo}/git/ref/tags/{tag}")
    if reference["object"]["type"] != "commit" or reference["object"]["sha"] != sha:
        raise ValueError("Release tag must point to the exact validated commit")
    deadline = time.monotonic() + timeout
    while True:
        states = [check_workflow(api(f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&event=workflow_dispatch&per_page=100")["workflow_runs"], sha, tag)
                  for workflow in ("verify.yml", "windows.yml")]
        if all(states):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Release builds did not complete before the publishing deadline")
        print("Waiting for validated Linux and Windows release builds...", flush=True)
        time.sleep(30)

    release = find_release(repo, tag)
    linux, windows = expected_packages(version)
    expected = linux | windows | {"SHA256SUMS", "SHA256SUMS-Windows"}
    names = [asset["name"] for asset in release["assets"]]
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError("Release assets are missing, duplicated, or unexpected; leaving the release unpublished")
    with tempfile.TemporaryDirectory(prefix="uoo-release-") as temporary:
        directory = Path(temporary)
        subprocess.run(["gh", "release", "download", tag, "--repo", repo, "--dir", str(directory)], check=True)
        verify_manifest(directory, "SHA256SUMS", linux)
        verify_manifest(directory, "SHA256SUMS-Windows", windows)
    if release["draft"]:
        subprocess.run(["gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest", "--notes-file", str(notes)], check=True)
    published = api(f"repos/{repo}/releases/{release['id']}")
    if published["draft"] or published["prerelease"]:
        raise ValueError("GitHub did not confirm a published stable release")
    print("Published verified release: " + published["html_url"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--notes", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if not args.notes.is_file():
        parser.error("Release notes are required")
    publish(args.repo, args.version, args.commit, args.notes, args.timeout)


if __name__ == "__main__":
    main()
