import unittest

import support  # noqa: F401  (puts the repository root on sys.path)
from sitepulse import audit

PAGE = """<!doctype html>
<html lang="en-GB">
<head>
<title>Page &amp; title</title>
<meta name="Description" content="A description">
<meta property="og:title" content="OG title">
<meta name="viewport" content="width=device-width">
<link rel="canonical" href="https://example.com/">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="stylesheet" href="/a.css">
<script src="/app.js"></script>
<script>var hidden = "not visible text";</script>
<style>p{color:red}</style>
<script type="application/ld+json">{"@type": "Article", "articleBody": "<p>one</p><p>two</p>"}</script>
</head>
<body>
<svg><title>Icon label</title><text>svg text</text></svg>
<h1>Main <em>heading</em></h1>
<h2>First</h2><h2>Second</h2><h3>Third</h3>
<p>Some visible words here.</p>
<noscript>fallback text</noscript>
<a href="/about"> About <b>us</b> </a>
<a href="tel:+15550100">Phone</a>
<img src="/a.png" alt="An image" width="10" height="20" loading="lazy">
<img src="/b.png">
<form><input name="q"><textarea></textarea><input type="submit" value="Send request"></form>
<iframe src="https://example.org/embed"></iframe>
</body></html>
"""


def parse(html):
    p = audit.PageParser()
    p.feed(html)
    p.close()
    return p


class PageParserTest(unittest.TestCase):

    def setUp(self):
        self.p = parse(PAGE)

    def test_title_ignores_svg_title(self):
        self.assertEqual(self.p.title, "Page & title")

    def test_svg_title_is_not_used_when_the_page_has_no_title(self):
        p = parse("<html><body><svg><title>Icon</title></svg><p>x</p></body></html>")
        self.assertEqual(p.title, "")

    def test_only_first_title_counts(self):
        p = parse("<title>One</title><title>Two</title>")
        self.assertEqual(p.title, "One")

    def test_lang_and_meta(self):
        self.assertEqual(self.p.lang, "en-GB")
        self.assertEqual(self.p.meta["description"], "A description")
        self.assertEqual(self.p.meta["og:title"], "OG title")
        self.assertIn("viewport", self.p.meta)

    def test_links_scripts_stylesheets(self):
        self.assertIn(("canonical", "https://example.com/"), self.p.links)
        self.assertIn(("shortcut icon", "/favicon.ico"), self.p.links)
        self.assertEqual(self.p.stylesheets, ["/a.css"])
        self.assertEqual(self.p.scripts, ["/app.js"])

    def test_headings_include_nested_inline_text(self):
        self.assertEqual([h.strip() for h in self.p.headings["h1"]], ["Main heading"])
        self.assertEqual(len(self.p.headings["h2"]), 2)
        self.assertEqual(len(self.p.headings["h3"]), 1)

    def test_anchors(self):
        self.assertIn(("/about", "About us"), self.p.anchors)
        self.assertIn(("tel:+15550100", "Phone"), self.p.anchors)

    def test_images(self):
        self.assertEqual(len(self.p.images), 2)
        self.assertEqual(self.p.images[0]["alt"], "An image")
        self.assertEqual(self.p.images[0]["width"], "10")
        self.assertIsNone(self.p.images[1]["alt"])

    def test_forms_inputs_iframes(self):
        self.assertEqual(self.p.forms, 1)
        self.assertEqual(self.p.inputs, 3)
        self.assertEqual(self.p.button_labels, ["Send request"])
        self.assertEqual(self.p.iframes, ["https://example.org/embed"])

    def test_visible_text_excludes_script_style_svg_noscript(self):
        text = " ".join(self.p.text)
        self.assertIn("Some visible words here.", text)
        for hidden in ("not visible", "color:red", "svg text", "fallback text",
                       "Icon label", "articleBody"):
            self.assertNotIn(hidden, text)
        self.assertGreater(self.p.inline_script_chars, 0)
        self.assertGreater(self.p.inline_css_chars, 0)

    def test_jsonld_block_with_markup_inside_strings(self):
        self.assertEqual(len(self.p.jsonld), 1)
        self.assertEqual(audit.jsonld_types(self.p.jsonld), ["Article"])

    def test_jsonld_block_fed_in_pieces_stays_whole(self):
        p = audit.PageParser()
        for piece in ('<script type="application/ld+json">{"@type": ',
                      '"Article", "name": "x"}', "</script><p>after</p>"):
            p.feed(piece)
        p.close()
        self.assertEqual(len(p.jsonld), 1)
        self.assertEqual(audit.jsonld_types(p.jsonld), ["Article"])
        self.assertEqual(p.text, ["after"])


class HelpersTest(unittest.TestCase):

    def test_jsonld_types(self):
        blocks = ['{"@type": "LocalBusiness"}',
                  '[{"@type": ["FAQPage", "WebPage"]}]',
                  '{"@graph": [{"@type": "Person"}, "junk"]}',
                  '{"@graph": "not a list"}',
                  "{broken"]
        self.assertEqual(
            audit.jsonld_types(blocks),
            sorted(["LocalBusiness", "FAQPage", "WebPage", "Person",
                    audit.INVALID_JSONLD]))

    def test_same_host_is_equality_not_substring(self):
        site = "https://www.example.com/"
        self.assertTrue(audit.same_host("https://example.com/a.js", site))
        self.assertTrue(audit.same_host("https://WWW.EXAMPLE.COM:8443/a.js", site))
        self.assertFalse(audit.same_host("https://cdn.example.com/a.js", site))
        # these three were "internal" with a substring comparison
        self.assertFalse(audit.same_host("https://ample.com/a.js", site))
        self.assertFalse(audit.same_host("https://example.co/a.js", site))
        self.assertFalse(audit.same_host("https://e.com/a.js", site))
        self.assertFalse(audit.same_host("mailto:someone", site))

    def test_same_host_accepts_several_site_urls(self):
        self.assertTrue(audit.same_host("https://example.org/x", "https://example.com",
                                        "https://www.example.org/"))

    def test_to_ascii_url(self):
        self.assertEqual(audit.to_ascii_url("https://example.com/café menu?q=tè#top"),
                         "https://example.com/caf%C3%A9%20menu?q=t%C3%A8")
        self.assertEqual(audit.to_ascii_url("https://example.com/a%20b"),
                         "https://example.com/a%20b")
        self.assertEqual(audit.to_ascii_url("https://bücher.example/x"),
                         "https://xn--bcher-kva.example/x")

    def test_normalize_url(self):
        self.assertEqual(audit.normalize_url("example.com"),
                         ("https://example.com/", "https://example.com"))
        self.assertEqual(audit.normalize_url(" https://example.com/ "),
                         ("https://example.com/", "https://example.com"))
        self.assertEqual(audit.normalize_url("https://example.com/blog/"),
                         ("https://example.com/blog/", "https://example.com/blog/"))
        self.assertEqual(audit.normalize_url(""), ("", ""))

    def test_cta_matching_is_whole_word(self):
        words = audit.resolve_cta_words({})
        self.assertEqual(audit.find_cta_words("Trying to bookkeep for callers", words), [])
        self.assertEqual(audit.find_cta_words("Call  us today or BOOK online.", words),
                         ["book", "call", "call us"])
        self.assertEqual(audit.find_cta_words("Contattaci, oppure prenota subito", words),
                         ["contattaci", "prenota"])

    def test_cta_words_selection(self):
        self.assertNotIn("prenota", audit.resolve_cta_words({"cta_languages": ["en"]}))
        self.assertIn("prenota", audit.resolve_cta_words({"cta_languages": ["it"]}))
        both = audit.resolve_cta_words({})
        self.assertIn("prenota", both)
        self.assertIn("book", both)
        self.assertEqual(audit.resolve_cta_words({"cta_words": ["Reserve ", ""],
                                                  "cta_languages": ["it"]}), ["reserve"])


if __name__ == "__main__":
    unittest.main()
