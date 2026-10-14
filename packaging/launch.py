#!/usr/bin/python3
"""Load the desktop app from its fixed, root-owned package directory."""

import sys

sys.path.insert(0, "/usr/lib/uoo-boot-manager")

from uoo.app import main


if __name__ == "__main__":
    raise SystemExit(main())
