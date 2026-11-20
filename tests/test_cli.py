"""End-to-end: the command line against the local fixture websites."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import support
from sitepulse import audit, cli


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:             # argparse errors
            code = e.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.good = support.good_site()
        cls.bad = support.bad_site()

    @classmethod
    def tearDownClass(cls):
        cls.good.close()
        cls.bad.close()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.dir = os.path.join(self.tmp, "data")
        self.reports = os.path.join(self.dir, "reports")

    def tearDown(self):
        self._tmp.cleanup()

    def read_json(self, *parts):
        with open(os.path.join(self.dir, *parts), encoding="utf-8") as f:
            return json.load(f)

    def write_config(self, cfg):
        path = os.path.join(self.tmp, "sites.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        return path

    # ---------------------------------------------------------------- --url

    def test_audit_url_end_to_end(self):
        code, out, err = run_cli("audit", "--url", self.good.url, "--dir", self.dir,
                                 "--date", "2026-01-05")
        self.assertEqual(code, 0, err)
        self.assertIn("-> 127.0.0.1:%d ..." % self.good.port, out)
        self.assertIn("(first run)", out)
        self.assertEqual(sorted(os.listdir(self.reports)),
                         ["DATA-2026-01-05.md", "data-2026-01-05.json"])
        data = self.read_json("reports", "data-2026-01-05.json")
        self.assertEqual(data["date"], "2026-01-05")
        self.assertEqual(len(data["sites"]), 1)
        s = data["sites"][0]
        self.assertEqual(s["name"], "127.0.0.1:%d" % self.good.port)
        self.assertEqual(data["average"], s["score"])
        self.assertGreater(s["score"], 90)
        self.assertTrue(s["comparison"]["first_run"])
        history = self.read_json("history.json")
        self.assertEqual(len(history["rows"]), 1)
        self.assertEqual(history["rows"][0]["score"], s["score"])
        with open(os.path.join(self.reports, "DATA-2026-01-05.md"), encoding="utf-8") as f:
            self.assertIn("# Weekly site data - 2026-01-05", f.read())

    def test_run_url_produces_html_report(self):
        code, out, err = run_cli("run", "--url", self.good.url, "--dir", self.dir,
                                 "--date", "2026-01-05")
        self.assertEqual(code, 0, err)
        report = os.path.join(self.reports, "REPORT-2026-01-05.html")
        self.assertEqual(out.strip().splitlines()[-1], report)
        with open(report, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("127.0.0.1:%d" % self.good.port, html)
        self.assertIn("<svg", html)

    def test_run_open_uses_webbrowser(self):
        with mock.patch("webbrowser.open") as opened:
            code, _, err = run_cli("run", "--url", self.good.url, "--dir", self.dir,
                                   "--open")
        self.assertEqual(code, 0, err)
        opened.assert_called_once()

    def test_python_dash_m_entry_point(self):
        proc = subprocess.run(
            [sys.executable, "-m", "sitepulse", "run", "--url", self.good.url,
             "--dir", self.dir, "--date", "2026-01-05"],
            cwd=support.ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.reports, "REPORT-2026-01-05.html")))
        self.assertEqual(proc.stderr, "")

    def test_user_agent_and_language_flags(self):
        self.good.hits.clear()
        code, _, err = run_cli("audit", "--url", self.good.url, "--dir", self.dir,
                               "--user-agent", "custom-agent/9", "--accept-language", "it")
        self.assertEqual(code, 0, err)
        self.assertEqual({h["User-Agent"] for _, _, h in self.good.hits}, {"custom-agent/9"})
        self.assertEqual({h["Accept-Language"] for _, _, h in self.good.hits}, {"it"})

    def test_verbose_logs_to_stderr_only(self):
        code, out, err = run_cli("audit", "--url", self.good.url, "--dir", self.dir, "-v")
        self.assertEqual(code, 0)
        self.assertIn("DEBUG sitepulse.audit: GET", err)
        self.assertNotIn("DEBUG", out)
        code, out, err = run_cli("audit", "--url", self.good.url, "--dir", self.dir)
        self.assertEqual(err, "")

    # --------------------------------------------------------------- config

    def config(self):
        return {
            "accept_language": "it",
            "cta_languages": ["it"],
            "sites": [
                {"name": "Good site", "url": self.good.url, "goal": "phone call",
                 "purpose": "leads", "cta_languages": ["en"], "accept_language": "en"},
                {"name": "Bad site", "url": self.bad.url, "path": ""},
                {"name": "Parked", "url": "http://127.0.0.1:1", "active": False},
            ]}

    def test_config_run_and_regression_on_the_second_week(self):
        cfg = self.write_config(self.config())
        self.good.hits.clear()
        self.bad.hits.clear()
        code, out, err = run_cli("run", "--config", cfg, "--dir", self.dir,
                                 "--date", "2026-01-05")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Parked", out)
        data = self.read_json("reports", "data-2026-01-05.json")
        by_name = {s["name"]: s for s in data["sites"]}
        self.assertEqual(sorted(by_name), ["Bad site", "Good site"])
        self.assertEqual(by_name["Good site"]["goal"], "phone call")
        self.assertGreater(by_name["Good site"]["score"], by_name["Bad site"]["score"])
        # per-site settings win over the config-level defaults
        self.assertEqual(self.good.hits[0][2]["Accept-Language"], "en")
        self.assertEqual(self.bad.hits[0][2]["Accept-Language"], "it")
        self.assertEqual(support.statuses(by_name["Good site"])["call-to-action"], audit.OK)

        # week two: the good site loses its title -> regression
        original = self.good.routes["/"]
        self.good.routes["/"] = (200, original[1], original[2].replace(
            "<title>Example Plumbing | Emergency repairs in Springfield</title>", ""))
        try:
            code, out, err = run_cli("run", "--config", cfg, "--dir", self.dir,
                                     "--date", "2026-01-12", "--only", "good")
        finally:
            self.good.routes["/"] = original
        self.assertEqual(code, 0, err)
        data = self.read_json("reports", "data-2026-01-12.json")
        self.assertEqual([s["name"] for s in data["sites"]], ["Good site"])
        cmp_ = data["sites"][0]["comparison"]
        self.assertFalse(cmp_["first_run"])
        self.assertEqual(cmp_["previous_date"], "2026-01-05")
        self.assertLess(cmp_["score_delta"], 0)
        self.assertEqual([(c["id"], c["from"], c["to"]) for c in cmp_["changed"]],
                         [("title", "ok", "problem")])
        with open(os.path.join(self.reports, "DATA-2026-01-12.md"), encoding="utf-8") as f:
            self.assertIn("**REGRESSION**", f.read())
        with open(os.path.join(self.reports, "REPORT-2026-01-12.html"),
                  encoding="utf-8") as f:
            html = f.read()
        self.assertIn('class="card reg"', html)
        self.assertIn("Average score over time", html)
        self.assertEqual(len(self.read_json("history.json")["rows"]), 3)

        # render alone picks the latest run
        code, out, _ = run_cli("render", "--dir", self.dir)
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().endswith("REPORT-2026-01-12.html"))

    def test_config_level_cta_words(self):
        cfg = self.config()
        cfg["cta_words"] = ["zzz-never-there"]
        del cfg["sites"][0]["cta_languages"]
        code, _, err = run_cli("audit", "--config", self.write_config(cfg), "--dir",
                               self.dir, "--date", "2026-01-05", "--only", "Good")
        self.assertEqual(code, 0, err)
        s = self.read_json("reports", "data-2026-01-05.json")["sites"][0]
        self.assertEqual(support.statuses(s)["call-to-action"], audit.WARN)

    # --------------------------------------------------------------- errors

    def test_missing_config(self):
        code, _, err = run_cli("audit", "--config", os.path.join(self.tmp, "nope.json"),
                               "--dir", self.dir)
        self.assertEqual(code, 2)
        self.assertIn("config file not found", err)
        self.assertIn("sites.example.json", err)
        self.assertFalse(os.path.exists(self.dir))

    def test_invalid_config_and_no_match(self):
        code, _, err = run_cli("audit", "--config", self.write_config(["x"]),
                               "--dir", self.dir)
        self.assertEqual(code, 2)
        self.assertIn('"sites" list', err)
        code, _, err = run_cli("audit", "--config", self.write_config(self.config()),
                               "--dir", self.dir, "--only", "nothing-matches")
        self.assertEqual(code, 2)
        self.assertIn("no site to check", err)

    def test_invalid_date_is_rejected(self):
        code, _, err = run_cli("audit", "--url", self.good.url, "--dir", self.dir,
                               "--date", "../../escape")
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)
        self.assertFalse(os.path.exists(self.dir))

    def test_render_without_data(self):
        code, _, err = run_cli("render", "--dir", self.dir)
        self.assertEqual(code, 2)
        self.assertIn("run `sitepulse audit` first", err)

    def test_damaged_history_is_never_overwritten(self):
        os.makedirs(self.dir)
        path = os.path.join(self.dir, "history.json")
        with open(path, "w") as f:
            f.write("{truncated")
        self.good.hits.clear()
        code, _, err = run_cli("audit", "--url", self.good.url, "--dir", self.dir)
        self.assertEqual(code, 2)
        self.assertIn("history.json", err)
        with open(path) as f:
            self.assertEqual(f.read(), "{truncated")
        self.assertEqual(self.good.hits, [])        # failed before any request

    def test_no_command_prints_help(self):
        code, _, err = run_cli()
        self.assertEqual(code, 2)
        self.assertIn("audit", err)

    def test_example_config_is_valid_and_uses_reserved_domains_only(self):
        with open(os.path.join(support.ROOT, "sites.example.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertTrue(cfg["sites"])
        for s in cfg["sites"]:
            host = audit._bare_host(s["url"])
            self.assertIn(host, ("example.com", "example.org"), s["url"])


if __name__ == "__main__":
    unittest.main()
