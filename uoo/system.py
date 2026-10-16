"""System inspection and narrowly scoped, authenticated boot changes."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import signal
import subprocess

from .advice import explain
from .core import parse_entries, parse_firmware, parse_settings


SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/root"}


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
    scripts: Path = Path("/etc/grub.d")
    efi_directory: Path = Path("/boot/efi")
    editenv: Path = Path("/usr/bin/grub-editenv")
    grub_reboot: Path = Path("/usr/sbin/grub-reboot")
    grub_set_default: Path = Path("/usr/sbin/grub-set-default")
    grub_install: Path = Path("/usr/sbin/grub-install")
    grub_probe: Path = Path("/usr/sbin/grub-probe")
    lsblk: Path = Path("/usr/bin/lsblk")
    findmnt: Path = Path("/usr/bin/findmnt")
    os_release: Path = Path("/etc/os-release")
    sys_block: Path = Path("/sys/class/block")
    mkrelpath: Path = Path("/usr/bin/grub-mkrelpath")
    bios_modules: Path = Path("/usr/lib/grub/i386-pc")
    os_prober: Path = Path("/usr/bin/os-prober")

    @property
    def override(self):
        return self.dropins / "zz-uoo.cfg"

    @property
    def grubenv(self):
        return self.config.parent / "grubenv"

    @property
    def custom_script(self):
        return self.scripts / "41_uoo"

    @property
    def custom_model(self):
        return self.state / "custom-entries.json"

    @property
    def background(self):
        return self.config.parent / "uoo-background.png"


def run_command(args, timeout=120):
    try:
        process = subprocess.Popen([str(arg) for arg in args], cwd="/", env=SAFE_ENV,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", start_new_session=True)
        try:
            output, error = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise RuntimeError(f"{Path(args[0]).name} timed out; its child processes were stopped.")
    except OSError as exc:
        raise RuntimeError(f"Cannot run {Path(args[0]).name}: {exc}") from exc
    if process.returncode:
        details = (error or output).strip()[-4000:]
        hint = explain(Path(args[0]).name, details)
        raise RuntimeError(f"{Path(args[0]).name} failed: {details or 'no details available'}" + (f"\n\nWhat this usually means: {hint}" if hint else ""))
    return output


def empty_firmware(error):
    return {"entries": [], "order": [], "current": "", "next": "", "available": False, "error": error}


def read_firmware(paths=Paths(), runner=run_command):
    if not paths.efi.is_dir():
        return empty_firmware("This session was not started in UEFI mode.")
    if not paths.efibootmgr.is_file():
        return empty_firmware("Install efibootmgr to manage UEFI boot options.")
    try:
        output = runner([paths.efibootmgr, "--verbose"], timeout=15)
        result = parse_firmware(output)
        lookup = {entry["id"]: entry for entry in result["entries"]}
        for line in output.splitlines():
            match = re.match(r"Boot([0-9A-Fa-f]{4})\*?\s+[^\t]*\t(.*)$", line)
            if match and match[1].upper() in lookup:
                lookup[match[1].upper()]["device_path"] = match[2]
        return result
    except (RuntimeError, ValueError) as exc:
        return empty_firmware(str(exc))


def _basic_snapshot(paths=Paths(), runner=run_command):
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
            env = paths.grubenv
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
    from .demo import DemoManager
    return DemoManager().dispatch({"action": "snapshot"})["snapshot"]


def snapshot(paths=Paths(), runner=run_command):
    """Inspect capabilities without elevating or changing system state."""
    from .production import custom_model, environment, revision
    from . import platform as facts
    result = _basic_snapshot(paths, runner)
    editable = True
    try:
        custom = custom_model(paths)
    except (OSError, ValueError, RuntimeError) as exc:
        custom, editable = [], False
        result["errors"].append(f"Custom entries need administrator access or repair: {exc}")
    try:
        inventory = facts.devices(paths, runner)
    except (OSError, ValueError, RuntimeError) as exc:
        inventory = []
        result["errors"].append(f"Device inspection failed: {exc}")
    diagnostic = facts.diagnostics(paths, runner, inventory)
    try:
        env = environment(paths, runner, strict=True)
        environment_readable = True
    except (OSError, ValueError, RuntimeError) as exc:
        env, environment_readable = {}, False
        result["errors"].append(f"GRUB environment could not be read: {exc}")
    grub = paths.update.is_file() and paths.script_check.is_file() and paths.defaults.is_file() and paths.config.is_file()
    result.update(custom_entries=custom, devices=inventory, diagnostics=diagnostic, grub_next=env.get("next_entry", ""),
                  grub_saved=env.get("saved_entry", ""), background=paths.background.is_file(), revision=revision(paths),
                  capabilities={"grub": grub, "uefi": result["firmware"]["available"], "custom_entries": grub and editable,
                                "grub_next": grub and diagnostic["environment_supported"] and environment_readable,
                                "background": grub, "repair": paths.grub_install.is_file()})
    return result


from .production import ProductionManager as Manager
