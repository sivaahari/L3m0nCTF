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


def _check_db_quick() -> bool:
    """Execute SELECT 1 with db.engine.connect() directly."""
    from CTFd.models import db

    try:
        with db.engine.connect() as conn:
            val = conn.execute(text("SELECT 1")).scalar()
            return val == 1
    except Exception:
        return False


def _check_redis_quick() -> bool:
    """Send PING to Redis client or verify cache responsiveness."""
    from CTFd.cache import cache

    try:
        backend = getattr(cache, "cache", None)
        client = (
            getattr(backend, "_read_client", None)
            or getattr(backend, "_write_client", None)
            or getattr(backend, "_client", None)
        )
        if client is not None and hasattr(client, "ping"):
            return bool(client.ping())
        # If cache itself has a ping method (e.g. test shim):
        if hasattr(cache, "ping") and callable(cache.ping):
            return bool(cache.ping())
        # Fallback for mock/SimpleCache in tests:
        key = "_l3mon_readyz"
        cache.set(key, 1, timeout=5)
        return cache.get(key) == 1
    except Exception:
        return False


def _run_with_timeout(fn, timeout_seconds: float = 1.0) -> bool:
    """Run a callable with a strict timeout in seconds, propagating app context."""
    try:
        app = current_app._get_current_object()
    except RuntimeError:
        app = None

    def target():
        if app is not None:
            with app.app_context():
                return fn()
        return fn()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(target)
            return bool(future.result(timeout=timeout_seconds))
    except Exception:
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
    for p in ("before", "during", "ended"):
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


def load(app):
    check_ctfd_version()
    app.register_blueprint(ops)
