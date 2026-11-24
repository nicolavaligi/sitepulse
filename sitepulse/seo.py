"""Capture public SEO signals and render metadata for a different project."""

import json
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .audit import Fetcher, PageParser, jsonld_types, normalize_url, same_host


class SEOParser(PageParser):
    def __init__(self):
        super().__init__()
        self.alternates = []
        self.headings.update({f"h{level}": [] for level in range(4, 7)})

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == "link":
            values = dict(attrs)
            rel = (values.get("rel") or "").lower().split()
            if "alternate" in rel and values.get("hreflang") and values.get("href"):
                self.alternates.append((values["hreflang"], values["href"]))


def web_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
        raise ValueError(f"Expected an HTTP(S) URL without credentials: {value!r}")
    return value


def extract(url, fetcher):
    response = fetcher.request(url)
    final = response["final_url"]
    page = {"url": url, "final_url": final, "status": response["status"]}
    if response["error"] or not 200 <= (response["status"] or 0) < 300:
        return dict(page, error=response["error"] or f'HTTP {response["status"]}')
    content_type = response["headers"].get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ("text/html", "application/xhtml+xml") or response["truncated"]:
        return dict(page, error="Expected a complete HTML response")

    def resolved(href):
        try:
            return web_url(urljoin(final, href))
        except ValueError:
            return href

    parser = SEOParser()
    parser.feed(response["body"].decode(response["charset"], "replace"))
    parser.close()
    internal = {}
    for href, anchor in parser.anchors:
        if not href or href.startswith("#"):
            continue
        try:
            target = urljoin(final, href).split("#", 1)[0]
            if urlsplit(target).scheme in ("http", "https") and same_host(target, final):
                internal.setdefault(target, anchor)
        except ValueError:
            continue
    return dict(
        page,
        title=parser.title.strip(),
        description=parser.meta.get("description", ""),
        lang=parser.lang,
        robots={key: parser.meta[key] for key in ("robots", "googlebot", "bingbot")
                if key in parser.meta},
        x_robots_tag=response["headers"].get("x-robots-tag"),
        canonicals=[resolved(href) for rel, href in parser.links
                    if "canonical" in rel.split() and href],
        hreflang=[{"lang": lang, "url": resolved(href)}
                  for lang, href in parser.alternates],
        social={key: value for key, value in parser.meta.items()
                if key.startswith(("og:", "twitter:"))},
        schema_types=jsonld_types(parser.jsonld),
        headings={tag: [heading.strip() for heading in items]
                  for tag, items in parser.headings.items()},
        images=len(parser.images),
        images_without_alt=sum(img["alt"] is None for img in parser.images),
        internal_links=internal,
    )


def capture(urls, fetcher=None):
    normalized = list(dict.fromkeys(web_url(normalize_url(url)[0]) for url in urls))
    fetcher = fetcher or Fetcher()
    pages = [extract(url, fetcher) for url in normalized]
    successful = [page for page in pages if "error" not in page]
    patterns = Counter(
        separator for page in successful for separator in (" | ", " - ", " – ", " — ")
        if page["title"].rpartition(separator)[0]
    )
    return {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "pages": pages,
        "summary": {
            "successful_pages": len(successful),
            "with_description": sum(bool(page["description"]) for page in successful),
            "with_one_h1": sum(len(page["headings"]["h1"]) == 1 for page in successful),
            "title_separators": dict(patterns),
        },
    }


def render_head(snapshot, source, canonical, title, description, brand=None, image=None):
    if snapshot.get("version") != 1:
        raise ValueError("Unsupported SEO snapshot version")
    matches = [page for page in snapshot["pages"]
               if source in (page["url"], page["final_url"]) and "error" not in page]
    if not matches:
        raise ValueError("Source URL has no successful capture in this snapshot")
    page = matches[0]
    if brand:
        separator = next((item for item in (" | ", " - ", " – ", " — ")
                          if page["title"].rpartition(item)[0]), " | ")
        title += separator + brand
    canonical = web_url(canonical)
    image = web_url(image) if image else None

    def meta(attribute, key, value):
        return f'<meta {attribute}="{key}" content="{escape(value, quote=True)}">'

    lines = [
        f"<title>{escape(title)}</title>",
        meta("name", "description", description),
        f'<link rel="canonical" href="{escape(canonical, quote=True)}">',
    ]
    social = page["social"]
    if any(key.startswith("og:") for key in social):
        lines.extend(meta("property", key, value) for key, value in (
            ("og:title", title), ("og:description", description), ("og:url", canonical)
        ))
        if image:
            lines.append(meta("property", "og:image", image))
    if any(key.startswith("twitter:") for key in social):
        lines.extend(meta("name", key, value) for key, value in (
            ("twitter:card", "summary_large_image" if image else "summary"),
            ("twitter:title", title), ("twitter:description", description),
        ))
        if image:
            lines.append(meta("name", "twitter:image", image))
    return "\n".join(lines) + "\n"


def add_commands(sub):
    snapshot = sub.add_parser("seo-capture", help="capture public SEO settings to JSON")
    snapshot.add_argument("--url", action="append", required=True, help="page URL (repeatable)")
    snapshot.add_argument("--output", type=Path, required=True, help="new JSON output file")
    profile = sub.add_parser("seo-head", help="generate HTML metadata for your project")
    profile.add_argument("snapshot", type=Path)
    profile.add_argument("--source", required=True, help="source page URL in the snapshot")
    profile.add_argument("--canonical", required=True, help="new project's page URL")
    profile.add_argument("--title", required=True)
    profile.add_argument("--description", required=True)
    profile.add_argument("--brand", help="optional brand suffix")
    profile.add_argument("--image", help="absolute URL of your social preview image")


def run(args):
    if args.command == "seo-capture":
        snapshot = capture(args.url)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(snapshot, output, ensure_ascii=False, indent=2)
            output.write("\n")
        return 0 if snapshot["summary"]["successful_pages"] == len(snapshot["pages"]) else 1
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    print(render_head(snapshot, normalize_url(args.source)[0], args.canonical,
                      args.title, args.description, args.brand, args.image), end="")
    return 0
