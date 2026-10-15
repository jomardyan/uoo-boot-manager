#!/usr/bin/env python3
"""Install build dependencies on Ubuntu/Debian or Arch/Manjaro.

Run as your normal user: system packages use sudo, while SDKs and runtimes
remain user-owned. --dry-run only probes installed tools and prints a plan.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "base": {
        "apt": "bash make python3 coreutils findutils grep sed tar gzip ca-certificates curl",
        "pacman": "bash make python coreutils findutils grep sed tar gzip ca-certificates curl",
    },
    "deb": {"apt": "dpkg-dev", "pacman": "dpkg"},
    "rpm": {"apt": "rpm", "pacman": "rpm-tools"},
    "gtk": {
        "apt": "python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1",
        "pacman": "python-gobject python-cairo gtk4 libadwaita",
    },
    "display": {
        "apt": "xvfb xauth dbus-x11 fonts-dejavu-core desktop-file-utils",
        "pacman": "xorg-server-xvfb xorg-xauth dbus ttf-dejavu desktop-file-utils",
    },
    "appimage": {
        "apt": "python3-venv binutils patchelf squashfs-tools desktop-file-utils",
        "pacman": "python python-pip binutils patchelf squashfs-tools desktop-file-utils",
    },
    "flatpak": {"apt": "flatpak flatpak-builder librsvg2-common", "pacman": "flatpak flatpak-builder librsvg"},
    "snap": {"apt": "snapd", "pacman": ""},
    "windows": {
        "apt": "libicu-dev libssl-dev libgssapi-krb5-2 zlib1g",
        "pacman": "icu openssl krb5 zlib",
    },
    "powershell": {"apt": "", "pacman": ""},
    "icons": {
        "apt": "python3-pil python3-gi gir1.2-gdkpixbuf-2.0 librsvg2-common",
        "pacman": "python-pillow python-gobject gdk-pixbuf2 librsvg",
    },
    "website": {"apt": "nodejs", "pacman": "nodejs"},
    "vm": {
        "apt": "qemu-system-x86 qemu-utils ovmf seabios xorriso openssh-client",
        "pacman": "qemu-system-x86 qemu-img edk2-ovmf seabios libisoburn openssh",
    },
    "arch": {"apt": "", "pacman": "base-devel zstd"},
}
TARGETS = {
    "help": (), "version": (), "clean": (), "distclean": (),
    "all": ("deb",), "build": ("deb",), "deb": ("deb",),
    "check-package": ("deb",), "test": ("gtk", "display"),
    "test-gtk": ("gtk", "display"), "smoke": ("gtk", "display"),
    "demo": ("gtk",), "check": ("deb", "gtk", "display"),
    "rpm": ("deb", "rpm"), "rpm-opensuse": ("deb", "rpm"),
    "arch": ("deb", "arch"), "appimage": ("gtk", "appimage"),
    "flatpak": ("flatpak",), "snap": ("gtk", "appimage", "snap"),
    "checksums": (), "verify-checksums": (),
    "windows": ("windows", "powershell"), "windows-test": ("windows",),
    "vm-test": ("deb", "vm"),
    "icons": ("icons",),
    "website": ("website",),
}
FORMATS = ("deb", "rpm", "rpm-opensuse", "arch", "appimage", "flatpak", "snap")
APPIMAGE_HASHES = {
    "x86_64": ("ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
               "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"),
    "aarch64": ("f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158",
                "00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444"),
}


def package_manager():
    if platform.system() != "Linux":
        raise ValueError("This installer supports Linux. On Windows use the .NET 10 SDK, PowerShell 7, and windows/scripts/install-build-tools.ps1 for Inno Setup.")
    info = platform.freedesktop_os_release()
    families = {info.get("ID", ""), *info.get("ID_LIKE", "").split()}
    if families & {"ubuntu", "debian"}:
        return "apt"
    if families & {"arch", "manjaro"}:
        return "pacman"
    raise ValueError("Automatic package installation supports Ubuntu/Debian and Arch/Manjaro. See docs/PACKAGING.md for other hosts.")


def resolve_targets(targets, manager, formats=FORMATS):
    # 'everything' is distinct from the Makefile's 'all' alias for Debian.
    everything = "everything" in targets
    selected = set(targets) - {"everything"}
    if everything:
        selected.update(("check", "appimage", "flatpak", "windows", "vm-test", "rpm", "arch", "icons", "website"))
        if manager == "apt":
            selected.add("snap")
            if "arch" not in targets:
                selected.discard("arch")
    if "packages" in selected:
        selected.remove("packages")
        if not formats or set(formats) - set(FORMATS):
            raise ValueError("PACKAGE_FORMATS must contain supported Linux package formats")
        selected.update(formats)
    unknown = selected - TARGETS.keys()
    if unknown:
        raise ValueError("Unknown Make operation(s): " + ", ".join(sorted(unknown)))
    if "arch" in selected and manager != "pacman":
        raise ValueError("The arch target needs an Arch/Manjaro host with makepkg; it cannot be installed with apt. Use Arch or the existing Arch CI job.")
    if "snap" in selected and manager != "apt":
        raise ValueError("Automatic Snapcraft setup requires an Ubuntu/Debian snapd host. Use that host or the existing Snap CI job.")
    groups = {"base"}
    for target in selected:
        groups.update(TARGETS[target])
    return groups


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


@contextmanager
def installation_lock():
    import fcntl

    directory = Path.home() / ".cache/uoo-boot-manager"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "install-deps.lock").open("a") as stream:
        print("Waiting for any other Uoo dependency installer...", flush=True)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Installer:
    def __init__(self, manager, dry_run=False):
        self.manager = manager
        self.dry_run = dry_run
        self.bin = Path.home() / ".local/bin"
        self.arch = platform.machine()
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")

    def run(self, *command):
        command = [str(value) for value in command]
        print("  $ " + shlex.join(command), flush=True)
        if not self.dry_run:
            subprocess.run(command, check=True, env=self.env)

    def note(self, message):
        print("  " + message, flush=True)

    def installed(self, package):
        if self.manager == "apt":
            result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True)
            return result.returncode == 0 and result.stdout.strip() == "install ok installed"
        return subprocess.run(["pacman", "-Q", package], capture_output=True).returncode == 0

    def system_packages(self, groups):
        packages = sorted({package for group in groups for package in GROUPS[group][self.manager].split()})
        missing = [package for package in packages if not self.installed(package)]
        if not missing:
            self.note("System packages are already installed.")
            return
        if self.manager == "apt":
            self.run("sudo", "apt-get", "update")
            self.run("sudo", "apt-get", "install", "-y", "--no-install-recommends", *missing)
        else:
            self.run("sudo", "pacman", "-S", "--needed", "--noconfirm", *missing)

    def download(self, url, destination, sha256=None):
        self.note("Download " + url + (" (verify SHA256)" if sha256 else ""))
        if self.dry_run:
            return
        if destination.is_symlink():
            raise ValueError("Refusing to overwrite a symlink: " + str(destination))
        if sha256 and destination.is_file() and digest(destination) == sha256:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                with urlopen(url, timeout=60) as response:
                    shutil.copyfileobj(response, stream)
                stream.close()
                if sha256 and digest(temporary) != sha256:
                    raise ValueError("SHA256 mismatch: " + url)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)

    def link(self, executable, name):
        link = self.bin / name
        if link.is_symlink() and link.resolve() == executable.resolve():
            return
        if os.path.lexists(link):
            raise ValueError("Refusing to overwrite " + str(link) + "; select the desired tool explicitly or remove the conflicting launcher.")
        self.note(f"Link {link} -> {executable}")
        if not self.dry_run:
            self.bin.mkdir(parents=True, exist_ok=True)
            link.symlink_to(executable)

    def dotnet(self):
        executable = shutil.which(os.environ.get("DOTNET", "dotnet"), path=self.env["PATH"])
        local = Path.home() / ".dotnet/dotnet"
        if not executable and local.is_file():
            executable = str(local)
        if executable:
            result = subprocess.run([executable, "--list-sdks"], capture_output=True, text=True)
            if result.returncode == 0 and re.search(r"^10\.0\.\d+\s", result.stdout, re.MULTILINE):
                self.note(".NET 10 SDK is already installed.")
                if executable == str(local):
                    self.link(local, "dotnet")
                return
        # Microsoft's installer supports an unprivileged, side-by-side SDK.
        script = ROOT / "build/tools/dotnet-install.sh"
        self.download("https://dot.net/v1/dotnet-install.sh", script)
        self.run("bash", script, "--channel", "10.0", "--install-dir", local.parent, "--no-path")
        self.link(local, "dotnet")
        self.run(local, "--list-sdks")

    def powershell(self):
        executable = shutil.which(os.environ.get("POWERSHELL", "pwsh"), path=self.env["PATH"])
        if executable:
            result = subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                                     'if ($PSVersionTable.PSVersion.Major -lt 7) { exit 1 }'], capture_output=True)
            if result.returncode == 0:
                self.note("PowerShell 7+ is already installed.")
                return
        if self.dry_run:
            self.note("Fetch Microsoft's latest stable PowerShell release, verify its published SHA256, extract under ~/.local/opt, and link ~/.local/bin/pwsh.")
            return
        with urlopen("https://api.github.com/repos/PowerShell/PowerShell/releases/latest", timeout=60) as response:
            release = json.load(response)
        tag = release["tag_name"]
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            raise ValueError("Expected a stable PowerShell version")
        cpu = {"x86_64": "x64", "aarch64": "arm64"}[self.arch]
        name = f"powershell-{tag[1:]}-linux-{cpu}.tar.gz"
        asset = next(asset for asset in release["assets"] if asset["name"] == name)
        sha256 = asset.get("digest", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", sha256):
            raise ValueError("PowerShell release is missing its published SHA256")
        url = f"https://github.com/PowerShell/PowerShell/releases/download/{tag}/{name}"
        destination = Path.home() / ".local/opt" / f"powershell-{tag[1:]}"
        if destination.exists():
            raise ValueError("PowerShell installation already exists but is not usable; inspect " + str(destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="powershell-", dir=destination.parent) as temporary:
            stage = Path(temporary)
            archive = stage / name
            self.download(url, archive, sha256.removeprefix("sha256:"))
            payload = stage / "payload"
            payload.mkdir()
            with tarfile.open(archive) as stream:
                stream.extractall(payload, filter="data")
            (payload / "pwsh").chmod(0o755)
            self.run(payload / "pwsh", "-NoProfile", "-NonInteractive", "-Command", '$PSVersionTable.PSVersion.ToString()')
            payload.rename(destination)
        self.link(destination / "pwsh", "pwsh")

    def appimage(self):
        tools = ROOT / "build/tools"
        tool = tools / "appimagetool.AppImage"
        runtime = tools / "runtime"
        tool_hash, runtime_hash = APPIMAGE_HASHES[self.arch]
        self.download(f"https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-{self.arch}.AppImage", tool, tool_hash)
        self.download(f"https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-{self.arch}", runtime, runtime_hash)
        if not self.dry_run:
            tool.chmod(0o755)
        python = "/usr/bin/python3" if self.manager == "apt" else "/usr/bin/python"
        venv = ROOT / "build/appimage-venv"
        self.run(python, "-m", "venv", "--system-site-packages", venv)
        self.run(venv / "bin/python", "-m", "pip", "install", "-r", ROOT / "packaging/appimage-requirements.txt")
        self.run(venv / "bin/python", "-c", 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_foreign("cairo"); from gi.repository import Adw; assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5), "libadwaita 1.5+ is required"')

    def flatpak(self):
        manifest = json.loads((ROOT / "packaging/org.uoo.BootManager.json").read_text())
        self.run("flatpak", "remote-add", "--user", "--if-not-exists", "flathub", "https://flathub.org/repo/flathub.flatpakrepo")
        self.run("flatpak", "install", "--user", "--noninteractive", "-y", "flathub",
                 manifest["runtime"] + "//" + manifest["runtime-version"],
                 manifest["sdk"] + "//" + manifest["runtime-version"])

    def snap(self):
        # A daemon-backed snapd installation is required; do not change host
        # security policy or enable unrelated services to bypass failures.
        if shutil.which("snap") and subprocess.run(["snap", "list", "snapcraft"], capture_output=True).returncode == 0:
            self.note("Snapcraft is already installed.")
        else:
            self.run("sudo", "snap", "install", "snapcraft", "--classic")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", default=["everything"], help="Make operations, or everything (default)")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without installs, downloads, or file changes")
    parser.add_argument("--package-formats", default=" ".join(FORMATS))
    args = parser.parse_args(argv)
    try:
        manager = package_manager()
        groups = resolve_targets(args.targets, manager, args.package_formats.split())
        installer = Installer(manager, args.dry_run)
        if groups & {"windows", "appimage"} and installer.arch not in APPIMAGE_HASHES:
            raise ValueError("SDK/AppImage bootstrap supports x86_64 and aarch64 hosts")
        if not args.dry_run and os.geteuid() == 0:
            raise ValueError("Run make deps as your normal user; only system package commands use sudo.")
        print(("Dependency preview" if args.dry_run else "Installing dependencies") + f" ({manager}; {installer.arch})", flush=True)
        if "everything" in args.targets:
            installer.note("Native Arch packaging is available on Arch/Manjaro; Snapcraft setup is available on Ubuntu/Debian.")
        if "windows" in groups:
            installer.note("make windows builds a Windows ZIP on Linux. Windows/Inno Setup is required for the installer and native Windows tests.")

        def install():
            installer.system_packages(groups)
            if "gtk" in groups:
                python = "/usr/bin/python3" if manager == "apt" else "/usr/bin/python"
                installer.run(python, "-c", 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Adw; assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5), "libadwaita 1.5+ is required; use a newer distribution"')
            if "windows" in groups:
                installer.dotnet()
            if "powershell" in groups:
                installer.powershell()
            if "windows" in groups:
                installer.note('Ensure ~/.local/bin is on PATH: export PATH="$HOME/.local/bin:$PATH"')
            if "appimage" in groups:
                installer.appimage()
            if "flatpak" in groups:
                installer.flatpak()
            if "snap" in groups:
                installer.snap()

        if args.dry_run:
            install()
        else:
            with installation_lock():
                install()
        return 0
    except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError, tarfile.TarError) as error:
        print("Dependency setup failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
