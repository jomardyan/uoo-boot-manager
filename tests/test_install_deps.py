"""Dependency selection, bootstrap integrity, and non-mutating previews."""

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_deps", ROOT / "scripts/install-deps.py")
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)


class DependencySelectionTests(unittest.TestCase):
    def test_every_host_supported_operation_resolves(self):
        for target in deps.TARGETS:
            manager = "pacman" if target == "arch" else "apt"
            with self.subTest(target=target):
                groups = deps.resolve_targets([target], manager)
                self.assertIn("base", groups)
                self.assertFalse(groups - deps.GROUPS.keys())

    def test_prerequisites_follow_build_graph(self):
        for target in ("rpm", "rpm-opensuse", "vm-test", "check"):
            with self.subTest(target=target):
                self.assertIn("deb", deps.resolve_targets([target], "apt"))
        self.assertTrue({"gtk", "appimage", "snap"} <= deps.resolve_targets(["snap"], "apt"))
        self.assertNotIn("powershell", deps.resolve_targets(["windows-test"], "apt"))

    def test_everything_omits_unavailable_native_builds(self):
        ubuntu = deps.resolve_targets(["everything"], "apt")
        arch = deps.resolve_targets(["everything"], "pacman")
        self.assertTrue({"snap", "windows", "powershell", "vm", "rpm", "flatpak", "appimage"} <= ubuntu)
        self.assertNotIn("arch", ubuntu)
        self.assertIn("arch", arch)
        self.assertNotIn("snap", arch)

    def test_explicit_unsupported_requests_fail_before_installation(self):
        for targets, manager in [(["arch"], "apt"), (["everything", "arch"], "apt"), (["snap"], "pacman"), (["oops"], "apt")]:
            with self.subTest(targets=targets, manager=manager), self.assertRaises(ValueError):
                deps.resolve_targets(targets, manager)

    def test_packages_honors_selected_formats_and_validates_them(self):
        self.assertEqual(deps.resolve_targets(["packages"], "apt", ["deb", "rpm"]), {"base", "deb", "rpm"})
        for formats in ([], ["deb", "oops"], ["arch"]):
            with self.subTest(formats=formats), self.assertRaises(ValueError):
                deps.resolve_targets(["packages"], "apt", formats)

    def test_distro_detection(self):
        for info, expected in [({"ID": "ubuntu"}, "apt"), ({"ID": "mint", "ID_LIKE": "ubuntu debian"}, "apt"), ({"ID": "manjaro", "ID_LIKE": "arch"}, "pacman")]:
            with self.subTest(info=info), patch.object(deps.platform, "system", return_value="Linux"), patch.object(deps.platform, "freedesktop_os_release", return_value=info):
                self.assertEqual(deps.package_manager(), expected)
        with patch.object(deps.platform, "system", return_value="Windows"), self.assertRaisesRegex(ValueError, "Windows"):
            deps.package_manager()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_only_missing_system_packages_are_installed(self):
        installer = deps.Installer("apt")
        with patch.object(installer, "installed", side_effect=lambda package: package != "rpm"), patch.object(installer, "run") as run:
            installer.system_packages({"base", "deb", "rpm"})
        self.assertEqual(run.call_args_list[0].args, ("sudo", "apt-get", "update"))
        self.assertEqual(run.call_args_list[1].args, ("sudo", "apt-get", "install", "-y", "--no-install-recommends", "rpm"))

    def test_existing_packages_do_not_trigger_package_manager_mutations(self):
        installer = deps.Installer("apt")
        with patch.object(installer, "installed", return_value=True), patch.object(installer, "run") as run:
            installer.system_packages({"base", "deb"})
        run.assert_not_called()

    def test_package_install_failure_stops_following_commands(self):
        installer = deps.Installer("apt")
        with patch.object(installer, "installed", return_value=False), patch.object(installer, "run", side_effect=subprocess.CalledProcessError(1, "apt-get")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                installer.system_packages({"deb"})
        self.assertEqual(run.call_count, 1)

    def test_arch_installs_with_needed_without_partial_database_refresh(self):
        installer = deps.Installer("pacman")
        with patch.object(installer, "installed", return_value=False), patch.object(installer, "run") as run:
            installer.system_packages({"arch"})
        self.assertEqual(run.call_args.args, ("sudo", "pacman", "-S", "--needed", "--noconfirm", "base-devel", "zstd"))

    def test_dpkg_removed_packages_are_not_mistaken_for_installed(self):
        installer = deps.Installer("apt")
        for status, expected in [("install ok installed", True), ("deinstall ok config-files", False)]:
            with self.subTest(status=status), patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=status)):
                self.assertEqual(installer.installed("example"), expected)

    def test_preview_does_not_download_write_or_execute_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = deps.Installer("apt", dry_run=True)
            installer.arch = "x86_64"
            installer.bin = root / "bin"
            with patch.object(deps, "urlopen") as network, patch.object(deps.subprocess, "run") as run, patch.object(deps, "ROOT", ROOT):
                installer.download("https://example.invalid/file", root / "download", "0" * 64)
                installer.link(root / "tool", "tool")
                installer.appimage()
                installer.flatpak()
                installer.powershell()
            network.assert_not_called()
            # Executable detection may probe an existing pwsh, but preview must
            # never start installers, package managers, pip, or Flatpak.
            for call in run.call_args_list:
                self.assertIn("-NonInteractive", call.args[0])
            self.assertEqual(list(root.iterdir()), [])

    def test_bad_download_digest_preserves_original_and_cleans_temporary_file(self):
        installer = deps.Installer("apt")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tool"
            destination.write_bytes(b"old")
            with patch.object(deps, "urlopen", return_value=io.BytesIO(b"bad")), self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                installer.download("https://example.invalid/tool", destination, hashlib.sha256(b"good").hexdigest())
            self.assertEqual(destination.read_bytes(), b"old")
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_verified_cached_download_is_reused_without_network(self):
        installer = deps.Installer("apt")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tool"
            destination.write_bytes(b"verified")
            with patch.object(deps, "urlopen") as network:
                installer.download("https://example.invalid/tool", destination, hashlib.sha256(b"verified").hexdigest())
            network.assert_not_called()

    def test_launcher_is_idempotent_and_preserves_conflicting_files(self):
        installer = deps.Installer("apt")
        with tempfile.TemporaryDirectory() as directory:
            installer.bin = Path(directory) / "bin"
            executable = Path(directory) / "tool"
            executable.touch()
            installer.link(executable, "tool")
            installer.link(executable, "tool")
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                installer.link(Path(directory) / "other", "tool")
            self.assertEqual((installer.bin / "tool").resolve(), executable)

    def test_existing_dotnet_10_is_reused(self):
        installer = deps.Installer("apt")
        with patch.object(deps.shutil, "which", return_value="/existing/dotnet"), patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="10.0.402 [/existing/sdk]\n")), patch.object(installer, "download") as download, patch.object(installer, "run") as run:
            installer.dotnet()
        download.assert_not_called()
        run.assert_not_called()

    def test_old_sdk_does_not_satisfy_dotnet_10_requirement(self):
        installer = deps.Installer("apt", dry_run=True)
        with patch.object(deps.shutil, "which", return_value="/existing/dotnet"), patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="8.0.100 [/existing/sdk]\n")), patch.object(installer, "download") as download, patch.object(installer, "run") as run, patch.object(installer, "link"):
            installer.dotnet()
        download.assert_called_once()
        self.assertIn("10.0", run.call_args_list[0].args)

    def test_invalid_selection_has_no_installation_side_effects(self):
        with patch.object(deps, "package_manager", return_value="apt"), patch.object(deps, "installation_lock") as lock, patch.object(deps.Installer, "system_packages") as install, redirect_stderr(io.StringIO()):
            self.assertEqual(deps.main(["arch"]), 1)
        lock.assert_not_called()
        install.assert_not_called()


@unittest.skipUnless(shutil.which("make") and shutil.which("bash"), "GNU Make and Bash are required")
class MakeDependencyTargetTests(unittest.TestCase):
    def test_make_routes_dependency_targets_and_options(self):
        # Replace the installer with a recorder in an isolated checkout. No
        # package managers or network tools run during this integration test.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "Makefile", root / "Makefile")
            (root / "scripts").mkdir()
            (root / "scripts/install-deps.py").write_text("import json, sys\nprint('ARGV=' + json.dumps(sys.argv[1:]))\n")
            for target, expected in [("deps", ["everything"]), ("deps-all", ["everything"]), ("install-deps", ["everything"]), ("deps-windows", ["windows"]), ("deps-check-package", ["check-package"]), ("deps-packages", ["packages"])]:
                with self.subTest(target=target):
                    result = subprocess.run(["make", "--no-print-directory", target, f"PYTHON={sys.executable}", "DEPS_ARGS=--dry-run", "PACKAGE_FORMATS=deb rpm"], cwd=root, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    line = next(line for line in result.stdout.splitlines() if line.startswith("ARGV="))
                    self.assertEqual(json.loads(line[5:]), ["--package-formats", "deb rpm", "--dry-run", *expected])


if __name__ == "__main__":
    unittest.main()
