"""The scoreboard, "TRP ratings" (SP3 part 3.5): the JSON at `/api/v1/l3mon/scoreboard` and the rows' markup at `.../scoreboard/rows`.

- The list is the CTFtime feed's list (`l3mon_core.standings.ranked` over CTFd's public standings): only studios with TRP above zero,
  never a hidden or a banned one, CTFd's tie-break, the freeze applied. The site and the feed cannot disagree.
- A row is a position, a studio name, a solve count and a TRP number. No dates, no ids of studios, no countdowns.
- The solve count is the studio's `Solves` rows (before the freeze second while the scoreboard is frozen, as the standings are), so a
  programme the crew has pulled back since still counts: the TRP stays, so the solve does.
- Before the start the list is empty. The first 100 are listed (`TOP`); `total` says how many are ranked.
- `me` is the viewer's own line: the name, the **live** score, the place in the list (None while the scoreboard is frozen, before the start,
  or when the studio is not in the list: no score yet, hidden) and how many are ranked. None for the crew.
- A name is shown as text (the template escapes it).
"""
import base64
import hashlib
import json
from types import SimpleNamespace

from CTFd.models import Solves, db
from CTFd.plugins.l3mon_board import banner, envelope, figures, markup
from CTFd.plugins.l3mon_core.clock import current_phase, window
from CTFd.plugins.l3mon_core.standings import number, ranked
from CTFd.plugins.l3mon_core.tick import signature
from CTFd.utils.dates import unix_time_to_utc
from CTFd.utils.scores import get_standings

TOP = 100  # rows listed; a studio below them has its own line in `me`


def _solve_counts(account_ids) -> dict:
    """{studio id: solves} for these studios, counted as the standings count (before the freeze while frozen)."""
    if not account_ids:
        return {}
    query = db.session.query(Solves.account_id, db.func.count(Solves.id)).filter(Solves.account_id.in_(account_ids))
    freeze = window().freeze
    if freeze:
        query = query.filter(Solves.date < unix_time_to_utc(freeze))
    return {int(account): int(count) for account, count in query.group_by(Solves.account_id).all()}


def read(team) -> SimpleNamespace:
    """What the viewer's studio (a Teams row, or None for the crew) is shown: `phase`, `total` (how many are ranked), `rows` (the first
    100: pos, team_id, name, solves, score), `me` and `sig` (a signature of the rows as this viewer sees them)."""
    phase = current_phase()
    standings = [] if phase.state == "before" else ranked(get_standings(admin=False))
    shown = standings[:TOP]
    counts = _solve_counts([row.account_id for _, row in shown])
    rows = [
        SimpleNamespace(pos=pos, team_id=int(row.account_id), name=row.name, solves=counts.get(int(row.account_id), 0), score=number(row.score))
        for pos, row in shown
    ]
    me = None
    if team is not None:
        place = None
        if not phase.frozen:
            place = next((pos for pos, row in standings if int(row.account_id) == team.id), None)
        me = {"name": team.name, "score": int(team.get_score(admin=True) or 0), "pos": place, "of": len(standings)}
    mine = team.id if team is not None else None
    raw = json.dumps([[[r.pos, r.team_id, r.name, r.solves, r.score] for r in rows], mine], separators=(",", ":"))
    sig = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()).decode().rstrip("=")[:16]
    return SimpleNamespace(phase=phase, total=len(standings), rows=rows, me=me, sig=sig, mine=mine)


def answer(view) -> tuple:
    """-> (data, etag) for a `read()`. The tag is over the data with the tick left out (the tick moves for every studio's news)."""
    data = {
        "ver": signature(), "phase": {"state": view.phase.state, "frozen": view.phase.frozen}, "banner": banner.for_phase(view.phase),
        "total": view.total, "shown": len(view.rows), "rows_sig": view.sig, "me": view.me,
    }
    return data, envelope.strong_etag("s", {**data, "ver": 0})


def rows_etag(view) -> str:
    return f'"r{view.sig}"'


def html(view) -> str:
    """One `<li class="sb-row">` for each row listed. The bar is relative to the leader and never empty."""
    lead = view.rows[0].score if view.rows else 0
    out = []
    for r in view.rows:
        yours = r.team_id == view.mine
        out.append(markup.render(
            "scoreboard_row.html",
            row={
                "pos": r.pos, "name": r.name, "score": r.score, "solves": f"{r.solves} {'solve' if r.solves == 1 else 'solves'}",
                "pct": max(1, figures.js_round(r.score / lead * 100)) if lead > 0 else 0,
                "cls": "is-you" if yours else (f"is-top is-p{r.pos}" if r.pos <= 3 else ""),
                "you": " (your studio)" if yours else "",
            },
        ))
    return "".join(out)
