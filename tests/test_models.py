"""Validate every user-controlled value before generating shell or GRUB code."""

import copy
import shutil
import subprocess
import struct
import tempfile
import unittest
from pathlib import Path
import zlib

from uoo.core import parse_entries, parse_settings
from uoo.models import (GRUB_COLORS, compile_custom_entries,
                        render_grub_settings, validate_custom_entries,
                        validate_grub_changes, validate_png)


def efi_entry(**changes):
    entry = {"id": "uoo-" + "1" * 32, "title": "Windows Boot Manager",
             "type": "efi", "enabled": True, "uuid": "ABCD-1234",
             "loader": "/EFI/Microsoft/Boot/bootmgfw.efi"}
    entry.update(changes)
    return entry


def linux_entry(**changes):
    entry = {"id": "uoo-" + "2" * 32, "title": "Ubuntu Linux",
             "type": "linux", "enabled": True,
             "uuid": "12345678-1234-1234-abcd-123456789abc",
             "kernel": "/boot/vmlinuz-6.8.0-generic",
             "initrd": "/boot/initrd.img-6.8.0-generic",
             "options": "root=UUID=12345678-1234-1234-abcd-123456789abc ro quiet splash"}
    entry.update(changes)
    return entry


class GrubModelTests(unittest.TestCase):
    def test_all_supported_settings(self):
        settings = {
            "GRUB_DEFAULT": "advanced>linux-6.8", "GRUB_TIMEOUT": 5,
            "GRUB_TIMEOUT_STYLE": "menu", "GRUB_SAVEDEFAULT": False,
            "GRUB_CMDLINE_LINUX": "root=UUID=abcd-1234 iommu=pt mitigations=off",
            "GRUB_CMDLINE_LINUX_DEFAULT": "quiet splash",
            "GRUB_DISABLE_OS_PROBER": True, "GRUB_DISABLE_RECOVERY": False,
            "GRUB_GFXMODE": "1920x1080x32,1280x720,auto",
            "GRUB_GFXPAYLOAD_LINUX": "keep",
            "GRUB_COLOR_NORMAL": "light-gray/black",
            "GRUB_COLOR_HIGHLIGHT": "white/blue", "GRUB_RECORDFAIL_TIMEOUT": 30,
        }
        validated = validate_grub_changes(settings)
        self.assertEqual(validated, settings)
        self.assertIsNot(validated, settings)
        validated["GRUB_TIMEOUT"] = 15
        self.assertEqual(settings["GRUB_TIMEOUT"], 5)

    def test_object_required_and_unknown_keys_refused(self):
        for settings in (None, [], "GRUB_TIMEOUT=5", {"GRUB_THEME": "/theme"},
                         {"BASH_ENV": "payload"}, {1: "value"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_grub_changes(settings)

    def test_empty_changes_valid(self):
        self.assertEqual(validate_grub_changes({}), {})
        self.assertEqual(render_grub_settings({}), "# Managed by Uoo Boot Manager.\n")

    def test_timeout_bounds(self):
        for key in ("GRUB_TIMEOUT", "GRUB_RECORDFAIL_TIMEOUT"):
            for value in (-1, 0, 300):
                with self.subTest(key=key, value=value):
                    self.assertEqual(validate_grub_changes({key: value}), {key: value})
            for value in (-2, 301, True, False, 5.0, "5", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_grub_changes({key: value})

    def test_strict_booleans(self):
        for key in ("GRUB_SAVEDEFAULT", "GRUB_DISABLE_OS_PROBER", "GRUB_DISABLE_RECOVERY"):
            for value in (True, False):
                with self.subTest(key=key, value=value):
                    self.assertEqual(validate_grub_changes({key: value}), {key: value})
            for value in (1, 0, "true", "false", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_grub_changes({key: value})

    def test_literal_defaults(self):
        for selector in ("0", "1>0>2", "ubuntu", "advanced>kernel-6.8", "saved",
                         "uoo-12345678123412341234123456789abc", "a_.:+/@%-b"):
            with self.subTest(selector=selector):
                self.assertEqual(validate_grub_changes({"GRUB_DEFAULT": selector}), {"GRUB_DEFAULT": selector})

    def test_unsafe_defaults_refused(self):
        for value in (None, 1, "", ">", ">ubuntu", "ubuntu>", "a>>b", "a b",
                      "${id}", 'a"b', "a'b", "a`b", "a\\b", "a;b", "a\n", "a\t",
                      "a" * 2049, "Убунту", "a\x7fb"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_grub_changes({"GRUB_DEFAULT": value})

    def test_styles_and_payload(self):
        for value in ("menu", "hidden", "countdown"):
            self.assertEqual(validate_grub_changes({"GRUB_TIMEOUT_STYLE": value})["GRUB_TIMEOUT_STYLE"], value)
        for value in ("keep", "text"):
            self.assertEqual(validate_grub_changes({"GRUB_GFXPAYLOAD_LINUX": value})["GRUB_GFXPAYLOAD_LINUX"], value)
        for key in ("GRUB_TIMEOUT_STYLE", "GRUB_GFXPAYLOAD_LINUX"):
            for value in ("", None, True, "auto", "menu;echo bad", ["menu"]):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_grub_changes({key: value})

    def test_realistic_kernel_options(self):
        options = "root=UUID=abcd-1234 ro quiet splash intel_iommu=on iommu=pt systemd.unit=rescue.target mitigations=off module_blacklist=nouveau,nvidia resume=/dev/disk/by-uuid/ABCD net.ifnames=0 path=/@home size=50% debug+test"
        for key in ("GRUB_CMDLINE_LINUX", "GRUB_CMDLINE_LINUX_DEFAULT"):
            self.assertEqual(validate_grub_changes({key: options})[key], options)
            self.assertEqual(validate_grub_changes({key: ""})[key], "")
            self.assertEqual(validate_grub_changes({key: "a" * 2048})[key], "a" * 2048)

    def test_unsafe_kernel_options_refused(self):
        for value in ("a;reboot", "a&&reboot", "a|cat", "a>file", "a<file", 'a"b',
                      "a'b", "$(reboot)", "`reboot`", "a\\b", "a\n", "a\r", "a\t",
                      "$(x)", "Убунту", "a\x00b", "a" * 2049, None, 3):
            for key in ("GRUB_CMDLINE_LINUX", "GRUB_CMDLINE_LINUX_DEFAULT"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_grub_changes({key: value})

    def test_graphics_modes(self):
        for value in ("auto", "640x480", "320x200x8", "16384x16384x32",
                      "1920x1080x24,1280x720x16,800x600x15,auto"):
            self.assertEqual(validate_grub_changes({"GRUB_GFXMODE": value})["GRUB_GFXMODE"], value)

    def test_invalid_graphics_modes(self):
        for value in ("", None, "Auto", "319x200", "320x199", "16385x200", "640x16385",
                      "640x480x0", "640x480x7", "640x480x30", "640x480x33", "640x480x100",
                      "640X480", "640x480,", ",640x480", "640x480,,auto", "640x480, auto",
                      "640x480;auto", "640x480$(x)", ",".join(["640x480"] * 17), "a" * 257):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_grub_changes({"GRUB_GFXMODE": value})

    def test_every_gnu_color(self):
        self.assertEqual(len(GRUB_COLORS), 16)
        for color in GRUB_COLORS:
            for key in ("GRUB_COLOR_NORMAL", "GRUB_COLOR_HIGHLIGHT"):
                value = f"{color}/black"
                self.assertEqual(validate_grub_changes({key: value})[key], value)

    def test_invalid_colors(self):
        for value in ("red", "red/black/blue", "/black", "red/", "gray/black", "lightgrey/black",
                      "RED/black", "red/ black", "red/black;reboot", None, ["red", "black"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_grub_changes({"GRUB_COLOR_NORMAL": value})

    def test_render_is_deterministic_and_shell_safe(self):
        settings = {"GRUB_TIMEOUT": -1, "GRUB_SAVEDEFAULT": True,
                    "GRUB_DEFAULT": "advanced>linux", "GRUB_CMDLINE_LINUX_DEFAULT": "quiet splash",
                    "GRUB_DISABLE_OS_PROBER": False}
        rendered = render_grub_settings(settings)
        self.assertEqual(rendered, render_grub_settings(dict(reversed(list(settings.items())))))
        assignments = rendered.splitlines()[1:]
        self.assertEqual([line.split("=", 1)[0] for line in assignments], sorted(settings))
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "settings.cfg"
            config.write_text(rendered, encoding="utf-8")
            process = subprocess.run(
                ["sh", "-c", '. "$1"; printf "%s\\n%s\\n%s\\n%s\\n%s\\n" "$GRUB_DEFAULT" "$GRUB_TIMEOUT" "$GRUB_SAVEDEFAULT" "$GRUB_CMDLINE_LINUX_DEFAULT" "$GRUB_DISABLE_OS_PROBER"',
                 "uoo-test", str(config)], text=True, capture_output=True, check=True,
            )
            self.assertEqual(process.stdout.splitlines(), ["advanced>linux", "-1", "true", "quiet splash", "false"])
        self.assertEqual(parse_settings(rendered), {
            "GRUB_TIMEOUT": "-1", "GRUB_SAVEDEFAULT": "true", "GRUB_DEFAULT": "advanced>linux",
            "GRUB_CMDLINE_LINUX_DEFAULT": "quiet splash", "GRUB_DISABLE_OS_PROBER": "false",
        })

    def test_render_revalidates_unsafe_input(self):
        with self.assertRaises(ValueError):
            render_grub_settings({"GRUB_DEFAULT": 'a";reboot'})


class CustomEntryModelTests(unittest.TestCase):
    def test_supported_entries_and_independent_copies(self):
        entries = [efi_entry(), linux_entry()]
        validated = validate_custom_entries(entries)
        self.assertEqual(validated, entries)
        self.assertIsNot(validated, entries)
        self.assertIsNot(validated[0], entries[0])
        validated[0]["title"] = "Other"
        self.assertEqual(entries[0]["title"], "Windows Boot Manager")

    def test_empty_and_entry_count_limit(self):
        self.assertEqual(validate_custom_entries([]), [])
        entries = [efi_entry(id=f"uoo-{index:032x}") for index in range(32)]
        self.assertEqual(len(validate_custom_entries(entries)), 32)
        with self.assertRaises(ValueError):
            validate_custom_entries(entries + [efi_entry(id="uoo-" + "f" * 32)])

    def test_container_and_schema_types(self):
        for entries in (None, {}, (), "[]", [None], ["entry"], [[]]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                validate_custom_entries(entries)
        for entry in (efi_entry(type=None), efi_entry(type="shell"), efi_entry(type=True),
                      efi_entry(type=["efi"]), efi_entry(extra="ignored"),
                      efi_entry(kernel="/boot/vmlinuz")):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                validate_custom_entries([entry])

    def test_missing_required_fields(self):
        for base in (efi_entry(), linux_entry()):
            for key in base:
                entry = dict(base)
                entry.pop(key)
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate_custom_entries([entry])

    def test_ids(self):
        for value in (None, "", "uoo-abc", "uoo-" + "a" * 31, "uoo-" + "a" * 33,
                      "uoo-" + "g" * 32, "uoo-" + "A" * 32, "other-" + "a" * 32):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_custom_entries([efi_entry(id=value)])
        with self.assertRaises(ValueError):
            validate_custom_entries([efi_entry(), efi_entry(title="Duplicate")])

    def test_strict_enabled_boolean(self):
        for value in (0, 1, None, "true", "false"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_custom_entries([efi_entry(enabled=value)])
        self.assertFalse(validate_custom_entries([efi_entry(enabled=False)])[0]["enabled"])

    def test_human_unicode_title_and_safe_punctuation(self):
        title = "Ubuntu — Відновлення 🐧 (#1) > Backup {safe}"
        self.assertEqual(validate_custom_entries([efi_entry(title=title)])[0]["title"], title)
        self.assertEqual(validate_custom_entries([efi_entry(title="a" * 120)])[0]["title"], "a" * 120)

    def test_unsafe_titles_refused(self):
        for value in ("", "   ", None, "a" * 121, "Ubuntu's", 'Ubuntu "LTS"',
                      "Ubuntu; reboot", "$(reboot)", "Ubuntu `reboot`", "Ubuntu\\reboot",
                      "Ubuntu\nreboot", "Ubuntu\rreboot", "Ubuntu\x00reboot",
                      "Ubuntu\x85reboot", "Ubuntu\u2028reboot", "Ubuntu\u2029reboot", "Ubuntu\tLinux"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_custom_entries([efi_entry(title=value)])

    def test_filesystem_uuid_forms(self):
        for value in ("ABCD-1234", "abcdef0123456789", "12345678-1234-1234-ABCD-123456789ABC"):
            with self.subTest(value=value):
                self.assertEqual(validate_custom_entries([efi_entry(uuid=value)])[0]["uuid"], value)

    def test_unsafe_uuids_refused(self):
        for value in ("", "123", "-ABCD", "ABCD-", "ABCD--1234", "ABCG-1234", "ABCD/1234",
                      "ABCD 1234", "ABCD;reboot", "$(reboot)", "'abcd'", "A" * 65, None, 1234):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_custom_entries([efi_entry(uuid=value)])

    def test_efi_paths(self):
        for value in ("/EFI/BOOT/BOOTX64.EFI", "/EFI/ubuntu/shimx64.efi", "/EFI/a-b_c.1@2+3%4/boot.efi"):
            self.assertEqual(validate_custom_entries([efi_entry(loader=value)])[0]["loader"], value)

    def test_unsafe_efi_paths_refused(self):
        for value in ("EFI/Boot/boot.efi", "/efi/Boot/boot.efi", "/boot/boot.efi", "/EFI/Boot/file.bin",
                      "/EFI/../Boot/file.efi", "/EFI/./file.efi", "/EFI//file.efi", "/EFI/file.efi/",
                      "/EFI/file name.efi", "/EFI/$(reboot).efi", "/EFI/a'b.efi", '/EFI/a"b.efi',
                      "/EFI/a\\b.efi", "/EFI/a;reboot.efi", "/EFI/a\n.efi", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_custom_entries([efi_entry(loader=value)])

    def test_linux_root_and_separate_boot_paths(self):
        for kernel, initrd in (("/boot/vmlinuz-6.8.0", "/boot/initrd.img-6.8.0"),
                               ("/vmlinuz-6.8.0", "/initrd.img-6.8.0"),
                               ("/boot/vmlinuz", "")):
            entry = linux_entry(kernel=kernel, initrd=initrd, options="")
            self.assertEqual(validate_custom_entries([entry]), [entry])

    def test_unsafe_linux_paths_refused(self):
        for field, values in (
            ("kernel", ("/other/vmlinuz", "/boot/kernel", "/boot/vmlinuz-directory/file",
                        "/vmlinuz-directory/file", "/boot/../vmlinuz", "/boot/vmlinuz;reboot", "")),
            ("initrd", ("/other/initrd", "/boot/ramdisk", "/boot/initrd-directory/file",
                        "/initrd-directory/file", "/boot/../initrd", "/boot/initrd;reboot", None)),
        ):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_custom_entries([linux_entry(**{field: value})])

    def test_unsafe_linux_options_refused(self):
        for options in ("$(reboot)", 'root="/dev/sda1"', "root=/dev/sda1;reboot", "ro\nreboot",
                        "ro\\ quiet", "ro`reboot`", None, "a" * 2049):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_custom_entries([linux_entry(options=options)])

    def test_disabled_entry_still_validated(self):
        with self.assertRaises(ValueError):
            validate_custom_entries([efi_entry(enabled=False, loader="$(reboot)")])

    def test_compile_payload(self):
        entries = [efi_entry(), linux_entry(), efi_entry(id="uoo-" + "3" * 32, title="Disabled", enabled=False)]
        payload = compile_custom_entries(entries)
        parsed = parse_entries(payload)
        self.assertEqual([entry.title for entry in parsed], ["Windows Boot Manager", "Ubuntu Linux"])
        self.assertEqual([entry.selector for entry in parsed], [entries[0]["id"], entries[1]["id"]])
        self.assertIn("search --no-floppy --fs-uuid --set=root 'ABCD-1234'", payload)
        self.assertIn("chainloader '/EFI/Microsoft/Boot/bootmgfw.efi'", payload)
        self.assertIn("linux '/boot/vmlinuz-6.8.0-generic' root=UUID=", payload)
        self.assertIn("initrd '/boot/initrd.img-6.8.0-generic'", payload)
        self.assertNotIn("Disabled", payload)
        self.assertEqual(compile_custom_entries(copy.deepcopy(entries)), payload)

    def test_compile_omits_optional_ram_disk_and_options(self):
        payload = compile_custom_entries([linux_entry(initrd="", options="")])
        self.assertIn("    linux '/boot/vmlinuz-6.8.0-generic'\n", payload)
        self.assertNotIn("    initrd ", payload)

    def test_compile_empty(self):
        self.assertEqual(compile_custom_entries([]), "# Managed by Uoo Boot Manager.\n")

    def test_compile_revalidates_input(self):
        for entry in (efi_entry(title="bad'; reboot"), linux_entry(options="ro; reboot")):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                compile_custom_entries([entry])

    def test_payload_survives_quoted_shell_heredoc(self):
        payload = compile_custom_entries([efi_entry(title="Ubuntu — 安全 { }"), linux_entry()])
        script = "#!/bin/sh\ncat <<'UOO_CONFIG_END'\n" + payload + "UOO_CONFIG_END\n"
        process = subprocess.run(["sh"], input=script, text=True, capture_output=True, check=True)
        self.assertEqual(process.stdout, payload)

    @unittest.skipUnless(shutil.which("grub-script-check"), "GNU grub-script-check is not installed")
    def test_compiled_payload_with_gnu_grub_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "grub.cfg"
            config.write_text(compile_custom_entries([efi_entry(title="Windows — EFI {test}"), linux_entry(),
                                                      linux_entry(id="uoo-" + "3" * 32, kernel="/vmlinuz-6.8", initrd="/initrd.img-6.8")]),
                              encoding="utf-8")
            process = subprocess.run([shutil.which("grub-script-check"), str(config)],
                                     text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)

    @unittest.skipUnless(shutil.which("grub-script-check"), "GNU grub-script-check is not installed")
    def test_duplicate_grub_ids_use_numeric_paths_in_valid_grub_source(self):
        config = """
set menuentry_id_option=--id
menuentry 'First' $menuentry_id_option duplicate {
    true
}
menuentry 'Second' --id duplicate {
    true
}
submenu 'Tools A' --id tools {
    menuentry 'A' --id child-a {
        true
    }
}
submenu 'Tools B' --id tools {
    menuentry 'B' --id child-b {
        true
    }
}
"""
        self.assertEqual([entry.selector for entry in parse_entries(config)], ["0", "1", "2>0", "3>0"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grub.cfg"
            path.write_text(config, encoding="utf-8")
            process = subprocess.run([shutil.which("grub-script-check"), str(path)], text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)


class PngModelTests(unittest.TestCase):
    @staticmethod
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    def png(self, raw=b"\0\x00\x00\x00\xff", width=1, height=1, depth=8, color=6, interlace=0):
        return (b"\x89PNG\r\n\x1a\n" + self.chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
                + self.chunk(b"IDAT", zlib.compress(raw)) + self.chunk(b"IEND", b""))

    def test_valid_rgba_and_adam7_image(self):
        self.assertEqual(validate_png(self.png()), (1, 1))
        self.assertEqual(validate_png(self.png(interlace=1)), (1, 1))
        self.assertEqual(validate_png(self.png(raw=(b"\0" + b"\0\0\0\xff" * 2) * 3, width=2, height=3)), (2, 3))

    def test_signature_type_size_and_dimension_limits(self):
        for data in (None, b"", bytearray(self.png()), b"not a PNG", self.png() + b"a" * (2 * 1024 * 1024),
                     self.png(width=0), self.png(width=4097), self.png(height=4097), self.png(depth=7), self.png(color=1)):
            with self.subTest(data_type=type(data)), self.assertRaises(ValueError):
                validate_png(data)

    def test_truncated_frames_and_crc(self):
        image = self.png()
        corrupt = bytearray(image)
        corrupt[29] ^= 1
        for data in (image[:12], image[:-1], image[:-12], bytes(corrupt), image + b"trailing"):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                validate_png(data)

    def test_duplicate_header_and_unknown_critical_chunks(self):
        image = self.png()
        header = image[8:33]
        for data in (image[:33] + header + image[33:],
                     image[:33] + self.chunk(b"ABCD", b"") + image[33:],
                     image[:33] + self.chunk(b"abct", b"") + image[33:]):
            with self.assertRaises(ValueError):
                validate_png(data)

    def test_bad_compressed_data_and_filter_rejected(self):
        header = self.png()[:33]
        for data in (self.png(raw=b"\x05\0\0\0\xff"), self.png(raw=b"\0\0"),
                     self.png(raw=b"\0" * 100000),
                     header + self.chunk(b"IDAT", b"not zlib") + self.chunk(b"IEND", b""),
                     header + self.chunk(b"IDAT", zlib.compress(b"\0\0\0\0\xff") + b"extra") + self.chunk(b"IEND", b"")):
            with self.assertRaises(ValueError):
                validate_png(data)

    def test_multiple_contiguous_image_chunks_accepted(self):
        data = zlib.compress(b"\0\0\0\0\xff")
        image = self.png()[:33] + self.chunk(b"IDAT", data[:3]) + self.chunk(b"IDAT", data[3:]) + self.chunk(b"IEND", b"")
        self.assertEqual(validate_png(image), (1, 1))
        interrupted = self.png()[:33] + self.chunk(b"IDAT", data[:3]) + self.chunk(b"tEXt", b"key\0value") + self.chunk(b"IDAT", data[3:]) + self.chunk(b"IEND", b"")
        with self.assertRaises(ValueError):
            validate_png(interrupted)

    def test_palette_is_required_and_bounded(self):
        missing = self.png(raw=b"\0\0", depth=1, color=3)
        with self.assertRaises(ValueError):
            validate_png(missing)
        valid = missing[:33] + self.chunk(b"PLTE", b"\0\0\0\xff\xff\xff") + missing[33:]
        self.assertEqual(validate_png(valid), (1, 1))
        excess = missing[:33] + self.chunk(b"PLTE", b"\0" * 9) + missing[33:]
        with self.assertRaises(ValueError):
            validate_png(excess)


if __name__ == "__main__":
    unittest.main()
