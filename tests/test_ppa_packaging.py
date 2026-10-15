"""Verify source-package staging preserves the reviewed privileged payload."""

import importlib.util
import io
from datetime import date
from pathlib import Path
import shutil
import subprocess
import tempfile
import tarfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ppa_payload_check", ROOT / "scripts/check-package.py")
payload_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload_check)
builder_spec = importlib.util.spec_from_file_location("ppa_source_builder", ROOT / "scripts/build-ppa-source.py")
source_builder = importlib.util.module_from_spec(builder_spec)
builder_spec.loader.exec_module(source_builder)


class SourceSeriesTests(unittest.TestCase):
    def test_release_discovery_filters_older_expired_and_unreleased_versions(self):
        data = (
            "version,codename,series,created,release,eol\n"
            "22.04 LTS,Jammy,jammy,2021-01-01,2022-04-21,2027-06-01\n"
            "24.04 LTS,Noble,noble,2023-01-01,2024-04-25,2029-05-31\n"
            "24.10,Oracular,oracular,2024-01-01,2024-10-10,2025-07-10\n"
            "26.04 LTS,Resolute,resolute,2025-01-01,2026-04-23,2031-05-29\n"
            "26.10,Stonking,stonking,2026-01-01,2026-10-15,2027-07-15\n"
            "27.04,Future,future,2026-10-16,2027-04-22,2028-01-20\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ubuntu.csv"
            path.write_text(data)
            self.assertEqual(
                source_builder.supported_series(path, date(2026, 10, 16)),
                {"noble": "24.04", "resolute": "26.04", "stonking": "26.10"},
            )

    def test_new_revision_preserves_the_target_and_does_not_reuse_upload_version(self):
        version, suite = source_builder.target_version("2.2.0-0ppa1~noble1", "noble", "noble", 2)
        self.assertEqual((version, suite), ("2.2.0-0ppa1~noble2", "noble"))
        with self.assertRaisesRegex(ValueError, "positive"):
            source_builder.target_version(version, suite, suite, 0)

    def test_resolute_has_a_distinct_version_without_changing_upstream(self):
        original = "2.2.0-0ppa1~noble1"
        version, suite = source_builder.target_version(original, "noble", "resolute")
        self.assertEqual(suite, "resolute")
        self.assertNotEqual(version, original)
        self.assertEqual(version.rsplit("-", 1)[0], original.rsplit("-", 1)[0])
        self.assertTrue(version.endswith("~resolute1"))

    def test_reused_upstream_rejects_traversal_links_and_packaging_files(self):
        for name, kind in (
            ("uoo-boot-manager-2.2.0/../../escape", tarfile.REGTYPE),
            ("uoo-boot-manager-2.2.0/link", tarfile.SYMTYPE),
            ("uoo-boot-manager-2.2.0/debian/postinst", tarfile.REGTYPE),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                archive = base / "upstream.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    if kind == tarfile.SYMTYPE:
                        member.linkname = "../../outside"
                    else:
                        member.size = 4
                    tar.addfile(member, io.BytesIO(b"data") if member.isfile() else None)
                destination = base / "extracted"
                destination.mkdir()
                with self.assertRaisesRegex(ValueError, "Unexpected upstream"):
                    source_builder.unpack_upstream(archive, destination, "2.2.0")
                self.assertEqual(list(destination.iterdir()), [])


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
