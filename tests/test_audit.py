"""Audit checks against local fixture websites (no internet)."""

import os
import tempfile
import time
import unittest
from datetime import datetime

import support
from sitepulse import audit

NOW = datetime(2026, 6, 1)


class GoodSiteTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.site = support.good_site()
        cls.fetcher = audit.Fetcher()
        cls.res = audit.audit_site(
            {"name": "Good", "url": cls.site.url, "goal": "phone call"},
            cls.fetcher, now=NOW)
        cls.st = support.statuses(cls.res)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def test_reachable(self):
        self.assertTrue(self.res["reachable"])
        self.assertEqual(self.res["url"], self.site.url)
        self.assertEqual(self.res["measurements"]["status"], 200)

    def test_everything_ok_except_plain_http(self):
        not_ok = {k: v for k, v in self.st.items() if v != audit.OK}
        self.assertEqual(not_ok, {"https": audit.PROBLEM})

    def test_technical(self):
        for id_ in ("online", "response-time", "page-404", "image-dimensions",
                    "page-weight", "external-dependencies", "broken-resources",
                    "broken-links", "mobile-viewport"):
            self.assertEqual(self.st[id_], audit.OK, id_)
            self.assertEqual(support.check(self.res, id_)["area"], audit.TECHNICAL)
        self.assertEqual(support.check(self.res, "page-404")["value"], "HTTP 404")

    def test_seo(self):
        for id_ in ("title", "meta-description", "lang", "canonical", "h1",
                    "heading-structure", "structured-data", "social-sharing",
                    "robots", "sitemap"):
            self.assertEqual(self.st[id_], audit.OK, id_)
            self.assertEqual(support.check(self.res, id_)["area"], audit.SEO)
        self.assertEqual(self.res["measurements"]["title"],
                         "Example Plumbing | Emergency repairs in Springfield")
        self.assertEqual(support.check(self.res, "structured-data")["value"],
                         "FAQPage, LocalBusiness, WebPage")
        self.assertEqual(support.check(self.res, "h1")["note"],
                         "Emergency plumbing in Springfield")

    def test_sitemap_declared_in_robots_is_used(self):
        sm = support.check(self.res, "sitemap")
        self.assertEqual(sm["value"], "3 URLs")
        self.assertTrue(sm["note"].endswith("/sitemap-main.xml"))
        self.assertEqual(support.check(self.res, "robots")["note"], "declares the sitemap")

    def test_content(self):
        for id_ in ("image-alt", "favicon", "text-amount", "stale-years"):
            self.assertEqual(self.st[id_], audit.OK, id_)
            self.assertEqual(support.check(self.res, id_)["area"], audit.CONTENT)
        self.assertGreaterEqual(self.res["measurements"]["words"], 300)

    def test_conversion(self):
        for id_ in ("contact-channels", "call-to-action", "analytics"):
            self.assertEqual(self.st[id_], audit.OK, id_)
            self.assertEqual(support.check(self.res, id_)["area"], audit.CONVERSION)
        self.assertEqual(self.res["measurements"]["contact"],
                         {"tel": 1, "mail": 1, "whatsapp": 0, "forms": 1})
        cta = support.check(self.res, "call-to-action")["value"]
        self.assertIn("call us", cta)
        self.assertIn("request", cta)       # from the submit button label
        self.assertEqual(support.check(self.res, "analytics")["value"], "Plausible")

    def test_security(self):
        self.assertEqual(self.st["security-headers"], audit.OK)
        self.assertEqual(support.check(self.res, "security-headers")["value"], "5/5 present")
        self.assertEqual(support.check(self.res, "https")["area"], audit.SECURITY)

    def test_assets_are_deduplicated_and_data_uris_skipped(self):
        # stylesheet + script + one logo (used twice); the data: image is ignored
        self.assertEqual(self.res["measurements"]["resources_weighed"], 3)
        self.assertEqual(self.site.paths("HEAD").count("/logo.png"), 1)

    def test_server_refusing_head_is_not_a_broken_resource(self):
        self.assertIn("/style.css", self.site.paths("HEAD"))
        self.assertIn("/style.css", self.site.paths("GET"))
        self.assertEqual(support.check(self.res, "broken-resources")["value"], "0")
        self.assertGreater(self.res["measurements"]["weight_kb"], 2.0)

    def test_links(self):
        m = self.res["measurements"]
        self.assertEqual(m["internal_links"], 2)      # /about (once), /café
        self.assertEqual(m["external_links"], 1)      # localhost = another host
        self.assertEqual(support.check(self.res, "broken-links")["value"],
                         "0 broken of 3 probed")

    def test_non_ascii_link_is_requested_not_reported_broken(self):
        self.assertIn("/café", self.site.paths())

    def test_non_http_links_are_never_requested(self):
        for path in self.site.paths():
            self.assertTrue(path.startswith("/"), path)
        self.assertNotIn("/contact", self.site.paths())

    def test_honest_user_agent_and_language(self):
        headers = self.site.hits[0][2]
        self.assertEqual(headers["User-Agent"], audit.DEFAULT_USER_AGENT)
        self.assertTrue(headers["User-Agent"].startswith("sitepulse/"))
        self.assertNotIn("Mozilla", headers["User-Agent"])
        self.assertEqual(headers["Accept-Language"], "en")

    def test_request_count_stays_within_politeness_limits(self):
        limit = 4 + 2 * (audit.MAX_ASSETS + audit.MAX_INTERNAL_LINKS
                         + audit.MAX_EXTERNAL_LINKS) + 3
        self.assertLessEqual(len(self.site.hits), limit)


class BadSiteTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.site = support.bad_site()
        cls.res = audit.audit_site({"name": "Bad", "url": cls.site.url},
                                   audit.Fetcher(), now=NOW)
        cls.st = support.statuses(cls.res)

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def test_expected_statuses(self):
        P, W, K = audit.PROBLEM, audit.WARN, audit.OK
        expected = {
            "online": K, "response-time": K, "page-weight": K,
            "https": P, "security-headers": P,
            "title": P, "meta-description": P, "lang": W, "canonical": W,
            "h1": P, "heading-structure": W, "structured-data": W,
            "social-sharing": P, "robots": W, "sitemap": W,
            "page-404": W, "image-alt": P, "image-dimensions": W,
            "external-dependencies": W, "broken-resources": P, "broken-links": P,
            "contact-channels": P, "call-to-action": W, "analytics": W,
            "favicon": W, "text-amount": P, "mobile-viewport": P,
            "stale-years": W,
        }
        self.assertEqual(self.st, expected)

    def test_soft_404_and_html_robots_are_detected(self):
        self.assertEqual(support.check(self.res, "page-404")["value"], "HTTP 200")
        self.assertEqual(support.check(self.res, "robots")["value"], "missing")
        self.assertEqual(support.check(self.res, "sitemap")["value"], "missing")

    def test_svg_title_does_not_count_as_page_title(self):
        self.assertEqual(support.check(self.res, "title")["value"], "0 chars")

    def test_invalid_jsonld_is_not_ok(self):
        self.assertEqual(support.check(self.res, "structured-data")["value"],
                         audit.INVALID_JSONLD)

    def test_external_dependencies_counted_by_host(self):
        self.assertEqual(support.check(self.res, "external-dependencies")["value"],
                         "4 from other hosts")

    def test_broken_resource_and_link_are_named(self):
        self.assertIn("css 404", support.check(self.res, "broken-resources")["note"])
        self.assertEqual(support.check(self.res, "broken-resources")["value"], "1")
        bl = support.check(self.res, "broken-links")
        self.assertEqual(bl["value"], "1 broken of 1 probed")
        self.assertIn("404: %s/gone" % self.site.url, bl["note"])

    def test_stale_year_and_whole_word_cta(self):
        self.assertEqual(support.check(self.res, "stale-years")["value"], "2019")
        self.assertEqual(support.check(self.res, "call-to-action")["value"], "none")

    def test_score_is_low(self):
        self.assertLess(audit.score(self.res), 40)


class OtherBehaviourTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.site = support.good_site()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def test_unreachable_site(self):
        url = "http://127.0.0.1:%d" % support.closed_port()
        res = audit.audit_site({"name": "Down", "url": url}, audit.Fetcher(timeout=5))
        self.assertFalse(res["reachable"])
        self.assertEqual(support.statuses(res), {"online": audit.PROBLEM})
        self.assertEqual(support.check(res, "online")["value"], "unreachable")
        self.assertEqual(audit.score(res), 0)

    def test_home_answering_with_an_error(self):
        res = audit.audit_site({"name": "Err", "url": self.site.url + "/500"},
                               audit.Fetcher())
        self.assertEqual(support.statuses(res), {"online": audit.PROBLEM})
        self.assertEqual(support.check(res, "online")["value"], "HTTP 500")

    def test_missing_url(self):
        res = audit.audit_site({"name": "Draft", "url": ""})
        self.assertEqual(support.statuses(res), {"missing-url": audit.PROBLEM})
        self.assertEqual(res["name"], "Draft")

    def test_unsupported_scheme_is_not_fetched(self):
        res = audit.audit_site({"name": "File", "url": "file:///etc/hosts"})
        self.assertEqual(support.check(res, "online")["value"], "unreachable")

    def test_url_with_a_path_probes_robots_at_the_host_root(self):
        self.site.hits.clear()
        res = audit.audit_site({"name": "Sub", "url": self.site.url + "/about"},
                               audit.Fetcher(), now=NOW)
        paths = self.site.paths()
        self.assertEqual(paths[0], "/about")
        self.assertIn("/robots.txt", paths)
        self.assertIn(audit.NOT_FOUND_PROBE, paths)
        self.assertNotIn("/about/robots.txt", paths)
        self.assertEqual(support.statuses(res)["robots"], audit.OK)

    def test_custom_user_agent_and_language(self):
        self.site.hits.clear()
        audit.audit_site({"name": "UA", "url": self.site.url + "/about"},
                         audit.Fetcher("my-bot/2.0", "it"), now=NOW)
        headers = self.site.hits[0][2]
        self.assertEqual(headers["User-Agent"], "my-bot/2.0")
        self.assertEqual(headers["Accept-Language"], "it")

    def test_site_level_cta_words(self):
        res = audit.audit_site(
            {"name": "Words", "url": self.site.url, "cta_words": ["emergency plumbing"]},
            audit.Fetcher(), now=NOW)
        self.assertEqual(support.check(res, "call-to-action")["value"], "emergency plumbing")
        res = audit.audit_site(
            {"name": "Words", "url": self.site.url, "cta_words": ["zzz"]},
            audit.Fetcher(), now=NOW)
        self.assertEqual(support.statuses(res)["call-to-action"], audit.WARN)

    def test_last_modified_from_local_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "index.html")
            with open(f, "w") as fh:
                fh.write("x")
            old = time.time() - 100 * 86400
            os.utime(f, (old, old))
            skipped = os.path.join(tmp, "node_modules")
            os.mkdir(skipped)
            with open(os.path.join(skipped, "fresh.js"), "w") as fh:
                fh.write("x")
            res = audit.audit_site({"name": "Local", "url": self.site.url, "path": tmp},
                                   audit.Fetcher())
        lm = support.check(res, "last-modified")
        self.assertEqual(lm["status"], audit.PROBLEM)
        self.assertEqual(lm["area"], audit.CONTENT)
        self.assertNotIn("path", res)          # local paths never reach the reports

    def test_resource_size_and_is_broken(self):
        f = audit.Fetcher()
        size, status, error = f.resource_size(self.site.url + "/logo.png")
        self.assertEqual((size, status, error), (2004, 200, None))
        size, status, error = f.resource_size(self.site.url + "/nope.png")
        self.assertEqual((size, status), (None, 404))
        self.assertIsNone(f.is_broken(self.site.url + "/style.css"))   # HEAD -> 405
        self.assertEqual(f.is_broken(self.site.url + "/nope"), "404")
        self.assertEqual(
            f.is_broken("http://127.0.0.1:%d/" % support.closed_port()), "error")


if __name__ == "__main__":
    unittest.main()
