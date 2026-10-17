"""l3mon_ops: operations and resilience endpoints.

Provides:
- GET /readyz: 200 {"status": "ready"} if database answers SELECT 1 and Redis answers PING within 1s.
               503 {"status": "not_ready"} on any failure or timeout. No sensitive details leaked.
- GET /l3mon/metrics: Prometheus format metrics (cached 15s) for internal monitoring network only.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import time

from flask import Blueprint, Response, current_app
from sqlalchemy import text

from CTFd.plugins.l3mon_core.versions import check_ctfd_version

ops = Blueprint("l3mon_ops", __name__)

_METRICS_CACHE_SECONDS = 15.0
_last_metrics_time: float = 0.0
_last_metrics_body: str = ""


import socket

# The checks run in a small pool of threads so that the one-second budget holds whether or not the worker is patched by gevent (under gunicorn's
# gevent worker the threads are greenlets). A check that hangs keeps its thread until its own socket gives up; the pool is bounded, and a probe that
# cannot get a thread in time simply answers "not ready".
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="l3mon-readyz")


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


def _port(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _check_db_quick() -> bool:
    """SELECT 1 through the platform's own database engine. When the platform's own settings name a database host (DATABASE_HOST, as CTFd reads it)
    a connection to that host is tried first, so a database that is simply down is answered at once instead of after the driver's own timeout."""
    host = os.environ.get("DATABASE_HOST", "").strip()
    if host and not _reachable(host, _port("DATABASE_PORT", 3306)):
        return False

    from CTFd.models import db

    try:
        with db.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar() == 1
    except Exception:
        return False


def _check_redis_quick() -> bool:
    """PING the platform's own cache client (CTFd's `REDIS_HOST` names the host: tried first, as above); on a cache that is not Redis (the tests'),
    write and read one key."""
    host = os.environ.get("REDIS_HOST", "").strip()
    if host and not _reachable(host, _port("REDIS_PORT", 6379)):
        return False

    from CTFd.cache import cache

    try:
        backend = getattr(cache, "cache", None)
        client = getattr(backend, "_read_client", None) or getattr(backend, "_write_client", None) or getattr(backend, "_client", None)
        if client is not None and hasattr(client, "ping"):
            return bool(client.ping())
        key = "_l3mon_readyz"
        cache.set(key, 1, timeout=5)
        return cache.get(key) == 1
    except Exception:
        return False


def _run_with_timeout(fn, timeout_seconds: float = 1.0) -> bool:
    """Run a check in a pool thread, inside the app's context, and give up after the budget."""
    app = current_app._get_current_object()

    def in_context():
        with app.app_context():
            return bool(fn())

    try:
        return _POOL.submit(in_context).result(timeout=timeout_seconds)
    except Exception:  # includes the timeout
        return False


@ops.route("/readyz")
def readyz():
    db_ok = _run_with_timeout(_check_db_quick, 1.0)
    redis_ok = _run_with_timeout(_check_redis_quick, 1.0)

    if db_ok and redis_ok:
        body = json.dumps({"status": "ready"})
        status = 200
    else:
        body = json.dumps({"status": "not_ready"})
        status = 503

    resp = Response(body, status=status, mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _render_metrics() -> str:
    """Compute and render prometheus text format metrics."""
    from CTFd.models import Fails, Solves, Users
    from CTFd.plugins.l3mon_core.clock import current_phase, now
    from CTFd.plugins.l3mon_core.tick import tick

    # 1. Event phase
    try:
        phase = current_phase().state
    except Exception:
        phase = "unknown"

    phase_lines = []
    for p in ("before", "live", "paused", "ended"):  # the names of l3mon_core.clock.Phase.state
        val = 1 if phase == p else 0
        phase_lines.append(f'l3mon_event_phase{{phase="{p}"}} {val}')

    # 2. Tick value
    try:
        tick_val = tick.value()
    except Exception:
        tick_val = 0

    # 3. Solves, wrong attempts, registrations in last minute
    t_now = now()
    one_min_ago = datetime.datetime.utcfromtimestamp(t_now - 60.0)
    try:
        solves_last_min = Solves.query.filter(Solves.date >= one_min_ago).count()
    except Exception:
        solves_last_min = 0

    try:
        fails_last_min = Fails.query.filter(Fails.date >= one_min_ago).count()
    except Exception:
        fails_last_min = 0

    try:
        regs_last_min = Users.query.filter(Users.created >= one_min_ago).count()
    except Exception:
        regs_last_min = 0

    build_version = os.environ.get("L3MON_BUILD", "dev").strip() or "dev"

    lines = [
        "# HELP l3mon_event_phase Current event phase (1 if active).",
        "# TYPE l3mon_event_phase gauge",
        *phase_lines,
        "# HELP l3mon_tick_value Current board tick counter value.",
        "# TYPE l3mon_tick_value gauge",
        f"l3mon_tick_value {tick_val}",
        "# HELP l3mon_solves_last_minute Solves in the last minute.",
        "# TYPE l3mon_solves_last_minute gauge",
        f"l3mon_solves_last_minute {solves_last_min}",
        "# HELP l3mon_wrong_attempts_last_minute Wrong flag attempts in the last minute.",
        "# TYPE l3mon_wrong_attempts_last_minute gauge",
        f"l3mon_wrong_attempts_last_minute {fails_last_min}",
        "# HELP l3mon_registrations_last_minute User registrations in the last minute.",
        "# TYPE l3mon_registrations_last_minute gauge",
        f"l3mon_registrations_last_minute {regs_last_min}",
        "# HELP l3mon_build_info Build information.",
        "# TYPE l3mon_build_info gauge",
        f'l3mon_build_info{{version="{build_version}"}} 1',
        "",
    ]
    return "\n".join(lines)


@ops.route("/l3mon/metrics")
def metrics():
    global _last_metrics_time, _last_metrics_body
    t = time.time()
    if t - _last_metrics_time > _METRICS_CACHE_SECONDS or not _last_metrics_body:
        _last_metrics_body = _render_metrics()
        _last_metrics_time = t

    resp = Response(_last_metrics_body, status=200, mimetype="text/plain; version=0.0.4; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store"
    return resp


from flask.sessions import SessionMixin


class DummyHealthSession(dict, SessionMixin):
    def __init__(self):
        super().__init__()
        self.modified = False
        self.accessed = False
        self.permanent = False
        self.sid = "healthcheck"


def load(app):
    check_ctfd_version()
    app.register_blueprint(ops)

    # Make import_in_progress resilient to Redis downtime
    try:
        import CTFd.utils
        if hasattr(CTFd.utils, "import_in_progress"):
            orig_import = CTFd.utils.import_in_progress
            def safe_import():
                try:
                    return orig_import()
                except Exception:
                    return False
            CTFd.utils.import_in_progress = safe_import
    except Exception:
        pass

    # Fast before_request handler for /readyz: skips CTFd initialization hooks
    def _handle_readyz_fast():
        from flask import request
        if request.path == "/readyz":
            return readyz()

    if None in app.before_request_funcs:
        app.before_request_funcs[None].insert(0, _handle_readyz_fast)
    else:
        app.before_request_funcs[None] = [_handle_readyz_fast]

    # Exclude /readyz from hooks in other plugins that check Redis (scheduler, scoring triggers)
    try:
        from CTFd.plugins.l3mon_release import scheduler
        if hasattr(scheduler, "_SKIP_PATHS") and "/readyz" not in scheduler._SKIP_PATHS:
            scheduler._SKIP_PATHS = (*scheduler._SKIP_PATHS, "/readyz")
    except Exception:
        pass

    try:
        from CTFd.plugins.l3mon_scoring import triggers
        if hasattr(triggers, "_SKIP_PATHS") and "/readyz" not in triggers._SKIP_PATHS:
            triggers._SKIP_PATHS = (*triggers._SKIP_PATHS, "/readyz")
    except Exception:
        pass

    if hasattr(app, "session_interface") and app.session_interface is not None:
        orig_open = app.session_interface.open_session
        orig_save = app.session_interface.save_session

        def safe_open_session(app_obj, req):
            if req.path in ("/readyz", "/l3mon/healthz", "/healthcheck"):
                return DummyHealthSession()
            try:
                return orig_open(app_obj, req)
            except Exception:
                return DummyHealthSession()

        def safe_save_session(app_obj, sess, resp):
            if isinstance(sess, DummyHealthSession):
                return
            try:
                return orig_save(app_obj, sess, resp)
            except Exception:
                pass

        app.session_interface.open_session = safe_open_session
        app.session_interface.save_session = safe_save_session
