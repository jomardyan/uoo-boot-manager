"""Read-only platform facts used for capability checks and repair planning."""

import json
from pathlib import Path
import platform
import re

ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BIOS_TYPE = "21686148-6449-6e6f-744e656564454649"


def verify_file_filesystem(filename, expected_uuid, paths, runner):
    """Check the actual filesystem, including mounts nested below its directory."""
    if not paths.grub_probe.is_file():
        raise RuntimeError("grub-probe is required to verify a boot file's filesystem.")
    actual = runner([paths.grub_probe, "--target=fs_uuid", filename], timeout=15).strip()
    if not expected_uuid or actual.casefold() != expected_uuid.casefold():
        raise ValueError(f"The boot file {filename.name} is on a different filesystem. Select its filesystem UUID and use a path relative to that filesystem.")


def devices(paths, runner):
    if not paths.lsblk.is_file():
        return []
    output = runner([paths.lsblk, "--json", "--paths", "--bytes", "--output",
                     "NAME,PATH,TYPE,FSTYPE,UUID,MOUNTPOINTS,PKNAME,PARTN,PARTTYPE,PARTUUID,SIZE,RO"], timeout=15)
    payload = json.loads(output)
    result = []

    def collect(nodes):
        for node in nodes:
            if node.get("type") != "loop":
                mounts = node.get("mountpoints") or []
                path = node.get("path") or node.get("name", "")
                if isinstance(path, str) and re.fullmatch(r"/dev/[A-Za-z0-9_./+-]+", path):
                    result.append({"name": Path(path).name, "path": path, "type": node.get("type", ""),
                                   "fstype": node.get("fstype") or "", "uuid": node.get("uuid") or "",
                                   "mountpoint": next((value for value in mounts if value), ""),
                                   "mountpoints": [value for value in mounts if value],
                                   "pkname": node.get("pkname") or "", "partn": node.get("partn"),
                                   "parttype": node.get("parttype") or "", "size": node.get("size"),
                                   "partuuid": node.get("partuuid") or "",
                                   "readonly": bool(node.get("ro")),
                                   "esp": str(node.get("parttype", "")).lower() == ESP_TYPE})
            collect(node.get("children") or [])
    collect(payload.get("blockdevices") or [])
    return result


def mount_for(path, paths, runner):
    if not paths.findmnt.is_file():
        return {}
    response = json.loads(runner([paths.findmnt, "--json", "--target", str(path), "--output",
                                  "SOURCE,FSTYPE,TARGET,OPTIONS"], timeout=15))
    return (response.get("filesystems") or [{}])[0]


def secure_boot(paths):
    try:
        candidates = list((paths.efi / "efivars").glob("SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"))
        if not candidates:
            return "unknown" if paths.efi.exists() else "not applicable"
        data = candidates[0].read_bytes()
        return "enabled" if len(data) >= 5 and data[4] else "disabled"
    except OSError:
        return "unknown"


def env_support(paths, runner, inventory=None):
    """Reject layouts where GRUB cannot reliably consume one-time state."""
    try:
        mount = mount_for(paths.config.parent, paths, runner)
        filesystem = mount.get("fstype", "")
        source = mount.get("source", "").split("[", 1)[0]
        inventory = inventory if inventory is not None else devices(paths, runner)
        current = next((node for node in inventory if node["path"] == source), None)
        if current is None:
            resolved = str(Path(source).resolve()) if source.startswith("/dev/") else source
            current = next((node for node in inventory if str(Path(node["path"]).resolve()) == resolved), None)
        if not current:
            return False, "The boot filesystem's backing device could not be verified."
        if current.get("readonly") or "ro" in mount.get("options", "").split(","):
            return False, "The boot filesystem is read-only; saved and one-time selections are unavailable."
        if current["type"] not in ("disk", "part"):
            return False, "GRUB state on LVM, encrypted mappings, or RAID is not supported for one-time boot."
        parent = current
        seen = set()
        while parent["type"] == "part":
            if parent["path"] in seen:
                return False, "The boot device graph is inconsistent."
            seen.add(parent["path"])
            parent = next((node for node in inventory if node["path"] == parent.get("pkname")), None)
            if parent is None or parent["type"] not in ("disk", "part"):
                return False, "The boot filesystem is not on a verified plain disk."
            if parent.get("readonly"):
                return False, "The boot device is read-only; saved and one-time selections are unavailable."
        if filesystem in ("ext2", "ext3", "ext4", "vfat", "fat", "msdos"):
            return True, "A plain disk filesystem supports the GRUB environment block."
        if filesystem == "btrfs":
            contents = paths.grubenv.read_bytes().decode("utf-8", "replace")
            if re.search(r"(?m)^env_block=.+", contents):
                return True, "An external Btrfs GRUB environment block is configured."
        return False, "This boot filesystem has no verified writable GRUB environment block."
    except (OSError, RuntimeError, ValueError):
        return False, "The GRUB environment block storage could not be verified."


def diagnostics(paths, runner, inventory=None):
    checks = []
    try:
        inventory = devices(paths, runner) if inventory is None else inventory
    except (OSError, RuntimeError, ValueError):
        inventory = []
    distribution = "Linux"
    try:
        for line in paths.os_release.read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                distribution = line.partition("=")[2].strip('"')
    except OSError:
        pass
    tools = {"update-grub": paths.update, "grub-script-check": paths.script_check,
             "grub-editenv": paths.editenv, "grub-reboot": paths.grub_reboot,
             "efibootmgr": paths.efibootmgr, "grub-install": paths.grub_install,
             "lsblk": paths.lsblk, "findmnt": paths.findmnt}
    for name, path in tools.items():
        present = path.is_file()
        checks.append({"name": name, "status": "ok" if present else "warning",
                       "detail": str(path) if present else "Not installed; related operations are unavailable."})
    grub = paths.config.is_file() and paths.defaults.is_file() and paths.update.is_file()
    checks.append({"name": "GRUB installation", "status": "ok" if grub else "warning",
                   "detail": str(paths.config) if grub else "An existing GRUB installation was not found."})
    supported, detail = env_support(paths, runner, inventory) if grub else (False, "GRUB is unavailable.")
    checks.append({"name": "GRUB saved/one-time boot", "status": "ok" if supported else "warning", "detail": detail})
    pending = paths.state / "pending.json"
    checks.append({"name": "Interrupted transaction", "status": "warning" if pending.exists() else "ok",
                   "detail": "A pending update will be recovered before the next change." if pending.exists() else "No pending recovery."})
    return {"system": {"distribution": distribution, "kernel": platform.release(), "architecture": platform.machine(),
                       "boot_mode": "UEFI" if paths.efi.is_dir() else "BIOS", "secure_boot": secure_boot(paths)},
            "checks": checks, "devices": inventory, "environment_supported": supported}
