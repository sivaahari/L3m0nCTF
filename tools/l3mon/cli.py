"""Command line for the platform tools: python -m l3mon <command>. Standard library only."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import api_rules, config, hygiene, secrets_gen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l3mon", description="L3m0nCTF platform tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hygiene", help="scan this repository for flags, secrets, dumps and story words")
    h.add_argument("--root", default=".", help="folder to scan (default: the current folder)")
    h.add_argument("--deny-words", default="", help="comma separated words that must not appear (story names)")
    h.add_argument("--deny-words-file", help="a file with one such word per line (kept outside the repository)")
    h.add_argument("--history", action="store_true", help="also read every line ever added in any commit (a secret deleted from the tree is still in the history)")

    c = sub.add_parser("config", help="check the event settings file and render what CTFd needs")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    cv = csub.add_parser("validate", help="check a settings file")
    cv.add_argument("file")
    cr = csub.add_parser("render", help="write the generated deployment files")
    cr.add_argument("file")
    cr.add_argument("--out", required=True, help="folder for the generated files")

    s = sub.add_parser("secrets", help="generate the random secrets for this machine")
    ssub = s.add_subparsers(dest="secrets_cmd", required=True)
    sg = ssub.add_parser("generate", help="write one file per secret; values are never printed")
    sg.add_argument("--dir", default=".secrets", help="where to write them (default: .secrets)")
    sg.add_argument("--force", action="store_true", help="replace secrets that already exist")

    a = sub.add_parser("api-rules", help="the nginx rules that keep CTFd's administrator API for the organisers' addresses")
    a.add_argument("action", choices=["generate", "check"], help="generate rewrites the two snippet files; check fails when they are out of date")
    a.add_argument("--image", default="l3mon/ctfd:dev", help="the platform image to read CTFd's routes from")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "hygiene":
        words = [w for w in args.deny_words.split(",") if w.strip()]
        if args.deny_words_file:
            words += [w.strip() for w in Path(args.deny_words_file).read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
        findings = hygiene.scan(Path(args.root), words)
        if args.history:
            findings += hygiene.scan_history(Path(args.root), words)
        for f in findings:
            print(f.human())
        print(f"{len(findings)} finding(s)")
        return 1 if findings else 0

    if args.cmd == "api-rules":
        return api_rules.main([args.action, "--image", args.image])

    if args.cmd == "config":
        try:
            data = config.load(Path(args.file))
        except (OSError, ValueError) as exc:
            print(f"cannot read {args.file}: {exc}")
            return 1
        errors = config.validate(data)
        for e in errors:
            print(f"error: {e}")
        if errors:
            return 1
        if args.config_cmd == "validate":
            print("the settings file is valid")
            return 0
        written = config.render(data, Path(args.out))
        for name, path in written.items():
            print(f"wrote {name} to {path}")
        return 0

    if args.cmd == "secrets":
        try:
            made = secrets_gen.generate(Path(args.dir), force=args.force)
        except FileExistsError as exc:
            print(f"error: {exc}")
            return 1
        print(f"wrote {len(made)} secrets to {args.dir}:")
        for name in made:
            print(f"  {name}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
