"""sitepulse command line.

    sitepulse audit   measure the sites, write data + history
    sitepulse render  build the HTML report from a run's data
    sitepulse run     audit, then render
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime

from . import __version__, audit, render, seo

log = logging.getLogger("sitepulse")

DEFAULT_DIR = "sitepulse-data"
DEFAULT_CONFIG = "sites.json"


class UsageError(Exception):
    """A problem the user can fix: reported on stderr, exit status 2."""


def _date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError("expected a date as YYYY-MM-DD, got %r" % value)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=DEFAULT_DIR, metavar="DIR",
                        help="data directory holding reports/ and history.json "
                             "(default: ./%s)" % DEFAULT_DIR)
    common.add_argument("--date", type=_date, metavar="YYYY-MM-DD",
                        help="run label (audit: default today; render: default latest run)")
    common.add_argument("-v", "--verbose", action="store_true",
                        help="debug logging on stderr")

    audit_opts = argparse.ArgumentParser(add_help=False)
    audit_opts.add_argument("--config", default=DEFAULT_CONFIG, metavar="FILE",
                            help="list of sites (default: ./%s)" % DEFAULT_CONFIG)
    audit_opts.add_argument("--only", metavar="TEXT",
                            help="only sites whose name or URL contains TEXT")
    audit_opts.add_argument("--url", metavar="URL",
                            help="audit this URL once, without a config file")
    audit_opts.add_argument("--user-agent", metavar="UA",
                            help="User-Agent header (default: %s)" % audit.DEFAULT_USER_AGENT)
    audit_opts.add_argument("--accept-language", metavar="LANG",
                            help="Accept-Language header (default: %s)"
                                 % audit.DEFAULT_ACCEPT_LANGUAGE)

    render_opts = argparse.ArgumentParser(add_help=False)
    render_opts.add_argument("--open", action="store_true",
                             help="open the report in the default browser")

    ap = argparse.ArgumentParser(
        prog="sitepulse",
        description="Measure your websites weekly; act on at most three things per site.")
    ap.add_argument("--version", action="version", version="sitepulse " + __version__)
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("audit", parents=[common, audit_opts],
                   help="measure the sites and write data + history")
    sub.add_parser("render", parents=[common, render_opts],
                   help="build the HTML report from a run's data")
    sub.add_parser("run", parents=[common, audit_opts, render_opts],
                   help="audit, then render")
    seo.add_commands(sub)
    return ap


def load_sites(args):
    """The list of site dicts to audit, with config defaults merged in."""
    if args.url:
        _, display = audit.normalize_url(args.url)
        name = urllib.parse.urlsplit(display).netloc or args.url
        return [{"name": name, "url": args.url}], {}

    try:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise UsageError("config file not found: %s\nCopy sites.example.json to "
                         "sites.json, or use --url to audit a single site."
                         % args.config)
    except (OSError, ValueError) as e:
        raise UsageError("cannot read config %s: %s" % (args.config, e))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("sites"), list):
        raise UsageError('config %s must be a JSON object with a "sites" list'
                         % args.config)

    defaults = {k: cfg[k] for k in ("user_agent", "accept_language",
                                    "cta_words", "cta_languages") if cfg.get(k)}
    sites = []
    for entry in cfg["sites"]:
        if not isinstance(entry, dict):
            raise UsageError('every entry of "sites" must be a JSON object')
        if not entry.get("active", True):
            continue
        site = dict(defaults)
        if entry.get("cta_languages") and not entry.get("cta_words"):
            site.pop("cta_words", None)     # a site-level language choice wins
        site.update({k: v for k, v in entry.items() if v not in (None, "", [])})
        sites.append(site)
    if args.only:
        q = args.only.lower()
        sites = [s for s in sites
                 if q in (str(s.get("name", "")) + " " + str(s.get("url", ""))).lower()]
    if not sites:
        raise UsageError("no site to check (none active, or --only matched nothing)")
    return sites, defaults


def do_audit(args):
    sites, defaults = load_sites(args)
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    data, md_path = audit.run(
        sites, args.dir, date,
        user_agent=args.user_agent or defaults.get("user_agent"),
        accept_language=args.accept_language,
        out=lambda line: print(line, flush=True))
    print("\nAverage %s/100 - wrote %s" % (data["average"], md_path))
    return date


def do_render(args, date=None):
    path = render.render(args.dir, date or args.date, open_browser=args.open)
    print(path)
    return path


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help(sys.stderr)
        return 2
    if args.command in ("seo-capture", "seo-head"):
        try:
            return seo.run(args)
        except (ValueError, OSError, KeyError, TypeError) as e:
            print("sitepulse: %s" % e, file=sys.stderr)
            return 2
    logging.basicConfig(stream=sys.stderr, force=True,
                        level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "audit":
            do_audit(args)
        elif args.command == "render":
            do_render(args)
        else:
            do_render(args, do_audit(args))
    except (UsageError, audit.HistoryError, FileNotFoundError) as e:
        print("sitepulse: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
