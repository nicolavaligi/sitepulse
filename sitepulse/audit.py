"""sitepulse.audit - collect the measurable facts about one or more websites.

This module does the objective half of the weekly routine only: it requests
the pages, measures, compares with the previous run and writes the results.
It has no opinions. What is worth doing about the numbers is decided by a
human or an agent following CHECKLIST.md.

Files written under the data directory (``--dir``, default ./sitepulse-data):

    reports/data-YYYY-MM-DD.json   full data for the run
    reports/DATA-YYYY-MM-DD.md     the same data, readable
    history.json                   one row per site per run (for the deltas)

Standard library only.
"""

from __future__ import annotations

import codecs
import gzip
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from . import PROJECT_URL

log = logging.getLogger("sitepulse.audit")

DEFAULT_USER_AGENT = "sitepulse/0.1 (+%s)" % PROJECT_URL
DEFAULT_ACCEPT_LANGUAGE = "en"
TIMEOUT = 20

# Politeness limits: the most extra requests sitepulse makes per site, so that
# a weekly audit never hammers anybody's server.
MAX_ASSETS = 25           # scripts + stylesheets + images weighed
MAX_IMAGES = 12           # images considered for weighing
MAX_INTERNAL_LINKS = 20   # internal links probed
MAX_EXTERNAL_LINKS = 8    # external links probed
MAX_BODY_BYTES = 10 * 1024 * 1024   # never download more than this per URL

HISTORY_MAX_ROWS = 2000
NOT_FOUND_PROBE = "/sitepulse-nonexistent-page-check"
INVALID_JSONLD = "(invalid JSON-LD)"

TRACKERS = {
    "Google Analytics / Tag Manager": ("googletagmanager.com", "google-analytics.com", "gtag("),
    "Plausible": ("plausible.io",),
    "Umami": ("umami.is", "/umami.js"),
    "Matomo": ("matomo.js", "matomo.php"),
    "Microsoft Clarity": ("clarity.ms",),
    "Fathom": ("usefathom.com",),
    "GoatCounter": ("goatcounter.com", "gc.zgo.at"),
    "Simple Analytics": ("simpleanalyticscdn.com",),
    "Cloudflare Web Analytics": ("static.cloudflareinsights.com",),
}

# Call-to-action vocabulary, matched as whole words/phrases (case-insensitive)
# against the visible text. Override per site with "cta_words" in the config.
CTA_WORDS = {
    "en": ("call", "call us", "contact", "contact us", "get in touch", "book",
           "request", "schedule", "sign up", "subscribe", "get started", "try",
           "buy", "order", "learn more", "discover", "download", "join"),
    "it": ("chiama", "chiamaci", "contatta", "contattaci", "richiedi", "prenota",
           "scrivici", "parliamo", "parliamone", "fissa", "scopri", "prova",
           "iscriviti", "acquista", "scarica"),
}

SECURITY_HEADERS = {
    "strict-transport-security": "HSTS",
    "x-content-type-options": "X-Content-Type-Options",
    "referrer-policy": "Referrer-Policy",
    "content-security-policy": "CSP",
    "x-frame-options": "X-Frame-Options",
}

SKIPPED_DIRS = (".git", "node_modules", ".venv", "venv", "__pycache__", ".cache")

# statuses
OK, WARN, PROBLEM, INFO = "ok", "warning", "problem", "info"
WEIGHTS = {OK: 1.0, WARN: 0.5, PROBLEM: 0.0}
LABELS = {OK: "OK", WARN: "WARNING", PROBLEM: "PROBLEM", INFO: "info"}

# areas
TECHNICAL, SEO, CONTENT, CONVERSION, SECURITY = (
    "technical", "seo", "content", "conversion", "security")


class HistoryError(Exception):
    """history.json exists but cannot be used. Never overwritten silently."""


# --------------------------------------------------------------------------
# network
# --------------------------------------------------------------------------

_URL_SAFE = "/%:@!$&'()*+,;=~-._"


def to_ascii_url(url):
    """Percent-encode / IDNA-encode a URL so urllib can request it.

    Links such as ``/café`` or ``/my page`` are valid in HTML but make urllib
    raise; without this they would be reported as broken. Drops the fragment.
    """
    p = urllib.parse.urlsplit(url.strip())
    netloc = p.netloc
    try:
        netloc.encode("ascii")
    except UnicodeEncodeError:
        host = (p.hostname or "").encode("idna").decode("ascii")
        netloc = host + (":%d" % p.port if p.port else "")
    path = urllib.parse.quote(p.path, safe=_URL_SAFE)
    query = urllib.parse.quote(p.query, safe=_URL_SAFE + "?")
    return urllib.parse.urlunsplit((p.scheme, netloc, path, query, ""))


def _bare_host(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def same_host(url, *site_urls):
    """True if ``url`` is served by the same host as any of ``site_urls``.

    Hosts are compared for equality (ignoring port and a leading ``www.``),
    never by substring.
    """
    host = _bare_host(url)
    return bool(host) and any(host == _bare_host(s) for s in site_urls)


class Fetcher:
    """Minimal HTTP client: one place for User-Agent, language and limits."""

    def __init__(self, user_agent=None, accept_language=None, timeout=TIMEOUT):
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.accept_language = accept_language or DEFAULT_ACCEPT_LANGUAGE
        self.timeout = timeout
        self._ssl = ssl.create_default_context()

    def request(self, url, method="GET", timeout=None):
        """Return a dict: status, headers (lower-case keys), body, raw_bytes,
        charset, seconds, final_url, truncated, error."""
        out = {"url": url, "status": None, "headers": {}, "body": b"",
               "raw_bytes": 0, "charset": "utf-8", "seconds": None,
               "final_url": url, "truncated": False, "error": None}
        body = b""
        t0 = time.monotonic()
        try:
            target = to_ascii_url(url)
            if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
                raise ValueError("unsupported URL scheme")
            req = urllib.request.Request(target, method=method, headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Encoding": "gzip",
                "Accept-Language": self.accept_language,
            })
            with urllib.request.urlopen(req, timeout=timeout or self.timeout,
                                        context=self._ssl) as r:
                if method == "GET":
                    body = r.read(MAX_BODY_BYTES + 1)
                out.update(status=r.status, final_url=r.geturl(),
                           headers={k.lower(): v for k, v in r.headers.items()},
                           charset=_charset(r.headers))
        except urllib.error.HTTPError as e:
            try:
                body = e.read(MAX_BODY_BYTES + 1) if method == "GET" else b""
            except Exception:           # body of an error page is optional
                body = b""
            finally:
                e.close()
            headers = e.headers or {}
            out.update(status=e.code, final_url=e.geturl() or url,
                       headers={k.lower(): v for k, v in headers.items()})
        except Exception as e:          # DNS, timeout, TLS, malformed URL...
            out["error"] = "%s: %s" % (type(e).__name__, e)
            out["seconds"] = round(time.monotonic() - t0, 3)
            log.debug("%s %s failed: %s", method, url, out["error"])
            return out
        out["seconds"] = round(time.monotonic() - t0, 3)
        if len(body) > MAX_BODY_BYTES:
            body = body[:MAX_BODY_BYTES]
            out["truncated"] = True
        out["raw_bytes"] = len(body)    # bytes transferred, before decompression
        if body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except Exception:
                log.debug("could not gunzip %s; keeping raw body", url)
        out["body"] = body
        log.debug("%s %s -> %s in %ss", method, url, out["status"], out["seconds"])
        return out

    def resource_size(self, url):
        """(bytes, status, error) for a resource. HEAD first, GET as fallback.

        The fallback also covers servers that refuse HEAD (403/405): those
        must not be reported as broken resources.
        """
        head = self.request(url, method="HEAD", timeout=12)
        if not head["error"] and (head["status"] or 0) < 400:
            length = head["headers"].get("content-length", "")
            if length.isdigit():
                return int(length), head["status"], None
        got = self.request(url, timeout=15)
        if got["error"]:
            return None, got["status"], got["error"]
        if (got["status"] or 0) >= 400:
            return None, got["status"], None
        return got["raw_bytes"], got["status"], None

    def is_broken(self, url):
        """None if the link works, otherwise a short reason (status or error)."""
        head = self.request(url, method="HEAD", timeout=12)
        if not head["error"] and (head["status"] or 0) < 400:
            return None
        got = self.request(url, timeout=12)     # some servers refuse HEAD
        if got["error"]:
            return "error"
        if (got["status"] or 0) >= 400:
            return str(got["status"])
        return None


def _charset(headers):
    name = headers.get_content_charset() or "utf-8"
    try:
        codecs.lookup(name)
    except LookupError:
        return "utf-8"
    return name


# --------------------------------------------------------------------------
# HTML parsing
# --------------------------------------------------------------------------

class PageParser(HTMLParser):
    """Extract what the checks need from one HTML document."""

    _SKIPPED = ("script", "style", "noscript", "svg")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._title_done = False
        self._skip = 0
        self.meta = {}              # name/property -> content
        self.lang = ""
        self.links = []             # (rel, href) from <link>
        self.headings = {"h%d" % i: [] for i in range(1, 4)}
        self._heading = None
        self.anchors = []           # (href, text)
        self._anchor = None
        self.images = []            # dicts: src/alt/width/height/loading
        self.scripts = []
        self.stylesheets = []
        self.jsonld = []            # one string per <script type=ld+json>
        self._jsonld_buf = None
        self.forms = 0
        self.inputs = 0
        self.button_labels = []     # value= of submit/button inputs
        self.iframes = []
        self.text = []
        self.inline_script_chars = 0
        self.inline_css_chars = 0
        self._in_script = False
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = self.lang or a.get("lang", "")
        elif tag == "title":
            # Only the document title: <title> inside inline <svg> is an
            # accessibility label for the graphic, not the page title.
            if not self._skip and not self._title_done:
                self._in_title = True
        elif tag == "meta":
            key = a.get("name") or a.get("property") or a.get("http-equiv")
            if key:
                self.meta[key.lower()] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower()
            self.links.append((rel, a.get("href", "")))
            if "stylesheet" in rel.split() and a.get("href"):
                self.stylesheets.append(a["href"])
        elif tag in self.headings:
            self._heading = tag
            self.headings[tag].append("")
        elif tag == "a":
            self._anchor = [a.get("href", "").strip(), ""]
        elif tag == "img":
            self.images.append({"src": a.get("src", ""), "alt": a.get("alt"),
                                "width": a.get("width"), "height": a.get("height"),
                                "loading": a.get("loading", "")})
        elif tag == "script":
            if a.get("type", "").strip().lower() == "application/ld+json":
                self._jsonld_buf = []
            elif a.get("src"):
                self.scripts.append(a["src"])
            else:
                self._in_script = True
        elif tag == "style":
            self._in_style = True
        elif tag == "form":
            self.forms += 1
        elif tag in ("input", "textarea", "select"):
            self.inputs += 1
            if tag == "input" and a.get("type", "").lower() in ("submit", "button") \
                    and a.get("value", "").strip():
                self.button_labels.append(a["value"].strip())
        elif tag == "iframe":
            self.iframes.append(a.get("src", ""))
        if tag in self._SKIPPED:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            if self._in_title:
                self._in_title = False
                self._title_done = True
        elif tag in self.headings:
            self._heading = None
        elif tag == "a" and self._anchor is not None:
            self.anchors.append((self._anchor[0], self._anchor[1].strip()))
            self._anchor = None
        elif tag == "script":
            # A script body can arrive in several handle_data() calls (for
            # instance when the document is fed in pieces): store it whole.
            if self._jsonld_buf is not None:
                self.jsonld.append("".join(self._jsonld_buf))
                self._jsonld_buf = None
            self._in_script = False
        elif tag == "style":
            self._in_style = False
        if tag in self._SKIPPED and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._jsonld_buf is not None:
            self._jsonld_buf.append(data)
        elif self._in_script:
            self.inline_script_chars += len(data)
        elif self._in_style:
            self.inline_css_chars += len(data)
        elif not self._skip:
            t = " ".join(data.split())
            if t:
                self.text.append(t)
                if self._heading and self.headings[self._heading]:
                    self.headings[self._heading][-1] += " " + t
                if self._anchor is not None:
                    self._anchor[1] += " " + t


def jsonld_types(blocks):
    """Sorted, de-duplicated @type values found in JSON-LD blocks."""
    found = set()

    def collect(item):
        if not isinstance(item, dict):
            return
        kind = item.get("@type")
        if isinstance(kind, list):
            found.update(str(k) for k in kind)
        elif kind:
            found.add(str(kind))
        graph = item.get("@graph")
        for node in graph if isinstance(graph, list) else []:
            collect(node)

    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            found.add(INVALID_JSONLD)
            continue
        for item in data if isinstance(data, list) else [data]:
            collect(item)
    return sorted(found)


def resolve_cta_words(site):
    """The CTA vocabulary for a site: explicit words, else built-in lists."""
    words = site.get("cta_words")
    if words:
        return [str(w).strip().lower() for w in words if str(w).strip()]
    languages = site.get("cta_languages") or sorted(CTA_WORDS)
    out = []
    for lang in languages:
        out.extend(CTA_WORDS.get(str(lang).lower(), ()))
    return out


def find_cta_words(text, words):
    """Words/phrases from ``words`` present in ``text`` as whole words."""
    text = " ".join(text.lower().split())
    return sorted({w for w in words
                   if re.search(r"(?<!\w)%s(?!\w)" % re.escape(w), text)})


# --------------------------------------------------------------------------
# auditing one site
# --------------------------------------------------------------------------

def add_check(checks, area, id_, status, value, note=""):
    checks.append({"area": area, "id": id_, "status": status,
                   "value": " ".join(str(value).split()),
                   "note": " ".join(str(note).split())})


def normalize_url(raw):
    """(home_url, display_url). Adds https:// when the scheme is missing."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = "https://" + raw
    p = urllib.parse.urlsplit(raw)
    home = urllib.parse.urlunsplit((p.scheme.lower(), p.netloc, p.path or "/",
                                    p.query, ""))
    display = home[:-1] if (p.path in ("", "/") and not p.query) else home
    return home, display


def audit_site(site, fetcher=None, now=None):
    """Measure one site. Returns a result dict with checks and measurements."""
    now = now or datetime.now()
    home_url, url = normalize_url(site.get("url"))
    res = {"name": site.get("name") or url, "url": url,
           "purpose": site.get("purpose", ""), "goal": site.get("goal", ""),
           "checks": [], "measurements": {}, "reachable": False}
    c = res["checks"]
    if fetcher is None:
        fetcher = Fetcher(site.get("user_agent"), site.get("accept_language"))

    if not url:
        add_check(c, TECHNICAL, "missing-url", PROBLEM, "missing",
                  "no URL in the config: site not online yet?")
        return res

    home = fetcher.request(home_url)
    res["measurements"]["response_s"] = home["seconds"]
    res["measurements"]["status"] = home["status"]

    if home["error"] or not home["status"]:
        add_check(c, TECHNICAL, "online", PROBLEM, "unreachable", home["error"] or "")
        return res
    if home["status"] >= 400:
        add_check(c, TECHNICAL, "online", PROBLEM, "HTTP %s" % home["status"],
                  "the home page answers with an error")
        return res

    res["reachable"] = True
    final_url = home["final_url"] or home_url
    fp = urllib.parse.urlsplit(final_url)
    origin = "%s://%s" % (fp.scheme, fp.netloc)
    add_check(c, TECHNICAL, "online", OK, "HTTP %s" % home["status"],
              "answered in %ss" % home["seconds"])

    # --- response time ---------------------------------------------------
    t = home["seconds"] or 0
    add_check(c, TECHNICAL, "response-time",
              OK if t < 1.0 else (WARN if t < 2.5 else PROBLEM),
              "%.2fs" % t, "under 1s is great; above 2.5s visitors leave")

    # --- https and security headers --------------------------------------
    # Judged on where visitors actually land (after redirects).
    is_https = fp.scheme == "https"
    add_check(c, SECURITY, "https", OK if is_https else PROBLEM,
              "https" if is_https else "http",
              "" if is_https else "the page is served without encryption")
    missing = [label for h, label in SECURITY_HEADERS.items()
               if h not in home["headers"]]
    total = len(SECURITY_HEADERS)
    add_check(c, SECURITY, "security-headers",
              OK if not missing else (WARN if len(missing) <= 2 else PROBLEM),
              "%d/%d present" % (total - len(missing), total),
              ("missing: " + ", ".join(missing)) if missing else "all present")

    # --- parsing ---------------------------------------------------------
    html = home["body"].decode(home["charset"], "replace")
    p = PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception as e:      # html.parser is lenient; this is a last resort
        add_check(c, TECHNICAL, "html-parse", WARN, "incomplete parsing", str(e))

    visible = " ".join(p.text)
    words = len(visible.split())
    title = " ".join(p.title.split())
    m = res["measurements"]
    m["html_kb"] = round(len(home["body"]) / 1024, 1)
    m["words"] = words
    m["title"] = title
    m["inline_script_kb"] = round(p.inline_script_chars / 1024, 1)
    m["inline_css_kb"] = round(p.inline_css_chars / 1024, 1)

    # --- basic SEO -------------------------------------------------------
    add_check(c, SEO, "title",
              OK if 25 <= len(title) <= 65 else (WARN if title else PROBLEM),
              "%d chars" % len(title),
              title[:120] or "missing: search engines will invent a title")

    desc = " ".join(p.meta.get("description", "").split())
    add_check(c, SEO, "meta-description",
              OK if 70 <= len(desc) <= 165 else (WARN if desc else PROBLEM),
              "%d chars" % len(desc),
              desc[:140] or "missing: no control over the search snippet")

    add_check(c, SEO, "lang", OK if p.lang else WARN, p.lang or "missing",
              "needed for language detection and accessibility")

    canonical = [h for r, h in p.links if "canonical" in r.split() and h]
    add_check(c, SEO, "canonical", OK if canonical else WARN,
              canonical[0] if canonical else "missing", "avoids duplicate content")

    h1 = [x.strip() for x in p.headings["h1"] if x.strip()]
    add_check(c, SEO, "h1", OK if len(h1) == 1 else (WARN if h1 else PROBLEM),
              "%d found" % len(h1), h1[0][:100] if h1 else "no H1")

    add_check(c, SEO, "heading-structure",
              OK if len(p.headings["h2"]) >= 2 else WARN,
              "%d H2 / %d H3" % (len(p.headings["h2"]), len(p.headings["h3"])),
              "subheadings guide both readers and indexing")

    types = jsonld_types(p.jsonld)
    jsonld_valid = bool(types) and INVALID_JSONLD not in types
    add_check(c, SEO, "structured-data", OK if jsonld_valid else WARN,
              ", ".join(types) or "missing",
              "JSON-LD: how search engines understand who you are and what you offer"
              if jsonld_valid or not types
              else "a JSON-LD block does not parse: search engines will ignore it")

    og = [k for k in ("og:title", "og:description", "og:image") if p.meta.get(k)]
    add_check(c, SEO, "social-sharing",
              OK if len(og) == 3 else (WARN if og else PROBLEM),
              "%d/3 Open Graph tags" % len(og),
              "without og:image a shared link shows a blank preview")

    # --- robots and sitemap (always at the root of the host) --------------
    rb = fetcher.request(origin + "/robots.txt")
    rb_ok = rb["status"] == 200 and b"<html" not in rb["body"][:500].lower()
    rb_txt = rb["body"].decode("utf-8", "replace") if rb_ok else ""
    declared = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", rb_txt)
    add_check(c, SEO, "robots", OK if rb_ok else WARN,
              "present" if rb_ok else "missing",
              ("declares the sitemap" if declared else "does not declare the sitemap")
              if rb_ok else "no robots.txt at the root of the host")

    sitemap_urls = list(dict.fromkeys(
        [urllib.parse.urljoin(origin + "/", d) for d in declared[:2]]
        + [origin + "/sitemap.xml"]))
    sm_value, sm_note, sm_found = "missing", "helps inner pages get indexed", False
    for sm_url in sitemap_urls:
        sm = fetcher.request(sm_url)
        if sm["status"] != 200:
            continue
        sm_txt = sm["body"].decode("utf-8", "replace")
        n_loc = len(re.findall(r"<loc[\s>]", sm_txt))
        if not n_loc:
            continue
        sm_found = True
        if re.search(r"<sitemapindex[\s>]", sm_txt):
            sm_value = "index of %d sitemaps" % n_loc
        else:
            sm_value = "%d URLs" % n_loc if n_loc != 1 else "1 URL"
        sm_note = sm_url
        break
    add_check(c, SEO, "sitemap", OK if sm_found else WARN, sm_value, sm_note)

    # --- 404 page --------------------------------------------------------
    nf = fetcher.request(origin + NOT_FOUND_PROBE)
    add_check(c, TECHNICAL, "page-404", OK if nf["status"] in (404, 410) else WARN,
              "HTTP %s" % nf["status"] if nf["status"] else "no answer",
              "a wrong URL must answer 404, not 200")

    # --- images ----------------------------------------------------------
    no_alt = [i for i in p.images if not (i["alt"] or "").strip()]
    add_check(c, CONTENT, "image-alt",
              OK if not no_alt else (WARN if len(no_alt) <= 3 else PROBLEM),
              "%d of %d without alt" % (len(no_alt), len(p.images)),
              "alt text = accessibility + image search")
    if p.images:
        no_dim = [i for i in p.images if not (i["width"] and i["height"])]
        add_check(c, TECHNICAL, "image-dimensions",
                  OK if len(no_dim) <= 1 else WARN,
                  "%d of %d without width/height" % (len(no_dim), len(p.images)),
                  "without dimensions the page jumps while loading")

    # --- total weight ----------------------------------------------------
    candidates = [("js", s) for s in p.scripts] + [("css", s) for s in p.stylesheets]
    candidates += [("img", i["src"]) for i in p.images[:MAX_IMAGES] if i["src"]]
    assets, seen = [], set()
    for kind, src in candidates:
        full = urllib.parse.urljoin(final_url, src.strip())
        if urllib.parse.urlsplit(full).scheme not in ("http", "https") or full in seen:
            continue                        # data:, blob:, duplicates
        seen.add(full)
        assets.append((kind, full))
    resources, total_bytes, broken = [], home["raw_bytes"], []
    for kind, full in assets[:MAX_ASSETS]:
        size, status, error = fetcher.resource_size(full)
        resources.append({"type": kind, "url": full, "bytes": size, "status": status})
        if size is not None:
            total_bytes += size
        else:
            broken.append("%s %s: %s" % (kind, status or "error", full))
    kb = total_bytes / 1024
    m["weight_kb"] = round(kb, 1)
    m["resources_weighed"] = len(resources)
    add_check(c, TECHNICAL, "page-weight",
              OK if kb < 1200 else (WARN if kb < 2500 else PROBLEM),
              "%.0f KB over %d resources" % (kb, len(resources)),
              "on a mobile connection, above 2.5 MB the wait is noticeable")
    external = [r for r in resources if not same_host(r["url"], home_url, final_url)]
    add_check(c, TECHNICAL, "external-dependencies",
              OK if len(external) <= 3 else WARN,
              "%d from other hosts" % len(external),
              "every external CDN is one more point of failure")
    add_check(c, TECHNICAL, "broken-resources", PROBLEM if broken else OK,
              "%d" % len(broken), "; ".join(broken[:3]))

    # --- links -----------------------------------------------------------
    internal, outbound = [], []
    for href, _ in p.anchors:
        if not href or href.startswith("#"):
            continue
        full = urllib.parse.urldefrag(urllib.parse.urljoin(final_url, href))[0]
        if urllib.parse.urlsplit(full).scheme not in ("http", "https"):
            continue                        # mailto:, tel:, javascript:, sms:...
        (internal if same_host(full, home_url, final_url) else outbound).append(full)
    internal = list(dict.fromkeys(internal))
    outbound = list(dict.fromkeys(outbound))
    m["internal_links"] = len(internal)
    m["external_links"] = len(outbound)

    probed = internal[:MAX_INTERNAL_LINKS] + outbound[:MAX_EXTERNAL_LINKS]
    broken_links = []
    for link in probed:
        reason = fetcher.is_broken(link)
        if reason:
            broken_links.append("%s: %s" % (reason, link))
    add_check(c, TECHNICAL, "broken-links", PROBLEM if broken_links else OK,
              "%d broken of %d probed" % (len(broken_links), len(probed)),
              "; ".join(broken_links[:4]))

    # --- conversion ------------------------------------------------------
    hrefs = [h.lower() for h, _ in p.anchors]
    tel = [h for h in hrefs if h.startswith("tel:")]
    mail = [h for h in hrefs if h.startswith("mailto:")]
    wa = [h for h in hrefs if "wa.me" in h or "whatsapp" in h]
    m["contact"] = {"tel": len(tel), "mail": len(mail),
                    "whatsapp": len(wa), "forms": p.forms}
    channels = sum(1 for x in (tel, mail, wa, p.forms) if x)
    add_check(c, CONVERSION, "contact-channels",
              OK if channels >= 2 else (WARN if channels == 1 else PROBLEM),
              "%d channels (tel %d, mail %d, whatsapp %d, forms %d)"
              % (channels, len(tel), len(mail), len(wa), p.forms),
              "offer at least two ways to get in touch")

    cta = find_cta_words(visible + " " + " ".join(p.button_labels),
                         resolve_cta_words(site))
    add_check(c, CONVERSION, "call-to-action", OK if cta else WARN,
              ", ".join(cta) or "none", "words in the visible text that ask for an action")

    html_lower = html.lower()
    trackers = [n for n, sigs in TRACKERS.items() if any(s in html_lower for s in sigs)]
    add_check(c, CONVERSION, "analytics", OK if trackers else WARN,
              ", ".join(trackers) or "none detected",
              "without data you cannot tell whether the site works")

    favicon = [h for r, h in p.links if "icon" in r]
    add_check(c, CONTENT, "favicon", OK if favicon else WARN,
              "present" if favicon else "missing", "recognisability in browser tabs")

    add_check(c, CONTENT, "text-amount",
              OK if words >= 300 else (WARN if words >= 120 else PROBLEM),
              "%d words" % words, "under 300 words search engines have little to read")

    viewport = p.meta.get("viewport", "")
    add_check(c, TECHNICAL, "mobile-viewport", OK if viewport else PROBLEM,
              viewport[:60] or "meta viewport missing",
              "without a viewport the site is unusable on a phone")

    # --- stale years in the text ------------------------------------------
    stale = sorted({y for y in re.findall(r"\b(20[0-9]{2})\b", visible)
                    if int(y) < now.year - 1})
    add_check(c, CONTENT, "stale-years", WARN if stale else OK,
              ", ".join(stale[-4:]) or "none",
              "old years in the text make the site look abandoned")

    # --- optional: state of the local project ------------------------------
    path = site.get("path") or ""
    if path and os.path.isdir(path):
        newest = None
        for base, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
            for f in files:
                try:
                    mtime = os.path.getmtime(os.path.join(base, f))
                except OSError:
                    continue
                newest = mtime if newest is None or mtime > newest else newest
        if newest:
            days = max(0.0, (now.timestamp() - newest) / 86400)
            m["days_since_change"] = round(days, 1)
            add_check(c, CONTENT, "last-modified",
                      OK if days < 30 else (WARN if days < 90 else PROBLEM),
                      "%.0f days" % days, "a site untouched for months loses rankings")
    elif path:
        log.warning("site %r: path is not a directory, skipping last-modified",
                    res["name"])

    return res


# --------------------------------------------------------------------------
# scoring, history, output
# --------------------------------------------------------------------------

def score(result):
    """0-100: the mean of OK=1, WARNING=0.5, PROBLEM=0 over scored checks."""
    scored = [x for x in result["checks"] if x["status"] in WEIGHTS]
    if not scored:
        return 0
    return round(100 * sum(WEIGHTS[x["status"]] for x in scored) / len(scored))


def area_scores(result):
    areas = {}
    for x in result["checks"]:
        if x["status"] not in WEIGHTS:
            continue
        a = areas.setdefault(x["area"], [0.0, 0])
        a[0] += WEIGHTS[x["status"]]
        a[1] += 1
    return {k: round(100 * v[0] / v[1]) for k, v in sorted(areas.items())}


def load_history(path):
    """Read history.json. A missing file is an empty history; a damaged one
    raises HistoryError (it is the only file that cannot be regenerated)."""
    if not os.path.exists(path):
        return {"rows": []}
    try:
        with open(path, encoding="utf-8") as f:
            history = json.load(f)
    except (OSError, ValueError) as e:
        raise HistoryError("cannot read %s: %s" % (path, e))
    if not isinstance(history, dict) or not isinstance(history.get("rows"), list):
        raise HistoryError("%s does not look like a sitepulse history file" % path)
    return history


def compare(result, history, date=None):
    """Differences from the previous run of the same site.

    "Previous" means the latest row with an earlier date than ``date``, so
    re-running on the same day keeps the real week-over-week delta instead of
    comparing the site with itself.
    """
    rows = [r for r in history.get("rows", [])
            if r.get("name") == result["name"]
            and (date is None or str(r.get("date", "")) < date)]
    if not rows:
        return {"first_run": True, "score_delta": None, "changed": []}
    last = max(enumerate(rows), key=lambda ir: (str(ir[1].get("date", "")), ir[0]))[1]
    old = {c["id"]: c["status"] for c in last.get("checks", [])}
    changed, seen = [], set()
    for c in result["checks"]:
        seen.add(c["id"])
        before = old.get(c["id"])
        if before != c["status"]:
            changed.append({"id": c["id"], "from": before or "-",
                            "to": c["status"], "value": c["value"]})
    for id_, before in old.items():
        if id_ not in seen:             # check no longer applicable
            changed.append({"id": id_, "from": before, "to": "-", "value": "not run"})
    return {"first_run": False,
            "previous_date": last.get("date"),
            "previous_score": last.get("score"),
            "score_delta": result["score"] - last.get("score", 0),
            "changed": changed}


def record_history(history, date, results):
    """Add this run to the history, replacing rows of the same site and date."""
    names = {r["name"] for r in results}
    rows = [r for r in history.get("rows", [])
            if not (r.get("date") == date and r.get("name") in names)]
    for r in results:
        rows.append({"date": date, "name": r["name"], "score": r["score"],
                     "areas": r["areas"],
                     "measurements": {k: v for k, v in r["measurements"].items()
                                      if not isinstance(v, dict)},
                     "checks": [{"id": c["id"], "status": c["status"]}
                                for c in r["checks"]]})
    rows.sort(key=lambda r: str(r.get("date", "")))     # stable
    history["rows"] = rows[-HISTORY_MAX_ROWS:]
    return history


def _cell(value):
    """Make a value safe inside a markdown table cell."""
    return " ".join(str(value).split()).replace("|", "\\|")


def _delta_text(delta):
    if delta is None:
        return "first run"
    return "+%d" % delta if delta > 0 else str(delta)


def to_markdown(data):
    out = ["# Weekly site data - %s" % data["date"], "",
           "Sites checked: **%d**  " % len(data["sites"]),
           "Average score: **%s/100**" % data["average"], "",
           "| Site | Score | Δ | Problems | Warnings |",
           "|---|---|---|---|---|"]
    for s in data["sites"]:
        problems = sum(1 for x in s["checks"] if x["status"] == PROBLEM)
        warnings = sum(1 for x in s["checks"] if x["status"] == WARN)
        out.append("| %s | %s | %s | %d | %d |" % (
            _cell(s["name"]), s["score"],
            _delta_text(s["comparison"]["score_delta"]), problems, warnings))
    out.append("")
    order = {PROBLEM: 0, WARN: 1, OK: 2, INFO: 3}
    for s in data["sites"]:
        out += ["## %s" % s["name"], "",
                "- URL: %s" % (s["url"] or "(not online)"),
                "- Purpose: %s" % (s["purpose"] or "-"),
                "- Goal: %s" % (s["goal"] or "-"),
                "- Score: **%s/100** (%s)" % (
                    s["score"],
                    ", ".join("%s %s" % kv for kv in s["areas"].items()) or "-")]
        simple = ["%s=%s" % (k, v) for k, v in s["measurements"].items()
                  if not isinstance(v, dict)]
        if simple:
            out.append("- Measurements: " + ", ".join(simple))
        out.append("")
        cmp_ = s["comparison"]
        if cmp_["first_run"]:
            out += ["**First run: nothing to compare with yet.**", ""]
        else:
            delta = cmp_["score_delta"]
            line = "Compared with %s: **%s** (was %s/100)" % (
                cmp_.get("previous_date"), _delta_text(delta), cmp_.get("previous_score"))
            if delta < 0:
                line += " - **REGRESSION**"
            out += [line, ""]
            if cmp_["changed"]:
                out.append("**Changed since the previous run:**")
                for ch in cmp_["changed"]:
                    out.append("- `%s`: %s -> **%s** (%s)" % (
                        ch["id"], ch["from"], ch["to"], ch["value"]))
                out.append("")
            else:
                out += ["No check changed status since the previous run.", ""]
        out += ["| Area | Check | Status | Value | Note |", "|---|---|---|---|---|"]
        for x in sorted(s["checks"],
                        key=lambda x: (order.get(x["status"], 9), x["area"])):
            out.append("| %s | %s | %s | %s | %s |" % (
                x["area"], x["id"], LABELS.get(x["status"], x["status"]),
                _cell(x["value"]), _cell(x["note"])))
        out.append("")
    return "\n".join(out)


def write_json(path, obj):
    """Write JSON atomically, so an interrupted run cannot damage the file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run(sites, data_dir, date, user_agent=None, accept_language=None, now=None,
        out=print):
    """Audit ``sites``, write the data files, update the history.

    Returns (data, markdown_path). Raises HistoryError before any request is
    made if history.json is damaged.
    """
    reports = os.path.join(data_dir, "reports")
    history_path = os.path.join(data_dir, "history.json")
    history = load_history(history_path)
    os.makedirs(reports, exist_ok=True)

    done = []
    for site in sites:
        out("-> %s ..." % (site.get("name") or site.get("url") or "(unnamed)"))
        fetcher = Fetcher(user_agent or site.get("user_agent"),
                          accept_language or site.get("accept_language"))
        res = audit_site(site, fetcher, now=now)
        res["score"] = score(res)
        res["areas"] = area_scores(res)
        res["comparison"] = compare(res, history, date)
        done.append(res)
        problems = sum(1 for x in res["checks"] if x["status"] == PROBLEM)
        out("   %d/100 (%s) - %d problems" % (
            res["score"], _delta_text(res["comparison"]["score_delta"]), problems))

    data = {"date": date,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "average": round(sum(s["score"] for s in done) / len(done)) if done else 0,
            "sites": done}

    write_json(os.path.join(reports, "data-%s.json" % date), data)
    md_path = os.path.join(reports, "DATA-%s.md" % date)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(data))
    write_json(history_path, record_history(history, date, done))
    return data, md_path
