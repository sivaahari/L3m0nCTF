"""`python -m l3mon verify ...`: the crew's commands for the verification portal (see the package docstring)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import certificate, codes, issue, platform, site, store


def add_parser(sub) -> None:
    v = sub.add_parser("verify", help="the participant verification portal: issue codes and build the static website (docs/runbooks/22-verify-portal.md)")
    vs = v.add_subparsers(dest="verify_cmd", required=True)

    i = vs.add_parser("init", help="make an empty credentials database (refuses an existing file)")
    i.add_argument("db")

    s = vs.add_parser("issue", help="issue codes to everyone in the given lists who has none yet (safe to run again)")
    s.add_argument("db")
    s.add_argument("--date", required=True, help="the Issue Date of the codes issued in this run, e.g. 2026-11-30")
    s.add_argument("--org", help="organisers: one full name per line")
    s.add_argument("--prelims", help="qualifier participants: the output of export_prelims.py (or a CSV with user_id, username, studio)")
    s.add_argument("--finals", help="finals results: a CSV with username, studio, rank and optionally user_id")
    s.add_argument("--reissue", action="append", default=[], metavar="CODE",
                   help="a WITHDRAWN code whose holder may get a new code in this run (repeat for several); without it a withdrawn holder gets nothing")

    w = vs.add_parser("withdraw", help="withdraw one code (its page then says it is no longer valid; the reason stays private)")
    w.add_argument("db")
    w.add_argument("code")
    w.add_argument("--reason", required=True)

    ls = vs.add_parser("list", help="print every code with its category, status and shown fields")
    ls.add_argument("db")

    b = vs.add_parser("build", help="write the static website for nginx from the database")
    b.add_argument("db")
    b.add_argument("out", help="the output folder (a previous build there is replaced as a whole)")
    b.add_argument("--sponsors", help="the sponsor folder: sponsors.json and the logo files")
    b.add_argument("--base-url", default=site.BASE_URL, help=f"the portal's address for the QR codes (default {site.BASE_URL})")
    b.add_argument("--certificate", action="append", default=[], metavar="CAT[=DESIGN.svg]",
                   help="also print a PDF certificate for every valid code of CAT from an SVG design with placeholders; PAR alone uses the "
                        "bundled qualifier design (needs Chrome, Chromium or Edge; L3MON_CHROME names one). May be given once per category.")

    e = vs.add_parser("export-platform", help="write the platform's list (player id -> qualifier code) for the studio-page certificates; PRIVATE")
    e.add_argument("db")
    e.add_argument("out", help="the JSON file to write (it holds usernames and codes: keep it private)")
    e.add_argument("--base-url", default=site.BASE_URL, help=f"the portal's address (default {site.BASE_URL})")


def _safe_output() -> None:
    """Names may hold any character. A Windows pipe or file defaults to cp1252, where printing 'Ŝ' or '名' would crash: write UTF-8 when the output
    is not a console (a real console takes any character), and never crash on a character the output cannot hold."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="backslashreplace")
            else:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def run(args: argparse.Namespace) -> int:
    _safe_output()
    try:
        if args.verify_cmd == "init":
            if Path(args.db).exists():
                print(f"error: {args.db} already exists")
                return 1
            store.connect(Path(args.db), create=True).close()
            print(f"An empty credentials database is at {args.db}. Keep it private and backed up: it is the only record of the codes.")
            return 0

        if args.verify_cmd == "issue":
            if not (args.org or args.prelims or args.finals):
                print("error: give at least one of --org, --prelims, --finals")
                return 1
            conn = store.connect(Path(args.db))
            try:
                report = issue.issue(
                    conn, issue_date=args.date,
                    org=issue.read_org(Path(args.org)) if args.org else None,
                    prelims=issue.read_prelims(Path(args.prelims)) if args.prelims else None,
                    finals=issue.read_finals(Path(args.finals)) if args.finals else None,
                    reissue={codes.normalize(c) or c for c in args.reissue})
            finally:
                conn.close()
            for line in report.lines():
                print(line)
            return 0

        if args.verify_cmd == "withdraw":
            code = codes.normalize(args.code) or args.code
            conn = store.connect(Path(args.db))
            try:
                cred = store.withdraw(conn, code, args.reason)
            finally:
                conn.close()
            print(f"{cred.verify_code} is withdrawn. Build and publish the site again for its page to say so.")
            return 0

        if args.verify_cmd == "list":
            conn = store.connect(Path(args.db))
            try:
                rows = store.all_rows(conn)
            finally:
                conn.close()
            for c in rows:
                shown = " | ".join(f"{k}: {v}" for k, v in c.shown())
                extra = f" -> {c.replaced_by}" if c.replaced_by else ""
                print(f"{c.verify_code}  {c.status}{extra}  {shown}")
            print(f"{len(rows)} code(s)")
            return 0

        if args.verify_cmd == "build":
            designs = {}
            for spec in args.certificate:
                cat, _, path = spec.partition("=")
                cat = cat.strip().upper()
                if cat in designs:
                    raise site.SiteError(f"--certificate {cat} is given twice")
                if not path:
                    if cat not in certificate.BUNDLED:
                        raise site.SiteError(f"--certificate {cat} needs a design: {cat}=path/to/design.svg")
                    path = str(certificate.BUNDLED[cat])
                designs[cat] = certificate.load(Path(path))
            summary = site.build(Path(args.db), Path(args.out), sponsors_dir=Path(args.sponsors) if args.sponsors else None, base_url=args.base_url,
                                 certificates=designs, log=print)
            valid = summary["counts"]["valid"]
            print(f"Built {summary['pages']} pages into {args.out}: " + ", ".join(f"{k} {n}" for k, n in valid.items())
                  + f", {summary['counts']['withdrawn']} withdrawn, {summary['counts']['replaced']} replaced"
                  + (f", {summary['counts']['pdf']} PDF certificate(s)." if summary["counts"].get("pdf") else "."))
            return 0
        if args.verify_cmd == "export-platform":
            n = platform.write(Path(args.db), Path(args.out), args.base_url)
            print(f"Wrote {n} qualifier codes to {args.out} for the platform. It is private: import it on the server (runbook 22, step 7), then delete every copy but yours.")
            return 0
    except (store.StoreError, issue.IssueError, site.SiteError, certificate.CertificateError, platform.PlatformError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    return 2
