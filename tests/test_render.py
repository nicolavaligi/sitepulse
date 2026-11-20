import os
import tempfile
import unittest
from unittest import mock

import support  # noqa: F401
from sitepulse import audit, render

XSS = '<script>alert("x")</script>'


def site(name="Example", score=80, delta=None, url="https://example.com", **extra):
    s = {"name": name, "url": url, "purpose": "", "goal": "phone call",
         "score": score, "areas": {"seo": 70, "technical": 90},
         "measurements": {},
         "checks": [
             {"area": "seo", "id": "title", "status": "problem", "value": "0 chars",
              "note": "missing"},
             {"area": "technical", "id": "online", "status": "ok", "value": "HTTP 200",
              "note": ""}],
         "comparison": {"first_run": delta is None, "score_delta": delta, "changed": []}}
    s.update(extra)
    return s


def data(*sites):
    return {"date": "2026-01-12", "average": 80, "sites": list(sites) or [site()]}


class InlineMarkdownTest(unittest.TestCase):

    def test_bold_code_link(self):
        self.assertEqual(render.inline("a **b** `c<d>` e"),
                         "a <strong>b</strong> <code>c&lt;d&gt;</code> e")
        self.assertEqual(
            render.inline("[docs](https://example.com/?a=1&b=2)"),
            '<a href="https://example.com/?a=1&amp;b=2" rel="noopener noreferrer">docs</a>')
        self.assertIn('href="mailto:info@example.com"',
                      render.inline("[write](mailto:info@example.com)"))

    def test_raw_html_is_escaped(self):
        self.assertEqual(render.inline(XSS),
                         "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;")

    def test_only_http_https_mailto_links(self):
        for target in ("javascript:alert(1)", "JaVaScRiPt:alert(1)",
                       "data:text/html,<script>1</script>", "vbscript:x",
                       "file:///etc/passwd", "/relative", "//example.com/x"):
            out = render.inline("[click](%s)" % target)
            self.assertNotIn("<a ", out, target)
            self.assertNotIn("href", out, target)
            self.assertIn("click", out)

    def test_href_cannot_break_out_of_the_attribute(self):
        out = render.inline('[x](https://example.com/"onmouseover="alert(1))')
        self.assertNotIn('"onmouseover="', out)
        self.assertEqual(out.count("<a "), 1)

    def test_code_span_content_is_not_interpreted(self):
        self.assertEqual(render.inline("`**x** [a](https://example.com)`"),
                         "<code>**x** [a](https://example.com)</code>")


class BlockMarkdownTest(unittest.TestCase):

    def test_blocks(self):
        html = render.md_to_html(
            "# Title\n\nParagraph **one**.\n\n- a\n- b\n\n1. first\n2. second\n\n"
            "| A | B |\n|---|:-:|\n| 1 | 2 |\n")
        self.assertIn("<h2>Title</h2>", html)
        self.assertIn("<p>Paragraph <strong>one</strong>.</p>", html)
        self.assertIn("<ul>\n<li>a</li>\n<li>b</li>\n</ul>", html)
        self.assertIn("<ol>\n<li>first</li>\n<li>second</li>\n</ol>", html)
        self.assertIn("<thead><tr><th>A</th><th>B</th></tr></thead>", html)
        self.assertIn("<tr><td>1</td><td>2</td></tr>", html)
        self.assertNotIn("---", html)
        self.assertTrue(html.endswith("</tbody></table>"))

    def test_html_in_every_block_type_is_escaped(self):
        html = render.md_to_html("# %s\n%s\n- %s\n\n| %s |\n|---|\n| %s |"
                                 % ((XSS,) * 5))
        self.assertNotIn("<script", html)
        self.assertEqual(html.count("&lt;script&gt;"), 5)


class ChartTest(unittest.TestCase):

    def test_bar_chart(self):
        svg = render.bar_chart([("One", 40, "#111"), ("Two", 250, "#222")])
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        self.assertEqual(svg.count("<rect"), 2)
        self.assertEqual(render.bar_chart([]), "")

    def test_label_is_truncated_before_escaping(self):
        # 28 chars + "&&&": cutting the escaped text at 30 would leave "&am"
        svg = render.bar_chart([("x" * 28 + "&&&", 50, "#111")])
        self.assertIn("x" * 28 + "&amp;&amp;<", svg)

    def test_trend_chart(self):
        self.assertEqual(render.trend_chart([("2026-01-05", 50)]), "")
        svg = render.trend_chart([("2026-01-05", 50), ("2026-01-12", 70)])
        self.assertIn("<polyline", svg)
        self.assertEqual(svg.count("<circle"), 2)
        self.assertIn(">01-12<", svg)
        self.assertNotIn("<b>", render.trend_chart([("2026-<b>", 1), ("2026-01-12", 2)]))


class ReportTest(unittest.TestCase):

    def test_basic_structure(self):
        html = render.build_report(data())
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn('<html lang="en">', html)
        self.assertIn("<title>Weekly site check — 2026-01-12</title>", html)
        self.assertIn("Score by site", html)
        self.assertIn("first run", html)
        self.assertIn('<a href="https://example.com" rel="noopener noreferrer">', html)
        self.assertIn("goal: phone call", html)
        self.assertIn("<code>title</code>", html)
        self.assertNotIn("<code>online</code>", html)       # OK checks are not listed

    def test_no_powered_by_footer_and_no_external_resources(self):
        html = render.build_report(data())
        self.assertNotIn("Powered by", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<link", html)

    def test_regression_is_highlighted(self):
        html = render.build_report(data(site(delta=-12), site("Other", delta=4),
                                        site("Flat", delta=0)))
        self.assertIn('class="card reg"', html)
        self.assertIn("-12 points — regression", html)
        self.assertIn("+4 points", html)
        self.assertIn("unchanged", html)
        self.assertEqual(html.count('class="card reg"'), 1)
        self.assertIn(">1</div><div class=\"t\">Regressions<", html)

    def test_lowest_score_first(self):
        html = render.build_report(data(site("High", score=95), site("Low", score=20)))
        detail = html[html.index("Detail by site"):]
        self.assertLess(detail.index("Low"), detail.index("High"))

    def test_all_ok_site(self):
        s = site()
        s["checks"] = [c for c in s["checks"] if c["status"] == "ok"]
        self.assertIn("Nothing flagged", render.build_report(data(s)))

    def test_analysis_is_included_before_the_numbers(self):
        html = render.build_report(data(), "## Verdict\n\n1. Fix the **title**")
        self.assertIn('<div class="analysis"><h3>Verdict</h3>', html)
        self.assertLess(html.index('class="analysis"'), html.index("Score by site"))
        self.assertNotIn('class="analysis"', render.build_report(data(), "  \n"))

    def test_trend_needs_two_runs(self):
        rows = [{"date": "2026-01-05", "score": 60}, {"date": "2026-01-12", "score": 80}]
        self.assertIn("Average score over time",
                      render.build_report(data(), "", {"rows": rows}))
        self.assertNotIn("Average score over time",
                         render.build_report(data(), "", {"rows": rows[:1]}))

    def test_everything_from_the_data_is_escaped(self):
        s = site(name=XSS, url='https://example.com/"><script>alert(1)</script>',
                 goal=XSS, areas={XSS: 50})
        s["checks"] = [{"area": XSS, "id": XSS, "status": "problem", "value": XSS,
                        "note": XSS}]
        s["comparison"] = {"first_run": False, "score_delta": -5, "changed": [
            {"id": XSS, "from": XSS, "to": XSS, "value": XSS}]}
        d = data(s)
        d["date"] = XSS
        history = {"rows": [{"date": "2026-01-05", "score": 1}, {"date": XSS, "score": 2}]}
        html = render.build_report(d, "Intro %s [x](javascript:alert(1))" % XSS, history)
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:alert(1)\"", html)
        self.assertNotIn("<a href=\"javascript", html)
        self.assertIn("&lt;script&gt;", html)

    def test_non_http_site_url_is_not_linked(self):
        html = render.build_report(data(site(url="javascript:alert(1)")))
        self.assertNotIn('href="javascript', html)
        self.assertIn("javascript:alert(1) · ", html)


class RenderFilesTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.reports = os.path.join(self.dir, "reports")
        os.makedirs(self.reports)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, date):
        d = data()
        d["date"] = date
        audit.write_json(os.path.join(self.reports, "data-%s.json" % date), d)

    def test_no_data(self):
        with self.assertRaises(FileNotFoundError):
            render.render(self.dir)
        self.write("2026-01-12")
        with self.assertRaises(FileNotFoundError):
            render.render(self.dir, "2026-02-02")

    def test_latest_run_by_default_and_specific_date(self):
        self.write("2026-01-05")
        self.write("2026-01-12")
        self.assertTrue(render.render(self.dir).endswith("REPORT-2026-01-12.html"))
        path = render.render(self.dir, "2026-01-05")
        self.assertTrue(path.endswith("REPORT-2026-01-05.html"))
        self.assertTrue(os.path.exists(path))

    def test_analysis_file_is_picked_up(self):
        self.write("2026-01-12")
        with open(os.path.join(self.reports, "ANALYSIS-2026-01-12.md"), "w") as f:
            f.write("# The week in three lines\n\n- most urgent: fix the title\n")
        with open(render.render(self.dir), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("The week in three lines", html)
        self.assertIn("<li>most urgent: fix the title</li>", html)

    def test_damaged_history_only_skips_the_trend(self):
        self.write("2026-01-12")
        with open(os.path.join(self.dir, "history.json"), "w") as f:
            f.write("{truncated")
        with self.assertLogs("sitepulse.render", level="WARNING"):
            path = render.render(self.dir)
        self.assertTrue(os.path.exists(path))

    def test_open_uses_webbrowser(self):
        self.write("2026-01-12")
        with mock.patch("webbrowser.open") as opened:
            path = render.render(self.dir, open_browser=True)
        opened.assert_called_once()
        uri = opened.call_args[0][0]
        self.assertTrue(uri.startswith("file://"))
        self.assertTrue(uri.endswith(os.path.basename(path)))
        with mock.patch("webbrowser.open") as opened:
            render.render(self.dir)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
