"""Static deployment checks and browser-independent download control behavior."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_check", ROOT / "scripts/check-website.py")
website_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(website_check)


class LandingPageTests(unittest.TestCase):
    def test_source_page_references_existing_assets(self):
        website_check.check(ROOT / "website")

    def test_docs_pages_are_indexable_and_listed_in_sitemap(self):
        sitemap = (ROOT / "website/sitemap.xml").read_text()
        for guide in ("getting-started", "windows", "ubuntu", "encrypted-disks", "troubleshooting", "faq"):
            self.assertTrue((ROOT / "website/docs" / guide / "index.html").is_file())
            self.assertIn(f"https://uoo.lolisoft.eu/docs/{guide}/", sitemap)

    def test_checker_rejects_wrong_canonical_and_broken_links(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            shutil.copytree(ROOT / "website", site, ignore=shutil.ignore_patterns("dist"))
            page = site / "docs/faq/index.html"
            original = page.read_text()
            page.write_text(original.replace('href="https://uoo.lolisoft.eu/docs/faq/">', 'href="https://uoo.lolisoft.eu/docs/other/">', 1))
            with self.assertRaisesRegex(ValueError, "canonical"):
                website_check.check(site)
            page.write_text(original.replace('href="/docs/windows/"', 'href="/docs/missing/"', 1))
            with self.assertRaisesRegex(ValueError, "Missing or invalid"):
                website_check.check(site)
            page.write_text(original)
            (site / "docs/unlisted").mkdir()
            (site / "docs/unlisted/index.html").write_text(original)
            with self.assertRaises(ValueError):
                website_check.check(site)

    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript behavior checks")
    def test_platform_downloads_and_preview_controls(self):
        script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const make = () => ({ listeners: {}, hidden:false, textContent:'', href:'', attrs:{},
  addEventListener(type, fn) { this.listeners[type] = fn; },
  setAttribute(name,value) { this.attrs[name] = value; },
  classList:{toggle() {}} });
const tabs = ['windows','ubuntu'].map(platform => Object.assign(make(), {dataset:{platform}}));
const document = {
  querySelector(name) { if (!elements.has(name)) elements.set(name,make()); return elements.get(name); },
  querySelectorAll() { return tabs; }
};
vm.runInNewContext(fs.readFileSync('website/app.js','utf8'), { document });
const change = elements.get('#platform-select').listeners.change;
for (const [value, suffix, manifest] of [
  ['windows-x64','windows-x64-setup.exe','SHA256SUMS-Windows'],
  ['windows-arm64','windows-arm64-setup.exe','SHA256SUMS-Windows'],
  ['ubuntu','_all.deb','SHA256SUMS'],
  ['appimage-x64','x86_64.AppImage','SHA256SUMS'],
  ['appimage-arm64','aarch64.AppImage','SHA256SUMS']
]) {
  change({target:{value}});
  assert(elements.get('#download-button').href.endsWith(suffix));
  assert(elements.get('#checksum-link').href.endsWith(manifest));
  assert(elements.get('#download-button').href.includes('/v2.2.1/'));
}
tabs[1].listeners.click();
assert.equal(elements.get('#windows-preview').hidden,true);
assert.equal(elements.get('#ubuntu-preview').hidden,false);
assert.equal(tabs[1].attrs['aria-pressed'],'true');
tabs[0].listeners.click();
assert.equal(elements.get('#windows-preview').hidden,false);
assert.equal(tabs[0].attrs['aria-pressed'],'true');
console.log('Download selection and platform previews verified.');
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
