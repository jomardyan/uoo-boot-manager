"""Launch Uoo without elevating the desktop process."""

import sys


def launch():
    try:
        from .app import main
    except ModuleNotFoundError as exc:
        if exc.name == "tkinter":
            print("Uoo needs Python Tk. Install it with: sudo apt install python3-tk", file=sys.stderr)
            return 1
        raise
    return main()


if __name__ == "__main__":
    raise SystemExit(launch())
