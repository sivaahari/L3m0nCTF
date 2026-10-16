"""The cold-open comic's endpoints (read only):

    GET /api/v1/l3mon/story          the stories this account may open now: slug, title, kicker, number of panels
    GET /api/v1/l3mon/story/<slug>   one channel's story (the checked bundle); strong ETag, 304 on a match
    GET /api/v1/l3mon/story/<slug>/pic/<id>   one painted picture of a picture story (a WebP), behind the very same gate
    GET /story/<slug>                the page that plays it (a visitor is sent to the registration page)

Who: a signed-in account that is not suspended and, when the settings ask, has a verified email. It does not need a studio yet.
When: the channel has something on air for this account (the board's own rule) and the broadcast has started. The crew can open any
story at any time to preview it. A channel that is not on air, a channel with no story and a name that is not a channel at all
all give the same 404. A visitor gets 401 for every name, existing or not, so a visitor learns nothing. The one story that is not a
channel's, `finale`, opens for every registered player when the on-air meter is full or the crew says so (finale.py).
"""
import logging
import re

from flask import Blueprint, Response, abort, redirect, render_template

from CTFd.models import db
from CTFd.plugins import register_plugin_assets_directory
from CTFd.plugins.l3mon_board import envelope, viewer
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids
from CTFd.plugins.l3mon_story import finale, store
from CTFd.utils.user import authed

bp = Blueprint("l3mon_story", __name__, url_prefix="/api/v1/l3mon")
page_bp = Blueprint("l3mon_story_page", __name__, template_folder="templates")
_log = logging.getLogger("l3mon")
NOT_FOUND = ("not_found", 404, "That story is not on air.")


def on_air_channels(solved) -> set:
    """The slugs of the channels that have a programme this account can see now (the board's rule), or none before the start."""
    if current_phase().state == "before":
        return set()
    shown = visible_challenge_ids(solved_ids=solved)
    if not shown:
        return set()
    rows = (
        db.session.query(Channel.slug).join(Programme, Programme.channel_id == Channel.id).filter(Programme.challenge_id.in_(shown)).distinct().all()
    )
    return {slug for (slug,) in rows}


def available(who) -> dict:
    """slug -> Story for the stories this account may open now."""
    stories = store.all_stories()
    if who.admin:
        return stories
    on_air = on_air_channels(who.solved)
    mine = {slug: story for slug, story in stories.items() if slug in on_air and slug != finale.SLUG}  # the finale has its own rule, never a channel's
    if finale.SLUG in stories and finale.is_open(who):
        mine[finale.SLUG] = stories[finale.SLUG]
    return mine


def story_for(who, slug):
    """The Story this account may open now under this name, or None (the same None for every reason, so nothing can be learned)."""
    story = store.get(slug)  # a name that is not a slug never reaches the dictionary
    if story is None:
        return None
    if who.admin:
        return story
    if slug == finale.SLUG:
        return story if finale.is_open(who) else None
    return story if slug in on_air_channels(who.solved) else None  # (the finale was answered above)


def _refused():
    return viewer.refusal(need_team=False)


@bp.route("/story", methods=["GET"])
def list_stories():
    no = _refused()
    if no is not None:
        return no
    mine = available(viewer.current())
    data = [{"slug": s.slug, "title": s.title, "kicker": s.kicker, "panels": s.panels} for s in sorted(mine.values(), key=lambda s: s.slug)]
    return envelope.ok({"stories": data})


@bp.route("/story/<slug>", methods=["GET"])
def get_story(slug):
    no = _refused()
    if no is not None:
        return no
    story = story_for(viewer.current(), slug)
    if story is None:
        return envelope.fail(*NOT_FOUND)
    if envelope.wants(story.etag):
        return envelope.not_modified(story.etag)
    return envelope.ok_bytes(story.body, story.etag)


@bp.route("/story/<slug>/pic/<name>", methods=["GET"])
def get_picture(slug, name):
    """One painted picture. The gate is the story's own: whatever makes the story unavailable (not on air, not started, unknown name, a
    picture the story does not name, a file that is not what the bundle says) gives the same 404."""
    no = _refused()
    if no is not None:
        return no
    if story_for(viewer.current(), slug) is None:
        return envelope.fail(*NOT_FOUND)
    found = store.picture(slug, name)
    if found is None:
        return envelope.fail(*NOT_FOUND)
    raw, etag = found
    if envelope.wants(etag):
        response = Response(b"", status=304)
    else:
        response = Response(raw, status=200, mimetype="image/webp")
        response.headers["Content-Disposition"] = f'inline; filename="{name}.webp"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["Vary"] = "Cookie"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@bp.after_request
def banned_page_to_json(response):
    """CTFd's own check refuses a suspended account with a page before any route runs; here it is the contract's JSON."""
    if response.status_code == 403 and response.mimetype == "text/html":
        return envelope.fail("banned", 403, "This account has been suspended.")
    return response


@bp.errorhandler(Exception)
def unexpected(error):
    from werkzeug.exceptions import HTTPException

    if isinstance(error, HTTPException):
        return error
    _log.exception("l3mon: the story failed (request %s)", envelope.request_id())
    return envelope.fail("server_error", 500, "Something broke on our side.")


@page_bp.route("/story/<slug>", methods=["GET"])
def story_page(slug):
    """The page that plays a story. Its words are also on the page as text, for anyone who cannot or will not run the player."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug):
        abort(404)
    if not authed():
        return redirect(f"/register?next=/story/{slug}", code=303)  # the slug is checked above: nothing a visitor typed is put in the address
    if _refused() is not None:
        abort(403)
    story = story_for(viewer.current(), slug)
    if story is None:
        abort(404)
    response = render_template("l3mon_story/story.html", story=story)
    return response, 200, {"Cache-Control": "private, no-cache", "Vary": "Cookie"}


def install_assets(app):
    register_plugin_assets_directory(app, base_path="/plugins/l3mon_story/assets/")
