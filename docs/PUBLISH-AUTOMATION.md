# Automated platform publishing

The **Publish to distribution platforms** workflow
([`publish-platforms.yml`](../.github/workflows/publish-platforms.yml)) uploads an
already published GitHub release to the Ubuntu PPA, Snap Store, WinGet, and AUR.
It is started by hand (**Actions → Publish to distribution platforms → Run
workflow**) because every upload is hard to undo. Nothing publishes unless you
tick that platform, and each publishing job waits for approval.

Not automated: Flathub (first submission is a reviewed pull request; afterwards
Flathub's bot proposes updates) and the Microsoft Store (needs a code-signing
certificate and a Partner Center submission first).

## One-time repository setup

1. **Settings → Environments → New environment** named `release`. Enable
   **Required reviewers** (yourself) and restrict secrets to this environment.
2. Add these **environment secrets** to `release`:

| Secret | Used by | How to create it |
| --- | --- | --- |
| `PPA_GPG_PRIVATE_KEY` | PPA | `gpg --export-secret-keys --armor <FINGERPRINT> \| base64 -w0`. Prefer a dedicated signing subkey registered on Launchpad. |
| `PPA_GPG_PASSPHRASE` | PPA | The key's passphrase |
| `PPA_GPG_FINGERPRINT` | PPA | Full primary fingerprint, no spaces |
| `SNAPCRAFT_STORE_CREDENTIALS` | Snap | `snapcraft export-login --snaps uoo-boot-manager --channels edge,beta,candidate,stable --acls package_access,package_push,package_release --expires <ISO date> -` |
| `WINGET_CREATE_GITHUB_TOKEN` | WinGet | Classic token with `public_repo`, created at github.com/settings/tokens |
| `AUR_SSH_PRIVATE_KEY` | AUR | A dedicated key (`ssh-keygen -t ed25519`); register its `.pub` on the AUR account |

Rotate the Snap credentials before their expiry date.

## Prerequisites per platform

- **PPA:** `debian/changelog` must start with `uoo-boot-manager (X.Y.Z-0ppa1~noble1) noble;`
  for the release. Re-uploading the same version needs a manual build with
  `--revision` and `--orig` (see [PUBLISHING.md](PUBLISHING.md)).
- **Snap:** classic confinement must be approved first. Run with channel `edge`,
  test, then run again with `stable`.
- **WinGet:** the x64 install check in [WINGET.md](WINGET.md) must pass. The
  workflow opens a pull request; Microsoft's review decides acceptance.
- **AUR:** create the AUR account and register the key. The first run creates
  the `uoo-boot-manager-bin` package. Test the package on Arch first
  (`makepkg -si` with the files from `scripts/build-aur.py`).

## Running a release

1. Publish the GitHub release (existing `release: vX.Y.Z` flow).
2. Run the workflow with that version and the platforms you want.
3. Approve the `release` environment prompt.
4. Check each platform's page (Launchpad builds, Snap revisions, the WinGet PR,
   the AUR package) before announcing availability.
