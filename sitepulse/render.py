"""sitepulse.render - turn a run's data into a self-contained HTML report.

The report is a single file with inline CSS and inline SVG charts: no
JavaScript, no external requests. It prints well (browser print -> PDF).

If ``reports/ANALYSIS-<date>.md`` exists (the judgement written by a human or
an agent following CHECKLIST.md) it is included at the top of the report,
before the numbers.

Every value that comes from the data (and therefore, indirectly, from the
audited websites) is HTML-escaped before it is written.
"""

from __future__ import annotations

import glob
import html
import json
import logging
import os
import pathlib
import re
import webbrowser

log = logging.getLogger("sitepulse.render")

STATUS_COLOR = {"ok": "#1f8a4c", "warning": "#b8860b", "problem": "#c0392b",
                "info": "#5c6b7a"}
STATUS_LABEL = {"ok": "OK", "warning": "WARNING", "problem": "PROBLEM", "info": "info"}
AREA_COLOR = {"technical": "#2b6cb0", "seo": "#6b46c1", "content": "#0d7377",
              "conversion": "#c05621", "security": "#9b1c31"}
NEUTRAL = "#5c6b7a"


def esc(value):
    return html.escape(str(value), quote=True)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_color(points):
    points = _num(points)
    return "#1f8a4c" if points >= 85 else ("#b8860b" if points >= 65 else "#c0392b")


# ------------------------------------------------------------ mini markdown

_SAFE_HREF = re.compile(r"^(?:https?://|mailto:)", re.I)
_INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)")
_TABLE_RULE = re.compile(r"^\|?[\s:|-]*-[\s:|-]*$")


def inline(text):
    """Inline markdown -> HTML: code, bold, links. Everything else is escaped.

    Only http, https and mailto links become anchors; any other target
    (javascript:, data:, relative paths...) is shown as plain text.
    """
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(esc(text[pos:m.start()]))
        code, bold, label, href = m.groups()
        if code is not None:
            out.append("<code>%s</code>" % esc(code))
        elif bold is not None:
            out.append("<strong>%s</strong>" % inline(bold))
        elif _SAFE_HREF.match(href):
            out.append('<a href="%s" rel="noopener noreferrer">%s</a>'
                       % (esc(href), esc(label)))
        else:
            out.append(esc("%s (%s)" % (label, href)))
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def md_to_html(text):
    """A deliberately small markdown subset: headings, lists, tables,
    paragraphs. Raw HTML in the source is escaped, never passed through."""
    out, list_tag, in_table = [], None, False

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append("</%s>" % list_tag)
            list_tag = None

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            close_list()
            close_table()
            continue
        if in_table and _TABLE_RULE.match(line):
            continue
        if line.startswith("|"):
            close_list()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                out.append('<table class="an"><thead><tr>'
                           + "".join("<th>%s</th>" % inline(c) for c in cells)
                           + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in cells)
                           + "</tr>")
            continue
        close_table()
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            close_list()
            level = min(len(m.group(1)) + 1, 5)
            out.append("<h%d>%s</h%d>" % (level, inline(m.group(2)), level))
            continue
        m = re.match(r"^\s*([-*]|\d+\.)\s+(.*)", line)
        if m:
            tag = "ul" if m.group(1) in "-*" else "ol"
            if list_tag != tag:
                close_list()
                out.append("<%s>" % tag)
                list_tag = tag
            out.append("<li>%s</li>" % inline(m.group(2)))
            continue
        close_list()
        out.append("<p>%s</p>" % inline(line))
    close_list()
    close_table()
    return "\n".join(out)


# ------------------------------------------------------------ charts (SVG)

def bar_chart(items, width=560, row_height=30):
    """items: [(label, value 0-100, colour)] -> horizontal bar chart."""
    if not items:
        return ""
    label_w, height = 190, row_height * len(items) + 26
    plot = width - label_w - 46
    p = ['<svg viewBox="0 0 %d %d" class="chart" role="img" aria-label="bar chart">'
         % (width, height)]
    for x in (0, 25, 50, 75, 100):
        gx = label_w + plot * x / 100
        p.append('<line x1="%.0f" y1="14" x2="%.0f" y2="%d" stroke="#e3e6ea" '
                 'stroke-width="1"/>' % (gx, gx, height - 14))
        p.append('<text x="%.0f" y="%d" class="tick">%d</text>' % (gx, height - 3, x))
    for i, (label, value, colour) in enumerate(items):
        y = 16 + i * row_height
        v = max(0.0, min(100.0, _num(value)))
        w = max(2.0, plot * v / 100)
        # truncate first, escape after: cutting an escaped string can split
        # an entity such as &amp; in half
        p.append('<text x="%d" y="%d" class="lab">%s</text>'
                 % (label_w - 8, y + 13, esc(str(label)[:30])))
        p.append('<rect x="%d" y="%d" width="%.1f" height="18" rx="3" fill="%s"/>'
                 % (label_w, y, w, esc(colour)))
        p.append('<text x="%.1f" y="%d" class="val">%s</text>'
                 % (label_w + w + 6, y + 13, esc(value)))
    p.append("</svg>")
    return "".join(p)


def trend_chart(series, width=560, height=130):
    """series: [(date, score)] -> line chart of the score over time."""
    if len(series) < 2:
        return ""
    n = len(series)
    ml, mr, mt, mb = 34, 12, 12, 26
    pw, ph = width - ml - mr, height - mt - mb
    points = []
    for i, (_, value) in enumerate(series):
        x = ml + pw * i / (n - 1)
        y = mt + ph * (1 - max(0.0, min(100.0, _num(value))) / 100)
        points.append((x, y))
    p = ['<svg viewBox="0 0 %d %d" class="chart" role="img" aria-label="trend chart">'
         % (width, height)]
    for v in (0, 50, 100):
        y = mt + ph * (1 - v / 100)
        p.append('<line x1="%d" y1="%.0f" x2="%d" y2="%.0f" stroke="#e3e6ea"/>'
                 '<text x="%d" y="%.0f" class="tick" text-anchor="end">%d</text>'
                 % (ml, y, width - mr, y, ml - 6, y + 4, v))
    p.append('<polyline fill="none" stroke="#2b6cb0" stroke-width="2.5" points="%s"/>'
             % " ".join("%.1f,%.1f" % xy for xy in points))
    for x, y in points:
        p.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="#2b6cb0"/>' % (x, y))
    for i, (date, _) in enumerate(series):
        if n <= 8 or i in (0, n - 1) or i % max(1, n // 6) == 0:
            p.append('<text x="%.0f" y="%d" class="tick" text-anchor="middle">%s</text>'
                     % (points[i][0], height - 6, esc(str(date)[5:])))
    p.append("</svg>")
    return "".join(p)


CSS = """
:root{--ink:#1c2330;--soft:#5c6b7a;--line:#e3e6ea;--bg:#ffffff;--paper:#f7f9fb}
*{box-sizing:border-box}
body{margin:0;font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
 color:var(--ink);background:var(--bg)}
.wrap{max-width:900px;margin:0 auto;padding:34px 22px 60px}
h1{font-size:30px;line-height:1.15;margin:0 0 6px}
h2{font-size:22px;margin:38px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h3{font-size:18px;margin:22px 0 8px}
h4,h5{font-size:16px;margin:16px 0 6px}
p{margin:8px 0}
.sub{color:var(--soft);margin:0 0 22px}
.kpi{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0 26px}
.kpi div{flex:1 1 150px;background:var(--paper);border:1px solid var(--line);
 border-radius:10px;padding:14px 16px}
.kpi .n{font-size:30px;font-weight:700;line-height:1.1}
.kpi .n small{font-size:15px;color:var(--soft);font-weight:400}
.kpi .t{font-size:13px;color:var(--soft);text-transform:uppercase;letter-spacing:.04em}
.card{border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:16px 0;
 background:var(--bg);break-inside:avoid}
.card.reg{border-color:#c0392b;background:#fdf3f2}
.row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.name{font-size:18px}
.score{font-size:26px;font-weight:700}
.delta{font-size:14px;font-weight:700}
.up{color:#1f8a4c}.down{color:#c0392b}.flat{color:var(--soft)}
.fine{color:#1f8a4c}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top;
 overflow-wrap:anywhere}
th{background:var(--paper);font-size:12.5px;text-transform:uppercase;letter-spacing:.03em;color:var(--soft)}
td.e{font-weight:700;white-space:nowrap}
code{background:var(--paper);padding:1px 5px;border-radius:4px;font-size:13px}
.chart{width:100%;height:auto;margin:8px 0 4px}
.chart .lab{font-size:11.5px;fill:#1c2330;text-anchor:end}
.chart .val{font-size:11.5px;fill:#5c6b7a;font-weight:700}
.chart .tick{font-size:10px;fill:#8a97a6;text-anchor:middle}
.analysis{background:var(--paper);border:1px solid var(--line);border-left:5px solid #2b6cb0;
 border-radius:10px;padding:6px 22px 18px;margin:20px 0 30px}
.analysis h2{border:none;margin-top:20px}
.mini{font-size:13px;color:var(--soft)}
a{color:#2b6cb0}
@media print{.wrap{max-width:none;padding:0 6mm}.card,.analysis{break-inside:avoid}
 h2{break-after:avoid}body{font-size:12pt}}
@media (prefers-color-scheme:dark){
 :root{--ink:#e8ecf1;--soft:#9aa7b4;--line:#2b333d;--bg:#12161b;--paper:#1a2028}
 .card.reg{background:#2a1719}
 .chart .lab{fill:#e8ecf1}.chart line{stroke:#2b333d}
 th{color:#9aa7b4}
}
"""


def _kpi(value, label, colour, suffix=""):
    return ('<div><div class="n" style="color:%s">%s%s</div><div class="t">%s</div></div>'
            % (esc(colour), esc(value), suffix, esc(label)))


def build_report(data, analysis_md="", history=None):
    """Return the full HTML document for one run."""
    history = history or {"rows": []}
    date = data.get("date", "")
    sites = sorted(data.get("sites", []), key=lambda s: _num(s.get("score")))
    problems = sum(1 for s in sites for x in s["checks"] if x["status"] == "problem")
    warnings = sum(1 for s in sites for x in s["checks"] if x["status"] == "warning")
    regressions = [s for s in sites
                   if _num(s["comparison"].get("score_delta")) < 0]
    good, bad, warn = STATUS_COLOR["ok"], STATUS_COLOR["problem"], STATUS_COLOR["warning"]

    p = ['<div class="wrap">',
         "<h1>Weekly site check</h1>",
         '<p class="sub">Run of %s · %d site%s checked · measured by '
         "<code>sitepulse</code></p>" % (esc(date), len(sites), "" if len(sites) == 1 else "s"),
         '<div class="kpi">',
         _kpi(data.get("average", 0), "Average score",
              score_color(data.get("average", 0)), "<small>/100</small>"),
         _kpi(problems, "Problems", bad if problems else good),
         _kpi(warnings, "Warnings", warn if warnings else good),
         _kpi(len(regressions), "Regressions", bad if regressions else good),
         "</div>"]

    if analysis_md.strip():
        p.append('<div class="analysis">' + md_to_html(analysis_md) + "</div>")

    p.append("<h2>Score by site</h2>")
    p.append(bar_chart([(s["name"], s["score"], score_color(s["score"])) for s in sites]))

    by_area = {}
    for s in sites:
        for area, value in s.get("areas", {}).items():
            by_area.setdefault(area, []).append(_num(value))
    p.append("<h2>Score by area (average across sites)</h2>")
    p.append(bar_chart([(a, round(sum(v) / len(v)), AREA_COLOR.get(a, NEUTRAL))
                        for a, v in sorted(by_area.items())]))

    by_date = {}
    for row in history.get("rows", []):
        by_date.setdefault(str(row.get("date", "")), []).append(_num(row.get("score")))
    series = [(d, round(sum(v) / len(v))) for d, v in sorted(by_date.items())][-12:]
    if len(series) >= 2:
        p.append("<h2>Average score over time</h2>")
        p.append(trend_chart(series))

    p.append("<h2>Detail by site</h2>")
    for s in sites:
        delta = s["comparison"].get("score_delta")
        regressed = delta is not None and _num(delta) < 0
        if delta is None:
            ds = '<span class="delta flat">first run</span>'
        elif _num(delta) > 0:
            ds = '<span class="delta up">+%s points</span>' % esc(delta)
        elif regressed:
            ds = '<span class="delta down">%s points — regression</span>' % esc(delta)
        else:
            ds = '<span class="delta flat">unchanged</span>'
        url = s.get("url") or ""
        if re.match(r"^https?://", url, re.I):
            link = '<a href="%s" rel="noopener noreferrer">%s</a> · ' % (esc(url), esc(url))
        else:
            link = (esc(url) + " · ") if url else ""
        p.append('<div class="%s"><div class="row"><span class="score" style="color:%s">'
                 '%s</span><strong class="name">%s</strong>%s</div>'
                 '<p class="mini">%sgoal: %s</p>'
                 % ("card reg" if regressed else "card", score_color(s["score"]),
                    esc(s["score"]), esc(s["name"]), ds, link, esc(s.get("goal") or "-")))
        areas = ", ".join('<strong style="color:%s">%s %s</strong>'
                          % (AREA_COLOR.get(a, NEUTRAL), esc(a), esc(v))
                          for a, v in s.get("areas", {}).items())
        p.append('<p class="mini">%s</p>' % areas)

        changed = s["comparison"].get("changed") or []
        if changed:
            rows = "".join(
                '<li><code>%s</code>: %s → <strong style="color:%s">%s</strong> (%s)</li>'
                % (esc(ch["id"]), esc(STATUS_LABEL.get(ch["from"], ch["from"])),
                   STATUS_COLOR.get(ch["to"], NEUTRAL),
                   esc(STATUS_LABEL.get(ch["to"], ch["to"])), esc(ch["value"]))
                for ch in changed[:12])
            more = ("<p class=\"mini\">…and %d more.</p>" % (len(changed) - 12)
                    if len(changed) > 12 else "")
            p.append("<p><strong>Changed since the previous run:</strong></p>"
                     "<ul>%s</ul>%s" % (rows, more))

        flagged = [x for x in s["checks"] if x["status"] in ("problem", "warning")]
        if flagged:
            p.append("<table><thead><tr><th>Status</th><th>Area</th><th>Check</th>"
                     "<th>Value</th><th>Why it matters</th></tr></thead><tbody>")
            for x in sorted(flagged, key=lambda x: 0 if x["status"] == "problem" else 1):
                p.append('<tr><td class="e" style="color:%s">%s</td><td>%s</td>'
                         "<td><code>%s</code></td><td>%s</td><td>%s</td></tr>"
                         % (STATUS_COLOR[x["status"]], STATUS_LABEL[x["status"]],
                            esc(x["area"]), esc(x["id"]), esc(x["value"]), esc(x["note"])))
            p.append("</tbody></table>")
        else:
            p.append('<p class="fine"><strong>Nothing flagged: all in order.</strong></p>')
        p.append("</div>")

    p.append('<h2>How to read this</h2><p class="mini">The score weighs the automatic '
             "checks: OK = 1, WARNING = 0.5, PROBLEM = 0. The movement since the "
             "previous run matters more than the absolute value, and a negative "
             "movement (a regression) always comes first. The judgement criteria "
             "live in <code>CHECKLIST.md</code>.</p>")
    p.append('<p class="mini">Generated by sitepulse.</p>')
    p.append("</div>")

    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Weekly site check — %s</title>"
            "<style>%s</style></head><body>" % (esc(date), CSS)
            + "".join(p) + "</body></html>")


def latest_data_file(reports_dir):
    found = sorted(glob.glob(os.path.join(reports_dir, "data-*.json")))
    return found[-1] if found else None


def render(data_dir, date=None, open_browser=False):
    """Write reports/REPORT-<date>.html and return its path.

    Raises FileNotFoundError when there is no data for the requested run.
    """
    reports = os.path.join(data_dir, "reports")
    if date:
        data_path = os.path.join(reports, "data-%s.json" % date)
    else:
        data_path = latest_data_file(reports)
    if not data_path or not os.path.exists(data_path):
        raise FileNotFoundError(
            "no data found in %s%s: run `sitepulse audit` first"
            % (reports, " for %s" % date if date else ""))
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    date = date or os.path.basename(data_path)[len("data-"):-len(".json")]

    analysis = ""
    analysis_path = os.path.join(reports, "ANALYSIS-%s.md" % date)
    if os.path.exists(analysis_path):
        with open(analysis_path, encoding="utf-8") as f:
            analysis = f.read()
        log.info("including %s", analysis_path)

    history = {"rows": []}
    history_path = os.path.join(data_dir, "history.json")
    if os.path.exists(history_path):
        try:
            with open(history_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("rows"), list):
                history = loaded
            else:
                log.warning("%s has an unexpected shape: trend chart skipped", history_path)
        except (OSError, ValueError) as e:
            log.warning("cannot read %s (%s): trend chart skipped", history_path, e)

    out_path = os.path.join(reports, "REPORT-%s.html" % date)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_report(data, analysis, history))
    if open_browser:
        webbrowser.open(pathlib.Path(out_path).resolve().as_uri())
    return out_path
