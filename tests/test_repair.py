"""Repair planning, rollback and disk validation without real block devices."""

import base64
import copy
from contextlib import contextmanager
import importlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import Mock, call, patch
import zlib

from uoo.storage import file_state


# Reuse the fully temporary Paths and fake platform/tool fixture without
# importing its TestCase as a discoverable class into this test module.
_fixtures = importlib.import_module("test_production")


class RepairTests(unittest.TestCase):
    def setUp(self):
        architecture = patch("uoo.repair.platform.machine", return_value="x86_64")
        architecture.start()
        self.addCleanup(architecture.stop)
        self.fixture = _fixtures.ProductionTests("test_inspection_uses_no_mutating_tools_or_state")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manager = self.fixture.manager
        self.paths, self.root = self.fixture.paths, self.fixture.root
        directory = self.root / "signed"
        directory.mkdir()
        self.sources = {
            "/usr/lib/shim/shimx64.efi.signed.latest": directory / "shimx64.efi",
            "/usr/lib/shim/shimx64.efi.signed": directory / "shimx64.efi",
            "/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed": directory / "grubx64.efi",
            "/usr/lib/shim/mmx64.efi": directory / "mmx64.efi",
        }
        for path in set(self.sources.values()):
            path.write_bytes(b"MZsigned-fixture-" + path.name.encode())
            path.chmod(0o644)
        self.source_owner = 0
        self.device_files = {}
        self.block_inodes = set()

    def dispatch(self, action, **fields):
        return self.manager.dispatch({"action": action, **fields})

    @contextmanager
    def signed_sources(self):
        actual_stat = Path.stat
        allowed = set(self.sources.values())

        def source_path(*arguments):
            path = Path(*arguments)
            return self.sources.get(str(path), path)

        def source_stat(path, *arguments, **kwargs):
            info = actual_stat(path, *arguments, **kwargs)
            if path in allowed:
                values = list(info)
                values[4] = self.source_owner
                return os.stat_result(values)
            return info

        with patch("uoo.repair.Path", new=source_path), patch.object(Path, "stat", new=source_stat):
            yield

    def tree(self):
        return {str(path.relative_to(self.paths.config.parent)): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                for path in self.paths.config.parent.rglob("*") if path.is_file() and not path.is_symlink()}

    def configure_bios(self, gpt=False):
        self.paths.efi.rmdir()
        self.paths.bios_modules.mkdir(parents=True)
        for index, entry in enumerate(self.fixture.inventory[1:], 1):
            entry["parttype"] = "21686148-6449-6e6f-744e-656564454649" if gpt and index == 1 else "0x83"
            entry["size"] = 1024 * 1024 if index == 1 else 128 * 1024 * 1024
            directory = self.paths.sys_block / f"sda{index}"
            directory.mkdir(parents=True)
            (directory / "start").write_text(str(2048 * index))
        module = self.paths.config.parent / "i386-pc/normal.mod"
        module.parent.mkdir()
        module.write_bytes(b"original module")
        module.chmod(0o640)
        for device in ("/dev/sda", "/dev/sda1"):
            file = self.root / (Path(device).name + ".disk-fixture")
            file.write_bytes((b"D" if device == "/dev/sda" else b"P") * (2 * 1024 * 1024))
            self.device_files[device] = file
            info = file.stat()
            self.block_inodes.add((info.st_dev, info.st_ino))

    @contextmanager
    def emulated_block_devices(self):
        actual_open, actual_fstat = os.open, os.fstat

        def device_open(path, flags, *arguments, **kwargs):
            return actual_open(self.device_files.get(str(path), path), flags, *arguments, **kwargs)

        def device_fstat(descriptor):
            info = actual_fstat(descriptor)
            if (info.st_dev, info.st_ino) in self.block_inodes:
                values = list(info)
                values[0] = (info.st_mode & 0o7777) | stat.S_IFBLK
                return os.stat_result(values)
            return info

        with patch("uoo.repair.os.open", new=device_open), patch("uoo.repair.os.fstat", new=device_fstat):
            yield

    def test_uefi_plan_is_read_only_even_when_optional_directories_are_missing(self):
        self.paths.dropins.rmdir()
        self.paths.scripts.rmdir()
        before = self.fixture.captured()
        with self.signed_sources():
            plan = self.dispatch("repair_plan")["plan"]
        self.assertTrue(plan["supported"], plan["reason"])
        self.assertEqual(plan["mode"], "uefi")
        self.assertEqual(plan["target"], str(self.paths.efi_directory))
        self.assertFalse(self.paths.dropins.exists())
        self.assertFalse(self.paths.scripts.exists())
        self.assertFalse(self.paths.state.exists())
        self.assertEqual(self.fixture.captured(), before)
        self.assertFalse(any(call[0] in (str(self.paths.update), str(self.paths.grub_install)) for call in self.fixture.calls))

    def test_uefi_plan_requires_root_owned_safe_signed_sources(self):
        for owner, mode in ((1000, 0o644), (0, 0o666)):
            self.source_owner = owner
            source = self.sources["/usr/lib/shim/shimx64.efi.signed.latest"]
            source.chmod(mode)
            with self.signed_sources():
                plan = self.dispatch("repair_plan")["plan"]
            self.assertFalse(plan["supported"])
            self.assertIn("signed", plan["reason"])
        self.assertFalse(self.paths.state.exists())

    def test_uefi_plan_rejects_invalid_executable_and_unsupported_architecture(self):
        self.sources["/usr/lib/shim/mmx64.efi"].write_bytes(b"not executable")
        with self.signed_sources():
            plan = self.dispatch("repair_plan")["plan"]
        self.assertFalse(plan["supported"])
        with patch("uoo.repair.platform.machine", return_value="aarch64"):
            plan = self.dispatch("repair_plan")["plan"]
        self.assertFalse(plan["supported"])
        self.assertIn("x86-64", plan["reason"])

    def test_repair_plan_rejects_readonly_boot_and_efi_targets_before_writes(self):
        for case in ("boot-partition", "boot-mount", "efi-partition", "efi-mount"):
            self.fixture.inventory[2]["ro"] = case == "boot-partition"
            self.fixture.inventory[1]["ro"] = case == "efi-partition"
            self.fixture.boot_mount_options = "ro,relatime" if case == "boot-mount" else "rw"
            self.fixture.efi_mount_options = "ro" if case == "efi-mount" else "rw"
            before = self.fixture.captured()
            with self.subTest(case=case), self.signed_sources():
                plan = self.dispatch("repair_plan")["plan"]
                self.assertFalse(plan["supported"], plan)
                self.assertTrue(plan["reason"])
                self.assertEqual(self.fixture.captured(), before)
                self.assertFalse(self.paths.state.exists())

    def test_repair_rechecks_plan_and_exact_confirmation_before_writes(self):
        before = self.fixture.captured()
        with self.signed_sources():
            plan = self.dispatch("repair_plan")["plan"]
            with self.assertRaisesRegex(ValueError, "exact target"):
                self.dispatch("repair", plan=plan["id"], confirm="/wrong")
            self.fixture.inventory[1]["uuid"] = "FFFF-AAAA"
            with self.assertRaisesRegex(ValueError, "current repair plan"):
                self.dispatch("repair", plan=plan["id"], confirm=plan["confirmation"])
        self.assertEqual(self.fixture.captured(), before)
        self.assertFalse((self.paths.state / "backups").exists())

    def test_uefi_repair_reinstalls_approved_sources_and_checks_generated_menu(self):
        firmware = copy.deepcopy(self.fixture.firmware)
        with self.signed_sources():
            plan = self.dispatch("repair_plan")["plan"]
            response = self.dispatch("repair", plan=plan["id"], confirm=plan["confirmation"])
        targets = self.manager.file_paths()
        for key in ("efi_shim", "efi_grub", "efi_mm"):
            self.assertEqual(targets[key].read_bytes(), Path(plan["sources"][key]).read_bytes())
        self.assertIn("search.fs_uuid " + _fixtures.ROOT_UUID, targets["efi_config"].read_text())
        self.assertIn("configfile $prefix/grub.cfg", targets["efi_config"].read_text())
        self.assertEqual(self.fixture.firmware, firmware)
        self.assertEqual(response["backups"][0]["kind"], "uefi_repair")
        self.assertIn([str(self.paths.script_check), str(self.paths.config)], self.fixture.calls)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_uefi_generation_failure_restores_every_loader_and_menu(self):
        before = self.fixture.captured()
        self.fixture.fail_update = True
        with self.signed_sources():
            plan = self.dispatch("repair_plan")["plan"]
            with self.assertRaisesRegex(RuntimeError, "previous configuration restored"):
                self.dispatch("repair", plan=plan["id"], confirm=plan["confirmation"])
        self.assertEqual(self.fixture.captured(), before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_uefi_syntax_failure_rolls_back_new_mok_and_stub_files(self):
        before = self.fixture.captured()
        self.fixture.fail_script_check = True
        with self.signed_sources():
            plan = self.dispatch("repair_plan")["plan"]
            with self.assertRaises(RuntimeError):
                self.dispatch("repair", plan=plan["id"], confirm=plan["confirmation"])
        self.assertEqual(self.fixture.captured(), before)
        self.assertFalse(self.manager.file_paths()["efi_mm"].exists())
        self.assertFalse(self.manager.file_paths()["efi_config"].exists())

    def test_uefi_pending_file_journal_restores_partial_loader_writes(self):
        paths = self.manager.file_paths()
        before = self.fixture.captured()
        keys = ("efi_shim", "efi_grub", "efi_mm", "efi_config", "generated")
        with self.manager.lock():
            self.manager.store.journal({"version": 2, "files": {key: before[key] for key in keys}})
        for key in keys:
            paths[key].write_bytes(b"partial")
        with self.manager.lock():
            self.manager.recover()
        self.assertEqual(self.fixture.captured(), before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_boot_tree_capture_restore_preserves_modes_and_removes_new_modules(self):
        self.configure_bios()
        original = self.tree()
        captured = self.manager.capture_boot_tree()
        self.paths.config.write_text("partial menu")
        (self.paths.config.parent / "i386-pc/normal.mod").write_bytes(b"new module")
        (self.paths.config.parent / "i386-pc/new.mod").write_bytes(b"new extra module")
        self.manager.restore_boot_tree(captured)
        self.assertEqual(self.tree(), original)

    def test_boot_tree_capture_refuses_symlinks_special_files_and_oversized_files(self):
        self.configure_bios()
        root = self.paths.config.parent
        target = self.root / "outside"
        target.write_bytes(b"outside")
        link = root / "link"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            self.manager.capture_boot_tree()
        link.unlink()
        fifo = root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            self.manager.capture_boot_tree()
        fifo.unlink()
        file = root / "oversized"
        with file.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises((ValueError, RuntimeError)):
            self.manager.capture_boot_tree()

    def test_boot_tree_file_count_boundary_is_restorable(self):
        self.configure_bios()
        root = self.paths.config.parent
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink()
        for index in range(2049):
            (root / f"{index}.mod").touch()
        with self.assertRaises(ValueError):
            self.manager.capture_boot_tree()

    def test_invalid_tree_record_does_not_delete_or_replace_any_existing_files(self):
        self.configure_bios()
        original = self.tree()
        valid = {"data": base64.b64encode(zlib.compress(b"replacement")).decode(), "size": 11, "mode": 0o644}
        invalid = [
            {"a.mod": valid, "z.mod": {**valid, "mode": "invalid"}},
            {"a.mod": valid, "z.mod": {**valid, "mode": True}},
            {"a.mod": valid, "z.mod": {**valid, "mode": 0o4777}},
            {"module": valid, "module/sub.mod": valid},
            {"../outside": valid}, {"/absolute": valid}, {"a.mod": valid, "z.mod": None},
            {"a.mod": valid, "z.mod": {**valid, "size": 0}},
            {"a.mod": valid, "z.mod": {**valid, "data": "not-base64!"}},
        ]
        for entries in invalid:
            with self.subTest(entries=entries), self.assertRaises((ValueError, RuntimeError)):
                self.manager.restore_boot_tree(entries)
            self.assertEqual(self.tree(), original)

    def test_compressed_tree_expansion_is_bounded_before_mutation(self):
        self.configure_bios()
        original = self.tree()
        payload = base64.b64encode(zlib.compress(b"\0" * (8 * 1024 * 1024))).decode()
        entries = {f"{index}.mod": {"data": payload, "size": 8 * 1024 * 1024, "mode": 0o644} for index in range(7)}
        with self.assertRaises(ValueError):
            self.manager.restore_boot_tree(entries)
        self.assertEqual(self.tree(), original)

    def test_tree_restore_refuses_existing_symlinks_before_deletion(self):
        self.configure_bios()
        captured = self.manager.capture_boot_tree()
        extra = self.paths.config.parent / "extra.mod"
        extra.write_bytes(b"preserve until validation")
        link = self.paths.config.parent / "link"
        link.symlink_to(self.root / "outside")
        with self.assertRaises(ValueError):
            self.manager.restore_boot_tree(captured)
        self.assertTrue(extra.exists())
        self.assertTrue(link.is_symlink())

    def test_bios_region_layout_limits(self):
        self.configure_bios()
        plan = self.dispatch("repair_plan")["plan"]
        self.assertTrue(plan["supported"], plan["reason"])
        self.assertEqual(plan["regions"], [{"path": "/dev/sda", "length": 1024 * 1024}])
        (self.paths.sys_block / "sda1/start").write_text("0")
        self.assertFalse(self.dispatch("repair_plan")["plan"]["supported"])
        (self.paths.sys_block / "sda1/start").write_text(str(40000))
        (self.paths.sys_block / "sda2/start").write_text(str(50000))
        self.assertFalse(self.dispatch("repair_plan")["plan"]["supported"])

    def test_gpt_bios_regions_capture_restore_and_device_identity(self):
        self.configure_bios(gpt=True)
        plan = self.dispatch("repair_plan")["plan"]
        self.assertEqual(plan["regions"], [{"path": "/dev/sda", "length": 512}, {"path": "/dev/sda1", "length": 1024 * 1024}])
        with self.emulated_block_devices():
            saved = self.manager.capture_bios(plan)
            self.device_files["/dev/sda"].write_bytes(b"X" * (2 * 1024 * 1024))
            self.device_files["/dev/sda1"].write_bytes(b"Y" * (2 * 1024 * 1024))
            self.manager.restore_bios_state(saved)
            self.assertEqual(self.device_files["/dev/sda"].read_bytes()[:512], b"D" * 512)
            self.assertEqual(self.device_files["/dev/sda1"].read_bytes()[:1024 * 1024], b"P" * (1024 * 1024))
            unchanged = self.device_files["/dev/sda"].read_bytes()
            with self.assertRaises(ValueError):
                self.manager.restore_bios_state({**saved, "boot_uuid": "different"})
            self.assertEqual(self.device_files["/dev/sda"].read_bytes(), unchanged)

    def test_invalid_second_sector_record_cannot_write_first_sector(self):
        self.configure_bios(gpt=True)
        plan = self.dispatch("repair_plan")["plan"]
        with self.emulated_block_devices():
            saved = self.manager.capture_bios(plan)
            self.device_files["/dev/sda"].write_bytes(b"X" * (2 * 1024 * 1024))
            before = self.device_files["/dev/sda"].read_bytes()
            for payload in ("invalid-base64!", base64.b64encode(b"short").decode()):
                bad = copy.deepcopy(saved)
                bad["regions"][1]["data"] = payload
                with self.assertRaises((ValueError, RuntimeError)):
                    self.manager.restore_bios_state(bad)
                self.assertEqual(self.device_files["/dev/sda"].read_bytes(), before)

    def test_second_unverified_sector_target_cannot_write_first_sector(self):
        self.configure_bios(gpt=True)
        plan = self.dispatch("repair_plan")["plan"]
        with self.emulated_block_devices():
            saved = self.manager.capture_bios(plan)
        self.device_files["/dev/sda"].write_bytes(b"X" * (2 * 1024 * 1024))
        before = self.device_files["/dev/sda"].read_bytes()
        info = self.device_files["/dev/sda1"].stat()
        self.block_inodes.remove((info.st_dev, info.st_ino))
        with self.emulated_block_devices():
            with self.assertRaisesRegex(ValueError, "block device"):
                self.manager.restore_bios_state(saved)
        self.assertEqual(self.device_files["/dev/sda"].read_bytes(), before)

    def test_sector_restore_completes_short_writes_and_syncs_every_target(self):
        class ShortStream:
            def __init__(self, descriptor, limit):
                self.descriptor, self.limit = descriptor, limit
                self.contents = bytearray()
                self.writes, self.closed = 0, False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.closed = True

            def fileno(self):
                return self.descriptor

            def write(self, data):
                count = min(len(data), self.limit)
                self.contents.extend(data[:count])
                self.writes += 1
                return count

        payloads = [bytes(range(256)) * 2, b"partition bytes\x00\xff" * 64]
        streams = {1001: ShortStream(1001, 37), 1002: ShortStream(1002, 47)}
        self.manager.validate_bios_state = Mock(return_value=[({"path": "/dev/fake1"}, payloads[0]),
                                                            ({"path": "/dev/fake2"}, payloads[1])])
        block_stat = os.stat_result((stat.S_IFBLK | 0o600, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        with patch("uoo.repair.os.open", side_effect=[1001, 1002]), \
             patch("uoo.repair.os.fdopen", side_effect=lambda descriptor, *args, **kwargs: streams[descriptor]), \
             patch("uoo.repair.os.fstat", return_value=block_stat), \
             patch("uoo.repair.os.fsync") as sync:
            self.manager.restore_bios_state({"test": "prevalidated state"})
        for descriptor, expected in zip((1001, 1002), payloads):
            self.assertEqual(bytes(streams[descriptor].contents), expected)
            self.assertGreater(streams[descriptor].writes, 1)
            self.assertTrue(streams[descriptor].closed)
        self.assertEqual(sync.call_args_list, [call(1001), call(1002)])

    def test_sector_restore_zero_progress_fails_without_sync_or_later_target_writes(self):
        streams = [Mock(), Mock()]
        for descriptor, stream in zip((1001, 1002), streams):
            stream.__enter__ = Mock(return_value=stream)
            stream.__exit__ = Mock(return_value=False)
            stream.fileno.return_value = descriptor
        streams[0].write.return_value = 0
        self.manager.validate_bios_state = Mock(return_value=[({"path": "/dev/fake1"}, b"first sector"),
                                                            ({"path": "/dev/fake2"}, b"second sector")])
        block_stat = os.stat_result((stat.S_IFBLK | 0o600, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        with patch("uoo.repair.os.open", side_effect=[1001, 1002]), \
             patch("uoo.repair.os.fdopen", side_effect=streams), \
             patch("uoo.repair.os.fstat", return_value=block_stat), \
             patch("uoo.repair.os.fsync") as sync:
            with self.assertRaisesRegex(OSError, "write did not complete"):
                self.manager.restore_bios_state({"test": "prevalidated state"})
        streams[0].write.assert_called_once()
        streams[1].write.assert_not_called()
        streams[0].__exit__.assert_called_once()
        streams[1].__exit__.assert_called_once()
        sync.assert_not_called()

    def test_nonblock_disk_is_rejected_without_writes(self):
        self.configure_bios()
        plan = self.dispatch("repair_plan")["plan"]
        actual_open = os.open
        with patch("uoo.repair.os.open", side_effect=lambda path, flags, *args, **kwargs:
                   actual_open(self.device_files.get(str(path), path), flags, *args, **kwargs)):
            with self.assertRaisesRegex(ValueError, "block device"):
                self.manager.capture_bios(plan)
        self.assertEqual(self.device_files["/dev/sda"].read_bytes(), b"D" * (2 * 1024 * 1024))

    def test_bios_repair_failure_restores_embedding_sectors_and_module_tree(self):
        self.configure_bios()
        original_tree, original_disk = self.tree(), self.device_files["/dev/sda"].read_bytes()
        original_runner = self.manager.runner

        def failed_install(args, timeout=120):
            if str(args[0]) == str(self.paths.grub_install):
                self.device_files["/dev/sda"].write_bytes(b"X" * (2 * 1024 * 1024))
                (self.paths.config.parent / "i386-pc/normal.mod").write_bytes(b"partial module")
                (self.paths.config.parent / "i386-pc/new.mod").write_bytes(b"new module")
                raise RuntimeError("install failed after partial sector/module writes")
            return original_runner(args, timeout=timeout)

        self.manager.runner = failed_install
        with self.emulated_block_devices():
            plan = self.dispatch("repair_plan")["plan"]
            with self.assertRaisesRegex(RuntimeError, "previous boot sectors and menu were restored"):
                self.dispatch("repair", plan=plan["id"], confirm=plan["confirmation"])
        self.assertEqual(self.tree(), original_tree)
        self.assertEqual(self.device_files["/dev/sda"].read_bytes()[:1024 * 1024], original_disk[:1024 * 1024])
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_bios_pending_journal_recovers_sectors_modules_and_menu(self):
        self.configure_bios()
        original_tree = self.tree()
        with self.emulated_block_devices():
            plan = self.dispatch("repair_plan")["plan"]
            sectors = self.manager.capture_bios(plan)
            tree = self.manager.capture_boot_tree()
            with self.manager.lock():
                self.manager.store.journal({"version": 2, "files": {"generated": file_state(self.paths.config)},
                                            "bios": sectors, "boot_tree": tree})
            self.device_files["/dev/sda"].write_bytes(b"X" * (2 * 1024 * 1024))
            self.paths.config.write_text("partial config")
            (self.paths.config.parent / "i386-pc/new.mod").write_bytes(b"new module")
            with self.manager.lock():
                self.manager.recover()
        self.assertEqual(self.tree(), original_tree)
        self.assertEqual(self.device_files["/dev/sda"].read_bytes()[:1024 * 1024], b"D" * (1024 * 1024))
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_bios_wrong_disk_journal_cannot_restore_current_tree_first(self):
        self.configure_bios()
        with self.emulated_block_devices():
            plan = self.dispatch("repair_plan")["plan"]
            sectors = self.manager.capture_bios(plan)
            tree = self.manager.capture_boot_tree()
            sectors["boot_uuid"] = "wrong-filesystem"
            with self.manager.lock():
                self.manager.store.journal({"version": 2, "files": {}, "bios": sectors, "boot_tree": tree})
            self.paths.config.write_text("current disk menu")
            before = self.tree()
            with self.manager.lock():
                with self.assertRaises((ValueError, RuntimeError)):
                    self.manager.recover()
        self.assertEqual(self.tree(), before)
        self.assertTrue((self.paths.state / "pending.json").exists())

    def test_helper_duplicate_json_fields_refused_at_every_object_depth(self):
        spec = importlib.util.spec_from_file_location("uoo_helper_under_test", Path(__file__).parent.parent / "helper.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        for text in ('{"action":"snapshot","action":"repair"}',
                     '{"action":"apply_grub","changes":{"GRUB_TIMEOUT":5,"GRUB_TIMEOUT":10}}'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "Duplicate JSON field"):
                json.loads(text, object_pairs_hook=helper.request_object)
        parsed = json.loads('{"action":"apply_grub","changes":{"GRUB_TIMEOUT":5}}', object_pairs_hook=helper.request_object)
        self.assertEqual(parsed["changes"]["GRUB_TIMEOUT"], 5)
        self.assertFalse(self.paths.state.exists())

    def test_action_schema_rejects_unknown_fields_and_mixed_settings_before_state_creation(self):
        requests = ({"action": "repair_plan", "target": "/dev/sdb"},
                    {"action": "apply_grub", "changes": {"GRUB_TIMEOUT": 5}, "entry": "windows"},
                    {"action": "snapshot", "paths": {"config": "/other"}},
                    {"action": "repair", "plan": "plan", "confirm": "target", "commands": ["reboot"]})
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.manager.dispatch(request)
        self.assertFalse(self.paths.state.exists())

    def test_package_writer_lock_blocks_transactions_and_is_released_cleanly(self):
        frontend = self.root / "dpkg-lock-frontend"
        package = self.root / "dpkg-lock"
        frontend.touch()
        package.touch()
        process = subprocess.Popen(
            [sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'r+b'); fcntl.lockf(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()", str(package)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(process.stdout.readline(), "locked\n")

            def lock_path(*arguments):
                path = Path(*arguments)
                return {"/etc/default/grub": self.paths.defaults,
                        "/var/lib/dpkg/lock-frontend": frontend, "/var/lib/dpkg/lock": package}.get(str(path), path)

            with patch("uoo.production.Path", new=lock_path):
                with self.assertRaisesRegex(RuntimeError, "package update"):
                    with self.manager.lock():
                        pass
                process.stdin.close()
                process.wait(timeout=5)
                with self.manager.lock():
                    pass
            self.assertFalse((self.paths.state / "pending.json").exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
