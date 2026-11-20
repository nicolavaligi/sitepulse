import json
import os
import tempfile
import unittest

import support  # noqa: F401
from sitepulse import audit


def result(name="Site", **checks):
    """Build a result from id=(area, status) pairs."""
    res = {"name": name, "url": "https://example.com", "purpose": "", "goal": "",
           "checks": [], "measurements": {"words": 10, "contact": {"tel": 1}}}
    for id_, (area, status) in checks.items():
        audit.add_check(res["checks"], area, id_.replace("_", "-"), status, "v", "n")
    res["score"] = audit.score(res)
    res["areas"] = audit.area_scores(res)
    return res


class ScoringTest(unittest.TestCase):

    def test_score_weights(self):
        res = result(a=("seo", audit.OK), b=("seo", audit.WARN),
                     c=("technical", audit.PROBLEM), d=("technical", audit.OK))
        self.assertEqual(res["score"], 62)          # (1 + .5 + 0 + 1) / 4 = 62.5 -> 62

    def test_all_ok_and_all_problems(self):
        self.assertEqual(result(a=("seo", audit.OK))["score"], 100)
        self.assertEqual(result(a=("seo", audit.PROBLEM))["score"], 0)

    def test_info_checks_do_not_count(self):
        res = result(a=("seo", audit.OK), b=("seo", audit.INFO))
        self.assertEqual(res["score"], 100)
        self.assertEqual(res["areas"], {"seo": 100})

    def test_no_checks(self):
        self.assertEqual(audit.score({"checks": []}), 0)
        self.assertEqual(audit.area_scores({"checks": []}), {})

    def test_area_scores(self):
        res = result(a=("seo", audit.OK), b=("seo", audit.WARN),
                     c=("technical", audit.PROBLEM), d=("content", audit.OK),
                     e=("content", audit.OK), f=("security", audit.WARN))
        self.assertEqual(res["areas"],
                         {"content": 100, "security": 50, "seo": 75, "technical": 0})
        self.assertEqual(list(res["areas"]), sorted(res["areas"]))


class HistoryTest(unittest.TestCase):

    def history_with(self, date, res):
        return audit.record_history({"rows": []}, date, [res])

    def test_first_run(self):
        res = result(a=("seo", audit.OK))
        self.assertEqual(audit.compare(res, {"rows": []}, "2026-01-12"),
                         {"first_run": True, "score_delta": None, "changed": []})

    def test_other_sites_do_not_count_as_previous_runs(self):
        history = self.history_with("2026-01-05", result("Other", a=("seo", audit.OK)))
        self.assertTrue(audit.compare(result(a=("seo", audit.OK)), history,
                                      "2026-01-12")["first_run"])

    def test_regression(self):
        before = result(a=("seo", audit.OK), b=("technical", audit.OK))
        after = result(a=("seo", audit.OK), b=("technical", audit.PROBLEM))
        cmp_ = audit.compare(after, self.history_with("2026-01-05", before), "2026-01-12")
        self.assertFalse(cmp_["first_run"])
        self.assertEqual(cmp_["previous_date"], "2026-01-05")
        self.assertEqual(cmp_["previous_score"], 100)
        self.assertEqual(cmp_["score_delta"], -50)
        self.assertEqual(cmp_["changed"],
                         [{"id": "b", "from": "ok", "to": "problem", "value": "v"}])

    def test_improvement(self):
        before = result(a=("seo", audit.WARN))
        cmp_ = audit.compare(result(a=("seo", audit.OK)),
                             self.history_with("2026-01-05", before), "2026-01-12")
        self.assertEqual(cmp_["score_delta"], 50)

    def test_unchanged(self):
        res = result(a=("seo", audit.OK), b=("technical", audit.WARN))
        cmp_ = audit.compare(res, self.history_with("2026-01-05", res), "2026-01-12")
        self.assertEqual(cmp_["score_delta"], 0)
        self.assertEqual(cmp_["changed"], [])

    def test_new_and_disappeared_checks_are_reported(self):
        before = result(a=("seo", audit.OK), old=("content", audit.PROBLEM))
        after = result(a=("seo", audit.OK), new=("content", audit.WARN))
        cmp_ = audit.compare(after, self.history_with("2026-01-05", before), "2026-01-12")
        self.assertIn({"id": "new", "from": "-", "to": "warning", "value": "v"},
                      cmp_["changed"])
        self.assertIn({"id": "old", "from": "problem", "to": "-", "value": "not run"},
                      cmp_["changed"])

    def test_latest_earlier_run_is_the_baseline(self):
        history = {"rows": []}
        audit.record_history(history, "2026-01-05", [result(a=("seo", audit.PROBLEM))])
        audit.record_history(history, "2025-12-29", [result(a=("seo", audit.WARN))])
        cmp_ = audit.compare(result(a=("seo", audit.OK)), history, "2026-01-12")
        self.assertEqual(cmp_["previous_date"], "2026-01-05")
        self.assertEqual(cmp_["score_delta"], 100)

    def test_rerun_on_the_same_day_keeps_the_weekly_delta(self):
        history = self.history_with("2026-01-05", result(a=("seo", audit.OK)))
        broken = result(a=("seo", audit.PROBLEM))
        first = audit.compare(broken, history, "2026-01-12")
        audit.record_history(history, "2026-01-12", [broken])
        second = audit.compare(broken, history, "2026-01-12")
        audit.record_history(history, "2026-01-12", [broken])
        self.assertEqual(first["score_delta"], -100)
        self.assertEqual(second, first)             # not "unchanged vs itself"
        self.assertEqual([r["date"] for r in history["rows"]],
                         ["2026-01-05", "2026-01-12"])      # no duplicate row

    def test_record_history_row_shape(self):
        history = self.history_with("2026-01-05", result(a=("seo", audit.OK)))
        self.assertEqual(history["rows"], [{
            "date": "2026-01-05", "name": "Site", "score": 100,
            "areas": {"seo": 100}, "measurements": {"words": 10},
            "checks": [{"id": "a", "status": "ok"}]}])

    def test_history_is_capped(self):
        history = {"rows": [{"date": "2020-01-01", "name": "S%d" % i, "score": 1,
                             "checks": []} for i in range(audit.HISTORY_MAX_ROWS + 5)]}
        audit.record_history(history, "2026-01-05", [result(a=("seo", audit.OK))])
        self.assertEqual(len(history["rows"]), audit.HISTORY_MAX_ROWS)
        self.assertEqual(history["rows"][-1]["date"], "2026-01-05")

    def test_load_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.json")
            self.assertEqual(audit.load_history(path), {"rows": []})
            audit.write_json(path, {"rows": [{"date": "2026-01-05"}]})
            self.assertEqual(len(audit.load_history(path)["rows"]), 1)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_damaged_history_is_an_error_not_an_empty_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.json")
            for content in ("{truncated", json.dumps(["not", "a", "history"])):
                with open(path, "w") as f:
                    f.write(content)
                with self.assertRaises(audit.HistoryError):
                    audit.load_history(path)


class MarkdownTest(unittest.TestCase):

    def data(self):
        first = result("Shop | Example", a=("seo", audit.PROBLEM), b=("seo", audit.OK))
        first["checks"][0]["value"] = "Home | Shop"
        first["comparison"] = audit.compare(first, {"rows": []}, "2026-01-12")
        before = result("Blog", a=("seo", audit.OK))
        second = result("Blog", a=("seo", audit.WARN))
        second["comparison"] = audit.compare(
            second, audit.record_history({"rows": []}, "2026-01-05", [before]),
            "2026-01-12")
        return {"date": "2026-01-12", "average": 50, "sites": [first, second]}

    def test_summary(self):
        md = audit.to_markdown(self.data())
        self.assertIn("# Weekly site data - 2026-01-12", md)
        self.assertIn("Sites checked: **2**", md)
        self.assertIn("Average score: **50/100**", md)
        self.assertIn("| Site | Score | Δ | Problems | Warnings |", md)
        self.assertIn("| Shop \\| Example | 50 | first run | 1 | 0 |", md)
        self.assertIn("| Blog | 50 | -50 | 0 | 1 |", md)

    def test_site_sections(self):
        md = audit.to_markdown(self.data())
        self.assertIn("## Shop | Example", md)
        self.assertIn("**First run: nothing to compare with yet.**", md)
        self.assertIn("Compared with 2026-01-05: **-50** (was 100/100) - **REGRESSION**", md)
        self.assertIn("- `a`: ok -> **warning** (v)", md)
        self.assertIn("- Measurements: words=10", md)
        self.assertNotIn("contact=", md)

    def test_pipes_in_values_do_not_break_the_table(self):
        md = audit.to_markdown(self.data())
        self.assertIn("| seo | a | PROBLEM | Home \\| Shop | n |", md)
        for line in md.splitlines():
            if line.startswith("| seo |"):
                self.assertEqual(line.replace("\\|", "").count("|"), 6, line)

    def test_problems_are_listed_first(self):
        md = audit.to_markdown(self.data())
        self.assertLess(md.index("| seo | a | PROBLEM"), md.index("| seo | b | OK"))

    def test_unchanged_run(self):
        res = result(a=("seo", audit.OK))
        res["comparison"] = audit.compare(
            res, audit.record_history({"rows": []}, "2026-01-05", [res]), "2026-01-12")
        md = audit.to_markdown({"date": "2026-01-12", "average": 100, "sites": [res]})
        self.assertIn("**0** (was 100/100)", md)
        self.assertIn("No check changed status since the previous run.", md)
        self.assertNotIn("REGRESSION", md)


if __name__ == "__main__":
    unittest.main()
