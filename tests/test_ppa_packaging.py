"""Verify source-package staging preserves the reviewed privileged payload."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ppa_payload_check", ROOT / "scripts/check-package.py")
payload_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload_check)


@unittest.skipUnless(shutil.which("dpkg-deb"), "Debian package tools required")
class PpaStagingTests(unittest.TestCase):
    def test_staging_preserves_source_bytes_and_helper_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "package"
            subprocess.run([
                "bash", str(ROOT / "scripts/build-deb.sh"), "--stage-only", str(stage),
            ], check=True, capture_output=True)
            expected = payload_check.expected_files(ROOT)
            actual = {p.relative_to(stage).as_posix() for p in (stage / "usr").rglob("*") if p.is_file()}
            self.assertEqual(actual, set(expected))
            for name, (source, mode) in expected.items():
                with self.subTest(path=name):
                    target = stage / name
                    self.assertFalse(target.is_symlink())
                    self.assertEqual(target.read_bytes(), source.read_bytes())
                    self.assertEqual(target.stat().st_mode & 0o7777, mode)

    def test_staging_refuses_existing_directory_without_touching_its_files(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "keep.txt"
            sentinel.write_text("existing content")
            result = subprocess.run([
                "bash", str(ROOT / "scripts/build-deb.sh"), "--stage-only", directory,
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not already exist", result.stderr)
            self.assertEqual(sentinel.read_text(), "existing content")


if __name__ == "__main__":
    unittest.main()
