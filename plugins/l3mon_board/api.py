"""The player endpoints of the board (SP3 parts 3.4 and 3.5), read only:

    GET /api/v1/l3mon/board             the channels, the programmes on air and the studio's own numbers; strong ETag, 304 on a match
    GET /api/v1/l3mon/ticks             {ver, notif_ver, own}: what the pages poll every 15 seconds
    GET /api/v1/l3mon/guide             the studio's numbers, its members, the channel progress, the story meter; strong ETag, 304
    GET /api/v1/l3mon/guide/epg         the programme grid as markup; ETag "e<epg_sig>", 304
    GET /api/v1/l3mon/scoreboard        "TRP ratings": phase, banner, how many are ranked, the studio's own line; strong ETag, 304
    GET /api/v1/l3mon/scoreboard/rows   the first 100 rows as markup; ETag "r<rows_sig>", 304

All need a signed-in account (viewer.player). Nothing here writes. The bell (`GET /api/v1/notifications?since_id=`) is CTFd's own route,
answered before CTFd's view runs (notices.py).
"""
import logging

from flask import Blueprint

from CTFd.plugins.l3mon_board import board, envelope, epg, guide, plan, scoreboard, ticks, viewer
from CTFd.plugins.l3mon_core.clock import current_phase

bp = Blueprint("l3mon_board", __name__, url_prefix="/api/v1/l3mon")
_log = logging.getLogger("l3mon")


@bp.route("/board", methods=["GET"])
@viewer.player
def get_board():
    data, etag = board.build(viewer.current())
    if envelope.wants(etag):
        return envelope.not_modified(etag)
    return envelope.ok(data, etag=etag)


@bp.route("/ticks", methods=["GET"])
@viewer.player
def get_ticks():
    return envelope.ok(ticks.answer(viewer.current()))


@bp.route("/guide", methods=["GET"])
@viewer.player
def get_guide():
    data, etag = guide.build(viewer.current())
    if envelope.wants(etag):
        return envelope.not_modified(etag)
    return envelope.ok(data, etag=etag)


@bp.route("/guide/epg", methods=["GET"])
@viewer.player
def get_guide_epg():
    asker = viewer.current()
    grid = epg.model(plan.read(asker, current_phase()), asker)
    etag = f'"e{epg.signature(grid)}"'
    if envelope.wants(etag):
        return envelope.not_modified(etag)
    return envelope.fragment(epg.html(grid), etag)


@bp.route("/scoreboard", methods=["GET"])
@viewer.player
def get_scoreboard():
    data, etag = scoreboard.answer(scoreboard.read(viewer.current_team()))
    if envelope.wants(etag):
        return envelope.not_modified(etag)
    return envelope.ok(data, etag=etag)


@bp.route("/scoreboard/rows", methods=["GET"])
@viewer.player
def get_scoreboard_rows():
    view = scoreboard.read(viewer.current_team())
    etag = scoreboard.rows_etag(view)
    if envelope.wants(etag):
        return envelope.not_modified(etag)
    return envelope.fragment(scoreboard.html(view), etag)


@bp.after_request
def banned_page_to_json(response):
    """CTFd's own check refuses a suspended account (or a suspended studio) with a page before any route runs; here it is the contract's JSON."""
    if response.status_code == 403 and response.mimetype == "text/html":
        return envelope.fail("banned", 403, "This account has been suspended.")
    return response


@bp.errorhandler(Exception)
def unexpected(error):
    """A bug is a plain 500 with the request id; the traceback goes to the log with the same id, never to the player."""
    from werkzeug.exceptions import HTTPException

    if isinstance(error, HTTPException):
        return error
    _log.exception("l3mon: the board failed (request %s)", envelope.request_id())
    return envelope.fail("server_error", 500, "Something broke on our side.")
