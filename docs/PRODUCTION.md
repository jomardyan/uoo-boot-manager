# Uoo 2.0 production requirements

<img src="../assets/icons/png/uoo-64.png" width="64" height="64" alt="Uoo Boot Manager icon">

This release manages Ubuntu GRUB and UEFI boot configuration. Release readiness requires evidence for every item below; a successful package build alone does not establish readiness.

## Features

- Inspect installed operating systems, submenus, firmware entries, disks, boot mode, Secure Boot, and tool availability.
- Set a fixed GRUB default or remember the last selection; configure timeout, visibility, recovery behavior, OS discovery, and kernel command lines.
- Schedule and clear a one-time GRUB or UEFI boot.
- Configure graphics mode, menu colors, and a managed background image.
- Add, edit, enable, disable, reorder, and remove managed Linux and EFI chainloader menu entries.
- Reorder firmware entries, manage inclusion in BootOrder, change active flags, and create/remove firmware loader entries with explicit confirmation and protection for the current boot path.
- Create, inspect, export, remove, and restore backups; export/import validated portable GRUB/custom-entry configurations.
- Diagnose boot prerequisites and provide guided UEFI and BIOS bootloader repair/reinstallation where the current installation supports it.
- Provide a complete demo, keyboard navigation, responsive desktop layouts, progress, recoverable error messages, and confirmation of material changes.

## Reliability and trust

- Run the GUI as a normal user; execute fixed installed root-owned helper code under polkit authentication.
- Validate request schemas and live devices; never execute user-provided shell text.
- Serialize system writes, preserve unrelated settings, write atomically, save backups, and recover unfinished transactions.
- Verify generated GRUB syntax and requested firmware state; report rollback failures and repair limitations accurately.
- Detect conflicting GRUB drop-ins, stale selections, unsupported environment-block storage, and unavailable firmware.
- Keep privileged backups private, validate their schema/integrity, and constrain imported data to supported configuration values.
- Do not reboot or modify the host boot configuration during development verification.

## Release gates

- Unit and failure-path tests cover validation, parsing, transactions, recovery, GRUB/UEFI operations, and backup handling.
- Real GTK4/libadwaita tests exercise the full demo, drafts, dialogs, and reachable controls at laptop window sizes; screenshots verify native light/dark appearance.
- The Debian package has correct dependencies, fixed executable paths, root ownership, protected helper/module permissions, and current sources.
- A disposable Ubuntu UEFI virtual machine installs the package and exercises the installed helper, real GRUB generation, custom entries, UEFI changes, repair, restore, and boot/reboot behavior.
- BIOS repair is verified in a disposable BIOS virtual machine or remains an explicit unmet release gate.
- CI, build instructions, an operational manual, supported-platform limits, and reproducible validation instructions accompany the release.

Evidence is recorded in [the release validation report](VALIDATION.md) once the gates run. Hardware-specific firmware behavior beyond the test environment must be stated as a support limit.
