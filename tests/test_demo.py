"""Behavioral tests for complete, side-effect-free demo workflows."""

import base64
import json
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch
import zlib

from uoo.demo import DemoManager


def png_data(width=1, height=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\0\0\0\xff" * width) * height)) + chunk(b"IEND", b""))


def custom_entry(**changes):
    entry = {"id": "uoo-" + "a" * 32, "title": "Custom Ubuntu", "type": "linux", "enabled": True,
             "uuid": "12345678-1234-1234-abcd-123456789abc", "kernel": "/boot/vmlinuz-6.8.0-generic",
             "initrd": "/boot/initrd.img-6.8.0-generic", "options": "ro quiet"}
    entry.update(changes)
    return entry


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.manager = DemoManager()

    def dispatch(self, action, **fields):
        return self.manager.dispatch({"action": action, **fields})

    def state(self):
        return self.dispatch("snapshot")["snapshot"]

    def assert_refused_without_changes(self, action, **fields):
        before = self.dispatch("snapshot")
        with self.assertRaises(ValueError):
            self.dispatch(action, **fields)
        after = self.dispatch("snapshot")
        self.assertEqual(before, after)

    def test_complete_snapshot_schema(self):
        state = self.state()
        self.assertTrue(state["demo"])
        self.assertEqual(len(state["entries"]), 5)
        self.assertTrue(all(state["capabilities"].values()))
        esp = next(item for item in state["devices"] if item["esp"])
        self.assertEqual((esp["path"], esp["mountpoint"], esp["pkname"], esp["partn"]),
                         ("/dev/sda1", "/boot/efi", "/dev/sda", 1))
        self.assertTrue(state["diagnostics"]["environment_supported"])
        self.assertTrue(state["diagnostics"]["checks"])
        self.assertEqual(state["grub_next"], "")
        self.assertEqual(state["grub_saved"], "ubuntu")
        self.assertFalse(state["background"])

    def test_no_files_platform_commands_or_randomness(self):
        with patch("builtins.open", side_effect=AssertionError("file access")), \
             patch.object(Path, "read_text", side_effect=AssertionError("file read")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("file read")), \
             patch.object(subprocess, "run", side_effect=AssertionError("command")), \
             patch("os.urandom", side_effect=AssertionError("platform randomness")):
            manager = DemoManager()
            manager.dispatch({"action": "apply_grub", "changes": {"GRUB_TIMEOUT": 15}})
            created = manager.dispatch({"action": "create_backup", "kind": "all"})
            manager.dispatch({"action": "export_backup", "backup": created["backup"]})
            manager.dispatch({"action": "repair_plan"})
            manager.dispatch({"action": "repair", "plan": "demo-repair", "confirm": "/boot/efi"})

    def test_responses_cannot_mutate_state_or_backups(self):
        response = self.dispatch("create_backup", kind="all")
        response["snapshot"]["firmware"]["entries"][0]["active"] = False
        response["snapshot"]["devices"][1]["mountpoint"] = "changed"
        response["backups"][0]["summary"] = "changed"
        details = self.dispatch("backup_details", backup=response["backup"])
        details["configuration"]["grub"]["GRUB_TIMEOUT"] = 999
        self.assertTrue(self.state()["firmware"]["entries"][0]["active"])
        self.assertEqual(self.state()["devices"][1]["mountpoint"], "/boot/efi")
        self.assertEqual(self.dispatch("backup_details", backup=response["backup"])["configuration"]["grub"]["GRUB_TIMEOUT"], 10)

    def test_manager_instances_are_independent(self):
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 15})
        other = DemoManager().dispatch({"action": "snapshot"})
        self.assertEqual(other["snapshot"]["settings"]["GRUB_TIMEOUT"], "10")
        self.assertEqual(other["backups"], [])

    def test_unsupported_requests(self):
        for request in (None, [], {}, {"action": []}, {"action": "run_shell"}, {"action": "snapshot", "command": "reboot"}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.manager.dispatch(request)

    def test_legacy_and_advanced_grub_changes_merge(self):
        self.dispatch("apply_grub", entry="windows", timeout=0, style="hidden")
        state = self.dispatch("apply_grub", changes={"GRUB_CMDLINE_LINUX": "iommu=pt", "GRUB_DISABLE_OS_PROBER": False})["snapshot"]
        self.assertEqual(state["settings"]["GRUB_DEFAULT"], "windows")
        self.assertEqual(state["settings"]["GRUB_TIMEOUT"], "0")
        self.assertEqual(state["settings"]["GRUB_TIMEOUT_STYLE"], "hidden")
        self.assertEqual(state["settings"]["GRUB_CMDLINE_LINUX"], "iommu=pt")
        self.assertEqual(state["settings"]["GRUB_DISABLE_OS_PROBER"], "false")
        self.assertTrue(state["managed"])

    def test_invalid_grub_input_has_no_side_effects(self):
        for changes in ({"GRUB_DEFAULT": "missing"}, {"GRUB_TIMEOUT": True}, {"GRUB_DEFAULT": 'ubuntu"; reboot'},
                        {"GRUB_CMDLINE_LINUX": "ro; reboot"}, {"GRUB_UNKNOWN": "value"}):
            with self.subTest(changes=changes):
                self.assert_refused_without_changes("apply_grub", changes=changes)
        self.assert_refused_without_changes("apply_grub", changes={}, entry="ubuntu")

    def test_saved_default_and_next_grub_choice(self):
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "saved", "GRUB_SAVEDEFAULT": True})
        state = self.dispatch("grub_next_boot", entry="windows")["snapshot"]
        self.assertEqual(state["grub_saved"], "ubuntu")
        self.assertEqual(state["settings"]["UOO_SAVED_ENTRY"], "ubuntu")
        self.assertEqual(state["grub_next"], "windows")
        self.assertEqual(state["firmware"]["next"], "")
        self.assertEqual(self.dispatch("grub_next_boot", entry="")["snapshot"]["grub_next"], "")
        self.assert_refused_without_changes("grub_next_boot", entry="missing")

    def test_grub_next_restore_preserves_later_default(self):
        backup = self.dispatch("grub_next_boot", entry="windows")["backups"][0]["id"]
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "advanced>linux-6.8"})
        restored = self.dispatch("restore", backup=backup)["snapshot"]
        self.assertEqual(restored["grub_next"], "")
        self.assertEqual(restored["settings"]["GRUB_DEFAULT"], "advanced>linux-6.8")

    def test_custom_entries_merge_and_disabled_entries_omitted(self):
        entry = custom_entry()
        disabled = custom_entry(id="uoo-" + "b" * 32, title="Disabled", enabled=False)
        request_entries = [entry, disabled]
        response = self.dispatch("custom_entries", entries=request_entries)
        request_entries[0]["title"] = "mutated after request"
        state = response["snapshot"]
        self.assertEqual(len(state["entries"]), 6)
        self.assertEqual(state["entries"][-1]["selector"], entry["id"])
        self.assertEqual(state["entries"][-1]["position"], "4")
        self.assertEqual(state["custom_entries"][0]["title"], "Custom Ubuntu")
        self.assertEqual(len(state["custom_entries"]), 2)
        self.assertEqual(self.dispatch("refresh_grub")["snapshot"]["entries"], state["entries"])

    def test_cannot_remove_current_default_or_next_custom_entry(self):
        entry = custom_entry()
        self.dispatch("custom_entries", entries=[entry])
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": entry["id"]})
        self.assert_refused_without_changes("custom_entries", entries=[])
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "ubuntu"})
        self.dispatch("grub_next_boot", entry=entry["id"])
        self.assert_refused_without_changes("custom_entries", entries=[])

    def test_firmware_subset_order_retains_inventory(self):
        response = self.dispatch("apply_order", order=["0001", "0000"])
        self.assertEqual(response["snapshot"]["firmware"]["order"], ["0001", "0000"])
        self.assertEqual(len(response["snapshot"]["firmware"]["entries"]), 3)
        for order in ([], ["0001"], ["0000", "0000"], ["0000", "ffff"], "0000", [None]):
            with self.subTest(order=order):
                self.assert_refused_without_changes("apply_order", order=order)

    def test_firmware_active_protection(self):
        state = self.dispatch("set_active", entry="0001", active=False)["snapshot"]
        self.assertFalse(next(item for item in state["firmware"]["entries"] if item["id"] == "0001")["active"])
        self.assert_refused_without_changes("next_boot", entry="0001")
        self.assert_refused_without_changes("set_active", entry="0000", active=False)
        self.assert_refused_without_changes("set_active", entry="0001", active="false")
        self.assert_refused_without_changes("set_active", entry="ffff", active=True)
        self.dispatch("set_active", entry="0001", active=True)
        self.dispatch("next_boot", entry="0001")
        self.assert_refused_without_changes("set_active", entry="0001", active=False)
        self.assert_refused_without_changes("delete_uefi", entry="0001", confirm="0001")
        self.dispatch("next_boot", entry="")

    def test_firmware_next_is_independent_and_scoped_restore(self):
        backup = self.dispatch("next_boot", entry="0001")["backups"][0]["id"]
        self.dispatch("apply_order", order=["0002", "0000"])
        restored = self.dispatch("restore", backup=backup)["snapshot"]
        self.assertEqual(restored["firmware"]["next"], "")
        self.assertEqual(restored["firmware"]["order"], ["0002", "0000"])
        self.assertEqual(restored["grub_next"], "")
        self.assert_refused_without_changes("next_boot", entry="ffff")

    def test_create_and_delete_firmware(self):
        state = self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")["snapshot"]
        self.assertEqual(state["firmware"]["entries"][-1], {"id": "0003", "label": "Fedora", "active": True})
        self.dispatch("next_boot", entry="0003")
        self.assert_refused_without_changes("delete_uefi", entry="0003", confirm="wrong")
        self.assert_refused_without_changes("delete_uefi", entry="0003", confirm="0003")
        self.assert_refused_without_changes("delete_uefi", entry="0000", confirm="0000")
        self.dispatch("next_boot", entry="")
        state = self.dispatch("delete_uefi", entry="0003", confirm="0003")["snapshot"]
        self.assertNotIn("0003", state["firmware"]["order"])
        self.assertEqual(state["firmware"]["next"], "")
        self.assertEqual(len(state["firmware"]["entries"]), 3)

    def test_invalid_loader_create(self):
        base = {"label": "Fedora", "disk": "/dev/sda", "partition": 1, "loader": "/EFI/fedora/shimx64.efi"}
        for changes in ({"partition": True}, {"partition": 2}, {"disk": "/dev/sdb"},
                        {"loader": "/EFI/../x.efi"}, {"label": "bad'; reboot"}):
            with self.subTest(changes=changes):
                self.assert_refused_without_changes("create_uefi", **{**base, **changes})

    def test_manual_backup_scopes_and_restore_saves_current(self):
        backup = self.dispatch("create_backup", kind="grub")["backup"]
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 30})
        self.dispatch("next_boot", entry="0002")
        before_count = len(self.dispatch("list_backups")["backups"])
        response = self.dispatch("restore", backup=backup)
        self.assertEqual(len(response["backups"]), before_count + 1)
        self.assertEqual(response["snapshot"]["settings"]["GRUB_TIMEOUT"], "10")
        self.assertEqual(response["snapshot"]["firmware"]["next"], "0002")
        self.assert_refused_without_changes("create_backup", kind="shell")

    def test_firmware_restore_keeps_new_entries(self):
        backup = self.dispatch("create_backup", kind="uefi")["backup"]
        self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")
        self.dispatch("apply_order", order=["0003", "0000"])
        state = self.dispatch("restore", backup=backup)["snapshot"]
        self.assertEqual(state["firmware"]["order"], ["0000", "0001", "0002", "0003"])
        self.assertEqual(len(state["firmware"]["entries"]), 4)

    def test_full_backup_recreates_deleted_local_firmware_entries(self):
        backup = self.dispatch("create_backup", kind="all")["backup"]
        self.dispatch("delete_uefi", entry="0001", confirm="0001")
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 20})
        restored = self.dispatch("restore", backup=backup)["snapshot"]
        self.assertEqual(restored["settings"]["GRUB_TIMEOUT"], "10")
        self.assertEqual(restored["firmware"]["order"], ["0000", "0001", "0002"])
        self.assertEqual(next(entry for entry in restored["firmware"]["entries"] if entry["id"] == "0001")["label"], "Windows Boot Manager")

    def test_created_loader_undo_removes_only_its_id_and_retains_later_addition(self):
        backup = self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")["backups"][0]["id"]
        self.dispatch("create_uefi", label="Debian", disk="/dev/sda", partition=1, loader="/EFI/debian/shimx64.efi")
        response = self.dispatch("restore", backup=backup)
        entries = {entry["id"]: entry for entry in response["snapshot"]["firmware"]["entries"]}
        self.assertNotIn("0003", entries)
        self.assertEqual(entries["0004"]["label"], "Debian")
        self.assertEqual(response["snapshot"]["firmware"]["order"], ["0000", "0001", "0002", "0004"])

    def test_deleted_loader_undo_recreates_same_id_and_activation_preserving_next(self):
        self.dispatch("set_active", entry="0001", active=False)
        backup = self.dispatch("delete_uefi", entry="0001", confirm="0001")["backups"][0]["id"]
        self.dispatch("next_boot", entry="0002")
        state = self.dispatch("restore", backup=backup)["snapshot"]
        restored = next(entry for entry in state["firmware"]["entries"] if entry["id"] == "0001")
        self.assertEqual(restored, {"id": "0001", "label": "Windows Boot Manager", "active": False})
        self.assertEqual(state["firmware"]["next"], "0002")

    def test_create_undo_protects_scheduled_entry_and_reused_id(self):
        backup = self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")["backups"][0]["id"]
        self.dispatch("next_boot", entry="0003")
        self.assert_refused_without_changes("restore", backup=backup)
        self.dispatch("next_boot", entry="")
        self.dispatch("delete_uefi", entry="0003", confirm="0003")
        self.dispatch("create_uefi", label="Debian", disk="/dev/sda", partition=1, loader="/EFI/debian/shimx64.efi")
        self.assert_refused_without_changes("restore", backup=backup)

    def test_delete_undo_and_full_restore_refuse_reused_boot_number(self):
        manual = self.dispatch("create_backup", kind="uefi")["backup"]
        deletion = self.dispatch("delete_uefi", entry="0001", confirm="0001")["backups"][0]["id"]
        self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")
        self.assert_refused_without_changes("restore", backup=deletion)
        self.assert_refused_without_changes("restore", backup=manual)

    def test_activation_restore_is_scoped_and_protects_scheduled_next(self):
        self.dispatch("set_active", entry="0001", active=False)
        inactive_backup = self.dispatch("set_active", entry="0001", active=True)["backups"][0]["id"]
        self.dispatch("next_boot", entry="0001")
        self.assert_refused_without_changes("restore", backup=inactive_backup)
        self.dispatch("next_boot", entry="")
        self.dispatch("apply_order", order=["0002", "0000"])
        state = self.dispatch("restore", backup=inactive_backup)["snapshot"]
        self.assertEqual(state["firmware"]["order"], ["0002", "0000"])
        self.assertFalse(next(entry for entry in state["firmware"]["entries"] if entry["id"] == "0001")["active"])

    def test_repeated_next_clear_does_not_add_redundant_backups(self):
        self.dispatch("next_boot", entry="")
        self.dispatch("next_boot", entry="")
        self.assertEqual(self.dispatch("list_backups")["backups"], [])
        self.dispatch("next_boot", entry="0001")
        self.dispatch("next_boot", entry="")
        self.dispatch("next_boot", entry="")
        self.assertEqual(len(self.dispatch("list_backups")["backups"]), 2)

    def test_backup_details_export_import_and_delete(self):
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 25})
        backup = self.dispatch("create_backup", kind="all")["backup"]
        details = self.dispatch("backup_details", backup=backup)
        self.assertEqual(details["backup"]["id"], backup)
        self.assertIs(type(details["configuration"]["grub"]["GRUB_TIMEOUT"]), int)
        envelope = json.loads(self.dispatch("export_backup", backup=backup)["data"])
        self.assertEqual(envelope["format"], "uoo-configuration")
        self.assertEqual(envelope["version"], 1)
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 5})
        self.dispatch("next_boot", entry="0001")
        response = self.dispatch("import_configuration", data=envelope)
        self.assertEqual(response["snapshot"]["settings"]["GRUB_TIMEOUT"], "25")
        self.assertEqual(response["snapshot"]["firmware"]["next"], "0001")
        self.dispatch("delete_backup", backup=backup)
        self.assert_refused_without_changes("restore", backup=backup)
        self.assert_refused_without_changes("backup_details", backup="../../etc/passwd")

    def test_import_can_reference_imported_custom_selector(self):
        entry = custom_entry()
        envelope = {"format": "uoo-configuration", "version": 1,
                    "configuration": {"grub": {"GRUB_DEFAULT": entry["id"]}, "custom_entries": [entry]}}
        self.assertEqual(self.dispatch("import_configuration", data=envelope)["snapshot"]["settings"]["GRUB_DEFAULT"], entry["id"])

    def test_invalid_import_is_atomic(self):
        valid = {"format": "uoo-configuration", "version": 1,
                 "configuration": {"grub": {"GRUB_TIMEOUT": 5}, "custom_entries": []}}
        invalid = [None, {}, {**valid, "version": True}, {**valid, "format": "shell"}, {**valid, "extra": "ignored"},
                   {**valid, "configuration": {"grub": {}, "custom_entries": [], "shell": "reboot"}},
                   {**valid, "configuration": {"grub": {"GRUB_CMDLINE_LINUX": "ro; reboot"}, "custom_entries": []}},
                   {**valid, "configuration": {"grub": {"GRUB_DEFAULT": "missing"}, "custom_entries": []}},
                   {**valid, "configuration": {"grub": {}, "custom_entries": [], "firmware": {"entries": [], "order": ["0001"]}}}]
        for envelope in invalid:
            with self.subTest(envelope=envelope):
                self.assert_refused_without_changes("import_configuration", data=envelope)

    def test_background_validation_restore_and_clear(self):
        initial = self.dispatch("apply_background", png=base64.b64encode(png_data()).decode("ascii"))
        self.assertTrue(initial["snapshot"]["background"])
        self.assertEqual(initial["snapshot"]["background_info"]["width"], 1)
        backup = self.dispatch("clear_background")["backups"][0]["id"]
        self.assertFalse(self.state()["background"])
        self.assertTrue(self.dispatch("restore", backup=backup)["snapshot"]["background"])
        for encoded in (None, "not base64!", base64.b64encode(b"not a png").decode("ascii"),
                        base64.b64encode(png_data()[:-1]).decode("ascii")):
            with self.subTest(encoded=encoded):
                self.assert_refused_without_changes("apply_background", png=encoded)

    def test_repair_plan_and_exact_confirmation(self):
        plan = self.dispatch("repair_plan")["plan"]
        self.assertTrue(plan["supported"])
        self.assertEqual(plan["target"], "/boot/efi")
        self.assert_refused_without_changes("repair", plan=plan["id"], confirm="/dev/sda1")
        self.assert_refused_without_changes("repair", plan="stale", confirm=plan["confirmation"])
        before = self.state()
        response = self.dispatch("repair", plan=plan["id"], confirm=plan["confirmation"])
        self.assertEqual(response["snapshot"], before)
        self.assertEqual(response["backups"][0]["kind"], "all")
        self.assertIn("No restart", response["message"])


if __name__ == "__main__":
    unittest.main()
