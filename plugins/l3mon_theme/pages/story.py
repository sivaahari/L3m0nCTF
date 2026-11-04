"""The Stories page (SP6 Stream E milestone 2): `/stories` route and view-model builder.

Renders the theme adapter `stories.html`. Gated for signed-in players with a studio,
and the crew (CTFd administrators). Suspended users get 403 (the OFF AIR slate).
"""
from flask import abort, redirect, render_template, request, url_for

from CTFd.plugins.l3mon_board import banner as board_banner
from CTFd.plugins.l3mon_board import viewer
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.models import Channel
from CTFd.plugins.l3mon_story import api as story_api
from CTFd.plugins.l3mon_story import finale


def build_stories_view(stories_dict: dict) -> dict:
    """Build the template context for stories.html (docs/contract-e-story-and-limits.md)."""
    channels = Channel.query.all()
    chan_by_slug = {c.slug: c for c in channels}

    cards = []
    # Sort: channel stories in order of channel position (or slug), finale at the very end
    items = sorted(
        stories_dict.items(),
        key=lambda pair: (
            pair[0] == finale.SLUG,
            chan_by_slug.get(pair[0]).position if chan_by_slug.get(pair[0]) else 999,
            pair[0],
        ),
    )

    for slug, s in items:
        if slug == finale.SLUG:
            cards.append({
                "slug": slug,
                "channelId": "99",
                "badge": "FINALE",
                "title": s.title,
                "kicker": s.kicker,
                "panels": str(s.panels),
                "cls": "is-finale",
                "btnCls": "btn-main",
                "actionText": "Watch the finale",
            })
        else:
            c = chan_by_slug.get(slug)
            pos = c.position if c else 0
            cards.append({
                "slug": slug,
                "channelId": str(pos),
                "badge": f"CH {pos:02d}",
                "title": s.title,
                "kicker": s.kicker,
                "panels": str(s.panels),
                "cls": "",
                "btnCls": "btn-ghost",
                "actionText": "Watch the cold open",
            })

    return {
        "empty": len(cards) == 0,
        "cards": cards,
    }


def stories_page():
    """Route handler for `/stories`."""
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
    mine = story_api.available(v)
    stories_model = build_stories_view(mine)
    phase_banner = board_banner.for_phase(current_phase())

    return render_template("stories.html", stories=stories_model, banner=phase_banner)


def install(app):
    app.add_url_rule("/stories", endpoint="l3mon_theme_stories", view_func=stories_page, methods=["GET", "HEAD"])
