# Uoo Boot Manager

A native boot manager for Ubuntu and Windows 11. The Ubuntu edition uses
GTK4/libadwaita for GRUB and UEFI. The Windows edition uses a native WPF Fluent
desktop for Windows Boot Manager, BCD, and UEFI.

**Windows 11:** see the [Windows installation, features, and build guide](docs/WINDOWS.md).
Windows builds include self-contained x64 and ARM64 installers and ZIPs.
The instructions below describe the Ubuntu edition.

![Uoo Boot Manager preview](docs/images/grub.png)

## Features

- A fixed GRUB default, remembering the last selection, menu timeout, visibility, and one-time boot choices.
- Kernel command lines, recovery entries, operating-system discovery, and the timeout after a failed boot.
- Graphics mode, menu colors, and a validated background PNG copied into the boot filesystem.
- Managed Linux and EFI chainloader entries: add, edit, reorder, enable, disable, and remove.
- UEFI boot order, order inclusion, one-time boot, activation, and adding or removing existing EFI loader registrations.
- Automatic and manual backups, inspection, restoration, portable configuration export/import, and backup deletion.
- System diagnostics and guided bootloader repair/reinstallation for supported UEFI and BIOS installations.
- A complete demo, system light/dark styling, keyboard navigation, and native confirmation dialogs.

The desktop runs as your normal user. System actions authenticate through the installed polkit helper. Uoo keeps its settings in `/etc/default/grub.d/zz-uoo.cfg`, managed entries in `/etc/grub.d/41_uoo`, and private backups in `/var/lib/uoo-boot-manager/backups`. Other GRUB settings are preserved. Changes are serialized, journaled, verified, and rolled back when a tool fails; an unfinished transaction is recovered before the next change.

Ubuntu 24.04 or newer, GTK 4.6+, and libadwaita 1.5+ are required. GRUB features require an existing GRUB installation, `update-grub`, and `grub-script-check`; firmware features require UEFI mode and `efibootmgr`. Reinstallation currently supports x86-64 Ubuntu with a verified plain disk boot partition. Firmware behavior varies between computers. Uoo never reboots automatically.

## Build and install

From this directory on Ubuntu:

```sh
bash scripts/build-deb.sh
sudo apt install ./dist/uoo-boot-manager_2.1.2_all.deb
uoo-boot-manager
```

The package also adds **Uoo Boot Manager** to the application menu. Installing it does not change boot settings. Apply changes from the app when you are ready; boot changes take effect on a later boot.

The package installs Python, PyGObject, GTK/libadwaita, and polkit dependencies. It recommends `grub2-common` and `efibootmgr`. Existing signed Ubuntu shim/GRUB packages are needed for UEFI reinstallation; `grub-pc-bin` is needed for BIOS reinstallation. Install only the repair prerequisites appropriate to your machine's boot mode.

GitHub Actions builds Debian, Fedora/openSUSE RPM, Arch/Manjaro, AppImage, Flatpak, and Snap packages, including native ARM64 AppImage/Flatpak/Snap builds. It installs and tests the packages and uploads all downloads with SHA256 checksums. Version tags create draft releases. Flatpak uses the matching native host package for inspection and authenticated actions; Snap and AppImage also require the native helper for real changes. See [Linux packaging](docs/PACKAGING.md) for downloads, release steps, and platform limits.

## Try the demo

The demo uses sample boot entries and does not change the system:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m uoo --demo
```

You can inspect the real machine from the source directory with `python3 -m uoo --live`. Applying real changes requires the installed, root-owned helper. The backend uses only Python's standard library. Source mode never elevates user-editable Python code.

If the boot menu or managed entries cannot be read as a normal user, choose the lock button to load them with administrator authentication. Each subsequent system change requires authentication too. Changes to forms, custom entries, and firmware order remain drafts until applied; reloading or closing prompts before discarding drafts.

## Supported operations and limits

EFI registration creates a firmware pointer to an existing `.efi` file; it does not install another operating system. Mount the selected filesystem before enabling a managed entry or creating/removing a local firmware loader, so Uoo can verify the file. The currently booted firmware entry is protected. Clear a scheduled next boot before disabling or removing that entry. At least one active entry must remain in the boot order.

Managed entries are separate from Ubuntu's automatically generated kernel/OS entries. Use OS discovery and recovery settings for generated entries. Direct shell editing of GRUB scripts is deliberately not exposed. Imports contain validated supported settings and managed entries; they do not execute shell code or apply another computer's firmware inventory.

Saved and one-time GRUB selection require a verified writable environment block. Unsupported RAID/LVM/encrypted or checksumming boot layouts disable those controls. An independently mounted plain `/boot` partition can support them. BIOS embedding areas and GRUB directory snapshots must fit the stated backup limits; the repair plan explains unsupported layouts before any writes.

UEFI repair replaces Ubuntu's installed signed shim, GRUB, and MOK-manager files in the mounted Ubuntu EFI directory and rebuilds the loader configuration. It preserves other vendors' files, firmware order, and Secure Boot settings. BIOS repair backs up the boot sectors, embedding area, and GRUB files before running the native installer. Review the detected target and type it to confirm either operation. Backups cannot fix a failed disk or guarantee recovery from power loss before the app can run again.

## Restore and uninstall

Use Backups to restore previous settings while Uoo is installed. Restoring saves the current state first. Scope matters: a one-time boot backup restores that choice; an order backup restores order; a full backup also covers entry activation and managed GRUB files. Local EFI registrations removed through Uoo have restoration data; arbitrary deleted network/vendor firmware entries cannot be reconstructed from an order-only backup. Newly added firmware entries are preserved where possible.

Portable exports contain settings, not raw privileged scripts or boot-sector images. Imports apply validated GRUB and managed-entry data; firmware configuration remains local to the machine. Private recovery snapshots stay in the root-owned backup directory.

```sh
sudo apt remove uoo-boot-manager
```

Uninstalling preserves the GRUB settings drop-in and backups, so it does not silently change your boot behavior. To return to the previous settings, restore a backup **before** uninstalling. Existing firmware boot-order changes also remain after uninstalling.

The package build stages files under `build/` and writes the `.deb` to `dist/`; it does not install files or alter boot configuration on the build machine.

## Validation

Run `python3 -m unittest discover -s tests -v` from this directory. Unit and failure tests use temporary files and simulated tools. Native interaction tests run with an isolated display, for example:

```sh
GDK_BACKEND=x11 GSK_RENDERER=cairo xvfb-run -a python3 -m unittest discover -s tests -v
python3 scripts/check-package.py dist/uoo-boot-manager_2.1.2_all.deb
```

`scripts/vm-integration.py` downloads a checksum-verified official Ubuntu image, boots an independent disposable QEMU overlay, and can install/exercise/reboot the package under UEFI, Secure Boot, or BIOS. It does not use host disks or install the package on the host. See [production requirements](docs/PRODUCTION.md) and the [release validation report](docs/VALIDATION.md) for gates and recorded results. The CI workflow runs unit/native/package checks and provides an opt-in virtual-machine matrix.

GRUB settings follow the [GNU GRUB configuration reference](https://www.gnu.org/software/grub/manual/grub/html_node/Simple-configuration.html). Firmware actions use the options documented in Ubuntu's [efibootmgr manual](https://manpages.ubuntu.com/manpages/noble/man8/efibootmgr.8.html).
