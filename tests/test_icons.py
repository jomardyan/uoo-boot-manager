"""Validate platform image formats, export sizes, and runtime icon identity."""

import json
from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "assets/icons"


def png_size(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("Invalid PNG: " + str(path))
    return struct.unpack(">II", data[16:24])


class IconAssetTests(unittest.TestCase):
    def test_linux_png_exports_have_the_declared_sizes(self):
        for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
            with self.subTest(size=size):
                self.assertEqual(png_size(ICONS / f"png/uoo-{size}.png"), (size, size))

    def test_ico_contains_valid_frames_for_windows_scaling(self):
        for name, expected in [("uoo.ico", {16, 20, 24, 32, 40, 48, 64, 96, 128, 256}), ("favicon.ico", {16, 32, 48})]:
            with self.subTest(name=name):
                data = (ICONS / name).read_bytes()
                reserved, kind, count = struct.unpack_from("<HHH", data)
                self.assertEqual((reserved, kind, count), (0, 1, len(expected)))
                found = set()
                for index in range(count):
                    width, height, colors, reserved, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
                    width, height = width or 256, height or 256
                    self.assertEqual(width, height)
                    self.assertEqual(bits, 32)
                    self.assertGreaterEqual(offset, 6 + count * 16)
                    self.assertLessEqual(offset + length, len(data))
                    self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack_from(">II", data, offset + 16), (width, height))
                    found.add(width)
                self.assertEqual(found, expected)

    def test_icns_container_contains_retina_exports(self):
        data = (ICONS / "uoo.icns").read_bytes()
        self.assertEqual(data[:4], b"icns")
        self.assertEqual(struct.unpack_from(">I", data, 4)[0], len(data))
        types = set()
        offset = 8
        while offset < len(data):
            kind, length = struct.unpack_from(">4sI", data, offset)
            self.assertGreaterEqual(length, 8)
            self.assertLessEqual(offset + length, len(data))
            types.add(kind)
            offset += length
        self.assertEqual(offset, len(data))
        self.assertTrue({b"ic07", b"ic08", b"ic09", b"ic10"} <= types)

    def test_manifest_and_touch_icons_resolve_to_the_right_sizes(self):
        manifest = json.loads((ICONS / "site.webmanifest").read_text())
        for icon in manifest["icons"]:
            expected = tuple(int(value) for value in icon["sizes"].split("x"))
            self.assertEqual(png_size(ICONS / icon["src"]), expected)
        self.assertTrue(any(icon["purpose"] == "maskable" for icon in manifest["icons"]))
        touch = ICONS / "apple-touch-icon.png"
        self.assertEqual(png_size(touch), (180, 180))
        self.assertEqual(touch.read_bytes()[25], 2, "Apple touch icon should be opaque RGB")

    def test_platform_copies_match_the_vector_and_windows_masters(self):
        self.assertEqual((ROOT / "packaging/uoo-boot-manager.svg").read_bytes(), (ICONS / "uoo.svg").read_bytes())
        self.assertEqual((ROOT / "uoo/icons/hicolor/scalable/apps/org.uoo.BootManager.svg").read_bytes(), (ICONS / "uoo.svg").read_bytes())
        self.assertEqual((ROOT / "windows/Uoo.Desktop/uoo.ico").read_bytes(), (ICONS / "uoo.ico").read_bytes())
        self.assertEqual((ROOT / "windows/Uoo.Desktop/uoo.png").read_bytes(), (ICONS / "png/uoo-256.png").read_bytes())
        self.assertIn("Icon=org.uoo.BootManager\n", (ROOT / "packaging/uoo-boot-manager.desktop").read_text())

    def test_all_svg_sources_parse_and_have_a_viewbox(self):
        for path in ICONS.glob("*.svg"):
            with self.subTest(path=path.name):
                root = ET.parse(path).getroot()
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertIn("viewBox", root.attrib)


if __name__ == "__main__":
    unittest.main()
