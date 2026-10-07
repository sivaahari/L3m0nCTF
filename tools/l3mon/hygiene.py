"""Keep flags, secrets, dumps and story material out of the public repository (standard library only).

What it looks at: every file git tracks or would add (`git ls-files --cached --others --exclude-standard`), or, outside a git
repository, every file under the folder except tool folders such as node_modules and nested repositories (a folder holding
its own `.git` is a different repository, for example the private one). With `--history` it also reads every line ever
added in any commit, because a secret removed from the tree is still in the history.

What it looks for: flags (in any letter case, URL- or HTML-encoded, base64, hex, UTF-16, inside binary files too), private
keys, access tokens (AWS, GitHub, Slack, Google, CTFd), database dumps and key files by name, archives, captures and
executables by name (challenge files belong in the private repository), and the story words someone supplies.
"""
from __future__ import annotations

import base64
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".secrets", "__pycache__", ".pytest_cache", ".venv", "venv"}
DUMP_SUFFIXES = (".sql", ".sql.gz", ".dump", ".sqlite", ".sqlite3", ".db", ".bak")
KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
# archives, captures, disk images and executables: not text, and where challenge files and dumps tend to hide
ARTIFACT_SUFFIXES = (
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".pcap", ".pcapng", ".cap",
    ".elf", ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".img", ".vmdk", ".qcow2", ".apk", ".jar", ".war",
)

# The event's flag format is L3m0n{...}. The retired L3m0nCTF{...} is looked for too, so a flag written the old way cannot slip through.
PREFIXES = ("L3m0n{", "L3m0nCTF{")
# Any flag with content, in any letter case. The format hint L3m0n{...} is the only allowed form.
FLAG = re.compile(r"l3m0n(?:ctf)?\{(?!\.\.\.\}|…\})[^}\n]{3,}\}", re.IGNORECASE)
# the prefix with its brace written another way: %7B, &#123;, &#x7B;, &lbrace;, {, \x7b
FLAG_ENCODED_BRACE = re.compile(r"l3m0n(?:ctf)?(?:%7b|&#0*123;|&#x0*7b;|&lbrace;|\\u007b|\\x7b)", re.IGNORECASE)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TOKENS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google API key
    re.compile(r"\bGOCSPX-[0-9A-Za-z_-]{20,}\b"),  # Google OAuth client secret
    re.compile(r"\bctfd_[0-9a-f]{64}\b"),  # a CTFd API token (this project's administrator token has this shape)
)
MAX_BYTES = 5 * 1024 * 1024


def _encoded_prefixes() -> list[tuple[str, bytes]]:
    """The flag prefixes as they look inside a binary or an encoded blob: (name, bytes to look for), all compared in lower case."""
    out: list[tuple[str, bytes]] = []
    for prefix in PREFIXES:
        raw = prefix.encode()
        out.append(("plain", raw.lower()))
        out.append(("hex", raw.hex().encode()))
        out.append(("utf-16-le", prefix.lower().encode("utf-16-le")))
        out.append(("utf-16-be", prefix.lower().encode("utf-16-be")))
        # base64 of the prefix depends on where it starts among the 3-byte groups: three alignments, keeping only the characters
        # that do not depend on the bytes before or after it
        for shift in range(3):
            encoded = base64.b64encode(b"x" * shift + raw + b"yyy")
            first, last = -(-shift // 3), (shift + len(raw)) // 3  # whole groups inside the prefix
            out.append((f"base64-{shift}", encoded[4 * first : 4 * last]))
    return out


ENCODED = _encoded_prefixes()


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
    if lower.endswith(ARTIFACT_SUFFIXES):
        return [Finding("artifact", rel, 0, "an archive, capture, image or executable: challenge files and dumps belong in the private repository")]
    return []


def _deny_pattern(deny_words: list[str] | None):
    words = [w.strip() for w in (deny_words or []) if w and w.strip()]
    if not words:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in words) + r")(?![A-Za-z0-9])", re.IGNORECASE)


def _line_findings(rel: str, lineno: int, line: str, deny) -> list[Finding]:
    found = []
    if FLAG.search(line):
        found.append(Finding("flag", rel, lineno, "a flag-shaped string; only the format hint L3m0n{...} may appear in this repository"))
    elif FLAG_ENCODED_BRACE.search(line):
        found.append(Finding("flag", rel, lineno, "the flag prefix with an encoded opening brace"))
    if PRIVATE_KEY.search(line):
        found.append(Finding("private-key", rel, lineno, "a private key"))
    for rx in TOKENS:
        if rx.search(line):
            found.append(Finding("token", rel, lineno, "looks like an access token"))
    if deny and deny.search(line):
        found.append(Finding("deny-word", rel, lineno, "contains a word that belongs in the private repository"))
    return found


def _binary_findings(rel: str, raw: bytes, text_file: bool = False) -> list[Finding]:
    """The prefix inside a binary, or hex or base64 encoded inside any file. (Plain text is the line rules' business: it must
    allow the format hint, which a byte search cannot.)"""
    lower = raw.lower()
    for name, needle in ENCODED:
        if text_file and not (name == "hex" or name.startswith("base64")):
            continue
        haystack = raw if name.startswith("base64") else lower  # base64 is case-sensitive; the rest is compared in lower case
        if needle and needle in haystack:
            return [Finding("flag-binary", rel, 0, f"the flag prefix ({name}) appears inside a binary or encoded file")]
    return []


def _files(root: Path) -> list[Path]:
    """Files to scan, relative to root: what git tracks or would add, or a walk when this is not a git repository."""
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"], capture_output=True, timeout=60)
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
        if done.returncode == 0 and top.returncode == 0 and Path(top.stdout.strip()).resolve() == root.resolve():
            return [Path(p) for p in done.stdout.decode("utf-8", "replace").split("\0") if p and (root / p).is_file()]
    except (OSError, subprocess.SubprocessError):
        pass
    out, stack = [], [root]
    while stack:
        folder = stack.pop()
        for entry in sorted(folder.iterdir(), key=lambda p: p.name):
            if entry.is_dir():
                if entry.name in SKIP_DIRS or (entry / ".git").exists():  # a nested repository is somebody else's to check
                    continue
                stack.append(entry)
            else:
                out.append(entry.relative_to(root))
    return out


def scan(root: Path, deny_words: list[str] | None = None) -> list[Finding]:
    """Scan every file git tracks or would add (all files outside git). deny_words are matched as whole words, ignoring case."""
    root = Path(root)
    deny = _deny_pattern(deny_words)
    findings: list[Finding] = []
    for relpath in _files(root):
        rel = relpath.as_posix()
        entry = root / relpath
        findings.extend(_name_rules(rel, entry.name))
        try:
            raw = entry.read_bytes()[:MAX_BYTES]
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            findings.extend(_binary_findings(rel, raw))
            continue
        text = raw.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            findings.extend(_line_findings(rel, lineno, line, deny))
        findings.extend(_binary_findings(rel, raw, text_file=True))  # hex or base64 inside a text file
    return sorted(set(findings), key=lambda f: (f.path, f.line, f.rule))


def scan_history(root: Path, deny_words: list[str] | None = None) -> list[Finding]:
    """Every line ever added in any commit of any branch: a secret deleted from the tree is still in the history."""
    root = Path(root)
    deny = _deny_pattern(deny_words)
    done = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "-p", "-U0", "--no-color", "--no-renames", "--format=commit:%H"],
        capture_output=True, timeout=600,
    )
    if done.returncode != 0:
        return [Finding("history", "(git log)", 0, "cannot read the history: " + done.stderr.decode("utf-8", "replace").strip()[:200])]
    findings, commit, path = [], "", ""
    for raw_line in done.stdout.decode("utf-8", "replace").splitlines():
        if raw_line.startswith("commit:"):
            commit = raw_line[7:19]
        elif raw_line.startswith("+++ b/"):
            path = raw_line[6:]
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            for f in _line_findings(f"{path}@{commit}", 0, raw_line[1:], deny):
                findings.append(Finding(f.rule, f.path, 0, f.message + " (in the history)"))
    return sorted(set(findings), key=lambda f: (f.path, f.rule))
