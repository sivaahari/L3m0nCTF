"""The Guide page (SP6 Stream C): `/guide` route and view-model builder.

Renders the theme adapter `guide.html`. Gated for signed-in players with a studio,
and the crew (CTFd administrators). Suspended users get 403 (the OFF AIR slate).
"""
from CTFd.plugins.l3mon_board.figures import js_round


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st."""
    k = n % 100
    if 11 <= k <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def channel_keys(channels):
    """{database id: the number the page knows the channel by}: `no`, or the database ids when `no` cannot be told apart.

    The lead replaces this helper by A's after merge.
    """
    def _get_num(c):
        if isinstance(c, dict):
            return c.get("no", c.get("position"))
        return getattr(c, "no", getattr(c, "position", None))

    def _get_id(c):
        if isinstance(c, dict):
            return c.get("id")
        return getattr(c, "id", None)

    numbers = [_get_num(c) for c in channels]
    usable = all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 99 for n in numbers) and len(set(numbers)) == len(numbers)
    return {_get_id(c): (_get_num(c) if usable else _get_id(c)) for c in channels}


def build_guide_view(data: dict, me: dict | None, epg_html: str = "", channel_keys_map: dict | None = None) -> dict:
    """The flat template context for the Guide (docs/template-contract.md, section 4)."""
    t = data.get("team")
    phase = data.get("phase", {})
    frozen = bool(phase.get("frozen"))

    if not t:
        place_text = ""
    elif t.get("place") is None:
        place_text = "Ranks frozen" if frozen else "Not ranked yet"
    else:
        place_text = f"{ordinal(t['place'])} of {t['of']}"

    s = data.get("story")
    has_epg = bool(data.get("epg_sig") and data.get("epg_sig") != "none" and phase.get("state") != "before")

    studio_name = me.get("studio") if me and me.get("studio") else "Your studio"

    team_dict = None
    if t is not None:
        team_dict = {
            "score": str(t.get("score", 0)),
            "place": place_text,
            "frozen": "true" if frozen else "",
            "solves": str(t.get("solves", 0)),
            "hints": str(t.get("hints_used", 0)),
            "live": str(t.get("instances_live", 0)),
        }

    bonus_val = t.get("bonus", 0) if t else 0
    has_bonus = bool(t and bonus_val != 0)
    bonus_str = (f"+{bonus_val}" if bonus_val > 0 else str(bonus_val)) if t else ""

    notes = t.get("notes", []) if t else []
    members = t.get("members", []) if t else []
    by_channel = t.get("by_channel", []) if t else []

    keys = channel_keys_map
    if keys is None:
        if "channels" in data:
            keys = channel_keys(data["channels"])
        else:
            keys = {}

    chans = []
    for c in by_channel:
        cid = c["channel"]
        k = keys.get(cid, cid)
        solved = c.get("solved", 0)
        total = c.get("total", 0)
        pct = js_round(solved / total * 100) if total > 0 else 0
        chans.append({
            "id": str(k),
            "no": f"{k:02d}",
            "name": c["name"],
            "solved": str(solved),
            "total": str(total),
            "pct": str(pct),
            "text": f"{solved} of {total}",
        })

    formatted_members = []
    for m in members:
        tags = []
        if m.get("captain"):
            tags.append("captain")
        if m.get("you"):
            tags.append("you")
        tag_str = " · ".join(tags)
        cls_str = "is-you" if m.get("you") else ""
        name_str = m.get("name", "")
        ini_str = name_str[0].upper() if name_str else "?"
        solves = m.get("solves", 0)
        formatted_members.append({
            "id": str(m["id"]),
            "name": name_str,
            "ini": ini_str,
            "tag": tag_str,
            "cls": cls_str,
            "solves": str(solves),
            "trp": str(m.get("trp", 0)),
            "pct": str(m.get("pct", 0)),
            "text": f"{solves} {'solve' if solves == 1 else 'solves'}",
        })

    story_dict = None
    if s is not None:
        reels = s.get("reels", 0)
        needed = s.get("reels_needed", 0)
        pct_story = js_round(reels / needed * 100) if needed > 0 else 0
        story_dict = {
            "reels": str(reels),
            "needed": str(needed),
            "pct": str(pct_story),
            "text": f"{reels} of {needed}",
            "valuetext": f"{reels} of {needed} reels",
            "air": str(js_round(s.get("on_air", 0) * 100)),
        }

    return {
        "ver": str(data.get("ver", "")),
        "hasEpg": has_epg,
        "epg": epg_html,
        "epgSig": data.get("epg_sig", ""),
        "hasTeam": t is not None,
        "studio": studio_name,
        "team": team_dict,
        "hasBonus": has_bonus,
        "bonus": bonus_str,
        "hasNotes": bool(notes),
        "notes": [{"title": n["title"], "content": n["content"]} for n in notes],
        "hasMembers": bool(formatted_members),
        "members": formatted_members,
        "chans": chans,
        "hasStory": s is not None,
        "story": story_dict,
    }


def guide_page():
    """Route handler for `/guide`."""
    from flask import abort, redirect, render_template, request, url_for

    from CTFd.plugins.l3mon_board import epg, plan, viewer
    from CTFd.plugins.l3mon_board import guide as board_guide
    from CTFd.plugins.l3mon_core.clock import current_phase

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

    v = viewer.current()
    data, _ = board_guide.build(v)
    phase = current_phase()
    view_plan = plan.read(v, phase)
    keys = channel_keys(view_plan.channels)
    grid_model = epg.model(view_plan, v)
    epg_markup = epg.html(grid_model)

    user = v.user
    studio_name = v.team.name if v.team else user.name
    me_info = {"name": user.name, "studio": studio_name}
    guide_ctx = build_guide_view(data, me_info, epg_markup, channel_keys_map=keys)

    return render_template("guide.html", guide=guide_ctx, banner=data.get("banner"))


def install(app):
    app.add_url_rule("/guide", endpoint="l3mon_theme_guide", view_func=guide_page, methods=["GET", "HEAD"])
