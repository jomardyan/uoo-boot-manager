"""Dual-boot diagnosis and the reviewed one-click fix, on the emulated boot tools from test_production."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from uoo.core import DEMO_CONFIG, parse_settings
from collections import namedtuple

from uoo.dualboot import DualBoot, analyze, failed_plan
from uoo.system import Manager

from tests import test_production

NO_WINDOWS = DEMO_CONFIG.replace("menuentry 'Windows Boot Manager' --id 'windows' {\n    chainloader /EFI/Microsoft/Boot/bootmgfw.efi\n}\n", "")


def base_state(**changes):
    state = {"grub_installed": True, "uefi": True, "secure_boot": "disabled", "grub_has_windows": True,
             "grub_style": "menu", "grub_timeout": 5, "os_prober_disabled": True, "os_prober_installed": True,
             "windows_firmware": [], "windows_loader": None, "ubuntu_loader": None, "ubuntu_entries": [],
             "firmware_available": True, "firmware_order": [], "bitlocker": [], "luks": [], "ntfs": []}
    state.update(changes)
    return state


class AnalysisTests(unittest.TestCase):
    def ids(self, plan):
        return [step["id"] for step in plan["steps"]]

    def test_healthy_dual_boot_has_no_steps(self):
        plan = analyze(base_state(windows_firmware=["0001"], ubuntu_entries=[{"id": "0000", "active": True}],
                                  firmware_order=["0000", "0001"]))
        self.assertEqual(plan["steps"], [])
        self.assertFalse(plan["requires_acknowledgement"])

    def test_without_windows_nothing_is_flagged(self):
        plan = analyze(base_state(grub_has_windows=False, ubuntu_entries=[{"id": "0000", "active": True}], firmware_order=["0000"]))
        self.assertEqual(plan["steps"], [])
        self.assertFalse(any(item["severity"] == "problem" for item in plan["findings"]))

    def test_unsupported_without_grub(self):
        plan = analyze(base_state(grub_installed=False))
        self.assertFalse(plan["supported"])
        self.assertEqual(plan["steps"], [])

    def test_bios_ntfs_volume_suggests_os_prober_only(self):
        plan = analyze(base_state(uefi=False, grub_has_windows=False, ntfs=["/dev/sda1"]))
        self.assertEqual(self.ids(plan), ["os_prober"])
        self.assertTrue(any(item["id"] == "bios_mode" for item in plan["findings"]))

    def test_missing_os_prober_gives_guidance_instead_of_a_step(self):
        plan = analyze(base_state(uefi=False, grub_has_windows=False, ntfs=["/dev/sda1"], os_prober_installed=False))
        self.assertEqual(plan["steps"], [])
        self.assertIn("apt install os-prober", next(item for item in plan["findings"] if item["id"] == "windows_missing_from_grub")["detail"])

    def test_bitlocker_makes_firmware_reordering_optional_and_requires_acknowledgement(self):
        plan = analyze(base_state(windows_firmware=["0001"], ubuntu_entries=[{"id": "0000", "active": True}],
                                  firmware_order=["0001", "0000"], bitlocker=["/dev/nvme0n1p3"]))
        step = plan["steps"][0]
        self.assertEqual(step["id"], "uefi_order")
        self.assertFalse(step["recommended"])
        self.assertTrue(plan["requires_acknowledgement"])
        self.assertEqual(next(item for item in plan["findings"] if item["id"] == "windows_boots_first")["severity"], "warning")

    def test_plan_id_changes_with_the_state(self):
        first = analyze(base_state())
        self.assertNotEqual(first["id"], analyze(base_state(grub_timeout=0, windows_firmware=["0001"]))["id"])
        self.assertEqual(first["id"], analyze(base_state())["id"])

    def finding_ids(self, plan):
        return [item["id"] for item in plan["findings"]]

    def test_read_only_boot_filesystem_blocks_grub_steps_with_an_explanation(self):
        plan = analyze(base_state(windows_firmware=["0001"], grub_has_windows=False, windows_loader={"uuid": "A-B", "device": "/dev/sda1", "loader": "/EFI/x.efi"},
                                  grub_style="hidden", boot_readonly=True))
        self.assertEqual(plan["steps"], [])
        self.assertIn("boot_readonly", self.finding_ids(plan))

    def test_read_only_efivars_blocks_firmware_steps_but_not_grub_steps(self):
        plan = analyze(base_state(windows_firmware=["0001"], ubuntu_entries=[{"id": "0000", "active": True}], firmware_order=["0001", "0000"],
                                  efivars_readonly=True, grub_style="hidden"))
        self.assertEqual(self.ids(plan), ["grub_menu"])
        self.assertIn("efivars_readonly", self.finding_ids(plan))

    def test_later_grub_dropins_that_override_a_setting_prevent_a_useless_step(self):
        plan = analyze(base_state(windows_firmware=["0001"], grub_style="hidden", grub_conflicts={"zzz-local.cfg": ["GRUB_TIMEOUT_STYLE"]}))
        self.assertEqual(plan["steps"], [])
        found = next(item for item in plan["findings"] if item["id"] == "grub_menu_overridden")
        self.assertIn("zzz-local.cfg", found["detail"])
        plan = analyze(base_state(windows_firmware=["0001"], grub_has_windows=False, uefi=False, ntfs=["/dev/sda1"],
                                  grub_conflicts={"zzz-local.cfg": ["GRUB_DISABLE_OS_PROBER"]}))
        self.assertEqual(plan["steps"], [])
        self.assertIn("os_prober_overridden", self.finding_ids(plan))

    def test_unrelated_override_does_not_block_other_steps(self):
        plan = analyze(base_state(windows_firmware=["0001"], grub_style="hidden", grub_conflicts={"zzz-local.cfg": ["GRUB_DISABLE_OS_PROBER"]}))
        self.assertEqual(self.ids(plan), ["grub_menu"])

    def test_nearly_full_esp_is_reported_and_does_not_change_the_plan_identifier(self):
        first = analyze(base_state(esp_low=True, esp_free_mib=3))
        second = analyze(base_state(esp_low=True, esp_free_mib=2))
        self.assertIn("esp_nearly_full", self.finding_ids(first))
        self.assertIn("3 MiB", next(item for item in first["findings"] if item["id"] == "esp_nearly_full")["detail"])
        self.assertEqual(first["id"], second["id"])
        self.assertNotIn("esp_nearly_full", self.finding_ids(analyze(base_state())))

    def test_mixed_boot_modes_are_called_out(self):
        plan = analyze(base_state(uefi=False, esp_present=True))
        found = next(item for item in plan["findings"] if item["id"] == "mixed_boot_modes")
        self.assertEqual(found["severity"], "problem")
        self.assertIn("UEFI mode", found["detail"])
        self.assertNotIn("mixed_boot_modes", self.finding_ids(analyze(base_state(uefi=False))))

    def test_unmounted_esp_guidance_names_the_device(self):
        plan = analyze(base_state(windows_firmware=["0001"], grub_has_windows=False, os_prober_disabled=False, unmounted_esps=["/dev/sdb1"]))
        self.assertEqual(plan["steps"], [])
        self.assertIn("/dev/sdb1", next(item for item in plan["findings"] if item["id"] == "windows_missing_from_grub")["detail"])

    def test_unreadable_inputs_are_reported_instead_of_hidden(self):
        plan = analyze(base_state(storage_error="The disk list could not be read: lsblk failed", grub_read_error="invalid start byte",
                                  firmware_available=False, firmware_error="Install efibootmgr to manage UEFI boot options."))
        ids = self.finding_ids(plan)
        for expected in ("storage_unreadable", "grub_unreadable", "firmware_unreadable"):
            self.assertIn(expected, ids)

    def test_secure_boot_note_only_with_windows_and_secure_boot_enabled(self):
        enabled = analyze(base_state(windows_firmware=["0001"], secure_boot="enabled"))
        self.assertIn("secure_boot_keys", self.finding_ids(enabled))
        self.assertIn("SBAT", next(item for item in enabled["findings"] if item["id"] == "secure_boot_keys")["detail"])
        self.assertNotIn("secure_boot_keys", self.finding_ids(analyze(base_state(windows_firmware=["0001"], secure_boot="disabled"))))
        self.assertNotIn("secure_boot_keys", self.finding_ids(analyze(base_state(secure_boot="enabled", grub_has_windows=False))))

    def test_missing_fallback_loader_is_explained_only_when_registration_is_needed(self):
        needed = analyze(base_state(windows_firmware=["0001"], ubuntu_loader={"loader": "/EFI/ubuntu/shimx64.efi", "disk": "/dev/sda", "partition": 1},
                                    firmware_available=True, fallback_loader=False))
        self.assertIn("no_fallback_loader", self.finding_ids(needed))
        present = analyze(base_state(windows_firmware=["0001"], ubuntu_loader={"loader": "/EFI/ubuntu/shimx64.efi", "disk": "/dev/sda", "partition": 1},
                                     firmware_available=True, fallback_loader=True))
        self.assertNotIn("no_fallback_loader", self.finding_ids(present))

    def test_several_windows_loaders_and_pending_recovery_are_informational(self):
        plan = analyze(base_state(windows_loader_count=2, pending_recovery=True))
        for expected in ("windows_on_several_partitions", "pending_recovery"):
            self.assertIn(expected, self.finding_ids(plan))
        self.assertTrue(all(item["severity"] == "info" for item in plan["findings"] if item["id"] in ("windows_on_several_partitions", "pending_recovery")))

    def test_bitlocker_finding_mentions_automatic_device_encryption(self):
        plan = analyze(base_state(bitlocker=["/dev/nvme0n1p3"]))
        self.assertIn("turn BitLocker on by itself", next(item for item in plan["findings"] if item["id"] == "bitlocker_volume")["detail"])

    def test_failed_plan_is_unsupported_and_has_no_steps(self):
        plan = failed_plan("boom")
        self.assertEqual((plan["supported"], plan["reason"], plan["steps"]), (False, "boom", []))

    def test_every_finding_has_text_and_a_known_severity(self):
        loud = base_state(windows_firmware=["0001"], windows_loader={"uuid": "A-B", "device": "/dev/sda1", "loader": "/EFI/x.efi"}, grub_has_windows=False,
                          grub_style="hidden", bitlocker=["/dev/a"], luks=["/dev/b"], ntfs=["/dev/c"], esp_low=True, secure_boot="enabled",
                          boot_readonly=True, efivars_readonly=True, storage_error="x", grub_read_error="y", pending_recovery=True,
                          windows_loader_count=2, unmounted_esps=["/dev/d"])
        for item in analyze(loud)["findings"]:
            self.assertTrue(item["title"].strip() and item["detail"].strip())
            self.assertIn(item["severity"], ("problem", "warning", "info"))


class DualBootTests(unittest.TestCase):
    def setUp(self):
        self.fx = test_production.ProductionTests("test_inspection_uses_no_mutating_tools_or_state")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        tool = self.fx.root / "tools" / "os-prober"
        tool.touch()
        self.paths = replace(self.fx.paths, os_prober=tool)
        self.manager = Manager(self.paths, self.fx.runner)

    def dispatch(self, action, **fields):
        return self.manager.dispatch({"action": action, **fields})

    def plan(self):
        return self.dispatch("dual_boot_plan")["plan"]

    def fix(self, plan=None, **fields):
        plan = plan or self.plan()
        return self.dispatch("dual_boot_fix", plan=plan["id"], **fields)

    def ids(self, plan):
        return [step["id"] for step in plan["steps"]]

    def hide_windows_from_grub(self):
        self.fx.generated_base_config = NO_WINDOWS
        self.paths.config.write_text(NO_WINDOWS)

    def test_clean_system_needs_no_fix_and_planning_is_read_only(self):
        before = self.fx.captured()
        plan = self.plan()
        self.assertEqual(plan["steps"], [])
        self.assertTrue(plan["supported"])
        self.assertEqual(self.fx.captured(), before)
        self.assertFalse((self.paths.state / "backups").exists())
        with self.assertRaisesRegex(ValueError, "Select at least one"):
            self.fix(plan, steps=[])

    def test_windows_is_added_to_grub_through_a_verified_managed_entry(self):
        self.hide_windows_from_grub()
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["grub_chainloader"])
        response = self.fix(plan)
        self.assertIn("Add Windows Boot Manager to the GRUB menu", response["applied"])
        titles = [entry["title"] for entry in response["snapshot"]["entries"]]
        self.assertIn("Windows Boot Manager", titles)
        custom = response["snapshot"]["custom_entries"]
        self.assertEqual((custom[0]["type"], custom[0]["uuid"], custom[0]["loader"]),
                         ("efi", "ABCD-1234", "/EFI/Microsoft/Boot/bootmgfw.efi"))
        self.assertTrue(any(backup["id"] == response["backup"] for backup in response["backups"]))
        self.assertEqual(self.ids(self.plan()), [])

    def test_applying_twice_does_not_duplicate_the_windows_entry(self):
        self.hide_windows_from_grub()
        self.fix()
        self.hide_windows_from_grub()
        self.paths.custom_model.write_text(self.paths.custom_model.read_text())
        self.fix()
        self.assertEqual(len(self.dispatch("snapshot")["snapshot"]["custom_entries"]), 1)

    def test_os_prober_is_enabled_when_no_windows_loader_is_mounted(self):
        self.hide_windows_from_grub()
        (self.paths.efi_directory / "EFI/Microsoft/Boot/bootmgfw.efi").unlink()
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["os_prober"])
        self.fix(plan)
        self.assertEqual(parse_settings(self.paths.override.read_text())["GRUB_DISABLE_OS_PROBER"], "false")

    def test_hidden_menu_is_shown_with_a_timeout(self):
        self.paths.defaults.write_text("GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=0\n")
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["grub_menu"])
        self.fix(plan)
        settings = parse_settings(self.paths.override.read_text())
        self.assertEqual((settings["GRUB_TIMEOUT_STYLE"], settings["GRUB_TIMEOUT"]), ("menu", "10"))

    def test_menu_and_os_prober_changes_share_one_grub_update(self):
        self.hide_windows_from_grub()
        (self.paths.efi_directory / "EFI/Microsoft/Boot/bootmgfw.efi").unlink()
        self.paths.defaults.write_text("GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=0\n")
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["grub_menu", "os_prober"])
        self.fx.calls.clear()
        self.fix(plan)
        updates = [call for call in self.fx.calls if call[0] == str(self.paths.update)]
        self.assertEqual(len(updates), 1)
        settings = parse_settings(self.paths.override.read_text())
        self.assertEqual((settings["GRUB_TIMEOUT_STYLE"], settings["GRUB_DISABLE_OS_PROBER"]), ("menu", "false"))

    def test_missing_ubuntu_entry_is_registered_and_placed_first(self):
        self.fx.firmware["entries"] = [entry for entry in self.fx.firmware["entries"] if entry["id"] != "0000"]
        self.fx.firmware["order"] = ["0001", "0002"]
        self.fx.firmware["current"] = "0001"
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["uefi_create_ubuntu"])
        self.fix(plan)
        created = next(entry for entry in self.fx.firmware["entries"] if entry["label"] == "ubuntu")
        self.assertEqual(created["loader"], "/EFI/ubuntu/shimx64.efi")
        self.assertEqual(self.fx.firmware["order"][0], created["id"])
        self.assertEqual(set(self.fx.firmware["order"]), {"0001", "0002", created["id"]})

    def test_missing_ubuntu_loader_file_is_reported_not_guessed(self):
        self.fx.firmware["entries"] = [entry for entry in self.fx.firmware["entries"] if entry["id"] != "0000"]
        self.fx.firmware["order"] = ["0001", "0002"]
        for name in ("shimx64.efi", "grubx64.efi"):
            (self.paths.efi_directory / "EFI/ubuntu" / name).unlink()
        plan = self.plan()
        self.assertEqual(plan["steps"], [])
        self.assertIn("ubuntu_loader_missing", [item["id"] for item in plan["findings"]])

    def test_windows_first_is_reordered_so_the_grub_menu_appears(self):
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["uefi_order"])
        self.fix(plan)
        self.assertEqual(self.fx.firmware["order"], ["0000", "0001", "0002"])

    def test_inactive_ubuntu_entry_is_activated(self):
        next(entry for entry in self.fx.firmware["entries"] if entry["id"] == "0000")["active"] = False
        self.fx.firmware["current"] = "0001"
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["uefi_order"])
        self.fix(plan)
        self.assertTrue(next(entry for entry in self.fx.firmware["entries"] if entry["id"] == "0000")["active"])

    def test_bitlocker_requires_acknowledgement_before_any_change(self):
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        self.fx.inventory.append({"name": "/dev/sda3", "path": "/dev/sda3", "type": "part", "pkname": "/dev/sda", "partn": 3,
                                  "fstype": "BitLocker", "uuid": "", "partuuid": "p3", "parttype": "ebd0a0a2", "ro": False})
        plan = self.plan()
        self.assertTrue(plan["requires_acknowledgement"])
        self.assertEqual(plan["encrypted"]["bitlocker"], ["/dev/sda3"])
        before = self.fx.captured()
        order = list(self.fx.firmware["order"])
        with self.assertRaisesRegex(ValueError, "recovery key"):
            self.fix(plan, steps=["uefi_order"])
        self.assertEqual(self.fx.captured(), before)
        self.assertEqual(self.fx.firmware["order"], order)
        self.assertFalse((self.paths.state / "backups").exists())
        self.fix(plan, steps=["uefi_order"], acknowledge_recovery=True)
        self.assertEqual(self.fx.firmware["order"][0], "0000")

    def test_bitlocker_registration_keeps_the_existing_boot_order(self):
        self.fx.firmware["entries"] = [entry for entry in self.fx.firmware["entries"] if entry["id"] != "0000"]
        self.fx.firmware["order"] = ["0001", "0002"]
        self.fx.firmware["current"] = "0001"
        self.fx.inventory.append({"name": "/dev/sda3", "path": "/dev/sda3", "type": "part", "pkname": "/dev/sda", "partn": 3,
                                  "fstype": "BitLocker", "uuid": "", "partuuid": "p3", "parttype": "ebd0a0a2", "ro": False})
        self.fix(steps=["uefi_create_ubuntu"], acknowledge_recovery=True)
        created = next(entry for entry in self.fx.firmware["entries"] if entry["label"] == "ubuntu")
        self.assertEqual(self.fx.firmware["order"], ["0001", "0002", created["id"]])

    def test_luks_is_reported_without_blocking_or_acknowledgement(self):
        self.fx.inventory.append({"name": "/dev/sda3", "path": "/dev/sda3", "type": "part", "pkname": "/dev/sda", "partn": 3,
                                  "fstype": "crypto_LUKS", "uuid": "L", "partuuid": "p3", "parttype": "linux", "ro": False})
        plan = self.plan()
        self.assertFalse(plan["requires_acknowledgement"])
        self.assertEqual(plan["encrypted"]["luks"], ["/dev/sda3"])
        self.assertIn("luks_volume", [item["id"] for item in plan["findings"]])

    def test_stale_or_foreign_plans_and_steps_are_refused_before_any_write(self):
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        before = self.fx.captured()
        order = list(self.fx.firmware["order"])
        for fields in ({"plan": "0" * 64, "steps": ["uefi_order"]}, {"plan": self.plan()["id"], "steps": ["repair"]},
                       {"plan": self.plan()["id"], "steps": ["uefi_order", "uefi_order"]},
                       {"plan": self.plan()["id"], "steps": "uefi_order"},
                       {"plan": self.plan()["id"], "steps": ["uefi_order"], "acknowledge_recovery": "yes"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.dispatch("dual_boot_fix", **fields)
        with self.assertRaises(ValueError):
            self.dispatch("dual_boot_fix", plan=self.plan()["id"], steps=["uefi_order"], shell="reboot")
        self.assertEqual(self.fx.captured(), before)
        self.assertEqual(self.fx.firmware["order"], order)

    def test_a_plan_made_before_the_system_changed_is_refused(self):
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        plan = self.plan()
        self.fx.firmware["order"] = ["0000", "0001", "0002"]
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.fix(plan)

    def test_a_failure_part_way_restores_the_pre_fix_state(self):
        self.paths.defaults.write_text("GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=0\n")
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        plan = self.plan()
        self.assertEqual(self.ids(plan), ["grub_menu", "uefi_order"])
        self.fx.fail_efi_once = True
        with self.assertRaisesRegex(RuntimeError, "previous settings were restored"):
            self.fix(plan)
        self.assertEqual(self.fx.firmware["order"], ["0001", "0000", "0002"])
        self.assertFalse(self.paths.override.exists() and "GRUB_TIMEOUT_STYLE" in self.paths.override.read_text())
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_a_precondition_failure_before_any_change_does_not_trigger_a_restore(self):
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        plan = self.plan()
        with patch.object(Manager, "apply_order", side_effect=ValueError("no longer valid")), \
                patch.object(Manager, "restore") as restore, self.assertRaisesRegex(RuntimeError, "No changes were made"):
            self.fix(plan)
        restore.assert_not_called()

    def test_a_failure_after_the_firmware_entry_was_created_is_restored(self):
        self.fx.firmware["entries"] = [entry for entry in self.fx.firmware["entries"] if entry["id"] != "0000"]
        self.fx.firmware["order"] = ["0001", "0002"]
        self.fx.firmware["current"] = "0001"
        # With BitLocker the existing order is kept, which needs a reorder after the entry is created.
        self.fx.inventory.append({"name": "/dev/sda3", "path": "/dev/sda3", "type": "part", "pkname": "/dev/sda", "partn": 3,
                                  "fstype": "BitLocker", "uuid": "", "partuuid": "p3", "parttype": "ebd0a0a2", "ro": False})
        plan = self.plan()
        with patch.object(Manager, "apply_order", side_effect=ValueError("late failure")), \
                patch.object(Manager, "restore") as restore, self.assertRaisesRegex(RuntimeError, "previous settings were restored"):
            self.fix(plan, steps=["uefi_create_ubuntu"], acknowledge_recovery=True)
        restore.assert_called_once()

    def finding_ids(self, plan):
        return [item["id"] for item in plan["findings"]]

    def test_a_nearly_full_esp_is_detected_from_the_mounted_partition(self):
        usage = namedtuple("usage", "total used free")
        with patch("uoo.dualboot.shutil.disk_usage", return_value=usage(100 << 20, 98 << 20, 2 << 20)):
            plan = self.plan()
        self.assertIn("esp_nearly_full", self.finding_ids(plan))
        with patch("uoo.dualboot.shutil.disk_usage", return_value=usage(100 << 20, 10 << 20, 90 << 20)):
            self.assertNotIn("esp_nearly_full", self.finding_ids(self.plan()))

    def test_read_only_efivars_remove_firmware_steps(self):
        self.fx.firmware["order"] = ["0001", "0000", "0002"]
        from uoo import platform as facts
        real = facts.mount_for
        def mount_for(path, paths, runner):
            if str(path).endswith("efivars"):
                return {"source": "efivarfs", "fstype": "efivarfs", "target": str(path), "options": "ro,nosuid"}
            return real(path, paths, runner)
        with patch("uoo.dualboot.facts.mount_for", side_effect=mount_for):
            plan = self.plan()
        self.assertEqual(plan["steps"], [])
        self.assertIn("efivars_readonly", self.finding_ids(plan))

    def test_read_only_boot_filesystem_removes_grub_steps(self):
        self.paths.defaults.write_text("GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=0\n")
        self.fx.boot_mount_options = "ro"
        plan = self.plan()
        self.assertEqual(plan["steps"], [])
        self.assertIn("boot_readonly", self.finding_ids(plan))

    def test_a_later_dropin_override_is_found_and_named(self):
        self.paths.defaults.write_text("GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=5\n")
        (self.paths.dropins / "zzz-local.cfg").write_text("export GRUB_TIMEOUT_STYLE=hidden\n")
        (self.paths.dropins / "10-early.cfg").write_text("GRUB_TIMEOUT_STYLE=menu\n")
        plan = self.plan()
        self.assertEqual(plan["steps"], [])
        found = next(item for item in plan["findings"] if item["id"] == "grub_menu_overridden")
        self.assertIn("zzz-local.cfg", found["detail"])
        self.assertNotIn("10-early.cfg", found["detail"])

    def test_an_unmounted_efi_partition_is_listed_when_windows_cannot_be_verified(self):
        self.hide_windows_from_grub()
        (self.paths.efi_directory / "EFI/Microsoft/Boot/bootmgfw.efi").unlink()
        self.fx.inventory.append({"name": "/dev/sdb1", "path": "/dev/sdb1", "type": "part", "pkname": "/dev/sdb", "partn": 1,
                                  "fstype": "vfat", "uuid": "EEEE-1111", "partuuid": "p9",
                                  "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "ro": False})
        self.paths.defaults.write_text("GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT=5\nGRUB_DISABLE_OS_PROBER=false\n")
        plan = self.plan()
        self.assertEqual(plan["steps"], [])
        self.assertIn("/dev/sdb1", next(item for item in plan["findings"] if item["id"] == "windows_missing_from_grub")["detail"])

    def test_legacy_bios_session_with_an_efi_partition_is_flagged(self):
        self.paths.efi.rmdir()
        plan = self.plan()
        self.assertIn("mixed_boot_modes", self.finding_ids(plan))

    def test_unreadable_storage_firmware_and_grub_are_reported_not_fatal(self):
        with patch("uoo.dualboot.facts.devices", side_effect=RuntimeError("lsblk failed: boom")):
            plan = self.plan()
        self.assertTrue(plan["supported"])
        self.assertIn("storage_unreadable", self.finding_ids(plan))
        self.paths.efibootmgr.unlink()
        self.assertIn("firmware_unreadable", self.finding_ids(self.plan()))
        self.paths.config.write_bytes(b"\xff\xfe\x00")
        self.assertIn("grub_unreadable", self.finding_ids(self.plan()))

    def test_an_unexpected_failure_while_checking_becomes_an_explanation(self):
        with patch.object(DualBoot, "dual_boot_state", side_effect=KeyError("surprise")):
            plan = self.plan()
        self.assertFalse(plan["supported"])
        self.assertIn("could not finish the dual-boot check", plan["reason"])
        self.assertEqual(plan["steps"], [])

    def test_a_pending_interrupted_change_is_mentioned(self):
        self.paths.state.mkdir(parents=True, exist_ok=True)
        (self.paths.state / "pending.json").write_text("{}")
        self.assertIn("pending_recovery", self.finding_ids(self.plan()))

    def test_fixing_with_an_unsupported_plan_explains_why(self):
        self.paths.update.unlink()
        plan = self.plan()
        self.assertFalse(plan["supported"])
        with self.assertRaisesRegex(ValueError, "GRUB installation"):
            self.fix(plan, steps=["grub_menu"])

    def test_bios_mode_never_touches_firmware(self):
        self.paths.efi.rmdir()
        plan = self.plan()
        self.assertEqual(plan["boot_mode"], "bios")
        self.assertFalse(any(step["id"].startswith("uefi") for step in plan["steps"]))


if __name__ == "__main__":
    unittest.main()
