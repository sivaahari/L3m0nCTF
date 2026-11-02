"""The Scoreboard page (SP6 Stream C): view-model builder and theme values.

Renders the theme adapter `scoreboard.html`. Gated for signed-in players with a studio,
and the crew (CTFd administrators). Suspended users get 403 (the OFF AIR slate).
"""
from CTFd.plugins.l3mon_board.figures import js_round
from CTFd.plugins.l3mon_theme.registry import value


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st."""
    k = n % 100
    if 11 <= k <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def build_scoreboard_view(data: dict, rows_html: str = "") -> dict:
    """The flat template context for the Scoreboard (docs/contract-c-guide-scoreboard.md, section 2)."""
    me = data.get("me")
    phase = data.get("phase", {})
    frozen = bool(phase.get("frozen"))

    place_text = ""
    if me:
        pos = me.get("pos")
        if pos is None:
            place_text = "Ranks frozen" if frozen else "Not ranked yet"
        else:
            place_text = f"{ordinal(pos)} of {me.get('of', 0)}"

    total = data.get("total", 0)
    top = 100

    return {
        "ver": str(data.get("ver", 0)),
        "top": str(top),
        "total": str(total),
        "hasRows": total > 0,
        "rows": rows_html,
        "sig": data.get("rows_sig", ""),
        "hasMe": bool(me),
        "me": {
            "name": me.get("name", ""),
            "score": str(me.get("score", 0)),
            "place": place_text,
            "frozen": "true" if frozen else "",
        } if me else None,
        "more": f"Showing the first {top} of {total} studios." if total > top else "",
    }


@value("scoreboard")
def scoreboard_value():
    """Dynamic theme value {{ l3mon.scoreboard }}."""
    from CTFd.plugins.l3mon_board import scoreboard, viewer

    team = viewer.current_team()
    view = scoreboard.read(team)
    data, _ = scoreboard.answer(view)
    rows_markup = scoreboard.html(view)
    return build_scoreboard_view(data, rows_markup)


@value("scoreboard_banner")
def scoreboard_banner_value():
    """Dynamic theme value {{ l3mon.scoreboard_banner }}."""
    from CTFd.plugins.l3mon_board import banner
    from CTFd.plugins.l3mon_core.clock import current_phase

    phase = current_phase()
    return banner.for_phase(phase)


def _guard_scoreboard():
    """Gate /scoreboard for signed-in players with a studio (or the crew), matching Guide and Stream A's /notifications."""
    from flask import abort, redirect, request, url_for

    if request.path.rstrip("/") != "/scoreboard" or request.method not in ("GET", "HEAD"):
        return None

    from CTFd.utils import get_config

    if get_config("ctf_theme") != "l3mon":
        return None

    from CTFd.plugins.l3mon_board import viewer

    no = viewer.refusal()
    if no is not None:
        code = (no.get_json(silent=True) or {}).get("error")
        if code == "auth_required":
            next_url = request.full_path.rstrip("?") if not request.query_string else request.full_path
            return redirect(url_for("auth.login", next=next_url))
        if code == "unverified":
            return redirect(url_for("auth.confirm"))
        if code == "no_team":
            return redirect(url_for("teams.private"))
        abort(403)
    return None


def install(app):
    if "l3mon_theme_scoreboard_gate" not in app.extensions:
        app.extensions["l3mon_theme_scoreboard_gate"] = True
        app.before_request(_guard_scoreboard)
