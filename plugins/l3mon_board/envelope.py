"""The answer shapes of the player endpoints (the contract, section 1) and the one gate in front of them.

A success is `{"success": true, "data": ...}`. An error is `{"success": false, "error": "<code>", "message": "<one plain sentence>",
["retry_after"], ["errors"], ["phase"], "request_id": "XXXX-XXXX", ...extra}` with the keys in that order, so two answers can be
compared. Every answer carries `X-Request-Id` equal to the body's `request_id`: a player can quote it and the log line has it.
Nothing is cached unless the caller gives an ETag (the board), and then only privately: the answer is about one studio.
"""
import base64
import hashlib
import json
import secrets

from flask import Response, g, request

REQUEST_ID_KEY = "l3mon_request_id"
_ORDER = ("retry_after", "errors", "phase")


def request_id() -> str:
    """The id of this request (`3FA9-0B1C`), made once and kept for the whole of it."""
    found = g.get(REQUEST_ID_KEY)
    if found is None:
        raw = secrets.token_hex(4).upper()
        found = f"{raw[:4]}-{raw[4:]}"
        setattr(g, REQUEST_ID_KEY, found)
    return found


def _response(body, status, headers=None) -> Response:
    response = Response(json.dumps(body, separators=(",", ":")), status=status, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Request-Id"] = request_id()
    response.headers["Vary"] = "Cookie"
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def ok(data, etag=None, status=200) -> Response:
    """A success. With an ETag the answer may be kept by the browser and checked again (`private, no-cache`), never by a shared cache."""
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"} if etag else None
    return _response({"success": True, "data": data}, status, headers)


def ok_bytes(body: bytes, etag) -> Response:
    """A success whose JSON body was made (and checked) earlier and is kept as bytes: the cold-open stories. Always privately cached."""
    response = Response(body, status=200, mimetype="application/json")
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Request-Id"] = request_id()
    response.headers["Vary"] = "Cookie"
    return response


def fragment(markup: str, etag) -> Response:
    """A piece of a page (the programme grid, the scoreboard's rows): HTML with a strong ETag, kept only privately."""
    response = Response(markup, status=200, mimetype="text/html")
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Request-Id"] = request_id()
    response.headers["Vary"] = "Cookie"
    return response


def strong_etag(prefix: str, content) -> str:
    """`"<prefix><22 characters>"`: a hash of the JSON of `content` with its keys in order, so the same content is always the same tag.
    The prefix tells the answers apart (g the Guide, s the scoreboard, e the grid, r the rows)."""
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).digest()
    return '"' + prefix + base64.urlsafe_b64encode(digest).decode().rstrip("=")[:22] + '"'


def not_modified(etag) -> Response:
    response = Response(b"", status=304)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Request-Id"] = request_id()
    response.headers["Vary"] = "Cookie"
    return response


def fail(code, status, message, **extra) -> Response:
    """An error. `retry_after`, `errors` and `phase` come first, in that order; anything else the caller adds follows the request id."""
    body = {"success": False, "error": code, "message": message}
    for key in _ORDER:
        if key in extra:
            body[key] = extra.pop(key)
    body["request_id"] = request_id()
    body.update(extra)
    headers = {"Retry-After": str(int(body["retry_after"]))} if "retry_after" in body else None
    return _response(body, status, headers)


def etag_matches(header, etag) -> bool:
    """True when an If-None-Match header names this ETag: a list, weak forms (W/"...") and `*` included."""
    if not header:
        return False
    for part in header.split(","):
        part = part.strip()
        if part.startswith("W/"):
            part = part[2:]
        if part == etag or part == "*":
            return True
    return False


def wants(etag) -> bool:
    """True when this request already holds the answer with this ETag."""
    return etag_matches(request.headers.get("If-None-Match"), etag)
