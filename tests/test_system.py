"""Exercise boot transactions against temporary files and a fake firmware tool."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from uoo.core import DEMO_CONFIG
from uoo.system import Manager, Paths, demo_snapshot, snapshot


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = Paths(defaults=root / "grub", dropins=root / "grub.d",
                           config=root / "grub.cfg", state=root / "state", efi=root / "efi",
                           update=root / "update-grub", efibootmgr=root / "efibootmgr",
                           script_check=root / "grub-script-check", scripts=root / "scripts",
                           lsblk=root / "missing-lsblk", findmnt=root / "missing-findmnt",
                           editenv=root / "missing-editenv", grub_install=root / "missing-grub-install")
        self.paths.defaults.write_text('GRUB_DEFAULT=0\nGRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX="quiet splash"\n')
        self.paths.config.write_text(DEMO_CONFIG)
        self.paths.config.chmod(0o600)
        self.paths.dropins.mkdir()
        self.paths.efi.mkdir()
        self.paths.update.touch()
        self.paths.efibootmgr.touch()
        self.paths.script_check.touch()
        self.firmware = {"order": ["0000", "0001"], "next": "", "entries": ["0000", "0001"]}
        self.calls = []
        self.update_failure = False
        self.firmware_failure = False
        self.replace_menu = None
        self.manager = Manager(self.paths, self.runner)

    def runner(self, args, timeout=120):
        self.calls.append([str(arg) for arg in args])
        if args[0] == self.paths.update:
            if self.update_failure:
                self.paths.config.write_text("broken partial generation")
                raise RuntimeError("fake update failed")
            if self.replace_menu:
                self.paths.config.write_text(self.replace_menu)
            return "updated"
        if args[0] == self.paths.script_check:
            return ""
        if args[0] == self.paths.efibootmgr:
            if len(args) > 1 and args[1] != "--verbose":
                if args[1] == "--bootorder":
                    self.firmware["order"] = args[2].split(",")
                elif args[1] == "--delete-bootorder":
                    self.firmware["order"] = []
                elif args[1] == "--bootnext":
                    self.firmware["next"] = args[2]
                elif args[1] == "--delete-bootnext":
                    self.firmware["next"] = ""
                if self.firmware_failure:
                    self.firmware_failure = False
                    raise RuntimeError("fake firmware failed after writing")
            text = "BootCurrent: 0000\nBootOrder: " + ",".join(self.firmware["order"]) + "\n"
            if self.firmware["next"]:
                text += "BootNext: " + self.firmware["next"] + "\n"
            for entry in self.firmware["entries"]:
                text += f"Boot{entry}* OS {entry}\tHD(1,GPT,example)\n"
            return text
        raise AssertionError(f"Unexpected command: {args}")

    def apply(self, **extra):
        return self.manager.dispatch({"action": "apply_grub", "entry": "windows", "timeout": 8, "style": "menu", **extra})

    def test_inspection_does_not_write_or_invoke_update(self):
        data = snapshot(self.paths, self.runner)
        self.assertEqual(data["entries"][0]["title"], "Ubuntu")
        self.assertEqual(data["settings"]["GRUB_TIMEOUT"], "5")
        self.assertFalse(self.paths.state.exists())
        self.assertFalse(self.paths.override.exists())
        self.assertTrue(all(call == [str(self.paths.efibootmgr), "--verbose"] for call in self.calls))

    def test_demo_has_no_system_side_effects(self):
        state = demo_snapshot()
        state["firmware"]["order"].reverse()
        self.assertEqual(demo_snapshot()["firmware"]["order"][0], "0000")
        self.assertTrue(state["demo"])
        self.assertFalse(self.paths.state.exists())

    def test_apply_preserves_unrelated_settings_and_creates_private_backup(self):
        original = self.paths.defaults.read_bytes()
        result = self.apply()
        self.assertTrue(result["ok"])
        self.assertEqual(self.paths.defaults.read_bytes(), original)
        self.assertIn("GRUB_DEFAULT=windows", self.paths.override.read_text())
        self.assertEqual(result["snapshot"]["settings"]["GRUB_TIMEOUT"], "8")
        backup = next((self.paths.state / "backups").glob("*.json"))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(self.manager.store.read_backup(backup.stem)["data"]["files"]["override"]["data"])

    def test_update_failure_restores_both_files_and_permissions(self):
        self.paths.override.write_text("GRUB_TIMEOUT=22\n")
        self.paths.override.chmod(0o640)
        original = self.paths.config.read_bytes()
        self.update_failure = True
        with self.assertRaisesRegex(RuntimeError, "previous configuration restored"):
            self.apply()
        self.assertEqual(self.paths.config.read_bytes(), original)
        self.assertEqual(self.paths.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.paths.override.read_text(), "GRUB_TIMEOUT=22\n")
        self.assertEqual(self.paths.override.stat().st_mode & 0o777, 0o640)

    def test_first_apply_failure_removes_new_override(self):
        self.update_failure = True
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertFalse(self.paths.override.exists())

    def test_entry_changed_during_regeneration_rolls_back(self):
        original = self.paths.config.read_bytes()
        self.replace_menu = "menuentry 'Ubuntu' --id ubuntu {\n}\n"
        with self.assertRaisesRegex(RuntimeError, "selected boot entry changed"):
            self.apply()
        self.assertEqual(self.paths.config.read_bytes(), original)
        self.assertFalse(self.paths.override.exists())

    def test_unknown_grub_entry_rejected_before_any_write(self):
        with self.assertRaisesRegex(ValueError, "no longer present"):
            self.apply(entry="does-not-exist")
        self.assertFalse(self.paths.override.exists())
        self.assertFalse((self.paths.state / "backups").exists())

    def test_later_override_conflict_rejected(self):
        (self.paths.dropins / "zzz-other.cfg").write_text("GRUB_TIMEOUT=$(printf 20)\n")
        with self.assertRaisesRegex(RuntimeError, "overrides Uoo"):
            self.apply()
        self.assertFalse(self.paths.override.exists())

    def test_symlink_override_rejected_without_touching_target(self):
        target = self.paths.state.parent / "target"
        target.write_text("unchanged")
        self.paths.override.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "Symbolic links|regular file"):
            self.apply()
        self.assertEqual(target.read_text(), "unchanged")

    def test_restore_grub_removes_first_override_and_backs_up_current(self):
        result = self.apply()
        first = result["backups"][0]["id"]
        restored = self.manager.dispatch({"action": "restore", "backup": first})
        self.assertFalse(self.paths.override.exists())
        self.assertEqual(restored["snapshot"]["settings"]["GRUB_TIMEOUT"], "5")
        self.assertEqual(len(restored["backups"]), 2)

    def test_restore_existing_override_preserves_previous_permissions(self):
        self.paths.override.write_text("GRUB_TIMEOUT=22\n")
        self.paths.override.chmod(0o640)
        result = self.apply()
        self.manager.dispatch({"action": "restore", "backup": result["backups"][0]["id"]})
        self.assertEqual(self.paths.override.read_text(), "GRUB_TIMEOUT=22\n")
        self.assertEqual(self.paths.override.stat().st_mode & 0o777, 0o640)

    def test_reorder_keeps_all_current_entries(self):
        result = self.manager.dispatch({"action": "apply_order", "order": ["0001", "0000"]})
        self.assertEqual(result["snapshot"]["firmware"]["order"], ["0001", "0000"])
        self.assertEqual(result["backups"][0]["kind"], "uefi_order")
        for order in (["0001"], ["0000", "0000"], ["0001", "FFFF"], ["0001", "0000", "0002"]):
            with self.subTest(order=order), self.assertRaises(ValueError):
                self.manager.dispatch({"action": "apply_order", "order": order})

    def test_firmware_partial_failure_restores_previous_order(self):
        self.firmware_failure = True
        with self.assertRaisesRegex(RuntimeError, "previous setting restored"):
            self.manager.dispatch({"action": "apply_order", "order": ["0001", "0000"]})
        self.assertEqual(self.firmware["order"], ["0000", "0001"])

    def test_next_boot_keeps_order_and_can_be_cleared(self):
        self.manager.dispatch({"action": "next_boot", "entry": "0001"})
        self.assertEqual(self.firmware["next"], "0001")
        self.assertEqual(self.firmware["order"], ["0000", "0001"])
        self.manager.dispatch({"action": "next_boot", "entry": ""})
        self.assertEqual(self.firmware["next"], "")

    def test_restore_next_boot_and_order_restore_does_not_resurrect_next_boot(self):
        self.firmware["next"] = "0000"
        self.manager.dispatch({"action": "next_boot", "entry": "0001"})
        backup = self.manager.list_backups()[0]["id"]
        self.manager.dispatch({"action": "restore", "backup": backup})
        self.assertEqual(self.firmware["next"], "0000")
        result = self.manager.dispatch({"action": "apply_order", "order": ["0001", "0000"]})
        backup = next(item["id"] for item in result["backups"] if item["kind"] == "uefi_order")
        self.firmware["next"] = ""
        self.manager.dispatch({"action": "restore", "backup": backup})
        self.assertEqual(self.firmware["next"], "")

    def test_restore_firmware_keeps_new_entries(self):
        result = self.manager.dispatch({"action": "apply_order", "order": ["0001", "0000"]})
        backup = result["backups"][0]["id"]
        self.firmware["entries"].append("0002")
        self.firmware["order"].append("0002")
        self.manager.dispatch({"action": "restore", "backup": backup})
        self.assertEqual(self.firmware["order"], ["0000", "0001", "0002"])

    def test_restore_refuses_missing_firmware_entry(self):
        result = self.manager.dispatch({"action": "apply_order", "order": ["0001", "0000"]})
        backup = result["backups"][0]["id"]
        self.firmware["entries"].remove("0000")
        self.firmware["order"].remove("0000")
        with self.assertRaisesRegex(ValueError, "no longer present"):
            self.manager.dispatch({"action": "restore", "backup": backup})

    def test_invalid_backup_path_and_actions_rejected(self):
        for backup in ("../../etc/default/grub", "arbitrary", None):
            with self.subTest(backup=backup), self.assertRaises(ValueError):
                self.manager.dispatch({"action": "restore", "backup": backup})
        for request in ({"action": "remove_entry"}, {"action": []}, [], None):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.manager.dispatch(request)

    def test_world_writable_backup_directory_rejected(self):
        self.paths.state.mkdir(mode=0o777)
        os.chmod(self.paths.state, 0o777)
        with self.assertRaisesRegex(RuntimeError, "not writable"):
            self.apply()

    def test_non_uefi_machine_disables_firmware_without_commands(self):
        self.paths.efi.rmdir()
        result = snapshot(self.paths, self.runner)
        self.assertFalse(result["firmware"]["available"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
