#!/usr/bin/python3
"""Authenticated helper. Only the installed, root-owned copy can change boot data."""

import json
import os
from pathlib import Path
import stat
import sys


def main():
    try:
        installed = Path("/usr/lib/uoo-boot-manager")
        actual = Path(__file__).resolve()
        if os.geteuid() != 0:
            raise RuntimeError("Administrator authentication is required.")
        if actual != installed / "helper.py":
            raise RuntimeError("Install the Ubuntu package before applying real boot changes.")
        # Prevent privileged execution of user-editable Python files, even if a
        # damaged installation leaves the launcher itself in the expected place.
        for path in (installed, actual, installed / "uoo", installed / "uoo" / "__init__.py",
                     installed / "uoo" / "core.py", installed / "uoo" / "system.py"):
            info = path.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise RuntimeError("The installed helper and modules must be owned by root and not writable by other users.")
        if len(sys.argv) != 2 or len(sys.argv[1]) > 16384:
            raise ValueError("Expected one bounded JSON request.")
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(installed))
        from uoo.system import Manager
        response = Manager().dispatch(json.loads(sys.argv[1]))
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    print(json.dumps(response))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
