"""Plain-language explanations for boot-tool failures."""

import os
from pathlib import Path
import stat
import tempfile
import unittest

from uoo.advice import RULES, explain
from uoo.system import run_command


class AdviceTests(unittest.TestCase):
    def test_known_failures_are_explained(self):
        cases = [
            ("efibootmgr", "Could not set variable Boot0003: No space left on device", "variable storage"),
            ("efibootmgr", "efibootmgr: Could not prepare Boot variable: No space left on device", "variable storage"),
            ("efibootmgr", "Read-only file system", "remount"),
            ("efibootmgr", "Could not delete: Operation not permitted", "protected"),
            ("efibootmgr", "efibootmgr: EFI variables are not supported on this system.", "UEFI mode"),
            ("efibootmgr", "Input/output error", "refused"),
            ("grub-install", "cp: write error: No space left on device", "EFI System Partition"),
            ("update-grub", "grub-probe: error: failed to get canonical path of `/cow'.", "live sessions"),
            ("grub-script-check", "/boot/grub/grub.cfg:42:7: syntax error", "syntax error"),
        ]
        for program, details, expected in cases:
            with self.subTest(details=details):
                self.assertIn(expected, explain(program, details))

    def test_unrelated_programs_and_messages_get_no_hint(self):
        self.assertEqual(explain("lsblk", "No space left on device"), "")
        self.assertEqual(explain("efibootmgr", "something unexpected"), "")
        self.assertEqual(explain(None, None), "")

    def test_matching_is_exact_on_the_program_name(self):
        self.assertEqual(explain("efibootmgr-fake", "No space left on device"), "")

    def test_hints_never_suggest_destructive_commands(self):
        for _programs, _pattern, hint in RULES:
            for risky in ("rm ", "dd ", "mkfs", "wipefs", "chattr -i", "ntfsfix", "efivar -d"):
                self.assertNotIn(risky, hint)

    def test_run_command_appends_the_hint_after_the_tool_message(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "efibootmgr"
            tool.write_text("#!/bin/sh\necho 'Could not set variable Boot0003: No space left on device' >&2\nexit 1\n")
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            with self.assertRaises(RuntimeError) as caught:
                run_command([tool])
        message = str(caught.exception)
        self.assertTrue(message.startswith("efibootmgr failed: Could not set variable Boot0003"))
        self.assertIn("What this usually means:", message)
        self.assertIn("variable storage", message)

    def test_run_command_without_a_known_pattern_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "efibootmgr"
            tool.write_text("#!/bin/sh\necho 'odd failure' >&2\nexit 2\n")
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            with self.assertRaisesRegex(RuntimeError, r"^efibootmgr failed: odd failure$"):
                run_command([tool])


if __name__ == "__main__":
    unittest.main()
