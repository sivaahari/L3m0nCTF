"""Keep flags, secrets, dumps and story material out of the public repository (standard library only)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".secrets", "private", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build"}
DUMP_SUFFIXES = (".sql", ".sql.gz", ".dump", ".sqlite", ".sqlite3", ".db", ".bak")
KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

# Any flag with content. The format hint L3m0nCTF{...} is the only allowed form.
FLAG = re.compile(r"L3m0nCTF\{(?!\.\.\.\}|…\})[^}\n]{3,}\}")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TOKENS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
)
MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    message: str

    def human(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: [{self.rule}] {self.message}"


def _name_rules(rel: str, name: str) -> list[Finding]:
    lower = name.lower()
    if lower.endswith(DUMP_SUFFIXES):
        return [Finding("dump", rel, 0, "a database dump or backup must never be committed")]
    if lower.endswith(KEY_SUFFIXES):
        return [Finding("key-file", rel, 0, "a key file must never be committed")]
    if lower == ".env" or (lower.startswith(".env.") and lower != ".env.example"):
        return [Finding("env-file", rel, 0, "an environment file holds secrets; commit only .env.example")]
    return []


def scan(root: Path, deny_words: list[str] | None = None) -> list[Finding]:
    """Scan every text file under root. deny_words are matched as whole words, ignoring case."""
    root = Path(root)
    deny = None
    words = [w.strip() for w in (deny_words or []) if w and w.strip()]
    if words:
        deny = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in words) + r")(?![A-Za-z0-9])", re.IGNORECASE)
    findings: list[Finding] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        for entry in sorted(folder.iterdir(), key=lambda p: p.name):
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
                continue
            rel = entry.relative_to(root).as_posix()
            findings.extend(_name_rules(rel, entry.name))
            try:
                raw = entry.read_bytes()[:MAX_BYTES]
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue  # binary
            text = raw.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if FLAG.search(line):
                    findings.append(Finding("flag", rel, lineno, "a flag-shaped string; only the format hint L3m0nCTF{...} may appear in this repository"))
                if PRIVATE_KEY.search(line):
                    findings.append(Finding("private-key", rel, lineno, "a private key"))
                for rx in TOKENS:
                    if rx.search(line):
                        findings.append(Finding("token", rel, lineno, "looks like an access token"))
                if deny and deny.search(line):
                    findings.append(Finding("deny-word", rel, lineno, "contains a word that belongs in the private repository"))
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule))
