# PyInstaller spec: GTK4 version selection is required for GI's dynamic imports.
from pathlib import Path

source = Path(SPECPATH).parent
a = Analysis(
    [str(source / "packaging/launch.py")],
    pathex=[str(source)],
    datas=[(str(source / "uoo/icons"), "uoo/icons")],
    hiddenimports=["gi.repository.Gtk", "gi.repository.Gdk", "gi.repository.Adw"],
    hooksconfig={"gi": {"module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Adw": "1"},
                        "icons": ["Adwaita"], "themes": ["Adwaita"]}},
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="uoo-boot-manager", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="uoo-boot-manager")
