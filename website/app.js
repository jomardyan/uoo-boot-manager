const releaseBase = 'https://github.com/jomardyan/uoo-boot-manager/releases/download/v2.2.0/';
const documentation = 'https://github.com/jomardyan/uoo-boot-manager/blob/main/';
const downloads = {
  'windows-x64': {file:'Uoo-Boot-Manager-2.2.0-windows-x64-setup.exe',label:'Download for Windows',detail:'Version 2.2.0 · x64 installer · .NET runtime included',note:'Windows packages are currently unsigned. Install Uoo before using administrator actions.',guide:'docs/WINDOWS.md',checksums:'SHA256SUMS-Windows'},
  'windows-arm64': {file:'Uoo-Boot-Manager-2.2.0-windows-arm64-setup.exe',label:'Download for Windows ARM64',detail:'Version 2.2.0 · ARM64 installer · .NET runtime included',note:'For Windows on Arm devices. Packages are currently unsigned. Install Uoo before using administrator actions.',guide:'docs/WINDOWS.md',checksums:'SHA256SUMS-Windows'},
  'ubuntu': {file:'uoo-boot-manager_2.2.0_all.deb',label:'Download for Ubuntu',detail:'Version 2.2.0 · architecture-independent Debian package',note:'Ubuntu 24.04+ with GTK4 and libadwaita 1.5+. GRUB and UEFI features depend on the detected system.',guide:'README.md#ubuntu',checksums:'SHA256SUMS'},
  'appimage-x64': {file:'Uoo_Boot_Manager-2.2.0-x86_64.AppImage',label:'Download AppImage',detail:'Version 2.2.0 · x64 AppImage · glibc 2.39+',note:'Demo works independently. System changes require the matching native host package and administrator helper.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'},
  'appimage-arm64': {file:'Uoo_Boot_Manager-2.2.0-aarch64.AppImage',label:'Download AppImage ARM64',detail:'Version 2.2.0 · ARM64 AppImage · glibc 2.39+',note:'Demo works independently. System changes require the matching native host package and administrator helper.',guide:'docs/PACKAGING.md',checksums:'SHA256SUMS'}
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
    document.querySelector('#preview-caption').textContent = windows ? 'Windows workflow illustration. Available options depend on your system.' : 'The native Ubuntu desktop, shown in demo mode. No system changes are made.';
    for (const other of document.querySelectorAll('[data-platform]')) {
      const selected = other === tab;
      other.classList.toggle('selected', selected);
      other.setAttribute('aria-pressed', String(selected));
    }
  });
}
