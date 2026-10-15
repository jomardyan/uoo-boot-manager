"""Pure parsers and generated shell configuration must never execute input."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from uoo.core import (DEMO_CONFIG, Entry, override_text, parse_entries,
                      parse_firmware, parse_settings, structural_braces)


class GrubParserTests(unittest.TestCase):
    def test_demo_menu(self):
        entries = parse_entries(DEMO_CONFIG)
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0], Entry("Ubuntu", "ubuntu", "0", ""))
        self.assertEqual(entries[1].selector, "advanced>linux-6.8")
        self.assertEqual(entries[1].position, "1>0")
        self.assertEqual(entries[1].group, "Advanced options for Ubuntu")
        self.assertEqual(entries[-1].position, "3")

    def test_generated_id_variable(self):
        config = """
menuentry 'Ubuntu' --class ubuntu --class gnu-linux $menuentry_id_option 'gnulinux-simple-abc' {
}
submenu 'Advanced' ${menuentry_id_option} 'gnulinux-advanced-abc' {
    menuentry 'Recovery' --class ubuntu $menuentry_id_option 'gnulinux-6.8-recovery-abc' {
    }
}
"""
        entries = parse_entries(config)
        self.assertEqual(entries[0].selector, "gnulinux-simple-abc")
        self.assertEqual(entries[1].selector, "gnulinux-advanced-abc>gnulinux-6.8-recovery-abc")

    def test_multiple_nested_submenus(self):
        config = """
function helper {
    if x; then
        echo 'unrelated { and }'
    fi
}
menuentry 'First' --id=first { }
submenu 'Tools' --id tools {
    menuentry 'First tool' --id tool { }
    submenu 'Nested' --id nested {
        menuentry 'Rescue' --id rescue { }
    }
    menuentry 'Last tool' --id last-tool { }
}
menuentry 'Last' --id last { }
"""
        entries = parse_entries(config)
        self.assertEqual([entry.position for entry in entries], ["0", "1>0", "1>1>0", "1>2", "2"])
        self.assertEqual(entries[2], Entry("Rescue", "tools>nested>rescue", "1>1>0", "Tools / Nested"))
        self.assertEqual(entries[-1].group, "")

    def test_numeric_path_if_any_id_missing(self):
        entries = parse_entries("""
menuentry 'Top' { }
submenu 'Without ID' {
    submenu 'With ID' --id nested {
        menuentry 'Leaf' --id leaf { }
    }
}
""")
        self.assertEqual([entry.selector for entry in entries], ["0", "1>0>0"])

    def test_numeric_path_for_dynamic_or_separator_id(self):
        for entry_id in ("a>b", "${dynamic}", "$dynamic", "", "17", 'a"b', "a\\b", "a`b", "a;b", "ubuntu#1"):
            with self.subTest(entry_id=entry_id):
                entries = parse_entries("menuentry 'Title' --id '" + entry_id + "' { }")
                self.assertEqual(entries[0].selector, "0")

    def test_duplicate_ids_use_numeric_paths(self):
        entries = parse_entries("""
menuentry 'First' --id duplicate { }
menuentry 'Second' --id duplicate { }
submenu 'A' --id submenu {
    menuentry 'Child A' --id child { }
}
submenu 'B' --id submenu {
    menuentry 'Child B' --id child { }
}
""")
        self.assertEqual([entry.selector for entry in entries], ["0", "1", "2>0", "3>0"])

    def test_duplicate_submenu_id_with_distinct_children_uses_numeric_paths(self):
        entries = parse_entries("""
submenu 'A' --id submenu {
    menuentry 'Child A' --id child-a { }
}
submenu 'B' --id submenu {
    menuentry 'Child B' --id child-b { }
}
""")
        self.assertEqual([entry.selector for entry in entries], ["0>0", "1>0"])

    def test_same_child_id_in_distinct_submenus_is_valid(self):
        entries = parse_entries("""
submenu 'A' --id a {
    menuentry 'Child A' --id child { }
}
submenu 'B' --id b {
    menuentry 'Child B' --id child { }
}
""")
        self.assertEqual([entry.selector for entry in entries], ["a>child", "b>child"])

    def test_multiline_header_and_comments(self):
        entries = parse_entries("""
menuentry
    'Ubuntu { recovery }' # a header comment with a fake {
    --class ubuntu
    --id recovery
    {
        echo 'literal }'
    }
""")
        self.assertEqual(entries, [Entry("Ubuntu { recovery }", "recovery", "0")])

    def test_backslash_continuation_header(self):
        config = "menuentry \\\n 'Ubuntu' \\\n --id ubuntu \\\n {\n}\n"
        self.assertEqual(parse_entries(config), [Entry("Ubuntu", "ubuntu", "0")])

    def test_multiline_quoted_title(self):
        config = "menuentry 'Ubuntu\nmenuentry fake {\nrecovery }' --id real {\n}\n"
        entries = parse_entries(config)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Ubuntu\nmenuentry fake {\nrecovery }")
        self.assertEqual(entries[0].selector, "real")

    def test_quotes_escapes_and_hash_inside_word(self):
        entries = parse_entries('menuentry "Ubuntu \\"LTS\\"" --id ubuntu#1 {\n echo \\{ \\}\n}\n')
        self.assertEqual(entries[0].title, 'Ubuntu "LTS"')
        self.assertEqual(entries[0].selector, "0")

    def test_nested_variables_are_not_blocks(self):
        config = """
set default=${one:-${two:-'a { value }'}}
menuentry "${OS:-\"Ubuntu\"}" --id ubuntu {
    set path=${prefix:-${fallback}}
    echo "${thing:-\"a } value\"}"
}
menuentry 'Other' --id other { }
"""
        entries = parse_entries(config)
        self.assertEqual([entry.selector for entry in entries], ["ubuntu", "other"])
        self.assertEqual([entry.position for entry in entries], ["0", "1"])

    def test_comments_and_quoted_menuentry_lines_ignored(self):
        entries = parse_entries("""
# menuentry 'Fake' { }
echo '
menuentry fake { }
'
menuentry 'Real' --id real { }
""")
        self.assertEqual(entries, [Entry("Real", "real", "0")])

    def test_structural_braces(self):
        braces, quote = structural_braces("echo ${x:-${y}} '\\{ }' \\{ { } # { fake")
        self.assertEqual([char for _, char in braces], ["{", "}"])
        self.assertIsNone(quote)

    def test_structural_quotes_span_calls(self):
        braces, quote = structural_braces("echo 'first {")
        self.assertEqual(braces, [])
        self.assertEqual(quote, "'")
        braces, quote = structural_braces("second }' {", quote)
        self.assertEqual([char for _, char in braces], ["{"])
        self.assertIsNone(quote)

    def test_malformed_menu_rejected(self):
        malformed = ("menuentry 'Ubuntu'", "menuentry 'Ubuntu' {",
                     "menuentry 'Ubuntu' }", "menuentry '' { }", "}",
                     "menuentry 'Unclosed { }", "set x=${unfinished",
                     "menuentry 'First'\nmenuentry 'Second' { }")
        for config in malformed:
            with self.subTest(config=config), self.assertRaises(ValueError):
                parse_entries(config)

    def test_no_entries_is_valid(self):
        self.assertEqual(parse_entries("# no generated entries\nset default=0\n"), [])


class GrubSettingsTests(unittest.TestCase):
    def test_literal_settings(self):
        settings = parse_settings("""
# GRUB_DEFAULT=other
GRUB_DEFAULT='advanced>linux'
export GRUB_TIMEOUT="5" # comment
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR='Ubuntu # Linux'
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_EMPTY=
GRUB_EMPTY_QUOTED=''
OTHER=value
""")
        self.assertEqual(settings["GRUB_DEFAULT"], "advanced>linux")
        self.assertEqual(settings["GRUB_TIMEOUT"], "5")
        self.assertEqual(settings["GRUB_TIMEOUT_STYLE"], "menu")
        self.assertEqual(settings["GRUB_DISTRIBUTOR"], "Ubuntu # Linux")
        self.assertEqual(settings["GRUB_CMDLINE_LINUX_DEFAULT"], "quiet splash")
        self.assertEqual(settings["GRUB_EMPTY"], "")
        self.assertEqual(settings["GRUB_EMPTY_QUOTED"], "")
        self.assertNotIn("OTHER", settings)

    def test_last_assignment_wins(self):
        self.assertEqual(parse_settings("GRUB_TIMEOUT=5\nGRUB_TIMEOUT=10\n"), {"GRUB_TIMEOUT": "10"})

    def test_shell_expression_remains_literal(self):
        settings = parse_settings('GRUB_DEFAULT="$(touch /tmp/uoo-must-not-run)"\nGRUB_DISTRIBUTOR="${NAME}"\n')
        self.assertEqual(settings["GRUB_DEFAULT"], "$(touch /tmp/uoo-must-not-run)")
        self.assertEqual(settings["GRUB_DISTRIBUTOR"], "${NAME}")

    def test_malformed_and_command_lines_ignored(self):
        settings = parse_settings("""
GRUB_TIMEOUT='unclosed
GRUB_DEFAULT=0; touch /tmp/no
GRUB_TIMEOUT_STYLE=hidden && echo no
GRUB_CMDLINE_LINUX=two words
GRUB_TIMEOUT=5
""")
        self.assertEqual(settings, {"GRUB_TIMEOUT": "5"})

    def test_quoted_operators_are_literal(self):
        self.assertEqual(parse_settings("GRUB_DEFAULT='semi; quote| amp& redirect>'\n"),
                         {"GRUB_DEFAULT": "semi; quote| amp& redirect>"})

    def test_embedded_hash_not_a_comment(self):
        self.assertEqual(parse_settings("GRUB_DEFAULT=ubuntu#1 # a comment\n"), {"GRUB_DEFAULT": "ubuntu#1"})


class FirmwareParserTests(unittest.TestCase):
    def test_verbose_firmware(self):
        result = parse_firmware("""
BootCurrent: 000a
Timeout: 1 seconds
BootNext: 0001
BootOrder: 000a,0001,FFFF
Boot000a* Ubuntu\tHD(1,GPT,abc)/File(\\EFI\\ubuntu\\shimx64.efi)
Boot0001  Windows Boot Manager\tHD(1,GPT,def)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)
BootFFFF* USB Device
""")
        self.assertEqual(result, {
            "entries": [{"id": "000A", "label": "Ubuntu", "active": True},
                        {"id": "0001", "label": "Windows Boot Manager", "active": False},
                        {"id": "FFFF", "label": "USB Device", "active": True}],
            "order": ["000A", "0001", "FFFF"], "current": "000A", "next": "0001",
            "available": True, "error": "",
        })

    def test_missing_optional_fields(self):
        result = parse_firmware("Boot0001* Ubuntu\n")
        self.assertEqual(result["order"], [])
        self.assertEqual(result["current"], "")
        self.assertEqual(result["next"], "")

    def test_empty_order(self):
        self.assertEqual(parse_firmware("BootOrder:\n")["order"], [])

    def test_order_preserved_and_whitespace_allowed(self):
        result = parse_firmware("BootOrder: 0002, 0001\nBoot0001* Ubuntu\nBoot0002* Windows\n")
        self.assertEqual(result["order"], ["0002", "0001"])
        self.assertEqual([entry["id"] for entry in result["entries"]], ["0001", "0002"])

    def test_duplicate_order_ids_rejected(self):
        with self.assertRaises(ValueError):
            parse_firmware("BootOrder: 000a,000A\nBoot000a* Ubuntu\n")

    def test_malformed_order_rejected(self):
        for order in ("1", "000G", "0001,", ",0001", "0001,,0002", "0001 0002"):
            with self.subTest(order=order), self.assertRaises(ValueError):
                parse_firmware("BootOrder: " + order + "\nBoot0001* Ubuntu\nBoot0002* Windows\n")
        for line in ("BootOrder 0001", "BootOrder=0001", "BootOrderBroken: 0001"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_firmware(line + "\nBoot0001* Ubuntu\n")

    def test_missing_order_entry_rejected(self):
        with self.assertRaises(ValueError):
            parse_firmware("BootOrder: 0001,0002\nBoot0001* Ubuntu\n")

    def test_duplicate_metadata_rejected(self):
        for key, value in (("BootOrder", "0001"), ("BootCurrent", "0001"), ("BootNext", "0001")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_firmware(f"{key}: {value}\n{key}: {value}\nBoot0001* Ubuntu\n")

    def test_malformed_current_and_next_rejected(self):
        for key in ("BootCurrent", "BootNext"):
            for value in ("", "1", "ZZZZ", "0001,0002"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    parse_firmware(f"{key}: {value}\nBoot0001* Ubuntu\n")

    def test_duplicate_entry_rejected(self):
        with self.assertRaises(ValueError):
            parse_firmware("Boot000A* Ubuntu\nBoot000a* Other\n")

    def test_malformed_entry_rejected(self):
        for entry in ("Boot0001*", "Boot0001", "Boot001* Ubuntu", "Boot00011* Ubuntu", "Boot0001? Ubuntu", "BootGGGG* Ubuntu"):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                parse_firmware(entry)

    def test_empty_or_error_output_rejected(self):
        for text in ("", "\n", "EFI variables are not supported on this system.", "Permission denied"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_firmware(text)


class OverrideTests(unittest.TestCase):
    def test_valid_ranges_and_styles(self):
        for timeout in (-1, 0, 1, 300):
            for style in ("menu", "hidden", "countdown"):
                with self.subTest(timeout=timeout, style=style):
                    text = override_text("advanced>ubuntu", timeout, style)
                    settings = parse_settings(text)
                    self.assertEqual(settings["GRUB_DEFAULT"], "advanced>ubuntu")
                    self.assertEqual(settings["GRUB_TIMEOUT"], str(timeout))
                    self.assertEqual(settings["GRUB_TIMEOUT_STYLE"], style)
                    self.assertEqual(settings["GRUB_SAVEDEFAULT"], "false")

    def test_invalid_timeout(self):
        for timeout in (-2, 301, True, False, 5.0, "5", None):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                override_text("ubuntu", timeout, "menu")

    def test_invalid_styles(self):
        for style in ("", "MENU", "menu;touch /tmp/no", None, 1):
            with self.subTest(style=style), self.assertRaises(ValueError):
                override_text("ubuntu", 5, style)

    def test_invalid_selectors(self):
        for selector in ("", None, 1, "a" * 2049, "ubuntu\nother", "ubuntu\tother", "a\x00b", "a\x1fb"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                override_text(selector, 5, "menu")

    def test_malicious_selector_is_shell_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "must-not-exist"
            selector = f"$(touch {sentinel}); 'quoted' `touch {sentinel}` $HOME \\ "
            override = Path(directory) / "override.cfg"
            override.write_text(override_text(selector, 5, "menu"), encoding="utf-8")
            process = subprocess.run(
                ["sh", "-c", '. "$1"; printf "%s\\n%s\\n%s\\n%s\\n" "$GRUB_DEFAULT" "$GRUB_TIMEOUT" "$GRUB_TIMEOUT_STYLE" "$GRUB_SAVEDEFAULT"',
                 "uoo-test", str(override)],
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(process.stdout.splitlines(), [selector, "5", "menu", "false"])
            self.assertFalse(sentinel.exists())
            self.assertEqual(parse_settings(override.read_text())["GRUB_DEFAULT"], selector)


if __name__ == "__main__":
    unittest.main()
