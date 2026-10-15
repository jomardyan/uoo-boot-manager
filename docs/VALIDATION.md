# Uoo 2.1 validation record

Validation dates: 14–15 October 2026. This report separates current 2.1 package and native-desktop CI from the historical 2.0 Ubuntu boot/reboot evidence. Ubuntu system management requires Ubuntu 24.04+ with an existing GRUB installation; its repair support is limited to the verified x86-64 plain-disk layouts described in the operational manual. The Windows 11 edition uses native WPF and a separately authenticated installed administrator helper.

## Current 2.1 CI evidence

| Scope | Recorded result | Evidence |
| --- | --- | --- |
| Linux packages and native desktops | Passed | [Linux run 38798966968](https://github.com/jomardyan/uoo-boot-manager/actions/runs/38798966968), source `0238b41`: Debian, Fedora and openSUSE RPM, Arch-compatible package, and native x86-64/ARM64 AppImage, Flatpak, and Snap builds, with installation/startup checks and collected checksums. All ten downloads from the earlier [run 38792648872](https://github.com/jomardyan/uoo-boot-manager/actions/runs/38792648872) were also checked independently against that run's manifest. |
| Windows native desktop, offline BCD tests, and installation | Passed | [Windows run 38798967044](https://github.com/jomardyan/uoo-boot-manager/actions/runs/38798967044), source [0238b41](https://github.com/jomardyan/uoo-boot-manager/commit/0238b413db9d495019e38f85249e2a01b2c15186): x64 and Windows 11 ARM64 builds, 47 model/protocol tests, 27 real native/offline BCD tests, self-contained ZIPs and installers, installed administrator-helper inspection, and uninstall checks |
| Windows live transactions and recovery | Passed | The same Windows run exercises menu/profile operations, typed imports, firmware order and one-time choices, missing-EFI-loader reconstruction through BCDBoot, byte-identical EFI-file restoration, exact complete BCD/firmware recovery, native UEFI option bytes, and interrupted-write recovery on both architectures. |

The successful Windows native UI run records **35 demo assertions and 22 PNGs per architecture**, covering seven pages in light/dark mode and at minimum size, plus the typed repair dialog. Administrator inspection uses the installed native helper. The separate live suite restores all original BCD objects, firmware registrations and order, native UEFI option data, and EFI files before the installer checks. EFI-registration creation was skipped on these hosted guests because their current firmware identity/creation-session proof was unavailable; its request and protection logic is covered by model and native validation tests. CI does not reboot Windows or establish recovery of an offline, unbootable installation.

Download the successful runs' `linux-packages` and `windows-packages` artifacts for the complete format sets and their `SHA256SUMS` / `SHA256SUMS-Windows` manifests. Native desktop evidence is uploaded as `windows-verification-<architecture>`. These are Actions build artifacts; this report does not claim publication to a package store or a public GitHub release. See [Linux packaging](PACKAGING.md) and [Windows packaging](WINDOWS.md) for installation, native-helper requirements, architecture limits, and release steps.

## Historical 2.0 Ubuntu release evidence

The frozen 2.0 Ubuntu runtime passed UEFI, enforced Secure Boot, and BIOS integration gates in independent disposable Ubuntu virtual machines. Its source suite, native desktop checks, and Debian archive checks also passed. This evidence remains scoped to those tested runtime bytes and guests; it is not a fresh boot/reboot test of every later 2.1 package or of Windows.

The historical source test suite passed **275 tests with no skips**, including **21 native GTK interaction tests**, **51 production transaction tests**, and **28 repair tests**. The native source launcher passed `--help` and an eight-second demo startup. The Debian archive checker passed **27 source-matching files**, root ownership, and protected permissions. Python 3.10 syntax compatibility was checked for all 23 application/helper/build-script Python files; the app also ran on Python 3.12 in the Ubuntu 24.04 guests and Python 3.14 on the development desktop.

The historical frozen runtime fingerprint is SHA256 `6dcd777c1b05e1d2b5196517507779f076177623781afe3ab6d20246d7f37847`. It covers the sorted relative source paths and bytes of `helper.py`, all `uoo/*.py`, and `packaging/{launch.py,uoo-boot-manager,uoo-boot-helper,org.uoo.boot-manager.policy}`, with a NUL after each path and contents (20 files). It identifies the 2.0 VM-tested runtime, not a manifest of the current 2.1 source tree.

| Gate | Evidence |
| --- | --- |
| Typed GRUB settings, custom entries, images | `tests/test_models.py`, `tests/test_core.py`, and real GRUB generation and syntax validation in all three VMs |
| Transactions and recovery | `tests/test_production.py`, `tests/test_storage.py`, `tests/test_system.py`: partial-tool failures, durable interrupted-update recovery, stale state, file permissions, protected firmware identities, and semantic external GRUB environment recovery |
| Firmware management | Real UEFI and Secure Boot helper operations: order, inclusion, activation, create/delete/recreate, and one-time selection; current/scheduled boot protection and ID-reuse collisions covered by regressions |
| Repair and recovery | Signed shim/GRUB/MOK-manager EFI replacement with unchanged Secure Boot; actual BIOS `grub-install`; rollback and block-device preflight in `tests/test_repair.py` |
| Backups and portability | Create, inspect, export, import, restore, and delete in VMs; scoped restores, corrupt-record handling, bounded data, and private storage covered by unit tests |
| Native desktop | 21 GTK4/libadwaita interaction tests; all eight pages and dialogs captured at 1120×740 and 900×640 in light/dark mode; draft retention, authentication cancellation, editor validation, corrupt backups, unsupported layouts, and icon accessibility names checked; representative screenshots in `docs/images/` |
| Package trust | `scripts/check-package.py`: source-byte comparison, root ownership, protected module/helper permissions, fixed paths, dependencies, and absence of package maintenance scripts or host boot configuration files |
| Reproducibility | Checked official Ubuntu image; independent overlays and firmware variables; localhost-only SSH; `scripts/vm-integration.py` and optional CI matrix |

## Historical Ubuntu boot integration

The official Ubuntu Noble amd64 cloud image was checked against SHA256 `6a81c37564db9b1ee84e141922625e1d7c5b389b99bb3c572e0243607d5bb4d2`. Each guest used an independent writable overlay. The image has a separately mounted `/boot`; custom entries and signed EFI configuration therefore use the boot filesystem UUID and filesystem-relative paths.

All three guests ran Ubuntu 24.04.5 LTS, kernel `6.8.0-142-generic`, Python 3.12.3, PyGObject 3.48.2, GTK 4.14.5, libadwaita 1.5.0, GRUB `2.12-1ubuntu7.3`, efibootmgr 18, and polkit 124. Secure Boot repair used Ubuntu's signed GRUB package `1.202.5+2.12-1ubuntu7.3` and shim package `1.58+15.8-0ubuntu1`.

| Guest | Installed-helper operations before reboot | Actual boot result |
| --- | ---: | --- |
| UEFI | 53 | SSH returned; firmware `BootNext` and GRUB `next_entry` were consumed; `uoo_vm_check=1` appeared in the running kernel command line |
| Enforced Secure Boot | 53 | Signed EFI repair and actual boot succeeded; one-time choices were consumed; kernel marker verified; Secure Boot remained enabled after boot |
| BIOS | 33 | Reinstallation on the verified disposable `/dev/vda` succeeded; custom Linux one-time entry booted with `uoo_vm_custom=1`; GRUB one-time choice was consumed |

Each guest's original baseline was restored after the checks. This includes GRUB settings, managed entries, images, environment selections, and firmware state where applicable. Tests never use the development machine's disks as QEMU storage and never change its boot configuration.

These are the historical final repeat-run counts; earlier runs also passed 54, 54, and 34 operations respectively, including first-time baseline creation. The repeat reused those immutable baseline backups. Documentation and the installer checksum were finalized after these boot tests; the historical executable runtime fingerprint remained unchanged.

All three guests matched every one of the 20 frozen runtime files after testing. Their tested 2.0 installer SHA256 was `b1221ac5bda77002ca861d0538c7a242fe7a0e29a95f03aafb92e477403c5baf`. This historical hash is not the checksum of a current 2.1 package; use the manifest accompanying the downloaded build.

Local detailed evidence is retained in `/tmp/uoo-vm-live/`, `/tmp/uoo-vm-secure/`, and `/tmp/uoo-vm-bios/`: `integration-before.log`, `integration-after.log`, `serial.log`, and `vm.json`. These temporary files are session evidence; the runner can reproduce them on another machine.

## Reproduce on current 2.1 sources

These commands build and test the current version. A new VM execution records evidence for that current runtime; it does not reproduce the historical source fingerprint merely by using the same integration script.

```sh
bash scripts/build-deb.sh
python3 scripts/check-package.py dist/uoo-boot-manager_2.1.0_all.deb
GDK_BACKEND=x11 GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a \
  python3 -m unittest discover -s tests -v
python3 scripts/vm-integration.py --package dist/uoo-boot-manager_2.1.0_all.deb \
  --work-dir /tmp/uoo-check-uefi --exercise
python3 scripts/vm-integration.py --package dist/uoo-boot-manager_2.1.0_all.deb \
  --work-dir /tmp/uoo-check-secure --secure-boot --exercise
python3 scripts/vm-integration.py --package dist/uoo-boot-manager_2.1.0_all.deb \
  --work-dir /tmp/uoo-check-bios --boot-mode bios --exercise
```

Run the VM commands on a machine with QEMU, `qemu-img`, OVMF, and `xorriso`. KVM accelerates verification when available; the historical session used software emulation. Windows build and test commands are documented in [WINDOWS.md](WINDOWS.md); live Windows transaction checks require the explicit disposable-runner opt-in and are not part of the default test command.

## Support limits

The historical boot/reboot evidence covers disposable Ubuntu guests and simulated failure paths. Current CI package, desktop, and offline-store checks have the scope recorded above. Neither establishes compatibility with every physical firmware implementation, storage controller, filesystem, or power-loss scenario. Ubuntu repair refuses unsupported architectures, unverified targets, RAID/LVM/encrypted boot mappings, and layouts exceeding its rollback bounds. Signed UEFI repair uses the installed Ubuntu binaries and preserves firmware order and Secure Boot settings. Firmware deletion requires reconstructable local loader data.

External Btrfs GRUB environment behavior, failure injection, corrupt backup metadata, and cancelled authentication were exercised with simulations. Real system-generated administrator password prompts and screen-reader operation were not manually audited. The installed polkit policy, fixed helper path, privileged ownership checks, and real helper operations were verified in the guests.

The historical local delivery contained the Ubuntu `.deb`; its earlier portable build limitations do not describe the successful 2.1 multiformat CI run. Current Linux CI includes Fedora/openSUSE/Arch installation checks, clean-container AppImage startup, and Flatpak/Snap builds and host-bridge checks. Ubuntu remains the verified Linux boot-management backend. AppImage, Flatpak, and Snap require the matching installed native package helper for real changes; packaging for another distro or CPU does not establish bootloader repair support there.
