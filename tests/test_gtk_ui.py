"""Native GTK interaction tests; skip cleanly without dependencies/display."""

import copy
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch
import zlib

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gdk, GLib, Gtk
    from uoo.gtk_app import BACKUP_TYPES, BootWindow, UooApplication, run_helper
    AVAILABLE = True
except (ModuleNotFoundError, ValueError) as exc:
    if isinstance(exc, ModuleNotFoundError) and exc.name != "gi":
        raise
    AVAILABLE = False


def ui_value(widget):
    from uoo import gtk_widgets
    return gtk_widgets.combo_value(widget)


def png():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xe9\x54\x20")) + chunk(b"IEND", b"")


@unittest.skipUnless(AVAILABLE, "GTK4/libadwaita PyGObject is not installed")
class NativeDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # PyGObject's Python 3.14 asyncio bridge emits this host-only warning.
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise unittest.SkipTest("No graphical display")
        if Adw.get_minor_version() < 5:
            raise unittest.SkipTest("libadwaita 1.5 or newer is required")
        Adw.init()
        cls.application = UooApplication(demo=True)
        cls.application.register(None)

    def setUp(self):
        self.window = BootWindow(self.application, demo=True)
        self.errors = []
        self.window.error_handler = lambda title, message: self.errors.append((title, str(message)))
        self.window.confirm_handler = lambda *_args: True
        self.window.present()
        self.addCleanup(self.window.dispose_window)
        self.pump()

    def pump(self, duration=0.1):
        def run_for(seconds):
            loop = GLib.MainLoop()
            GLib.timeout_add(max(1, int(seconds * 1000)), loop.quit)
            loop.run()

        deadline = time.monotonic() + 3
        while self.window.busy and time.monotonic() < deadline:
            run_for(0.05)
        self.assertFalse(self.window.busy)
        run_for(duration)

    def assert_no_errors(self):
        self.assertEqual(self.errors, [])

    def test_native_window_sidebar_and_small_layout(self):
        self.assertIsInstance(self.window, Adw.ApplicationWindow)
        self.assertEqual(len(self.window.pages), 9)
        self.window.set_default_size(900, 640)
        self.pump(0.2)
        for key, page in self.window.pages.items():
            self.window.navigate(key)
            self.pump(0.05)
            self.assertGreater(page.footer.get_height(), 0)
            self.assertLessEqual(page.footer.get_width(), page.get_width())
            self.assertTrue(page.scroll.get_vexpand())
        self.assert_no_errors()

    def test_application_icon_resolves_from_the_source_fallback_theme(self):
        self.assertEqual(self.window.get_icon_name(), "org.uoo.BootManager")
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        self.assertTrue(theme.has_icon("org.uoo.BootManager"))
        self.assertTrue(theme.has_icon("org.uoo.BootManager-symbolic"))

    def test_fixed_default_remember_and_one_time_grub(self):
        self.window.select_grub(3)
        self.window.timeout.set_value(6)
        self.window.apply_boot()
        self.assertEqual(self.window.snapshot["settings"]["GRUB_DEFAULT"], "windows")
        self.window.default_mode.set_selected(1)
        self.window.apply_boot()
        self.assertEqual(self.window.snapshot["settings"]["GRUB_DEFAULT"], "saved")
        self.window.select_grub(0)
        self.window.grub_next_boot(False)
        self.assertEqual(self.window.snapshot["grub_next"], "ubuntu")
        self.window.grub_next_boot(True)
        self.assertEqual(self.window.snapshot["grub_next"], "")
        self.assert_no_errors()

    def test_advanced_appearance_and_shared_menu_style(self):
        self.window.fields["GRUB_CMDLINE_LINUX"].set_text("ipv6.disable=1")
        self.window.recordfail_timeout.set_value(20)
        self.window.os_probe.set_active(True)
        self.window.recovery_enabled.set_active(False)
        self.window.apply_advanced()
        self.assertEqual(self.window.snapshot["settings"]["GRUB_CMDLINE_LINUX"], "ipv6.disable=1")
        self.assertEqual(str(self.window.snapshot["settings"]["GRUB_DISABLE_RECOVERY"]).lower(), "true")
        self.window.fields["GRUB_GFXMODE"].set_text("1280x720")
        self.window.appearance_style.set_selected(2)
        self.assertEqual(self.window.menu_style.get_selected(), 2)
        self.window.apply_appearance()
        self.assertEqual(self.window.snapshot["settings"]["GRUB_GFXMODE"], "1280x720")
        self.assertEqual(self.window.snapshot["settings"]["GRUB_TIMEOUT_STYLE"], "countdown")
        self.assert_no_errors()

    def test_grub_forms_send_only_edited_settings(self):
        # A value the form shows inexactly (an unlisted payload, a misread command line) must survive other edits.
        self.window.snapshot["settings"]["GRUB_GFXPAYLOAD_LINUX"] = "1024x768"
        self.window._render()
        self.assertEqual(ui_value(self.window.payload), "keep")
        requests = []
        with patch.object(self.window, "_mutate", lambda request, *_args: requests.append(request)):
            self.window.recordfail_timeout.set_value(self.window.recordfail_timeout.get_value_as_int() + 5)
            self.window.apply_advanced()
            self.window.fields["GRUB_GFXMODE"].set_text("1280x720")
            self.window.apply_appearance()
            self.window.apply_appearance()
        self.assertEqual([set(request["changes"]) for request in requests[:2]], [{"GRUB_RECORDFAIL_TIMEOUT"}, {"GRUB_GFXMODE"}])
        self.assertEqual(len(requests), 3)

    def test_stale_boot_order_id_can_be_excluded(self):
        firmware = self.window.snapshot["firmware"]
        firmware["order"] = firmware["order"] + ["00FF"]
        self.window._render()
        self.window.select_firmware("00FF")
        self.assertTrue(self.window.buttons["firmware_include"].get_sensitive())
        self.window.toggle_order_inclusion()
        self.assertNotIn("00FF", self.window.order)

    def test_failed_first_read_replaces_the_loading_banner(self):
        window = BootWindow(self.application, demo=True)
        window.error_handler = lambda *_args: None
        self.addCleanup(window.dispose_window)

        def idle():
            deadline = time.monotonic() + 3
            while window.busy and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
        idle()
        with patch.object(window.demo_manager, "dispatch", side_effect=RuntimeError("unreadable")):
            window.snapshot = {}
            window._reload()
            idle()
        self.assertEqual(window.banner.get_title(), "The boot configuration could not be read")

    def test_invalid_kernel_options_are_rejected_before_dispatch(self):
        before = copy.deepcopy(self.window.snapshot)
        self.window.fields["GRUB_CMDLINE_LINUX"].set_text("quiet; reboot")
        self.window.apply_advanced()
        self.assertEqual(self.window.snapshot, before)
        self.assertEqual(len(self.errors), 1)

    def test_custom_editor_add_edit_cancel_and_apply(self):
        filesystem_uuid = next(device["uuid"] for device in self.window.snapshot["devices"] if device.get("esp"))
        self.window.edit_custom(True)
        editor = self.window.last_editor
        editor["fields"]["title"].set_text("My EFI loader")
        editor["fields"]["uuid"].set_text(filesystem_uuid)
        editor["fields"]["loader"].set_text("/EFI/ubuntu/shimx64.efi")
        editor["save"].emit("clicked")
        self.assertEqual(len(self.window.custom_draft), 1)
        self.assertEqual(self.window.snapshot["custom_entries"], [])
        self.window.edit_custom(False)
        editor = self.window.last_editor
        editor["fields"]["title"].set_text("Renamed EFI loader")
        editor["save"].emit("clicked")
        self.window.apply_custom()
        self.assertEqual(self.window.snapshot["custom_entries"][0]["title"], "Renamed EFI loader")
        self.window.edit_custom(True)
        self.window.last_editor["window"].destroy()
        self.assertEqual(len(self.window.custom_draft), 1)
        self.assert_no_errors()

    def test_custom_filesystem_picker_and_editor_error_parent(self):
        self.window.edit_custom(True)
        editor = self.window.last_editor
        self.assertEqual(editor["device_picker"].get_selected(), 0)
        self.assertEqual(editor["fields"]["uuid"].get_text(), "")
        editor["device_picker"].set_selected(1)
        expected = next(device["uuid"] for device in self.window.snapshot["devices"] if device.get("uuid"))
        self.assertEqual(editor["fields"]["uuid"].get_text(), expected)
        self.window.error_handler = None
        editor["save"].emit("clicked")
        self.pump(0.05)
        self.assertEqual(self.window.last_dialog.get_root(), editor["window"])
        self.window.last_dialog.close()
        editor["window"].destroy()

    def test_boot_file_browse_starts_on_selected_filesystem(self):
        self.window.edit_custom(True)
        editor = self.window.last_editor
        device = next(device for device in self.window.snapshot["devices"] if device.get("esp"))
        with patch("uoo.gtk_app.ui.file_dialog") as choose:
            self.window._browse_partition(device["uuid"], editor["fields"]["loader"], editor["window"], "*.efi")
        self.assertEqual(choose.call_args.kwargs["folder"], device["mountpoint"])
        editor["window"].destroy()
        self.assert_no_errors()

    def test_custom_linux_editor_and_toggle_delete(self):
        device = next(device for device in self.window.snapshot["devices"] if device.get("mountpoint") == "/")
        self.window.edit_custom(True)
        editor = self.window.last_editor
        editor["kind"].set_selected(1)
        for key, value in {"title": "Linux custom", "uuid": device["uuid"], "kernel": "/boot/vmlinuz-demo",
                           "initrd": "/boot/initrd.img-demo", "options": "quiet"}.items():
            editor["fields"][key].set_text(value)
        editor["save"].emit("clicked")
        self.window.toggle_custom()
        self.window.apply_custom()
        self.assertFalse(self.window.snapshot["custom_entries"][0]["enabled"])
        self.window.custom_list.select_row(self.window.custom_list.get_row_at_index(0))
        self.window.delete_custom()
        self.window.apply_custom()
        self.assertEqual(self.window.snapshot["custom_entries"], [])
        self.assert_no_errors()

    def test_firmware_order_next_active_and_create_editor(self):
        self.window.move_firmware(1)
        self.window.apply_order()
        self.assertEqual(self.window.snapshot["firmware"]["order"][:2], ["0001", "0000"])
        self.window.select_firmware("0002")
        self.window.next_boot(False)
        self.assertEqual(self.window.snapshot["firmware"]["next"], "0002")
        self.window.next_boot(True)
        self.window.select_firmware("0002")
        self.window.set_firmware_active()
        usb = next(entry for entry in self.window.snapshot["firmware"]["entries"] if entry["id"] == "0002")
        self.assertFalse(usb["active"])
        self.window.create_firmware_entry()
        editor = self.window.last_editor
        editor["label"].set_text("Added loader")
        editor["loader"].set_text("/EFI/ubuntu/shimx64.efi")
        editor["save"].emit("clicked")
        self.assertTrue(any(entry["label"] == "Added loader" for entry in self.window.snapshot["firmware"]["entries"]))
        self.assert_no_errors()

    def test_scheduled_firmware_entry_requires_clearing_before_disable_delete(self):
        self.window.select_firmware("0002")
        self.window.next_boot(False)
        self.window.select_firmware("0002")
        before = copy.deepcopy(self.window.snapshot)
        self.window.set_firmware_active()
        self.window.delete_firmware_entry()
        self.assertEqual(self.window.snapshot, before)
        self.assertEqual([heading for heading, _message in self.errors], ["Clear next boot first", "Clear next boot first"])

    def test_unreadable_grub_environment_and_custom_model_disable_editing(self):
        self.window.snapshot["capabilities"]["grub_next"] = False
        self.window.snapshot["capabilities"]["custom_entries"] = False
        self.window._render()
        self.assertFalse(self.window.buttons["grub_once"].get_sensitive())
        self.assertFalse(self.window.buttons["custom_add"].get_sensitive())
        self.assertFalse(self.window.buttons["custom_apply"].get_sensitive())
        self.assertIn("Read as administrator", self.window.custom_note.get_text())

    def test_unsupported_environment_disables_remembered_default(self):
        self.window.snapshot["capabilities"]["grub_next"] = False
        self.window.default_mode.set_selected(1)
        self.window._controls()
        self.assertFalse(self.window.buttons["boot_apply"].get_sensitive())
        self.assertIn("Diagnostics", self.window.default_mode.get_subtitle())
        before = copy.deepcopy(self.window.snapshot)
        self.window.apply_boot()
        self.assertEqual(self.window.snapshot, before)
        self.assertEqual(self.errors[0][0], "Remembered default unavailable")

    def test_native_typed_delete_dialog_rejects_wrong_id(self):
        self.window.confirm_handler = None
        self.window.select_firmware("0002")
        self.window.delete_firmware_entry()
        dialog = self.window.last_dialog
        field = dialog.get_extra_child()
        field.set_text("wrong")
        self.assertFalse(dialog.get_response_enabled("apply"))
        dialog.emit("response", "apply")
        dialog.close()
        self.assertTrue(any(entry["id"] == "0002" for entry in self.window.snapshot["firmware"]["entries"]))
        self.window.delete_firmware_entry()
        dialog = self.window.last_dialog
        dialog.get_extra_child().set_text("0002")
        self.assertTrue(dialog.get_response_enabled("apply"))
        dialog.emit("response", "apply")
        dialog.close()
        self.assertFalse(any(entry["id"] == "0002" for entry in self.window.snapshot["firmware"]["entries"]))
        self.assert_no_errors()

    def test_current_firmware_entry_cannot_be_deleted(self):
        self.window.select_firmware("0000")
        self.window.delete_firmware_entry()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.window.snapshot["firmware"]["current"], "0000")

    def test_backup_creation_preserves_draft_export_import_delete(self):
        self.window.timeout.set_value(21)
        self.window.create_manual_backup()
        self.window.last_editor["save"].emit("clicked")
        self.assertEqual(self.window.timeout.get_value_as_int(), 21)
        self.assertTrue(self.window.has_unsaved_changes())
        self.assertTrue(self.window.backups)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "configuration.json"
            self.window.export_selected_backup(str(path))
            self.assertEqual(json.loads(path.read_text())["format"], "uoo-configuration")
            self.window.import_configuration(str(path))
        identifier = self.window._selected(self.window.backup_list)["id"]
        self.window.delete_selected_backup()
        self.assertFalse(any(backup["id"] == identifier for backup in self.window.backups))
        self.assert_no_errors()

    def test_background_png_set_clear_and_diagnostics_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "background.png"
            image.write_bytes(png())
            self.window.choose_background(str(image))
            self.assertTrue(self.window.snapshot["background"])
            self.window.clear_background()
            self.assertFalse(self.window.snapshot["background"])
            report = Path(temporary) / "diagnostics.txt"
            self.window.export_diagnostics(str(report))
            self.assertIn("CHECKS", report.read_text())
        self.assert_no_errors()

    def test_readable_backup_types_and_corrupt_backup_delete_only(self):
        self.window.backups = [{"id": f"backup-{index}", "created": "2026-10-14 12:00", "kind": kind,
                                "summary": "Saved boot configuration"} for index, kind in enumerate(BACKUP_TYPES)]
        self.window._render_backups()
        for index, label in enumerate(BACKUP_TYPES.values()):
            self.assertTrue(self.window.backup_list.get_row_at_index(index).get_subtitle().endswith(label))
        corrupt = next(backup for backup in self.window.backups if backup["kind"] == "corrupt")
        index = self.window.backups.index(corrupt)
        self.window.backup_list.select_row(self.window.backup_list.get_row_at_index(index))
        self.assertFalse(self.window.buttons["backup_restore"].get_sensitive())
        self.assertTrue(self.window.buttons["backup_actions"].get_sensitive())
        with patch.object(self.window, "dispatch") as dispatch:
            self.window.restore()
            dispatch.assert_not_called()
            self.window.delete_selected_backup()
            dispatch.assert_called_once_with({"action": "delete_backup", "backup": corrupt["id"]})
        self.assertEqual(self.errors[0][0], "Unreadable backup")

    def test_refresh_cancel_and_repair_typed_target(self):
        self.window.timeout.set_value(22)
        self.window.confirm_handler = lambda *_args: False
        self.window.reload()
        self.assertEqual(self.window.timeout.get_value_as_int(), 22)
        self.window.confirm_handler = lambda *_args: True
        self.window.reload()
        self.pump()
        self.assertFalse(self.window.has_unsaved_changes())
        self.window.inspect_repair()
        self.assertTrue(self.window.repair_plan["supported"])
        self.window.confirm_handler = None
        self.window.execute_repair()
        dialog = self.window.last_dialog
        self.assertFalse(dialog.get_response_enabled("apply"))
        dialog.get_extra_child().set_text("/boot/efi")
        dialog.emit("response", "apply")
        dialog.close()
        self.assert_no_errors()

    def break_demo_dual_boot(self):
        self.window.dispatch({"action": "apply_grub", "changes": {"GRUB_TIMEOUT_STYLE": "hidden", "GRUB_TIMEOUT": 0}})
        self.window.dispatch({"action": "apply_order", "order": ["0001", "0000", "0002"]})

    def test_dual_boot_check_lists_findings_and_selectable_steps(self):
        self.window.navigate("dualboot")
        self.assertFalse(self.window.buttons["dualboot_fix"].get_sensitive())
        self.window.inspect_dual_boot()
        self.assertEqual(self.window.dual_plan["steps"], [])
        self.assertFalse(self.window.buttons["dualboot_fix"].get_sensitive())
        self.break_demo_dual_boot()
        self.window.inspect_dual_boot()
        plan = self.window.dual_plan
        self.assertEqual([step["id"] for step in plan["steps"]], ["grub_menu", "uefi_order"])
        self.assertTrue(plan["requires_acknowledgement"])
        self.assertEqual(len(self.window.dual_step_rows), 2)
        self.assertTrue(self.window.buttons["dualboot_fix"].get_sensitive())
        # The BitLocker-sensitive reordering is not preselected; deselecting every step disables the fix.
        self.assertEqual(self.window._dual_selected(), ["grub_menu"])
        self.window.dual_step_rows[0].set_active(False)
        self.assertFalse(self.window.buttons["dualboot_fix"].get_sensitive())
        self.window.dual_step_rows[1].set_active(True)
        self.assertTrue(self.window.buttons["dualboot_fix"].get_sensitive())
        self.assert_no_errors()

    def test_dual_boot_fix_applies_selected_steps_and_clears_the_plan(self):
        self.break_demo_dual_boot()
        self.window.inspect_dual_boot()
        self.window.dual_step_rows[1].set_active(True)
        self.window.execute_dual_boot_fix()
        self.pump()
        self.assertEqual(self.window.snapshot["settings"]["GRUB_TIMEOUT_STYLE"], "menu")
        self.assertEqual(self.window.snapshot["firmware"]["order"][0], "0000")
        self.assertIsNone(self.window.dual_plan)
        self.assertEqual(self.window.backups[0]["kind"], "all")
        self.window.inspect_dual_boot()
        self.assertEqual(self.window.dual_plan["steps"], [])
        self.assert_no_errors()

    def test_native_dual_boot_dialog_requires_the_recovery_key_acknowledgement(self):
        self.break_demo_dual_boot()
        self.window.inspect_dual_boot()
        self.window.confirm_handler = None
        self.window.execute_dual_boot_fix()
        dialog = self.window.last_dialog
        check = dialog.get_extra_child()
        self.assertIsInstance(check, Gtk.CheckButton)
        self.assertFalse(dialog.get_response_enabled("apply"))
        dialog.emit("response", "apply")
        self.assertEqual(self.window.snapshot["settings"]["GRUB_TIMEOUT_STYLE"], "hidden")
        check.set_active(True)
        self.assertTrue(dialog.get_response_enabled("apply"))
        dialog.emit("response", "apply")
        dialog.close()
        self.pump()
        self.assertEqual(self.window.snapshot["settings"]["GRUB_TIMEOUT_STYLE"], "menu")
        self.assert_no_errors()

    def test_cancelled_authentication_keeps_drafts_and_reenables_controls(self):
        self.window.demo = False
        self.window.snapshot["demo"] = False
        with patch("uoo.gtk_app.trusted_helper_available", return_value=True):
            self.window._render()
            self.window.timeout.set_value(25)
            with patch("uoo.gtk_app.run_helper", side_effect=RuntimeError("Administrator authentication was cancelled.")):
                self.window.read_as_administrator()
                self.pump()
            self.assertEqual(self.window.timeout.get_value_as_int(), 25)
            self.assertTrue(self.window.has_unsaved_changes())
            self.assertFalse(self.window.busy)
            self.assertTrue(self.window.admin_button.get_sensitive())
            self.assertTrue(self.window.buttons["boot_apply"].get_sensitive())
            self.assertIn("cancelled", self.errors[0][1])

    def test_backup_refresh_keeps_firmware_order_draft(self):
        self.window.move_firmware(1)
        draft = list(self.window.order)
        self.window.dispatch({"action": "create_backup", "kind": "all"})
        self.window.load_backups()
        self.assertEqual(self.window.order, draft)
        self.assertTrue(self.window.has_unsaved_changes())
        self.window.confirm_handler = lambda *_args: False
        self.window.restore()
        self.assertEqual(self.window.order, draft)
        self.assert_no_errors()

    def test_helper_transports_large_json_on_stdin_fixed_wrapper(self):
        response = subprocess.CompletedProcess([], 0, stdout='{"ok": true, "message": "done"}', stderr="")
        with patch("uoo.gtk_app.subprocess.run", return_value=response) as called:
            run_helper({"action": "apply_background", "png": "x" * 200000})
        args, kwargs = called.call_args
        self.assertEqual(args[0], ["/usr/bin/pkexec", "/usr/bin/uoo-boot-helper", "--stdin"])
        self.assertGreater(len(kwargs["input"]), 200000)

    def test_helper_failures_are_explained_in_plain_language(self):
        def failing(returncode, stdout="", stderr=""):
            return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)
        cases = [
            (dict(return_value=failing(126)), "cancelled or you are not authorized"),
            (dict(return_value=failing(127)), "no polkit agent"),
            (dict(return_value=failing(1, stdout="", stderr="")), "no valid response"),
            (dict(return_value=failing(1, stdout="not json", stderr="pkexec: boom")), "pkexec: boom"),
            (dict(return_value=failing(1, stdout='{"ok": false, "error": "efibootmgr failed: No space"}')), "efibootmgr failed: No space"),
            (dict(side_effect=subprocess.TimeoutExpired(["pkexec"], 600)), "10 minutes"),
            (dict(side_effect=FileNotFoundError("pkexec")), "pkexec is not installed"),
            (dict(side_effect=PermissionError("denied")), "could not be started"),
        ]
        for options, expected in cases:
            with self.subTest(expected=expected), patch("uoo.gtk_app.subprocess.run", **options):
                with self.assertRaisesRegex(RuntimeError, expected):
                    run_helper({"action": "snapshot"})

    def test_flatpak_authentication_uses_the_host_fixed_helper(self):
        response = subprocess.CompletedProcess([], 0, stdout='{"ok": true}', stderr="")
        with patch("uoo.desktop_runtime.is_flatpak", return_value=True), patch("uoo.gtk_app.subprocess.run", return_value=response) as called:
            run_helper({"action": "snapshot"})
        self.assertEqual(called.call_args.args[0], ["/usr/bin/flatpak-spawn", "--host", "/usr/bin/pkexec", "/usr/bin/uoo-boot-helper", "--stdin"])
        self.assertEqual(json.loads(called.call_args.kwargs["input"]), {"action": "snapshot"})

    def test_flatpak_demo_does_not_require_or_contact_a_host_helper(self):
        with patch("uoo.desktop_runtime.is_flatpak", return_value=True), patch("uoo.desktop_runtime._inspect", side_effect=AssertionError("Demo contacted host")):
            self.window.reload()
            self.pump()
            self.window.edit_custom(True)
            editor = self.window.last_editor
            editor["fields"]["title"].set_text("Manual EFI path")
            editor["fields"]["uuid"].set_text("ABCD-1234")
            editor["fields"]["loader"].set_text("/EFI/ubuntu/shimx64.efi")
            editor["save"].emit("clicked")
        self.assertEqual(self.window.custom_draft[0]["title"], "Manual EFI path")
        self.assert_no_errors()


if __name__ == "__main__":
    unittest.main()
