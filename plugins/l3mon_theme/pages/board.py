"""The channels board, a programme's own page and the notifications page (SP6, stream A).

    board_view(data, ...)    the board page's view model (docs/template-contract.md, "board"), made from what `l3mon_board.board.build` returns
    notice_lines(lines)      the notifications page's list (contract "nt"): the newest first, at most 50
    channel_keys(channels)   the number a channel is known by on the page

The three functions above are pure (no Flask, no database), so the golden pairs of the prototype can be fed to them
(tests/test_board_golden.py). They are the Python form of `boardView` (server/api-board.mjs) and `noticeItems` (server/notices.mjs) of
the prototype, and the numbers they print must match it to the character, so a rounding rule is JavaScript's (`math.floor(x + .5)`).

**The channel key.** The prototype has one number for a channel: it is the channel's id, its `CH 03`, the `?ch=` of its address, its art
class `.ch-3` and the key that jumps to it. The platform has two: the database row (`id`, which programmes point at) and the number the
crew gave (`no`, 0 to 99; the news says "CH 3" with it). The page uses `no` for all of the prototype's uses. When two channels have the same
`no`, or one is outside 0 to 99, the page falls back to the database ids for every channel, so nothing is shown twice; `api.js` applies
the same rule to the board reply (src/js/api.js, `channelKeys`), and a test pins both.

**Gates.** `/board` and `/board/<slug>` run CTFd's own decorators and the body of CTFd's own `/challenges` view, so a visitor meets the
gate CTFd gives; the programme's page then answers 404 for a slug that is not on air for this viewer, exactly as for a slug that never
existed. The notifications page is served for a signed-in player (or the crew) before the guard of `l3mon_board/notices.py`, which closes the
stock page for everyone else.
"""
import math
import re

from CTFd.plugins.l3mon_theme.registry import value

# the categories the filter chips know, in the order the board shows them: id, label (the prototype's CATEGORIES)
CATEGORIES = (
    ("web", "Web"), ("crypto", "Crypto"), ("forensics", "Forensics"), ("osint", "OSINT"), ("pwn", "Pwn"), ("reverse", "Reverse"),
    ("hardware", "Hardware"), ("rf", "RF"), ("ai", "AI"), ("web3", "Web3"), ("misc", "Misc"),
)
DIFFICULTIES = (("warmup", "Warm-up"), ("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard"), ("insane", "Insane"))
_LABEL = dict(CATEGORIES)
NOTICES_SHOWN = 50  # the page lists the newest 50 (the prototype's NOTICES_SHOWN)
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}\Z")  # a programme's slug as the plan loader writes it (l3mon_release/plan.py PROGRAMME_SLUG; the column holds 48); `match` anchors the start, \Z the end
CHANNEL_ASK = re.compile(r"[0-9]{1,2}\Z")  # ?ch=
COLUMNS = 6  # tiles in a row of the picture
MAX_ROWS = 12


def js_round(x):
    """JavaScript's Math.round (halves go up), which the prototype's numbers use; Python's round() goes to the even number."""
    return math.floor(x + 0.5)


def _pad(n, width):
    return str(n).rjust(width, "0")


def channel_keys(channels):
    """{database id: the number the page knows the channel by}: `no`, or the database ids when `no` cannot be told apart (see the module)."""
    numbers = [c.get("no") for c in channels]
    usable = all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 99 for n in numbers) and len(set(numbers)) == len(numbers)
    return {c["id"]: (c["no"] if usable else c["id"]) for c in channels}


def _plural(n):
    return f"{n} programme{'' if n == 1 else 's'}"


def count_text(n, total, active, coming=0):
    """The filter line (src/js/filters.js, `fltCountText`)."""
    return (f"{n} of {_plural(total)} match" if active else _plural(total)) + (f" · {coming} coming up" if coming > 0 else "")


def _meter(frac):
    on = js_round(frac * 6)
    return "▰" * on + "▱" * (6 - on)


def find_programme(data, slug):
    """The programme with this slug among the ones this viewer may see, or None. A slug that is not plain, or that is withheld, is None alike."""
    if not isinstance(slug, str) or not SLUG.match(slug):
        return None
    return next((p for p in data["programmes"] if p["slug"] == slug), None)


def board_view(data, ask=None, programme=None, show_coming=None):
    """The board page's view model (contract section 4, "board") from `l3mon_board.board.build(...)[0]`.

    `ask` is the raw `?ch=` of the address (a string or None); `programme` a programme of `data` for a deep link (its channel is shown).
    `show_coming` False hides the "N coming up" count (the crew's setting); None takes the data's own `show_coming`."""
    channels = data["channels"]
    if show_coming is None:
        show_coming = data.get("show_coming", True) is not False
    keys = channel_keys(channels)
    by_key = {keys[c["id"]]: c for c in channels}
    programmes = sorted(data["programmes"], key=lambda p: (p["order"], p["id"]))

    def stats(channel):
        shown = [p for p in programmes if p["channel"] == channel["id"]]
        slots = max(channel["total"], len(shown))  # the picture has one cell for each programme, on air or not
        solved = sum(1 for p in shown if p["solved_by_me"])
        return {"slots": slots, "list": shown, "solved": solved, "frac": solved / slots if slots else 0}

    if programme is not None:
        wanted = keys.get(programme["channel"])
    elif isinstance(ask, str) and CHANNEL_ASK.match(ask) and int(ask) in by_key:
        wanted = int(ask)
    else:
        wanted = None
    current = by_key.get(wanted) or (channels[0] if channels else None)
    if current is None:
        return _empty_view()
    key = keys[current["id"]]
    s = stats(current)
    pct = js_round(s["frac"] * 100)
    rows = max(1, math.ceil(s["slots"] / COLUMNS))
    coming = s["slots"] - len(s["list"]) if show_coming else 0
    present = {p["category"] for p in programmes}

    def strip(channel):
        st = stats(channel)
        on = channel["id"] == current["id"]
        sponsor = (channel.get("sponsor") or {}).get("name") or ""
        return {
            "id": str(keys[channel["id"]]), "name": channel["name"], "sig": str(js_round(st["frac"] * 10)), "pct": str(js_round(st["frac"] * 100)),
            "selected": "true" if on else "false", "tabindex": "0" if on else "-1", "current": "page" if on else "",
            "tag": "Sponsored" if sponsor else "", "sponsor": sponsor, "said": f", sponsored by {sponsor}" if sponsor else "",
        }

    sponsor = (current.get("sponsor") or {}).get("name") or ""
    return {
        "ch": str(key), "chNo": f"CH {_pad(key, 2)}", "chName": current["name"], "synopsis": (current.get("synopsis") or "") if s["list"] else "",
        "meter": _meter(s["frac"]), "sigText": f"{pct}% · {s['solved']} of {s['slots']} clear",
        "slug": programme["slug"] if programme is not None else "",
        "ad": f"Sponsored by {sponsor}. These programmes count toward your TRP like any other." if sponsor else "",
        "rows": str(min(MAX_ROWS, rows)),
        "countText": count_text(len(s["list"]), len(s["list"]), False, coming),
        "channels": [strip(c) for c in channels],
        "entries": [
            {"slug": p["slug"], "no": _pad(p["number"], 3), "name": p["name"], "cat": _LABEL.get(p["category"], p["category"]),
             "pts": str(p["value"]), "solved": " · solved by your studio" if p["solved_by_me"] else ""}
            for p in s["list"]
        ],
        "skeleton": [{"c": str(i % COLUMNS), "r": str(min(11, i // COLUMNS))} for i in range(rows * COLUMNS)],
        "cats": [{"id": cid, "label": label, "icon": f"i-{cid}"} for cid, label in CATEGORIES if cid in present],
        "diffs": [{"id": did, "label": label} for did, label in DIFFICULTIES],
    }


def _empty_view():
    """A platform with no channel at all (before the crew has loaded a plan): the board's own frame, with nothing on it."""
    return {
        "ch": "0", "chNo": "CH 00", "chName": "Channels", "synopsis": "", "meter": _meter(0), "sigText": "0% · 0 of 0 clear", "slug": "", "ad": "",
        "rows": "1", "countText": count_text(0, 0, False), "channels": [], "entries": [],
        "skeleton": [{"c": str(i), "r": "0"} for i in range(COLUMNS)], "cats": [], "diffs": [{"id": d, "label": label} for d, label in DIFFICULTIES],
    }


def notice_lines(lines):
    """The notifications page's list from a viewer's lines (oldest first, `[{id, title, content}]`): the newest first, at most 50, each a title and a text."""
    newest = list(reversed(lines))[:NOTICES_SHOWN]
    return [{"title": str(n.get("title") or "News"), "content": str(n.get("content") or "")} for n in newest]


# ---------------------------------------------------------------------------------------------------------------------------------
# The wiring: what only CTFd knows, the three views and the hook. Nothing below runs for a request that is not ours.
# ---------------------------------------------------------------------------------------------------------------------------------

STATE = "l3mon.theme.board"  # the key in this request's environ: what the request built, kept for the request only (flask.g outlives a request when a test, or a
#                              caller, holds one application context, and stale board data would then be served to the next request)
NOTIFICATIONS = "/notifications"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _state():
    from flask import request

    return request.environ.setdefault(STATE, {})


def _build():
    """(data, viewer) of this request, built once: one call of the board plugin whatever number of values read it."""
    state = _state()
    if "built" not in state:
        from CTFd.plugins.l3mon_board import board, viewer

        who = viewer.current()
        data, _ = board.build(who)
        state["built"] = (data, who)
    return state["built"]


def _phase_banner(state):
    from types import SimpleNamespace

    from CTFd.plugins.l3mon_board import banner

    return banner.for_phase(SimpleNamespace(state=state["state"], frozen=state["frozen"]))


@value("board_page", default=None)
def board_page():
    """What the board's adapter reads: {board, banner, before, missing}. `missing` is a programme's page for a slug this viewer may not see
    (the view then answers 404, exactly as for a slug that never existed)."""
    from flask import request

    data, _ = _build()
    state = _state()
    slug = state.get("slug")
    programme = find_programme(data, slug) if slug is not None else None
    missing = slug is not None and programme is None
    state["missing"] = missing
    view = board_view(data, request.args.get("ch"), programme)
    return {"board": view, "banner": _phase_banner(data["phase"]), "before": data["phase"]["state"] == "before", "missing": missing, "pn": None}


def _lines(who):
    """The viewer's news, oldest first: a player's own list (l3mon_board/news.py); the crew read CTFd's own, whole."""
    from CTFd.models import Notifications, db
    from CTFd.plugins.l3mon_board import news

    if not who.admin:
        return news.lines_for(who)
    rows = db.session.query(Notifications.id, Notifications.title, Notifications.content).order_by(Notifications.id).all()
    return [{"id": int(i), "title": str(t or ""), "content": str(c or "")} for i, t, c in rows]


@value("notifications_page", default=None)
def notifications_page():
    """What the notifications adapter reads: {lines, banner}. The board's data is not built for this page."""
    from CTFd.plugins.l3mon_board import viewer
    from CTFd.plugins.l3mon_core.clock import current_phase

    phase = current_phase()
    return {"lines": notice_lines(_lines(viewer.current())), "banner": _phase_banner({"state": phase.state, "frozen": phase.frozen})}


CRAWL_LINES = 8  # the news crawl shows the newest public notifications


def crawl_text(lines):
    """The crawl's lines from public notifications (oldest first, `[{title, content}]`): the newest first, each "title: text" (or the title alone)."""
    out = []
    for n in notice_lines(lines)[:CRAWL_LINES]:
        out.append(f"{n['title']}: {n['content']}" if n["content"] else n["title"])
    return out


@value("crawl", default=())
def crawl():
    """The news crawl of the board: the newest public notifications (never a studio's private line). Empty when there are none."""
    from CTFd.models import Notifications, db

    rows = (
        db.session.query(Notifications.id, Notifications.title, Notifications.content)
        .filter(Notifications.team_id.is_(None), Notifications.user_id.is_(None))
        .order_by(Notifications.id.desc())
        .limit(CRAWL_LINES)
        .all()
    )
    return crawl_text([{"id": i, "title": t, "content": c} for i, t, c in reversed(rows)])


@value("start_text", default="")
def start_text():
    """The start of the event as the slate says it ("10:00 IST on 28 November 2026"); empty when CTFd has no start time."""
    from CTFd.plugins.l3mon_core.clock import to_ist
    from CTFd.utils import get_config

    raw = get_config("start")
    if not raw:
        return ""
    moment = to_ist(int(raw))
    return f"{moment:%H:%M} IST on {moment.day} {MONTHS[moment.month - 1]} {moment.year}"  # English month names whatever the server's locale


def _stock_body():
    """The body of CTFd's own /challenges view, under none of its decorators (functools.wraps keeps the chain in __wrapped__)."""
    from CTFd.challenges import listing

    body = listing
    while hasattr(body, "__wrapped__"):
        body = body.__wrapped__
    return body


def _guarded(view):
    """`view` behind the decorators of CTFd's /challenges, in its order, so a visitor meets the gate CTFd gives."""
    from CTFd.utils.decorators import during_ctf_time_only, require_complete_profile, require_verified_emails
    from CTFd.utils.decorators.visibility import check_challenge_visibility

    return require_complete_profile(during_ctf_time_only(require_verified_emails(check_challenge_visibility(view))))


def _before_start_view(slug=None):
    """Before the start, a signed-in player with a studio (or the crew) sees the board's own "not on air yet" page, not CTFd's 403: the same
    gates as the listing except the one that closes the event to players (`during_ctf_time_only`)."""
    from flask import redirect, render_template, request, url_for

    from CTFd.utils.config import is_teams_mode
    from CTFd.utils.user import get_current_team, is_admin

    if is_teams_mode() and get_current_team() is None and not is_admin():
        return redirect(url_for("teams.private", next=request.full_path))
    return render_template("challenges.html")


def _board_view(slug=None):
    """CTFd's own listing (its team redirect, its notes), drawn by the board adapter. For a programme's page the slug is read by the value; a slug
    the viewer may not see answers 404 once CTFd's own gates have let the request through."""
    from flask import abort, render_template

    from CTFd.utils.user import is_admin

    state = _state()
    if slug is not None:
        state["slug"] = slug
    # The crew have no studio, and CTFd's own view sends an account without one to make a studio. The board shows the crew what a player sees.
    page = render_template("challenges.html") if is_admin() else _stock_body()()
    if slug is not None and state.get("missing"):
        abort(404)
    return page


def _serve_notifications():
    """Before the guard of l3mon_board/notices.py: the notifications page for a player who may read the news."""
    from flask import abort, redirect, render_template, request, url_for

    if request.path != NOTIFICATIONS or request.method not in ("GET", "HEAD"):
        return None
    from CTFd.utils import get_config

    if get_config("ctf_theme") != "l3mon":
        return None  # under any other theme there is no page of ours: CTFd's stock page would list every studio's lines, so the board plugin's guard keeps it closed
    from CTFd.plugins.l3mon_board import viewer

    no = viewer.refusal()
    if no is not None:
        code = (no.get_json(silent=True) or {}).get("error")
        if code == "auth_required":
            return redirect(url_for("auth.login", next=request.full_path))
        if code == "unverified":
            return redirect(url_for("auth.confirm"))
        if code == "no_team":
            return redirect(url_for("teams.private", next=request.full_path))
        abort(403)
    return render_template("notifications.html")


def install(app):
    from CTFd.plugins.l3mon_board import notices

    existing = {rule.rule for rule in app.url_map.iter_rules()}
    guarded = _guarded(_board_view)
    from CTFd.utils.decorators import require_complete_profile, require_verified_emails
    from CTFd.utils.decorators.visibility import check_challenge_visibility

    early = require_complete_profile(require_verified_emails(check_challenge_visibility(_before_start_view)))

    def dispatch(slug=None):
        from CTFd.plugins.l3mon_core.clock import current_phase

        return (early if current_phase().state == "before" else guarded)(slug=slug)

    guarded_or_early = dispatch
    if "/board" not in existing:
        app.add_url_rule("/board", endpoint="l3mon_theme_board", view_func=guarded_or_early, methods=["GET"])
    if "/board/<slug>" not in existing:
        app.add_url_rule("/board/<slug>", endpoint="l3mon_theme_programme", view_func=guarded_or_early, methods=["GET"])
    if "l3mon_theme_notifications" not in app.extensions:
        app.extensions["l3mon_theme_notifications"] = True
        hooks = app.before_request_funcs.setdefault(None, [])
        at = hooks.index(notices.guard) if notices.guard in hooks else len(hooks)
        hooks.insert(at, _serve_notifications)  # just before the guard that closes the stock page, so CTFd's own request hooks still run first
