"""Release integrity across native and sandboxed package formats."""

import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


arch = module("check-arch")
checksums = module("release-checksums")


class ReleaseManifestTests(unittest.TestCase):
    def test_all_formats_and_both_architectures_are_checksummed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ("app.deb", "app.rpm", "app.opensuse.rpm", "app-x86_64.AppImage", "app-aarch64.AppImage",
                     "app.pkg.tar.zst", "app-x86_64.flatpak", "app-aarch64.flatpak", "app_amd64.snap", "app_arm64.snap")
            for name in names:
                (root / name).write_bytes(name.encode())
            (root / "notes.txt").write_text("not a package")
            self.assertEqual(checksums.write_checksums(root), len(names))
            original = (root / "SHA256SUMS").read_text()
            self.assertEqual({line.split("  ")[1] for line in original.splitlines()}, set(names))
            checksums.write_checksums(root)
            self.assertEqual((root / "SHA256SUMS").read_text(), original)

    def test_empty_and_symlink_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "No release"):
                checksums.write_checksums(root)
            (root / "data").write_bytes(b"payload")
            (root / "app.deb").symlink_to(root / "data")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                checksums.write_checksums(root)


class ArchArchiveTests(unittest.TestCase):
    def validate(self, missing_dependency=False, script=False, uid=0):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            dependencies = arch.DEPENDENCIES - ({"gobject-introspection-runtime"} if missing_dependency else set())
            metadata = "pkgname = uoo-boot-manager\narch = any\n" + "".join(f"depend = {value}\n" for value in dependencies)
            for name, data, mode in [(".PKGINFO", metadata.encode(), 0o644), *[
                    (name, path.read_bytes(), mode) for name, (path, mode) in arch.deb_check.expected_files(SOURCE).items()]]:
                item = tarfile.TarInfo(name)
                item.mode, item.uid, item.size = mode, uid, len(data)
                archive.addfile(item, io.BytesIO(data))
            if script:
                item = tarfile.TarInfo(".INSTALL")
                archive.addfile(item, io.BytesIO(b""))
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            return arch.validate_archive(archive)

    def test_source_matching_package_is_accepted(self):
        self.assertEqual(self.validate(), len(arch.deb_check.expected_files(SOURCE)))

    def test_missing_typelib_provider_scriptlets_and_unprivileged_ownership_are_rejected(self):
        for fields in ({"missing_dependency": True}, {"script": True}, {"uid": 1000}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.validate(**fields)
