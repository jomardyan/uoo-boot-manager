"""Native Ubuntu boot manager using GTK4 and libadwaita."""

import argparse
import base64
import copy
import json
from pathlib import Path
import subprocess
import threading
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import system
from . import desktop_runtime
from .demo import DemoManager
from .models import validate_custom_entries, validate_grub_changes, validate_png
from . import gtk_widgets as ui


HELPER = Path("/usr/lib/uoo-boot-manager/helper.py")
AUTH_HELPER = Path("/usr/bin/uoo-boot-helper")
APP_ICON = "org.uoo.BootManager"
STYLE_LABELS = ("Show menu", "Hidden", "Countdown")
STYLE_VALUES = ("menu", "hidden", "countdown")
BACKUP_TYPES = {
    "grub": "GRUB configuration", "uefi": "UEFI firmware", "all": "Complete configuration",
    "next_boot": "UEFI next boot", "grub_next": "GRUB next boot", "uefi_order": "UEFI boot order",
    "uefi_active": "UEFI entry state", "uefi_created": "Added UEFI entry", "uefi_deleted": "Removed UEFI entry",
    "uefi_repair": "EFI loader repair", "bios_repair": "BIOS loader repair", "corrupt": "Unreadable backup",
}


def trusted_helper_available():
    if desktop_runtime.is_flatpak():
        return desktop_runtime.host_helper_available()
    try:
        info = HELPER.lstat()
        wrapper = AUTH_HELPER.lstat()
        return (HELPER.is_file() and not HELPER.is_symlink() and info.st_uid == 0
                and not info.st_mode & 0o022 and AUTH_HELPER.is_file() and not AUTH_HELPER.is_symlink()
                and wrapper.st_uid == 0 and not wrapper.st_mode & 0o022 and Path("/usr/bin/pkexec").is_file())
    except OSError:
        return False


def run_helper(request):
    command = desktop_runtime.host_command(["/usr/bin/pkexec", str(AUTH_HELPER), "--stdin"])
    try:
        result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The administrator action did not finish within 10 minutes, so Uoo stopped waiting. "
                           "Reload to see what actually changed before trying again.") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("pkexec is not installed, so administrator actions are unavailable. Install polkit "
                           "(sudo apt install polkitd pkexec) or use the demo.") from exc
    except OSError as exc:
        raise RuntimeError(f"The administrator helper could not be started: {exc}") from exc
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        if result.returncode == 126:
            raise RuntimeError("Administrator authentication was cancelled or you are not authorized. Nothing was changed.") from exc
        if result.returncode == 127:
            raise RuntimeError("Administrator authentication failed or no polkit agent is running. Start Uoo from a desktop "
                               "session, or install and start a polkit authentication agent, then try again. Nothing was changed.") from exc
        raise RuntimeError(result.stderr.strip() or "The administrator helper returned no valid response. Reload to check the current state.") from exc
    if not isinstance(response, dict) or not response.get("ok") or result.returncode:
        raise RuntimeError(response.get("error", "The action could not be completed.") if isinstance(response, dict) else "Invalid helper response.")
    return response


class BootWindow(Adw.ApplicationWindow):
    """Native window; every real write goes through the installed helper."""

    def __init__(self, application, demo=False, auto_demo=True):
        super().__init__(application=application, title="Uoo Boot Manager", default_width=1120, default_height=740)
        self.set_icon_name(APP_ICON)
        self.set_size_request(900, 640)
        self.explicit_demo = demo
        self.auto_demo = auto_demo
        self.demo = demo
        self.demo_manager = DemoManager()
        self.snapshot = {}
        self.backups = []
        self.custom_draft = []
        self.order = []
        self.repair_plan = None
        self.dual_plan = None
        self.dual_choices = {}
        self.busy = False
        self.closed = False
        self.installed = False if self.demo else trusted_helper_available()
        self.baseline = None
        self.grub_baseline = {}
        self.confirm_handler = None
        self.error_handler = None
        self.last_dialog = None
        self.last_editor = None
        self.fields = {}
        self.buttons = {}
        self.pages = {}
        self._rendering = False
        self.connect("close-request", self._close_requested)
        self._build()
        self.reload()

    def _build(self):
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Uoo Boot Manager", subtitle="Boot configuration and recovery"))
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        self.reload_button = ui.button(None, self.reload, icon="view-refresh-symbolic")
        header.pack_end(self.reload_button)
        self.admin_button = ui.button(None, self.read_as_administrator, icon="changes-prevent-symbolic")
        self.admin_button.set_tooltip_text("Read boot settings with administrator access")
        self.admin_button.update_property([Gtk.AccessibleProperty.LABEL], ["Read boot settings with administrator access"])
        header.pack_start(self.admin_button)
        shell.append(header)
        self.banner = Adw.Banner(title="Reading the boot configuration…", revealed=True)
        self.banner.connect("button-clicked", lambda _banner: self.navigate("diagnostics"))
        shell.append(self.banner)
        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=220,
                          resize_start_child=False, shrink_start_child=False, vexpand=True)
        sidebar_shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                margin_top=18, margin_bottom=14)
        brand = Gtk.Box(spacing=10, margin_start=18, margin_end=12)
        logo = Gtk.Image.new_from_icon_name(APP_ICON)
        logo.set_pixel_size(32)
        logo.set_tooltip_text("Uoo Boot Manager")
        brand.append(logo)
        brand.append(ui.text("Boot manager", "title-4"))
        sidebar_shell.append(brand)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class("navigation-sidebar")
        sidebar_shell.append(self.sidebar)
        self.sidebar.connect("row-selected", self._navigate_selected)
        split.set_start_child(sidebar_shell)
        self.stack = Gtk.Stack(hexpand=True, vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        split.set_end_child(self.stack)
        shell.append(split)
        footer = Gtk.Box(spacing=12, margin_start=18, margin_end=18, margin_top=8, margin_bottom=10)
        self.status = ui.text("Reading configuration…", "dim-label", True)
        self.status.set_hexpand(True)
        footer.append(self.status)
        shell.append(footer)
        self.set_content(shell)
        self._build_boot()
        self._build_firmware()
        self._build_dualboot()
        self._build_custom()
        self._build_advanced()
        self._build_appearance()
        self._build_backups()
        self._build_diagnostics()
        self._build_repair()
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._key_pressed)
        self.add_controller(key)

    def _page(self, key, title, description, icon):
        page = ui.Page(title, description)
        self.pages[key] = page
        self.stack.add_titled(page, key, title)
        item = Gtk.ListBoxRow()
        contents = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)
        contents.append(Gtk.Image.new_from_icon_name(icon))
        contents.append(ui.text(title))
        item.set_child(contents)
        item.page_key = key
        self.sidebar.append(item)
        return page

    def _add_button(self, page, key, title, callback, **options):
        widget = ui.button(title, callback, **options)
        self.buttons[key] = widget
        page.footer.append(widget)
        return widget

    def _build_boot(self):
        page = self._page("boot", "Boot menu", "Choose the default operating system or schedule a one-time GRUB boot.", "system-run-symbolic")
        self.grub_list = ui.listbox()
        self.grub_list.connect("row-selected", lambda *_args: self._controls())
        page.body.append(self.grub_list)
        prefs = ui.group("Default and timing", "−1 waits indefinitely; 0 boots immediately.")
        self.default_mode = ui.combo("Default choice", ("Selected entry", "Remember last boot"))
        prefs.add(self.default_mode)
        timeout_row, self.timeout = ui.spin("Menu timeout (seconds)")
        prefs.add(timeout_row)
        self.menu_style = ui.combo("Menu appearance", STYLE_LABELS)
        prefs.add(self.menu_style)
        self.default_mode.connect("notify::selected", lambda *_args: self._controls())
        self.menu_style.connect("notify::selected", self._style_changed)
        page.body.append(prefs)
        self._add_button(page, "grub_once", "Use selected once", lambda: self.grub_next_boot(False))
        self._add_button(page, "grub_clear", "Clear next boot", lambda: self.grub_next_boot(True))
        self._add_button(page, "boot_apply", "Apply boot settings", self.apply_boot, suggested=True)

    def _build_firmware(self):
        page = self._page("firmware", "Firmware", "Manage UEFI boot order, loader entries, active states, and the next boot.", "computer-symbolic")
        self.firmware_list = ui.listbox()
        self.firmware_list.connect("row-selected", lambda *_args: self._controls())
        page.body.append(self.firmware_list)
        self.firmware_note = ui.text("", "dim-label", True)
        page.body.append(self.firmware_note)
        self._add_button(page, "firmware_up", None, lambda: self.move_firmware(-1), icon="go-up-symbolic")
        self._add_button(page, "firmware_down", None, lambda: self.move_firmware(1), icon="go-down-symbolic")
        self._add_button(page, "firmware_include", "Include / exclude", self.toggle_order_inclusion)
        tools = ui.menu("Entry tools", (("Add EFI loader…", self.create_firmware_entry),
                                        ("Enable / disable selected", self.set_firmware_active),
                                        ("Delete selected entry…", self.delete_firmware_entry)))
        self.buttons["firmware_tools"] = tools
        page.footer.append(tools)
        next_menu = ui.menu("Next boot", (("Use selected once", lambda: self.next_boot(False)),
                                         ("Clear next-boot override", lambda: self.next_boot(True))))
        self.buttons["firmware_next"] = next_menu
        page.footer.append(next_menu)
        self._add_button(page, "order_apply", "Save order", self.apply_order, suggested=True)

    def _build_dualboot(self):
        page = self._page("dualboot", "Dual boot", "Check how Ubuntu and Windows start, then apply the fixes Uoo can make safely. You choose every step; Uoo backs up first and never restarts your computer.", "view-dual-symbolic")
        self.dual_group = ui.group("Check results", "The check reads boot and storage settings with administrator access. Nothing changes until you apply fixes.")
        self.dual_placeholder = ui.row("Check dual boot", "The check reports problems, encrypted disks, and the steps Uoo can apply.", "dialog-information-symbolic")
        self.dual_group.add(self.dual_placeholder)
        self.dual_rows = [self.dual_placeholder]
        self.dual_steps_group = ui.group("Fixes Uoo can apply", "Selected steps run in order after a backup. If one fails, Uoo restores the backup.")
        self.dual_steps_group.set_visible(False)
        self.dual_step_rows = []
        page.body.append(self.dual_group)
        page.body.append(self.dual_steps_group)
        self._add_button(page, "dualboot_check", "Check dual boot", self.inspect_dual_boot)
        self._add_button(page, "dualboot_fix", "Fix dual boot…", self.execute_dual_boot_fix, suggested=True)

    def _build_custom(self):
        page = self._page("custom", "Custom entries", "Stage EFI loader or Linux kernel entries, then apply the draft to rebuild GRUB.", "list-add-symbolic")
        self.custom_list = ui.listbox()
        self.custom_list.connect("row-selected", lambda *_args: self._controls())
        page.body.append(self.custom_list)
        self.custom_note = ui.text("No pending changes.", "dim-label", True)
        page.body.append(self.custom_note)
        self._add_button(page, "custom_add", "Add entry…", lambda: self.edit_custom(True))
        menu = ui.menu("Entry actions", (("Edit selected…", lambda: self.edit_custom(False)),
                                          ("Enable / disable selected", self.toggle_custom),
                                          ("Delete selected", self.delete_custom)))
        self.buttons["custom_actions"] = menu
        page.footer.append(menu)
        self._add_button(page, "custom_up", None, lambda: self.move_custom(-1), icon="go-up-symbolic")
        self._add_button(page, "custom_down", None, lambda: self.move_custom(1), icon="go-down-symbolic")
        self._add_button(page, "custom_apply", "Apply entries", self.apply_custom, suggested=True)

    def _build_advanced(self):
        page = self._page("advanced", "Advanced", "Configure Linux kernel arguments, OS detection, recovery entries, and failed-boot timing.", "preferences-system-symbolic")
        options = ui.group("Linux boot options")
        for key, title in (("GRUB_CMDLINE_LINUX_DEFAULT", "Normal-boot kernel arguments"),
                           ("GRUB_CMDLINE_LINUX", "Arguments for every Linux boot")):
            self.fields[key] = ui.entry(title)
            options.add(self.fields[key])
        failure, self.recordfail_timeout = ui.spin("Timeout after a failed boot (seconds)", 30)
        options.add(failure)
        page.body.append(options)
        detection = ui.group("Generated menu entries")
        self.os_probe = ui.switch("Detect other operating systems", subtitle="Run os-prober on attached disks when GRUB is rebuilt.")
        self.recovery_enabled = ui.switch("Include Linux recovery-mode entries", True)
        detection.add(self.os_probe)
        detection.add(self.recovery_enabled)
        page.body.append(detection)
        self._add_button(page, "advanced_apply", "Apply advanced options", self.apply_advanced, suggested=True)

    def _build_appearance(self):
        page = self._page("appearance", "Appearance", "Set graphics, menu colors, and an optional validated PNG background.", "preferences-desktop-appearance-symbolic")
        graphics = ui.group("Graphics and colors", "Unsupported graphics modes may fall back to text. Colors use foreground/background names, such as white/blue.")
        for key, title in (("GRUB_GFXMODE", "Graphics resolution (auto or WIDTHxHEIGHT)"),
                           ("GRUB_COLOR_NORMAL", "Normal text / background colors"),
                           ("GRUB_COLOR_HIGHLIGHT", "Selected text / background colors")):
            self.fields[key] = ui.entry(title)
            graphics.add(self.fields[key])
        self.payload = ui.combo("Linux graphics handoff", ("keep", "text"))
        self.appearance_style = ui.combo("Menu appearance", STYLE_LABELS)
        self.appearance_style.connect("notify::selected", self._style_changed)
        graphics.add(self.payload)
        graphics.add(self.appearance_style)
        page.body.append(graphics)
        background = ui.group("Background image", "PNG files up to 2 MiB and 4096 × 4096 pixels are supported.")
        self.background_row = ui.row("No managed background PNG", icon="image-x-generic-symbolic")
        choose = ui.button("Choose PNG…", self.choose_background)
        clear = ui.button("Clear", self.clear_background)
        choose.set_valign(Gtk.Align.CENTER)
        clear.set_valign(Gtk.Align.CENTER)
        self.background_row.add_suffix(choose)
        self.background_row.add_suffix(clear)
        self.buttons["background_choose"] = choose
        self.buttons["background_clear"] = clear
        background.add(self.background_row)
        page.body.append(background)
        self._add_button(page, "appearance_apply", "Apply appearance", self.apply_appearance, suggested=True)

    def _build_backups(self):
        page = self._page("backups", "Backups", "Back up saved settings, inspect previous changes, or export and import portable GRUB configurations.", "document-revert-symbolic")
        self.backup_list = ui.listbox()
        self.backup_list.connect("row-selected", lambda *_args: self._controls())
        page.body.append(self.backup_list)
        self.backup_note = ui.text("A backup is created before every boot configuration change.", "dim-label", True)
        page.body.append(self.backup_note)
        self._add_button(page, "backup_create", "New backup…", self.create_manual_backup)
        menu = ui.menu("Backup actions", (("Inspect selected…", self.show_backup_details),
                                           ("Export selected…", self.export_selected_backup),
                                           ("Delete selected…", self.delete_selected_backup)))
        self.buttons["backup_actions"] = menu
        page.footer.append(menu)
        self._add_button(page, "backup_import", "Import…", self.import_configuration)
        self._add_button(page, "backup_restore", "Restore…", self.restore, suggested=True)
        self._add_button(page, "backup_refresh", None, self.load_backups, icon="view-refresh-symbolic")

    def _build_diagnostics(self):
        page = self._page("diagnostics", "Diagnostics", "Inspect system boot mode, Secure Boot, required tools, filesystems, and configuration health.", "dialog-information-symbolic")
        self.diagnostic_summary = ui.group("System")
        self.diagnostic_checks = ui.group("Checks")
        self.diagnostic_devices = ui.group("Filesystems")
        page.body.append(self.diagnostic_summary)
        page.body.append(self.diagnostic_checks)
        page.body.append(self.diagnostic_devices)
        self._add_button(page, "diagnostics_export", "Export report…", self.export_diagnostics)
        self._add_button(page, "grub_refresh", "Rescan and rebuild GRUB…", self.refresh_grub)

    def _build_repair(self):
        page = self._page("repair", "Repair", "Inspect and review a supported repair for this running Ubuntu installation. Uoo verifies the target again before reinstalling its bootloader.", "applications-utilities-symbolic")
        self.repair_group = ui.group("Repair plan", "Repairs require administrator authentication, create a backup, and do not restart your computer.")
        self.repair_placeholder = ui.row("Inspect a fresh plan", "The plan identifies the boot mode, target, prerequisites, and steps.", "dialog-information-symbolic")
        self.repair_group.add(self.repair_placeholder)
        self.repair_rows = [self.repair_placeholder]
        page.body.append(self.repair_group)
        self._add_button(page, "repair_plan", "Inspect repair plan", self.inspect_repair)
        self._add_button(page, "repair_execute", "Execute reviewed repair…", self.execute_repair, destructive=True)

    def _navigate_selected(self, _sidebar, selected):
        if selected:
            self.stack.set_visible_child_name(selected.page_key)

    def navigate(self, key):
        for index in range(len(self.pages)):
            item = self.sidebar.get_row_at_index(index)
            if item and item.page_key == key:
                self.sidebar.select_row(item)
                break

    def _key_pressed(self, _controller, keyval, _keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (ord("r"), ord("R")):
            self.reload()
            return True
        return False

    def _style_changed(self, changed, _parameter):
        if self._rendering or not hasattr(self, "appearance_style"):
            return
        other = self.menu_style if changed is self.appearance_style else self.appearance_style
        if other.get_selected() != changed.get_selected():
            other.set_selected(changed.get_selected())

    def _status(self, message):
        self.status.set_text(message)

    def show_error(self, heading, message, parent=None):
        self._status("The action could not be completed.")
        if self.error_handler:
            self.error_handler(heading, str(message))
            return
        dialog = Adw.AlertDialog.new(heading, str(message))
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        self.last_dialog = dialog
        dialog.present(parent or self)

    def confirm(self, heading, message, callback, label="Apply", destructive=False, typed=None, acknowledge=None):
        if self.confirm_handler:
            if self.confirm_handler(heading, message, typed):
                callback()
            return
        dialog = Adw.AlertDialog.new(heading, message)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", label)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
        typed_entry = Gtk.Entry(placeholder_text=f"Type {typed} to confirm") if typed else None
        acknowledged = Gtk.CheckButton(label=acknowledge) if acknowledge else None

        def ready():
            return ((not typed or typed_entry.get_text() == typed)
                    and (not acknowledge or acknowledged.get_active()))

        controls = [widget for widget in (acknowledged, typed_entry) if widget]
        if controls:
            for widget in controls:
                widget.connect("changed" if widget is typed_entry else "toggled",
                               lambda *_args: dialog.set_response_enabled("apply", ready()))
            if len(controls) == 1:
                extras = controls[0]
            else:
                extras = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                for widget in controls:
                    extras.append(widget)
            dialog.set_extra_child(extras)
            dialog.set_response_enabled("apply", False)

        def response(_dialog, response_id):
            if response_id == "apply" and ready():
                callback()

        dialog.connect("response", response)
        self.last_dialog = dialog
        dialog.present(self)

    def _job(self, label, work, callback):
        if self.busy or self.closed:
            return
        self.busy = True
        self._status(label)
        self.spinner.start()
        self._controls()

        def deliver(result, error):
            if self.closed:
                return GLib.SOURCE_REMOVE
            self.busy = False
            self.spinner.stop()
            try:
                if error:
                    if not self.snapshot:
                        # The first read failed; replace the initial "Reading…" banner.
                        self.banner.set_title("The boot configuration could not be read")
                        self.banner.set_button_label("Diagnostics")
                    self.show_error("Uoo Boot Manager", error)
                else:
                    callback(result)
            except Exception as exc:
                self.show_error("Cannot display the result", exc)
            self._controls()
            return GLib.SOURCE_REMOVE

        def worker():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            GLib.idle_add(deliver, result, error)

        threading.Thread(target=worker, daemon=True).start()

    def dispatch(self, request, callback=None):
        if self.busy or self.closed:
            return
        callback = callback or (lambda response: self._committed(request, response))
        if self.demo:
            try:
                callback(self.demo_manager.dispatch(request))
                self._controls()
            except Exception as exc:
                self.show_error("Uoo Boot Manager", exc)
            return
        if not self.installed:
            self.show_error("Read-only session", "Install the native Uoo package to enable administrator actions.")
            return
        self._job("Waiting for administrator authentication…", lambda: run_helper(request), callback)

    def _committed(self, request, response):
        if "backups" in response:
            self.backups = response["backups"]
        if request["action"] in ("create_backup", "delete_backup", "list_backups"):
            self._render_backups()
        elif "snapshot" in response:
            self._loaded(response["snapshot"])
        self._status(response.get("message", "Action completed."))

    def reload(self):
        if self.busy:
            return
        if self.has_unsaved_changes():
            self.confirm("Unapplied changes", "Discard your unapplied form, entry, or boot-order changes and reload?", self._reload, "Discard and reload")
        else:
            self._reload()

    def _reload(self):
        def read():
            if self.demo or self.explicit_demo:
                return self.demo_manager.dispatch({"action": "snapshot"})["snapshot"]
            snapshot = desktop_runtime.host_snapshot() if desktop_runtime.is_flatpak() else system.snapshot()
            if (self.auto_demo and not snapshot.get("entries") and not snapshot.get("firmware", {}).get("entries")
                    and not snapshot.get("settings") and not Path("/boot/grub").exists()
                    and not Path("/sys/firmware/efi").exists()):
                preview = self.demo_manager.dispatch({"action": "snapshot"})["snapshot"]
                preview["preview_reason"] = "No boot entries were found on this computer."
                return preview
            return snapshot

        self._job("Reading the boot configuration…", read, self._loaded)

    def read_as_administrator(self):
        if self.busy or self.demo or not self.installed:
            return
        read = lambda: self.dispatch({"action": "snapshot"}, lambda response: self._loaded(response["snapshot"]))
        if self.has_unsaved_changes():
            self.confirm("Unapplied changes", "Discard your unapplied changes before reading saved configuration?", read, "Discard and read")
        else:
            read()

    @staticmethod
    def _matches(entry, default):
        path = (entry.get("group", "").replace(" / ", ">") + ">" if entry.get("group") else "") + entry["title"]
        return default in (entry["selector"], entry["position"], path)

    @staticmethod
    def _bool(settings, key, default=False):
        value = settings.get(key, default)
        return value is True or value == "true"

    def _loaded(self, snapshot):
        self.snapshot = snapshot
        self.demo = bool(snapshot.get("demo"))
        self._render()
        self._status("Preview ready. System boot settings are untouched." if self.demo else "Configuration loaded.")

    def _render(self):
        self._rendering = True
        self.installed = False if self.demo else trusted_helper_available()
        settings = self.snapshot.get("settings", {})
        default = str(settings.get("GRUB_DEFAULT", "0"))
        resolved = (self.snapshot.get("grub_saved") or settings.get("UOO_SAVED_ENTRY", default)) if default == "saved" else default
        ui.clear_list(self.grub_list)
        for entry in self.snapshot.get("entries", []):
            current = self._matches(entry, resolved)
            labels = [entry.get("group") or "Main menu"]
            if current:
                labels.append("Default")
            if entry["selector"] == self.snapshot.get("grub_next"):
                labels.append("Next boot")
            item = ui.row(entry["title"], " · ".join(labels), "system-run-symbolic")
            item.data = entry
            self.grub_list.append(item)
            if current:
                self.grub_list.select_row(item)
        self.default_mode.set_selected(1 if default == "saved" else 0)
        try:
            self.timeout.set_value(int(settings.get("GRUB_TIMEOUT", 10)))
            self.recordfail_timeout.set_value(int(settings.get("GRUB_RECORDFAIL_TIMEOUT", 30)))
        except (TypeError, ValueError):
            self.timeout.set_value(10)
            self.recordfail_timeout.set_value(30)
        style = settings.get("GRUB_TIMEOUT_STYLE", "menu")
        style_index = STYLE_VALUES.index(style) if style in STYLE_VALUES else 0
        self.menu_style.set_selected(style_index)
        self.appearance_style.set_selected(style_index)
        defaults = {"GRUB_GFXMODE": "auto", "GRUB_COLOR_NORMAL": "light-gray/black", "GRUB_COLOR_HIGHLIGHT": "white/blue"}
        for key, widget in self.fields.items():
            widget.set_text(str(settings.get(key, defaults.get(key, ""))))
        # An unlisted value such as 1024x768 shows as "keep" and is only replaced if the user changes it.
        payload = settings.get("GRUB_GFXPAYLOAD_LINUX", "keep")
        ui.set_combo(self.payload, payload if payload in self.payload._choices else "keep")
        self.os_probe.set_active(not self._bool(settings, "GRUB_DISABLE_OS_PROBER", True))
        self.recovery_enabled.set_active(not self._bool(settings, "GRUB_DISABLE_RECOVERY"))
        self.background_row.set_title("A managed background PNG is installed" if self.snapshot.get("background") else "No managed background PNG")
        self.order = list(self.snapshot.get("firmware", {}).get("order", []))
        self.custom_draft = copy.deepcopy(self.snapshot.get("custom_entries", []))
        self._render_firmware()
        self._render_custom()
        self._render_backups()
        self._render_diagnostics()
        self.repair_plan = None
        self._render_repair()
        self.dual_plan = None
        self._render_dualboot()
        if self.demo:
            self.banner.set_title("Preview mode — no system files or firmware are modified")
            self.banner.set_button_label("")
        elif not self.installed:
            self.banner.set_title("Read-only session — install the native Uoo package to enable changes")
            self.banner.set_button_label("Diagnostics")
        elif self.snapshot.get("errors"):
            self.banner.set_title("Some boot settings could not be read")
            self.banner.set_button_label("Diagnostics")
        else:
            self.banner.set_title("Changes require administrator authentication and are backed up first")
            self.banner.set_button_label("")
        self._rendering = False
        self.baseline = self._draft()
        self.grub_baseline = {**self._advanced_changes(), **self._appearance_changes()}
        self._controls()

    def _selected(self, widget):
        selected = widget.get_selected_row()
        return selected.data if selected and hasattr(selected, "data") else None

    def select_grub(self, index):
        self.grub_list.select_row(self.grub_list.get_row_at_index(index))

    def select_firmware(self, identifier):
        child = self.firmware_list.get_first_child()
        while child:
            if child.data["id"] == identifier:
                self.firmware_list.select_row(child)
                return
            child = child.get_next_sibling()

    def _draft(self):
        selected = self._selected(self.grub_list)
        return {"default": self.default_mode.get_selected(), "selected": selected["selector"] if selected else "",
                "timeout": self.timeout.get_value_as_int(), "style": self.menu_style.get_selected(),
                "fields": {key: widget.get_text() for key, widget in self.fields.items()},
                "recordfail": self.recordfail_timeout.get_value_as_int(), "probe": self.os_probe.get_active(),
                "recovery": self.recovery_enabled.get_active(), "payload": self.payload.get_selected(),
                "order": list(self.order), "custom": copy.deepcopy(self.custom_draft)}

    def has_unsaved_changes(self):
        return self.baseline is not None and self._draft() != self.baseline

    def _mutate(self, request, title, summary, label="Apply", destructive=False, typed=None, acknowledge=None):
        if not self._can_change():
            return
        suffix = "This is a preview. Your computer will not be modified." if self.demo else "Uoo creates a backup and requests administrator authentication."
        if self.has_unsaved_changes() and request["action"] not in ("create_backup", "delete_backup"):
            suffix += " Saved settings will be reloaded; other unapplied drafts will be discarded."
        self.confirm(title, summary + "\n\n" + suffix, lambda: self.dispatch(request), label, destructive, typed, acknowledge)

    def _can_change(self):
        return not self.busy and (self.demo or self.installed)

    def _controls(self):
        if not self.buttons or "repair_execute" not in self.buttons:
            return
        enabled = self._can_change()
        caps = self.snapshot.get("capabilities", {})
        grub = enabled and caps.get("grub", bool(self.snapshot.get("entries")))
        custom_access = grub and caps.get("custom_entries", False)
        grub_next_access = grub and caps.get("grub_next", False)
        firmware = enabled and self.snapshot.get("firmware", {}).get("available", False)
        selected = self._selected(self.grub_list)
        for name, widget in self.buttons.items():
            widget.set_sensitive(enabled)
        self.buttons["diagnostics_export"].set_sensitive(not self.busy)
        for name in ("boot_apply", "advanced_apply", "appearance_apply", "grub_refresh", "background_choose"):
            self.buttons[name].set_sensitive(grub)
        remember = self.default_mode.get_selected() == 1
        self.default_mode.set_subtitle("" if caps.get("grub_next") else "Read as administrator or check Diagnostics for support for remembering the last boot.")
        self.buttons["boot_apply"].set_sensitive(grub and (selected is not None or remember) and (not remember or grub_next_access))
        self.buttons["grub_once"].set_sensitive(grub_next_access and selected is not None)
        self.buttons["grub_clear"].set_sensitive(grub_next_access and bool(self.snapshot.get("grub_next")))
        self.buttons["background_clear"].set_sensitive(grub and bool(self.snapshot.get("background")))
        entry = self._selected(self.firmware_list)
        index = self.order.index(entry["id"]) if entry and entry["id"] in self.order else -1
        known = {item["id"] for item in self.snapshot.get("firmware", {}).get("entries", [])}
        for name in ("firmware_up", "firmware_down", "firmware_include", "firmware_tools", "firmware_next", "order_apply"):
            self.buttons[name].set_sensitive(firmware)
        self.buttons["firmware_up"].set_sensitive(firmware and index > 0)
        self.buttons["firmware_down"].set_sensitive(firmware and 0 <= index < len(self.order) - 1)
        self.buttons["firmware_include"].set_sensitive(firmware and entry is not None and (entry["id"] in known or entry["id"] in self.order))
        self.buttons["firmware_next"].set_sensitive(firmware and bool(self.snapshot.get("firmware", {}).get("next") or entry and entry.get("active", True)))
        self.buttons["order_apply"].set_sensitive(firmware and bool(self.order) and set(self.order) <= known and self.order != self.snapshot.get("firmware", {}).get("order", []))
        custom = self._selected(self.custom_list)
        for name in ("custom_actions", "custom_up", "custom_down"):
            self.buttons[name].set_sensitive(custom_access and custom is not None)
        self.buttons["custom_add"].set_sensitive(custom_access)
        self.buttons["custom_apply"].set_sensitive(custom_access and self.custom_draft != self.snapshot.get("custom_entries", []))
        backup = self._selected(self.backup_list)
        self.buttons["backup_actions"].set_sensitive(enabled and backup is not None)
        self.buttons["backup_restore"].set_sensitive(enabled and backup is not None and backup.get("kind") != "corrupt")
        self.buttons["repair_execute"].set_sensitive(enabled and bool(self.repair_plan and self.repair_plan.get("supported")))
        self.buttons["dualboot_fix"].set_sensitive(enabled and bool(self.dual_plan and self._dual_selected()))
        self.reload_button.set_sensitive(not self.busy)
        self.admin_button.set_sensitive(not self.busy and self.installed and not self.demo)
        self.stack.set_sensitive(not self.busy)

    def apply_boot(self):
        entry = self._selected(self.grub_list)
        remember = self.default_mode.get_selected() == 1
        if remember and not self.snapshot.get("capabilities", {}).get("grub_next"):
            self.show_error("Remembered default unavailable", "Read as administrator or check Diagnostics for a supported writable GRUB environment. You can choose Selected entry to set a fixed default.")
            return
        if not remember and not entry:
            return
        title = "Remember the last booted entry" if remember else entry["title"]
        changes = {"GRUB_DEFAULT": "saved" if remember else entry["selector"], "GRUB_SAVEDEFAULT": remember,
                   "GRUB_TIMEOUT": self.timeout.get_value_as_int(), "GRUB_TIMEOUT_STYLE": STYLE_VALUES[self.menu_style.get_selected()]}
        self._mutate({"action": "apply_grub", "changes": changes}, "Apply boot settings",
                     f"Default: {title}\nTimeout: {changes['GRUB_TIMEOUT']} seconds\nAppearance: {ui.combo_value(self.menu_style)}")

    def _advanced_changes(self):
        changes = {key: self.fields[key].get_text() for key in ("GRUB_CMDLINE_LINUX_DEFAULT", "GRUB_CMDLINE_LINUX")}
        changes.update(GRUB_RECORDFAIL_TIMEOUT=self.recordfail_timeout.get_value_as_int(),
                       GRUB_DISABLE_OS_PROBER=not self.os_probe.get_active(), GRUB_DISABLE_RECOVERY=not self.recovery_enabled.get_active())
        return changes

    def _appearance_changes(self):
        changes = {key: self.fields[key].get_text() for key in ("GRUB_GFXMODE", "GRUB_COLOR_NORMAL", "GRUB_COLOR_HIGHLIGHT")}
        changes.update(GRUB_GFXPAYLOAD_LINUX=ui.combo_value(self.payload), GRUB_TIMEOUT_STYLE=STYLE_VALUES[self.appearance_style.get_selected()])
        return changes

    def apply_advanced(self):
        self._apply_changes(self._advanced_changes(), "Apply advanced options")

    def apply_appearance(self):
        self._apply_changes(self._appearance_changes(), "Apply appearance")

    def _apply_changes(self, changes, title):
        # Send only edited keys, so a value the form could not show exactly (for example a
        # multi-line kernel command line) is never overwritten by its displayed approximation.
        changes = {key: value for key, value in changes.items() if self.grub_baseline.get(key) != value}
        if not changes:
            self._status("There are no changes to apply.")
            return
        try:
            validate_grub_changes(changes)
        except ValueError as exc:
            self.show_error("Check the settings", exc)
            return
        summary = "\n".join(f"{key.removeprefix('GRUB_').replace('_', ' ').title()}: {value or '(none)'}" for key, value in changes.items())
        self._mutate({"action": "apply_grub", "changes": changes}, title, summary)

    def grub_next_boot(self, clear):
        if not self.snapshot.get("capabilities", {}).get("grub_next"):
            self.show_error("Read as administrator first", "Administrator access is needed to inspect and manage one-time GRUB boots.")
            return
        entry = self._selected(self.grub_list)
        if not clear and not entry:
            return
        self._mutate({"action": "grub_next_boot", "entry": "" if clear else entry["selector"]},
                     "GRUB next boot", "Clear the one-time GRUB override." if clear else f"Use {entry['title']} on the next GRUB boot only.")

    def refresh_grub(self):
        self._mutate({"action": "refresh_grub"}, "Rescan and rebuild GRUB", "Regenerate GRUB's menu from installed kernels and current settings. OS detection follows your advanced configuration.", "Rebuild")

    def _render_firmware(self, selection=None):
        ui.clear_list(self.firmware_list)
        firmware = self.snapshot.get("firmware", {})
        entries = {entry["id"]: entry for entry in firmware.get("entries", [])}
        identifiers = self.order + [identifier for identifier in entries if identifier not in self.order]
        for identifier in identifiers:
            entry = entries.get(identifier, {"id": identifier, "label": "Unavailable boot entry", "active": False})
            labels = [f"ID {identifier}"]
            labels.append(f"Order {self.order.index(identifier) + 1}" if identifier in self.order else "Outside boot order")
            if identifier == firmware.get("current"):
                labels.append("Current boot")
            if identifier == firmware.get("next"):
                labels.append("Next boot")
            if not entry.get("active", True):
                labels.append("Inactive")
            item = ui.row(entry["label"], " · ".join(labels), "drive-harddisk-symbolic")
            item.data = entry
            self.firmware_list.append(item)
            if identifier == selection:
                self.firmware_list.select_row(item)
        if not self.firmware_list.get_selected_row() and identifiers:
            self.firmware_list.select_row(self.firmware_list.get_row_at_index(0))
        self.firmware_note.set_text(firmware.get("error", "") if not firmware.get("available") else "Changes are staged until you save the order. Current boot entries are protected.")
        self._controls()

    def move_firmware(self, direction):
        entry = self._selected(self.firmware_list)
        if not self._can_change() or not entry or entry["id"] not in self.order:
            return
        index = self.order.index(entry["id"])
        target = index + direction
        if 0 <= target < len(self.order):
            self.order[index], self.order[target] = self.order[target], self.order[index]
            self._render_firmware(entry["id"])
            self._status("Boot order edited. Save order to apply it.")

    def toggle_order_inclusion(self):
        entry = self._selected(self.firmware_list)
        if self._can_change() and entry:
            self.order.remove(entry["id"]) if entry["id"] in self.order else self.order.append(entry["id"])
            self._render_firmware(entry["id"])

    def apply_order(self):
        entries = {entry["id"]: entry for entry in self.snapshot.get("firmware", {}).get("entries", [])}
        if not self.order or not set(self.order) <= set(entries):
            return
        summary = "New boot order:\n" + "\n".join(f"{index + 1}. {entries[identifier]['label']} ({identifier})" for index, identifier in enumerate(self.order))
        self._mutate({"action": "apply_order", "order": list(self.order)}, "Save firmware order", summary, "Save")

    def next_boot(self, clear):
        entry = self._selected(self.firmware_list)
        if not clear and not entry:
            return
        if not clear and not entry.get("active", True):
            self.show_error("Inactive firmware entry", "Enable this firmware entry before scheduling it for the next boot.")
            return
        if clear and not self.snapshot.get("firmware", {}).get("next"):
            return
        self._mutate({"action": "next_boot", "entry": "" if clear else entry["id"]}, "Firmware next boot",
                     "Clear the next-boot override." if clear else f"Use {entry['label']} ({entry['id']}) for the next boot only.")

    def set_firmware_active(self):
        entry = self._selected(self.firmware_list)
        if entry:
            active = not entry.get("active", True)
            if not active and entry["id"] == self.snapshot["firmware"].get("next"):
                self.show_error("Clear next boot first", "This entry is scheduled for the next boot. Clear the next-boot override before disabling it.")
                return
            self._mutate({"action": "set_active", "entry": entry["id"], "active": active}, "Change firmware entry",
                         f"{'Enable' if active else 'Disable'} {entry['label']} ({entry['id']})?")

    def delete_firmware_entry(self):
        entry = self._selected(self.firmware_list)
        if not entry:
            return
        if entry["id"] == self.snapshot["firmware"].get("current"):
            self.show_error("Current boot entry", "The firmware entry used for the current boot cannot be deleted.")
            return
        if entry["id"] == self.snapshot["firmware"].get("next"):
            self.show_error("Clear next boot first", "This entry is scheduled for the next boot. Clear the next-boot override before deleting it.")
            return
        self._mutate({"action": "delete_uefi", "entry": entry["id"], "confirm": entry["id"]}, "Delete firmware entry",
                     f"Remove {entry['label']} ({entry['id']})? Its loader file remains on disk.\nType {entry['id']} to confirm.", "Delete", True, entry["id"])

    def _editor(self, title, description):
        window, shell = ui.editor_window(self, title)
        page = ui.Page(title, description)
        shell.append(page)
        page.set_vexpand(True)
        self.last_editor = {"window": window, "page": page}
        return window, page

    def edit_custom(self, new=False):
        if not self._can_change() or not self.snapshot.get("capabilities", {}).get("custom_entries"):
            return
        selected = None if new else self._selected(self.custom_list)
        if not new and not selected:
            return
        original = selected or {"id": "uoo-" + uuid.uuid4().hex, "title": "", "type": "efi", "enabled": True, "uuid": ""}
        window, page = self._editor("Add managed entry" if new else "Edit managed entry", "Paths are relative to the selected filesystem's root. No changes are saved until you apply the main draft.")
        common = ui.group("Entry")
        fields = {key: ui.entry(title, original.get(key, "")) for key, title in (("title", "Display name"), ("uuid", "Filesystem UUID"))}
        kind = ui.combo("Entry type", ("EFI loader", "Linux kernel"), 0 if original["type"] == "efi" else 1)
        enabled = ui.switch("Enable this entry", original.get("enabled", True))
        common.add(fields["title"])
        common.add(kind)
        common.add(fields["uuid"])
        common.add(enabled)
        page.body.append(common)
        device_choices = [device for device in self.snapshot.get("devices", []) if device.get("uuid")]
        device_picker = None
        if device_choices:
            choices = ("Choose a filesystem…",) + tuple(f"{device.get('path', device.get('name'))} · {device['uuid']}" for device in device_choices)
            selected_device = next((index + 1 for index, device in enumerate(device_choices) if device["uuid"] == original.get("uuid")), 0)
            device_picker = ui.combo("Use detected filesystem", choices, selected_device)
            common.add(device_picker)
            device_picker.connect("notify::selected", lambda choice, _prop: fields["uuid"].set_text(
                device_choices[choice.get_selected() - 1]["uuid"] if choice.get_selected() else ""))
        efi = ui.group("EFI loader")
        linux = ui.group("Linux boot files")
        for key, title, parent in (("loader", "EFI loader (/EFI/…efi)", efi), ("kernel", "Kernel (/boot/vmlinuz…)", linux),
                                    ("initrd", "Initrd (optional)", linux), ("options", "Kernel arguments", linux)):
            fields[key] = ui.entry(title, original.get(key, ""))
            parent.add(fields[key])
            if key != "options":
                browse = ui.button(None, lambda target=key: self._browse_partition(fields["uuid"].get_text(), fields[target], window, "*.efi" if target == "loader" else None), icon="document-open-symbolic")
                browse.set_tooltip_text("Browse on the selected filesystem")
                if desktop_runtime.is_flatpak():
                    browse.set_sensitive(False)
                    browse.set_tooltip_text("Enter the boot file's filesystem-relative path manually in Flatpak")
                fields[key].add_suffix(browse)
        page.body.append(efi)
        page.body.append(linux)

        def change_type(*_args):
            efi.set_visible(kind.get_selected() == 0)
            linux.set_visible(kind.get_selected() == 1)

        kind.connect("notify::selected", change_type)
        change_type()

        def stage():
            entry_type = "efi" if kind.get_selected() == 0 else "linux"
            result = {"id": original["id"], "title": fields["title"].get_text().strip(), "type": entry_type,
                      "uuid": fields["uuid"].get_text().strip(), "enabled": enabled.get_active()}
            result.update({key: fields[key].get_text().strip() for key in (("loader",) if entry_type == "efi" else ("kernel", "initrd", "options"))})
            try:
                result = validate_custom_entries([result])[0]
            except ValueError as exc:
                self.show_error("Check the entry", exc, parent=window)
                return
            if new:
                self.custom_draft.append(result)
            else:
                self.custom_draft[self.custom_draft.index(selected)] = result
            window.destroy()
            self._render_custom(result["id"])
            self._status("Entry staged. Apply entries to save it.")

        page.footer.append(ui.button("Cancel", window.destroy))
        save = ui.button("Stage entry", stage, suggested=True)
        page.footer.append(save)
        self.last_editor.update(fields=fields, kind=kind, enabled=enabled, device_picker=device_picker, save=save)
        window.present()

    def _browse_partition(self, filesystem_uuid, field, parent, pattern):
        device = next((device for device in self.snapshot.get("devices", []) if device.get("uuid") == filesystem_uuid), None)
        mount = device.get("mountpoint") if device else None
        if not mount:
            self.show_error("Filesystem is not mounted", "Select a mounted filesystem UUID to browse, or enter a path relative to its root.", parent=parent)
            return

        def chosen(name):
            try:
                # Keep a chosen symlink such as /boot/vmlinuz so the entry follows kernel updates.
                relative = (Path(name).parent.resolve() / Path(name).name).relative_to(Path(mount).resolve())
            except ValueError:
                self.show_error("Different filesystem", "Choose a file beneath the selected filesystem's mount point.", parent=parent)
                return
            field.set_text("/" + str(relative))

        ui.file_dialog(parent, "Choose a boot file", False, chosen, pattern, folder=mount)

    def _render_custom(self, selection=None):
        ui.clear_list(self.custom_list)
        for entry in self.custom_draft:
            target = entry.get("loader") if entry["type"] == "efi" else entry.get("kernel")
            item = ui.row(entry["title"], f"{entry['type'].upper()} · {'Enabled' if entry['enabled'] else 'Disabled'} · {target}", "system-run-symbolic")
            item.data = entry
            self.custom_list.append(item)
            if entry["id"] == selection:
                self.custom_list.select_row(item)
        note = "Draft changed. Apply entries to rebuild GRUB." if self.custom_draft != self.snapshot.get("custom_entries", []) else "No pending changes."
        if not self.snapshot.get("capabilities", {}).get("custom_entries"):
            note = "Read as administrator to inspect and edit managed entries."
        self.custom_note.set_text(note)
        self._controls()

    def toggle_custom(self):
        entry = self._selected(self.custom_list)
        if self._can_change() and self.snapshot.get("capabilities", {}).get("custom_entries") and entry:
            entry["enabled"] = not entry["enabled"]
            self._render_custom(entry["id"])

    def move_custom(self, direction):
        entry = self._selected(self.custom_list)
        if not self._can_change() or not self.snapshot.get("capabilities", {}).get("custom_entries") or not entry:
            return
        index = self.custom_draft.index(entry)
        target = index + direction
        if 0 <= target < len(self.custom_draft):
            self.custom_draft[index], self.custom_draft[target] = self.custom_draft[target], self.custom_draft[index]
            self._render_custom(entry["id"])

    def delete_custom(self):
        entry = self._selected(self.custom_list)
        if not self._can_change() or not self.snapshot.get("capabilities", {}).get("custom_entries") or not entry:
            return

        def remove():
            self.custom_draft.remove(entry)
            self._render_custom()

        self.confirm("Remove managed entry", f"Remove {entry['title']} from the draft? Apply entries to save this removal.", remove, "Remove", True)

    def apply_custom(self):
        if not self.snapshot.get("capabilities", {}).get("custom_entries"):
            self.show_error("Read as administrator first", "Administrator access is needed to inspect existing managed entries before replacing them.")
            return
        try:
            entries = validate_custom_entries(self.custom_draft)
        except ValueError as exc:
            self.show_error("Check managed entries", exc)
            return
        summary = "Managed entries after applying:\n" + ("\n".join(f"{index + 1}. {entry['title']} ({entry['type']}, {'enabled' if entry['enabled'] else 'disabled'})" for index, entry in enumerate(entries)) or "No managed entries.")
        request = {"action": "custom_entries", "entries": entries}
        if self.snapshot.get("revision"):
            request["revision"] = self.snapshot["revision"]
        self._mutate(request, "Apply managed entries", summary)

    def create_firmware_entry(self):
        if not self._can_change():
            return
        devices = [device for device in self.snapshot.get("devices", []) if device.get("partn") and device.get("esp")]
        if not devices:
            self.show_error("EFI System Partition", "No suitable EFI System Partition was detected. Read as administrator and inspect Diagnostics.")
            return
        window, page = self._editor("Add EFI loader", "Create a firmware entry pointing to an existing .efi file on a detected EFI System Partition.")
        options = ui.group("Loader")
        label = ui.entry("Firmware label")
        device_choice = ui.combo("EFI System Partition", tuple(device["path"] for device in devices))
        loader = ui.entry("Loader path (/EFI/…efi)")
        options.add(label)
        options.add(device_choice)
        options.add(loader)
        browse = ui.button(None, lambda: self._browse_partition(devices[device_choice.get_selected()].get("uuid", ""), loader, window, "*.efi"), icon="document-open-symbolic")
        browse.set_tooltip_text("Browse for an EFI loader")
        loader.add_suffix(browse)
        page.body.append(options)

        def save():
            device = devices[device_choice.get_selected()]
            disk = device.get("pkname", "")
            if disk and not disk.startswith("/dev/"):
                disk = "/dev/" + disk
            if not label.get_text().strip() or not loader.get_text().strip() or not disk:
                self.show_error("Complete the loader", "Label, EFI loader path, and detected parent disk are required.", parent=window)
                return
            request = {"action": "create_uefi", "label": label.get_text().strip(), "disk": disk,
                       "partition": int(device["partn"]), "loader": loader.get_text().strip()}
            window.destroy()
            self._mutate(request, "Add firmware loader", f"Label: {request['label']}\nDisk: {disk}, partition {request['partition']}\nLoader: {request['loader']}", "Add")

        page.footer.append(ui.button("Cancel", window.destroy))
        save_button = ui.button("Review and add", save, suggested=True)
        page.footer.append(save_button)
        self.last_editor.update(label=label, device=device_choice, loader=loader, save=save_button)
        window.present()

    def _render_backups(self):
        previous = self._selected(self.backup_list)
        selected_id = previous["id"] if previous else None
        ui.clear_list(self.backup_list)
        for backup in self.backups:
            label = BACKUP_TYPES.get(backup.get("kind"), "Boot configuration")
            item = ui.row(backup.get("summary", backup["id"]), f"{backup.get('created', '')} · {label}", "document-revert-symbolic")
            item.data = backup
            self.backup_list.append(item)
            if backup["id"] == selected_id:
                self.backup_list.select_row(item)
        if self.backups and not self.backup_list.get_selected_row():
            self.backup_list.select_row(self.backup_list.get_row_at_index(0))
        self.backup_note.set_text("Preview backups exist only in this session." if self.demo else "A backup is created before every boot configuration change. Load backups to inspect saved history.")
        self._controls()

    def load_backups(self):
        self.dispatch({"action": "list_backups"})

    def create_manual_backup(self):
        if not self._can_change():
            return
        window, page = self._editor("Create a backup", "Back up the current saved configuration. Unapplied drafts stay in the app.")
        prefs = ui.group("Backup scope")
        scope = ui.combo("Include", ("All supported settings", "GRUB configuration", "UEFI firmware"))
        prefs.add(scope)
        page.body.append(prefs)

        def save():
            kind = ("all", "grub", "uefi")[scope.get_selected()]
            window.destroy()
            self.dispatch({"action": "create_backup", "kind": kind})

        page.footer.append(ui.button("Cancel", window.destroy))
        create = ui.button("Create backup", save, suggested=True)
        page.footer.append(create)
        self.last_editor.update(scope=scope, save=create)
        window.present()

    def restore(self):
        backup = self._selected(self.backup_list)
        if backup and backup.get("kind") == "corrupt":
            self.show_error("Unreadable backup", "This backup cannot be restored. You can delete it from Backup actions.")
            return
        if backup:
            self._mutate({"action": "restore", "backup": backup["id"]}, "Restore boot configuration",
                         f"Restore the saved configuration from {backup.get('created', '')}?\n\n{backup.get('summary', '')}\n\nThe current saved configuration will be backed up first.", "Restore")

    def show_backup_details(self):
        backup = self._selected(self.backup_list)
        if not backup:
            return

        def show(response):
            window, page = self._editor("Backup details", "Saved metadata and configuration. This view is read only.")
            view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            metadata = copy.deepcopy(response.get("backup", {}))
            if "kind" in metadata:
                metadata["type"] = BACKUP_TYPES.get(metadata.pop("kind"), "Boot configuration")
            view.get_buffer().set_text(json.dumps({"backup": metadata, "configuration": response.get("configuration", {})}, indent=2, ensure_ascii=False))
            page.body.append(view)
            page.footer.append(ui.button("Close", window.destroy))
            window.present()

        self.dispatch({"action": "backup_details", "backup": backup["id"]}, show)

    def _write_export(self, path, data):
        try:
            Path(path).write_text(data, encoding="utf-8")
        except OSError as exc:
            self.show_error("Cannot export", exc)
            return
        self._status(f"Exported {Path(path).name}.")

    def export_selected_backup(self, path=None):
        backup = self._selected(self.backup_list)
        if not backup:
            return

        def chosen(name):
            self.dispatch({"action": "export_backup", "backup": backup["id"]}, lambda response: self._write_export(name, response["data"]))

        if path:
            chosen(path)
        else:
            ui.file_dialog(self, "Export a portable configuration", True, chosen, "*.json", "uoo-configuration.json")

    def import_configuration(self, path=None):
        def chosen(name):
            try:
                source = Path(name)
                if source.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("The configuration file is too large.")
                data = json.loads(source.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("format") != "uoo-configuration" or data.get("version") != 1 or not isinstance(data.get("configuration"), dict):
                    raise ValueError("Choose a version 1 Uoo configuration export.")
            except (OSError, ValueError) as exc:
                self.show_error("Cannot import", exc)
                return
            entries = data["configuration"].get("custom_entries", [])
            if not isinstance(entries, list):
                self.show_error("Cannot import", "Choose a version 1 Uoo configuration export.")
                return
            summary = f"Import GRUB settings and {len(entries)} managed entries from {source.name}.\nFirmware entries are not imported. The helper validates the full configuration before applying it."
            self._mutate({"action": "import_configuration", "data": data}, "Import portable configuration", summary, "Import")

        chosen(path) if path else ui.file_dialog(self, "Import a Uoo configuration", False, chosen, "*.json")

    def delete_selected_backup(self):
        backup = self._selected(self.backup_list)
        if backup and self._can_change():
            self.confirm("Delete backup", f"Permanently delete backup {backup['id']}? This does not alter boot settings.",
                         lambda: self.dispatch({"action": "delete_backup", "backup": backup["id"]}), "Delete", True)

    def choose_background(self, path=None):
        def chosen(name):
            try:
                source = Path(name)
                if source.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("Choose a PNG no larger than 2 MiB.")
                data = source.read_bytes()
                width, height = validate_png(data)
            except (OSError, ValueError) as exc:
                self.show_error("Background image", exc)
                return
            self._mutate({"action": "apply_background", "png": base64.b64encode(data).decode("ascii")}, "Set GRUB background",
                         f"Use {source.name} ({width} × {height}) as the boot-menu background? Uoo stores a validated copy.")

        chosen(path) if path else ui.file_dialog(self, "Choose a boot-menu background", False, chosen, "*.png")

    def clear_background(self):
        self._mutate({"action": "clear_background"}, "Clear GRUB background", "Remove Uoo's managed background setting and rebuild the menu?", "Clear")

    def _render_diagnostics(self):
        for group, name in ((self.diagnostic_summary, "system_rows"), (self.diagnostic_checks, "check_rows"), (self.diagnostic_devices, "device_rows")):
            for item in getattr(self, name, []):
                group.remove(item)
            setattr(self, name, [])
        diagnostics = self.snapshot.get("diagnostics", {})
        for key, value in diagnostics.get("system", {}).items():
            item = ui.row(str(key).replace("_", " ").title(), str(value))
            self.diagnostic_summary.add(item)
            self.system_rows.append(item)
        for check in diagnostics.get("checks", []):
            icon = {"ok": "emblem-ok-symbolic", "warning": "dialog-warning-symbolic", "error": "dialog-error-symbolic"}.get(check.get("status"), "dialog-information-symbolic")
            item = ui.row(check.get("name", "Check"), check.get("detail", ""), icon)
            self.diagnostic_checks.add(item)
            self.check_rows.append(item)
        for error in self.snapshot.get("errors", []):
            item = ui.row("Read error", error, "dialog-warning-symbolic")
            self.diagnostic_checks.add(item)
            self.check_rows.append(item)
        for device in self.snapshot.get("devices", diagnostics.get("devices", [])):
            item = ui.row(device.get("path", device.get("name", "Filesystem")),
                          f"{device.get('fstype') or 'Unknown filesystem'} · UUID {device.get('uuid') or '—'} · {device.get('mountpoint') or 'Not mounted'}",
                          "drive-harddisk-symbolic")
            self.diagnostic_devices.add(item)
            self.device_rows.append(item)

    def _diagnostics_report(self):
        diagnostics = self.snapshot.get("diagnostics", {})
        lines = ["Uoo Boot Manager diagnostics", "", "SYSTEM"]
        lines.extend(f"{key}: {value}" for key, value in diagnostics.get("system", {}).items())
        lines.extend(("", "CHECKS"))
        lines.extend(f"[{check.get('status', 'unknown').upper()}] {check.get('name', '')}: {check.get('detail', '')}" for check in diagnostics.get("checks", []))
        lines.extend(("", "FILESYSTEMS"))
        lines.extend(f"{device.get('path', '')} {device.get('fstype', '')} UUID={device.get('uuid') or '—'} mount={device.get('mountpoint') or '—'}" for device in self.snapshot.get("devices", []))
        if self.snapshot.get("errors"):
            lines.extend(("", "READ ERRORS", *self.snapshot["errors"]))
        return "\n".join(lines) + "\n"

    def export_diagnostics(self, path=None):
        save = lambda name: self._write_export(name, self._diagnostics_report())
        save(path) if path else ui.file_dialog(self, "Export diagnostics", True, save, "*.txt", "uoo-diagnostics.txt")

    def _render_repair(self):
        for item in self.repair_rows:
            self.repair_group.remove(item)
        self.repair_rows = []
        if not self.repair_plan:
            self.repair_group.add(self.repair_placeholder)
            self.repair_rows.append(self.repair_placeholder)
            return
        plan = self.repair_plan
        for title, detail in (("Boot mode", plan.get("mode", "Unknown")), ("Detected target", plan.get("target", "Unknown")),
                               ("Supported" if plan.get("supported") else "Unavailable", plan.get("reason", ""))):
            item = ui.row(title, detail)
            self.repair_group.add(item)
            self.repair_rows.append(item)
        for index, summary in enumerate(plan.get("summary", []), 1):
            item = ui.row(f"Step {index}", summary)
            self.repair_group.add(item)
            self.repair_rows.append(item)

    def inspect_repair(self):
        def show(response):
            self.repair_plan = response["plan"]
            self._render_repair()
            self._controls()
            self._status("Repair plan inspected. Review its target and steps.")

        self.dispatch({"action": "repair_plan"}, show)

    def execute_repair(self):
        plan = self.repair_plan
        if not plan or not plan.get("supported"):
            return
        target = plan["confirmation"]
        self._mutate({"action": "repair", "plan": plan["id"], "confirm": target}, "Execute bootloader repair",
                     f"Target: {target}\n\n" + "\n".join(plan.get("summary", [])) + f"\n\nType {target} to confirm.", "Repair", True, target)

    def _dual_selected(self):
        return [step["id"] for step in (self.dual_plan or {}).get("steps", []) if self.dual_choices.get(step["id"])]

    def _render_dualboot(self):
        for item in self.dual_rows:
            self.dual_group.remove(item)
        for item in self.dual_step_rows:
            self.dual_steps_group.remove(item)
        self.dual_rows, self.dual_step_rows = [], []
        plan = self.dual_plan
        if not plan:
            self.dual_group.add(self.dual_placeholder)
            self.dual_rows.append(self.dual_placeholder)
            self.dual_steps_group.set_visible(False)
            return
        icons = {"problem": "dialog-error-symbolic", "warning": "dialog-warning-symbolic", "info": "dialog-information-symbolic"}
        findings = list(plan.get("findings", []))
        if not plan.get("supported"):
            findings.insert(0, {"severity": "warning", "title": "Dual-boot check unavailable", "detail": plan.get("reason", "")})
        elif not any(item["severity"] in ("problem", "warning") for item in findings):
            findings.insert(0, {"severity": "ok", "title": "No dual-boot problems found",
                                "detail": "Uoo found nothing it needs to fix. Information below may still help."})
        for finding in findings:
            item = ui.row(finding["title"], finding["detail"], icons.get(finding["severity"], "emblem-ok-symbolic"))
            item.set_subtitle_lines(0)
            self.dual_group.add(item)
            self.dual_rows.append(item)
        steps = plan.get("steps", [])
        for step in steps:
            item = ui.switch(step["title"], bool(self.dual_choices.get(step["id"])), step["detail"])
            item.set_subtitle_lines(0)
            item.connect("notify::active", lambda widget, _param, key=step["id"]: self._dual_toggled(key, widget.get_active()))
            self.dual_steps_group.add(item)
            self.dual_step_rows.append(item)
        self.dual_steps_group.set_visible(bool(steps))

    def _dual_toggled(self, key, active):
        self.dual_choices[key] = active
        self._controls()

    def inspect_dual_boot(self):
        def show(response):
            self.dual_plan = response["plan"]
            self.dual_choices = {step["id"]: bool(step["recommended"]) for step in self.dual_plan.get("steps", [])}
            self._render_dualboot()
            self._controls()
            count = len(self.dual_plan.get("steps", []))
            self._status(f"Dual-boot check finished. {count} fix{'es' if count != 1 else ''} available." if count
                         else "Dual-boot check finished. Nothing needs fixing.")

        self.dispatch({"action": "dual_boot_plan"}, show)

    def execute_dual_boot_fix(self):
        plan = self.dual_plan
        chosen = self._dual_selected()
        if not plan or not chosen:
            return
        titles = {step["id"]: step["title"] for step in plan["steps"]}
        summary = "Uoo will apply, in this order:\n" + "\n".join(f"{number}. {titles[key]}" for number, key in enumerate(chosen, 1))
        acknowledge = None
        if plan.get("requires_acknowledgement"):
            volumes = ", ".join(plan["encrypted"]["bitlocker"])
            summary += (f"\n\nBitLocker-encrypted Windows volumes ({volumes}) were found. Boot changes can make Windows ask for its recovery "
                        "password. Uoo never changes encryption.")
            acknowledge = "I have my BitLocker recovery password"
        self._mutate({"action": "dual_boot_fix", "plan": plan["id"], "steps": chosen,
                      "acknowledge_recovery": bool(acknowledge)}, "Fix dual boot", summary, "Apply fixes", False, None, acknowledge)

    def _close_requested(self, _window):
        if self.busy:
            self.confirm("Action in progress", "An administrator action may still be running. Closing the window does not cancel it.", self.dispose_window, "Close window")
            return True
        if self.has_unsaved_changes():
            self.confirm("Unapplied changes", "Discard your unapplied changes and close Uoo?", self.dispose_window, "Discard and close")
            return True
        self.closed = True
        return False

    def dispose_window(self):
        self.closed = True
        self.destroy()


class UooApplication(Adw.Application):
    def __init__(self, demo=False, auto_demo=True):
        super().__init__(application_id="org.uoo.BootManager", flags=Gio.ApplicationFlags.NON_UNIQUE)
        display = Gdk.Display.get_default()
        fallback_icons = Path(__file__).resolve().parent / "icons"
        if display is not None and fallback_icons.is_dir():
            Gtk.IconTheme.get_for_display(display).add_search_path(str(fallback_icons))
        Gtk.Window.set_default_icon_name(APP_ICON)
        self.demo = demo
        self.auto_demo = auto_demo
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = BootWindow(self, self.demo, self.auto_demo)
        self.window.present()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Uoo Boot Manager for Ubuntu")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="use a safe preview with sample boot entries")
    mode.add_argument("--live", action="store_true", help="read this computer without automatic preview fallback")
    args = parser.parse_args(argv)
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        parser.exit(1, "Uoo needs a graphical desktop session.\n")
    if Adw.get_major_version() == 1 and Adw.get_minor_version() < 5:
        parser.exit(1, "Uoo requires libadwaita 1.5 or newer (Ubuntu 24.04+).\n")
    app = UooApplication(demo=args.demo, auto_demo=not args.live)
    return app.run([])
