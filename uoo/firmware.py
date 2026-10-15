"""Verified firmware changes with protected current boot and recoverable state."""

import copy
from pathlib import Path
import re

from . import platform as facts


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-F]{4}", value):
        raise ValueError("Invalid UEFI boot entry identifier.")
    return value


class Firmware:
    def firmware(self):
        from .system import read_firmware
        value = read_firmware(self.paths, self.runner)
        if not value["available"]:
            raise RuntimeError(value["error"])
        return value

    def full_firmware_state(self):
        result = copy.deepcopy(self.firmware())
        result["restore_specs"] = {}
        for entry in result["entries"]:
            try:
                result["restore_specs"][entry["id"]] = self.creation_spec(entry)
            except (ValueError, RuntimeError, OSError):
                # Non-file firmware devices remain order/activation restorable;
                # the app refuses destructive deletion of such entries.
                continue
        return result

    @staticmethod
    def validate_order(order, firmware, preserve=False):
        if not isinstance(order, list) or not order or len(order) > 1024:
            raise ValueError("A nonempty boot order is required.")
        for entry in order:
            identifier(entry)
        if len(set(order)) != len(order):
            raise ValueError("The boot order contains duplicate entries.")
        known = {entry["id"]: entry for entry in firmware["entries"]}
        if not set(order) <= set(known):
            raise ValueError("A UEFI entry is no longer present. Reload before applying changes.")
        current = firmware.get("current")
        if current and current in known and current not in order:
            raise ValueError("The currently booted firmware entry must remain in the boot order.")
        if not any(known[entry]["active"] for entry in order):
            raise ValueError("At least one active entry must remain in the boot order.")
        if preserve and set(order) != set(firmware["order"]):
            raise ValueError("Reordering must preserve every entry in the current boot order.")

    def creation_spec(self, entry):
        path = entry.get("device_path", "")
        match = re.search(r"HD\((\d+),GPT,([0-9a-fA-F-]+),[^)]*\).*?File\(([^)]+)\)", path)
        if not match:
            raise ValueError("This firmware entry has no restorable local EFI loader path; deletion is disabled.")
        inventory = facts.devices(self.paths, self.runner)
        partition = next((node for node in inventory if node["partuuid"].lower() == match[2].lower()
                          and int(node.get("partn") or 0) == int(match[1])), None)
        if partition is None or not partition["pkname"]:
            raise ValueError("The disk for this firmware entry is unavailable; it cannot be safely restored.")
        loader = match[3].replace("\\", "/")
        self.check_loader(entry["label"], partition["pkname"], int(match[1]), loader)
        return {"label": entry["label"], "disk": partition["pkname"], "partition": int(match[1]),
                "loader": loader, "active": bool(entry["active"])}

    def check_loader(self, label, disk, partition, loader):
        if not isinstance(label, str) or not label.strip() or len(label) > 120 or any(ord(c) < 32 for c in label):
            raise ValueError("Use a nonempty firmware label of at most 120 characters.")
        if not isinstance(disk, str) or not re.fullmatch(r"/dev/[A-Za-z0-9_.+-]+", disk):
            raise ValueError("Select a whole disk from the device list.")
        if type(partition) is not int or not 1 <= partition <= 4096:
            raise ValueError("Invalid EFI partition number.")
        if (not isinstance(loader, str) or not re.fullmatch(r"/EFI/[A-Za-z0-9_ .+@/-]+\.efi", loader, re.I)
                or any(part in ("", ".", "..") for part in loader.split("/")[1:]) or len(loader) > 512):
            raise ValueError("Use an absolute /EFI/... .efi loader path without traversal.")
        inventory = facts.devices(self.paths, self.runner)
        parent = next((node for node in inventory if node["path"] == disk and node["type"] == "disk"), None)
        selected = next((node for node in inventory if node["pkname"] == disk and node.get("partn") == partition), None)
        if not parent or not selected or not selected["esp"] or selected["readonly"]:
            raise ValueError("Select a writable EFI System Partition on an existing whole disk.")
        if not selected["mountpoint"]:
            raise ValueError("Mount the selected EFI System Partition before creating or deleting its loader entry.")
        mount = Path(selected["mountpoint"]).resolve()
        file = (mount / loader.lstrip("/")).resolve()
        if not file.is_relative_to(mount) or not file.is_file():
            raise ValueError("The chosen EFI loader does not exist on the selected partition.")
        facts.verify_file_filesystem(file, selected["uuid"], self.paths, self.runner)
        with file.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("The selected file is not a PE/EFI executable.")
        return selected

    def check_entry_identity(self, entry, original=None, spec=None):
        if original:
            if entry["label"] != original.get("label", entry["label"]):
                raise ValueError("A firmware ID was reused or renamed after this backup; restoration is refused.")
            old_path = original.get("device_path")
            if old_path and entry.get("device_path", "").casefold() != old_path.casefold():
                raise ValueError("A firmware ID now points to a different loader; restoration is refused.")
        if spec:
            actual = self.creation_spec(entry)
            if any(actual[key] != spec[key] for key in ("label", "disk", "partition")) or actual["loader"].casefold() != spec["loader"].casefold():
                raise ValueError("A firmware ID now points to a different loader; restoration is refused.")

    def validate_firmware_restore(self, target):
        if not isinstance(target, dict):
            raise ValueError("Invalid firmware backup.")
        current = self.firmware()
        known = {entry["id"]: entry for entry in current["entries"]}
        specifications = target.get("restore_specs", {})
        for original in target.get("entries", []):
            entry = identifier(original["id"])
            if entry in known:
                self.check_entry_identity(known[entry], original, specifications.get(entry))
                if entry in (current["current"], current["next"]) and not original["active"]:
                    raise ValueError("A backup cannot deactivate a current or scheduled firmware boot entry.")
            else:
                recreate = target.get("recreate", {})
                spec = specifications.get(entry) or (recreate.get("spec") if recreate.get("id") == entry else None)
                if not spec:
                    raise ValueError("A firmware entry from this backup was removed and has no reconstruction data.")
                self.check_loader(spec["label"], spec["disk"], spec["partition"], spec["loader"])
        if "recreate" in target:
            entry = identifier(target["recreate"]["id"])
            spec = target["recreate"]["spec"]
            if entry in known:
                self.check_entry_identity(known[entry], spec=spec)
            else:
                self.check_loader(spec["label"], spec["disk"], spec["partition"], spec["loader"])
        for entry in target.get("remove", []):
            identifier(entry)
            if entry in (current["current"], current["next"]):
                raise ValueError("Clear a scheduled next boot first; a current boot entry cannot be removed during restoration.")
            if entry in known:
                spec = target.get("remove_specs", {}).get(entry)
                if not spec:
                    raise ValueError("The backup cannot prove the identity of the entry it would remove.")
                self.check_entry_identity(known[entry], spec=spec)
        return current

    def firmware_rollback_target(self, before, target):
        """Account for exact entries a restore may recreate before it fails."""
        result = copy.deepcopy(before)
        known = {entry["id"] for entry in before["entries"]}
        candidates = dict(target.get("restore_specs", {}))
        if target.get("recreate"):
            candidates[target["recreate"]["id"]] = target["recreate"]["spec"]
        newly_created = {entry: spec for entry, spec in candidates.items() if entry not in known}
        if newly_created:
            result["remove"] = sorted(newly_created)
            result["remove_specs"] = newly_created
        return result

    def restore_firmware_state(self, target):
        """Restore known boot variables; preserve entries added after the backup."""
        current = self.validate_firmware_restore(target)
        if "recreate" in target:
            item = target["recreate"]
            if item["id"] not in {entry["id"] for entry in current["entries"]}:
                spec = item["spec"]
                self.check_loader(spec["label"], spec["disk"], spec["partition"], spec["loader"])
                self.runner([self.paths.efibootmgr, "--create-only", "--bootnum", identifier(item["id"]),
                             "--disk", spec["disk"], "--part", str(spec["partition"]), "--label", spec["label"],
                             "--loader", spec["loader"].replace("/", "\\")], timeout=15)
                current = self.firmware()
        if "remove" in target:
            for entry in target["remove"]:
                identifier(entry)
                if entry == current["current"]:
                    raise ValueError("The current boot entry cannot be removed during rollback.")
                if entry in {item["id"] for item in current["entries"]}:
                    self.runner([self.paths.efibootmgr, "--bootnum", entry, "--delete-bootnum"], timeout=15)
            current = self.firmware()
        desired = {entry["id"]: entry for entry in target.get("entries", [])}
        known = {entry["id"]: entry for entry in current["entries"]}
        missing = set(desired) - set(known)
        specifications = target.get("restore_specs", {})
        if not missing <= set(specifications):
            raise ValueError("A firmware entry from this backup was removed; it cannot be recreated from an order-only backup.")
        for entry in sorted(missing):
            spec = specifications[entry]
            self.check_loader(spec["label"], spec["disk"], spec["partition"], spec["loader"])
            self.runner([self.paths.efibootmgr, "--create-only", "--bootnum", identifier(entry), "--disk", spec["disk"],
                         "--part", str(spec["partition"]), "--label", spec["label"],
                         "--loader", spec["loader"].replace("/", "\\")], timeout=15)
        if missing:
            current = self.firmware()
            known = {entry["id"]: entry for entry in current["entries"]}
        for entry, item in desired.items():
            if known[entry]["active"] != bool(item["active"]):
                if entry == current["current"] and not item["active"]:
                    raise ValueError("A backup cannot deactivate the currently booted firmware entry.")
                self.runner([self.paths.efibootmgr, "--bootnum", entry, "--active" if item["active"] else "--inactive"], timeout=15)
        if "order" in target:
            order = list(target["order"])
            if not isinstance(target["order"], list):
                raise ValueError("Invalid firmware backup order.")
            # Newly created firmware entries remain usable after an older restore.
            order += [entry for entry in current["order"] if entry not in order and entry not in target.get("remove", [])]
            if order:
                self.validate_order(order, self.firmware())
                if order != self.firmware()["order"]:
                    self.runner([self.paths.efibootmgr, "--bootorder", ",".join(order)], timeout=15)
            elif self.firmware()["order"]:
                self.runner([self.paths.efibootmgr, "--delete-bootorder"], timeout=15)
        if "next" in target:
            entry = target["next"]
            if entry:
                identifier(entry)
                if entry not in {item["id"] for item in self.firmware()["entries"]}:
                    raise ValueError("The backup's one-time firmware entry no longer exists.")
            if self.firmware()["next"] != entry:
                self.runner([self.paths.efibootmgr, *( ["--bootnext", entry] if entry else ["--delete-bootnext"] )], timeout=15)
        verified = self.firmware()
        for entry, item in desired.items():
            actual = next((node for node in verified["entries"] if node["id"] == entry), None)
            if not actual or actual["active"] != bool(item["active"]):
                raise RuntimeError("Restored firmware activation could not be verified.")
        if "next" in target and verified["next"] != target["next"]:
            raise RuntimeError("Restored one-time boot state could not be verified.")
        if "order" in target and verified["order"][:len(target["order"])] != target["order"]:
            raise RuntimeError("Restored firmware order could not be verified.")

    def firmware_transaction(self, before, arguments, verify, summary, kind="uefi"):
        identifier_backup = self.backup(kind, summary, {"firmware": before, "configuration": self.configuration()})
        self.store.journal({"version": 2, "files": {}, "firmware": before, "backup": identifier_backup})
        try:
            self.runner([self.paths.efibootmgr, *arguments], timeout=15)
            verify(self.firmware())
            self.store.finish()
        except Exception as exc:
            try:
                self.restore_firmware_state(before)
                self.store.finish()
            except Exception as rollback:
                raise RuntimeError(f"UEFI change failed: {exc}. Rollback also failed: {rollback}. "
                                   f"Backup: {identifier_backup}. Check firmware settings before restarting.") from exc
            raise RuntimeError(f"UEFI change failed; previous setting restored. {exc}") from exc
        return identifier_backup

    def apply_order(self, request):
        firmware = self.firmware()
        order = request.get("order")
        self.validate_order(order, firmware)
        def verify(actual):
            if actual["order"] != order:
                raise RuntimeError("The firmware did not retain the requested boot order.")
        self.firmware_transaction({"order": firmware["order"]}, ["--bootorder", ",".join(order)], verify,
                                  "UEFI boot order before editing", kind="uefi_order")
        return "UEFI boot order saved and verified."

    def next_boot(self, request):
        firmware = self.firmware()
        entry = request.get("entry")
        if not isinstance(entry, str):
            raise ValueError("Invalid UEFI boot entry identifier.")
        if entry:
            identifier(entry)
            selected = next((node for node in firmware["entries"] if node["id"] == entry), None)
            if not selected or not selected["active"]:
                raise ValueError("Select an existing active UEFI entry for the next boot.")
        if firmware["next"] == entry:
            return "The requested one-time boot choice is already in place."
        def verify(actual):
            if actual["next"] != entry:
                raise RuntimeError("The firmware did not retain the one-time boot choice.")
        self.firmware_transaction({"next": firmware["next"]}, ["--bootnext", entry] if entry else ["--delete-bootnext"], verify,
                                  "UEFI one-time choice before changing it", kind="next_boot")
        return "One-time boot choice saved." if entry else "One-time boot choice cleared."

    def set_active(self, request):
        entry = identifier(request.get("entry"))
        active = request.get("active")
        if type(active) is not bool:
            raise ValueError("The activation choice must be true or false.")
        firmware = self.firmware()
        selected = next((node for node in firmware["entries"] if node["id"] == entry), None)
        if not selected:
            raise ValueError("The firmware entry is no longer present.")
        if not active and (entry == firmware["current"] or entry == firmware["next"]):
            raise ValueError("Clear a scheduled next boot first; the current boot entry cannot be disabled.")
        if not active and not any(node["id"] != entry and node["active"] and node["id"] in firmware["order"] for node in firmware["entries"]):
            raise ValueError("At least one active firmware entry must remain in the boot order.")
        def verify(actual):
            current = next((node for node in actual["entries"] if node["id"] == entry), None)
            if not current or current["active"] != active:
                raise RuntimeError("Firmware activation did not persist.")
        self.firmware_transaction({"entries": [selected]}, ["--bootnum", entry, "--active" if active else "--inactive"], verify,
                                  "Firmware entry activation before editing", kind="uefi_active")
        return "Firmware activation saved and verified."

    def create_uefi(self, request):
        label, disk, part, loader = (request.get(key) for key in ("label", "disk", "partition", "loader"))
        self.check_loader(label, disk, part, loader)
        before = self.firmware()
        known = {node["id"] for node in before["entries"]}
        # Reserve a known free ID before the write so crash recovery can remove
        # exactly the new entry, never an unrelated firmware entry.
        entry = next((f"{index:04X}" for index in range(65536) if f"{index:04X}" not in known), None)
        if entry is None:
            raise RuntimeError("No free firmware boot identifiers are available.")
        saved = {"remove": [entry], "remove_specs": {entry: {"label": label, "disk": disk, "partition": part,
                                                          "loader": loader, "active": True}}, "order": before["order"]}
        def verify(actual):
            selected = next((node for node in actual["entries"] if node["id"] == entry), None)
            if not selected or selected["label"] != label or not selected["active"]:
                raise RuntimeError("The new firmware loader entry could not be verified.")
        self.firmware_transaction(saved, ["--create", "--bootnum", entry, "--disk", disk, "--part", str(part),
                                           "--label", label, "--loader", loader.replace("/", "\\")], verify,
                                  "Firmware configuration before creating a loader entry", kind="uefi_created")
        return f"Created firmware entry {entry}: {label}."

    def delete_uefi(self, request):
        entry = identifier(request.get("entry"))
        if request.get("confirm") != entry:
            raise ValueError("Type the exact firmware entry ID to confirm removal.")
        firmware = self.firmware()
        selected = next((node for node in firmware["entries"] if node["id"] == entry), None)
        if not selected:
            raise ValueError("The firmware entry is no longer present.")
        if entry == firmware["current"] or entry == firmware["next"]:
            raise ValueError("The current or scheduled next-boot entry cannot be removed.")
        order = [item for item in firmware["order"] if item != entry]
        self.validate_order(order, firmware)
        spec = self.creation_spec(selected)
        saved = {"recreate": {"id": entry, "spec": spec}, "entries": [selected], "order": firmware["order"]}
        def verify(actual):
            if entry in {node["id"] for node in actual["entries"]}:
                raise RuntimeError("The firmware entry was not removed.")
        self.firmware_transaction(saved, ["--bootnum", entry, "--delete-bootnum"], verify,
                                  "Firmware loader before removing an entry", kind="uefi_deleted")
        return "Firmware entry removed. Its loader file was preserved, and the entry can be restored from the backup."

    def restore_firmware_backup(self, target):
        original = self.firmware()
        before = self.firmware_rollback_target(original, target)
        self.backup("uefi", "Firmware before restoring a backup", {"firmware": before, "configuration": self.configuration()})
        self.store.journal({"version": 2, "files": {}, "firmware": before})
        try:
            self.restore_firmware_state(target)
            self.store.finish()
        except Exception:
            self.restore_firmware_state(before)
            self.store.finish()
            raise
