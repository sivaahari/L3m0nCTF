"""The player endpoints of the board (SP3 part 3.4), read only:

    GET /api/v1/l3mon/board   the channels, the programmes on air and the studio's own numbers; strong ETag, 304 on a match
    GET /api/v1/l3mon/ticks   {ver, notif_ver, own}: what the pages poll every 15 seconds

Both need a signed-in account (viewer.player). Nothing here writes.
"""
import logging

from flask import Blueprint

from CTFd.plugins.l3mon_board import board, envelope, ticks, viewer

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
    return envelope.ok(ticks.answer(viewer.current().team))


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
