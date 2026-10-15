"""Private, bounded backups and crash-recoverable file updates."""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

MAX_RECORD = 24 * 1024 * 1024
BACKUP_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}")


def atomic_write(path, data, mode=0o644):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symbolic link: {path}")
    descriptor, name = tempfile.mkstemp(prefix=".uoo-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode & 0o777)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if Path(name).exists():
            os.unlink(name)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError("Backup directories must be owned by the administrator and not writable by other users.")


def read_regular(path, maximum=MAX_RECORD, private=False):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"Cannot read a regular file: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise RuntimeError(f"File is not a regular bounded file: {path}")
        if private and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise RuntimeError(f"Private state file has unsafe ownership or permissions: {path}")
    except Exception:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        contents = stream.read(maximum + 1)
        if len(contents) > maximum:
            raise RuntimeError(f"File grew beyond its permitted size: {path}")
        return contents


def file_state(path):
    contents = read_regular(path)
    return {"data": base64.b64encode(contents).decode() if contents is not None else None,
            "mode": stat.S_IMODE(path.stat().st_mode) if contents is not None else 0o644}


def restore_file(path, record):
    if not isinstance(record, dict) or set(record) != {"data", "mode"}:
        raise RuntimeError("Invalid file snapshot.")
    if type(record["mode"]) is not int or not 0 <= record["mode"] <= 0o777:
        raise RuntimeError("Invalid snapshot permissions.")
    if record["data"] is None:
        if path.is_symlink():
            raise RuntimeError(f"Refusing to remove a symbolic link: {path}")
        path.unlink(missing_ok=True)
        sync_directory(path.parent)
    else:
        if not isinstance(record["data"], str):
            raise RuntimeError("Invalid snapshot content.")
        atomic_write(path, base64.b64decode(record["data"], validate=True), record["mode"])


def sealed(record):
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    envelope = json.dumps({"record": record, "sha256": hashlib.sha256(raw).hexdigest()}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=True).encode()
    # Readers cap the stored file, not the canonical record, at MAX_RECORD.
    if len(envelope) > MAX_RECORD:
        raise RuntimeError("The backup is too large.")
    return envelope


def unseal(raw):
    try:
        envelope = json.loads(raw)
        if set(envelope) != {"record", "sha256"}:
            raise ValueError("Invalid envelope.")
        canonical = json.dumps(envelope["record"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        if hashlib.sha256(canonical).hexdigest() != envelope["sha256"]:
            raise ValueError("Checksum mismatch.")
        return envelope["record"]
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("The saved state is corrupt or has been changed.") from exc


class Store:
    def __init__(self, directory):
        self.directory = directory

    @contextmanager
    def lock(self):
        private_directory(self.directory)
        descriptor = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise RuntimeError("The state lock has unsafe ownership or permissions.")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Uoo change is in progress. Try again when it finishes.") from exc
            yield

    def backup(self, kind, summary, data):
        private_directory(self.directory / "backups")
        now = datetime.now(timezone.utc)
        identifier = now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        record = {"version": 2, "id": identifier, "created": now.isoformat(), "kind": kind,
                  "summary": summary, "data": data}
        atomic_write(self.directory / "backups" / (identifier + ".json"), sealed(record), 0o600)
        return identifier

    def read_backup(self, identifier):
        if not isinstance(identifier, str) or not BACKUP_ID.fullmatch(identifier):
            raise ValueError("Invalid backup identifier.")
        private_directory(self.directory / "backups")
        raw = read_regular(self.directory / "backups" / (identifier + ".json"), private=True)
        if raw is None:
            raise ValueError("The selected backup no longer exists.")
        # Read version-1 backups created by the first release, but never import
        # untrusted raw files through this compatibility path.
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("Invalid backup metadata.")
        if "record" not in parsed and parsed.get("version", 1) != 1:
            raise RuntimeError("Version-2 backups must have an integrity envelope.")
        record = unseal(raw) if "record" in parsed else parsed
        required = {"id", "created", "kind", "summary", "data"}
        if not isinstance(record, dict) or not required <= set(record) or record["id"] != identifier:
            raise RuntimeError("Invalid backup metadata.")
        if not all(isinstance(record[key], str) for key in ("id", "created", "kind", "summary")):
            raise RuntimeError("Invalid backup metadata types.")
        if not isinstance(record["data"], dict):
            raise RuntimeError("Invalid backup content.")
        return record

    def list_backups(self):
        if not self.directory.exists():
            return []
        private_directory(self.directory)
        directory = self.directory / "backups"
        if not directory.exists():
            return []
        private_directory(directory)
        result = []
        for path in directory.glob("*.json"):
            if not BACKUP_ID.fullmatch(path.stem):
                continue
            try:
                record = self.read_backup(path.stem)
                result.append({key: record[key] for key in ("id", "created", "kind", "summary")})
            except (OSError, RuntimeError, ValueError):
                result.append({"id": path.stem, "created": "", "kind": "corrupt",
                               "summary": "Unreadable backup; restoration is disabled."})
        result.sort(key=lambda item: item["created"], reverse=True)
        return result

    def delete_backup(self, identifier):
        if not isinstance(identifier, str) or not BACKUP_ID.fullmatch(identifier):
            raise ValueError("Invalid backup identifier.")
        private_directory(self.directory / "backups")
        path = self.directory / "backups" / (identifier + ".json")
        if read_regular(path, private=True) is None:
            raise ValueError("The selected backup no longer exists.")
        path.unlink()
        sync_directory(self.directory / "backups")

    def journal(self, record):
        atomic_write(self.directory / "pending.json", sealed(record), 0o600)

    def pending(self):
        path = self.directory / "pending.json"
        if not path.exists() and not path.is_symlink():
            return None
        private_directory(self.directory)
        return unseal(read_regular(path, private=True))

    def finish(self):
        (self.directory / "pending.json").unlink(missing_ok=True)
        sync_directory(self.directory)
