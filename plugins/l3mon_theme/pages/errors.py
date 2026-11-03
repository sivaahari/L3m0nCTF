"""The rate-limit page for the l3mon theme (SP6 / Stream E).

CTFd limits how often some page routes can be used (sign-in, registration, password reset,
the settings forms, joining a team) with its `ratelimit` decorator. When the limit is hit it answers
JSON ({"code": 429, "message": "Too many requests. Limit is 10 requests in 60 seconds"} with status 429)
even on a route that serves an HTML page, so a player who submits a form too often sees raw text.

This module installs an after_request hook, active only when the l3mon theme is enabled, that turns
CTFd's 429 JSON response into the themed HTML slate (`errors/429.html`) when the request is a page request,
and leaves every other 429 (API calls, AJAX fetches, admin endpoints, other themes) untouched.
"""
import re
from flask import make_response, render_template, request

EXCLUDED_PREFIXES = ("/api/", "/events", "/themes/", "/files/", "/story/", "/admin/")


def _is_page_request(req, response) -> bool:
    """Return True if this request is a browser page request that met a 429 JSON response."""
    if req.method not in ("GET", "POST"):
        return False

    path = req.path or "/"
    if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False

    # Check if client asked for JSON
    # 1. An Accept header that prefers application/json over text/html
    accept = req.accept_mimetypes
    if accept and accept.get("application/json", 0) > accept.get("text/html", 0):
        return False

    # 2. X-Requested-With: XMLHttpRequest (standard AJAX header)
    if req.headers.get("X-Requested-With") == "XMLHttpRequest":
        return False

    # 3. A JSON Content-Type body
    content_type = req.content_type or ""
    if "application/json" in content_type:
        return False

    # The answer really is CTFd's JSON 429
    if not getattr(response, "is_json", False):
        return False

    payload = response.get_json(silent=True)
    if not isinstance(payload, dict):
        return False

    if (
        payload.get("code") == 429
        or "Too many requests" in payload.get("message", "")
        or payload.get("status") == "ratelimited"
    ):
        return True

    return False


def _extract_retry_after(message: str, endpoint: str) -> int:
    """Read the real remaining seconds from the cache if available, or parse the window from the message."""
    try:
        from CTFd.cache import cache
        from CTFd.utils.user import get_ip

        ip = get_ip()
        if ip and endpoint:
            key = f"rl:{ip}:{endpoint}"
            backend = getattr(cache, "cache", None)
            client = getattr(backend, "_read_client", None) or getattr(backend, "_write_client", None)
            if client and hasattr(client, "ttl"):
                ttl = client.ttl(key)
                if isinstance(ttl, int) and ttl > 0:
                    return ttl
    except Exception:
        pass

    m = re.search(r"\b(\d+)\s+seconds?", message or "")
    if m:
        try:
            return max(1, int(m.group(1)))
        except (ValueError, TypeError):
            pass
    return 60


def install(app):
    """Install the after_request hook on the CTFd application."""

    @app.after_request
    def intercept_ratelimit(response):
        try:
            # First check: fast bailout on non-429 to incur zero cost
            if response.status_code != 429:
                return response

            from CTFd.utils import get_config

            if get_config("ctf_theme") != "l3mon":
                return response

            if not _is_page_request(request, response):
                return response

            payload = response.get_json(silent=True) or {}
            message = payload.get("message", "")
            seconds = _extract_retry_after(message, request.endpoint)

            html = render_template("errors/429.html", retry_after=seconds)
            new_response = make_response(html, 429)
            new_response.headers["Content-Type"] = "text/html; charset=utf-8"
            new_response.headers["Retry-After"] = str(seconds)
            new_response.headers["Cache-Control"] = "no-store"
            return new_response
        except Exception:
            # On any problem, return CTFd's own response untouched
            return response
