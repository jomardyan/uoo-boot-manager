"""Private-state integrity and durable replacement, using temporary files only."""

import base64
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from uoo.storage import (MAX_RECORD, Store, atomic_write, file_state,
                         read_regular, restore_file, sealed, unseal)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "state")

    def backup(self):
        with self.store.lock():
            return self.store.backup("grub", "Test configuration", {"settings": {"GRUB_TIMEOUT": 5}})

    def test_atomic_replace_and_permissions(self):
        path = self.root / "managed"
        path.write_bytes(b"before")
        atomic_write(path, b"after", 0o600)
        self.assertEqual(path.read_bytes(), b"after")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".uoo-*")), [])

    def test_atomic_replace_refuses_symlinks(self):
        target = self.root / "target"
        target.write_bytes(b"untouched")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaises(RuntimeError):
            atomic_write(link, b"changed")
        self.assertEqual(target.read_bytes(), b"untouched")
        self.assertTrue(link.is_symlink())

    def test_regular_reads_are_bounded_and_nonblocking(self):
        path = self.root / "file"
        path.write_bytes(b"12345")
        with self.assertRaises(RuntimeError):
            read_regular(path, maximum=4)
        self.assertEqual(read_regular(path, maximum=5), b"12345")
        self.assertIsNone(read_regular(self.root / "missing"))
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(RuntimeError):
            read_regular(fifo)
        with self.assertRaises(RuntimeError):
            read_regular(self.root)

    def test_read_refuses_symbolic_links_and_public_private_files(self):
        path = self.root / "file"
        path.write_bytes(b"private")
        path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            read_regular(path, private=True)
        path.chmod(0o600)
        self.assertEqual(read_regular(path, private=True), b"private")
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaises(RuntimeError):
            read_regular(link)

    def test_file_state_preserves_missing_and_nondefault_modes(self):
        path = self.root / "script"
        self.assertEqual(file_state(path), {"data": None, "mode": 0o644})
        path.write_bytes(b"#!/bin/sh\necho hello\n")
        path.chmod(0o750)
        saved = file_state(path)
        atomic_write(path, b"replacement", 0o600)
        restore_file(path, saved)
        self.assertEqual(path.read_bytes(), b"#!/bin/sh\necho hello\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o750)
        restore_file(path, {"data": None, "mode": 0o644})
        self.assertFalse(path.exists())

    def test_invalid_snapshot_refused_without_changes(self):
        path = self.root / "file"
        path.write_bytes(b"original")
        for saved in (None, {}, {"data": None}, {"data": None, "mode": True},
                      {"data": None, "mode": 0o1000}, {"data": None, "mode": -1},
                      {"data": "not-base64!", "mode": 0o600}, {"data": 123, "mode": 0o600}):
            with self.subTest(saved=saved), self.assertRaises((RuntimeError, ValueError)):
                restore_file(path, saved)
            self.assertEqual(path.read_bytes(), b"original")

    def test_missing_snapshot_refuses_symlink_removal(self):
        target = self.root / "target"
        target.write_bytes(b"keep")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaises(RuntimeError):
            restore_file(link, {"data": None, "mode": 0o644})
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_bytes(), b"keep")

    def test_sealed_records_detect_mutation_and_invalid_envelopes(self):
        record = {"unicode": "Відновлення", "data": {"a": 1}}
        encoded = sealed(record)
        self.assertEqual(unseal(encoded), record)
        envelope = json.loads(encoded)
        envelope["record"]["data"]["a"] = 2
        for raw in (json.dumps(envelope).encode(), b"not json", b"null", b"[]", b"123", b'{}'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                unseal(raw)

    def test_sealed_size_limit_applies_to_the_stored_envelope(self):
        # Readers cap the file itself, so a record that only fits before wrapping must be refused.
        record = {"data": "x" * (MAX_RECORD - 80)}
        with self.assertRaisesRegex(RuntimeError, "too large"):
            sealed(record)
        encoded = sealed({"data": "x"})
        self.assertNotIn(b" ", encoded)

    def test_backup_private_permissions_read_and_delete_integrity(self):
        identifier = self.backup()
        path = self.store.directory / "backups" / (identifier + ".json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        record = self.store.read_backup(identifier)
        self.assertEqual(record["id"], identifier)
        self.assertEqual(record["version"], 2)
        self.assertEqual(self.store.list_backups()[0]["summary"], "Test configuration")
        self.store.delete_backup(identifier)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            self.store.read_backup(identifier)

    def test_corrupt_backup_is_listed_refuses_restoration_and_can_be_deleted(self):
        identifier = self.backup()
        path = self.store.directory / "backups" / (identifier + ".json")
        parsed = json.loads(path.read_bytes())
        parsed["record"]["summary"] = "tampered"
        path.write_text(json.dumps(parsed))
        with self.assertRaises(RuntimeError):
            self.store.read_backup(identifier)
        self.assertTrue(path.exists())
        self.assertEqual(self.store.list_backups()[0]["kind"], "corrupt")
        self.store.delete_backup(identifier)
        self.assertFalse(path.exists())
        self.assertEqual(self.store.list_backups(), [])

    def test_private_backup_mode_checked_before_parse_or_delete(self):
        identifier = self.backup()
        path = self.store.directory / "backups" / (identifier + ".json")
        path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            self.store.read_backup(identifier)
        with self.assertRaises(RuntimeError):
            self.store.delete_backup(identifier)
        self.assertTrue(path.exists())
        self.assertEqual(self.store.list_backups()[0]["kind"], "corrupt")

    def test_backup_symbolic_link_refused(self):
        identifier = self.backup()
        path = self.store.directory / "backups" / (identifier + ".json")
        copy_path = self.root / "copy"
        copy_path.write_bytes(path.read_bytes())
        copy_path.chmod(0o600)
        path.unlink()
        path.symlink_to(copy_path)
        with self.assertRaises(RuntimeError):
            self.store.delete_backup(identifier)
        self.assertTrue(path.is_symlink())
        self.assertTrue(copy_path.exists())

    def test_backup_json_scalars_report_corrupt(self):
        identifier = self.backup()
        path = self.store.directory / "backups" / (identifier + ".json")
        for raw in (b"null", b"123", b"true", b'"text"', b"[]"):
            path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises((RuntimeError, ValueError)):
                self.store.read_backup(identifier)
            self.assertEqual(self.store.list_backups()[0]["kind"], "corrupt")

    def test_unenveloped_new_version_refused_but_legacy_readable(self):
        identifier = self.backup()
        path = self.store.directory / "backups" / (identifier + ".json")
        record = self.store.read_backup(identifier)
        path.write_text(json.dumps(record))
        with self.assertRaises(RuntimeError):
            self.store.read_backup(identifier)
        record.pop("version")
        path.write_text(json.dumps(record))
        self.assertEqual(self.store.read_backup(identifier)["id"], identifier)

    def test_bad_ids_cannot_escape_backup_directory(self):
        self.backup()
        for identifier in (None, "", "../../etc/passwd", "20261014T120000Z-aaaaaaaaaaaa.json", "bad"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.store.read_backup(identifier)

    def test_unsafe_directory_and_lock_modes_refused(self):
        self.store.directory.mkdir(mode=0o700)
        self.store.directory.chmod(0o777)
        with self.assertRaises(RuntimeError):
            with self.store.lock():
                pass
        self.store.directory.chmod(0o700)
        lock = self.store.directory / "lock"
        lock.write_bytes(b"")
        lock.chmod(0o644)
        with self.assertRaises(RuntimeError):
            with self.store.lock():
                pass

    def test_nested_lock_refuses_concurrent_writer(self):
        with self.store.lock():
            with self.assertRaises(RuntimeError):
                with self.store.lock():
                    pass

    def test_journal_integrity_and_finish(self):
        with self.store.lock():
            saved = {"version": 2, "files": {"override": {"data": base64.b64encode(b"old").decode(), "mode": 0o644}}}
            self.store.journal(saved)
            self.assertEqual(self.store.pending(), saved)
            path = self.store.directory / "pending.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            envelope = json.loads(path.read_text())
            envelope["record"]["files"]["override"]["mode"] = 0o600
            path.write_text(json.dumps(envelope))
            with self.assertRaises(RuntimeError):
                self.store.pending()
            self.store.journal(saved)
            self.store.finish()
            self.assertIsNone(self.store.pending())


if __name__ == "__main__":
    unittest.main()
