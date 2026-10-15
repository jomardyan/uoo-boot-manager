"""Launch Uoo without elevating the desktop process."""

import sys


def launch():
    try:
        from .gtk_app import main
    except (ModuleNotFoundError, ValueError) as exc:
        if getattr(exc, "name", None) == "gi" or "Namespace Gtk" in str(exc) or "Namespace Adw" in str(exc):
            print("Uoo needs GTK4 and libadwaita. On Ubuntu 24.04+, install: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1", file=sys.stderr)
            return 1
        raise
    return main()


if __name__ == "__main__":
    raise SystemExit(launch())
