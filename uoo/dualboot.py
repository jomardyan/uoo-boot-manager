"""Dual-boot diagnosis and a reviewed fix built only from Uoo's existing verified operations.

The plan is always computed from live state inside the privileged helper. A fix
request must quote the plan identifier it was shown, so a stale or tampered plan
is refused. Encryption is never changed: BitLocker and LUKS volumes only make the
plan more cautious and require the recovery key to be acknowledged.

The checks follow the failures dual-boot users report most often: Windows missing
from the GRUB menu (os-prober is off by default since GRUB 2.06), a hidden menu,
Windows updates moving Windows Boot Manager ahead of GRUB, a lost firmware entry,
a read-only or full EFI System Partition, firmware variable storage problems,
mixed BIOS/UEFI boot modes, overrides in other GRUB drop-ins, and BitLocker.
"""

import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

from . import platform as facts
from .core import parse_entries, parse_settings
from .models import validate_custom_entries

WINDOWS_LOADER = "/EFI/Microsoft/Boot/bootmgfw.efi"
UBUNTU_LOADERS = ("/EFI/ubuntu/shimx64.efi", "/EFI/ubuntu/grubx64.efi")
FALLBACK_LOADERS = ("/EFI/BOOT/BOOTX64.EFI", "/EFI/BOOT/BOOTAA64.EFI")
STEP_ORDER = ("grub_menu", "os_prober", "grub_chainloader", "uefi_create_ubuntu", "uefi_order")
WINDOWS_ENTRY_TITLE = "Windows Boot Manager"
UBUNTU_LABEL = "ubuntu"
LOW_ESP_BYTES = 8 * 1024 * 1024
MIB = 1024 * 1024
# efibootmgr 17 prints File(\EFI\...\x.efi); 18 and later print the bare path. Both contain these fragments.
WINDOWS_PATH = re.compile(r"bootmgfw\.efi", re.I)
UBUNTU_PATH = re.compile(r"\\EFI\\ubuntu\\(?:shim|grub)", re.I)

DEFAULTS = {
    "grub_installed": True, "uefi": True, "secure_boot": "unknown", "grub_has_windows": False,
    "grub_style": "menu", "grub_timeout": 5, "os_prober_disabled": True, "os_prober_installed": True,
    "windows_firmware": [], "windows_loader": None, "windows_loader_count": 0, "ubuntu_loader": None,
    "ubuntu_entries": [], "firmware_available": False, "firmware_order": [], "firmware_error": "",
    "bitlocker": [], "luks": [], "ntfs": [], "esp_present": False, "unmounted_esps": [],
    "esp_free_mib": None, "esp_low": False, "efivars_readonly": False, "boot_readonly": False,
    "grub_conflicts": {}, "grub_read_error": "", "storage_error": "", "pending_recovery": False,
    "fallback_loader": None,
}
# Values that change between two reads without changing what the fix does.
VOLATILE = ("esp_free_mib",)


def analyze(state):
    """Turn gathered facts into findings and an ordered list of fixable steps. No I/O."""
    state = {**DEFAULTS, **state}
    findings, steps = [], []
    encrypted = bool(state["bitlocker"])
    uefi = state["uefi"]
    evidence = bool(state["windows_firmware"] or state["windows_loader"] or state["grub_has_windows"]
                    or (not uefi and (state["ntfs"] or state["bitlocker"])))

    def finding(identifier, severity, title, detail, step=None):
        findings.append({"id": identifier, "severity": severity, "title": title, "detail": detail, "step": step})

    def step(identifier, title, detail, recommended=True):
        steps.append({"id": identifier, "title": title, "detail": detail, "recommended": recommended})
        return identifier

    if not state["grub_installed"]:
        return finish(findings, steps, state, False,
                      "An existing Ubuntu GRUB installation is required before Uoo can check dual boot. "
                      "Run Uoo from the installed system, not a live session or chroot.")

    if state["storage_error"]:
        finding("storage_unreadable", "warning", "Storage could not be inspected",
                state["storage_error"] + " Install util-linux (lsblk) and run the check as administrator; results below may be incomplete.")
    if state["grub_read_error"]:
        finding("grub_unreadable", "warning", "GRUB settings could not be read completely", state["grub_read_error"])
    if state["pending_recovery"]:
        finding("pending_recovery", "info", "An interrupted change will be recovered first",
                "A previous Uoo change did not finish. It is rolled back automatically before the next change is applied.")

    # Conditions that make a whole group of steps impossible are reported once, with the cause.
    grub_blocked = state["boot_readonly"]
    firmware_blocked = state["efivars_readonly"]
    if grub_blocked:
        finding("boot_readonly", "problem", "The boot filesystem is read-only",
                "GRUB cannot be rebuilt while /boot is read-only, so Uoo offers no GRUB changes. Remount it read-write "
                "(sudo mount -o remount,rw /boot) and check the disk for errors, then check again.")
    if uefi and firmware_blocked:
        finding("efivars_readonly", "problem", "UEFI variables are read-only",
                "The firmware variable store is mounted read-only, so boot entries cannot be changed. Remount it "
                "(sudo mount -o remount,rw /sys/firmware/efi/efivars), then check again. Do not edit these files by hand.")

    def conflict(*keys):
        """Name the later drop-in that would silently override the keys a step writes."""
        return next(((name, found) for name, found in sorted(state["grub_conflicts"].items())
                     if set(keys) & set(found)), None)

    def blocked_by_override(identifier, title, *keys):
        hit = conflict(*keys)
        if not hit:
            return False
        finding(identifier, "problem", title, f"{hit[0]} sets {', '.join(sorted(set(keys) & set(hit[1])))} after Uoo's file, so a change "
                "made here would have no effect. Edit or remove that setting in "
                f"/etc/default/grub.d/{hit[0]} first, then check again.")
        return True

    if evidence and not state["grub_has_windows"]:
        title = "Windows is missing from the GRUB menu"
        if grub_blocked:
            pass
        elif uefi and state["windows_loader"]:
            warning = (" Starting Windows through GRUB changes what the TPM measures, so BitLocker may ask for the recovery key if you choose it."
                       if encrypted else "")
            chosen = step("grub_chainloader", "Add Windows Boot Manager to the GRUB menu",
                          "Add a managed entry that starts Windows from its EFI partition. It works without os-prober." + warning)
            finding("windows_missing_from_grub", "problem", title,
                    "Windows is installed but GRUB does not list it, so you cannot choose it at startup.", chosen)
        elif state["os_prober_installed"] and state["os_prober_disabled"]:
            if not blocked_by_override("os_prober_overridden", title, "GRUB_DISABLE_OS_PROBER"):
                chosen = step("os_prober", "Turn on operating-system discovery",
                              "Allow GRUB to find Windows when it rebuilds the menu (GRUB_DISABLE_OS_PROBER=false). "
                              "Since GRUB 2.06 this is off by default.")
                finding("windows_missing_from_grub", "problem", title,
                        "Operating-system discovery is off, so GRUB does not look for Windows. update-grub prints "
                        "'os-prober will not be executed' in this case.", chosen)
        else:
            if state["unmounted_esps"] and not state["windows_loader"]:
                hint = ("Windows' boot files may be on an unmounted EFI System Partition (" + ", ".join(state["unmounted_esps"]) +
                        "). Mount it (for example sudo mount " + state["unmounted_esps"][0] + " /mnt) and check again.")
            elif not state["os_prober_installed"]:
                hint = "Install os-prober (sudo apt install os-prober) and use Rescan and rebuild GRUB."
            else:
                hint = "Mount Windows' EFI System Partition so Uoo can verify its loader, or check Diagnostics."
            finding("windows_missing_from_grub", "problem", title, "Uoo cannot add it automatically. " + hint)

    if evidence and not grub_blocked and (state["grub_style"] == "hidden" or state["grub_timeout"] == 0):
        keys = ("GRUB_TIMEOUT_STYLE",) + (("GRUB_TIMEOUT",) if state["grub_timeout"] == 0 else ())
        if not blocked_by_override("grub_menu_overridden", "The GRUB menu is hidden and cannot be changed here", *keys):
            change = "Show the GRUB menu" + (" and wait 10 seconds" if state["grub_timeout"] == 0 else "")
            chosen = step("grub_menu", change, "Set GRUB_TIMEOUT_STYLE=menu" +
                          (" and GRUB_TIMEOUT=10" if state["grub_timeout"] == 0 else "") + " so you can choose an operating system.")
            finding("grub_menu_hidden", "problem", "The GRUB menu is hidden",
                    "GRUB is set to start the default system without showing a menu, so you may be unable to choose between Ubuntu and Windows.", chosen)

    if not uefi and state["esp_present"]:
        finding("mixed_boot_modes", "problem", "Legacy BIOS session on a disk with an EFI System Partition",
                "This computer started in legacy BIOS (CSM) mode, but a disk has an EFI System Partition, so Windows most likely boots in UEFI mode. "
                "GRUB in BIOS mode cannot start a UEFI Windows. Switch the firmware to UEFI mode (and reinstall or repair Ubuntu's UEFI loader "
                "from the Repair page) rather than changing Windows.")

    if uefi and not state["firmware_available"] and state["firmware_error"]:
        finding("firmware_unreadable", "warning", "UEFI boot entries could not be read", state["firmware_error"])
    elif uefi and state["firmware_available"] and not firmware_blocked:
        entries = state["ubuntu_entries"]
        if not entries:
            if state["ubuntu_loader"]:
                place = ("It is added after the existing entries so the boot order does not change."
                         if encrypted else "It is placed first so the GRUB menu appears at startup.")
                chosen = step("uefi_create_ubuntu", "Register Ubuntu in the UEFI firmware",
                              f"Create a firmware entry for {state['ubuntu_loader']['loader']}. " + place)
                finding("ubuntu_entry_missing", "problem", "Ubuntu is missing from the UEFI boot list",
                        "The firmware has no entry for Ubuntu's loader, which often happens after a firmware reset or update.", chosen)
                if state["fallback_loader"] is False:
                    finding("no_fallback_loader", "info", "Some firmware ignores boot entries it does not like",
                            "There is no fallback loader at EFI/BOOT/BOOTX64.EFI. If the new entry disappears after a restart, "
                            "install one with 'sudo grub-install --removable'. Uoo does not do this automatically.")
            else:
                finding("ubuntu_loader_missing", "problem", "Ubuntu's EFI loader was not found",
                        "No loader exists at /boot/efi/EFI/ubuntu. Mount the EFI System Partition at /boot/efi, or use the Repair page to reinstall it.")
        elif state["windows_firmware"] or state["windows_loader"]:
            order = state["firmware_order"]
            first_windows = min((order.index(i) for i in state["windows_firmware"] if i in order), default=None)
            best = min(entries, key=lambda e: order.index(e["id"]) if e["id"] in order else len(order))
            excluded = best["id"] not in order
            inactive = not best["active"]
            windows_first = (first_windows is not None and not excluded
                             and first_windows < order.index(best["id"]))
            if excluded or inactive or windows_first:
                reason = ("Ubuntu's entry is inactive or left out of the boot order." if excluded or inactive
                          else "Windows Boot Manager starts before Ubuntu's GRUB menu, so the menu never appears. "
                               "Windows updates are a common cause.")
                detail = "Move Ubuntu first" + (" and activate it" if inactive else "") + ". You can reorder later on the Firmware page."
                if encrypted:
                    reason += " On a BitLocker system this can also change what the TPM measures when Windows starts."
                chosen = step("uefi_order", "Put Ubuntu's GRUB menu first", detail, recommended=not encrypted)
                finding("windows_boots_first", "warning" if encrypted else "problem", "Windows starts before the GRUB menu", reason, chosen)
                if len(state["windows_firmware"]) > 1:
                    finding("several_windows_entries", "info", "More than one Windows Boot Manager entry exists",
                            "Firmware lists several Windows Boot Manager entries. Uoo only moves Ubuntu; remove stale entries on the Firmware page if the order keeps changing.")

    if uefi and state["esp_low"]:
        free = state["esp_free_mib"]
        finding("esp_nearly_full", "warning", "The EFI System Partition is nearly full",
                (f"Only about {free} MiB is free on /boot/efi. " if free is not None else "") +
                "A full ESP makes bootloader and kernel updates fail. Windows often creates a 100 MiB ESP. Remove unused vendor folders under "
                "/boot/efi/EFI you are sure are obsolete, or enlarge the partition from a live session with a backup first. Uoo does not resize partitions.")
    if state["windows_loader_count"] > 1:
        finding("windows_on_several_partitions", "info", "Windows boot files exist on several EFI partitions",
                "Uoo uses the first mounted one for the GRUB entry. If Windows starts the wrong installation, check which disk the firmware boots.")

    if encrypted:
        finding("bitlocker_volume", "warning", "A BitLocker-encrypted volume was found",
                "Volumes: " + ", ".join(state["bitlocker"]) + ". Windows 11 can turn BitLocker on by itself, so you may not know it is active. "
                "Keep the 48-digit recovery password on another device. Changing the boot order or the Secure Boot state can make Windows ask for it. "
                "Uoo never changes encryption. Secure Boot is " + state["secure_boot"] + ".")
    if state["luks"]:
        finding("luks_volume", "info", "A LUKS-encrypted volume was found",
                "Volumes: " + ", ".join(state["luks"]) + ". Keep a working passphrase or recovery key and a LUKS header backup. "
                "If a TPM unlocks the disk automatically, keep the fallback passphrase as well.")
    if evidence and not uefi:
        finding("bios_mode", "info", "This computer starts in legacy BIOS mode",
                "Firmware boot-order and EFI checks apply only to UEFI systems.")
    if evidence and uefi and state["secure_boot"] == "enabled":
        finding("secure_boot_keys", "info", "Secure Boot keys are being replaced in 2026",
                "Microsoft's 2011 Secure Boot certificates expire during 2026 (June 24, June 27, and October 19). Systems keep booting, and Windows and "
                "firmware updates add the 2023 certificates over time. Keep Ubuntu's shim current (sudo apt update && sudo apt upgrade). If Linux fails "
                "with 'Verifying shim SBAT data failed' after a Windows update, update shim from a working session (see https://uoo.lolisoft.eu/docs/troubleshooting/). "
                "Updating the Secure Boot database changes what the TPM measures (PCR 7), so BitLocker or TPM-based LUKS unlocking can ask for the "
                "recovery key; keep it ready. Do not change Secure Boot keys by hand.")
    if evidence:
        finding("clock_tip", "info", "If the clock is wrong after switching systems",
                "Windows keeps local time in the hardware clock and Linux keeps UTC. Make them agree with "
                "'timedatectl set-local-rtc 1' on Ubuntu, or set RealTimeIsUniversal on Windows. Uoo does not change this.")
    if state["ntfs"]:
        finding("fast_startup_tip", "info", "Windows Fast Startup can lock its drive from Linux",
                "If an NTFS volume mounts read-only or fails to mount, shut Windows down fully or turn off Fast Startup (powercfg /h off). "
                "Do not run ntfsfix on a Windows installation you still use. Uoo does not change this.")

    return finish(findings, steps, state, True, "")


def finish(findings, steps, state, supported, reason):
    ordered = sorted(steps, key=lambda item: STEP_ORDER.index(item["id"]))
    needs_acknowledgement = bool(state["bitlocker"]) and bool(ordered)
    plan = {"supported": supported, "reason": reason, "boot_mode": "uefi" if state["uefi"] else "bios",
            "encrypted": {"bitlocker": list(state["bitlocker"]), "luks": list(state["luks"])},
            "requires_acknowledgement": needs_acknowledgement, "findings": findings, "steps": ordered}
    # The identifier covers the decisions that decide which operations run, not text or readings that merely drift.
    stable = {key: value for key, value in state.items() if key not in VOLATILE}
    basis = {"steps": [(item["id"], item["recommended"]) for item in ordered], "supported": supported,
             "encrypted": plan["encrypted"], "state": stable}
    plan["id"] = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return plan


def failed_plan(reason):
    """An unsupported plan for a check that could not complete, so the interface can explain instead of crash."""
    return finish([], [], {**DEFAULTS, "grub_installed": True}, False, reason)


class DualBoot:
    """Mixin for ProductionManager."""

    def dual_boot_state(self):
        p = self.paths
        uefi = p.efi.is_dir()
        grub_installed = p.update.is_file() and p.defaults.is_file() and p.config.is_file()
        state = {**DEFAULTS, "grub_installed": grub_installed, "uefi": uefi, "secure_boot": facts.secure_boot(p),
                 "os_prober_installed": p.os_prober.is_file(), "windows_firmware": [], "ubuntu_entries": [],
                 "firmware_order": [], "bitlocker": [], "luks": [], "ntfs": [], "unmounted_esps": [], "grub_conflicts": {}}
        try:
            inventory = facts.devices(p, self.runner)
        except (OSError, RuntimeError, ValueError) as error:
            inventory = []
            state["storage_error"] = f"The disk list could not be read: {error}"
        for node in inventory:
            if node["fstype"] == "BitLocker":
                state["bitlocker"].append(node["path"])
            elif node["fstype"] == "crypto_LUKS":
                state["luks"].append(node["path"])
            elif node["fstype"] in ("ntfs", "ntfs3"):
                state["ntfs"].append(node["path"])
            if node["esp"]:
                state["esp_present"] = True
                if not node["mountpoint"] and node["type"] == "part" and not node["readonly"]:
                    state["unmounted_esps"].append(node["path"])
        try:
            state["pending_recovery"] = (p.state / "pending.json").exists()
        except OSError:
            pass
        if grub_installed:
            self.dual_boot_grub_state(state)
        if uefi:
            self.dual_boot_uefi_state(state, inventory)
        return state

    def dual_boot_grub_state(self, state):
        p = self.paths
        settings = {}
        try:
            settings.update(parse_settings(p.defaults.read_text()))
            for path in sorted(p.dropins.glob("*.cfg")):
                settings.update(parse_settings(path.read_text()))
                if path.name > p.override.name:
                    found = sorted(key for key in ("GRUB_TIMEOUT_STYLE", "GRUB_TIMEOUT", "GRUB_DISABLE_OS_PROBER")
                                   if re.search(rf"(?m)^\s*(?:export\s+)?{key}\s*=", path.read_text()))
                    if found:
                        state["grub_conflicts"][path.name] = found
            state["grub_has_windows"] = any(re.search("windows", entry.title, re.I)
                                            for entry in parse_entries(p.config.read_text()))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            state["grub_read_error"] = f"{error}"
        state["grub_style"] = settings.get("GRUB_TIMEOUT_STYLE", "menu")
        try:
            state["grub_timeout"] = int(settings.get("GRUB_TIMEOUT", "5"))
        except ValueError:
            pass
        state["os_prober_disabled"] = settings.get("GRUB_DISABLE_OS_PROBER") != "false"
        state["boot_readonly"] = self.dual_boot_readonly(p.config.parent)

    def dual_boot_readonly(self, path):
        try:
            return "ro" in facts.mount_for(path, self.paths, self.runner).get("options", "").split(",")
        except (OSError, RuntimeError, ValueError):
            return False

    def dual_boot_uefi_state(self, state, inventory):
        p = self.paths
        windows_loaders = 0
        for node in inventory:
            if not node["esp"] or not node["mountpoint"] or not node["uuid"]:
                continue
            mount = Path(node["mountpoint"])
            if self.dual_boot_loader_ok(mount, WINDOWS_LOADER):
                windows_loaders += 1
                if state["windows_loader"] is None:
                    state["windows_loader"] = {"uuid": node["uuid"], "device": node["path"], "loader": WINDOWS_LOADER}
            if node["mountpoint"] == str(p.efi_directory):
                if node["pkname"] and type(node.get("partn")) is int:
                    for loader in UBUNTU_LOADERS:
                        if self.dual_boot_loader_ok(mount, loader):
                            state["ubuntu_loader"] = {"loader": loader, "disk": node["pkname"], "partition": node["partn"]}
                            break
                state["fallback_loader"] = any(self.dual_boot_loader_ok(mount, loader) for loader in FALLBACK_LOADERS)
                try:
                    free = shutil.disk_usage(mount).free
                    state["esp_free_mib"] = free // MIB
                    state["esp_low"] = free < LOW_ESP_BYTES
                except OSError:
                    pass
        state["windows_loader_count"] = windows_loaders
        state["efivars_readonly"] = self.dual_boot_readonly(p.efi / "efivars")
        from .system import read_firmware
        firmware = read_firmware(p, self.runner)
        state["firmware_available"] = bool(firmware["available"])
        state["firmware_error"] = "" if firmware["available"] else str(firmware.get("error", ""))
        state["firmware_order"] = list(firmware["order"])
        for entry in firmware["entries"]:
            path = entry.get("device_path", "")
            if WINDOWS_PATH.search(path) or entry["label"].lower().startswith("windows boot manager"):
                state["windows_firmware"].append(entry["id"])
            elif UBUNTU_PATH.search(path) or entry["label"].lower() == UBUNTU_LABEL:
                state["ubuntu_entries"].append({"id": entry["id"], "active": bool(entry["active"])})

    @staticmethod
    def dual_boot_loader_ok(mount, loader):
        target = mount / loader.lstrip("/")
        try:
            if target.is_symlink() or not target.is_file():
                return False
            with target.open("rb") as stream:
                return stream.read(2) == b"MZ"
        except OSError:
            return False

    def dual_boot_plan(self, request):
        try:
            return {"plan": analyze(self.dual_boot_state())}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            return {"plan": failed_plan(f"Uoo could not finish the dual-boot check: {error}. Open Diagnostics for details, then try again.")}

    def dual_boot_fix(self, request):
        state = self.dual_boot_state()
        plan = analyze(state)
        if request.get("plan") != plan["id"]:
            raise ValueError("The dual-boot plan changed. Check dual boot again and review the new plan.")
        if not plan["supported"]:
            raise ValueError(plan["reason"])
        available = {item["id"]: item for item in plan["steps"]}
        chosen = request.get("steps")
        if chosen is None:
            chosen = [item["id"] for item in plan["steps"] if item["recommended"]]
        if (not isinstance(chosen, list) or len(set(chosen)) != len(chosen)
                or not all(isinstance(item, str) and item in available for item in chosen)):
            raise ValueError("Choose only steps from the reviewed dual-boot plan.")
        if not chosen:
            raise ValueError("Select at least one step to apply.")
        acknowledged = request.get("acknowledge_recovery", False)
        if type(acknowledged) is not bool:
            raise ValueError("The recovery-key acknowledgement must be true or false.")
        if plan["requires_acknowledgement"] and not acknowledged:
            raise ValueError("A BitLocker-encrypted volume was found. Confirm that you have its recovery key before changing boot settings. "
                             "Uoo will not change encryption.")
        backup = self.create_backup({"kind": "all"})["backup"]
        done, changed = [], []
        try:
            grub_changes = {}
            if "grub_menu" in chosen:
                grub_changes["GRUB_TIMEOUT_STYLE"] = "menu"
                if state["grub_timeout"] == 0:
                    grub_changes["GRUB_TIMEOUT"] = 10
            if "os_prober" in chosen:
                grub_changes["GRUB_DISABLE_OS_PROBER"] = False
            if grub_changes:
                self.apply_grub({"changes": grub_changes})
                done.extend(available[name]["title"] for name in ("grub_menu", "os_prober") if name in chosen)
            if "grub_chainloader" in chosen:
                self.dual_boot_add_windows(state["windows_loader"])
                done.append(available["grub_chainloader"]["title"])
            if "uefi_create_ubuntu" in chosen:
                self.dual_boot_register_ubuntu(state["ubuntu_loader"], bool(state["bitlocker"]), changed)
                done.append(available["uefi_create_ubuntu"]["title"])
            if "uefi_order" in chosen:
                self.dual_boot_ubuntu_first()
                done.append(available["uefi_order"]["title"])
        except Exception as error:
            if not done and not changed and isinstance(error, ValueError):
                # A precondition failed before any step changed anything; there is nothing to undo.
                raise RuntimeError(f"The dual-boot fix could not start: {error}. No changes were made. A backup was saved as {backup}.") from error
            try:
                self.restore({"backup": backup})
            except Exception as restore_error:
                raise RuntimeError(f"The dual-boot fix stopped: {error}. Restoring the pre-fix backup {backup} also failed: {restore_error}. "
                                   "Review the boot settings before restarting.") from error
            raise RuntimeError(f"The dual-boot fix stopped: {error}. Your previous settings were restored from backup {backup}.") from error
        return {"message": "Dual-boot fix applied: " + "; ".join(done) + f". Pre-fix backup: {backup}. Uoo did not restart your computer.",
                "applied": done, "backup": backup}

    def dual_boot_add_windows(self, loader):
        if not loader:
            raise ValueError("Windows' EFI loader is no longer available on a mounted EFI System Partition.")
        from .production import custom_model
        entries = custom_model(self.paths)
        identifier = "uoo-" + uuid.uuid5(uuid.NAMESPACE_URL, f"uoo:windows:{loader['uuid'].lower()}:{loader['loader']}").hex
        wanted = {"id": identifier, "title": WINDOWS_ENTRY_TITLE, "type": "efi", "enabled": True,
                  "uuid": loader["uuid"], "loader": loader["loader"]}
        entries = [wanted if entry["id"] == identifier else entry for entry in entries]
        if not any(entry["id"] == identifier for entry in entries):
            entries.append(wanted)
        self.set_custom_entries({"entries": validate_custom_entries(entries)})

    def dual_boot_register_ubuntu(self, loader, keep_order, changed):
        if not loader:
            raise ValueError("Ubuntu's EFI loader is no longer available at the expected location.")
        before = self.firmware()
        self.create_uefi({"label": UBUNTU_LABEL, "disk": loader["disk"], "partition": loader["partition"], "loader": loader["loader"]})
        changed.append("firmware")
        after = self.firmware()
        created = next((entry for entry in after["entries"]
                        if entry["id"] not in {item["id"] for item in before["entries"]}), None)
        if created is None:
            raise RuntimeError("The new Ubuntu firmware entry could not be found after it was created.")
        if keep_order:
            wanted = [item for item in before["order"]] + [created["id"]]
        else:
            wanted = [created["id"]] + [item for item in after["order"] if item != created["id"]]
        if after["order"] != wanted:
            self.apply_order({"order": wanted})

    def dual_boot_ubuntu_first(self):
        firmware = self.firmware()
        candidates = [entry for entry in firmware["entries"]
                      if UBUNTU_PATH.search(entry.get("device_path", "")) or entry["label"].lower() == UBUNTU_LABEL]
        if not candidates:
            raise ValueError("Ubuntu's firmware entry is no longer present.")
        order = firmware["order"]
        entry = min(candidates, key=lambda item: order.index(item["id"]) if item["id"] in order else len(order))
        if not entry["active"]:
            self.set_active({"entry": entry["id"], "active": True})
            firmware = self.firmware()
            order = firmware["order"]
        wanted = [entry["id"]] + [item for item in order if item != entry["id"]]
        if firmware["order"] != wanted:
            self.apply_order({"order": wanted})
