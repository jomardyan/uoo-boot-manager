#!/usr/bin/python3
"""Read-only companion for sandboxed desktops; accepts no system actions."""

import json
from pathlib import Path
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv not in ([], ["--probe"]):
            raise ValueError("The inspector accepts only --probe or no arguments.")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from uoo import __version__
        from uoo.desktop_runtime import native_helper_available
        response = {"ok": True, "version": __version__, "installed": native_helper_available()}
        if not argv:
            from uoo.system import snapshot
            response["snapshot"] = snapshot()
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    print(json.dumps(response))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
