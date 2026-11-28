"""The platform's list of qualifier certificates: player id -> code, for the "Your certificate" card on each player's studio page.

    python -m l3mon verify export-platform DB OUT.json [--base-url https://verify.l3m0nctf.xyz]

Only VALID qualifier (PAR) codes, and only those issued from the platform's own export (`prelims:user:<id>`), so every entry names a platform
account. A code that was withdrawn, or replaced by a finals code, is not in the list, and the next import takes it off the platform. The list
is the whole truth for the platform: plugins/l3mon_certificates/loader.py makes its table exactly this list.

The file holds usernames and codes: keep it private, copy it to the server only to import it, and delete it there afterwards.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import codes, store

FORMAT = "l3mon-certificates-1"  # plugins/l3mon_certificates/loader.py reads exactly this
BASE_URL_RE = re.compile(r"\Ahttps://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
_REF = re.compile(r"\Aprelims:user:([1-9]\d*)\Z")


class PlatformError(ValueError):
    pass


def platform_list(conn: sqlite3.Connection, base_url: str) -> dict:
    if not BASE_URL_RE.match(base_url or ""):
        raise PlatformError(f"--base-url must be the portal's https address with no path, e.g. https://verify.l3m0nctf.xyz (not {base_url!r})")
    entries = []
    for c in store.all_rows(conn):
        if c.category != "PAR" or c.status != "valid":
            continue
        m = _REF.match(c.source_ref or "")
        if not m:
            continue  # not from the platform's export: it names no account
        if not codes.CODE_RE.match(c.verify_code):
            raise PlatformError(f"the database holds a malformed code: {c.verify_code!r}")
        entries.append({"user_id": int(m.group(1)), "username": c.username, "code": c.verify_code})
    entries.sort(key=lambda e: e["user_id"])
    ids = [e["user_id"] for e in entries]
    if len(ids) != len(set(ids)):
        raise PlatformError("two valid qualifier codes name the same player: the database is inconsistent (look at `verify list`)")
    return {"format": FORMAT, "base_url": base_url, "codes": entries}


def write(db: Path, out: Path, base_url: str) -> int:
    conn = store.connect(db)
    try:
        data = platform_list(conn, base_url)
    finally:
        conn.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(data["codes"])
