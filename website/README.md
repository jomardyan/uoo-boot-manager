# Uoo landing page

A static Fluent-inspired product page for `https://uoo.lolisoft.eu/`. It has no
server, package-manager dependencies, external fonts, analytics, or cookies.
Both platform panels show real demo screenshots. The Windows capture comes
from the successful x64 native-desktop job in GitHub Actions run 38809943611;
the Ubuntu capture comes from the local native GTK demo. The download picker
links to all 14 package assets from the 2.2.1 GitHub release: Windows x64/ARM64
installers and portable ZIPs, Debian, Fedora/openSUSE RPMs, Arch, and x64/ARM64
AppImage, Flatpak, and Snap builds. Each selection includes its installation
guide and the matching Linux or Windows SHA256 manifest.

Build and preview from the application repository root:

```sh
make website        # or: node website/build.mjs
unzip website/dist/uoo-website.zip -d /tmp/uoo-site
python3 -m http.server 4173 --bind 127.0.0.1 --directory /tmp/uoo-site
```

The build writes a single archive, `website/dist/uoo-website.zip`, instead of
a folder. For your hosting, extract **the contents of that ZIP** into the document
root for `uoo.lolisoft.eu`. The page works on ordinary static web hosting;
there is no Node.js process to run in production. Configure the domain and
HTTPS through your existing provider.

The build copies the current application icons and screenshot into the site.
The checked-in `assets/` copies also allow the website to build independently
of the desktop source. Set the correct release version in `index.html` and
`app.js` when preparing future releases.

## Documentation pages and SEO

`website/docs/` holds the static guides (getting started, Windows 11, Ubuntu,
encrypted disks, FAQ). Each page is a complete HTML file using root-relative
paths (`/styles.css`, `/docs/...`), so serve the site from the domain root.
When you add or rename a page, also update `sitemap.xml` and the guide lists.
`make website` runs the checker (`scripts/check-website.py`), which fails on
a missing or duplicate title, a missing meta description, a canonical URL that
does not match the page path, invalid JSON-LD, broken links or anchors, and a
sitemap that does not list exactly the indexable pages. `404.html` is marked
`noindex`. After deploying, submit `https://uoo.lolisoft.eu/sitemap.xml` in
Google Search Console and Bing Webmaster Tools.
