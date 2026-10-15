"""Fixed host commands for a desktop running inside Flatpak."""

import json
import os
from pathlib import Path
import subprocess

from . import __version__

INSPECTOR = "/usr/bin/uoo-boot-inspect"
FLATPAK_SPAWN = "/usr/bin/flatpak-spawn"
_host_installed = None


def is_flatpak():
    return os.environ.get("FLATPAK_ID") == "org.uoo.BootManager" or Path("/.flatpak-info").is_file()


def host_command(command):
    return [FLATPAK_SPAWN, "--host", *command] if is_flatpak() else list(command)


def native_helper_available():
    for path in (Path("/usr/lib/uoo-boot-manager/helper.py"), Path("/usr/bin/uoo-boot-helper")):
        try:
            info = path.lstat()
            if not path.is_file() or path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                return False
        except OSError:
            return False
    return Path("/usr/bin/pkexec").is_file()


def _inspect(probe=False):
    global _host_installed
    command = host_command([INSPECTOR, *(["--probe"] if probe else [])])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout)
        if result.returncode or not isinstance(payload, dict) or not payload.get("ok"):
            raise ValueError("Host inspection failed.")
        if payload.get("version") != __version__:
            raise ValueError("Install the matching native Uoo package before using this Flatpak.")
        if type(payload.get("installed")) is not bool:
            raise ValueError("Invalid host-helper response.")
        _host_installed = payload["installed"]
        return payload
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        _host_installed = False
        raise RuntimeError("Install the matching native Uoo package on the host to use system mode from Flatpak. "
                           "The --demo option works without a native package. " + str(exc)) from exc


def host_helper_available():
    if _host_installed is None:
        try:
            _inspect(probe=True)
        except RuntimeError:
            return False
    return bool(_host_installed)


def host_snapshot():
    global _host_installed
    value = _inspect()
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("capabilities"), dict):
        _host_installed = False
        raise RuntimeError("The native inspector returned an invalid boot snapshot.")
    return snapshot
