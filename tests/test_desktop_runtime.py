"""Sandbox host transport and the read-only inspector's contracts."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from uoo import __version__
from uoo import desktop_runtime as runtime

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("inspector", SOURCE / "inspector.py")
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.old = runtime._host_installed
        runtime._host_installed = None
        self.addCleanup(setattr, runtime, "_host_installed", self.old)

    def response(self, **extra):
        payload = {"ok": True, "version": __version__, "installed": True,
                   "snapshot": {"entries": [], "capabilities": {"grub": False}}, **extra}
        return Mock(returncode=0, stdout=json.dumps(payload))

    def test_native_commands_do_not_spawn_a_host_shell(self):
        with patch.object(runtime, "is_flatpak", return_value=False):
            self.assertEqual(runtime.host_command(["/usr/bin/pkexec", "helper", "--stdin"]),
                             ["/usr/bin/pkexec", "helper", "--stdin"])

    def test_flatpak_uses_fixed_host_executables_without_shell_interpolation(self):
        with patch.object(runtime, "is_flatpak", return_value=True), patch.object(runtime.subprocess, "run", return_value=self.response()) as run:
            self.assertEqual(runtime.host_snapshot()["entries"], [])
            self.assertEqual(run.call_args.args[0], ["/usr/bin/flatpak-spawn", "--host", "/usr/bin/uoo-boot-inspect"])
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertTrue(runtime.host_helper_available())
            self.assertEqual(run.call_count, 1)

    def test_missing_host_package_is_a_recoverable_prerequisite(self):
        with patch.object(runtime.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(runtime.host_helper_available())
            with self.assertRaisesRegex(RuntimeError, "matching native Uoo package"):
                runtime.host_snapshot()

    def test_probe_rejects_version_mismatch_malformed_and_untrusted_responses(self):
        for response in (self.response(version="0.0.0"), self.response(installed="yes"),
                         Mock(returncode=1, stdout="{}"), Mock(returncode=0, stdout="[]")):
            with self.subTest(response=response), patch.object(runtime.subprocess, "run", return_value=response):
                with self.assertRaises(RuntimeError):
                    runtime._inspect(probe=True)
                self.assertFalse(runtime._host_installed)

    def test_invalid_snapshot_and_timeout_do_not_enable_privileged_controls(self):
        with patch.object(runtime.subprocess, "run", return_value=self.response(snapshot=[])):
            with self.assertRaisesRegex(RuntimeError, "invalid boot snapshot"):
                runtime.host_snapshot()
        with patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("inspector", 30)):
            with self.assertRaises(RuntimeError):
                runtime.host_snapshot()


class InspectorTests(unittest.TestCase):
    def invoke(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            status = inspector.main(argv)
        return status, json.loads(output.getvalue())

    def test_probe_reads_availability_without_inspecting_disks(self):
        with patch("uoo.desktop_runtime.native_helper_available", return_value=True), patch("uoo.system.snapshot") as snapshot:
            status, response = self.invoke(["--probe"])
        self.assertEqual(status, 0)
        self.assertEqual(response, {"ok": True, "version": __version__, "installed": True})
        snapshot.assert_not_called()

    def test_default_mode_returns_only_a_read_only_snapshot(self):
        with patch("uoo.desktop_runtime.native_helper_available", return_value=False), patch("uoo.system.snapshot", return_value={"entries": []}) as snapshot:
            status, response = self.invoke([])
        self.assertEqual(status, 0)
        self.assertEqual(response["snapshot"], {"entries": []})
        snapshot.assert_called_once_with()

    def test_action_and_path_arguments_are_rejected_before_inspection(self):
        for argv in (["repair"], ["--probe", "/tmp/code.py"], ['{"action":"apply_grub"}']):
            with self.subTest(argv=argv), patch("uoo.system.snapshot") as snapshot:
                status, response = self.invoke(argv)
                self.assertEqual(status, 1)
                self.assertFalse(response["ok"])
                snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
