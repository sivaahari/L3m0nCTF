"""The import of the platform's certificate list, run INSIDE the platform container by the crew (docs/runbooks/22-verify-portal.md, step 7):

    docker exec -i "$(tools/compose.sh ps -q ctfd)" bash -c "source /usr/local/lib/l3mon-env.sh; cd /opt/CTFd; python -m CTFd.plugins.l3mon_certificates.loader" < list.json
    ... the same with `loader --apply` to write it

The list is what `python -m l3mon verify export-platform` writes on the laptop: {"format": "l3mon-certificates-1", "base_url": "https://verify...",
"codes": [{"user_id": 12, "username": "alice", "code": "L3M0N26-PAR-BCDFG"}, ...]}. Without --apply nothing is written: it only says what would change.

The rules:
- the list is the WHOLE truth: after an import the table holds exactly the list's codes (a code withdrawn or replaced on the laptop disappears from
  the platform at the next import), all in one transaction;
- the list is refused as a whole, and nothing changes, when anything in it is wrong: an unknown key, a code that is not a qualifier code, a code or a
  player twice, an address that is not the portal's;
- a player id that has no account on the platform is left out and counted; a player who renamed themself since the export still gets their code
  (it belongs to the account) and is counted;
- the report names no code and no username (it is printed on a shared terminal): only numbers and player ids;
- one line in the audit trail (`certificates.import`) with the numbers. Importing does not release anything: the crew page does that.
"""
import json
import sys

from CTFd.models import Users, db
from CTFd.plugins.l3mon_certificates import codes
from CTFd.plugins.l3mon_certificates.models import Certificate

FORMAT = "l3mon-certificates-1"
MAX_CODES = 20000


class Refused(ValueError):
    pass


def parse(payload) -> tuple:
    """-> (base_url, [(user_id, username, code)]) or Refused with every problem found."""
    problems = []
    if not isinstance(payload, dict):
        raise Refused("the list must be a JSON object")
    extra = sorted(set(payload) - {"format", "base_url", "codes"})
    if extra:
        problems.append(f"unknown keys: {', '.join(extra)}")
    if payload.get("format") != FORMAT:
        problems.append(f'"format" must be "{FORMAT}" (made by `python -m l3mon verify export-platform`)')
    base = payload.get("base_url")
    if not isinstance(base, str) or not codes.BASE_URL_RE.match(base):
        problems.append('"base_url" must be the portal\'s https address with no path, e.g. https://verify.l3m0nctf.xyz')
    rows = payload.get("codes")
    if not isinstance(rows, list) or len(rows) > MAX_CODES:
        problems.append(f'"codes" must be a list of at most {MAX_CODES} entries')
        rows = []
    seen_users, seen_codes, out = set(), set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {"user_id", "username", "code"}:
            problems.append(f"entry {n}: must hold exactly user_id, username and code")
            continue
        uid, name, code = row["user_id"], row["username"], row["code"]
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            problems.append(f"entry {n}: user_id must be a positive whole number")
            continue
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            problems.append(f"entry {n} (player {uid}): username must be text of 1 to 128 characters")
            continue
        if not isinstance(code, str) or not codes.CODE_RE.match(code):
            problems.append(f"entry {n} (player {uid}): not a qualifier code (L3M0N26-PAR-xxxxx)")
            continue
        if uid in seen_users:
            problems.append(f"entry {n}: player {uid} is in the list twice")
        if code in seen_codes:
            problems.append(f"entry {n} (player {uid}): this code is in the list twice")
        seen_users.add(uid)
        seen_codes.add(code)
        out.append((uid, name, code))
    if problems:
        raise Refused("; ".join(problems[:20]) + (f"; and {len(problems) - 20} more" if len(problems) > 20 else ""))
    return base, out


def load(payload, apply=False) -> dict:
    """Check the list against the platform and, with apply, make the table exactly the list. Returns the report (numbers and player ids only)."""
    base, rows = parse(payload)
    from CTFd.plugins.l3mon_certificates import release
    from CTFd.plugins.l3mon_core.locks import serialize

    if apply:
        serialize()  # the plan's lock first, so the report below is the truth the write replaces (two imports never interleave)
    names = dict(db.session.query(Users.id, Users.name).filter(Users.id.in_([uid for uid, _, _ in rows])).all()) if rows else {}
    kept = [(uid, name, code) for uid, name, code in rows if uid in names]
    missing = sorted(uid for uid, _, _ in rows if uid not in names)
    renamed = sorted(uid for uid, name, _ in kept if names[uid] != name)
    before = {uid: code for uid, code in db.session.query(Certificate.user_id, Certificate.code).all()}
    after = {uid: code for uid, _, code in kept}
    report = {
        "codes": len(kept),
        "new": len([u for u in after if u not in before]),
        "changed": len([u for u in after if u in before and before[u] != after[u]]),
        "removed": len([u for u in before if u not in after]),
        "unchanged": len([u for u in after if before.get(u) == after[u]]),
        "no_such_player": missing,
        "renamed_since_export": renamed,
        "base_url": base,
        "applied": False,
    }
    if not apply:
        db.session.rollback()
        return report
    from CTFd.plugins.l3mon_core import audit
    from CTFd.utils import set_config

    try:
        db.session.query(Certificate).delete(synchronize_session=False)
        db.session.flush()
        db.session.add_all([Certificate(user_id=uid, username=name, code=code) for uid, name, code in kept])
        audit.record("certificates.import", "qualifier certificates",
                     f"{report['codes']} codes ({report['new']} new, {report['changed']} changed, {report['removed']} removed); "
                     f"{len(missing)} without an account, {len(renamed)} renamed since the export", system=True)
        db.session.flush()
    except Exception:
        db.session.rollback()
        raise
    set_config(release.KEY_BASE_URL, base)  # commits the whole import with it
    db.session.commit()
    report["applied"] = True
    return report


def _print(report):
    lines = [
        f"{'Imported' if report['applied'] else 'Would import (nothing written; add --apply)'}: {report['codes']} codes for {report['base_url']}",
        f"  new {report['new']}, changed {report['changed']}, removed {report['removed']}, unchanged {report['unchanged']}",
    ]
    if report["no_such_player"]:
        ids = report["no_such_player"]
        lines.append(f"  left out, no such player on the platform: {len(ids)} (ids {', '.join(map(str, ids[:30]))}{' ...' if len(ids) > 30 else ''})")
    if report["renamed_since_export"]:
        ids = report["renamed_since_export"]
        lines.append(f"  renamed since the export (they keep their code): {len(ids)} (ids {', '.join(map(str, ids[:30]))}{' ...' if len(ids) > 30 else ''})")
    print("\n".join(lines))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if [a for a in argv if a != "--apply"]:
        print("usage: python -m CTFd.plugins.l3mon_certificates.loader [--apply] < list.json", file=sys.stderr)
        return 2
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        print(f"refused: the list is not UTF-8 JSON ({error.__class__.__name__}); nothing was changed", file=sys.stderr)
        return 1
    from CTFd import create_app

    app = create_app()
    with app.app_context():
        try:
            report = load(payload, apply="--apply" in argv)
        except Refused as refused:
            print(f"refused: {refused}; nothing was changed", file=sys.stderr)
            return 1
        except Exception as error:  # noqa: BLE001  (a database error's text carries the rows, codes and usernames: name only its kind)
            db.session.rollback()
            print(f"refused: an error while importing ({error.__class__.__name__}); nothing was changed", file=sys.stderr)
            return 1
    _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
