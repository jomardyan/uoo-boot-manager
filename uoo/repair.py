"""Guided reinstallation on the current boot target, with rollback snapshots."""

import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import zlib

from . import platform as facts
from .features import state_bytes
from .storage import file_state, read_regular, restore_file


class Repair:
    def capture_boot_tree(self):
        root = self.paths.config.parent
        entries = {}
        total = 0
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("BIOS reinstallation requires regular files in the GRUB directory, not symbolic links.")
            if path.is_file():
                data = read_regular(path, maximum=8 * 1024 * 1024)
                total += len(data)
                if total > 48 * 1024 * 1024 or len(entries) >= 2048:
                    raise ValueError("The existing GRUB files exceed the supported BIOS rollback size.")
                entries[str(path.relative_to(root))] = {"data": base64.b64encode(zlib.compress(data)).decode(),
                                                       "mode": stat.S_IMODE(path.stat().st_mode), "size": len(data)}
            elif not path.is_dir():
                raise ValueError("The GRUB directory contains an unsupported special file.")
        return entries

    def restore_boot_tree(self, entries):
        root = self.paths.config.parent
        if not isinstance(entries, dict) or len(entries) > 2048:
            raise ValueError("Invalid GRUB directory backup.")
        decoded = {}
        total = 0
        for relative, saved in entries.items():
            if (not isinstance(relative, str) or not relative or relative.startswith("/")
                    or any(part in ("", ".", "..") for part in relative.split("/"))):
                raise ValueError("Invalid path in the GRUB directory backup.")
            if not isinstance(saved, dict) or set(saved) != {"data", "mode", "size"} or not isinstance(saved["data"], str):
                raise ValueError("Invalid GRUB backup file record.")
            if type(saved.get("size")) is not int or not 0 <= saved["size"] <= 8 * 1024 * 1024:
                raise ValueError("Invalid GRUB backup file size.")
            if type(saved.get("mode")) is not int or not 0 <= saved["mode"] <= 0o777:
                raise ValueError("Invalid GRUB backup file permissions.")
            stream = zlib.decompressobj()
            data = stream.decompress(base64.b64decode(saved["data"], validate=True), saved["size"] + 1)
            if len(data) != saved["size"] or not stream.eof or stream.unused_data or stream.unconsumed_tail:
                raise ValueError("Invalid compressed GRUB backup file.")
            total += len(data)
            if total > 48 * 1024 * 1024:
                raise ValueError("The GRUB backup exceeds its permitted size.")
            decoded[relative] = (data, saved["mode"])
        for relative in decoded:
            parts = relative.split("/")
            if any("/".join(parts[:index]) in decoded for index in range(1, len(parts))):
                raise ValueError("A backup path is both a file and a directory.")
        # Check the complete target tree before removing anything installed by
        # the failed reinstall. A pending recovery never follows symlinks.
        existing = list(root.rglob("*"))
        if any(path.is_symlink() or (not path.is_file() and not path.is_dir()) for path in existing):
            raise ValueError("Symbolic links or special files in the GRUB directory prevent automatic recovery.")
        for relative in decoded:
            target = root / relative
            if target.is_dir():
                raise ValueError("A restored file conflicts with an existing directory.")
            for parent in target.parents:
                if parent == root:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError("A restored directory conflicts with an existing file.")
        for path in existing:
            if path.is_file() and str(path.relative_to(root)) not in decoded:
                path.unlink()
        for relative, (contents, mode) in decoded.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            self.atomic_write(target, contents, mode)

    def boot_target(self):
        inventory = facts.devices(self.paths, self.runner)
        mount = facts.mount_for(self.paths.config.parent, self.paths, self.runner)
        source = mount.get("source", "").split("[", 1)[0]
        partition = next((node for node in inventory if node["path"] == source), None)
        if not partition or partition["type"] != "part" or not partition["pkname"]:
            raise ValueError("Repair currently requires a plain disk partition for /boot; RAID, LVM, and encrypted mappings need an administrator's recovery procedure.")
        if partition.get("readonly") or "ro" in mount.get("options", "").split(","):
            raise ValueError("The boot filesystem is read-only; repair requires a writable boot partition.")
        disk = next((node for node in inventory if node["path"] == partition["pkname"] and node["type"] == "disk"), None)
        if not disk or disk["readonly"]:
            raise ValueError("The current boot disk cannot be verified as writable.")
        return inventory, partition, disk

    def repair_plan(self, request):
        mode = "uefi" if self.paths.efi.is_dir() else "bios"
        plan = {"mode": mode, "supported": False, "reason": "", "summary": [], "target": "", "confirmation": ""}
        try:
            self.check_grub()
            if platform.machine() not in ("x86_64", "amd64"):
                raise ValueError("Bootloader reinstallation currently supports x86-64 Ubuntu installations.")
            inventory, partition, disk = self.boot_target()
            if mode == "uefi":
                mount = facts.mount_for(self.paths.efi_directory, self.paths, self.runner)
                esp = next((node for node in inventory if node["path"] == mount.get("source")), None)
                if not esp or not esp["esp"] or esp["readonly"] or mount.get("target") != str(self.paths.efi_directory):
                    raise ValueError("Mount the current EFI System Partition at the expected /boot/efi location before reinstalling.")
                if "ro" in mount.get("options", "").split(","):
                    raise ValueError("The EFI System Partition is mounted read-only; repair requires a writable mount.")
                sources = {
                    "efi_shim": Path("/usr/lib/shim/shimx64.efi.signed.latest"),
                    "efi_grub": Path("/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"),
                    "efi_mm": Path("/usr/lib/shim/mmx64.efi"),
                }
                if not sources["efi_shim"].exists():
                    sources["efi_shim"] = Path("/usr/lib/shim/shimx64.efi.signed")
                sources = {key: source.resolve() for key, source in sources.items()}
                hashes = {}
                for key, path in sources.items():
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                        raise ValueError("Install Ubuntu's signed shim and GRUB packages before using UEFI reinstallation.")
                    with path.open("rb") as stream:
                        if stream.read(2) != b"MZ":
                            raise ValueError("The installed signed bootloader file is not a valid EFI executable.")
                    hashes[key] = hashlib.sha256(read_regular(path, maximum=8 * 1024 * 1024)).hexdigest()
                uuid = self.runner([self.paths.grub_probe, "--target=fs_uuid", self.paths.config.parent], timeout=15).strip()
                relative = self.runner([self.paths.mkrelpath, self.paths.config.parent], timeout=15).strip()
                if not re.fullmatch(r"[0-9A-Fa-f-]{4,64}", uuid) or not re.fullmatch(r"/[A-Za-z0-9_./+-]+", relative):
                    raise ValueError("The current GRUB filesystem cannot be represented safely in an EFI loader configuration.")
                plan.update(target=str(self.paths.efi_directory), confirmation=str(self.paths.efi_directory), supported=True,
                            sources={key: str(value) for key, value in sources.items()}, uuid=uuid, relative=relative,
                            source_hashes=hashes,
                            esp_uuid=esp["uuid"], disk=disk["path"],
                            summary=["Back up the current Ubuntu EFI loaders and GRUB configuration.",
                                     "Reinstall the signed Ubuntu shim, GRUB, and MOK manager files in EFI/ubuntu.",
                                     "Recreate the loader configuration and regenerate the GRUB menu.",
                                     "Preserve firmware order, other vendors' loaders, and Secure Boot settings."])
            else:
                if not self.paths.grub_install.is_file() or not self.paths.bios_modules.is_dir():
                    raise ValueError("Install grub-pc-bin before reinstalling GRUB on a BIOS machine.")
                regions = self.bios_regions(inventory, disk)
                plan.update(target=disk["path"], confirmation=disk["path"], supported=True,
                            disk=disk["path"], boot_uuid=partition["uuid"], regions=regions,
                            summary=["Back up the current disk boot sector and GRUB embedding area.",
                                     "Reinstall BIOS GRUB on the verified current boot disk.",
                                     "Regenerate and validate the GRUB menu; preserve partitions and filesystem data."])
        except (OSError, RuntimeError, ValueError) as exc:
            plan["reason"] = str(exc)
        fingerprint = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
        plan["id"] = hashlib.sha256(fingerprint).hexdigest()
        return {"plan": plan}

    def bios_regions(self, inventory, disk):
        partitions = [node for node in inventory if node["pkname"] == disk["path"] and node["type"] == "part"]
        starts = []
        for node in partitions:
            start = int((self.paths.sys_block / node["name"] / "start").read_text()) * 512
            starts.append(start)
        if not starts or min(starts) < 512:
            raise ValueError("The disk embedding layout cannot be verified.")
        gpt = any(node["parttype"].lower() == facts.BIOS_TYPE for node in partitions)
        if gpt:
            bios = [node for node in partitions if node["parttype"].lower() == facts.BIOS_TYPE]
            if len(bios) != 1 or not 1 <= int(bios[0]["size"]) <= 4 * 1024 * 1024:
                raise ValueError("A single BIOS GRUB embedding partition of at most 4 MiB is required.")
            return [{"path": disk["path"], "length": 512}, {"path": bios[0]["path"], "length": int(bios[0]["size"])}]
        if min(starts) > 16 * 1024 * 1024:
            raise ValueError("The BIOS embedding gap is unexpectedly large; use an administrator's recovery procedure.")
        return [{"path": disk["path"], "length": min(starts)}]

    def capture_bios(self, plan):
        regions = []
        for region in plan["regions"]:
            descriptor = os.open(region["path"], os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb", buffering=0) as stream:
                if not stat.S_ISBLK(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("The BIOS target is not a block device.")
                data = stream.read(region["length"])
                if len(data) != region["length"]:
                    raise RuntimeError("The BIOS embedding area could not be backed up completely.")
                regions.append({**region, "data": base64.b64encode(data).decode()})
        return {"disk": plan["disk"], "boot_uuid": plan["boot_uuid"], "regions": regions}

    def validate_bios_state(self, state):
        if not isinstance(state, dict) or set(state) != {"disk", "boot_uuid", "regions"} or not isinstance(state["regions"], list):
            raise ValueError("Invalid BIOS backup structure.")
        inventory, partition, disk = self.boot_target()
        expected = self.bios_regions(inventory, disk)
        if state.get("disk") != disk["path"] or state.get("boot_uuid") != partition["uuid"]:
            raise ValueError("The BIOS backup belongs to a different boot disk or filesystem.")
        if any(not isinstance(item, dict) or set(item) != {"path", "length", "data"} for item in state["regions"]):
            raise ValueError("Invalid BIOS backup region.")
        if [{key: item[key] for key in ("path", "length")} for item in state["regions"]] != expected:
            raise ValueError("The disk partition layout changed after the BIOS backup was made.")
        decoded = []
        for region in state["regions"]:
            contents = base64.b64decode(region["data"], validate=True)
            if len(contents) != region["length"]:
                raise ValueError("The BIOS backup length is invalid.")
            decoded.append((region, contents))
        return decoded

    def restore_bios_state(self, state):
        decoded = self.validate_bios_state(state)
        with ExitStack() as stack:
            targets = []
            for region, contents in decoded:
                descriptor = os.open(region["path"], os.O_WRONLY | os.O_NOFOLLOW)
                stream = stack.enter_context(os.fdopen(descriptor, "wb", buffering=0))
                if not stat.S_ISBLK(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("The BIOS target is not a block device.")
                targets.append((stream, contents))
            for stream, contents in targets:
                remaining = memoryview(contents)
                while remaining:
                    written = stream.write(remaining)
                    if not isinstance(written, int) or written <= 0 or written > len(remaining):
                        raise OSError("The BIOS boot-sector write did not complete.")
                    remaining = remaining[written:]
                os.fsync(stream.fileno())

    def repair(self, request):
        plan = self.repair_plan({})["plan"]
        if not plan["supported"]:
            raise ValueError(plan["reason"])
        if request.get("plan") != plan["id"] or request.get("confirm") != plan["confirmation"]:
            raise ValueError("Review the current repair plan and type its exact target to confirm reinstallation.")
        if plan["mode"] == "uefi":
            directory = self.paths.efi_directory / "EFI/ubuntu"
            if directory.is_symlink() or directory.parent.is_symlink():
                raise ValueError("Symbolic links in the EFI bootloader directory are not supported.")
            directory.mkdir(parents=True, exist_ok=True)
            updates = {key: state_bytes(read_regular(Path(path), maximum=8 * 1024 * 1024))
                       for key, path in plan["sources"].items()}
            stub = f"search.fs_uuid {plan['uuid']} root\nset prefix=($root)'{plan['relative']}'\nconfigfile $prefix/grub.cfg\n"
            updates["efi_config"] = state_bytes(stub.encode())
            self.transaction(updates, "Ubuntu EFI loaders before reinstallation", kind="uefi_repair", capture=("generated",),
                             verify=self.verify_grub)
            return "Signed Ubuntu EFI loaders and GRUB configuration were reinstalled. Firmware settings and Secure Boot were preserved."
        state = self.capture_bios(plan)
        tree = self.capture_boot_tree()
        files = {"generated": file_state(self.paths.config)}
        identifier = self.backup("bios_repair", "BIOS bootloader before reinstallation",
                                 {"bios": state, "boot_tree": tree, "files": files, "configuration": self.configuration()})
        self.store.journal({"version": 2, "files": files, "bios": state, "boot_tree": tree, "backup": identifier})
        try:
            self.runner([self.paths.grub_install, "--target=i386-pc", "--boot-directory=" + str(self.paths.config.parent.parent),
                         "--recheck", plan["disk"]], timeout=180)
            self.verify_grub()
            self.store.finish()
        except Exception as exc:
            try:
                self.restore_boot_tree(tree)
                self.restore_bios_state(state)
                restore_file(self.paths.config, files["generated"])
                self.store.finish()
            except Exception as rollback:
                raise RuntimeError(f"BIOS repair failed: {exc}. Rollback also failed: {rollback}. Backup: {identifier}.") from exc
            raise RuntimeError(f"BIOS repair failed; previous boot sectors and menu were restored. {exc}") from exc
        return "BIOS GRUB was reinstalled on the verified boot disk and the menu was regenerated."
