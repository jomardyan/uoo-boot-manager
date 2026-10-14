#!/usr/bin/python3
"""Capture native preview screens on an isolated Xvfb display.

Example: GDK_BACKEND=x11 GSK_RENDERER=cairo dbus-run-session -- \
    /usr/bin/python3 scripts/capture-ui.py --display :98

Development dependencies: PyGObject, GTK4, libadwaita >=1.5, Pillow, Xvfb.
Every boot operation uses DemoManager; system settings are never modified.
"""

import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", required=True, help="isolated Xvfb display, for example :98")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "images")
    args = parser.parse_args()
    os.environ["DISPLAY"] = args.display
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gdk, GLib, Gtk
    from PIL import ImageGrab
    from uoo.gtk_app import BootWindow, UooApplication

    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        parser.exit(1, "The requested virtual display is unavailable.\n")
    Adw.init()
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    args.output.mkdir(parents=True, exist_ok=True)
    application = UooApplication(demo=True)
    application.register(None)
    window = BootWindow(application, demo=True)
    window.confirm_handler = lambda *_args: True
    errors = []
    window.error_handler = lambda title, message: errors.append(f"{title}: {message}")
    window.present()

    def wait(milliseconds=250):
        loop = GLib.MainLoop()
        GLib.timeout_add(milliseconds, loop.quit)
        loop.run()

    wait(650)
    if window.busy or errors:
        raise RuntimeError("The preview could not initialize: " + "; ".join(errors))
    device = next(device for device in window.snapshot["devices"] if device.get("esp"))
    window.dispatch({"action": "custom_entries", "entries": [{
        "id": "uoo-" + "a" * 32, "title": "Ubuntu EFI loader", "type": "efi",
        "enabled": True, "uuid": device["uuid"], "loader": "/EFI/ubuntu/shimx64.efi",
    }]})
    window.dispatch({"action": "create_backup", "kind": "all"})
    manager = Adw.StyleManager.get_default()
    captures = []

    def capture(name):
        wait(350)
        surface = window.get_surface()
        picture = ImageGrab.grab(xdisplay=args.display)
        picture.crop((0, 0, surface.get_width(), surface.get_height())).save(args.output / name)
        captures.append(name)

    for theme, scheme in (("light", Adw.ColorScheme.FORCE_LIGHT), ("dark", Adw.ColorScheme.FORCE_DARK)):
        manager.set_color_scheme(scheme)
        for size, dimensions in (("", (1120, 740)), ("-small", (900, 640))):
            window.set_default_size(*dimensions)
            wait()
            for key in window.pages:
                window.navigate(key)
                window.pages[key].scroll.get_vadjustment().set_value(0)
                if key == "repair":
                    window.inspect_repair()
                capture(f"gtk-{theme}-{key}{size}.png")
            window.navigate("appearance")
            adjustment = window.pages["appearance"].scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_upper())
            capture(f"gtk-{theme}-appearance-background{size}.png")
        window.navigate("custom")
        window.edit_custom(True)
        wait()
        ImageGrab.grab(xdisplay=args.display).save(args.output / f"gtk-{theme}-entry-editor.png")
        captures.append(f"gtk-{theme}-entry-editor.png")
        window.last_editor["window"].destroy()
        window.navigate("repair")
        window.inspect_repair()
        window.confirm_handler = None
        window.execute_repair()
        wait()
        capture(f"gtk-{theme}-repair-confirmation.png")
        window.last_dialog.close()
        window.confirm_handler = lambda *_args: True
    window.dispose_window()
    if errors:
        raise RuntimeError("; ".join(errors))
    print(json.dumps({"directory": str(args.output), "captures": captures}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
