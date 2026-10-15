"""Read GRUB data without evaluating shell code."""

from collections import Counter
from dataclasses import dataclass
import re
import shlex


_DECLARATION = re.compile(r"(menuentry|submenu)(?=\s)")
_COMMAND_KEYWORD = re.compile(r"(?:^|[\n;{}])[ \t]*(?:then|do|else)$")


def _command_start(text, i):
    """Whether a word at i begins a command (after a newline, ;, {, }, then, do or else)."""
    j = i
    while j and text[j - 1] in " \t":
        j -= 1
    return not j or text[j - 1] in "\n;{}" or _COMMAND_KEYWORD.search(text, max(0, j - 64), j) is not None


@dataclass(frozen=True)
class Entry:
    title: str
    selector: str
    position: str
    group: str = ""


def _parameter_end(text, start):
    """Skip a braced variable, including nested variables and quoted defaults."""
    depth = 1
    quote = None
    i = start + 2
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            i += 2
            continue
        if text[i:i + 2] == "${" and quote != "'":
            i = _parameter_end(text, i)
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "}":
            depth -= 1
            if not depth:
                return i + 1
        i += 1
    raise ValueError("Unfinished GRUB variable expansion.")


def _grub_events(text, quote=None, declarations=False):
    """Lex structural events without evaluating any GRUB or shell expression."""
    events = []
    i = 0
    while i < len(text):
        char = text[i]
        if declarations and quote is None and char in "ms":
            match = _DECLARATION.match(text, i)
            if match and _command_start(text, i):
                events.append((i, match[1]))
        if char == "\\" and quote != "'":
            i += 2
            continue
        if text[i:i + 2] == "${" and quote != "'":
            i = _parameter_end(text, i)
            continue
        if quote:
            if char == quote:
                quote = None
        elif char == "#" and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";{}"):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif char in "\"'":
            quote = char
        elif char in "{}":
            events.append((i, char))
        i += 1
    return events, quote


def structural_braces(line, quote=None):
    """Return (unquoted brace events, open quote), ignoring comments, escapes, and ${variables}."""
    return _grub_events(line, quote)


def _literal_tokens(text, reject_operators=False, started=False):
    """Read shell quoting and continuations while leaving expansions literal.

    started=True reads text that continues a word, such as the value after NAME=,
    where a leading # is literal rather than a comment.
    """
    tokens = []
    token = []
    quote = None
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            if i + 1 >= len(text):
                raise ValueError("Unfinished escape.")
            following = text[i + 1]
            if following == "\n":
                i += 2
                continue
            if quote == '"' and following not in '$`"\\':
                token.append("\\")
                i += 1
                continue
            token.append(following)
            started = True
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                token.append(char)
        elif char in "\"'":
            started = True
            quote = char
        elif char == "#" and not started:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif char.isspace():
            if started:
                tokens.append("".join(token))
                token = []
                started = False
        elif reject_operators and char in ";|&<>":
            raise ValueError("Shell commands are not settings.")
        else:
            token.append(char)
            started = True
        i += 1
    if quote:
        raise ValueError("Unfinished quote.")
    if started:
        tokens.append("".join(token))
    return tokens


def _stable_id(value):
    # update-grub inserts GRUB_DEFAULT into quoted GRUB code after sourcing the
    # shell defaults. Keep identifiers literal at both interpretation stages.
    return bool(value) and not value.isdecimal() and re.fullmatch(r"[A-Za-z0-9_.:+/@%\-]+", value) is not None


def parse_entries(config):
    entries = []
    entry_paths = []
    sibling_ids = Counter()
    depth = 0
    frames = []
    counters = {(): 0}
    pending = None
    events, quote = _grub_events(config, declarations=True)
    if quote:
        raise ValueError("Unfinished quote in GRUB configuration.")
    for offset, event in events:
        if event in ("menuentry", "submenu"):
            if pending is not None:
                raise ValueError("Unfinished GRUB menu declaration.")
            pending = offset
            continue
        if pending is not None and event == "{":
            try:
                tokens = _literal_tokens(config[pending:offset])
            except ValueError as exc:
                raise ValueError("Cannot read a GRUB menu declaration.") from exc
            # GRUB accepts options anywhere; the title is the first argument that is not an option.
            kind, title, entry_id = tokens[0], None, None
            j = 1
            while j < len(tokens):
                token = tokens[j]
                if token.startswith("--id="):
                    entry_id = token[5:]
                elif token in ("--id", "$menuentry_id_option", "${menuentry_id_option}") and j + 1 < len(tokens):
                    entry_id = tokens[j + 1]
                    j += 1
                elif token in ("--class", "--users", "--hotkey") and j + 1 < len(tokens):
                    j += 1
                elif not token.startswith("--") and title is None:
                    title = token
                j += 1
            if not title:
                raise ValueError("Incomplete GRUB menu declaration.")
            parent = tuple(frame[2] for frame in frames)
            position = counters.get(parent, 0)
            counters[parent] = position + 1
            positions = parent + (str(position),)
            ids = [frame[3] for frame in frames] + [entry_id]
            if entry_id:
                sibling_ids[(parent, entry_id)] += 1
            selector = ">".join(ids) if all(_stable_id(item) for item in ids) else ">".join(positions)
            if kind == "submenu":
                # Ignore any nested shell/function blocks before this declaration.
                frames.append((depth + 1, title, str(position), entry_id))
                counters[positions] = 0
            else:
                entries.append(Entry(title, selector, ">".join(positions), " / ".join(frame[1] for frame in frames)))
                entry_paths.append((positions, ids))
            pending = None
        elif pending is not None and event == "}":
            raise ValueError("Unfinished GRUB menu declaration.")
        depth += 1 if event == "{" else -1
        if depth < 0:
            raise ValueError("Unmatched closing brace in GRUB configuration.")
        while frames and depth < frames[-1][0]:
            frames.pop()
    if pending is not None:
        raise ValueError("Unfinished GRUB menu declaration.")
    if depth:
        raise ValueError("Unfinished block in GRUB configuration.")
    counts = Counter(entry.selector for entry in entries)
    resolved = []
    for entry, (positions, ids) in zip(entries, entry_paths):
        ambiguous = counts[entry.selector] > 1 or any(
            sibling_ids[(positions[:index], entry_id)] > 1
            for index, entry_id in enumerate(ids)
        )
        resolved.append(Entry(entry.title, entry.position if ambiguous else entry.selector,
                              entry.position, entry.group))
    return resolved


def parse_settings(text):
    settings = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        # The shell only assigns NAME=value; "NAME =value" or "NAME= cmd" runs a command instead.
        match = re.match(r"^\s*(?:export\s+)?(GRUB_[A-Z_]+)=(.*)$", lines[i])
        i += 1
        if not match:
            continue
        value, end = match[2], i
        while True:
            try:
                if value[:1].isspace():
                    values = None if _literal_tokens(value, reject_operators=True) else [""]
                else:
                    values = _literal_tokens(value, reject_operators=True, started=bool(value))
                break
            except ValueError as exc:
                # An open quote or trailing backslash continues the value onto the next line.
                if end >= len(lines) or str(exc) not in ("Unfinished quote.", "Unfinished escape."):
                    values = None
                    break
                value += "\n" + lines[end]
                end += 1
        if values is None:
            continue
        i = end
        if len(values) == 1:
            settings[match[1]] = values[0]
        elif not values:
            settings[match[1]] = ""
    return settings


def parse_firmware(text):
    """Parse efibootmgr output; never infer an order from malformed data."""
    result = {"entries": [], "order": [], "current": "", "next": "",
              "available": True, "error": ""}
    metadata = set()
    entry_ids = set()
    for line in text.splitlines():
        line = line.strip()
        for key, target in (("BootOrder", "order"), ("BootCurrent", "current"),
                            ("BootNext", "next")):
            if line.startswith(key):
                match = re.fullmatch(rf"{key}:\s*(.*)", line)
                if not match or key in metadata:
                    raise ValueError(f"Malformed or duplicate {key}.")
                metadata.add(key)
                value = match[1].strip()
                if target == "order":
                    order = [] if not value else [part.strip().upper() for part in value.split(",")]
                    if any(not re.fullmatch(r"[0-9A-F]{4}", item) for item in order) or len(order) != len(set(order)):
                        raise ValueError("Malformed or duplicate BootOrder IDs.")
                    result[target] = order
                elif not re.fullmatch(r"[0-9A-Fa-f]{4}", value):
                    raise ValueError(f"Malformed {key} ID.")
                else:
                    result[target] = value.upper()
                break
        else:
            match = re.fullmatch(r"Boot([0-9A-Fa-f]{4})(\*)?[ \t]+(.+)", line)
            if match:
                entry_id = match[1].upper()
                if entry_id in entry_ids:
                    raise ValueError("Duplicate UEFI boot entry ID.")
                entry_ids.add(entry_id)
                label = match[3].split("\t", 1)[0].strip()
                if not label:
                    raise ValueError("Missing UEFI boot entry label.")
                result["entries"].append({"id": entry_id, "label": label, "active": bool(match[2])})
            elif re.match(r"Boot[0-9A-Fa-f]", line) or re.match(r"Boot[A-Za-z0-9]{4}(?:\*|\s|$)", line):
                raise ValueError("Malformed UEFI boot entry.")
    if not metadata and not result["entries"]:
        raise ValueError("No UEFI boot information was found.")
    if set(result["order"]) - entry_ids:
        raise ValueError("BootOrder references a missing UEFI boot entry.")
    return result


def override_text(selector, timeout, style):
    from .models import _SELECTOR
    # 00_header copies GRUB_DEFAULT into grub.cfg inside double quotes, so shell quoting alone is not enough.
    if not isinstance(selector, str) or len(selector) > 2048 or not _SELECTOR.fullmatch(selector):
        raise ValueError("Invalid boot entry.")
    if type(timeout) is not int or not -1 <= timeout <= 300:
        raise ValueError("Timeout must be between -1 and 300 seconds.")
    if style not in ("menu", "hidden", "countdown"):
        raise ValueError("Invalid menu style.")
    return ("# Managed by Uoo Boot Manager.\n"
            f"GRUB_DEFAULT={shlex.quote(selector)}\n"
            f"GRUB_TIMEOUT={timeout}\n"
            f"GRUB_TIMEOUT_STYLE={style}\n"
            "GRUB_SAVEDEFAULT=false\n")


DEMO_CONFIG = """
menuentry 'Ubuntu' --id 'ubuntu' {
    linux /boot/vmlinuz root=UUID=demo ro quiet splash
}
submenu 'Advanced options for Ubuntu' --id 'advanced' {
    menuentry 'Ubuntu, with Linux 6.8.0-generic' --id 'linux-6.8' {
        linux /boot/vmlinuz-6.8 ro
    }
    menuentry 'Ubuntu, with Linux 6.8.0-generic (recovery mode)' --id 'recovery-6.8' {
        linux /boot/vmlinuz-6.8 ro recovery
    }
}
menuentry 'Windows Boot Manager' --id 'windows' {
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
menuentry 'UEFI Firmware Settings' --id 'firmware' {
    fwsetup
}
"""
