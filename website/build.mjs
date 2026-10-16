import { cp, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateRawSync } from 'node:zlib';

const root = path.dirname(fileURLToPath(import.meta.url));
const app = path.dirname(root);
const dist = path.join(root, 'dist');
const archive = path.join(dist, 'uoo-website.zip');
const crcTable = Array.from({ length: 256 }, (_, n) => {
  for (let k = 0; k < 8; k++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
const crc32 = data => {
  let crc = 0xffffffff;
  for (const byte of data) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
};
const listFiles = async (dir, prefix = '') => {
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) files.push(...await listFiles(path.join(dir, entry.name), name + '/'));
    else files.push(name);
  }
  return files.sort();
};
// Minimal deterministic ZIP writer (deflate, fixed 2000-01-01 timestamps) so the build needs no external tools.
const zipDirectory = async dir => {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const name of await listFiles(dir)) {
    const data = await readFile(path.join(dir, name));
    const packed = deflateRawSync(data);
    const method = packed.length < data.length ? 8 : 0;
    const body = method ? packed : data;
    const label = Buffer.from(name);
    const common = Buffer.alloc(26);
    common.writeUInt16LE(20, 0);
    common.writeUInt16LE(0x0800, 2);
    common.writeUInt16LE(method, 4);
    common.writeUInt16LE(0, 6);
    common.writeUInt16LE(0x2821, 8);
    common.writeUInt32LE(crc32(data), 10);
    common.writeUInt32LE(body.length, 14);
    common.writeUInt32LE(data.length, 18);
    common.writeUInt16LE(label.length, 22);
    const local = Buffer.concat([Buffer.from([0x50, 0x4b, 3, 4]), common, label, body]);
    const entry = Buffer.alloc(14);
    entry.writeUInt32LE(offset, 10);
    central.push(Buffer.concat([Buffer.from([0x50, 0x4b, 1, 2]), Buffer.from([20, 0]), common.subarray(0, 26), entry, label]));
    locals.push(local);
    offset += local.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(central.length, 8);
  end.writeUInt16LE(central.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
};
const icons = ['uoo.svg','favicon.svg','favicon.ico','apple-touch-icon.png','mask-icon.svg','site.webmanifest','android-chrome-192x192.png','android-chrome-512x512.png','maskable-512x512.png'];
await mkdir(path.join(root, 'assets/icons'), { recursive: true });
if (existsSync(path.join(app, 'assets/icons/uoo.svg'))) {
  for (const icon of icons) await cp(path.join(app, 'assets/icons', icon), path.join(root, 'assets/icons', icon));
  await cp(path.join(app, 'docs/images/grub.png'), path.join(root, 'assets/app-preview.png'));
}
const stage = await mkdtemp(path.join(tmpdir(), 'uoo-website-'));
try {
  for (const file of ['index.html','styles.css','app.js','robots.txt','sitemap.xml']) await cp(path.join(root, file), path.join(stage, file));
  await cp(path.join(root, 'assets'), path.join(stage, 'assets'), { recursive: true });
  const html = await readFile(path.join(stage, 'index.html'), 'utf8');
  if (!html.includes('https://uoo.lolisoft.eu/')) throw new Error('Expected production canonical domain');
  await writeFile(path.join(stage, '.nojekyll'), '');
  await rm(dist, { recursive: true, force: true });
  await mkdir(dist, { recursive: true });
  await writeFile(archive, await zipDirectory(stage));
} finally {
  await rm(stage, { recursive: true, force: true });
}
console.log(`Landing page built: ${archive}`);
