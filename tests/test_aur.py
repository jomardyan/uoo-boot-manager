"""Verify AUR metadata rendering from a release checksum manifest."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aur_builder", ROOT / "scripts/build-aur.py")
aur = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aur)
DIGEST = "ab" * 32


class AurTests(unittest.TestCase):
    def test_render_writes_matching_version_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "SHA256SUMS"
            manifest.write_text(f"{DIGEST}  uoo-boot-manager_2.2.1_all.deb\n{'cd' * 32}  other.snap\n")
            output = Path(directory) / "aur"
            aur.render("2.2.1", aur.deb_sha256(manifest, "2.2.1"), output)
            for name in ("PKGBUILD", ".SRCINFO"):
                text = (output / name).read_text()
                self.assertIn("2.2.1", text)
                self.assertIn(DIGEST, text)
                self.assertNotIn("@VERSION@", text)
                self.assertNotIn("@SHA256@", text)

    def test_missing_or_duplicate_deb_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "SHA256SUMS"
            manifest.write_text(f"{DIGEST}  other.snap\n")
            with self.assertRaises(ValueError):
                aur.deb_sha256(manifest, "2.2.1")
            manifest.write_text((f"{DIGEST}  uoo-boot-manager_2.2.1_all.deb\n") * 2)
            with self.assertRaises(ValueError):
                aur.deb_sha256(manifest, "2.2.1")

    def test_version_must_be_stable_semver(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                aur.render("2.2", DIGEST, Path(directory))


if __name__ == "__main__":
    unittest.main()
