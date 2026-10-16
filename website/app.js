const releaseBase = 'https://github.com/jomardyan/uoo-boot-manager/releases/download/v2.2.1/';
const documentation = 'https://github.com/jomardyan/uoo-boot-manager/blob/main/';
const downloads = {
  'windows-x64': {file:'Uoo-Boot-Manager-2.2.1-windows-x64-setup.exe',label:'Download for Windows',detail:'Version 2.2.1 · x64 installer · .NET runtime included',note:'Windows packages are currently unsigned. Install Uoo before using administrator actions.',guide:'docs/WINDOWS.md',checksums:'SHA256SUMS-Windows'},
  'windows-arm64': {file:'Uoo-Boot-Manager-2.2.1-windows-arm64-setup.exe',label:'Download for Windows ARM64',detail:'Version 2.2.1 · ARM64 installer · .NET runtime included',note:'For Windows on Arm devices. Packages are currently unsigned. Install Uoo before using administrator actions.',guide:'docs/WINDOWS.md',checksums:'SHA256SUMS-Windows'},
  'windows-portable-x64': {file:'Uoo-Boot-Manager-2.2.1-windows-x64.zip',label:'Download Windows ZIP',detail:'Version 2.2.1 · x64 portable ZIP · .NET runtime included',note:'Unsigned portable build for demo and read-only inspection. Install Uoo before using administrator actions.',guide:'docs/WINDOWS.md',checksums:'SHA256SUMS-Windows'},
  'windows-portable-arm64': {file:'Uoo-Boot-Manager-2.2.1-windows-arm64.zip',label:'Download Windows ARM64 ZIP',detail:'Version 2.2.1 · ARM64 portable ZIP · .NET runtime included',note:'Unsigned portable build for Windows on Arm. Demo and read-only inspection work from a user folder; administrator actions require an installed Uoo.',guide:'docs/WINDOWS.md',checksums:'SHA256SUMS-Windows'},
  'ubuntu': {file:'uoo-boot-manager_2.2.1_all.deb',label:'Download for Ubuntu',detail:'Version 2.2.1 · architecture-independent Debian package',note:'Ubuntu 24.04+ with GTK4 and libadwaita 1.5+. GRUB and UEFI features depend on the detected system.',guide:'README.md#ubuntu',checksums:'SHA256SUMS'},
  'fedora': {file:'uoo-boot-manager-2.2.1-1.noarch.rpm',label:'Download Fedora RPM',detail:'Version 2.2.1 · architecture-independent Fedora RPM',note:'Requires system Python, GTK4, and libadwaita. Boot management support depends on the detected system; Ubuntu remains the verified GRUB backend.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'},
  'opensuse': {file:'uoo-boot-manager-2.2.1-1.opensuse.noarch.rpm',label:'Download openSUSE RPM',detail:'Version 2.2.1 · architecture-independent openSUSE RPM',note:'For openSUSE Tumbleweed with native Python and GTK dependencies. Boot management support depends on the detected system; Ubuntu remains the verified GRUB backend.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'},
  'arch': {file:'uoo-boot-manager-2.2.1-1-any.pkg.tar.zst',label:'Download Arch package',detail:'Version 2.2.1 · architecture-independent Arch package',note:'For Arch Linux and compatible Manjaro systems. Boot management support depends on the detected system; Ubuntu remains the verified GRUB backend.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'},
  'appimage-x64': {file:'Uoo_Boot_Manager-2.2.1-x86_64.AppImage',label:'Download AppImage',detail:'Version 2.2.1 · x64 AppImage · glibc 2.39+',note:'Demo works independently. System changes require the matching native host package and administrator helper.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'},
  'appimage-arm64': {file:'Uoo_Boot_Manager-2.2.1-aarch64.AppImage',label:'Download AppImage ARM64',detail:'Version 2.2.1 · ARM64 AppImage · glibc 2.39+',note:'Demo works independently. System changes require the matching native host package and administrator helper.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'},
  'flatpak-x64': {file:'uoo-boot-manager-2.2.1-x86_64.flatpak',label:'Download Flatpak',detail:'Version 2.2.1 · x64 Flatpak · GNOME 50 runtime',note:'Install the local Flatpak bundle with the packaging guide. System mode requires the matching native host package and administrator helper.',guide:'docs/PACKAGING.md#flatpak-and-snap',checksums:'SHA256SUMS'},
  'flatpak-arm64': {file:'uoo-boot-manager-2.2.1-aarch64.flatpak',label:'Download Flatpak ARM64',detail:'Version 2.2.1 · ARM64 Flatpak · GNOME 50 runtime',note:'Install the local Flatpak bundle with the packaging guide. System mode requires the matching native host package and administrator helper.',guide:'docs/PACKAGING.md#flatpak-and-snap',checksums:'SHA256SUMS'},
  'snap-x64': {file:'uoo-boot-manager_2.2.1_amd64.snap',label:'Download Snap',detail:'Version 2.2.1 · x64 Snap · classic confinement',note:'Unsigned Snap for local installation; follow the packaging guide. System changes require the matching native host package and administrator helper.',guide:'docs/PACKAGING.md#flatpak-and-snap',checksums:'SHA256SUMS'},
  'snap-arm64': {file:'uoo-boot-manager_2.2.1_arm64.snap',label:'Download Snap ARM64',detail:'Version 2.2.1 · ARM64 Snap · classic confinement',note:'Unsigned Snap for local installation; follow the packaging guide. System changes require the matching native host package and administrator helper.',guide:'docs/PACKAGING.md#flatpak-and-snap',checksums:'SHA256SUMS'}
};
document.querySelector('#platform-select').addEventListener('change', event => {
  const selected = downloads[event.target.value];
  if (!selected) return;
  document.querySelector('#download-button').href = releaseBase + selected.file;
  document.querySelector('#download-label').textContent = selected.label;
  document.querySelector('#download-detail').textContent = selected.detail;
  document.querySelector('#download-note').textContent = selected.note;
  document.querySelector('#install-guide').href = documentation + selected.guide;
  document.querySelector('#checksum-link').href = releaseBase + selected.checksums;
});
for (const tab of document.querySelectorAll('[data-platform]')) {
  tab.addEventListener('click', () => {
    const windows = tab.dataset.platform === 'windows';
    document.querySelector('#windows-preview').hidden = !windows;
    document.querySelector('#ubuntu-preview').hidden = windows;
    document.querySelector('#preview-caption').textContent = windows ? 'The native Windows desktop, shown in demo mode. Available options depend on your system.' : 'The native Ubuntu desktop, shown in demo mode. No system changes are made.';
    for (const other of document.querySelectorAll('[data-platform]')) {
      const selected = other === tab;
      other.classList.toggle('selected', selected);
      other.setAttribute('aria-pressed', String(selected));
    }
  });
}
