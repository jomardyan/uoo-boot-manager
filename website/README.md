# Uoo landing page

A static Fluent-inspired product page for `https://uoo.lolisoft.eu/`. It has no
server, package-manager dependencies, external fonts, analytics, or cookies.
The Windows panel is labeled as an illustration; the Ubuntu panel is a real
demo screenshot. Download links point to the 2.2.0 GitHub release assets.

Build and preview from the application repository root:

```sh
node website/build.mjs
python3 -m http.server 4173 --bind 127.0.0.1 --directory website/dist
```

For your hosting, upload **the contents of `website/dist/`** to the document
root for `uoo.lolisoft.eu`. The page works on ordinary static web hosting;
there is no Node.js process to run in production. Configure the domain and
HTTPS through your existing provider.

The build copies the current application icons and screenshot into the site.
The checked-in `assets/` copies also allow the website to build independently
of the desktop source. Set the correct release version in `index.html` and
`app.js` when preparing future releases.
