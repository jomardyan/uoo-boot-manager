"""Plain-language explanations for failures that boot tools report in terse or misleading ways.

Pure text matching with no I/O. The rules come from problems that users of dual-boot
systems report most often; each hint says what the message usually means and what is
safe to try, never a command that could make a boot problem worse.
"""

import re

# (program name pattern, message pattern, explanation)
RULES = (
    ("efibootmgr", r"No space left on device|Could not prepare Boot variable",
     "The firmware's variable storage (NVRAM), not your disk, is full. Remove unused UEFI entries on the Firmware page "
     "(old operating systems, USB drives), then try again. Do not delete files under /sys/firmware/efi/efivars by hand."),
    ("efibootmgr", r"Read-only file system",
     "The UEFI variable store is mounted read-only. Remount it with 'sudo mount -o remount,rw /sys/firmware/efi/efivars' and try again."),
    ("efibootmgr", r"Operation not permitted|Permission denied",
     "The firmware variable is protected. It may be marked immutable or the firmware may lock it. Check that Secure Boot or a firmware "
     "password is not blocking writes, and that you are running as administrator."),
    ("efibootmgr", r"EFI variables are not supported|not supported on this system",
     "This session did not start in UEFI mode, so boot entries cannot be read or changed. Restart in UEFI mode from the firmware menu."),
    ("efibootmgr", r"Input/output error|Invalid argument",
     "The firmware refused the change. Some firmware only accepts entries when a loader exists at EFI/BOOT/BOOTX64.EFI, or needs "
     "unused entries removed first. Updating the firmware may help."),
    ("grub-install|update-grub|grub-mkconfig", r"No space left on device",
     "The EFI System Partition or /boot is full. Free space there before trying again; Windows often creates a very small ESP."),
    ("grub-install|update-grub|grub-mkconfig", r"Read-only file system",
     "/boot or the EFI System Partition is mounted read-only, often after a disk error. Remount it read-write and check the disk."),
    ("grub-probe|update-grub|grub-mkconfig|grub-install", r"failed to get canonical path",
     "GRUB cannot resolve the root device. This happens in live sessions, chroots and some overlay setups. Run Uoo from the installed system."),
    ("update-grub|grub-mkconfig|grub-script-check", r"syntax error|unknown command|unrecognized token",
     "A GRUB script has a syntax error, usually a hand-edited file in /etc/grub.d or /etc/default/grub.d. Fix or remove it and try again; "
     "Uoo restored your previous settings."),
    ("grub-probe|grub-install", r"unknown filesystem|cannot find a device for",
     "GRUB could not identify a filesystem. The partition may be unmounted, encrypted or damaged. Check Diagnostics."),
)


def explain(program, details):
    """Return an explanation for the first matching rule, or an empty string."""
    name = str(program or "")
    text = str(details or "")
    for programs, pattern, hint in RULES:
        if re.fullmatch(programs, name) and re.search(pattern, text, re.I):
            return hint
    return ""
