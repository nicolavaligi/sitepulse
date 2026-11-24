# sitepulse

Measure your websites once a week, compare with last week, and act on **at most three
things per site**.

sitepulse answers one question: *what is worth doing on this site right now?* It is
split in two on purpose:

- **The script only measures.** About 30 checks per site, a 0–100 score, and a diff
  against the previous run. No opinions, no advice.
- **The judgement lives in a separate file**, [`CHECKLIST.md`](CHECKLIST.md): how to
  turn the measurements into decisions. You apply it yourself, or you hand it to an LLM
  agent with the prompt in [`prompts/weekly-review.md`](prompts/weekly-review.md).

Two rules hold the routine together:

1. **Three actions per site per week, maximum.** A list of 20 things never gets done.
   Three do.
2. **The week-over-week delta matters more than the absolute score.** A negative delta
   is a regression — something that worked has broken — and it is always the top
   priority.

Python 3.9+, standard library only. Nothing to install, no account, no API key.

## Quick start

Zero install — clone and run:

```bash
git clone https://github.com/nicolavaligi/sitepulse
cd sitepulse
python3 -m sitepulse audit --url https://example.com
```

That writes `sitepulse-data/reports/DATA-<date>.md` (the measurements) and starts
`sitepulse-data/history.json`. To get the HTML report as well:

```bash
python3 -m sitepulse run --url https://example.com --open
```

Or install the `sitepulse` command:

```bash
pip install .
sitepulse run --open
```

### Commands

```text
sitepulse audit    measure the sites, write the data files, update the history
sitepulse render   build the HTML report from a run's data
sitepulse run      audit, then render
```

| Option | Commands | Meaning |
|---|---|---|
| `--config FILE` | audit, run | list of sites (default `./sites.json`) |
| `--only TEXT` | audit, run | only sites whose name or URL contains TEXT |
| `--url URL` | audit, run | audit one URL, without a config file |
| `--date YYYY-MM-DD` | all | run label (audit: today; render: latest run) |
| `--dir DIR` | all | data directory (default `./sitepulse-data`) |
| `--open` | render, run | open the report in the default browser |
| `--user-agent UA` | audit, run | override the User-Agent header |
| `--accept-language LANG` | audit, run | override the Accept-Language header |
| `-v`, `--verbose` | all | debug logging on stderr |

One site takes roughly 10 seconds.

## Reuse SEO settings

Capture a representative set of public pages and use one as the template for a new
page's metadata. The commands use Python's standard library and require no API key.

```bash
python3 -m sitepulse seo-capture \
  --url https://tutelaespropri.it/ \
  --url https://tutelaespropri.it/calcolo-indennita-di-esproprio/ \
  --output tutela-seo.json

python3 -m sitepulse seo-head tutela-seo.json \
  --source https://tutelaespropri.it/calcolo-indennita-di-esproprio/ \
  --canonical https://example.com/guide/indennita/ \
  --title "Guida all’indennità di esproprio" \
  --description "Documenti e passaggi per esaminare una proposta di indennità." \
  --brand "Nome del progetto" > seo-head.html
```

The snapshot records HTTP results, titles, descriptions, canonicals, robots
directives, hreflang, social tags, JSON-LD types, headings, internal links and
missing image alt attributes. It reads only the requested pages and does not
execute JavaScript. A partial capture keeps errors alongside successful pages
and exits with status 1. Choose a fresh output filename for each capture.

The generated fragment uses your own title, description, canonical URL, optional
brand and optional `--image` URL. It picks up the source's title separator when
a brand is provided and its Open Graph and Twitter tag families. Replace the
equivalent tags in the target page's head or use the values in the framework's
metadata API. Review indexing, hreflang and structured data for the target
project independently.

See the [Tutela Espropri analysis](docs/tutela-espropri.md) for a worked example.

## Configuration

Copy `sites.example.json` to `sites.json` and list your sites:

```json
{
  "accept_language": "en",
  "cta_languages": ["en", "it"],
  "sites": [
    {
      "name": "Example shop",
      "url": "https://example.com",
      "purpose": "leads",
      "goal": "a phone call from a private customer",
      "active": true
    }
  ]
}
```

Per site:

| Field | Meaning |
|---|---|
| `name` | how the site appears in reports. It is also the key in the history: renaming a site starts a new history. |
| `url` | the page to audit, normally the home page. |
| `goal` | what you want a visitor to do. Not decoration: it is the yardstick the checklist uses to judge the page. |
| `purpose` | free text, for your own reference. |
| `active` | `false` skips the site without deleting it. Default `true`. |
| `path` | optional local directory of the site's source. If set, adds the `last-modified` check (days since any file changed). The path itself is never written to the reports. |
| `cta_words` | your own call-to-action vocabulary for this site, replacing the built-in lists. |
| `cta_languages` | which built-in lists to use: `"en"`, `"it"`. Default: both. |
| `accept_language` | `Accept-Language` header for this site. Default `"en"`. |

`accept_language`, `cta_languages`, `cta_words` and `user_agent` can also be set at the
top level as defaults for every site. Command-line flags win over the config.

Call-to-action words are matched as whole words or phrases, case-insensitively, against
the visible text of the page. The built-in lists are in
[`sitepulse/audit.py`](sitepulse/audit.py) (`CTA_WORDS`); pull requests for other
languages are welcome.

## What it checks

Every check yields `OK` (1 point), `WARNING` (half a point) or `PROBLEM` (zero). The
site score is the average, out of 100; each area gets its own score the same way.

| Area | Check id | What is measured |
|---|---|---|
| technical | `online` | the page answers without an HTTP error |
| technical | `response-time` | time to fetch the page (<1 s OK, <2.5 s warning) |
| technical | `page-weight` | bytes of the HTML plus the scripts, stylesheets and images weighed (<1.2 MB OK, <2.5 MB warning) |
| technical | `broken-resources` | scripts, stylesheets and images that fail to load |
| technical | `broken-links` | internal and external links that fail |
| technical | `external-dependencies` | assets loaded from other hosts (more than 3 is a warning) |
| technical | `page-404` | a nonexistent URL answers 404, not 200 |
| technical | `image-dimensions` | images without `width`/`height` (only if the page has images) |
| technical | `mobile-viewport` | `<meta name="viewport">` is present |
| seo | `title` | present, 25–65 characters |
| seo | `meta-description` | present, 70–165 characters |
| seo | `lang` | `<html lang>` is set |
| seo | `canonical` | `<link rel="canonical">` is present |
| seo | `h1` | exactly one H1 |
| seo | `heading-structure` | at least two H2 |
| seo | `structured-data` | JSON-LD is present and parses |
| seo | `social-sharing` | `og:title`, `og:description`, `og:image` |
| seo | `robots` | `/robots.txt` exists and is not an HTML page |
| seo | `sitemap` | the sitemap declared in robots.txt, or `/sitemap.xml`, lists URLs |
| content | `text-amount` | visible words (300+ OK, 120+ warning) |
| content | `image-alt` | images without `alt` text |
| content | `stale-years` | years older than last year in the visible text |
| content | `favicon` | an icon is declared |
| content | `last-modified` | days since the local project changed (only with `path`) |
| conversion | `contact-channels` | phone link, mail link, WhatsApp link, form: at least two |
| conversion | `call-to-action` | action words in the visible text |
| conversion | `analytics` | a visit-measurement script is detected |
| security | `https` | visitors land on an `https://` URL |
| security | `security-headers` | HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy |

Two more ids appear only when something is off before the checks can run: `missing-url`
(a site in the config has no URL) and `html-parse` (the HTML could not be parsed
completely). A site that is unreachable gets the single check `online` = `PROBLEM` and
a score of 0.

The thresholds are deliberately simple. They are not a verdict: that is the
checklist's job.

## Files produced

Everything goes under the data directory (`--dir`, default `./sitepulse-data`):

```text
sitepulse-data/
  history.json                    one row per site per run: the source of every delta
  reports/
    data-YYYY-MM-DD.json          full data for the run
    DATA-YYYY-MM-DD.md            the same data, readable (this is what you or the agent read)
    ANALYSIS-YYYY-MM-DD.md        the judgement: at most 3 actions per site (written by you or an agent)
    REPORT-YYYY-MM-DD.html        self-contained report with charts (print it to get a PDF)
```

`history.json` is the piece that matters over time: without history there is no delta,
and the routine turns into a pointless snapshot. Back it up; do not delete it. If it is
ever damaged, sitepulse stops with an error instead of starting a new one over it.
Running twice on the same day replaces that day's rows and still compares with the
previous run.

If `ANALYSIS-<date>.md` exists when you render, it is placed at the top of the HTML
report, before the numbers. The report is one HTML file with inline CSS and SVG: no
JavaScript, no external requests.

## Scheduling it weekly

The routine has three steps: **measure → judge → render**.

```bash
sitepulse audit                      # 1. measure
# 2. judge: write sitepulse-data/reports/ANALYSIS-<date>.md following CHECKLIST.md
sitepulse render --open              # 3. render
```

With cron, every Monday at 08:00 (measure and render; you read the report and decide):

```cron
0 8 * * 1  cd /path/to/sitepulse && python3 -m sitepulse run >> sitepulse-data/cron.log 2>&1
```

To automate the judgement too, give an LLM agent that can read and write files in this
directory the prompt in [`prompts/weekly-review.md`](prompts/weekly-review.md). It
reads `DATA-<date>.md` and `CHECKLIST.md` and writes `ANALYSIS-<date>.md` with at most
3 actions per site; `sitepulse render` then includes it. The prompt does not depend on
any particular agent or vendor.

## Privacy & data

- sitepulse only requests **public pages of the sites you list**, plus the assets and
  links found on their home page. It sends no cookies, submits no forms and runs no
  JavaScript.
- **Nothing is sent anywhere else.** No telemetry, no third-party API, no account.
- Reports and history **stay on your machine**, in the data directory. `sitepulse-data/`
  and `sites.json` are in `.gitignore` so they are not committed by accident.
- sitepulse is not designed to collect personal data: contact links are *counted*, not
  stored. But the reports quote text and URLs from the audited pages (title,
  description, first heading, broken links), so **they may contain contact details or
  names that appear on those pages**. Treat the reports accordingly before sharing them.
- If you hand the data to an LLM agent for the weekly review, that text goes to whichever
  service runs the agent. That is your choice and your responsibility, not something
  sitepulse does.
- It identifies itself honestly:
  `User-Agent: sitepulse/0.1 (+https://github.com/nicolavaligi/sitepulse)`.
  You can change it with `--user-agent`; do not use it to impersonate a browser.

### Politeness limits

sitepulse is meant for sites you own or look after, once a week. Per site and per run
it weighs at most **25 assets** (of which at most 12 images) and probes at most
**28 links** (20 internal, 8 external), mostly with `HEAD` requests, one at a time, and
never downloads more than 10 MB from a single URL. The limits are constants at the top
of [`sitepulse/audit.py`](sitepulse/audit.py). It does not consult `robots.txt` for
permission (it reads it only to check that it exists), so do not point it at sites whose
owners have not agreed.

## Stated limits

- It does not measure real browser performance (no Lighthouse, no Core Web Vitals): the
  response time and the weight of the resources are used as a hint.
- It audits **one page per site** (the URL you list, normally the home page), not the
  whole site. Internal links are probed, not analysed.
- It does not execute JavaScript: content rendered client-side is invisible to it.
- It knows nothing about search rankings or traffic. If the site has no analytics, it
  says so, but it cannot make the data up.
- It tries at most 25 resources and 28 links per site, so as not to hammer servers.
  On a large page the weight and the broken-link count are a lower bound.
- The call-to-action and analytics checks are keyword heuristics. They can miss things
  and they can be fooled.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests need nothing beyond the standard library and never touch the internet: they
start small fixture websites on `127.0.0.1` and audit those. CI runs the suite on
Python 3.9 and 3.13.

```text
sitepulse/audit.py    requests, HTML parsing, checks, scoring, history, markdown
sitepulse/render.py   HTML report, SVG charts, mini markdown renderer
sitepulse/cli.py      command line
CHECKLIST.md          the judgement criteria
prompts/              the agent prompt for the weekly review
tests/                unittest suite with local fixture sites
```

Design constraints worth keeping when you contribute: no dependencies; the script
measures and never advises; every value that reaches the HTML report is escaped.

## License

MIT — see [LICENSE](LICENSE).
