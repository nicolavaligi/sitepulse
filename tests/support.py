"""Test support: local fixture websites served on 127.0.0.1.

Nothing in the test suite touches the internet. Every URL in the fixture
pages points at 127.0.0.1 or localhost on the port of the test server
("localhost" plays the role of an external host).
"""

import os
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'self'",
    "X-Frame-Options": "DENY",
}

FILLER = " ".join(["Reliable plumbing repairs for homes and small shops."] * 60)

GOOD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Example Plumbing | Emergency repairs in Springfield</title>
<meta name="description" content="Burst pipe or blocked drain? Example Plumbing fixes it the same day across Springfield, with fixed prices agreed before work starts.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="Example Plumbing">
<meta property="og:description" content="Same-day repairs in Springfield.">
<meta property="og:image" content="http://127.0.0.1:{port}/logo.png">
<link rel="canonical" href="http://127.0.0.1:{port}/">
<link rel="icon" href="/favicon.ico">
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">{{"@context": "https://schema.org", "@graph": [
 {{"@type": "LocalBusiness", "name": "Example Plumbing", "description": "<p>Fast</p> repairs"}},
 {{"@type": ["FAQPage", "WebPage"]}}]}}</script>
<script defer src="/js/plausible.io.js"></script>
<style>body{{margin:0}}</style>
</head>
<body>
<svg width="10" height="10"><title>Decorative icon</title><circle r="4"/></svg>
<h1>Emergency plumbing <em>in Springfield</em></h1>
<h2>What we fix</h2>
<p>{filler}</p>
<h2>Prices</h2>
<h3>Call-out fee</h3>
<p>Fixed prices, agreed before we start.</p>
<img src="/logo.png" alt="Example Plumbing logo" width="120" height="40">
<img src="/logo.png" alt="Example Plumbing logo again" width="120" height="40">
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="spacer" width="1" height="1">
<nav>
<a href="/about">About</a>
<a href="/café">Our café</a>
<a href="/about#team">Team</a>
<a href="#prices">Prices</a>
<a href="http://localhost:{port}/partner">Partner</a>
<a href="tel:+15550100">Call us now</a>
<a href="mailto:hello@example.com">Email</a>
<a href="sms:+15550100">Text</a>
</nav>
<form action="/contact"><input name="name"><input type="submit" value="Request a quote"></form>
<script>console.log("inline script");</script>
</body>
</html>
"""

BAD_HTML = """<html>
<head>
<script type="application/ld+json">{{not json</script>
<script src="http://localhost:{port}/a.js"></script>
<script src="http://localhost:{port}/b.js"></script>
<script src="http://localhost:{port}/c.js"></script>
<script src="http://localhost:{port}/d.js"></script>
<link rel="stylesheet" href="/broken.css">
</head>
<body>
<svg><title>Icon title</title></svg>
<p>Welcome. Copyright 2019 Example. Trying to bookkeep for callers.</p>
<img src="/p1.png"><img src="/p2.png"><img src="/p3.png"><img src="/p4.png"><img src="/p5.png">
<a href="/gone">Gone</a>
<a href="javascript:void(0)">Script link</a>
</body>
</html>
"""

SOFT_404 = "<html><body>Welcome</body></html>"

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>http://127.0.0.1:{port}/</loc></url>
<url><loc>http://127.0.0.1:{port}/about</loc></url>
<url><loc>http://127.0.0.1:{port}/prices</loc></url>
</urlset>
"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _respond(self):
        site = self.server.site
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        site.hits.append((self.command, path, self.headers))   # case-insensitive
        if self.command == "HEAD" and path in site.no_head:
            status, ctype, body = 405, "text/plain", ""
        elif path in site.routes:
            status, ctype, body = site.routes[path]
        elif site.soft_404:
            status, ctype, body = 200, "text/html; charset=utf-8", SOFT_404
        else:
            status, ctype, body = 404, "text/plain", "not found"
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in site.headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = _respond
    do_HEAD = _respond

    def log_message(self, *args):       # keep test output clean
        pass


class FixtureSite:
    """A tiny website on 127.0.0.1:<random port>, served from a thread."""

    def __init__(self, soft_404=False, headers=None, no_head=()):
        self.routes = {}
        self.soft_404 = soft_404
        self.headers = headers or {}
        self.no_head = set(no_head)
        self.hits = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.site = self
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def paths(self, method=None):
        return [p for m, p, _ in self.hits if method in (None, m)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def good_site():
    site = FixtureSite(headers=SECURITY_HEADERS, no_head=("/style.css",))
    html = "text/html; charset=utf-8"
    site.routes.update({
        "/": (200, html, GOOD_HTML.format(port=site.port, filler=FILLER)),
        "/about": (200, html, "<html><body>About us</body></html>"),
        "/café": (200, html, "<html><body>Coffee</body></html>"),
        "/partner": (200, html, "<html><body>Partner</body></html>"),
        "/style.css": (200, "text/css", "body{color:#111}" * 50),
        "/js/plausible.io.js": (200, "text/javascript", "/* local stand-in */"),
        "/logo.png": (200, "image/png", b"\x89PNG" + b"0" * 2000),
        "/favicon.ico": (200, "image/x-icon", b"ico"),
        "/robots.txt": (200, "text/plain",
                        "User-agent: *\nAllow: /\n"
                        "Sitemap: http://127.0.0.1:%d/sitemap-main.xml\n" % site.port),
        "/sitemap-main.xml": (200, "application/xml", SITEMAP.format(port=site.port)),
        "/500": (500, html, "<html><body>boom</body></html>"),
    })
    return site


def bad_site():
    site = FixtureSite(soft_404=True)
    site.routes.update({
        "/": (200, "text/html", BAD_HTML.format(port=site.port)),
        "/gone": (404, "text/plain", "gone"),
        "/broken.css": (404, "text/plain", "missing"),
    })
    return site


def closed_port():
    """A port on 127.0.0.1 where nothing is listening."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def statuses(result):
    return {c["id"]: c["status"] for c in result["checks"]}


def check(result, id_):
    for c in result["checks"]:
        if c["id"] == id_:
            return c
    raise AssertionError("check %r not present: %s" % (id_, sorted(statuses(result))))
