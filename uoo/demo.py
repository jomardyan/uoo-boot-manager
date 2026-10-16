"""An in-memory boot-manager simulator with no platform or filesystem access."""

import base64
import binascii
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import re

from .core import DEMO_CONFIG, parse_entries
from .dualboot import UBUNTU_LABEL, WINDOWS_ENTRY_TITLE, WINDOWS_LOADER, analyze
from .models import (compile_custom_entries, validate_custom_entries,
                     validate_grub_changes, validate_png)


GRUB_KEYS = ("grub", "custom_entries", "background", "background_info", "grub_next", "grub_saved", "managed")


class DemoManager:
    """Exercise the complete UI safely; all state lasts only for this instance."""

    def __init__(self):
        self._grub = {
            "GRUB_DEFAULT": "ubuntu", "GRUB_TIMEOUT": 10, "GRUB_TIMEOUT_STYLE": "menu",
            "GRUB_SAVEDEFAULT": False, "GRUB_CMDLINE_LINUX": "",
            "GRUB_CMDLINE_LINUX_DEFAULT": "quiet splash", "GRUB_DISABLE_OS_PROBER": True,
            "GRUB_DISABLE_RECOVERY": False, "GRUB_GFXMODE": "auto",
            "GRUB_GFXPAYLOAD_LINUX": "keep", "GRUB_COLOR_NORMAL": "light-gray/black",
            "GRUB_COLOR_HIGHLIGHT": "white/blue", "GRUB_RECORDFAIL_TIMEOUT": 30,
        }
        devices = [
            {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 512000000000,
             "fstype": "", "uuid": "", "mountpoint": "", "mountpoints": [],
             "pkname": "", "partn": None, "parttype": "", "readonly": False, "esp": False},
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 536870912,
             "fstype": "vfat", "uuid": "ABCD-1234", "mountpoint": "/boot/efi",
             "mountpoints": ["/boot/efi"], "pkname": "/dev/sda", "partn": 1,
             "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "readonly": False, "esp": True},
            {"name": "sda2", "path": "/dev/sda2", "type": "part", "size": 511463129088,
             "fstype": "ext4", "uuid": "12345678-1234-1234-abcd-123456789abc",
             "mountpoint": "/", "mountpoints": ["/"], "pkname": "/dev/sda", "partn": 2,
             "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4", "readonly": False, "esp": False},
            {"name": "sda3", "path": "/dev/sda3", "type": "part", "size": 128849018880,
             "fstype": "BitLocker", "uuid": "", "mountpoint": "", "mountpoints": [],
             "pkname": "/dev/sda", "partn": 3,
             "parttype": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "readonly": False, "esp": False},
        ]
        self._state = {
            "entries": [], "settings": {}, "firmware": {
                "entries": [{"id": "0000", "label": "Ubuntu", "active": True},
                            {"id": "0001", "label": "Windows Boot Manager", "active": True},
                            {"id": "0002", "label": "USB drive", "active": True}],
                "order": ["0000", "0001", "0002"], "current": "0000", "next": "",
                "available": True, "error": "",
            }, "errors": [], "demo": True, "managed": False, "devices": devices,
            "custom_entries": [], "background": False, "background_info": {},
            "grub_next": "", "grub_saved": "ubuntu",
            "capabilities": {key: True for key in ("grub", "uefi", "grub_next", "custom_entries", "repair", "background")},
            "diagnostics": {
                "system": {"distribution": "Ubuntu 24.04 LTS (demo)", "kernel": "6.8.0-generic",
                           "architecture": "x86_64", "boot_mode": "UEFI", "secure_boot": "disabled (demo)"},
                "checks": [{"name": "GRUB configuration", "status": "ok", "detail": "Demo boot menu is valid."},
                           {"name": "EFI System Partition", "status": "ok", "detail": "/dev/sda1 is mounted at /boot/efi."},
                           {"name": "UEFI firmware", "status": "ok", "detail": "Three simulated boot entries are available."},
                           {"name": "Demo mode", "status": "info", "detail": "Changes stay in memory for this session."}],
                "devices": copy.deepcopy(devices), "environment_supported": True,
            },
        }
        self._records = []
        self._specifications = {
            identifier: {"label": label, "disk": "/dev/sda", "partition": 1,
                         "loader": loader, "active": True}
            for identifier, label, loader in (
                ("0000", "Ubuntu", "/EFI/ubuntu/shimx64.efi"),
                ("0001", "Windows Boot Manager", "/EFI/Microsoft/Boot/bootmgfw.efi"),
                ("0002", "USB drive", "/EFI/BOOT/BOOTX64.EFI"),
            )
        }
        self._counter = 0
        self._sync()

    def _sync(self):
        self._state["settings"] = {
            key: ("true" if value else "false") if type(value) is bool else str(value)
            for key, value in self._grub.items()
        }
        if self._grub.get("GRUB_DEFAULT") == "saved":
            self._state["settings"]["UOO_SAVED_ENTRY"] = self._state["grub_saved"]
        self._state["entries"] = [asdict(entry) for entry in self._menu()]

    def _menu(self, custom=None):
        return parse_entries(DEMO_CONFIG + "\n" + compile_custom_entries(
            self._state["custom_entries"] if custom is None else custom))

    def _configuration(self):
        return {"grub": copy.deepcopy(self._grub), "custom_entries": copy.deepcopy(self._state["custom_entries"]),
                "firmware": copy.deepcopy(self._state["firmware"])}

    def _grub_state(self, keys=GRUB_KEYS):
        """Copy only the GRUB pieces a real backup of the same action captures."""
        return {key: copy.deepcopy(self._grub if key == "grub" else self._state[key]) for key in keys}

    def _entry_spec(self, entry):
        spec = copy.deepcopy(self._specifications[entry["id"]])
        spec.update(label=entry["label"], active=entry["active"])
        return spec

    def _firmware_state(self):
        firmware = copy.deepcopy(self._state["firmware"])
        firmware["restore_specs"] = {entry["id"]: self._entry_spec(entry) for entry in firmware["entries"]}
        return firmware

    def _backup(self, kind, summary, data):
        self._counter += 1
        created = datetime(2026, 10, 14, 12, tzinfo=timezone.utc) + timedelta(seconds=self._counter)
        record = {"id": f"demo-{self._counter:04d}", "created": created.isoformat(),
                  "kind": kind, "summary": summary, "data": copy.deepcopy(data),
                  "configuration": self._configuration()}
        self._records.insert(0, record)
        return record["id"]

    def _metadata(self):
        return [{key: record[key] for key in ("id", "created", "kind", "summary")} for record in self._records]

    def _record(self, identifier):
        if type(identifier) is not str:
            raise ValueError("Choose a valid demo backup.")
        record = next((record for record in self._records if record["id"] == identifier), None)
        if record is None:
            raise ValueError("The demo backup does not exist.")
        return record

    def _valid_selector(self, entry, custom=None, allow_saved=False, positions=False):
        # GRUB_DEFAULT may also be a menu position such as "0" or "1>0"; one-time choices may not.
        known = {value for item in self._menu(custom) for value in (item.selector, item.position)[:2 if positions else 1]}
        if type(entry) is not str or (entry not in known and not (allow_saved and entry == "saved")):
            raise ValueError("Choose an existing GRUB boot entry.")

    def _resolve(self, value):
        return next((item.selector for item in self._menu() if item.position == value), value)

    def _firmware_entry(self, identifier):
        if type(identifier) is not str:
            raise ValueError("Choose an existing firmware entry.")
        entry = next((item for item in self._state["firmware"]["entries"] if item["id"] == identifier), None)
        if entry is None:
            raise ValueError("Choose an existing firmware entry.")
        return entry

    def _valid_order(self, order, firmware=None):
        firmware = self._state["firmware"] if firmware is None else firmware
        inventory = {entry["id"]: entry for entry in firmware["entries"]}
        if type(order) is not list or not order or any(type(item) is not str for item in order):
            raise ValueError("Boot order must be a nonempty list of firmware IDs.")
        if len(order) != len(set(order)) or any(item not in inventory for item in order):
            raise ValueError("Boot order has duplicate or unknown entries.")
        if firmware["current"] and firmware["current"] not in order:
            raise ValueError("Keep the current firmware boot entry in the order.")
        if not any(inventory[item]["active"] for item in order):
            raise ValueError("Keep at least one active firmware entry in the order.")

    def _restore_firmware(self, old):
        current = copy.deepcopy(self._state["firmware"])
        inventory = {entry["id"]: entry for entry in current["entries"]}
        specifications = copy.deepcopy(self._specifications)
        restore_specs = copy.deepcopy(old.get("restore_specs", {}))
        if "recreate" in old:
            restore_specs[old["recreate"]["id"]] = copy.deepcopy(old["recreate"]["spec"])

        def check_identity(identifier, spec=None, original=None):
            if identifier not in inventory:
                return
            if original is not None and inventory[identifier]["label"] != original["label"]:
                raise ValueError("A firmware ID was reused for a different entry; reload before restoring.")
            if spec is not None:
                actual = self._entry_spec(inventory[identifier])
                if any(actual[key] != spec[key] for key in ("label", "disk", "partition", "loader")):
                    raise ValueError("A firmware ID was reused for a different loader; reload before restoring.")

        for identifier in old.get("remove", []):
            if identifier in (current["current"], current["next"]):
                raise ValueError("Clear a scheduled next boot first; the current entry cannot be removed during restore.")
            check_identity(identifier, old.get("remove_specs", {}).get(identifier))
            inventory.pop(identifier, None)
        if "recreate" in old:
            identifier = old["recreate"]["id"]
            spec = restore_specs[identifier]
            check_identity(identifier, spec)
            if identifier not in inventory:
                inventory[identifier] = {"id": identifier, "label": spec["label"], "active": spec["active"]}
                specifications[identifier] = copy.deepcopy(spec)
        for entry in old.get("entries", []):
            identifier = entry["id"]
            spec = restore_specs.get(identifier)
            check_identity(identifier, spec, entry)
            if identifier not in inventory:
                if spec is None:
                    raise ValueError("A saved firmware entry has no loader reconstruction data.")
                inventory[identifier] = copy.deepcopy(entry)
                specifications[identifier] = copy.deepcopy(spec)
            if not entry["active"] and identifier in (current["current"], current["next"]):
                raise ValueError("A backup cannot deactivate a current or scheduled firmware boot entry.")
            inventory[identifier]["active"] = entry["active"]
        current["entries"] = sorted(inventory.values(), key=lambda entry: entry["id"])
        current["order"] = [identifier for identifier in current["order"] if identifier in inventory]
        if "order" in old:
            current["order"] = old["order"] + [identifier for identifier in current["order"] if identifier not in old["order"]]
        self._valid_order(current["order"], current)
        if "next" in old:
            if old["next"] and old["next"] not in inventory:
                raise ValueError("The saved one-time firmware entry no longer exists.")
            if old["next"] and not inventory[old["next"]]["active"]:
                raise ValueError("A backup cannot schedule an inactive firmware entry for the next boot.")
            current["next"] = old["next"]
        return current, specifications

    def _rollback_target(self, old):
        """Like the real helper: the current inventory, removing entries the restore recreates."""
        before = copy.deepcopy(self._state["firmware"])
        known = {entry["id"] for entry in before["entries"]}
        candidates = dict(old.get("restore_specs", {}))
        if "recreate" in old:
            candidates[old["recreate"]["id"]] = old["recreate"]["spec"]
        created = {identifier: copy.deepcopy(spec) for identifier, spec in candidates.items() if identifier not in known}
        if created:
            before.update(remove=sorted(created), remove_specs=created)
        return before

    @staticmethod
    def _validate_envelope(data):
        if type(data) is not dict or set(data) != {"format", "version", "configuration"}:
            raise ValueError("Choose a version 1 Uoo configuration export.")
        if data["format"] != "uoo-configuration" or type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Choose a version 1 Uoo configuration export.")
        config = data["configuration"]
        if type(config) is not dict or not {"grub", "custom_entries"} <= set(config) or set(config) - {"grub", "custom_entries", "firmware"}:
            raise ValueError("Configuration must contain GRUB settings and custom entries, with optional firmware inventory.")
        grub = validate_grub_changes(config["grub"])
        custom = validate_custom_entries(config["custom_entries"])
        if "firmware" in config:
            firmware = config["firmware"]
            if type(firmware) is not dict or set(firmware) - {"entries", "order", "current", "next", "available", "error"}:
                raise ValueError("Invalid exported firmware inventory.")
            entries = firmware.get("entries")
            order = firmware.get("order")
            if type(entries) is not list or type(order) is not list:
                raise ValueError("Invalid exported firmware inventory.")
            ids = set()
            for entry in entries:
                if type(entry) is not dict or set(entry) != {"id", "label", "active"}:
                    raise ValueError("Invalid exported firmware entry.")
                identifier = entry["id"]
                if type(identifier) is not str or not re.fullmatch(r"[0-9A-F]{4}", identifier) or identifier in ids:
                    raise ValueError("Invalid exported firmware entry ID.")
                if type(entry["label"]) is not str or not 1 <= len(entry["label"]) <= 255 or any(ord(char) < 32 for char in entry["label"]):
                    raise ValueError("Invalid exported firmware entry label.")
                if type(entry["active"]) is not bool:
                    raise ValueError("Invalid exported firmware entry state.")
                ids.add(identifier)
            if any(type(item) is not str or item not in ids for item in order) or len(order) != len(set(order)):
                raise ValueError("Invalid exported firmware order.")
            for key in ("current", "next"):
                if key in firmware and (type(firmware[key]) is not str or firmware[key] and firmware[key] not in ids):
                    raise ValueError("Invalid exported firmware selection.")
            if "available" in firmware and type(firmware["available"]) is not bool:
                raise ValueError("Invalid exported firmware availability.")
            if "error" in firmware and type(firmware["error"]) is not str:
                raise ValueError("Invalid exported firmware error.")
        return grub, custom

    @staticmethod
    def _repair_plan():
        return {"id": "demo-repair", "mode": "uefi", "target": "/boot/efi", "confirmation": "/boot/efi",
                "summary": ["Back up the current supported configuration.",
                            "Reinstall the Ubuntu EFI bootloader on /dev/sda1.",
                            "Rebuild and validate the GRUB menu. No restart is performed."],
                "supported": True, "reason": ""}

    def _dual_boot_plan(self):
        """Analyze the demo's own state, so the plan reacts to what the user changes."""
        firmware = self._state["firmware"]
        label = lambda entry: entry["label"].lower()
        state = {"grub_installed": True, "uefi": True, "secure_boot": "disabled (demo)",
                 "grub_has_windows": any("windows" in entry.title.lower() for entry in self._menu()),
                 "grub_style": str(self._grub["GRUB_TIMEOUT_STYLE"]), "grub_timeout": int(self._grub["GRUB_TIMEOUT"]),
                 "os_prober_disabled": bool(self._grub["GRUB_DISABLE_OS_PROBER"]), "os_prober_installed": True,
                 "windows_firmware": [entry["id"] for entry in firmware["entries"] if label(entry).startswith("windows boot manager")],
                 "windows_loader": {"uuid": "ABCD-1234", "device": "/dev/sda1", "loader": WINDOWS_LOADER},
                 "ubuntu_loader": {"loader": "/EFI/ubuntu/shimx64.efi", "disk": "/dev/sda", "partition": 1},
                 "ubuntu_entries": [{"id": entry["id"], "active": entry["active"]} for entry in firmware["entries"] if label(entry) == UBUNTU_LABEL],
                 "firmware_available": True, "firmware_order": list(firmware["order"]),
                 "bitlocker": ["/dev/sda3"], "luks": [], "ntfs": []}
        return analyze(state)

    def _dual_boot_fix(self, request):
        plan = self._dual_boot_plan()
        if request.get("plan") != plan["id"]:
            raise ValueError("The demo dual-boot plan changed. Check dual boot again and review the new plan.")
        available = {item["id"]: item for item in plan["steps"]}
        chosen = request.get("steps")
        if chosen is None:
            chosen = [item["id"] for item in plan["steps"] if item["recommended"]]
        if (type(chosen) is not list or len(set(chosen)) != len(chosen)
                or not all(type(item) is str and item in available for item in chosen)):
            raise ValueError("Choose only steps from the reviewed dual-boot plan.")
        if not chosen:
            raise ValueError("Select at least one step to apply.")
        if type(request.get("acknowledge_recovery", False)) is not bool:
            raise ValueError("The recovery-key acknowledgement must be true or false.")
        if plan["requires_acknowledgement"] and not request.get("acknowledge_recovery"):
            raise ValueError("A BitLocker-encrypted volume was found. Confirm that you have its recovery key before changing boot settings. "
                             "Uoo will not change encryption.")
        backup = self._backup("all", "Complete demo configuration before the dual-boot fix",
                              {**self._grub_state(), "firmware": self._firmware_state()})
        firmware = self._state["firmware"]
        if "grub_menu" in chosen:
            self._grub["GRUB_TIMEOUT_STYLE"] = "menu"
            if self._grub["GRUB_TIMEOUT"] == 0:
                self._grub["GRUB_TIMEOUT"] = 10
        if "os_prober" in chosen:
            self._grub["GRUB_DISABLE_OS_PROBER"] = False
        if "grub_chainloader" in chosen:
            entry = {"id": "uoo-" + "d" * 32, "title": WINDOWS_ENTRY_TITLE, "type": "efi", "enabled": True,
                     "uuid": "ABCD-1234", "loader": WINDOWS_LOADER}
            self._state["custom_entries"] = [item for item in self._state["custom_entries"] if item["id"] != entry["id"]] + [entry]
        if "uefi_create_ubuntu" in chosen:
            used = {item["id"] for item in firmware["entries"]}
            identifier = next(f"{number:04X}" for number in range(65536) if f"{number:04X}" not in used)
            self._specifications[identifier] = {"label": UBUNTU_LABEL, "disk": "/dev/sda", "partition": 1,
                                                "loader": "/EFI/ubuntu/shimx64.efi", "active": True}
            firmware["entries"].append({"id": identifier, "label": UBUNTU_LABEL, "active": True})
            firmware["entries"].sort(key=lambda item: item["id"])
            if plan["encrypted"]["bitlocker"]:
                firmware["order"].append(identifier)
            else:
                firmware["order"].insert(0, identifier)
        if "uefi_order" in chosen:
            ubuntu = next(item for item in firmware["entries"] if item["label"].lower() == UBUNTU_LABEL)
            ubuntu["active"] = True
            firmware["order"] = [ubuntu["id"]] + [item for item in firmware["order"] if item != ubuntu["id"]]
        self._state["managed"] = True
        return {"backup": backup, "applied": [available[name]["title"] for name in chosen],
                "message": "Demo dual-boot fix applied: " + "; ".join(available[name]["title"] for name in chosen) +
                           f". Pre-fix backup: {backup}. No restart was performed."}

    def dispatch(self, request):
        """Validate and simulate one action, returning independently copied data."""
        if type(request) is not dict or type(request.get("action")) is not str:
            raise ValueError("Expected an action object.")
        action = request["action"]
        allowed = {
            "snapshot": set(), "list_backups": set(), "apply_grub": {"changes", "entry", "timeout", "style"},
            "grub_next_boot": {"entry"}, "refresh_grub": set(), "custom_entries": {"entries"},
            "apply_order": {"order"}, "next_boot": {"entry"}, "set_active": {"entry", "active"},
            "create_uefi": {"label", "disk", "partition", "loader"}, "delete_uefi": {"entry", "confirm"},
            "apply_background": {"png"}, "clear_background": set(), "create_backup": {"kind"},
            "backup_details": {"backup"}, "export_backup": {"backup"}, "import_configuration": {"data"},
            "delete_backup": {"backup"}, "restore": {"backup"}, "repair_plan": set(), "repair": {"plan", "confirm"},
            "dual_boot_plan": set(), "dual_boot_fix": {"plan", "steps", "acknowledge_recovery"},
        }
        if action not in allowed or set(request) - allowed[action] - {"action"}:
            raise ValueError("Unsupported action or request field.")
        extra = {}
        message = "Demo boot options reloaded."
        if action in ("snapshot", "list_backups"):
            message = "Demo boot options reloaded." if action == "snapshot" else "Demo backups loaded."
        elif action == "apply_grub":
            if "changes" in request:
                if set(request) != {"action", "changes"}:
                    raise ValueError("Supply settings changes or legacy fields, not both.")
                changes = validate_grub_changes(request["changes"])
            else:
                changes = validate_grub_changes({"GRUB_DEFAULT": request.get("entry"),
                                                "GRUB_TIMEOUT": request.get("timeout"),
                                                "GRUB_TIMEOUT_STYLE": request.get("style"), "GRUB_SAVEDEFAULT": False})
            if not changes:
                raise ValueError("No GRUB setting changes were supplied.")
            merged = {**self._grub, **changes}
            if merged["GRUB_SAVEDEFAULT"]:
                merged["GRUB_DEFAULT"] = "saved"
            self._valid_selector(merged["GRUB_DEFAULT"], allow_saved=True, positions=True)
            self._backup("grub", "GRUB settings before applying changes", self._grub_state(("grub", "managed")))
            self._grub = merged
            self._state["managed"] = True
            message = "Demo GRUB settings applied."
        elif action == "grub_next_boot":
            entry = request.get("entry")
            if entry != "":
                self._valid_selector(entry)
            self._backup("grub_next", "GRUB one-time choice before changing it", {"grub_next": self._state["grub_next"]})
            self._state["grub_next"] = entry
            message = "Demo GRUB one-time choice updated."
        elif action == "refresh_grub":
            self._backup("grub", "GRUB configuration before rebuilding", {})
            message = "Demo GRUB menu rebuilt."
        elif action == "custom_entries":
            custom = validate_custom_entries(request.get("entries"))
            self._valid_selector(self._resolve(self._grub["GRUB_DEFAULT"]), custom, allow_saved=True)
            if self._state["grub_next"]:
                self._valid_selector(self._state["grub_next"], custom)
            if self._grub["GRUB_DEFAULT"] == "saved":
                self._valid_selector(self._resolve(self._state["grub_saved"]), custom)
            self._backup("grub", "Custom entries before changing them", self._grub_state(("custom_entries",)))
            self._state["custom_entries"] = custom
            message = "Demo custom entries applied."
        elif action == "apply_order":
            order = request.get("order")
            self._valid_order(order)
            self._backup("uefi_order", "UEFI boot order before changing it", {"firmware": {"order": self._state["firmware"]["order"]}})
            self._state["firmware"]["order"] = list(order)
            message = "Demo firmware boot order saved."
        elif action == "next_boot":
            entry = request.get("entry")
            if entry != "":
                if not self._firmware_entry(entry)["active"]:
                    raise ValueError("Choose an existing active firmware entry for the next boot.")
            if self._state["firmware"]["next"] == entry:
                message = "The requested demo one-time boot choice is already in place."
            else:
                self._backup("next_boot", "UEFI one-time choice before changing it", {"firmware": {"next": self._state["firmware"]["next"]}})
                self._state["firmware"]["next"] = entry
                message = "Demo firmware one-time choice updated."
        elif action == "set_active":
            entry = self._firmware_entry(request.get("entry"))
            active = request.get("active")
            if type(active) is not bool:
                raise ValueError("Firmware active must be true or false.")
            firmware = copy.deepcopy(self._state["firmware"])
            if not active and entry["id"] in (firmware["current"], firmware["next"]):
                raise ValueError("Clear a scheduled next boot first; the current boot entry cannot be disabled.")
            next(item for item in firmware["entries"] if item["id"] == entry["id"])["active"] = active
            self._valid_order(firmware["order"], firmware)
            self._backup("uefi_active", "Firmware entry before changing its state",
                         {"firmware": {"entries": [entry], "restore_specs": {entry["id"]: self._entry_spec(entry)}}})
            entry["active"] = active
            message = "Demo firmware entry state updated."
        elif action == "create_uefi":
            label, loader = request.get("label"), request.get("loader")
            if (type(label) is not str or not label.strip() or label != label.strip() or len(label) > 120
                    or any(ord(char) < 32 for char in label)):
                raise ValueError("Use a nonempty firmware label of at most 120 characters.")
            if request.get("disk") != "/dev/sda" or type(request.get("partition")) is not int or request["partition"] != 1:
                raise ValueError("Choose the demo EFI partition /dev/sda1.")
            if (type(loader) is not str or not re.fullmatch(r"/EFI/[A-Za-z0-9_ .+@/-]+\.efi", loader, re.I)
                    or any(part in ("", ".", "..") for part in loader.split("/")[1:]) or len(loader) > 512):
                raise ValueError("Use an absolute /EFI/... .efi loader path without traversal.")
            firmware = self._state["firmware"]
            used = {entry["id"] for entry in firmware["entries"]}
            identifier = next((f"{number:04X}" for number in range(65536) if f"{number:04X}" not in used), None)
            if identifier is None:
                raise ValueError("No demo firmware IDs remain.")
            spec = {"label": request["label"], "disk": request["disk"], "partition": request["partition"],
                    "loader": request["loader"], "active": True}
            self._backup("uefi_created", "Firmware configuration before adding a loader",
                         {"firmware": {"remove": [identifier], "remove_specs": {identifier: spec}, "order": firmware["order"]}})
            self._specifications[identifier] = copy.deepcopy(spec)
            firmware["entries"].append({"id": identifier, "label": request["label"], "active": True})
            firmware["entries"].sort(key=lambda entry: entry["id"])
            firmware["order"].insert(0, identifier)
            message = f"Demo firmware loader {identifier} added."
        elif action == "delete_uefi":
            entry = self._firmware_entry(request.get("entry"))
            firmware = self._state["firmware"]
            if entry["id"] in (firmware["current"], firmware["next"]) or request.get("confirm") != entry["id"]:
                raise ValueError("Confirm the firmware ID; current or scheduled next-boot entries cannot be deleted.")
            order = [item for item in firmware["order"] if item != entry["id"]]
            self._valid_order(order)
            self._backup("uefi_deleted", "Firmware loader before deleting its entry",
                         {"firmware": {"recreate": {"id": entry["id"], "spec": self._entry_spec(entry)},
                                       "entries": [entry], "order": firmware["order"]}})
            firmware["entries"].remove(entry)
            firmware["order"] = order
            if firmware["next"] == entry["id"]:
                firmware["next"] = ""
            message = "Demo firmware entry deleted."
        elif action == "apply_background":
            encoded = request.get("png")
            if type(encoded) is not str or len(encoded) > 3 * 1024 * 1024:
                raise ValueError("Background must be a base64 PNG no larger than 2 MiB.")
            try:
                image = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Background PNG encoding is invalid.") from exc
            width, height = validate_png(image)
            self._backup("grub", "GRUB appearance before changing its background",
                         self._grub_state(("grub", "managed", "background", "background_info")))
            self._state["managed"] = self._state["background"] = True
            self._state["background_info"] = {"width": width, "height": height, "path": "/boot/grub/uoo-background.png"}
            message = "Demo background applied."
        elif action == "clear_background":
            self._backup("grub", "GRUB appearance before clearing its background",
                         self._grub_state(("grub", "managed", "background", "background_info")))
            self._state["managed"] = True
            self._state["background"] = False
            self._state["background_info"] = {}
            message = "Demo background cleared."
        elif action == "create_backup":
            kind = request.get("kind")
            if kind not in ("grub", "uefi", "all"):
                raise ValueError("Backup scope must be grub, uefi or all.")
            data = self._grub_state() if kind in ("grub", "all") else {}
            if kind in ("uefi", "all"):
                data["firmware"] = self._firmware_state()
            extra["backup"] = self._backup(kind, "Manual demo configuration backup", data)
            message = "Demo backup created."
        elif action in ("backup_details", "export_backup", "delete_backup", "restore"):
            record = self._record(request.get("backup"))
            if action == "backup_details":
                extra["backup"] = {key: record[key] for key in ("id", "created", "kind", "summary")}
                extra["configuration"] = record["configuration"]
                message = "Demo backup details loaded."
            elif action == "export_backup":
                extra["data"] = json.dumps({"format": "uoo-configuration", "version": 1,
                                            "configuration": record["configuration"]}, ensure_ascii=False, indent=2)
                message = "Demo configuration exported."
            elif action == "delete_backup":
                self._records.remove(record)
                message = "Demo backup deleted."
            else:
                data = record["data"]
                pieces = tuple(key for key in GRUB_KEYS if key in data)
                firmware = None
                specifications = None
                if "firmware" in data:
                    firmware, specifications = self._restore_firmware(data["firmware"])
                if pieces:
                    after = {**self._grub_state(), **data}
                    default = after["grub"]["GRUB_DEFAULT"]
                    self._valid_selector((after["grub_saved"] or "0") if default == "saved" else default,
                                         after["custom_entries"], positions=True)
                    if after["grub_next"]:
                        self._valid_selector(after["grub_next"], after["custom_entries"])
                current = self._grub_state(pieces)
                if firmware is not None:
                    current["firmware"] = self._rollback_target(data["firmware"])
                # Firmware-only restores snapshot the firmware as "uefi"; file restores reuse the backup's kind.
                kind = "uefi" if firmware is not None and not pieces else record["kind"]
                self._backup(kind, "Current demo configuration before restoring a backup", current)
                for key in pieces:
                    if key == "grub":
                        self._grub = copy.deepcopy(data["grub"])
                    else:
                        self._state[key] = copy.deepcopy(data[key])
                if firmware is not None:
                    self._state["firmware"] = firmware
                    if specifications is not None:
                        self._specifications = specifications
                message = "Demo backup restored; the previous state was backed up first."
        elif action == "import_configuration":
            grub, custom = self._validate_envelope(request.get("data"))
            merged = {**self._grub, **grub}
            self._valid_selector(merged["GRUB_DEFAULT"], custom, allow_saved=True, positions=True)
            if merged["GRUB_DEFAULT"] == "saved":
                self._valid_selector(self._state["grub_saved"], custom)
            self._backup("grub", "GRUB configuration before importing settings",
                         self._grub_state(("grub", "custom_entries", "managed")))
            self._grub = merged
            self._state["custom_entries"] = custom
            if self._state["grub_next"]:
                try:
                    self._valid_selector(self._state["grub_next"], custom)
                except ValueError:
                    self._state["grub_next"] = ""
            self._state["managed"] = True
            message = "Demo GRUB configuration imported; firmware remains unchanged."
        elif action == "dual_boot_plan":
            extra["plan"] = self._dual_boot_plan()
            message = "Demo dual-boot check completed."
        elif action == "dual_boot_fix":
            extra = self._dual_boot_fix(request)
            message = extra.pop("message")
        elif action == "repair_plan":
            extra["plan"] = self._repair_plan()
            message = "Demo repair plan inspected."
        elif action == "repair":
            if request.get("plan") != "demo-repair" or request.get("confirm") != "/boot/efi":
                raise ValueError("Inspect the demo repair plan and confirm /boot/efi exactly.")
            # The real backup holds only EFI loader files and the regenerated menu, which the demo does not model.
            self._backup("uefi_repair", "Ubuntu EFI loaders before simulated reinstallation", {})
            message = "Demo bootloader repair completed. No restart was performed."
        self._sync()
        return copy.deepcopy({"ok": True, "snapshot": self._state, "backups": self._metadata(),
                              "message": message, **extra})
