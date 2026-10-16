# Contributing

Thanks for helping with Uoo Boot Manager. The app edits boot settings, so
changes are reviewed with extra care for safety over features.

## Ground rules

- **Never reboot or change the boot configuration of a machine you care about**
  while developing. Use `--demo`, which uses sample data, or a disposable
  virtual machine for real operations.
- The desktop runs as a normal user; privileged work belongs only in the fixed,
  root-owned helper. Do not add code paths that run user-supplied shell text or
  load user-editable code as root.
- Validate every request the helper receives, preserve unrelated settings, back
  up before writing, and keep rollback and journal behavior intact.
- Keep Windows and Linux behavior documented honestly, including limits. Do not
  claim support that CI does not verify.
- Never put recovery keys, passphrases, tokens, or private keys in issues,
  commits, or logs.

## Set up and run

Ubuntu (GTK4/libadwaita) from source:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m uoo --demo       # or: make demo
make test                   # Python unit tests
make test-gtk               # GTK tests (needs xvfb and libadwaita 1.5+)
make check                  # build the .deb, validate it, then run tests
```

Windows (.NET 10 SDK, PowerShell 7):

```powershell
dotnet run --project windows/Uoo.Tests -c Release
dotnet run --project windows/Uoo.Platform.Tests -c Release
```

See [Linux packaging](docs/PACKAGING.md) and [Windows build](docs/WINDOWS.md#build)
for package builds, and [production requirements](docs/PRODUCTION.md) for what a
release must prove.

## Pull requests

1. Open an issue first for larger changes, or describe the problem in the PR.
2. Add or update tests for behavior changes, including failure paths.
3. Run `make test` (and the Windows tests if you touched `windows/`).
4. Keep PRs focused; update the README or docs when behavior changes.
5. Describe how you tested, and on which OS and boot mode (UEFI or BIOS).

**Continuous integration:** GitHub Actions workflows do not run automatically on pushes or pull
requests. Run the tests locally (`make test`), and maintainers start the **Linux** and **Windows**
build workflows manually (**Actions → Run workflow**) when a change needs package or native
verification.

## Reporting security issues

Do not use public issues. See [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contribution is licensed under the
repository's [MIT License](LICENSE).
