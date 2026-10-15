#!/usr/bin/env python3
"""Check the static landing page's internal links and deployable asset tree."""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.references = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                raise ValueError("Duplicate element ID: " + attrs["id"])
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.references.append(attrs[key])
        if tag == "img" and "alt" not in attrs:
            raise ValueError("Images require alternative text or an empty decorative alt")


def check(directory):
    directory = directory.resolve()
    parser = Page()
    parser.feed((directory / "index.html").read_text())
    for reference in parser.references:
        url = urlsplit(reference)
        if url.scheme or url.netloc:
            if url.scheme != "https":
                raise ValueError("External links must use HTTPS: " + reference)
            continue
        if url.path:
            asset = (directory / unquote(url.path).lstrip("/")).resolve()
            if not asset.is_relative_to(directory) or not asset.is_file():
                raise ValueError("Missing or invalid local asset: " + reference)
        elif url.fragment and url.fragment not in parser.ids:
            raise ValueError("Missing anchor: " + reference)
    manifest_path = directory / "assets/icons/site.webmanifest"
    for icon in json.loads(manifest_path.read_text())["icons"]:
        if not (manifest_path.parent / icon["src"]).is_file():
            raise ValueError("Missing manifest icon: " + icon["src"])
    for name in ("robots.txt", "sitemap.xml"):
        if "https://uoo.lolisoft.eu/" not in (directory / name).read_text():
            raise ValueError("Expected canonical domain in " + name)
    print(f"Landing page verified: {len(parser.references)} references, icons, anchors, and production domain.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "website/dist")
    check(parser.parse_args().directory)
