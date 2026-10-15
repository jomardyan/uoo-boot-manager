"""Real transaction orchestration on temporary files and emulated boot tools."""

import copy
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock

from uoo.core import DEMO_CONFIG, parse_settings
from uoo.models import compile_custom_entries
from uoo.storage import file_state
from uoo.system import Manager, Paths


ROOT_UUID = "12345678-1234-1234-abcd-123456789abc"
ESP_PARTUUID = "12345678-1234-1234-abcd-123456789def"


class ProductionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tools = self.root / "tools"
        tools.mkdir()
        self.paths = Paths(
            defaults=self.root / "etc/default/grub", dropins=self.root / "etc/default/grub.d",
            config=self.root / "boot/grub/grub.cfg", state=self.root / "var/lib/uoo",
            scripts=self.root / "etc/grub.d", efi=self.root / "sys/firmware/efi",
            efi_directory=self.root / "boot/efi", os_release=self.root / "etc/os-release",
            sys_block=self.root / "sys/class/block", bios_modules=self.root / "lib/grub/i386-pc",
            **{name: tools / name for name in ("update", "efibootmgr", "script_check", "editenv", "grub_reboot",
                                              "grub_set_default", "grub_install", "grub_probe", "lsblk", "findmnt", "mkrelpath")},
        )
        for directory in (self.paths.defaults.parent, self.paths.dropins, self.paths.config.parent,
                          self.paths.scripts, self.paths.efi, self.paths.efi_directory):
            directory.mkdir(parents=True, exist_ok=True)
        for name in ("update", "efibootmgr", "script_check", "editenv", "grub_reboot", "grub_set_default",
                     "grub_install", "grub_probe", "lsblk", "findmnt", "mkrelpath"):
            getattr(self.paths, name).touch()
        self.paths.defaults.write_text('GRUB_DEFAULT=ubuntu\nGRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n')
        self.paths.os_release.write_text('PRETTY_NAME="Ubuntu fixture"\n')
        self.paths.config.write_text(DEMO_CONFIG)
        self.paths.config.chmod(0o640)
        self.paths.grubenv.write_text("# GRUB Environment Block\nsaved_entry=ubuntu\n")
        for filename in ("EFI/ubuntu/shimx64.efi", "EFI/ubuntu/grubx64.efi", "EFI/Microsoft/Boot/bootmgfw.efi", "EFI/fedora/shimx64.efi"):
            path = self.paths.efi_directory / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"MZ" + b"fixture" * 20)
        for filename in ("vmlinuz-6.8.0-generic", "initrd.img-6.8.0-generic"):
            (self.root / "boot" / filename).write_bytes(b"boot fixture")
        self.inventory = [
            {"name": "/dev/sda", "path": "/dev/sda", "type": "disk", "size": 512000000000, "ro": False},
            {"name": "/dev/sda1", "path": "/dev/sda1", "type": "part", "pkname": "/dev/sda", "partn": 1,
             "fstype": "vfat", "uuid": "ABCD-1234", "partuuid": ESP_PARTUUID,
             "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "mountpoints": [str(self.paths.efi_directory)], "ro": False},
            {"name": "/dev/sda2", "path": "/dev/sda2", "type": "part", "pkname": "/dev/sda", "partn": 2,
             "fstype": "ext4", "uuid": ROOT_UUID, "partuuid": "abc123", "parttype": "linux",
             "mountpoints": [str(self.root)], "ro": False},
        ]
        self.firmware = {
            "entries": [{"id": "0000", "label": "Ubuntu", "active": True, "loader": "/EFI/ubuntu/shimx64.efi"},
                        {"id": "0001", "label": "Windows Boot Manager", "active": True, "loader": "/EFI/Microsoft/Boot/bootmgfw.efi"},
                        {"id": "0002", "label": "USB drive", "active": True, "loader": "/EFI/BOOT/BOOTX64.EFI"}],
            "order": ["0000", "0001", "0002"], "next": "", "current": "0000",
        }
        self.calls = []
        self.fail_update = False
        self.fail_script_check = False
        self.omit_custom = False
        self.generated_base_config = DEMO_CONFIG
        self.fail_efi_once = False
        self.fail_efi_always = False
        self.efibootmgr18 = False
        self.reject_absent_efi_deletes = True
        self.boot_source = "/dev/sda2"
        self.boot_filesystem = "ext4"
        self.boot_probe_uuid = ROOT_UUID
        self.efi_probe_uuid = "ABCD-1234"
        self.boot_mount_options = "rw"
        self.efi_mount_options = "rw"
        self.external_environment = None
        self.fail_environment_write_once = False
        self.fail_environment_list = False
        self.manager = Manager(self.paths, self.runner)

    def environment_values(self):
        if self.external_environment is not None:
            return dict(self.external_environment)
        return {line.partition("=")[0]: line.partition("=")[2]
                for line in self.paths.grubenv.read_text().splitlines() if "=" in line and not line.startswith("#")}

    def write_environment_values(self, values):
        if self.external_environment is not None:
            self.external_environment.clear()
            self.external_environment.update(values)
        else:
            self.paths.grubenv.write_text("# GRUB Environment Block\n" + "".join(f"{key}={value}\n" for key, value in values.items()))

    def enable_external_environment(self, values):
        self.boot_filesystem = "btrfs"
        self.external_environment = dict(values)
        self.paths.grubenv.write_text("# GRUB Environment Block\nenv_block=4096+2048\n")

    def runner(self, arguments, timeout=120):
        args = list(map(str, arguments))
        self.calls.append(args)
        program = args[0]
        if program == str(self.paths.lsblk):
            return json.dumps({"blockdevices": self.inventory})
        if program == str(self.paths.findmnt):
            if str(self.paths.efi_directory) in args:
                source, filesystem, target = "/dev/sda1", "vfat", str(self.paths.efi_directory)
                options = self.efi_mount_options
            else:
                source, filesystem, target = self.boot_source, self.boot_filesystem, str(self.root)
                options = self.boot_mount_options
            return json.dumps({"filesystems": [{"source": source, "fstype": filesystem, "target": target, "options": options}]})
        if program == str(self.paths.update):
            if self.fail_update:
                self.paths.config.write_text("torn generated configuration")
                raise RuntimeError("generation failed after writing")
            custom = []
            if self.paths.custom_model.exists() and not self.omit_custom:
                custom = json.loads(self.paths.custom_model.read_text())
            self.paths.config.write_text(self.generated_base_config + "\n" + compile_custom_entries(custom))
            return "updated"
        if program == str(self.paths.script_check):
            if self.fail_script_check:
                raise RuntimeError("GRUB syntax check failed")
            return ""
        if program in (str(self.paths.grub_reboot), str(self.paths.grub_set_default), str(self.paths.editenv)):
            values = self.environment_values()
            if program == str(self.paths.editenv):
                operation = args[2]
                if operation == "list":
                    if self.fail_environment_list:
                        raise RuntimeError("native environment block read failed")
                    return "".join(f"{key}={value}\n" for key, value in values.items())
                if operation == "unset":
                    for key in args[3:]:
                        values.pop(key, None)
                elif operation == "set":
                    for assignment in args[3:]:
                        key, separator, value = assignment.partition("=")
                        if not separator:
                            raise AssertionError("Bad fake grub-editenv assignment")
                        values[key] = value
                else:
                    raise AssertionError("Unexpected fake grub-editenv operation: " + operation)
            elif program == str(self.paths.grub_set_default):
                values.pop("next_entry", None)
                values.pop("prev_saved_entry", None)
                values["saved_entry"] = args[-1]
            else:
                if values.get("prev_saved_entry"):
                    values["saved_entry"] = values.pop("prev_saved_entry")
                values["next_entry"] = args[-1]
            self.write_environment_values(values)
            if self.fail_environment_write_once:
                self.fail_environment_write_once = False
                raise RuntimeError("environment update failed after writing")
            return ""
        if program == str(self.paths.grub_probe):
            target = Path(args[-1]).resolve()
            return (self.efi_probe_uuid if target.is_relative_to(self.paths.efi_directory.resolve())
                    else self.boot_probe_uuid) + "\n"
        if program == str(self.paths.mkrelpath):
            return "/boot/grub\n"
        if program == str(self.paths.efibootmgr):
            mutating = len(args) > 1 and args[1] != "--verbose"
            if "--bootorder" in args:
                self.firmware["order"] = args[args.index("--bootorder") + 1].split(",")
            elif "--delete-bootorder" in args:
                if self.reject_absent_efi_deletes and not self.firmware["order"]:
                    raise RuntimeError("BootOrder does not exist")
                self.firmware["order"] = []
            elif "--bootnext" in args:
                self.firmware["next"] = args[args.index("--bootnext") + 1]
            elif "--delete-bootnext" in args:
                if self.reject_absent_efi_deletes and not self.firmware["next"]:
                    raise RuntimeError("BootNext does not exist")
                self.firmware["next"] = ""
            elif "--create" in args or "--create-only" in args:
                identifier = args[args.index("--bootnum") + 1]
                self.firmware["entries"].append({"id": identifier, "label": args[args.index("--label") + 1],
                                                 "active": True, "loader": args[args.index("--loader") + 1].replace("\\", "/")})
                if "--create" in args:
                    self.firmware["order"].insert(0, identifier)
            elif "--delete-bootnum" in args:
                identifier = args[args.index("--bootnum") + 1]
                self.firmware["entries"] = [entry for entry in self.firmware["entries"] if entry["id"] != identifier]
                self.firmware["order"] = [entry for entry in self.firmware["order"] if entry != identifier]
            elif "--active" in args or "--inactive" in args:
                identifier = args[args.index("--bootnum") + 1]
                next(entry for entry in self.firmware["entries"] if entry["id"] == identifier)["active"] = "--active" in args
            if mutating and (self.fail_efi_once or self.fail_efi_always):
                self.fail_efi_once = False
                raise RuntimeError("firmware failed after writing")
            text = "BootCurrent: " + self.firmware["current"] + "\nBootOrder: " + ",".join(self.firmware["order"]) + "\n"
            if self.firmware["next"]:
                text += "BootNext: " + self.firmware["next"] + "\n"
            for entry in self.firmware["entries"]:
                loader = entry["loader"].replace("/", "\\")
                # efibootmgr 18 prints the bare file path followed by any optional data as hex.
                path = f"{loader}57494e444f5753" if self.efibootmgr18 else f"File({loader})"
                text += f"Boot{entry['id']}{'*' if entry['active'] else ' '} {entry['label']}\tHD(1,GPT,{ESP_PARTUUID},0x800,0x100000)/{path}\n"
            return text
        raise AssertionError("Unexpected fixture command: " + repr(args))

    def dispatch(self, action, **fields):
        return self.manager.dispatch({"action": action, **fields})

    def custom(self, **changes):
        entry = {"id": "uoo-" + "a" * 32, "title": "Custom Ubuntu", "type": "linux", "enabled": True,
                 "uuid": ROOT_UUID, "kernel": "/boot/vmlinuz-6.8.0-generic",
                 "initrd": "/boot/initrd.img-6.8.0-generic", "options": "ro quiet"}
        entry.update(changes)
        return entry

    def captured(self):
        return {key: file_state(path) for key, path in self.manager.file_paths().items()}

    def backup_id(self, response):
        return response["backups"][0]["id"]

    def test_inspection_uses_no_mutating_tools_or_state(self):
        response = self.dispatch("snapshot")
        self.assertTrue(response["snapshot"]["capabilities"]["grub_next"])
        self.assertFalse(self.paths.state.exists())
        self.assertTrue(all(call[0] in (str(self.paths.efibootmgr), str(self.paths.lsblk), str(self.paths.findmnt))
                            or (call[0] == str(self.paths.editenv) and call[2] == "list") for call in self.calls))

    def test_changes_merge_owned_settings_without_rewriting_defaults(self):
        defaults = self.paths.defaults.read_bytes()
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "windows", "GRUB_TIMEOUT": 8})
        self.dispatch("apply_grub", changes={"GRUB_CMDLINE_LINUX": "iommu=pt", "GRUB_DISABLE_RECOVERY": True})
        settings = parse_settings(self.paths.override.read_text())
        self.assertEqual(settings["GRUB_DEFAULT"], "windows")
        self.assertEqual(settings["GRUB_TIMEOUT"], "8")
        self.assertEqual(settings["GRUB_CMDLINE_LINUX"], "iommu=pt")
        self.assertEqual(settings["GRUB_DISABLE_RECOVERY"], "true")
        self.assertEqual(self.paths.defaults.read_bytes(), defaults)

    def test_generation_failure_restores_file_contents_and_modes(self):
        self.paths.override.write_text("GRUB_TIMEOUT=22\n")
        self.paths.override.chmod(0o640)
        before = self.captured()
        self.fail_update = True
        with self.assertRaisesRegex(RuntimeError, "previous configuration restored"):
            self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 8})
        self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "pending.json").exists())
        self.assertEqual(len(self.manager.list_backups()), 1)

    def test_syntax_failure_rolls_back_first_dropin(self):
        before = self.captured()
        self.fail_script_check = True
        with self.assertRaises(RuntimeError):
            self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 8})
        self.assertEqual(self.captured(), before)
        self.assertFalse(self.paths.override.exists())

    def test_invalid_requests_and_imports_rejected_before_commit(self):
        before = self.captured()
        invalid = [None, {"action": "shell"}, {"action": "apply_grub", "changes": {"GRUB_TIMEOUT": True}},
                   {"action": "apply_grub", "changes": {"GRUB_DEFAULT": "missing"}},
                   {"action": "import_configuration", "data": {"format": "uoo-configuration", "version": 1,
                     "configuration": {"grub": {"GRUB_CMDLINE_LINUX": "ro; reboot"}, "custom_entries": []}}}]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.manager.dispatch(request)
            self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "backups").exists())
        self.assertFalse(any(call[0] == str(self.paths.update) for call in self.calls))

    def test_custom_entries_verified_and_script_is_executable(self):
        response = self.dispatch("custom_entries", entries=[self.custom()])
        self.assertEqual(response["snapshot"]["entries"][-1]["selector"], self.custom()["id"])
        self.assertEqual(stat.S_IMODE(self.paths.custom_script.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.paths.custom_model.stat().st_mode), 0o600)
        self.assertIn("cat <<'UOO_GRUB_ENTRIES'", self.paths.custom_script.read_text())

    def test_missing_custom_files_rejected_before_backup(self):
        before = self.captured()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.dispatch("custom_entries", entries=[self.custom(kernel="/boot/vmlinuz-missing")])
        self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "backups").exists())

    def test_custom_regeneration_failure_removes_new_script_and_model(self):
        before = self.captured()
        self.omit_custom = True
        with self.assertRaisesRegex(RuntimeError, "Not every enabled custom"):
            self.dispatch("custom_entries", entries=[self.custom()])
        self.assertEqual(self.captured(), before)

    def test_custom_default_and_next_choice_protected(self):
        custom = self.custom()
        self.dispatch("custom_entries", entries=[custom])
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": custom["id"]})
        with self.assertRaisesRegex(ValueError, "default"):
            self.dispatch("custom_entries", entries=[])
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "ubuntu"})
        self.dispatch("grub_next_boot", entry=custom["id"])
        with self.assertRaisesRegex(ValueError, "one-time"):
            self.dispatch("custom_entries", entries=[])

    def test_numeric_custom_default_is_also_protected(self):
        self.dispatch("custom_entries", entries=[self.custom()])
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "4"})
        before = self.captured()
        with self.assertRaisesRegex(ValueError, "default"):
            self.dispatch("custom_entries", entries=[])
        self.assertEqual(self.captured(), before)

    def test_environment_choice_changes_are_scoped(self):
        backup = self.backup_id(self.dispatch("grub_next_boot", entry="windows"))
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 30})
        restored = self.dispatch("restore", backup=backup)
        self.assertEqual(restored["snapshot"]["grub_next"], "")
        self.assertEqual(restored["snapshot"]["settings"]["GRUB_TIMEOUT"], "30")

    def test_grub_next_backup_restore_preserves_later_saved_entry_and_environment(self):
        backup = self.backup_id(self.dispatch("grub_next_boot", entry="windows"))
        self.paths.grubenv.write_text("# GRUB Environment Block\nsaved_entry=advanced>linux-6.8\nnext_entry=windows\ncustom_state=keep-this\n")
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "saved", "GRUB_SAVEDEFAULT": True})
        restored = self.dispatch("restore", backup=backup)
        self.assertEqual(restored["snapshot"]["grub_next"], "")
        self.assertEqual(restored["snapshot"]["grub_saved"], "advanced>linux-6.8")
        self.assertEqual(restored["snapshot"]["settings"]["GRUB_DEFAULT"], "saved")
        self.assertIn("custom_state=keep-this\n", self.paths.grubenv.read_text())

    def test_external_environment_write_failure_restores_semantics_without_changing_pointer(self):
        self.enable_external_environment({"saved_entry": "ubuntu", "custom_state": "unchanged"})
        pointer = self.paths.grubenv.read_bytes()
        before = self.captured()
        self.fail_environment_write_once = True
        with self.assertRaisesRegex(RuntimeError, "previous configuration restored"):
            self.dispatch("grub_next_boot", entry="windows")
        self.assertEqual(self.external_environment, {"saved_entry": "ubuntu", "custom_state": "unchanged"})
        self.assertEqual(self.paths.grubenv.read_bytes(), pointer)
        self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_external_environment_journal_recovers_backing_values(self):
        self.enable_external_environment({"saved_entry": "windows", "next_entry": "windows", "custom_state": "preserve"})
        pointer = file_state(self.paths.grubenv)
        with self.manager.lock():
            self.manager.store.journal({"version": 2, "files": {"grubenv": pointer},
                                        "grub_environment": {"saved_entry": "ubuntu", "next_entry": None, "prev_saved_entry": None}})
            self.manager.recover()
        self.assertEqual(self.external_environment, {"saved_entry": "ubuntu", "custom_state": "preserve"})
        self.assertEqual(file_state(self.paths.grubenv), pointer)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_external_one_time_restore_preserves_later_saved_and_other_values(self):
        self.enable_external_environment({"saved_entry": "ubuntu", "next_entry": "advanced>linux-6.8", "custom_state": "old"})
        pointer = self.paths.grubenv.read_bytes()
        backup = self.backup_id(self.dispatch("grub_next_boot", entry="windows"))
        self.external_environment["saved_entry"] = "windows"
        self.external_environment["custom_state"] = "new"
        restored = self.dispatch("restore", backup=backup)
        self.assertEqual(restored["snapshot"]["grub_next"], "advanced>linux-6.8")
        self.assertEqual(restored["snapshot"]["grub_saved"], "windows")
        self.assertEqual(self.external_environment["custom_state"], "new")
        self.assertEqual(self.paths.grubenv.read_bytes(), pointer)

    def test_external_manual_backup_restores_only_managed_semantic_variables(self):
        self.enable_external_environment({"saved_entry": "ubuntu", "custom_state": "old"})
        backup = self.backup_id(self.dispatch("create_backup", kind="grub"))
        self.external_environment.update(saved_entry="windows", next_entry="windows", custom_state="new")
        self.dispatch("restore", backup=backup)
        self.assertEqual(self.external_environment, {"saved_entry": "ubuntu", "custom_state": "new"})

    def test_external_legacy_previous_default_is_restored_after_partial_failure(self):
        original = {"saved_entry": "windows", "prev_saved_entry": "ubuntu", "custom_state": "keep"}
        self.enable_external_environment(original)
        self.fail_environment_write_once = True
        with self.assertRaises(RuntimeError):
            self.dispatch("grub_next_boot", entry="advanced>linux-6.8")
        self.assertEqual(self.external_environment, original)

    def test_invalid_semantic_journal_rejected_before_restoring_any_files(self):
        self.enable_external_environment({"saved_entry": "ubuntu", "custom_state": "keep"})
        saved_menu = file_state(self.paths.config)
        self.paths.config.write_text("current menu must not be replaced")
        before_files, before_values = self.captured(), dict(self.external_environment)
        invalid = (None, {}, {"next_entry": "ubuntu"},
                   {"saved_entry": "ubuntu", "next_entry": True, "prev_saved_entry": None},
                   {"saved_entry": "ubuntu\nmalformed", "next_entry": None, "prev_saved_entry": None},
                   {"saved_entry": "ubuntu", "next_entry": None, "prev_saved_entry": None, "other": "no"})
        for state in invalid:
            with self.manager.lock():
                self.manager.store.journal({"version": 2, "files": {"generated": saved_menu}, "grub_environment": state})
            with self.subTest(state=state), self.manager.lock():
                with self.assertRaises((ValueError, RuntimeError)):
                    self.manager.recover()
            self.assertEqual(self.captured(), before_files)
            self.assertEqual(self.external_environment, before_values)
            self.assertTrue((self.paths.state / "pending.json").exists())

    def test_native_environment_read_failure_blocks_mutation_before_backup(self):
        self.enable_external_environment({"saved_entry": "windows", "next_entry": "ubuntu", "custom_state": "keep"})
        before_files, before_values = self.captured(), dict(self.external_environment)
        self.fail_environment_list = True
        with self.assertRaises(RuntimeError):
            self.dispatch("grub_next_boot", entry="windows")
        self.assertEqual(self.captured(), before_files)
        self.assertEqual(self.external_environment, before_values)
        self.assertFalse((self.paths.state / "backups").exists())
        self.assertTrue(all(call[2] == "list" for call in self.calls if call[0] == str(self.paths.editenv)))

    def test_successful_empty_native_list_does_not_fall_back_to_stale_pointer_values(self):
        self.enable_external_environment({})
        self.paths.grubenv.write_text("# GRUB Environment Block\nenv_block=4096+2048\nsaved_entry=windows\nnext_entry=windows\n")
        response = self.dispatch("snapshot")
        self.assertEqual(response["snapshot"]["grub_saved"], "")
        self.assertEqual(response["snapshot"]["grub_next"], "")
        self.assertEqual(self.external_environment, {})

    def test_external_custom_default_and_next_choices_are_protected(self):
        entry = self.custom()
        self.dispatch("custom_entries", entries=[entry])
        self.enable_external_environment({"saved_entry": entry["id"]})
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "saved", "GRUB_SAVEDEFAULT": True})
        with self.assertRaisesRegex(ValueError, "default"):
            self.dispatch("custom_entries", entries=[])
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "ubuntu", "GRUB_SAVEDEFAULT": False})
        self.external_environment["next_entry"] = entry["id"]
        with self.assertRaisesRegex(ValueError, "one-time"):
            self.dispatch("custom_entries", entries=[])

    def test_split_boot_filesystem_uuid_mismatch_refused_before_writes(self):
        boot_uuid = "12345678-1234-1234-abcd-123456789000"
        self.boot_probe_uuid = boot_uuid
        self.boot_source = "/dev/sda3"
        self.inventory.append({"name": "/dev/sda3", "path": "/dev/sda3", "type": "part", "pkname": "/dev/sda", "partn": 3,
                               "fstype": "ext4", "uuid": boot_uuid, "partuuid": "boot-partition",
                               "parttype": "linux", "mountpoints": [str(self.root / "boot")], "ro": False})
        before = self.captured()
        with self.assertRaises(ValueError):
            self.dispatch("custom_entries", entries=[self.custom()])
        self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "backups").exists())
        valid = self.custom(uuid=boot_uuid, kernel="/vmlinuz-6.8.0-generic", initrd="/initrd.img-6.8.0-generic",
                            options="root=UUID=" + ROOT_UUID + " ro")
        self.assertTrue(self.dispatch("custom_entries", entries=[valid])["ok"])

    def test_efi_loader_uuid_mismatch_refused_before_firmware_write(self):
        self.efi_probe_uuid = "FFFF-AAAA"
        original = copy.deepcopy(self.firmware)
        with self.assertRaises(ValueError):
            self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")
        self.assertEqual(self.firmware, original)
        self.assertFalse((self.paths.state / "backups").exists())

    def test_partial_firmware_write_rolls_back_and_journal_finishes(self):
        before = copy.deepcopy(self.firmware)
        self.fail_efi_once = True
        with self.assertRaisesRegex(RuntimeError, "previous setting restored"):
            self.dispatch("apply_order", order=["0001", "0000"])
        self.assertEqual(self.firmware, before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_failed_rollback_leaves_journal_for_later_recovery(self):
        before = copy.deepcopy(self.firmware)
        self.fail_efi_always = True
        with self.assertRaisesRegex(RuntimeError, "Rollback also failed"):
            self.dispatch("next_boot", entry="0001")
        self.assertTrue((self.paths.state / "pending.json").exists())
        self.fail_efi_always = False
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 9})
        self.assertEqual(self.firmware, before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_firmware_flags_reject_current_next_and_nonboolean(self):
        self.dispatch("set_active", entry="0001", active=False)
        self.assertFalse(self.firmware["entries"][1]["active"])
        with self.assertRaises(ValueError):
            self.dispatch("next_boot", entry="0001")
        self.dispatch("set_active", entry="0001", active=True)
        self.dispatch("next_boot", entry="0001")
        for request in ({"entry": "0000", "active": False}, {"entry": "0001", "active": False}, {"entry": "0002", "active": "false"}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.dispatch("set_active", **request)

    def test_repeated_next_clear_does_not_delete_an_absent_firmware_variable(self):
        self.dispatch("next_boot", entry="")
        self.dispatch("next_boot", entry="")
        self.assertEqual(self.manager.list_backups(), [])
        self.dispatch("next_boot", entry="0001")
        self.dispatch("next_boot", entry="")
        self.dispatch("next_boot", entry="")
        deletes = [call for call in self.calls if "--delete-bootnext" in call]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(self.firmware["next"], "")
        self.assertEqual(len(self.manager.list_backups()), 2)

    def test_baseline_restore_with_empty_next_never_deletes_absent_variable(self):
        backup = self.backup_id(self.dispatch("create_backup", kind="uefi"))
        self.dispatch("apply_order", order=["0001", "0000"])
        self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware["order"], ["0000", "0001", "0002"])
        self.assertEqual(self.firmware["next"], "")
        self.assertFalse(any("--delete-bootnext" in call for call in self.calls))

    def test_retry_after_clear_succeeded_but_journal_finish_failed(self):
        self.firmware["next"] = "0001"
        with self.manager.lock():
            self.manager.store.journal({"version": 2, "files": {}, "firmware": {"next": ""}})
        original_finish = self.manager.store.finish
        self.manager.store.finish = Mock(side_effect=RuntimeError("interruption after variable clear"))
        with self.manager.lock():
            with self.assertRaisesRegex(RuntimeError, "needs recovery"):
                self.manager.recover()
        self.assertEqual(self.firmware["next"], "")
        self.assertTrue((self.paths.state / "pending.json").exists())
        self.manager.store.finish = original_finish
        with self.manager.lock():
            self.manager.recover()
        self.assertFalse((self.paths.state / "pending.json").exists())
        self.assertEqual(len([call for call in self.calls if "--delete-bootnext" in call]), 1)

    def test_empty_order_restoration_does_not_delete_absent_bootorder(self):
        self.firmware["order"] = []
        original_entries = copy.deepcopy(self.firmware["entries"])
        self.manager.restore_firmware_state({"order": []})
        self.manager.restore_firmware_state({"order": []})
        self.assertEqual(self.firmware["order"], [])
        self.assertEqual(self.firmware["entries"], original_entries)
        self.assertFalse(any("--delete-bootorder" in call for call in self.calls))

    def test_firmware_creation_can_be_undone_without_deleting_other_entries(self):
        original = copy.deepcopy(self.firmware)
        backup = self.backup_id(self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi"))
        self.assertTrue(any(entry["id"] == "0003" for entry in self.firmware["entries"]))
        self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware, original)

    def test_firmware_deletion_backup_recreates_original_id_and_order(self):
        original = copy.deepcopy(self.firmware)
        backup = self.backup_id(self.dispatch("delete_uefi", entry="0001", confirm="0001"))
        self.assertFalse(any(entry["id"] == "0001" for entry in self.firmware["entries"]))
        self.dispatch("restore", backup=backup)
        self.assertEqual({entry["id"]: entry for entry in self.firmware["entries"]}, {entry["id"]: entry for entry in original["entries"]})
        self.assertEqual(self.firmware["order"], original["order"])

    def test_efibootmgr_18_device_paths_keep_deletion_restorable(self):
        self.efibootmgr18 = True
        self.test_firmware_deletion_backup_recreates_original_id_and_order()

    def test_order_change_allowed_when_current_entry_is_outside_boot_order(self):
        # Booting through a one-time or firmware menu choice leaves BootCurrent outside BootOrder.
        self.firmware["order"] = ["0001", "0002"]
        self.dispatch("apply_order", order=["0002", "0001"])
        self.assertEqual(self.firmware["order"], ["0002", "0001"])

    def test_restore_refuses_to_schedule_an_inactive_next_boot_entry(self):
        self.dispatch("next_boot", entry="0002")
        backup = self.backup_id(self.dispatch("next_boot", entry=""))
        self.dispatch("set_active", entry="0002", active=False)
        with self.assertRaisesRegex(ValueError, "inactive firmware entry"):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware["next"], "")

    def test_creation_rejects_labels_with_surrounding_whitespace(self):
        with self.assertRaisesRegex(ValueError, "firmware label"):
            self.dispatch("create_uefi", label="Fedora ", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")

    def test_creation_undo_refuses_reused_id_without_removing_unrelated_loader(self):
        backup = self.backup_id(self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi"))
        self.dispatch("delete_uefi", entry="0003", confirm="0003")
        self.dispatch("create_uefi", label="Another loader", disk="/dev/sda", partition=1, loader="/EFI/ubuntu/shimx64.efi")
        before = copy.deepcopy(self.firmware)
        self.calls.clear()
        with self.assertRaises(ValueError):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware, before)
        self.assertFalse(any("--delete-bootnum" in call for call in self.calls))

    def test_deletion_undo_refuses_reused_id_before_writes(self):
        backup = self.backup_id(self.dispatch("delete_uefi", entry="0001", confirm="0001"))
        self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")
        before = copy.deepcopy(self.firmware)
        self.calls.clear()
        with self.assertRaises(ValueError):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware, before)
        self.assertTrue(all(call[1] == "--verbose" for call in self.calls if call[0] == str(self.paths.efibootmgr)))

    def test_creation_undo_protects_scheduled_entry_until_cleared(self):
        backup = self.backup_id(self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi"))
        self.dispatch("next_boot", entry="0003")
        before = copy.deepcopy(self.firmware)
        with self.assertRaises(ValueError):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware, before)
        self.dispatch("next_boot", entry="")
        self.dispatch("restore", backup=backup)
        self.assertFalse(any(entry["id"] == "0003" for entry in self.firmware["entries"]))

    def test_manual_firmware_backup_recreates_missing_local_loader(self):
        backup = self.backup_id(self.dispatch("create_backup", kind="uefi"))
        self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")
        self.dispatch("delete_uefi", entry="0001", confirm="0001")
        self.dispatch("restore", backup=backup)
        entries = {entry["id"]: entry for entry in self.firmware["entries"]}
        self.assertEqual(entries["0001"]["label"], "Windows Boot Manager")
        self.assertEqual(entries["0001"]["loader"], "/EFI/Microsoft/Boot/bootmgfw.efi")
        self.assertEqual(entries["0003"]["label"], "Fedora")
        self.assertEqual(self.firmware["order"], ["0000", "0001", "0002", "0003"])

    def test_manual_firmware_restore_refuses_reused_local_loader_identity(self):
        backup = self.backup_id(self.dispatch("create_backup", kind="uefi"))
        self.dispatch("delete_uefi", entry="0001", confirm="0001")
        self.dispatch("create_uefi", label="Fedora", disk="/dev/sda", partition=1, loader="/EFI/fedora/shimx64.efi")
        before = copy.deepcopy(self.firmware)
        with self.assertRaises(ValueError):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.firmware, before)

    def test_full_restore_failure_rolls_back_files_and_firmware_together(self):
        backup = self.backup_id(self.dispatch("create_backup", kind="all"))
        self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 27})
        self.dispatch("set_active", entry="0001", active=False)
        before_files, before_firmware = self.captured(), copy.deepcopy(self.firmware)
        self.fail_efi_once = True
        with self.assertRaises(RuntimeError):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.captured(), before_files)
        self.assertEqual(self.firmware, before_firmware)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_full_grub_restore_refuses_removed_fixed_kernel_and_rolls_back(self):
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "advanced>linux-6.8"})
        backup = self.backup_id(self.dispatch("create_backup", kind="grub"))
        self.generated_base_config = DEMO_CONFIG.replace("6.8", "6.9")
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "ubuntu", "GRUB_TIMEOUT": 22})
        before = self.captured()
        with self.assertRaisesRegex(RuntimeError, "default GRUB entry is no longer available"):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_full_grub_restore_refuses_removed_saved_kernel_and_reverts_external_values(self):
        self.enable_external_environment({"saved_entry": "advanced>linux-6.8", "custom_state": "old"})
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "saved", "GRUB_SAVEDEFAULT": True})
        backup = self.backup_id(self.dispatch("create_backup", kind="grub"))
        self.generated_base_config = DEMO_CONFIG.replace("6.8", "6.9")
        self.external_environment.update(saved_entry="ubuntu", custom_state="current")
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "ubuntu", "GRUB_SAVEDEFAULT": False})
        before_files, before_values = self.captured(), dict(self.external_environment)
        with self.assertRaisesRegex(RuntimeError, "default GRUB entry is no longer available"):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.captured(), before_files)
        self.assertEqual(self.external_environment, before_values)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_full_grub_restore_validates_raw_legacy_title_path(self):
        title = "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-generic"
        self.paths.override.write_text("GRUB_DEFAULT='" + title + "'\n")
        backup = self.backup_id(self.dispatch("create_backup", kind="grub"))
        self.generated_base_config = DEMO_CONFIG.replace("6.8", "6.9")
        self.dispatch("apply_grub", changes={"GRUB_DEFAULT": "ubuntu"})
        before = self.captured()
        with self.assertRaisesRegex(RuntimeError, "default GRUB entry is no longer available"):
            self.dispatch("restore", backup=backup)
        self.assertEqual(self.captured(), before)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_pending_journal_recovers_before_next_change(self):
        original_files, original_firmware = self.captured(), copy.deepcopy(self.firmware)
        with self.manager.lock():
            self.manager.store.journal({"version": 2, "files": {"override": original_files["override"], "generated": original_files["generated"]},
                                        "firmware": {"order": original_firmware["order"], "next": original_firmware["next"]}})
        self.paths.override.write_text("GRUB_TIMEOUT=999\n")
        self.paths.config.write_text("torn configuration")
        self.firmware["order"] = ["0002", "0000"]
        self.firmware["next"] = "0002"
        response = self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 12})
        self.assertEqual(response["snapshot"]["settings"]["GRUB_TIMEOUT"], "12")
        self.assertEqual(self.firmware, original_firmware)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_corrupt_or_unsupported_journal_blocks_new_writes(self):
        before = self.captured()
        with self.manager.lock():
            self.manager.store.journal({"version": 2, "files": {"unknown": {"data": None, "mode": 0o644}}})
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 8})
        self.assertEqual(self.captured(), before)
        path = self.paths.state / "pending.json"
        path.write_bytes(b"corrupt")
        with self.assertRaises(RuntimeError):
            self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 8})
        self.assertEqual(self.captured(), before)
        self.assertTrue(path.exists())

    def test_bios_journal_invokes_boot_sector_recovery(self):
        state = {"disk": "/dev/sda", "boot_uuid": ROOT_UUID, "regions": []}
        self.manager.restore_bios_state = Mock()
        self.manager.validate_bios_state = Mock()
        with self.manager.lock():
            self.manager.store.journal({"version": 2, "files": {}, "bios": state})
            self.manager.recover()
        self.manager.restore_bios_state.assert_called_once_with(state)
        self.manager.validate_bios_state.assert_called_once_with(state)
        self.assertFalse((self.paths.state / "pending.json").exists())

    def test_invalid_journal_schemas_are_not_ignored_or_cleared(self):
        before = self.captured()
        records = (None, {}, [], ["state"], {"version": 3, "files": {}},
                   {"version": 2, "files": []}, {"version": 2, "files": {}, "new_disk_format": {}})
        for record in records:
            with self.manager.lock():
                self.manager.store.journal(record)
            with self.subTest(record=record), self.assertRaises(RuntimeError):
                self.dispatch("apply_grub", changes={"GRUB_TIMEOUT": 8})
            self.assertEqual(self.captured(), before)
            self.assertTrue((self.paths.state / "pending.json").exists())

    def test_unresolved_boot_device_disables_environment_changes(self):
        self.boot_source = "/dev/mapper/not-in-inventory"
        self.assertFalse(self.dispatch("snapshot")["snapshot"]["capabilities"]["grub_next"])
        before = self.captured()
        with self.assertRaises(ValueError):
            self.dispatch("grub_next_boot", entry="windows")
        self.assertEqual(self.captured(), before)

    def test_readonly_boot_disk_partition_or_mount_disables_environment_changes(self):
        for case in ("disk", "partition", "mount"):
            self.inventory[0]["ro"] = case == "disk"
            self.inventory[2]["ro"] = case == "partition"
            self.boot_mount_options = "ro,relatime" if case == "mount" else "rw,errors=remount-ro"
            before = self.captured()
            with self.subTest(case=case):
                self.assertFalse(self.dispatch("snapshot")["snapshot"]["capabilities"]["grub_next"])
                with self.assertRaises(ValueError):
                    self.dispatch("grub_next_boot", entry="windows")
                self.assertEqual(self.captured(), before)
                self.assertFalse((self.paths.state / "backups").exists())

    def test_rw_error_policy_is_not_mistaken_for_readonly_mount(self):
        self.boot_mount_options = "rw,relatime,errors=remount-ro"
        self.assertTrue(self.dispatch("snapshot")["snapshot"]["capabilities"]["grub_next"])

    def test_portable_export_import_preserves_firmware(self):
        backup = self.backup_id(self.dispatch("create_backup", kind="all"))
        envelope = json.loads(self.dispatch("export_backup", backup=backup)["data"])
        envelope["configuration"]["grub"]["GRUB_TIMEOUT"] = 16
        before = copy.deepcopy(self.firmware)
        response = self.dispatch("import_configuration", data=envelope)
        self.assertEqual(response["snapshot"]["settings"]["GRUB_TIMEOUT"], "16")
        self.assertEqual(self.firmware, before)

    def test_import_missing_custom_entry_rolls_back_entire_configuration(self):
        before = self.captured()
        self.omit_custom = True
        envelope = {"format": "uoo-configuration", "version": 1,
                    "configuration": {"grub": {"GRUB_DEFAULT": self.custom()["id"]}, "custom_entries": [self.custom()]}}
        with self.assertRaises(RuntimeError):
            self.dispatch("import_configuration", data=envelope)
        self.assertEqual(self.captured(), before)

    def test_backup_deletion_requires_valid_integrity_and_leaves_configuration(self):
        backup = self.backup_id(self.dispatch("create_backup", kind="grub"))
        before = self.captured()
        self.dispatch("delete_backup", backup=backup)
        self.assertEqual(self.captured(), before)
        with self.assertRaises(ValueError):
            self.dispatch("restore", backup=backup)


if __name__ == "__main__":
    unittest.main()
