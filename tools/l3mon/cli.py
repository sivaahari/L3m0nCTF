"""Command line for the platform tools: python -m l3mon <command>. Standard library only."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import hygiene


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l3mon", description="L3m0nCTF platform tools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hygiene", help="scan this repository for flags, secrets, dumps and story words")
    h.add_argument("--root", default=".", help="folder to scan (default: the current folder)")
    h.add_argument("--deny-words", default="", help="comma separated words that must not appear (story names)")
    h.add_argument("--deny-words-file", help="a file with one such word per line (kept outside the repository)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hygiene":
        words = [w for w in args.deny_words.split(",") if w.strip()]
        if args.deny_words_file:
            words += [w.strip() for w in Path(args.deny_words_file).read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
        findings = hygiene.scan(Path(args.root), words)
        for f in findings:
            print(f.human())
        print(f"{len(findings)} finding(s)")
        return 1 if findings else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
