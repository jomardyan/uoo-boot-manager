import importlib.util
import io
from pathlib import Path
import tarfile
import unittest
import xml.etree.ElementTree as ET


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_check", SOURCE / "scripts/check-package.py")
package_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_check)


class PackageValidationTests(unittest.TestCase):
    def validate_archive(self, name, mode=0o755, uid=0, data=None, link=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            item = tarfile.TarInfo(name)
            item.mode, item.uid = mode, uid
            payload = (SOURCE / "helper.py").read_bytes() if data is None else data
            if link:
                item.type, item.linkname = tarfile.SYMTYPE, link
                payload = b""
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            package_check.validate_data(SOURCE, archive)

    def test_rejects_user_owned_privileged_helper(self):
        with self.assertRaisesRegex(package_check.PackageError, "not root-owned"):
            self.validate_archive("usr/lib/uoo-boot-manager/helper.py", uid=1000)

    def test_rejects_writable_privileged_helper(self):
        with self.assertRaisesRegex(package_check.PackageError, "Unsafe archive permissions"):
            self.validate_archive("usr/lib/uoo-boot-manager/helper.py", mode=0o777)

    def test_rejects_setuid_privileged_helper(self):
        with self.assertRaisesRegex(package_check.PackageError, "Unsafe archive permissions"):
            self.validate_archive("usr/lib/uoo-boot-manager/helper.py", mode=0o4755)

    def test_rejects_helper_symlink_to_user_writable_code(self):
        with self.assertRaisesRegex(package_check.PackageError, "Only regular files"):
            self.validate_archive("usr/lib/uoo-boot-manager/helper.py", link="/tmp/attacker.py")

    def test_rejects_unreviewed_helper_contents(self):
        with self.assertRaisesRegex(package_check.PackageError, "differs from reviewed source"):
            self.validate_archive("usr/lib/uoo-boot-manager/helper.py", data=b"unreviewed")

    def test_rejects_unexpected_host_boot_configuration(self):
        with self.assertRaisesRegex(package_check.PackageError, "Unexpected or duplicate"):
            self.validate_archive("etc/default/grub.d/zz-uoo.cfg", mode=0o644)

    def test_complete_source_archive_is_accepted(self):
        stream = io.BytesIO()
        mapping = package_check.expected_files(SOURCE)
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, (source, mode) in mapping.items():
                item = tarfile.TarInfo(name)
                item.mode = mode
                data = source.read_bytes()
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            self.assertEqual(package_check.validate_data(SOURCE, archive), len(mapping))

    def test_polkit_requires_fresh_admin_auth_for_fixed_helper(self):
        policy = ET.parse(SOURCE / "packaging/org.uoo.boot-manager.policy").getroot()
        actions = policy.findall("action")
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.attrib["id"], "org.uoo.boot-manager.modify")
        defaults = {item.tag: item.text for item in action.find("defaults")}
        self.assertEqual(defaults, {"allow_any": "auth_admin", "allow_inactive": "auth_admin", "allow_active": "auth_admin"})
        annotations = {item.attrib["key"]: item.text for item in action.findall("annotate")}
        self.assertEqual(annotations, {"org.freedesktop.policykit.exec.path": "/usr/bin/uoo-boot-helper"})

    def test_built_package_matches_source(self):
        packages = sorted((SOURCE / "dist").glob("uoo-boot-manager_*_all.deb"), key=lambda path: path.stat().st_mtime)
        if not packages:
            self.skipTest("Build the Debian package before checking its archive")
        self.assertGreater(package_check.check_package(packages[-1], SOURCE), 6)


if __name__ == "__main__":
    unittest.main()
