"""Exercise source and installed launchers without starting a desktop."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_loads_adjacent_package_from_any_working_directory(self):
        for source_layout in (True, False):
            for isolated in (False, True):
                with self.subTest(source_layout=source_layout, isolated=isolated):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        app = root / "app"
                        package = app / "uoo"
                        package.mkdir(parents=True)
                        (package / "__init__.py").write_text("")
                        (package / "__main__.py").write_text(
                            "def launch():\n"
                            "    print('adjacent package')\n"
                            "    return 17\n"
                        )
                        launcher = app / "packaging" / "launch.py" if source_layout else app / "launch.py"
                        launcher.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(SOURCE / "packaging/launch.py", launcher)
                        # An older package must not override the launcher's own code.
                        stale = root / "stale" / "uoo"
                        stale.mkdir(parents=True)
                        (stale / "__init__.py").write_text("")
                        (stale / "__main__.py").write_text("raise RuntimeError('stale package')\n")
                        env = dict(os.environ, PYTHONPATH=str(stale.parent))
                        command = [sys.executable]
                        if isolated:
                            command.append("-I")
                        result = subprocess.run(
                            [*command, str(launcher)], cwd=root, env=env,
                            capture_output=True, text=True, timeout=10,
                        )
                        self.assertEqual(result.returncode, 17, result.stderr)
                        self.assertEqual(result.stdout.strip(), "adjacent package")


if __name__ == "__main__":
    unittest.main()
