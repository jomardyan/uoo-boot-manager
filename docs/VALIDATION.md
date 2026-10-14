# Uoo 2.0 release validation

Validation dates: 14–15 October 2026. Supported system management: Ubuntu 24.04+ with an existing GRUB installation; bootloader repair: verified x86-64 plain-disk layouts described in the operational manual. The implementation uses native GTK4/libadwaita and a separately authenticated, installed root-owned helper.

## Release evidence

The final frozen runtime passed the UEFI, enforced Secure Boot, and BIOS integration gates in independent disposable Ubuntu virtual machines. The source suite, native desktop checks, and Debian archive checks also passed. All requirements in `PRODUCTION.md` are satisfied within the stated platform and storage limits.

The final source test suite passed **275 tests with no skips**, including **21 native GTK interaction tests**, **51 production transaction tests**, and **28 repair tests**. The native source launcher passed `--help` and an eight-second demo startup. The Debian archive checker passed **27 source-matching files**, root ownership, and protected permissions. Python 3.10 syntax compatibility was checked for all 23 application/helper/build-script Python files; the app also ran on Python 3.12 in the Ubuntu 24.04 guests and Python 3.14 on the development desktop.

The frozen runtime fingerprint is SHA256 `6dcd777c1b05e1d2b5196517507779f076177623781afe3ab6d20246d7f37847`. It covers the sorted relative source paths and bytes of `helper.py`, all `uoo/*.py`, and `packaging/{launch.py,uoo-boot-manager,uoo-boot-helper,org.uoo.boot-manager.policy}`, with a NUL after each path and contents (20 files). Documentation and checksums may be finalized after boot tests without changing these executable files.

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

## Boot integration

The official Ubuntu Noble amd64 cloud image was checked against SHA256 `6a81c37564db9b1ee84e141922625e1d7c5b389b99bb3c572e0243607d5bb4d2`. Each guest used an independent writable overlay. The image has a separately mounted `/boot`; custom entries and signed EFI configuration therefore use the boot filesystem UUID and filesystem-relative paths.

All three guests ran Ubuntu 24.04.5 LTS, kernel `6.8.0-142-generic`, Python 3.12.3, PyGObject 3.48.2, GTK 4.14.5, libadwaita 1.5.0, GRUB `2.12-1ubuntu7.3`, efibootmgr 18, and polkit 124. Secure Boot repair used Ubuntu's signed GRUB package `1.202.5+2.12-1ubuntu7.3` and shim package `1.58+15.8-0ubuntu1`.

| Guest | Installed-helper operations before reboot | Actual boot result |
| --- | ---: | --- |
| UEFI | 53 | SSH returned; firmware `BootNext` and GRUB `next_entry` were consumed; `uoo_vm_check=1` appeared in the running kernel command line |
| Enforced Secure Boot | 53 | Signed EFI repair and actual boot succeeded; one-time choices were consumed; kernel marker verified; Secure Boot remained enabled after boot |
| BIOS | 33 | Reinstallation on the verified disposable `/dev/vda` succeeded; custom Linux one-time entry booted with `uoo_vm_custom=1`; GRUB one-time choice was consumed |

Each guest's original baseline was restored after the checks. This includes GRUB settings, managed entries, images, environment selections, and firmware state where applicable. Tests never use the development machine's disks as QEMU storage and never change its boot configuration.

These are the final repeat-run counts; earlier runs also passed 54, 54, and 34 operations respectively, including first-time baseline creation. The final repeat reused those immutable baseline backups. Documentation and the installer checksum were finalized after these boot tests; the executable runtime fingerprint remained unchanged.

All three guests matched every one of the 20 frozen runtime files after testing. Their tested installer SHA256 was `b1221ac5bda77002ca861d0538c7a242fe7a0e29a95f03aafb92e477403c5baf`; the delivered archive includes the finalized documentation, with identical runtime bytes and its own verified checksum in `dist/SHA256SUMS`.

Local detailed evidence is retained in `/tmp/uoo-vm-live/`, `/tmp/uoo-vm-secure/`, and `/tmp/uoo-vm-bios/`: `integration-before.log`, `integration-after.log`, `serial.log`, and `vm.json`. These temporary files are session evidence; the runner can reproduce them on another machine.

## Reproduce

```sh
bash scripts/build-deb.sh
python3 scripts/check-package.py dist/uoo-boot-manager_2.0.0_all.deb
GDK_BACKEND=x11 GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a \
  python3 -m unittest discover -s tests -v
python3 scripts/vm-integration.py --package dist/uoo-boot-manager_2.0.0_all.deb \
  --work-dir /tmp/uoo-check-uefi --exercise
python3 scripts/vm-integration.py --package dist/uoo-boot-manager_2.0.0_all.deb \
  --work-dir /tmp/uoo-check-secure --secure-boot --exercise
python3 scripts/vm-integration.py --package dist/uoo-boot-manager_2.0.0_all.deb \
  --work-dir /tmp/uoo-check-bios --boot-mode bios --exercise
```

Run the VM commands on a machine with QEMU, `qemu-img`, OVMF, and `xorriso`. KVM accelerates verification when available; this session used software emulation.

## Support limits

The evidence covers disposable Ubuntu guests and simulated failure paths. It does not establish compatibility with every physical firmware implementation, storage controller, filesystem, or power-loss scenario. Repair refuses unsupported architectures, unverified targets, RAID/LVM/encrypted boot mappings, and layouts exceeding its rollback bounds. Signed UEFI repair uses the installed Ubuntu binaries and preserves firmware order and Secure Boot settings. Firmware deletion requires reconstructable local loader data.

External Btrfs GRUB environment behavior, failure injection, corrupt backup metadata, and cancelled authentication were exercised with simulations. Real system-generated administrator password prompts and screen-reader operation were not manually audited. The installed polkit policy, fixed helper path, privileged ownership checks, and real helper operations were verified in the guests.

The delivered release artifact is the verified Ubuntu `.deb`, with its checksum in `dist/SHA256SUMS`. The repository includes optional RPM and AppImage build workflows; those formats were not rebuilt for this final release. Earlier portable artifacts were preserved under `build/previous-artifacts/` and are excluded from delivery. The earlier AppImage used newer host libraries and did not satisfy the Ubuntu 24.04 compatibility baseline.

Ubuntu remains the supported boot-management backend. An optional AppImage requires the matching installed system package helper for real changes. Hosted GitHub Actions and Fedora/clean-container runs were not executed in this local session; the workflow and reproducible instructions are provided without claiming those executions.
