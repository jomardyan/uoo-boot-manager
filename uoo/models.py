"""Validated data models and deterministic GRUB source generation.

These helpers perform no I/O and never evaluate configuration. The privileged
backend must additionally check that selectors, filesystem UUIDs, and files
still exist immediately before applying a change.
"""

import re
import shlex
import struct
import unicodedata
import zlib


GRUB_COLORS = frozenset({
    "black", "blue", "green", "cyan", "red", "magenta", "brown", "light-gray",
    "dark-gray", "light-blue", "light-green", "light-cyan", "light-red",
    "light-magenta", "yellow", "white",
})
GRUB_BOOLEAN_KEYS = frozenset({
    "GRUB_SAVEDEFAULT", "GRUB_DISABLE_OS_PROBER", "GRUB_DISABLE_RECOVERY",
})
GRUB_TIMEOUT_KEYS = frozenset({"GRUB_TIMEOUT", "GRUB_RECORDFAIL_TIMEOUT"})
GRUB_KERNEL_KEYS = frozenset({"GRUB_CMDLINE_LINUX", "GRUB_CMDLINE_LINUX_DEFAULT"})
GRUB_COLOR_KEYS = frozenset({"GRUB_COLOR_NORMAL", "GRUB_COLOR_HIGHLIGHT"})
GRUB_KEYS = GRUB_BOOLEAN_KEYS | GRUB_TIMEOUT_KEYS | GRUB_KERNEL_KEYS | GRUB_COLOR_KEYS | {
    "GRUB_DEFAULT", "GRUB_TIMEOUT_STYLE", "GRUB_GFXMODE", "GRUB_GFXPAYLOAD_LINUX",
}

_SELECTOR = re.compile(r"[A-Za-z0-9_.:+/@%\-]+(?:>[A-Za-z0-9_.:+/@%\-]+)*")
_KERNEL_OPTIONS = re.compile(r"[A-Za-z0-9 ,/:_%+.=@\-]*")
_CUSTOM_ID = re.compile(r"uoo-[0-9a-f]{32}")
_UUID = re.compile(r"[0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*")
_PATH = re.compile(r"/[A-Za-z0-9_./:+@%\-]+")
_MODE = re.compile(r"([0-9]{3,5})x([0-9]{3,5})(?:x([0-9]{1,2}))?")


def _text(value, label, limit, empty=False):
    if type(value) is not str:
        raise ValueError(f"{label} must be text.")
    if len(value) > limit or (not empty and not value):
        raise ValueError(f"{label} must contain {'0' if empty else '1'}–{limit} characters.")
    return value


def _kernel_options(value, label):
    value = _text(value, label, 2048, empty=True)
    if not _KERNEL_OPTIONS.fullmatch(value):
        raise ValueError(
            f"{label} supports ASCII letters, digits, spaces and , / : _ % + . = @ -; "
            "quotes, substitutions, escapes and command operators are not allowed."
        )
    return value


def _graphics_mode(value):
    value = _text(value, "GRUB_GFXMODE", 256)
    modes = value.split(",")
    if len(modes) > 16:
        raise ValueError("GRUB_GFXMODE supports at most 16 modes.")
    for mode in modes:
        if mode == "auto":
            continue
        match = _MODE.fullmatch(mode)
        if not match:
            raise ValueError("GRUB_GFXMODE must be auto or comma-separated WIDTHxHEIGHT[xDEPTH] modes.")
        width, height = int(match[1]), int(match[2])
        if not 320 <= width <= 16384 or not 200 <= height <= 16384:
            raise ValueError("Graphics modes need widths of 320–16384 and heights of 200–16384.")
        if match[3] and int(match[3]) not in (8, 15, 16, 24, 32):
            raise ValueError("Graphics depth must be 8, 15, 16, 24 or 32 bits.")
    return value


def validate_grub_changes(settings):
    """Return a copied settings dictionary or raise ValueError on invalid input.

    Timeout values are integers, flags are booleans, and all other values are
    literal strings. The caller validates GRUB_DEFAULT against the live menu.
    Graphics validation checks syntax and conventional bounds, not hardware
    support. Unknown keys are rejected rather than silently dropped.
    """
    if type(settings) is not dict:
        raise ValueError("GRUB changes must be a settings object.")
    unknown = set(settings) - GRUB_KEYS
    if unknown:
        raise ValueError("Unsupported GRUB setting: " + ", ".join(sorted(map(str, unknown))))
    validated = {}
    for key, value in settings.items():
        if key in GRUB_BOOLEAN_KEYS:
            if type(value) is not bool:
                raise ValueError(f"{key} must be true or false.")
        elif key in GRUB_TIMEOUT_KEYS:
            if type(value) is not int or not -1 <= value <= 300:
                raise ValueError(f"{key} must be an integer between -1 and 300 seconds.")
        elif key in GRUB_KERNEL_KEYS:
            value = _kernel_options(value, key)
        elif key in GRUB_COLOR_KEYS:
            value = _text(value, key, 40)
            colors = value.split("/")
            if len(colors) != 2 or any(color not in GRUB_COLORS for color in colors):
                raise ValueError(f"{key} must contain two GNU GRUB color names, as foreground/background.")
        elif key == "GRUB_DEFAULT":
            value = _text(value, key, 2048)
            if not _SELECTOR.fullmatch(value):
                raise ValueError("GRUB_DEFAULT must be a literal entry ID or numeric path, separated with >.")
        elif key == "GRUB_TIMEOUT_STYLE":
            if type(value) is not str or value not in ("menu", "hidden", "countdown"):
                raise ValueError("GRUB_TIMEOUT_STYLE must be menu, hidden or countdown.")
        elif key == "GRUB_GFXMODE":
            value = _graphics_mode(value)
        elif key == "GRUB_GFXPAYLOAD_LINUX":
            if type(value) is not str or value not in ("keep", "text"):
                raise ValueError("GRUB_GFXPAYLOAD_LINUX must be keep or text.")
        validated[key] = value
    return validated


def render_grub_settings(settings):
    """Validate and render a shell defaults drop-in in deterministic key order."""
    settings = validate_grub_changes(settings)
    lines = ["# Managed by Uoo Boot Manager."]
    for key, value in sorted(settings.items()):
        if type(value) is bool:
            text = "true" if value else "false"
        else:
            text = str(value)
        lines.append(f"{key}={shlex.quote(text)}")
    return "\n".join(lines) + "\n"


def _custom_title(value):
    value = _text(value, "Entry title", 120)
    if not value.strip() or any(
        char in "'\"`$\\;\u2028\u2029" or unicodedata.category(char) == "Cc"
        for char in value
    ):
        raise ValueError("Entry titles must be readable text without controls, quotes, $, `, \\ or ;.")
    return value


def _custom_path(value, label):
    value = _text(value, label, 512)
    if not _PATH.fullmatch(value) or any(part in ("", ".", "..") for part in value.split("/")[1:]):
        raise ValueError(f"{label} must be an absolute path without traversal, spaces or shell symbols.")
    return value


def validate_custom_entries(entries):
    """Validate at most 32 JSON-style EFI or Linux entries and return copies.

    Common keys: id, title, type, enabled, uuid. EFI entries also require loader.
    Linux entries require kernel, initrd (empty is allowed), and options (empty
    is allowed). UUID and path existence are checked by the privileged caller.
    """
    if type(entries) is not list or len(entries) > 32:
        raise ValueError("Custom entries must be a list of at most 32 entries.")
    validated = []
    seen = set()
    common = {"id", "title", "type", "enabled", "uuid"}
    for index, entry in enumerate(entries, 1):
        if type(entry) is not dict:
            raise ValueError(f"Custom entry {index} must be an object.")
        kind = entry.get("type")
        if kind not in ("efi", "linux"):
            raise ValueError(f"Custom entry {index} type must be efi or linux.")
        expected = common | ({"loader"} if kind == "efi" else {"kernel", "initrd", "options"})
        if set(entry) != expected:
            missing, unknown = expected - set(entry), set(entry) - expected
            details = []
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if unknown:
                details.append("unsupported " + ", ".join(sorted(map(str, unknown))))
            raise ValueError(f"Custom entry {index}: " + "; ".join(details) + ".")
        entry_id = _text(entry["id"], "Entry ID", 36)
        if not _CUSTOM_ID.fullmatch(entry_id) or entry_id in seen:
            raise ValueError("Custom entry IDs must be unique uoo- followed by 32 lowercase hex digits.")
        seen.add(entry_id)
        if type(entry["enabled"]) is not bool:
            raise ValueError("Entry enabled must be true or false.")
        uuid = _text(entry["uuid"], "Filesystem UUID", 64)
        if len(uuid) < 4 or not _UUID.fullmatch(uuid):
            raise ValueError("Filesystem UUID must contain 4–64 hexadecimal digits and optional separating hyphens.")
        copied = {"id": entry_id, "title": _custom_title(entry["title"]),
                  "type": kind, "enabled": entry["enabled"], "uuid": uuid}
        if kind == "efi":
            loader = _custom_path(entry["loader"], "EFI loader")
            if not loader.startswith("/EFI/") or not loader.lower().endswith(".efi"):
                raise ValueError("EFI loader must be a .efi file below /EFI/.")
            copied["loader"] = loader
        else:
            kernel = _custom_path(entry["kernel"], "Linux kernel")
            if not re.fullmatch(r"/(?:boot/)?vmlinuz[^/]*", kernel):
                raise ValueError("Linux kernel must be a vmlinuz file below /boot/ or at the filesystem root.")
            initrd = _text(entry["initrd"], "Initial RAM disk", 512, empty=True)
            if initrd:
                initrd = _custom_path(initrd, "Initial RAM disk")
                if not re.fullmatch(r"/(?:boot/)?initrd[^/]*", initrd):
                    raise ValueError("Initial RAM disk must be an initrd file below /boot/ or at the filesystem root.")
            copied.update(kernel=kernel, initrd=initrd,
                          options=_kernel_options(entry["options"], "Kernel options"))
        validated.append(copied)
    return validated


def compile_custom_entries(entries):
    """Return a GRUB script payload; callers wrap it in a quoted heredoc."""
    entries = validate_custom_entries(entries)
    lines = ["# Managed by Uoo Boot Manager."]
    for entry in entries:
        if not entry["enabled"]:
            continue
        lines.extend([
            f"menuentry '{entry['title']}' --id '{entry['id']}' {{",
            f"    search --no-floppy --fs-uuid --set=root '{entry['uuid']}'",
        ])
        if entry["type"] == "efi":
            lines.append(f"    chainloader '{entry['loader']}'")
        else:
            options = f" {entry['options']}" if entry["options"] else ""
            lines.append(f"    linux '{entry['kernel']}'{options}")
            if entry["initrd"]:
                lines.append(f"    initrd '{entry['initrd']}'")
        lines.append("}")
    return "\n".join(lines) + "\n"


def validate_png(data):
    """Validate a PNG up to 2 MiB and 4096² pixels, returning (width, height).

    Check chunk framing/CRCs, image properties and the complete compressed row
    stream. Decompression is bounded and streamed to avoid allocating a bitmap.
    """
    if type(data) is not bytes or not 8 < len(data) <= 2 * 1024 * 1024 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Background must be a PNG file no larger than 2 MiB.")
    offset, header, palette, image_started, image_ended, end = 8, None, False, False, False, False
    compressed = []
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("Truncated PNG chunk.")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        if length > len(data) - offset - 12 or not re.fullmatch(b"[A-Za-z]{4}", kind) or kind[2] & 32:
            raise ValueError("Malformed PNG chunk.")
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ValueError("PNG chunk checksum is invalid.")
        offset += 12 + length
        if header is None and kind != b"IHDR":
            raise ValueError("PNG must begin with its image header.")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("Malformed or duplicate PNG header.")
            header = struct.unpack(">IIBBBBB", payload)
            width, height, depth, color, compression, filtering, interlace = header
            allowed_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if not 1 <= width <= 4096 or not 1 <= height <= 4096 or depth not in allowed_depths.get(color, ()):
                raise ValueError("PNG dimensions or color depth are unsupported (maximum 4096 × 4096).")
            if compression or filtering or interlace not in (0, 1):
                raise ValueError("PNG compression, filter or interlace method is unsupported.")
        elif kind == b"PLTE":
            if (palette or image_started or not length or length % 3 or length > 768 or header[3] in (0, 4)
                    or header[3] == 3 and length > 3 * (1 << header[2])):
                raise ValueError("Malformed PNG palette.")
            palette = True
        elif kind == b"IDAT":
            if image_ended or (header[3] == 3 and not palette):
                raise ValueError("PNG image chunks or palette are out of order.")
            image_started = True
            compressed.append(payload)
        elif kind == b"IEND":
            if length or not image_started or offset != len(data):
                raise ValueError("Malformed PNG end marker.")
            end = True
            break
        else:
            if not kind[0] & 32:
                raise ValueError("Unsupported critical PNG chunk.")
            if image_started:
                image_ended = True
    if not end:
        raise ValueError("PNG is incomplete.")
    width, height, depth, color, _, _, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = [(0, 0, 1, 1)] if not interlace else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    ]
    filters = []
    expected = 0
    for x, y, dx, dy in passes:
        pw, ph = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if pw and ph:
            stride = (pw * channels * depth + 7) // 8 + 1
            filters.extend(expected + row * stride for row in range(ph))
            expected += stride * ph
    inflater = zlib.decompressobj()
    produced, filter_index = 0, 0
    try:
        chunk = b"".join(compressed)
        while chunk:
            output = inflater.decompress(chunk, min(65536, expected - produced + 1))
            chunk = inflater.unconsumed_tail
            if produced + len(output) > expected:
                raise ValueError("PNG decompressed data exceeds its dimensions.")
            while filter_index < len(filters) and filters[filter_index] < produced + len(output):
                if output[filters[filter_index] - produced] > 4:
                    raise ValueError("PNG row filter is invalid.")
                filter_index += 1
            produced += len(output)
            if inflater.eof:
                if inflater.unused_data or chunk:
                    raise ValueError("PNG image stream contains trailing data.")
                break
        if not inflater.eof or produced != expected:
            raise ValueError("PNG image stream is truncated or has the wrong size.")
    except zlib.error as exc:
        raise ValueError("PNG image data is invalid.") from exc
    return width, height
