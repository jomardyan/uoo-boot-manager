#!/usr/bin/env python3
"""Check the static landing page's internal links and deployable asset tree (directory or ZIP)."""

import argparse
from html.parser import HTMLParser
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
import zipfile


DOMAIN = "https://uoo.lolisoft.eu"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.references = []
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.robots = ""
        self.h1_count = 0
        self.json_ld = []
        self._capture = None

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
        if tag == "meta" and attrs.get("name") == "description":
            self.description = attrs.get("content", "")
        if tag == "meta" and attrs.get("name") == "robots":
            self.robots = attrs.get("content", "")
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href", "")
        if tag == "h1":
            self.h1_count += 1
        if tag == "title":
            self._capture = "title"
        elif tag == "script" and attrs.get("type") == "application/ld+json":
            self._capture = "json_ld"
            self.json_ld.append("")

    def handle_endtag(self, tag):
        if tag in ("title", "script"):
            self._capture = None

    def handle_data(self, data):
        if self._capture == "title":
            self.title += data
        elif self._capture == "json_ld":
            self.json_ld[-1] += data


def check(path):
    path = path.resolve()
    if path.is_file():
        with tempfile.TemporaryDirectory() as extracted:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError("Corrupt entry in " + path.name)
                archive.extractall(extracted)
            check_directory(Path(extracted))
            if not (Path(extracted) / ".nojekyll").is_file():
                raise ValueError("Missing .nojekyll in " + path.name)
    else:
        check_directory(path)


def page_url(directory, page):
    relative = page.relative_to(directory).as_posix()
    if relative == "index.html":
        return DOMAIN + "/"
    return DOMAIN + "/" + relative.removesuffix("index.html")


def resolve(directory, page, path):
    base = directory if path.startswith("/") else page.parent
    target = (base / unquote(path).lstrip("/")).resolve()
    if target.is_dir():
        target = target / "index.html"
    if not target.is_relative_to(directory) or not target.is_file():
        raise ValueError("Missing or invalid local asset: " + path)
    return target


def check_directory(directory):
    directory = directory.resolve()
    pages = {}
    for file in sorted(directory.rglob("*.html")):
        parser = Page()
        parser.feed(file.read_text())
        pages[file] = parser
    if directory / "index.html" not in pages:
        raise ValueError("Missing index.html")
    titles, indexable, references = set(), set(), 0
    for file, parser in pages.items():
        label = file.relative_to(directory).as_posix()
        references += len(parser.references)
        if not parser.title.strip():
            raise ValueError("Missing <title> in " + label)
        if parser.title in titles:
            raise ValueError("Duplicate <title> in " + label)
        titles.add(parser.title)
        if parser.h1_count != 1:
            raise ValueError(f"{label} must contain exactly one h1")
        for block in parser.json_ld:
            try:
                json.loads(block)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON-LD in {label}: {error}") from error
        if "noindex" in parser.robots:
            continue
        if not 60 <= len(parser.description) <= 180:
            raise ValueError(f"{label} needs a 60-180 character meta description")
        if parser.canonical != page_url(directory, file):
            raise ValueError(f"{label} canonical must be {page_url(directory, file)}")
        indexable.add(parser.canonical)
    for file, parser in pages.items():
        for reference in parser.references:
            url = urlsplit(reference)
            if url.scheme or url.netloc:
                if url.scheme != "https":
                    raise ValueError("External links must use HTTPS: " + reference)
                continue
            target = resolve(directory, file, url.path) if url.path else file
            if url.fragment and target in pages and url.fragment not in pages[target].ids:
                raise ValueError(f"Missing anchor: {reference} (from {file.relative_to(directory)})")
    manifest_path = directory / "assets/icons/site.webmanifest"
    for icon in json.loads(manifest_path.read_text())["icons"]:
        if not (manifest_path.parent / icon["src"]).is_file():
            raise ValueError("Missing manifest icon: " + icon["src"])
    robots = (directory / "robots.txt").read_text()
    if DOMAIN + "/sitemap.xml" not in robots:
        raise ValueError("robots.txt must reference the sitemap")
    sitemap = set(re.findall(r"<loc>([^<]+)</loc>", (directory / "sitemap.xml").read_text()))
    if sitemap != indexable:
        raise ValueError("sitemap.xml must list exactly the indexable pages; difference: " + ", ".join(sorted(sitemap ^ indexable)))
    print(f"Landing page and docs verified: {len(pages)} pages, {references} references, icons, anchors, sitemap, and production domain.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "website/dist/uoo-website.zip", help="website directory or ZIP archive")
    check(parser.parse_args().path)
