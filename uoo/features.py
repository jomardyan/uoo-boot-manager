"""GRUB settings, managed menu entries, images, and configuration backups."""

import base64
import json
from pathlib import Path
import re
import shlex

from .core import parse_entries, parse_settings
from .models import compile_custom_entries, render_grub_settings, validate_custom_entries, validate_grub_changes, validate_png
from .storage import file_state, read_regular
from . import platform as facts


def state_bytes(data, mode=0o644):
    return {"data": base64.b64encode(data).decode() if data is not None else None, "mode": mode}


class Features:
    def validate_grub_environment(self, state):
        if (not isinstance(state, dict) or not {"saved_entry", "next_entry"} <= set(state)
                or set(state) - {"saved_entry", "next_entry", "prev_saved_entry"}):
            raise ValueError("Invalid GRUB environment snapshot.")
        for value in state.values():
            if value is not None and (not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError("Invalid GRUB environment value.")
        if not self.paths.editenv.is_file():
            raise RuntimeError("grub-editenv is required to restore the GRUB environment safely.")

    def restore_grub_environment(self, state):
        self.validate_grub_environment(state)
        from .production import environment
        current = environment(self.paths, self.runner, strict=True)
        for key, value in state.items():
            if current.get(key) == value:
                continue
            if value is None:
                self.runner([self.paths.editenv, self.paths.grubenv, "unset", key])
            else:
                self.runner([self.paths.editenv, self.paths.grubenv, "set", key + "=" + value])
        after = environment(self.paths, self.runner, strict=True)
        if any(after.get(key) != value for key, value in state.items()):
            raise RuntimeError("GRUB environment recovery could not be verified.")

    def own_settings(self):
        from .production import typed_settings
        raw = read_regular(self.paths.override)
        return typed_settings(parse_settings(raw.decode())) if raw else {}

    def verify_restored_grub_choices(self):
        """A valid old configuration may reference kernels removed since backup."""
        entries = parse_entries(self.paths.config.read_text())
        known = {value for entry in entries for value in (
            entry.selector, entry.position,
            (entry.group.replace(" / ", ">") + ">" if entry.group else "") + entry.title)}
        settings = parse_settings(self.paths.defaults.read_text())
        for path in sorted(self.paths.dropins.glob("*.cfg")):
            settings.update(parse_settings(path.read_text()))
        selector = settings.get("GRUB_DEFAULT", "0")
        from .production import environment
        values = environment(self.paths, self.runner, strict=True) if selector == "saved" or self.paths.editenv.is_file() else {}
        if selector == "saved":
            selector = values.get("saved_entry") or "0"
        if selector not in known:
            raise RuntimeError("The backup's default GRUB entry is no longer available. Choose an available entry before importing its settings.")
        if values.get("next_entry") and values["next_entry"] not in known:
            raise RuntimeError("The backup's one-time GRUB entry is no longer available.")

    def render_settings(self, settings, background=None):
        value = render_grub_settings(settings)
        background = self.paths.background.exists() if background is None else background
        if background:
            value += "GRUB_BACKGROUND=" + shlex.quote(str(self.paths.background)) + "\n"
            value += "GRUB_TERMINAL_OUTPUT=gfxterm\n"
        return value.encode()

    def apply_grub(self, request):
        if "changes" in request:
            changes = validate_grub_changes(request["changes"])
        else:
            changes = validate_grub_changes({"GRUB_DEFAULT": request.get("entry"), "GRUB_TIMEOUT": request.get("timeout"),
                                             "GRUB_TIMEOUT_STYLE": request.get("style"), "GRUB_SAVEDEFAULT": False})
        if not changes:
            raise ValueError("No GRUB setting changes were supplied.")
        settings = self.own_settings()
        settings.update(changes)
        selected = None
        if settings.get("GRUB_SAVEDEFAULT"):
            settings["GRUB_DEFAULT"] = "saved"
        selector = settings.get("GRUB_DEFAULT")
        if selector == "saved":
            supported, reason = facts.env_support(self.paths, self.runner)
            if not supported:
                raise ValueError(reason)
        elif selector is not None:
            selected = next((entry for entry in parse_entries(self.paths.config.read_text()) if entry.selector == selector
                             or entry.position == selector), None)
            if selected is None:
                raise ValueError("The selected boot entry is no longer present. Reload and select it again.")
        identifier = self.grub_transaction(self.render_settings(settings), "GRUB settings before changing configuration",
                                           selected, changed_keys=settings)
        return f"GRUB settings applied. Previous settings saved as {identifier}."

    def refresh_grub(self, request):
        self.check_grub()
        self.transaction({}, "GRUB menu before rescanning installed systems", capture=("generated",), verify=self.verify_grub)
        return "The GRUB menu was regenerated and verified. OS discovery follows your Advanced settings."

    def check_custom_files(self, entries):
        inventory = facts.devices(self.paths, self.runner)
        for entry in entries:
            if not entry["enabled"]:
                continue
            if entry["type"] == "efi" and not self.paths.efi.is_dir():
                raise ValueError("EFI chainloader entries can be enabled only when the computer boots in UEFI mode.")
            device = next((node for node in inventory if node["uuid"].lower() == entry["uuid"].lower()), None)
            if device is None:
                raise ValueError(f"The filesystem for {entry['title']} is not present.")
            if not device["mountpoint"]:
                raise ValueError(f"Mount the filesystem for {entry['title']} before adding an enabled entry, so its files can be checked.")
            mount = Path(device["mountpoint"]).resolve()
            paths = [entry["loader"]] if entry["type"] == "efi" else [entry["kernel"], entry["initrd"]]
            for filename in filter(None, paths):
                candidate = (mount / filename.lstrip("/")).resolve()
                if not candidate.is_relative_to(mount) or not candidate.is_file():
                    raise ValueError(f"The boot file {filename} for {entry['title']} is missing or leaves the selected filesystem.")
                facts.verify_file_filesystem(candidate, entry["uuid"], self.paths, self.runner)

    def custom_updates(self, entries):
        payload = compile_custom_entries(entries)
        # Data is emitted through a quoted heredoc, never sourced as shell text.
        script = "#!/bin/sh\n# Managed by Uoo Boot Manager.\ncat <<'UOO_GRUB_ENTRIES'\n" + payload + "UOO_GRUB_ENTRIES\n"
        return {"custom_script": state_bytes(script.encode(), 0o755),
                "custom_model": state_bytes(json.dumps(entries, sort_keys=True).encode(), 0o600)}

    def set_custom_entries(self, request):
        entries = validate_custom_entries(request.get("entries"))
        self.check_grub()
        self.check_custom_files(entries)
        from .production import custom_model, environment
        old = custom_model(self.paths)
        enabled = {entry["id"] for entry in entries if entry["enabled"]}
        removed = {entry["id"] for entry in old} - enabled
        raw_settings = parse_settings(self.paths.defaults.read_text())
        for path in sorted(self.paths.dropins.glob("*.cfg")):
            raw_settings.update(parse_settings(path.read_text()))
        current = raw_settings.get("GRUB_DEFAULT", "0")
        current_environment = environment(self.paths, self.runner, strict=True)
        saved = current_environment.get("saved_entry")
        live = parse_entries(self.paths.config.read_text())
        def resolve(value):
            entry = next((item for item in live if value in (item.selector, item.position,
                         (item.group.replace(" / ", ">") + ">" if item.group else "") + item.title)), None)
            return entry.selector if entry else value
        if resolve(current) in removed or (current == "saved" and resolve(saved) in removed):
            raise ValueError("Choose another default boot entry before disabling or removing the current custom default.")
        if resolve(current_environment.get("next_entry")) in removed:
            raise ValueError("Clear the one-time GRUB boot choice before removing its custom entry.")
        def verify():
            self.verify_grub()
            selectors = {entry.selector for entry in parse_entries(self.paths.config.read_text())}
            if not enabled <= selectors:
                raise RuntimeError("Not every enabled custom entry appeared in the regenerated menu.")
        self.check_grub(prepare=True)
        self.transaction(self.custom_updates(entries), "Custom boot entries before editing", capture=("generated",), verify=verify)
        return f"Saved {len(entries)} custom entries and verified the GRUB menu."

    def grub_next_boot(self, request):
        entry = request.get("entry")
        if not isinstance(entry, str):
            raise ValueError("Invalid GRUB boot entry.")
        supported, reason = facts.env_support(self.paths, self.runner)
        if not supported:
            raise ValueError(reason)
        if not self.paths.editenv.is_file() or (entry and not self.paths.grub_reboot.is_file()):
            raise RuntimeError("The GRUB environment tools are unavailable.")
        if entry and entry not in {item.selector for item in parse_entries(self.paths.config.read_text())}:
            raise ValueError("The selected GRUB entry is no longer present.")
        from .production import environment
        current_environment = environment(self.paths, self.runner, strict=True)
        before = {key: current_environment.get(key) for key in ("saved_entry", "next_entry", "prev_saved_entry")}
        def verify():
            if entry:
                self.runner([self.paths.grub_reboot, "--boot-directory=" + str(self.paths.config.parent.parent), entry])
            else:
                self.runner([self.paths.editenv, self.paths.grubenv, "unset", "next_entry"])
            if environment(self.paths, self.runner, strict=True).get("next_entry", "") != entry:
                raise RuntimeError("The GRUB one-time boot choice could not be verified.")
        self.transaction({}, "GRUB one-time choice before changing it", kind="grub_next", capture=("grubenv",), verify=verify,
                         environment_before=before)
        return "GRUB one-time boot choice saved." if entry else "GRUB one-time boot choice cleared."

    def apply_background(self, request):
        text = request.get("png")
        if not isinstance(text, str) or len(text) > 3 * 1024 * 1024:
            raise ValueError("Choose a PNG image no larger than 2 MiB.")
        try:
            contents = base64.b64decode(text, validate=True)
        except ValueError as exc:
            raise ValueError("The background image data is invalid.") from exc
        validate_png(contents)
        settings = self.own_settings()
        self.grub_transaction(self.render_settings(settings, background=True), "GRUB appearance before changing background",
                              extra_updates={"background": state_bytes(contents)}, changed_keys=("GRUB_BACKGROUND", "GRUB_TERMINAL_OUTPUT"))
        return "The background image was saved in the boot partition and GRUB was regenerated."

    def clear_background(self, request):
        self.grub_transaction(self.render_settings(self.own_settings(), background=False), "GRUB appearance before clearing background",
                              extra_updates={"background": state_bytes(None)}, changed_keys=("GRUB_BACKGROUND", "GRUB_TERMINAL_OUTPUT"))
        return "The managed background image was cleared."

    def create_backup(self, request):
        kind = request.get("kind", "all")
        if kind not in ("grub", "uefi", "all"):
            raise ValueError("Unknown backup scope.")
        data = {"configuration": self.configuration()}
        if kind in ("grub", "all"):
            self.check_grub()
            keys = ("override", "generated", "custom_script", "custom_model", "grubenv", "background")
            data["files"] = {key: file_state(self.file_paths()[key]) for key in keys}
            from .production import environment
            values = environment(self.paths, self.runner, strict=True)
            data["grub_environment"] = {key: values.get(key) for key in ("saved_entry", "next_entry", "prev_saved_entry")}
        if kind == "uefi" or (kind == "all" and self.paths.efi.is_dir() and self.paths.efibootmgr.is_file()):
            data["firmware"] = self.full_firmware_state()
        identifier = self.backup(kind, "Manually saved " + kind.upper() + " configuration", data)
        return {"message": f"Backup created: {identifier}.", "backup": identifier}

    def backup_details(self, request):
        record = self.store.read_backup(request.get("backup"))
        return {"backup": {key: record[key] for key in ("id", "created", "kind", "summary")},
                "configuration": record["data"].get("configuration", {})}

    def export_backup(self, request):
        record = self.store.read_backup(request.get("backup"))
        configuration = record["data"].get("configuration")
        if configuration is None:
            from .production import typed_settings
            raw = record["data"].get("override")
            configuration = {"grub": typed_settings(parse_settings(base64.b64decode(raw).decode())) if raw else {}, "custom_entries": []}
        envelope = {"format": "uoo-configuration", "version": 1, "configuration": configuration}
        return {"data": json.dumps(envelope, indent=2, sort_keys=True), "message": "Portable configuration exported."}

    def delete_backup(self, request):
        self.store.delete_backup(request.get("backup"))
        return "The selected backup was deleted."

    def import_configuration(self, request):
        data = request.get("data")
        if (not isinstance(data, dict) or set(data) != {"format", "version", "configuration"}
                or data.get("format") != "uoo-configuration" or type(data.get("version")) is not int or data["version"] != 1):
            raise ValueError("This is not a supported Uoo portable configuration.")
        configuration = data["configuration"]
        if (not isinstance(configuration, dict) or not {"grub", "custom_entries"} <= set(configuration)
                or set(configuration) - {"grub", "custom_entries", "firmware"}):
            raise ValueError("The import must contain GRUB settings and custom entries only.")
        settings = validate_grub_changes(configuration["grub"])
        entries = validate_custom_entries(configuration["custom_entries"])
        self.check_custom_files(entries)
        if settings.get("GRUB_DEFAULT") == "saved":
            supported, reason = facts.env_support(self.paths, self.runner)
            if not supported:
                raise ValueError(reason)
        selector = settings.get("GRUB_DEFAULT")
        def verify():
            actual = parse_entries(self.paths.config.read_text())
            known = {entry.selector for entry in actual} | {entry.position for entry in actual}
            if selector and selector != "saved" and selector not in known:
                raise RuntimeError("The imported default entry is not available on this computer.")
            enabled = {entry["id"] for entry in entries if entry["enabled"]}
            if not enabled <= known:
                raise RuntimeError("Some imported custom entries did not appear in the generated menu.")
        self.grub_transaction(self.render_settings(settings), "Configuration before importing portable settings",
                              extra_updates=self.custom_updates(entries), changed_keys=settings, extra_verify=verify)
        return "Imported GRUB settings and custom entries. Firmware settings were preserved."

    def restore(self, request):
        record = self.store.read_backup(request.get("backup"))
        data = record["data"]
        if "grub_environment" in data:
            self.validate_grub_environment(data["grub_environment"])
        if record["kind"] == "grub_next" and "files" in data:
            saved = data["files"].get("grubenv", {})
            raw = base64.b64decode(saved.get("data") or "", validate=True).decode("utf-8", "replace")
            match = re.search(r"(?m)^next_entry=(.*)$", raw)
            next_entry = (data["grub_environment"]["next_entry"] or "") if "grub_environment" in data else (match[1] if match else "")
            self.grub_next_boot({"entry": next_entry})
            return "The one-time GRUB choice was restored. Saved default and other environment values were preserved."
        if "bios" in data:
            self.validate_bios_state(data["bios"])
            plan = self.repair_plan({})["plan"]
            if not plan["supported"] or plan["mode"] != "bios":
                raise ValueError("This BIOS backup can be restored only on its verified BIOS boot disk.")
            current = self.capture_bios(plan)
            tree = self.capture_boot_tree()
            self.backup("bios_repair", "BIOS sectors before restoring a backup",
                        {"bios": current, "boot_tree": tree, "files": {"generated": file_state(self.paths.config)}, "configuration": self.configuration()})
            self.store.journal({"version": 2, "files": {"generated": file_state(self.paths.config)}, "bios": current, "boot_tree": tree})
            try:
                if data.get("boot_tree"):
                    self.restore_boot_tree(data["boot_tree"])
                self.restore_bios_state(data["bios"])
                self.store.finish()
            except Exception:
                self.restore_boot_tree(tree)
                self.restore_bios_state(current)
                self.store.finish()
                raise
            return "BIOS boot sectors were restored. The current sectors were backed up first."
        if "files" in data:
            saved = data["files"]
            if not isinstance(saved, dict) or not set(saved) <= set(self.file_paths()):
                raise RuntimeError("The backup contains unsupported file paths.")
            # Regenerate instead of restoring an obsolete generated menu after
            # kernel upgrades; retain its original snapshot for error rollback.
            updates = {key: value for key, value in saved.items() if key != "generated"}
            self.check_grub()
            if "firmware" in data:
                self.validate_firmware_restore(data["firmware"])
                before = self.firmware_rollback_target(self.firmware(), data["firmware"])
            else:
                before = None
            def verify():
                self.verify_grub()
                if "grub_environment" in data:
                    self.restore_grub_environment(data["grub_environment"])
                self.verify_restored_grub_choices()
                if "firmware" in data:
                    self.restore_firmware_state(data["firmware"])
            from .production import environment
            current_environment = environment(self.paths, self.runner, strict=True) if "grub_environment" in data else {}
            semantic_before = {key: current_environment.get(key) for key in ("saved_entry", "next_entry", "prev_saved_entry")} if "grub_environment" in data else None
            self.transaction(updates, "Configuration before restoring a backup", kind=record["kind"],
                             capture=("generated",), verify=verify, firmware_before=before, environment_before=semantic_before)
        elif "firmware" in data:
            self.validate_firmware_restore(data["firmware"])
            self.restore_firmware_backup(data["firmware"])
        elif record["kind"] == "grub":
            contents = base64.b64decode(data["override"], validate=True) if data["override"] is not None else None
            self.grub_transaction(contents, "GRUB settings before restoring a legacy backup", new_mode=data.get("mode", 0o644),
                                  extra_verify=self.verify_restored_grub_choices)
        elif record["kind"] == "next_boot":
            self.next_boot({"entry": data["next"]})
        elif record["kind"] == "uefi":
            self.apply_order({"order": data["order"] + [item for item in self.firmware()["order"] if item not in data["order"]]})
        else:
            raise ValueError("Unknown backup type.")
        return "Backup restored. Current settings were backed up first."
