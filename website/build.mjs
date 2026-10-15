import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const app = path.dirname(root);
const dist = path.join(root, 'dist');
const icons = ['uoo.svg','favicon.svg','favicon.ico','apple-touch-icon.png','mask-icon.svg','site.webmanifest','android-chrome-192x192.png','android-chrome-512x512.png','maskable-512x512.png'];
await mkdir(path.join(root, 'assets/icons'), { recursive: true });
if (existsSync(path.join(app, 'assets/icons/uoo.svg'))) {
  for (const icon of icons) await cp(path.join(app, 'assets/icons', icon), path.join(root, 'assets/icons', icon));
  await cp(path.join(app, 'docs/images/grub.png'), path.join(root, 'assets/app-preview.png'));
}
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
for (const file of ['index.html','styles.css','app.js','robots.txt','sitemap.xml']) await cp(path.join(root, file), path.join(dist, file));
await cp(path.join(root, 'assets'), path.join(dist, 'assets'), { recursive: true });
const html = await readFile(path.join(dist, 'index.html'), 'utf8');
if (!html.includes('https://uoo.lolisoft.eu/')) throw new Error('Expected production canonical domain');
await writeFile(path.join(dist, '.nojekyll'), '');
console.log(`Landing page built: ${dist}`);
