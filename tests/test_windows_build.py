"""Cross-build defaults and installer preflight, without publishing real code."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL and os.name == "posix", "Cross-build tests require PowerShell 7 on a non-Windows host")
class WindowsBuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "windows/scripts").mkdir(parents=True)
        (self.root / "windows/Uoo.Desktop").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "bin").mkdir()
        for name in ("windows/scripts/build.ps1", "windows/Directory.Build.props", "windows/Uoo.Desktop/uoo.png", "Makefile"):
            shutil.copyfile(ROOT / name, self.root / name)
        (self.root / "LICENSE").write_text("Test license\n")
        (self.root / "docs/WINDOWS.md").write_text('<img src="../assets/icons/png/uoo-64.png" alt="Uoo">\nTest Windows build guide\n')
        # Replace dotnet with a publisher stub. PowerShell still performs the
        # real staging, archive construction, and checksum generation.
        fake = self.root / "bin/dotnet"
        fake.write_text("""#!/usr/bin/env python3
from pathlib import Path
import sys
output = Path(sys.argv[sys.argv.index('--output') + 1])
output.mkdir(parents=True, exist_ok=True)
for name in ('Uoo.BootManager.exe', 'Uoo.BootHelper.exe', 'Uoo.Core.dll',
             'Uoo.Platform.dll', 'System.Management.dll', 'coreclr.dll'):
    (output / name).write_bytes(b'Test publisher payload')
with (Path(__file__).resolve().parents[1] / 'publish.log').open('a') as log:
    log.write('publish\\n')
""")
        fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.root / "bin") + os.pathsep + self.env.get("PATH", "")
        self.env["NO_COLOR"] = "1"
        self.env.pop("ProgramFiles", None)
        self.env.pop("WINDOWS_RUNTIME", None)
        self.env.pop("WINDOWS_BUILD_ARGS", None)

    def run_build(self, *args):
        return subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                               str(self.root / "windows/scripts/build.ps1"), *args],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

    def verify_zip(self, cpu):
        version = next(line.split("<Version>")[1].split("</Version>")[0]
                       for line in (self.root / "windows/Directory.Build.props").read_text().splitlines() if "<Version>" in line)
        output = self.root / "dist/windows"
        archive = output / f"Uoo-Boot-Manager-{version}-windows-{cpu}.zip"
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as payload:
            self.assertIsNone(payload.testzip())
            self.assertTrue({"Uoo.BootManager.exe", "Uoo.BootHelper.exe", "LICENSE", "README-Windows.md"} <= set(payload.namelist()))
            self.assertIn('src="uoo.png"', payload.read("README-Windows.md").decode())
            self.assertEqual(payload.read("uoo.png"), (ROOT / "windows/Uoo.Desktop/uoo.png").read_bytes())
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertIn(f"{checksum}  {archive.name}", (output / "SHA256SUMS").read_text())
        self.assertFalse(list(output.glob("*.exe")))
        self.assertEqual((self.root / "publish.log").read_text().splitlines(), ["publish", "publish"])

    @unittest.skipUnless(shutil.which("make") and shutil.which("bash"), "Make and Bash are required")
    def test_plain_make_windows_builds_zip_without_program_files(self):
        result = subprocess.run(["make", "--no-print-directory", "windows", f"POWERSHELL={POWERSHELL}"],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Non-Windows host", result.stdout)
        self.assertIn("[OK] windows", result.stdout)
        self.verify_zip("x64")

    def test_explicit_skip_and_arm64_still_work(self):
        result = self.run_build("-Runtime", "win-arm64", "-SkipInstaller")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.verify_zip("arm64")

    def test_explicit_installer_request_fails_before_touching_outputs(self):
        marker = self.root / "build/windows/win-x64/keep.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("Keep this build")
        result = self.run_build("-SkipInstaller:$false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a Windows host", result.stderr)
        self.assertNotIn("Cannot bind argument", result.stderr)
        self.assertEqual(marker.read_text(), "Keep this build")
        self.assertFalse((self.root / "publish.log").exists())
        self.assertFalse((self.root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
