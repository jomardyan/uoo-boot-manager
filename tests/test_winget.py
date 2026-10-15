"""WinGet metadata must describe the real installer and reject unverified releases."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("winget_builder", ROOT / "scripts/build-winget.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WingetTests(unittest.TestCase):
    def release(self):
        base = f"https://github.com/{builder.REPO}/releases/download/v2.2.0/"
        names = ["SHA256SUMS-Windows"] + [f"Uoo-Boot-Manager-2.2.0-windows-{arch}-setup.exe" for arch in ("x64", "arm64")]
        assets = [{"name": name, "browser_download_url": base + name, "state": "uploaded", "size": 7} for name in names]
        digest = hashlib.sha256(b"payload").hexdigest()
        checksums = "".join(digest + "  " + name + "\n" for name in names[1:]).encode()
        return {"tag_name": "v2.2.0", "draft": False, "prerelease": False, "assets": assets}, checksums, digest

    def test_hashes_are_verified_for_both_native_installers(self):
        release, checksums, digest = self.release()
        with patch.object(builder, "fetch", side_effect=[json.dumps(release).encode(), checksums]), patch.object(builder, "installer_hash", return_value=digest.upper()) as hash_file:
            installers = builder.verified_installers("2.2.0")
        self.assertEqual([item["Architecture"] for item in installers], ["x64", "arm64"])
        self.assertEqual(hash_file.call_count, 2)

    def test_unpublished_or_mismatched_release_is_rejected_before_installers(self):
        for changes in ({"draft": True}, {"prerelease": True}, {"tag_name": "v2.1.2"}):
            release, _, _ = self.release()
            release.update(changes)
            with self.subTest(changes=changes), patch.object(builder, "fetch", return_value=json.dumps(release).encode()), patch.object(builder, "installer_hash") as download:
                with self.assertRaisesRegex(ValueError, "published stable"):
                    builder.verified_installers("2.2.0")
                download.assert_not_called()

    def test_bad_hash_and_unexpected_installer_url_are_rejected(self):
        release, checksums, _ = self.release()
        with patch.object(builder, "fetch", side_effect=[json.dumps(release).encode(), checksums]), patch.object(builder, "installer_hash", return_value="F" * 64):
            with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
                builder.verified_installers("2.2.0")
        release["assets"][1]["browser_download_url"] = "https://example.com/other.exe"
        with patch.object(builder, "fetch", side_effect=[json.dumps(release).encode(), checksums]), patch.object(builder, "installer_hash") as download:
            with self.assertRaisesRegex(ValueError, "unexpected release asset"):
                builder.verified_installers("2.2.0")
            download.assert_not_called()

    def test_registration_and_requirements_match_inno_setup(self):
        installer = builder.documents("2.2.0", [])["installer"]
        inno = (ROOT / "windows/packaging/uoo.iss").read_text()
        self.assertIn("AppId={" + builder.PRODUCT_CODE.removesuffix("_is1"), inno)
        self.assertIn("MinVersion=10.0.22000", inno)
        self.assertIn("PrivilegesRequired=admin", inno)
        self.assertEqual(installer["InstallerType"], "inno")
        self.assertEqual(installer["Scope"], "machine")
        self.assertEqual(installer["MinimumOSVersion"], "10.0.22000.0")

    def test_existing_version_is_preserved_and_changed_metadata_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = builder.write_manifests(root, "2.2.0", [])
            self.assertEqual(builder.write_manifests(root, "2.2.0", []), directory)
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                builder.write_manifests(root, "2.2.0", [{"Architecture": "x64"}])
            self.assertEqual(len(list(directory.glob("*.yaml"))), 3)


if __name__ == "__main__":
    unittest.main()
