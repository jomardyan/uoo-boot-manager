"""System inspection and narrowly scoped, authenticated boot changes."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

from .core import DEMO_CONFIG, override_text, parse_entries, parse_firmware, parse_settings


SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/root"}
MANAGED_KEYS = {"GRUB_DEFAULT", "GRUB_TIMEOUT", "GRUB_TIMEOUT_STYLE", "GRUB_SAVEDEFAULT"}


@dataclass(frozen=True)
class Paths:
    defaults: Path = Path("/etc/default/grub")
    dropins: Path = Path("/etc/default/grub.d")
    config: Path = Path("/boot/grub/grub.cfg")
    state: Path = Path("/var/lib/uoo-boot-manager")
    efi: Path = Path("/sys/firmware/efi")
    update: Path = Path("/usr/sbin/update-grub")
    efibootmgr: Path = Path("/usr/bin/efibootmgr")
    script_check: Path = Path("/usr/bin/grub-script-check")

    @property
    def override(self):
        return self.dropins / "zz-uoo.cfg"


def run_command(args, timeout=120):
    try:
        result = subprocess.run([str(arg) for arg in args], cwd="/", env=SAFE_ENV,
                                capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The boot tool timed out. Check the system before retrying.") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot run {Path(args[0]).name}: {exc}") from exc
    if result.returncode:
        details = (result.stderr or result.stdout).strip()[-4000:]
        raise RuntimeError(f"{Path(args[0]).name} failed: {details or 'no details available'}")
    return result.stdout


def empty_firmware(error):
    return {"entries": [], "order": [], "current": "", "next": "", "available": False, "error": error}


def read_firmware(paths=Paths(), runner=run_command):
    if not paths.efi.is_dir():
        return empty_firmware("This session was not started in UEFI mode.")
    if not paths.efibootmgr.is_file():
        return empty_firmware("Install efibootmgr to manage UEFI boot options.")
    try:
        return parse_firmware(runner([paths.efibootmgr], timeout=15))
    except (RuntimeError, ValueError) as exc:
        return empty_firmware(str(exc))


def snapshot(paths=Paths(), runner=run_command):
    errors = []
    settings = {}
    entries = []
    try:
        entries = [asdict(entry) for entry in parse_entries(paths.config.read_text())]
    except (OSError, ValueError) as exc:
        errors.append(f"Cannot read GRUB entries: {exc}")
    try:
        settings.update(parse_settings(paths.defaults.read_text()))
        for path in sorted(paths.dropins.glob("*.cfg")):
            settings.update(parse_settings(path.read_text()))
    except OSError as exc:
        errors.append(f"Cannot read GRUB settings: {exc}")
    # Resolve saved defaults for display without evaluating any configuration.
    if settings.get("GRUB_DEFAULT") == "saved":
        try:
            env = paths.config.parent / "grubenv"
            data = env.read_bytes().decode("utf-8", "replace")
            match = re.search(r"(?m)^saved_entry=(.*)$", data)
            if match:
                settings["UOO_SAVED_ENTRY"] = match[1]
        except OSError:
            pass
    return {"entries": entries, "settings": settings,
            "firmware": read_firmware(paths, runner), "errors": errors,
            "demo": False, "managed": paths.override.exists()}


def demo_snapshot():
    return {"entries": [asdict(entry) for entry in parse_entries(DEMO_CONFIG)],
            "settings": {"GRUB_DEFAULT": "ubuntu", "GRUB_TIMEOUT": "10", "GRUB_TIMEOUT_STYLE": "menu"},
            "firmware": {"entries": [{"id": "0000", "label": "Ubuntu", "active": True},
                                       {"id": "0001", "label": "Windows Boot Manager", "active": True},
                                       {"id": "0002", "label": "USB drive", "active": True}],
                         "order": ["0000", "0001", "0002"], "current": "0000", "next": "",
                         "available": True, "error": ""},
            "errors": [], "demo": True, "managed": False}


def atomic_write(path, data, mode=0o644):
    """Replace a file in its own filesystem; never follow its existing symlink."""
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symbolic link: {path}")
    descriptor, name = tempfile.mkstemp(prefix=".uoo-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Manager:
    def __init__(self, paths=Paths(), runner=run_command):
        self.paths = paths
        self.runner = runner

    @staticmethod
    def private_directory(path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022):
            raise RuntimeError("Backup directories must be owned by the administrator and not writable by other users.")

    @contextmanager
    def lock(self):
        self.private_directory(self.paths.state)
        lock_path = self.paths.state / "lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Uoo change is in progress. Try again when it finishes.") from exc
            yield

    def backup(self, kind, summary, data):
        directory = self.paths.state / "backups"
        self.private_directory(directory)
        now = datetime.now(timezone.utc)
        backup_id = now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        record = {"id": backup_id, "created": now.isoformat(), "kind": kind,
                  "summary": summary, "data": data}
        atomic_write(directory / (backup_id + ".json"), json.dumps(record).encode(), 0o600)
        return backup_id

    def list_backups(self):
        records = []
        if not self.paths.state.exists():
            return records
        self.private_directory(self.paths.state)
        if not (self.paths.state / "backups").exists():
            return records
        self.private_directory(self.paths.state / "backups")
        for path in sorted((self.paths.state / "backups").glob("*.json"), reverse=True):
            if path.is_symlink():
                continue
            try:
                record = json.loads(path.read_text())
                records.append({key: record[key] for key in ("id", "created", "kind", "summary")})
            except (OSError, ValueError, KeyError):
                continue
        records.sort(key=lambda item: item["created"], reverse=True)
        return records

    def grub_transaction(self, new_contents, summary, expected_entry=None, new_mode=0o644):
        p = self.paths
        if not p.update.is_file():
            raise RuntimeError("update-grub is unavailable. This app requires Ubuntu's GRUB tools.")
        if not p.defaults.is_file() or not p.config.is_file():
            raise RuntimeError("An existing Ubuntu GRUB installation is required.")
        p.dropins.mkdir(mode=0o755, exist_ok=True)
        if p.dropins.is_symlink() or p.override.is_symlink() or p.config.is_symlink():
            raise RuntimeError("Symbolic links in managed GRUB paths are not supported.")
        # Ubuntu sources drop-ins in filename order. Never claim settings were
        # applied if a later file could override them, including shell expressions.
        for other in sorted(p.dropins.glob("*.cfg")):
            if other.name > p.override.name and re.search(
                r"(?m)^\s*(?:export\s+)?GRUB_(?:DEFAULT|TIMEOUT|TIMEOUT_STYLE|SAVEDEFAULT)\s*=", other.read_text()
            ):
                raise RuntimeError(f"{other.name} overrides Uoo's settings. Adjust that file before applying changes.")
        previous = p.override.read_bytes() if p.override.exists() else None
        previous_mode = stat.S_IMODE(p.override.stat().st_mode) if previous is not None else 0o644
        generated = p.config.read_bytes()
        generated_mode = stat.S_IMODE(p.config.stat().st_mode)
        backup_id = self.backup("grub", summary, {
            "override": base64.b64encode(previous).decode() if previous is not None else None,
            "mode": previous_mode,
        })
        try:
            if new_contents is None:
                p.override.unlink(missing_ok=True)
            else:
                atomic_write(p.override, new_contents, new_mode)
            self.runner([p.update])
            if p.script_check.is_file():
                self.runner([p.script_check, p.config])
            regenerated = parse_entries(p.config.read_text())
            if not regenerated:
                raise RuntimeError("The generated GRUB menu contains no readable boot entries.")
            if expected_entry and not any(entry.selector == expected_entry.selector
                                         and entry.title == expected_entry.title
                                         and entry.group == expected_entry.group for entry in regenerated):
                raise RuntimeError("The selected boot entry changed while GRUB was regenerated. Reload and try again.")
        except Exception as exc:
            try:
                if previous is None:
                    p.override.unlink(missing_ok=True)
                else:
                    atomic_write(p.override, previous, previous_mode)
                atomic_write(p.config, generated, generated_mode)
            except Exception as rollback_error:
                raise RuntimeError(f"GRUB update failed: {exc}. Rollback also failed: {rollback_error}. "
                                   f"Backup: {backup_id}. Check GRUB before restarting.") from exc
            raise RuntimeError(f"GRUB update failed; previous configuration restored. {exc}") from exc
        return backup_id

    def apply_grub(self, request):
        contents = override_text(request.get("entry"), request.get("timeout"), request.get("style"))
        entries = parse_entries(self.paths.config.read_text())
        selected = next((entry for entry in entries if entry.selector == request["entry"]), None)
        if selected is None:
            raise ValueError("The selected boot entry is no longer present. Reload and select it again.")
        backup_id = self.grub_transaction(contents.encode(), "GRUB settings before changing the default or menu", selected)
        return f"GRUB settings applied. Previous settings saved as {backup_id}."

    def firmware(self):
        firmware = read_firmware(self.paths, self.runner)
        if not firmware["available"]:
            raise RuntimeError(firmware["error"])
        return firmware

    @staticmethod
    def validate_order(order, firmware, preserve=True):
        if not isinstance(order, list) or not order or len(order) > 1024:
            raise ValueError("A nonempty boot order is required.")
        if any(not isinstance(item, str) or not re.fullmatch(r"[0-9A-F]{4}", item) for item in order):
            raise ValueError("Invalid UEFI boot entry identifier.")
        if len(set(order)) != len(order):
            raise ValueError("The boot order contains duplicate entries.")
        known = {entry["id"] for entry in firmware["entries"]}
        if not set(order) <= known:
            raise ValueError("A UEFI entry is no longer present. Reload before applying changes.")
        if preserve and set(order) != set(firmware["order"]):
            raise ValueError("Reordering must preserve every entry in the current boot order. Reload first.")

    def firmware_transaction(self, firmware, arguments, expected, summary, kind="uefi"):
        backup_id = self.backup(kind, summary, {"order": firmware["order"], "next": firmware["next"]})
        try:
            self.runner([self.paths.efibootmgr, *arguments], timeout=15)
            after = self.firmware()
            if any(after[key] != value for key, value in expected.items()):
                raise RuntimeError("The firmware did not retain the requested setting.")
        except Exception as exc:
            try:
                if "order" in expected:
                    rollback = ["--bootorder", ",".join(firmware["order"])] if firmware["order"] else ["--delete-bootorder"]
                else:
                    rollback = ["--bootnext", firmware["next"]] if firmware["next"] else ["--delete-bootnext"]
                self.runner([self.paths.efibootmgr, *rollback], timeout=15)
                after = self.firmware()
                if any(after[key] != firmware[key] for key in expected):
                    raise RuntimeError("Firmware rollback could not be verified.")
            except Exception as rollback_error:
                raise RuntimeError(f"UEFI change failed: {exc}. Rollback also failed: {rollback_error}. "
                                   f"Backup: {backup_id}. Check firmware settings before restarting.") from exc
            raise RuntimeError(f"UEFI change failed; previous setting restored. {exc}") from exc
        return backup_id

    def apply_order(self, request):
        firmware = self.firmware()
        order = request.get("order")
        self.validate_order(order, firmware)
        backup_id = self.firmware_transaction(firmware, ["--bootorder", ",".join(order)], {"order": order},
                                              "UEFI boot order before reordering")
        return f"UEFI boot order saved. Previous order saved as {backup_id}."

    def next_boot(self, request):
        firmware = self.firmware()
        entry = request.get("entry")
        if not isinstance(entry, str) or (entry and not re.fullmatch(r"[0-9A-F]{4}", entry)):
            raise ValueError("Invalid UEFI boot entry identifier.")
        if entry and entry not in {item["id"] for item in firmware["entries"]}:
            raise ValueError("The selected UEFI entry is no longer present. Reload first.")
        args = ["--bootnext", entry] if entry else ["--delete-bootnext"]
        self.firmware_transaction(firmware, args, {"next": entry},
                                  "UEFI one-time boot choice before changing it", kind="next_boot")
        return "One-time boot choice saved for your next restart." if entry else "One-time boot choice cleared."

    def restore(self, request):
        backup_id = request.get("backup")
        if not isinstance(backup_id, str) or not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{12}", backup_id):
            raise ValueError("Invalid backup identifier.")
        path = self.paths.state / "backups" / (backup_id + ".json")
        self.private_directory(self.paths.state / "backups")
        if path.is_symlink():
            raise ValueError("Symbolic-link backups are not supported.")
        record = json.loads(path.read_text())
        data = record["data"]
        if record["kind"] == "grub":
            contents = base64.b64decode(data["override"], validate=True) if data["override"] is not None else None
            self.grub_transaction(contents, "GRUB settings before restoring a backup", new_mode=data.get("mode", 0o644))
        elif record["kind"] == "uefi":
            firmware = self.firmware()
            self.validate_order(data["order"], firmware, preserve=False)
            # Retain newly added entries when restoring an older firmware order.
            order = data["order"] + [item for item in firmware["order"] if item not in data["order"]]
            self.firmware_transaction(firmware, ["--bootorder", ",".join(order)], {"order": order},
                                      "UEFI boot order before restoring a backup")
        elif record["kind"] == "next_boot":
            self.next_boot({"entry": data["next"]})
        else:
            raise ValueError("Unknown backup type.")
        return "Backup restored. Current settings were backed up first."

    def dispatch(self, request):
        if not isinstance(request, dict):
            raise ValueError("Expected a JSON object.")
        action = request.get("action")
        if action == "snapshot":
            return {"ok": True, "snapshot": snapshot(self.paths, self.runner), "message": "Boot options reloaded."}
        if action == "list_backups":
            return {"ok": True, "backups": self.list_backups(), "message": "Backups loaded."}
        operations = {"apply_grub": self.apply_grub, "apply_order": self.apply_order,
                      "next_boot": self.next_boot, "restore": self.restore}
        if not isinstance(action, str) or action not in operations:
            raise ValueError("Unsupported action.")
        with self.lock():
            message = operations[action](request)
        return {"ok": True, "message": message, "snapshot": snapshot(self.paths, self.runner),
                "backups": self.list_backups()}
