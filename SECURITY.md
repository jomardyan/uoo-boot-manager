# Security policy

Uoo Boot Manager changes boot configuration through a root-owned helper
(polkit on Ubuntu, UAC on Windows). A flaw in that helper, in backup or import
handling, or in the installers can affect whether a computer starts, so please
report security problems privately.

## Reporting a vulnerability

1. Preferred: open a private report at
   <https://github.com/jomardyan/uoo-boot-manager/security/advisories/new>
   (**Security → Report a vulnerability**).
2. If you cannot use GitHub, email **jomardyan@outlook.com** with the subject
   `Uoo security`.

Please include the affected version, platform, steps to reproduce, and the
impact you expect. Do not open a public issue or post exploit details before a
fix is available. This is a single-maintainer project, so replies are
best-effort; reports are acknowledged as soon as practical.

**Never include a BitLocker recovery password, LUKS passphrase, or private key
in a report, even for your own machine.** Describe the setup instead.

## Supported versions

Only the latest release receives security fixes. Check
[GitHub Releases](https://github.com/jomardyan/uoo-boot-manager/releases).

## In scope

- Privilege escalation or arbitrary code execution through the helper
  (`uoo-boot-helper`, `Uoo.BootHelper.exe`), its polkit policy, or its request
  validation.
- Unsafe handling of backups, journals, or imported configuration files,
  including path traversal and writes outside the documented locations.
- Installers or packages that install writable code into privileged paths.
- Tampering with release packages or their SHA256 manifests.

## Out of scope

- Problems that need an attacker who already has administrator or root rights.
- Boot failures or BitLocker/LUKS recovery prompts after a change that Uoo
  documented as risky; these are bugs or support questions, not vulnerabilities
  (open a normal issue and read the encrypted-disk warning in the README).
- Unsigned Windows builds triggering SmartScreen warnings. This is a known
  limitation.

Fixes are published in a new release; credit is given in the release notes
unless you prefer to stay anonymous.
