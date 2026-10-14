"""Regression checks for release metadata and privileged RPM payload validation."""

import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rpm_check", SOURCE / "scripts/check-rpm.py")
rpm_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpm_check)


class RpmValidationTests(unittest.TestCase):
    def validate(self, alter=None, scripts="", triggers="", requirements=None):
        rows = []
        for name, (source, mode) in rpm_check.deb_check.expected_files(SOURCE).items():
            row = ["/" + name, str(0o100000 | mode), "root", "root",
                   hashlib.sha256(source.read_bytes()).hexdigest(), ""]
            if alter and name == "usr/lib/uoo-boot-manager/helper.py":
                alter(row)
            rows.append("\t".join(row))
        if requirements is None:
            requirements = "\n".join(("python3 >= 3.10", "python3-gobject", "gobject-introspection",
                                      "gtk4 >= 4.6", "libadwaita >= 1.5", "polkit"))
        outputs = [scripts, triggers, "uoo-boot-manager noarch 8", requirements, "\n".join(rows)]
        with patch.object(rpm_check, "query", side_effect=outputs), patch.object(rpm_check.subprocess, "run"):
            return rpm_check.check_package(Path("payload.rpm"))

    def test_accepts_complete_source_matching_payload(self):
        self.assertGreater(self.validate(), 6)

    def test_rejects_user_owned_helper(self):
        with self.assertRaisesRegex(ValueError, "Unsafe RPM ownership"):
            self.validate(lambda row: row.__setitem__(2, "user"))

    def test_rejects_writable_helper(self):
        with self.assertRaisesRegex(ValueError, "Unsafe RPM ownership"):
            self.validate(lambda row: row.__setitem__(1, str(0o100777)))

    def test_rejects_helper_symlink(self):
        with self.assertRaisesRegex(ValueError, "Unsafe RPM ownership"):
            self.validate(lambda row: row.__setitem__(5, "/tmp/helper.py"))

    def test_rejects_changed_helper(self):
        with self.assertRaisesRegex(ValueError, "differ from source"):
            self.validate(lambda row: row.__setitem__(4, "0" * 64))

    def test_rejects_package_scriptlets_and_triggers(self):
        for fields in ({"scripts": "postinstall script"}, {"triggers": "package trigger"}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "scriptlets or triggers"):
                self.validate(**fields)

    def test_rejects_missing_cairo_typelib_runtime_dependency(self):
        requirements = "\n".join(("python3 >= 3.10", "python3-gobject", "gtk4 >= 4.6",
                                  "libadwaita >= 1.5", "polkit"))
        with self.assertRaisesRegex(ValueError, "Missing RPM runtime dependencies: gobject-introspection"):
            self.validate(requirements=requirements)

    def test_rejects_missing_python_or_gtk_runtime_dependency(self):
        requirements = ("python3 >= 3.10", "python3-gobject", "gobject-introspection",
                        "gtk4 >= 4.6", "libadwaita >= 1.5", "polkit")
        for omitted in ("python3 >= 3.10", "gtk4 >= 4.6", "libadwaita >= 1.5"):
            with self.subTest(omitted=omitted), self.assertRaisesRegex(ValueError, "Missing RPM runtime dependencies"):
                self.validate(requirements="\n".join(item for item in requirements if item != omitted))

    def test_rejects_failed_payload_integrity_check(self):
        with patch.object(rpm_check.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "rpmkeys")):
            with self.assertRaises(subprocess.CalledProcessError):
                rpm_check.check_package(Path("corrupted.rpm"))


class ReleaseVersionTests(unittest.TestCase):
    def check_versions(self, control, module):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("scripts", "packaging", "uoo"):
                (root / name).mkdir()
            shutil.copyfile(SOURCE / "scripts/package-version.py", root / "scripts/package-version.py")
            (root / "packaging/control").write_text(f"Version: {control}\n")
            (root / "uoo/__init__.py").write_text(f"__version__ = {module!r}\n")
            return subprocess.run([sys.executable, str(root / "scripts/package-version.py")],
                                  capture_output=True, text=True, timeout=10)

    def test_matching_versions(self):
        result = self.check_versions("2.0.0", "2.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2.0.0")

    def test_rejects_version_mismatch(self):
        result = self.check_versions("2.0.0", "1.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must match", result.stderr)

    def test_rejects_version_that_cannot_be_used_in_all_formats(self):
        result = self.check_versions("2.0.0-rc1", "2.0.0-rc1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("three-part release version", result.stderr)


if __name__ == "__main__":
    unittest.main()
