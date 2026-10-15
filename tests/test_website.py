"""Static deployment checks and browser-independent download control behavior."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_check", ROOT / "scripts/check-website.py")
website_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(website_check)


class LandingPageTests(unittest.TestCase):
    def test_source_page_references_existing_assets(self):
        website_check.check(ROOT / "website")

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
