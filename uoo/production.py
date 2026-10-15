"""Production manager: fixed actions, durable backups, and recovery journals."""

import base64
from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
from pathlib import Path
import re

from .storage import Store, atomic_write, file_state, private_directory, read_regular, restore_file
from .core import parse_entries, parse_settings
from .features import Features
from .firmware import Firmware
from .repair import Repair


def typed_settings(settings):
    from .models import validate_grub_changes
    valid = {}
    for key, value in settings.items():
        try:
            if key in ("GRUB_TIMEOUT", "GRUB_RECORDFAIL_TIMEOUT"):
                value = int(value)
            elif key in ("GRUB_SAVEDEFAULT", "GRUB_DISABLE_OS_PROBER", "GRUB_DISABLE_RECOVERY"):
                # GRUB's scripts compare against the exact string "true" ("TRUE" does nothing).
                if value not in ("true", "false"):
                    continue
                value = value == "true"
            valid.update(validate_grub_changes({key: value}))
        except (ValueError, TypeError):
            continue
    return valid


def environment(paths, runner=None, strict=False):
    result = {}
    try:
        raw = read_regular(paths.grubenv, maximum=65536)
        if runner is not None and paths.editenv.is_file():
            output = runner([paths.editenv, paths.grubenv, "list"], timeout=15)
            # A successful empty native list means no variables. In particular,
            # an external Btrfs block must never fall back to pointer-file data.
            raw = output.encode()
        elif strict:
            raise RuntimeError("grub-editenv is required to read the GRUB environment safely.")
        if raw:
            for line in raw.decode("utf-8", "replace").splitlines():
                if re.match(r"^(saved_entry|next_entry|prev_saved_entry|env_block)=", line):
                    key, _, value = line.partition("=")
                    result[key] = value
    except (OSError, RuntimeError):
        if strict:
            raise
    return result


def custom_model(paths):
    from .models import validate_custom_entries
    raw = read_regular(paths.custom_model, maximum=128 * 1024)
    return validate_custom_entries(json.loads(raw)) if raw else []


def revision(paths):
    digest = hashlib.sha256()
    for path in (paths.override, paths.custom_model, paths.custom_script):
        try:
            digest.update(str(path).encode())
            digest.update(read_regular(path) or b"absent")
        except (OSError, RuntimeError):
            return ""
    return digest.hexdigest()


class ProductionManager(Features, Firmware, Repair):
    def __init__(self, paths=None, runner=None):
        from .system import Paths, run_command
        self.paths = paths if paths is not None else Paths()
        self.runner = runner if runner is not None else run_command
        self.store = Store(self.paths.state)

    private_directory = staticmethod(private_directory)
    atomic_write = staticmethod(atomic_write)

    @contextmanager
    def lock(self):
        with self.store.lock(), ExitStack() as locks:
            # Prevent a kernel/package update from racing a GRUB transaction.
            # Alternate Paths are injected only by internal tests; the installed
            # helper never accepts paths supplied in a request.
            if self.paths.defaults == Path("/etc/default/grub"):
                for path in (Path("/var/lib/dpkg/lock-frontend"), Path("/var/lib/dpkg/lock")):
                    stream = locks.enter_context(path.open("r+b"))
                    try:
                        fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise RuntimeError("A package update is in progress. Wait for it to finish before changing boot settings.") from exc
            yield

    def backup(self, kind, summary, data):
        return self.store.backup(kind, summary, data)

    def list_backups(self):
        return self.store.list_backups()

    def file_paths(self):
        p = self.paths
        return {"override": p.override, "generated": p.config, "custom_script": p.custom_script,
                "custom_model": p.custom_model, "grubenv": p.grubenv, "background": p.background,
                "efi_shim": p.efi_directory / "EFI/ubuntu/shimx64.efi",
                "efi_grub": p.efi_directory / "EFI/ubuntu/grubx64.efi",
                "efi_mm": p.efi_directory / "EFI/ubuntu/mmx64.efi",
                "efi_config": p.efi_directory / "EFI/ubuntu/grub.cfg"}

    def configuration(self):
        settings = {}
        try:
            settings.update(parse_settings(self.paths.defaults.read_text()))
            for path in sorted(self.paths.dropins.glob("*.cfg")):
                settings.update(parse_settings(path.read_text()))
        except OSError:
            pass
        return {"grub": typed_settings(settings), "custom_entries": custom_model(self.paths)}

    def recover(self):
        record = self.store.pending()
        if record is None and not (self.paths.state / "pending.json").exists():
            return
        if (not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 2
                or not {"version", "files"} <= set(record)
                or set(record) - {"version", "files", "firmware", "bios", "boot_tree", "backup", "grub_environment"}):
            raise RuntimeError("The pending recovery journal has an unsupported schema; no new changes can be applied.")
        files = record.get("files")
        if not isinstance(files, dict) or not set(files) <= set(self.file_paths()):
            raise RuntimeError("The recovery journal contains unsupported paths.")
        if record.get("bios"):
            self.validate_bios_state(record["bios"])
        if record.get("firmware"):
            self.validate_firmware_restore(record["firmware"])
        if "grub_environment" in record:
            self.validate_grub_environment(record["grub_environment"])
        try:
            if record.get("boot_tree"):
                self.restore_boot_tree(record["boot_tree"])
            for key, saved in files.items():
                restore_file(self.file_paths()[key], saved)
            if record.get("firmware"):
                self.restore_firmware_state(record["firmware"])
            if record.get("bios"):
                self.restore_bios_state(record["bios"])
            if "grub_environment" in record:
                self.restore_grub_environment(record["grub_environment"])
            self.store.finish()
        except Exception as exc:
            raise RuntimeError(f"An interrupted update needs recovery before new changes can be made: {exc}") from exc

    def transaction(self, updates, summary, kind="grub", capture=(), verify=None, firmware_before=None, environment_before=None):
        paths = self.file_paths()
        if not set(updates) <= set(paths) or not set(capture) <= set(paths):
            raise ValueError("Unsupported managed file.")
        if environment_before is not None:
            self.validate_grub_environment(environment_before)
        saved = {key: file_state(paths[key]) for key in set(updates) | set(capture)}
        backup_data = {"files": saved, "configuration": self.configuration()}
        journal = {"version": 2, "files": saved}
        if firmware_before is not None:
            backup_data["firmware"] = firmware_before
            journal["firmware"] = firmware_before
        if environment_before is not None:
            backup_data["grub_environment"] = environment_before
            journal["grub_environment"] = environment_before
        identifier = self.backup(kind, summary, backup_data)
        journal["backup"] = identifier
        self.store.journal(journal)
        try:
            for key, data in updates.items():
                restore_file(paths[key], data)
            if verify:
                verify()
            self.store.finish()
        except Exception as exc:
            try:
                for key, data in saved.items():
                    restore_file(paths[key], data)
                if firmware_before is not None:
                    self.restore_firmware_state(firmware_before)
                if environment_before is not None:
                    self.restore_grub_environment(environment_before)
                self.store.finish()
            except Exception as rollback_error:
                raise RuntimeError(f"Update failed: {exc}. Rollback also failed: {rollback_error}. "
                                   f"Backup: {identifier}. Check boot settings before restarting.") from exc
            raise RuntimeError(f"Update failed; previous configuration restored. {exc}") from exc
        return identifier

    def check_grub(self, changed_keys=(), prepare=False):
        p = self.paths
        if not p.update.is_file() or not p.script_check.is_file():
            raise RuntimeError("update-grub and grub-script-check are required before changing GRUB.")
        if not p.defaults.is_file() or not p.config.is_file():
            raise RuntimeError("An existing Ubuntu GRUB installation is required.")
        for directory in (p.dropins, p.scripts):
            if prepare:
                directory.mkdir(mode=0o755, exist_ok=True)
            if directory.is_symlink():
                raise RuntimeError("Symbolic links in managed GRUB paths are not supported.")
        for path in (p.override, p.config, p.custom_script):
            if path.is_symlink():
                raise RuntimeError("Symbolic links in managed GRUB paths are not supported.")
        if changed_keys:
            keys = "|".join(re.escape(key) for key in changed_keys)
            for other in sorted(p.dropins.glob("*.cfg")):
                if other.name > p.override.name and re.search(rf"(?m)^\s*(?:export\s+)?(?:{keys})\s*=", other.read_text()):
                    raise RuntimeError(f"{other.name} overrides Uoo's settings. Adjust that file before applying changes.")

    def verify_grub(self, expected_entry=None):
        self.runner([self.paths.update])
        self.runner([self.paths.script_check, self.paths.config])
        regenerated = parse_entries(self.paths.config.read_text())
        if not regenerated:
            raise RuntimeError("The generated GRUB menu contains no readable boot entries.")
        if expected_entry and not any(entry.selector == expected_entry.selector and entry.title == expected_entry.title
                                     and entry.group == expected_entry.group for entry in regenerated):
            raise RuntimeError("The selected boot entry changed while GRUB was regenerated. Reload and try again.")

    def grub_transaction(self, new_contents, summary, expected_entry=None, new_mode=0o644, extra_updates=None, changed_keys=(), extra_verify=None):
        self.check_grub(changed_keys, prepare=True)
        updates = {"override": {"data": base64.b64encode(new_contents).decode() if new_contents is not None else None,
                                 "mode": new_mode}}
        updates.update(extra_updates or {})
        def verify():
            self.verify_grub(expected_entry)
            if extra_verify:
                extra_verify()
        return self.transaction(updates, summary, capture=("generated",), verify=verify)

    def dispatch(self, request):
        from .system import snapshot
        if not isinstance(request, dict):
            raise ValueError("Expected a JSON object.")
        action = request.get("action")
        fields = {
            "snapshot": set(), "list_backups": set(), "repair_plan": set(), "refresh_grub": set(),
            "clear_background": set(), "apply_grub": {"entry", "timeout", "style", "changes"},
            "apply_order": {"order"}, "next_boot": {"entry"}, "grub_next_boot": {"entry"},
            "custom_entries": {"entries"}, "set_active": {"entry", "active"},
            "create_uefi": {"label", "disk", "partition", "loader"}, "delete_uefi": {"entry", "confirm"},
            "create_backup": {"kind"}, "restore": {"backup"}, "delete_backup": {"backup"},
            "backup_details": {"backup"}, "export_backup": {"backup"}, "import_configuration": {"data"},
            "apply_background": {"png"}, "repair": {"plan", "confirm"},
        }
        if not isinstance(action, str) or action not in fields:
            raise ValueError("Unsupported action.")
        if set(request) - (fields[action] | {"action", "revision"}):
            raise ValueError("The request contains unsupported fields.")
        if "revision" in request and (not isinstance(request["revision"], str)
                or (request["revision"] and not re.fullmatch(r"[0-9a-f]{64}", request["revision"]))):
            raise ValueError("Invalid configuration revision.")
        if action == "apply_grub" and "changes" in request and set(request) & {"entry", "timeout", "style"}:
            raise ValueError("Use one GRUB settings format per request.")
        if action == "snapshot":
            return {"ok": True, "snapshot": snapshot(self.paths, self.runner), "message": "Boot options reloaded."}
        if action == "list_backups":
            return {"ok": True, "backups": self.list_backups(), "message": "Backups loaded."}
        reads = {"backup_details": self.backup_details, "export_backup": self.export_backup, "repair_plan": self.repair_plan}
        if isinstance(action, str) and action in reads:
            return {"ok": True, **reads[action](request)}
        operations = {"apply_grub": self.apply_grub, "apply_order": self.apply_order, "next_boot": self.next_boot,
                      "restore": self.restore, "grub_next_boot": self.grub_next_boot, "refresh_grub": self.refresh_grub,
                      "custom_entries": self.set_custom_entries, "set_active": self.set_active,
                      "create_uefi": self.create_uefi, "delete_uefi": self.delete_uefi,
                      "create_backup": self.create_backup, "delete_backup": self.delete_backup,
                      "import_configuration": self.import_configuration, "apply_background": self.apply_background,
                      "clear_background": self.clear_background, "repair": self.repair}
        if not isinstance(action, str) or action not in operations:
            raise ValueError("Unsupported action.")
        with self.lock():
            self.recover()
            if action == "custom_entries" and request.get("revision") and request["revision"] != revision(self.paths):
                raise ValueError("Configuration changed while this window was open. Reload before saving custom entries.")
            outcome = operations[action](request)
        details = outcome if isinstance(outcome, dict) else {"message": outcome}
        return {"ok": True, **details, "snapshot": snapshot(self.paths, self.runner), "backups": self.list_backups()}
