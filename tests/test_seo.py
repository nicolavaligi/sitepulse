import unittest

from sitepulse.seo import capture, render_head


class FixtureFetcher:
    def request(self, url):
        return {
            "final_url": url, "status": 200, "error": None, "truncated": False,
            "headers": {"content-type": "text/html", "x-robots-tag": "noindex"},
            "charset": "utf-8",
            "body": b"""<html lang="it"><head><title>Guide - Source</title>
            <meta property="og:image" content="https://source.test/image.png">
            <meta name="twitter:card" content="summary">
            <link rel="canonical" href="/">
            <link rel="alternate" hreflang="en" href="/en/">
            <script type="application/ld+json">{"@type":"WebPage"}</script>
            </head><body><h1>Guide</h1><h4>Steps</h4>
            <a href="/details">Details</a><img src="/one.png"></body></html>""",
        }


class SEOTransferTest(unittest.TestCase):
    def test_extract_and_adapt_without_importing_source_values(self):
        source = "https://source.test/"
        snapshot = capture([source], FixtureFetcher())
        page = snapshot["pages"][0]
        self.assertEqual(page["canonicals"], [source])
        self.assertEqual(page["x_robots_tag"], "noindex")
        self.assertEqual(page["hreflang"], [{"lang": "en", "url": source + "en/"}])
        self.assertEqual(page["headings"]["h4"], ["Steps"])
        self.assertEqual(page["schema_types"], ["WebPage"])
        self.assertEqual(page["internal_links"], {source + "details": "Details"})
        self.assertEqual(page["images_without_alt"], 1)
        head = render_head(snapshot, source, "https://target.test/page", "A & B",
                           'A "quote"', brand="Target", image="https://target.test/img")
        self.assertIn("<title>A &amp; B - Target</title>", head)
        self.assertIn("A &quot;quote&quot;", head)
        self.assertIn("summary_large_image", head)
        self.assertNotIn("source.test", head)
        self.assertNotIn("noindex", head)
        with self.assertRaises(ValueError):
            render_head(snapshot, source, "javascript:void(0)", "Title", "Description")
