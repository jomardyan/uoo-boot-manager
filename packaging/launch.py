#!/usr/bin/python3
"""Load the desktop app from this checkout or the installed package."""

from pathlib import Path
import sys

package_dir = Path(__file__).resolve().parent
if package_dir.name == "packaging":
    package_dir = package_dir.parent
sys.path.insert(0, str(package_dir))

from uoo.__main__ import launch


if __name__ == "__main__":
    raise SystemExit(launch())
