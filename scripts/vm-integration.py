#!/usr/bin/env python3
"""Boot a disposable Ubuntu UEFI VM and test an installed Uoo package.

Requires QEMU, qemu-img, OVMF, xorriso (or genisoimage), ssh, scp, and
ssh-keygen. Every guest disk write targets a temporary overlay; host firmware
and boot files are never opened. Downloads use Ubuntu's official cloud image
and its SHA256SUMS over HTTPS. Artifacts, keys, and logs remain outside source.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid


IMAGE_BASE = "https://cloud-images.ubuntu.com/noble/current/"
IMAGE_NAME = "noble-server-cloudimg-amd64.img"


GUEST_EXERCISE = r'''
import base64
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import time
import uuid
import zlib

operations = []
def call(action, **fields):
    started = time.monotonic()
    result = subprocess.run(['/usr/bin/uoo-boot-helper', '--stdin'], input=json.dumps({'action': action, **fields}),
                            capture_output=True, text=True, timeout=300)
    response = json.loads(result.stdout)
    if result.returncode or not response.get('ok'):
        raise AssertionError(f'{action} failed: {response}')
    operations.append({'action': action, 'seconds': round(time.monotonic()-started, 2)})
    print(action + ': ' + response.get('message', 'ok'), flush=True)
    return response

def snapshot():
    return call('snapshot')['snapshot']

def entry_present(state, selector):
    return any(item['selector'] == selector for item in state['entries'])

def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data) & 0xffffffff)

uefi = Path('/sys/firmware/efi').is_dir()
policy = subprocess.run(['pkaction', '--action-id', 'org.uoo.boot-manager.modify', '--verbose'],
                        check=True, capture_output=True, text=True).stdout
assert 'auth_admin' in policy and 'auth_admin_keep' not in policy, policy
recovery_file = Path('/var/tmp/uoo-vm-baseline.json')
original_baseline_file = Path('/var/tmp/uoo-vm-original-baseline.json')
if recovery_file.exists():
    call('restore', backup=json.loads(recovery_file.read_text())['backup'])
initial = snapshot()
assert initial['entries'] and initial['firmware']['available'] == uefi, initial
assert initial['diagnostics']['environment_supported'], initial['diagnostics']
if original_baseline_file.exists():
    baseline = json.loads(original_baseline_file.read_text())['backup']
else:
    baseline = call('create_backup', kind='all' if uefi else 'grub')['backup']
    original_baseline_file.write_text(json.dumps({'backup': baseline}))
if not recovery_file.exists():
    recovery_file.write_text(json.dumps({'backup': baseline}))
normal = next((item for item in initial['entries'] if item['title'] == 'Ubuntu'), initial['entries'][0])
selector = normal['selector']
original_options = initial['settings'].get('GRUB_CMDLINE_LINUX_DEFAULT', '')
marker = 'uoo_vm_check=1'
options = (original_options + ' ' + marker).strip()
call('apply_grub', changes={'GRUB_DEFAULT': selector, 'GRUB_TIMEOUT': 5, 'GRUB_RECORDFAIL_TIMEOUT': 5,
                           'GRUB_TIMEOUT_STYLE': 'menu', 'GRUB_SAVEDEFAULT': False,
                           'GRUB_CMDLINE_LINUX_DEFAULT': options, 'GRUB_DISABLE_OS_PROBER': True,
                           'GRUB_COLOR_NORMAL': 'light-gray/black', 'GRUB_COLOR_HIGHLIGHT': 'black/light-gray'})
state = snapshot()
assert state['settings']['GRUB_DEFAULT'] == selector and state['settings']['GRUB_TIMEOUT'] == '5'
assert marker in Path('/boot/grub/grub.cfg').read_text()
call('refresh_grub')
custom_id = 'uoo-' + uuid.uuid4().hex
if uefi:
    esp = next(item for item in state['devices'] if item['esp'] and item['mountpoint'] == '/boot/efi')
    assert Path('/boot/efi/EFI/ubuntu/shimx64.efi').is_file()
    custom = {'id': custom_id, 'title': 'Uoo integration chainloader', 'type': 'efi', 'enabled': True,
              'uuid': esp['uuid'], 'loader': '/EFI/ubuntu/shimx64.efi'}
else:
    root = next(item for item in state['devices'] if item['mountpoint'] == '/')
    boot = next((item for item in state['devices'] if item['mountpoint'] == '/boot'), root)
    prefix = '/' if boot['mountpoint'] == '/boot' else '/boot/'
    custom = {'id': custom_id, 'title': 'Uoo integration Linux', 'type': 'linux', 'enabled': True,
              'uuid': boot['uuid'], 'kernel': prefix + 'vmlinuz-' + os.uname().release,
              'initrd': prefix + 'initrd.img-' + os.uname().release,
              'options': 'root=UUID=' + root['uuid'] + ' ro console=tty1 console=ttyS0 ' + marker + ' uoo_vm_custom=1'}
original_custom = initial['custom_entries']
call('custom_entries', entries=original_custom + [custom])
assert entry_present(snapshot(), custom_id)
custom['title'] = 'Uoo integration chainloader revised'
call('custom_entries', entries=original_custom + [custom])
custom['enabled'] = False
call('custom_entries', entries=original_custom + [custom])
assert not entry_present(snapshot(), custom_id)
custom['enabled'] = True
call('custom_entries', entries=original_custom + [custom])
call('custom_entries', entries=original_custom)
assert not entry_present(snapshot(), custom_id)
call('custom_entries', entries=original_custom + [custom])

manual = call('create_backup', kind='grub')['backup']
assert call('backup_details', backup=manual)['configuration']['grub']['GRUB_TIMEOUT'] == 5
call('apply_grub', changes={'GRUB_TIMEOUT': 3})
assert snapshot()['settings']['GRUB_TIMEOUT'] == '3'
call('restore', backup=manual)
assert snapshot()['settings']['GRUB_TIMEOUT'] == '5'
exported = json.loads(call('export_backup', backup=manual)['data'])
call('apply_grub', changes={'GRUB_TIMEOUT': 6})
call('import_configuration', data=exported)
assert snapshot()['settings']['GRUB_TIMEOUT'] == '5'
call('delete_backup', backup=manual)
assert manual not in {item['id'] for item in call('list_backups')['backups']}
png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\0\x20\x40\x60\xff')) + png_chunk(b'IEND', b''))
call('apply_background', png=base64.b64encode(png).decode())
assert snapshot()['background']
call('clear_background')
assert not snapshot()['background']

if uefi:
    firmware = snapshot()['firmware']
    ubuntu = next(item for item in firmware['entries'] if item['label'].lower() == 'ubuntu')
    known = {item['id'] for item in firmware['entries']}
    created = call('create_uefi', label='Uoo integration copy', disk=esp['pkname'],
                   partition=int(esp['partn']), loader='/EFI/ubuntu/shimx64.efi')['snapshot']['firmware']
    new_ids = {item['id'] for item in created['entries']} - known
    assert len(new_ids) == 1
    new_id = new_ids.pop()
    call('set_active', entry=new_id, active=False)
    assert not next(item for item in snapshot()['firmware']['entries'] if item['id'] == new_id)['active']
    call('set_active', entry=new_id, active=True)
    assert next(item for item in snapshot()['firmware']['entries'] if item['id'] == new_id)['active']
    ordered = [ubuntu['id'], new_id] + [item for item in created['order'] if item not in (ubuntu['id'], new_id)]
    order_backup = call('create_backup', kind='uefi')['backup']
    call('apply_order', order=ordered)
    assert snapshot()['firmware']['order'] == ordered
    call('restore', backup=order_backup)
    assert snapshot()['firmware']['order'] == created['order']
    call('next_boot', entry=new_id)
    assert snapshot()['firmware']['next'] == new_id
    call('next_boot', entry='')
    assert snapshot()['firmware']['next'] == ''
    call('delete_uefi', entry=new_id, confirm=new_id)
    assert new_id not in {item['id'] for item in snapshot()['firmware']['entries']}

plan = call('repair_plan')['plan']
assert plan['supported'], plan
assert plan['mode'].lower() == ('uefi' if uefi else 'bios'), plan
call('repair', plan=plan['id'], confirm=plan['confirmation'])
if uefi:
    assert Path('/boot/efi/EFI/ubuntu/shimx64.efi').is_file()
    assert Path('/boot/efi/EFI/ubuntu/grubx64.efi').is_file()
else:
    assert Path('/boot/grub/i386-pc/core.img').is_file()
    assert Path('/boot/grub/i386-pc/normal.mod').is_file()
subprocess.run(['grub-script-check', '/boot/grub/grub.cfg'], check=True)
if uefi:
    call('apply_order', order=[ubuntu['id']] + [item for item in snapshot()['firmware']['order'] if item != ubuntu['id']])
call('grub_next_boot', entry=custom_id)
if uefi:
    call('next_boot', entry=ubuntu['id'])
state = snapshot()
assert state['grub_next'] == custom_id
if uefi:
    assert state['firmware']['next'] == ubuntu['id']
assert state['settings']['GRUB_TIMEOUT'] == '5'
for path in Path('/var/lib/uoo-boot-manager/backups').glob('*.json'):
    info = path.stat()
    assert info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, path
expected = {'baseline': baseline, 'default': selector, 'custom': custom_id, 'marker': marker,
            'firmware_next': ubuntu['id'] if uefi else '', 'uefi': uefi, 'operations': operations,
            'package_version': subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', 'uoo-boot-manager'], text=True)}
Path('/var/tmp/uoo-vm-expected.json').write_text(json.dumps(expected, indent=2))
print('UOO-INTEGRATION-PREPARED ' + json.dumps(expected), flush=True)
'''


GUEST_AFTER_REBOOT = r'''
import json
from pathlib import Path
import subprocess

expected = json.loads(Path('/var/tmp/uoo-vm-expected.json').read_text())
def call(action, **fields):
    result = subprocess.run(['/usr/bin/uoo-boot-helper', '--stdin'], input=json.dumps({'action': action, **fields}),
                            capture_output=True, text=True, timeout=300)
    response = json.loads(result.stdout)
    assert result.returncode == 0 and response.get('ok'), response
    return response

state = call('snapshot')['snapshot']
assert expected['marker'] in Path('/proc/cmdline').read_text(), 'Guest did not boot the changed GRUB configuration'
assert state['grub_next'] == '', 'GRUB next_entry was not consumed by the real boot'
if expected.get('uefi', True):
    assert state['firmware']['next'] == '', 'Firmware BootNext was not consumed by the real boot'
    assert state['firmware']['current'] == expected['firmware_next'], state['firmware']
else:
    assert not state['firmware']['available']
    assert 'uoo_vm_custom=1' in Path('/proc/cmdline').read_text(), 'The custom Linux one-time entry did not boot'
assert state['settings']['GRUB_DEFAULT'] == expected['default']
assert state['settings']['GRUB_TIMEOUT'] == '5'
assert any(item['selector'] == expected['custom'] for item in state['entries'])
subprocess.run(['grub-script-check', '/boot/grub/grub.cfg'], check=True)
call('restore', backup=expected['baseline'])
restored = call('snapshot')['snapshot']
assert expected['custom'] not in {item['selector'] for item in restored['entries']}
assert expected['marker'] not in restored['settings'].get('GRUB_CMDLINE_LINUX_DEFAULT', '')
Path('/var/tmp/uoo-vm-baseline.json').unlink(missing_ok=True)
print('UOO-INTEGRATION-PASSED ' + json.dumps({'package_version': expected['package_version'],
      'operations': len(expected['operations']), 'uefi_bootnext_consumed': expected.get('uefi', True), 'grub_next_entry_consumed': True,
      'live_kernel_command_line_verified': True, 'baseline_restored': True,
      'secure_boot': state['diagnostics']['system']['secure_boot']}), flush=True)
'''


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], check=True, text=True, **kwargs)


def guest_python(guest, source, log_path, timeout):
    try:
        result = guest.ssh("sudo /usr/bin/python3 -", input=source, capture_output=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stdout, stderr = exc.stdout or "", exc.stderr or ""
        stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        log_path.write_text(stdout + stderr)
        print(stdout, end="", flush=True)
        print(stderr, end="", file=sys.stderr, flush=True)
        raise
    log_path.write_text(result.stdout + result.stderr)
    print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)
    return result


def download(url, target):
    temporary = target.with_suffix(target.suffix + ".part")
    with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    temporary.replace(target)


def verified_image(cache, base):
    if not base.startswith("https://cloud-images.ubuntu.com/"):
        raise ValueError("Use an official HTTPS Ubuntu cloud-image directory")
    cache.mkdir(parents=True, exist_ok=True)
    sums, image = cache / "SHA256SUMS", cache / IMAGE_NAME
    if not sums.exists():
        print("Downloading official Ubuntu checksums", flush=True)
        download(base.rstrip("/") + "/SHA256SUMS", sums)
    match = re.search(r"^([a-f0-9]{64})\s+\*?" + re.escape(IMAGE_NAME) + r"$", sums.read_text(), re.M)
    if not match:
        raise ValueError("Ubuntu checksum list does not contain the AMD64 cloud image")
    if not image.exists():
        print("Downloading official Ubuntu UEFI cloud image", flush=True)
        download(base.rstrip("/") + "/" + IMAGE_NAME, image)
    with image.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != match[1]:
        raise ValueError("Ubuntu cloud-image SHA256 mismatch; remove the cached image/checksum pair and retry")
    return image.resolve(), actual


def executable(name, override=None):
    path = override or shutil.which(name)
    if not path or not Path(path).is_file():
        raise ValueError(f"Required executable unavailable: {name}")
    return str(Path(path).resolve())


def firmware(path, filename):
    if path:
        selected = Path(path)
    else:
        selected = Path("/usr/share/OVMF") / filename
    if not selected.is_file():
        raise ValueError(f"OVMF firmware unavailable: {selected}")
    return selected.resolve()


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


class Guest:
    def __init__(self, work, port):
        self.work, self.port = work, port
        self.common = ["-F", "/dev/null", "-i", str(work / "id_ed25519"),
                       "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                       "-o", "StrictHostKeyChecking=accept-new",
                       "-o", "UserKnownHostsFile=" + str(work / "known_hosts"),
                       "-o", "ConnectTimeout=10"]

    def ssh(self, command, **kwargs):
        return run(["ssh", *self.common, "-p", str(self.port), "ubuntu@127.0.0.1", command], **kwargs)

    def copy(self, source, destination):
        run(["scp", *self.common, "-P", str(self.port), source, "ubuntu@127.0.0.1:" + destination])

    def ready(self, timeout, process=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"QEMU exited; inspect {self.work / 'qemu.log'} and serial.log")
            try:
                self.ssh("true", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(3)
        raise TimeoutError(f"Guest SSH was not ready within {timeout}s; inspect {self.work / 'serial.log'}")

    def reboot(self, timeout):
        previous = self.ssh("cat /proc/sys/kernel/random/boot_id", capture_output=True).stdout.strip()
        try:
            self.ssh("sudo systemctl reboot", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 255:
                raise
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                current = self.ssh("cat /proc/sys/kernel/random/boot_id", capture_output=True, timeout=15).stdout.strip()
                if current and current != previous:
                    self.ssh("cloud-init status --wait", timeout=timeout)
                    return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            time.sleep(3)
        raise TimeoutError("Guest did not return with a new boot ID")


def seed(work):
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", work / "id_ed25519"])
    key = (work / "id_ed25519.pub").read_text().strip()
    (work / "user-data").write_text("#cloud-config\nusers:\n  - default\nssh_authorized_keys:\n  - " + key + "\nssh_pwauth: false\n")
    (work / "meta-data").write_text("instance-id: uoo-" + uuid.uuid4().hex + "\nlocal-hostname: uoo-integration\n")
    iso = work / "seed.iso"
    if shutil.which("xorriso"):
        run(["xorriso", "-as", "mkisofs", "-quiet", "-volid", "cidata", "-joliet", "-rock", "-o", iso,
             work / "user-data", work / "meta-data"])
    else:
        run([executable("genisoimage"), "-quiet", "-volid", "cidata", "-joliet", "-rock", "-output", iso,
             work / "user-data", work / "meta-data"])
    return iso


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, help="built .deb to install inside the guest")
    parser.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "uoo-vm-cache")
    parser.add_argument("--image-base", default=IMAGE_BASE, help="official Ubuntu directory; pin a dated directory for repeatable builds")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--attach", type=Path, help="reuse a running VM's work directory")
    parser.add_argument("--qemu")
    parser.add_argument("--qemu-img")
    parser.add_argument("--qemu-data", type=Path)
    parser.add_argument("--library-path", help="library directory for QEMU extracted without host installation")
    parser.add_argument("--firmware-code", type=Path)
    parser.add_argument("--firmware-vars", type=Path)
    parser.add_argument("--boot-mode", choices=["uefi", "bios"], default="uefi")
    parser.add_argument("--bios-rom", type=Path, help="SeaBIOS ROM when QEMU is extracted instead of installed")
    parser.add_argument("--secure-boot", action="store_true", help="use OVMF Secure Boot firmware and Microsoft-enrolled variable template")
    parser.add_argument("--accel", choices=["tcg", "kvm"], default="tcg")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--guest-script", type=Path, help="Python script executed as guest root after installation")
    parser.add_argument("--exercise", action="store_true", help="exercise real helper operations, supported bootloader repair, and an actual reboot")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--reboot", action="store_true", help="verify an actual reboot after the guest script")
    parser.add_argument("--keep-running", action="store_true", help="retain the guest for additional localhost SSH checks")
    args = parser.parse_args()
    process = None
    work = None
    retained = False
    try:
        if args.attach:
            work = args.attach.resolve()
            metadata = json.loads((work / "vm.json").read_text())
            guest = Guest(work, metadata["ssh_port"])
            boot_mode = metadata.get("boot_mode", "uefi")
            secure_boot = metadata.get("secure_boot", False)
        else:
            work = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="uoo-vm-"))
            work.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (work / "disk.qcow2").exists():
                raise ValueError("Work directory already contains a guest disk; use --attach or a new directory")
            image, checksum = verified_image(args.cache.resolve(), args.image_base)
            qemu = executable("qemu-system-x86_64", args.qemu)
            qemu_img = executable("qemu-img", args.qemu_img)
            env = os.environ.copy()
            if args.library_path:
                env["LD_LIBRARY_PATH"] = args.library_path
            run([qemu_img, "create", "-f", "qcow2", "-F", "qcow2", "-b", image, work / "disk.qcow2", "12G"], env=env)
            iso = seed(work)
            port = args.ssh_port or free_port()
            machine = "q35,smm=on,accel=" + args.accel if args.secure_boot else "q35,accel=" + args.accel
            command = [qemu, "-machine", machine, "-cpu", "max", "-m", "3072", "-smp", "2",
                       "-drive", f"file={work / 'disk.qcow2'},format=qcow2,if=virtio",
                       "-drive", f"file={iso},format=raw,if=virtio,readonly=on",
                       "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
                       "-device", "virtio-net-pci,netdev=net0,romfile=", "-display", "none", "-vga", "none",
                       "-serial", f"file:{work / 'serial.log'}", "-monitor", "none"]
            boot_mode = args.boot_mode
            secure_boot = args.secure_boot
            if secure_boot and boot_mode != "uefi":
                raise ValueError("Secure Boot requires --boot-mode uefi")
            if boot_mode == "uefi":
                code = firmware(args.firmware_code, "OVMF_CODE_4M.secboot.fd" if secure_boot else "OVMF_CODE_4M.fd")
                variables = work / "OVMF_VARS.fd"
                shutil.copyfile(firmware(args.firmware_vars, "OVMF_VARS_4M.ms.fd" if secure_boot else "OVMF_VARS_4M.fd"), variables)
                command.extend(["-drive", f"if=pflash,unit=0,format=raw,readonly=on,file={code}",
                                "-drive", f"if=pflash,unit=1,format=raw,file={variables}"])
                if secure_boot:
                    command.extend(["-global", "driver=cfi.pflash01,property=secure,value=on"])
            elif args.bios_rom:
                command.extend(["-bios", str(args.bios_rom.resolve())])
            if args.qemu_data:
                command.extend(["-L", str(args.qemu_data.resolve())])
            with (work / "qemu.log").open("w") as log:
                process = subprocess.Popen(command, env=env, stdout=log, stderr=log, start_new_session=True)
            metadata = {"pid": process.pid, "ssh_port": port, "work_dir": str(work), "image_sha256": checksum,
                        "image_source": args.image_base, "qemu_command": command, "boot_mode": boot_mode, "secure_boot": secure_boot}
            (work / "vm.json").write_text(json.dumps(metadata, indent=2) + "\n")
            guest = Guest(work, port)
        print(f"Waiting for disposable Ubuntu guest; logs: {work}", flush=True)
        guest.ready(args.timeout, process)
        boot_check = "test -d /sys/firmware/efi && echo UEFI-BOOT-VERIFIED" if boot_mode == "uefi" else "test ! -d /sys/firmware/efi && echo BIOS-BOOT-VERIFIED"
        guest.ssh("cloud-init status --wait && uname -r && " + boot_check, timeout=args.timeout)
        if secure_boot:
            guest.ssh("sudo python3 -c 'from pathlib import Path; p=Path(\"/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c\"); assert p.read_bytes()[4] == 1; print(\"SECURE-BOOT-ENABLED-VERIFIED\")'", timeout=30)
        if args.package and not args.skip_install:
            guest.copy(args.package.resolve(), "/home/ubuntu/uoo-test.deb")
            apt_options = "-o Acquire::ForceIPv4=true -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=1"
            boot_tools = "grub-efi-amd64-signed shim-signed" if boot_mode == "uefi" else "grub-pc-bin"
            guest.ssh(f"sudo apt-get {apt_options} update -qq && sudo env DEBIAN_FRONTEND=noninteractive apt-get {apt_options} install --no-install-recommends -y /home/ubuntu/uoo-test.deb efibootmgr {boot_tools} && sudo dpkg -i /home/ubuntu/uoo-test.deb", timeout=args.timeout)
        if args.guest_script:
            guest.ssh("sudo /usr/bin/python3 -", input=args.guest_script.read_text(), timeout=args.timeout)
        if args.exercise:
            guest_python(guest, GUEST_EXERCISE, work / "integration-before.log", args.timeout * 2)
        if args.reboot or args.exercise:
            guest.reboot(args.timeout)
            print("Guest reboot and SSH recovery verified", flush=True)
        if args.exercise:
            guest_python(guest, GUEST_AFTER_REBOOT, work / "integration-after.log", args.timeout)
        print("SSH: " + shlex.join(["ssh", *guest.common, "-p", str(guest.port), "ubuntu@127.0.0.1"]), flush=True)
        if args.keep_running:
            retained = True
            print("VM remains running; shut down with that SSH command followed by 'sudo poweroff'", flush=True)
        else:
            try:
                guest.ssh("sudo poweroff", timeout=30)
            except subprocess.CalledProcessError as exc:
                if exc.returncode != 255:
                    raise
            if process:
                process.wait(timeout=90)
    finally:
        if process is not None and process.poll() is None and not retained:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
