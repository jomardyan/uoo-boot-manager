#!/usr/bin/env python3
"""Export Uoo's SVG masters to platform assets (Pillow + GdkPixbuf/librsvg)."""

import json
from pathlib import Path
import shutil

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "assets/icons"
APP_ID = "org.uoo.BootManager"
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def render(source, size):
    # Render each size directly from vector, with extra samples for tiny icons.
    scale = 4 if size <= 64 else 1
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(source), size * scale, size * scale, True)
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    result = Image.frombytes(mode, (pixbuf.get_width(), pixbuf.get_height()),
                             bytes(pixbuf.get_pixels()), "raw", mode, pixbuf.get_rowstride()).convert("RGBA")
    return result.resize((size, size), Image.Resampling.LANCZOS) if scale > 1 else result


def save(image, path, **options):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, **options)


def copy(source, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, path)


def build():
    master = ICONS / "uoo.svg"
    small = ICONS / "favicon.svg"
    images = {size: render(small if size < 48 else master, size) for size in PNG_SIZES}
    for size, image in images.items():
        save(image, ICONS / f"png/uoo-{size}.png", optimize=True)

    # Supply custom small frames to ICO so they retain the simplified glyph.
    frames = [render(small if size < 48 else master, size) for size in ICO_SIZES]
    save(frames[-1], ICONS / "uoo.ico", sizes=[(size, size) for size in ICO_SIZES], append_images=frames[:-1])
    copy(ICONS / "uoo.ico", ROOT / "windows/Uoo.Desktop/uoo.ico")
    copy(ICONS / "png/uoo-256.png", ROOT / "windows/Uoo.Desktop/uoo.png")
    save(images[1024], ICONS / "uoo.icns")

    favicon_frames = [render(small, size) for size in (16, 32, 48)]
    save(favicon_frames[-1], ICONS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=favicon_frames[:-1])
    for size in (16, 32):
        save(render(small, size), ICONS / f"favicon-{size}x{size}.png", optimize=True)
    touch = Image.new("RGBA", (180, 180), "#1d2738")
    touch.alpha_composite(render(master, 180))
    save(touch.convert("RGB"), ICONS / "apple-touch-icon.png", optimize=True)
    for size in (192, 512):
        save(render(master, size), ICONS / f"android-chrome-{size}x{size}.png", optimize=True)
    # Keep the entire mark within Android's central mask-safe circle.
    maskable = Image.new("RGBA", (512, 512), "#1d2738")
    inset = render(master, 384)
    maskable.alpha_composite(inset, (64, 64))
    save(maskable, ICONS / "maskable-512x512.png", optimize=True)
    manifest = {
        "name": "Uoo Boot Manager", "short_name": "Uoo", "display": "standalone",
        "theme_color": "#1d2738", "background_color": "#1d2738",
        "icons": [
            {"src": f"android-chrome-{size}x{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
            for size in (192, 512)
        ] + [{"src": "maskable-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }
    (ICONS / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n")

    # A private fallback theme gives source runs and bundled desktops the same
    # icon as installed packages, without altering the host's icon theme.
    theme = ROOT / "uoo/icons/hicolor"
    copy(master, theme / f"scalable/apps/{APP_ID}.svg")
    copy(ICONS / "uoo-symbolic.svg", theme / f"symbolic/apps/{APP_ID}-symbolic.svg")
    (theme / "index.theme").write_text("[Icon Theme]\nName=Uoo\nComment=Uoo application icons\nDirectories=scalable/apps,symbolic/apps\n\n[scalable/apps]\nSize=128\nMinSize=16\nMaxSize=1024\nType=Scalable\nContext=Applications\n\n[symbolic/apps]\nSize=16\nType=Scalable\nMinSize=8\nMaxSize=256\nContext=Applications\n")
    copy(master, ROOT / "packaging/uoo-boot-manager.svg")

    # Contact sheet for checking desktop backgrounds and tiny-size legibility.
    sheet = Image.new("RGB", (1200, 700), "#f3f5f8")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=20)
        title_font = ImageFont.load_default(size=32)
    except TypeError:  # Pillow <10.1 still supports all platform exports.
        font = title_font = ImageFont.load_default()
    draw.text((40, 28), "Uoo Boot Manager", fill="#1d2738", font=title_font)
    draw.text((40, 74), "Platform icon family / graphite + signal orange", fill="#526176", font=font)
    sheet.paste(images[256], (65, 150), images[256])
    draw.rounded_rectangle((380, 130, 720, 470), radius=24, fill="#111827")
    sheet.paste(images[256], (422, 172), images[256])
    draw.rounded_rectangle((760, 130, 1150, 470), radius=24, fill="#e0e5ed")
    for index, size in enumerate((16, 24, 32, 48, 64, 128)):
        x = 56 + index * 120
        sheet.paste(images[size], (x, 560 - size // 2), images[size])
        draw.text((x, 645), str(size) + " px", fill="#526176", font=font)
    sheet.paste(render(master, 256), (825, 174), render(master, 256))
    draw.text((66, 438), "Desktop / 256 px", fill="#526176", font=font)
    draw.text((800, 510), "SVG · PNG · ICO · ICNS", fill="#526176", font=font)
    save(sheet, ICONS / "preview.png", optimize=True)
    print("Exported Uoo icons to assets/icons/, Linux fallback theme, and Windows resources.")


if __name__ == "__main__":
    build()
