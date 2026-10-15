"""Release publishing must preserve CI, complete asset, and checksum gates."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publisher", ROOT / "scripts/publish-release.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
SHA = "a" * 40


class ReleasePublicationTests(unittest.TestCase):
    def run_record(self, identity=1, **changes):
        return dict(id=identity, head_sha=SHA, head_branch="v2.2.0", event="workflow_dispatch",
                    status="completed", conclusion="success", html_url="https://github.com/example/run") | changes

    def test_only_matching_dispatched_tag_builds_count(self):
        for changes in ({"head_sha": "b" * 40}, {"head_branch": "main"}, {"event": "push"}, {"status": "in_progress"}):
            with self.subTest(changes=changes):
                self.assertFalse(publisher.check_workflow([self.run_record(**changes)], SHA, "v2.2.0"))
        self.assertTrue(publisher.check_workflow([self.run_record()], SHA, "v2.2.0"))

    def test_newer_failure_cannot_be_hidden_by_older_success(self):
        with self.assertRaisesRegex(ValueError, "did not succeed"):
            publisher.check_workflow([self.run_record(), self.run_record(2, conclusion="failure")], SHA, "v2.2.0")

    def test_full_release_has_all_platforms_and_architectures(self):
        linux, windows = publisher.expected_packages("2.2.0")
        self.assertEqual(len(linux), 10)
        self.assertEqual(len(windows), 4)
        self.assertIn("uoo-boot-manager-2.2.0-1-any.pkg.tar.zst", linux)
        self.assertIn("Uoo-Boot-Manager-2.2.0-windows-arm64-setup.exe", windows)

    def test_checksum_verification_accepts_complete_matching_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.deb").write_bytes(b"package")
            checksum = hashlib.sha256(b"package").hexdigest()
            (root / "SHA256SUMS").write_text(checksum + "  app.deb\n")
            publisher.verify_manifest(root, "SHA256SUMS", {"app.deb"})
            (root / "app.deb").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                publisher.verify_manifest(root, "SHA256SUMS", {"app.deb"})

    def test_missing_duplicated_and_traversal_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.deb").write_bytes(b"package")
            checksum = hashlib.sha256(b"package").hexdigest()
            for manifest in ("", (checksum + "  app.deb\n") * 2, checksum + "  ../app.deb\n"):
                with self.subTest(manifest=manifest):
                    (root / "SHA256SUMS").write_text(manifest)
                    with self.assertRaises(ValueError):
                        publisher.verify_manifest(root, "SHA256SUMS", {"app.deb"})

    def test_unrelated_tag_never_starts_release_build_polling(self):
        with patch.object(publisher, "api", return_value={"object": {"type": "commit", "sha": "b" * 40}}) as api, patch.object(publisher.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "exact validated commit"):
                publisher.publish("example/repo", "2.2.0", SHA, ROOT / "docs/releases/2.2.0.md")
        self.assertEqual(api.call_count, 1)
        run.assert_not_called()

    def test_incomplete_release_never_downloads_or_publishes(self):
        responses = [{"object": {"type": "commit", "sha": SHA}},
                     {"workflow_runs": [self.run_record()]}, {"workflow_runs": [self.run_record()]},
                     {"draft": True, "assets": [{"name": "app.deb"}]}]
        with patch.object(publisher, "api", side_effect=responses), patch.object(publisher.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Release assets"):
                publisher.publish("example/repo", "2.2.0", SHA, ROOT / "docs/releases/2.2.0.md")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
