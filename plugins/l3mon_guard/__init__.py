"""l3mon_guard: Anti-abuse suite for L3m0nCTF 2026 (SP5).

Provides:
- Registration policies: disposable email domains, disguised email normalization
- Team name policy: Unicode NFKC + case folding, whitespace collapsing, reserved impersonation blocking
- Cloudflare Turnstile CAPTCHA on register, reset, and login after failures
- Per-team flag attempt rate limits (429 Retry-After, 'burst' evidence)
- NAT-aware evidence heuristics ('shared_address', 'registration_burst')
- Evidence queue table (l3mon_evidence), crew review API and admin page
- Context processor value 'l3mon_guard' for theme templates
"""
import functools
import json
import logging
from flask import abort, jsonify, render_template, request, Response

from CTFd.models import Teams, db
from CTFd.plugins.l3mon_core.text import plain_text
from CTFd.plugins.l3mon_guard.crew_api import bp as admin_bp, page_bp as admin_page_bp
from CTFd.plugins.l3mon_guard.evidence import record
from CTFd.plugins.l3mon_guard.limits import (
    check_and_record_team_attempt,
    is_campus_ip,
    track_registration_burst,
    track_shared_address,
)
from CTFd.plugins.l3mon_guard.policies.disposable import is_disposable_domain
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used, normalize_email
from CTFd.plugins.l3mon_guard.policies.team_name import canonical_team_name, validate_team_name
from CTFd.plugins.l3mon_guard.turnstile import (
    clear_login_failure,
    get_guard_context,
    is_login_captcha_required,
    record_login_failure,
    verify_turnstile,
)

_log = logging.getLogger("l3mon.guard")


def _wrap_register(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            from CTFd.utils.user import get_ip
            ip = get_ip()

            # 1. Turnstile CAPTCHA check
            token = request.form.get("cf-turnstile-response", "")
            valid, err = verify_turnstile(token, ip, action="register")
            if not valid:
                name = request.form.get("name", "")
                email = request.form.get("email", "")
                return render_template("register.html", errors=[err], name=name, email=email)

            # 2. Disposable email check
            raw_email = request.form.get("email", "").strip()
            if is_disposable_domain(raw_email):
                domain = raw_email.rsplit("@", 1)[-1].strip().lower()[:80]
                record("disposable_email_blocked", detail=f"domain={domain}")  # the domain only: never the address of a person
                name = request.form.get("name", "")
                msg = "This email address cannot be used here. Please use a permanent email address."
                return render_template("register.html", errors=[msg], name=name, email=raw_email)

            # 3. Disguised email check
            norm_email, norm_err = normalize_email(raw_email)
            if norm_err:
                name = request.form.get("name", "")
                return render_template("register.html", errors=[norm_err], name=name, email=raw_email)

            if is_email_disguise_used(raw_email):
                name = request.form.get("name", "")
                # Generic sentence standard in CTFd for used email
                return render_template("register.html", errors=["That email has already been used"], name=name, email=raw_email)

            # Execute stock registration
            resp = orig_view(*args, **kwargs)

            # If registration succeeded (redirects), record registration heuristic
            if getattr(resp, "status_code", 0) in (302, 303):
                track_registration_burst(ip)

            return resp

        return orig_view(*args, **kwargs)

    return wrapped


def _wrap_login(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            from CTFd.utils.user import get_ip
            ip = get_ip()
            name = request.form.get("name", "").strip()

            # Check if login requires CAPTCHA verification
            if is_login_captcha_required(name, ip):
                token = request.form.get("cf-turnstile-response", "")
                valid, err = verify_turnstile(token, ip, action="login")
                if not valid:
                    record_login_failure(name, ip)
                    return render_template("login.html", errors=[err], name=name)

            resp = orig_view(*args, **kwargs)

            # CTFd redirects on successful login
            if getattr(resp, "status_code", 0) in (302, 303):
                clear_login_failure(name, ip)
            else:
                # Failed login (200 returned with form errors)
                record_login_failure(name, ip)

            return resp

        return orig_view(*args, **kwargs)

    return wrapped


def _wrap_reset_password(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            from CTFd.utils.user import get_ip
            ip = get_ip()

            token = request.form.get("cf-turnstile-response", "")
            valid, err = verify_turnstile(token, ip, action="reset")
            if not valid:
                return render_template("reset_password.html", errors=[err])

        return orig_view(*args, **kwargs)

    return wrapped


def _wrap_team_new(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method == "POST":
            from CTFd.utils.user import is_admin
            name = request.form.get("name", "").strip()
            clean_name, err = validate_team_name(name, is_crew=is_admin())
            if err:
                record("name_policy_blocked", detail=f"name={name[:50]}; err={err}")
                return render_template("teams/new_team.html", errors=[err], name=name)

        return orig_view(*args, **kwargs)

    return wrapped


def _wrap_team_api_patch(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method in ("PATCH", "POST"):
            data = request.get_json(silent=True)
            if isinstance(data, dict) and "name" in data:
                from CTFd.utils.user import is_admin
                name = str(data["name"]).strip()
                target_team_id = kwargs.get("team_id")
                clean_name, err = validate_team_name(name, is_crew=is_admin(), exclude_team_id=target_team_id)
                if err:
                    record("name_policy_blocked", detail=f"name={name[:50]}; err={err}")
                    return jsonify({"success": False, "errors": {"name": [err]}}), 400

        return orig_view(*args, **kwargs)

    return wrapped


def _install_attempt_guard(app):
    """Intercept flag submissions to enforce per-team limits across challenges."""
    @app.before_request
    def check_submission_limit():
        if request.path == "/api/v1/challenges/attempt" and request.method == "POST":
            from CTFd.utils.user import authed, get_current_team, get_ip
            if authed():
                team = get_current_team()
                if team is not None:
                    ip = get_ip()
                    allowed, retry_after = check_and_record_team_attempt(team.id)
                    if not allowed:
                        resp = jsonify({
                            "success": False,
                            "data": {
                                "status": "paused",
                                "message": f"Too many flag attempts from your team. Please wait {retry_after} seconds."
                            }
                        })
                        resp.status_code = 429
                        resp.headers["Retry-After"] = str(retry_after)
                        return resp

                    track_shared_address(team.id, ip)


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version
    check_ctfd_version()

    from CTFd.plugins import register_admin_plugin_menu_bar
    from CTFd.plugins.migrations import upgrade

    # 1. Database migrations
    upgrade(plugin_name="l3mon_guard")

    # 1b. Register email normalization event listeners
    from CTFd.plugins.l3mon_guard.policies.email_norm import register_email_norm_listeners
    register_email_norm_listeners()

    # 2. Blueprints
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_page_bp)
    register_admin_plugin_menu_bar("Anti-abuse", "/admin/l3mon/guard")

    # 3. Context processor for theme templates
    @app.context_processor
    def inject_guard_context():
        return {"l3mon_guard": get_guard_context()}

    # 4. View function wrappers
    if "auth.register" in app.view_functions:
        app.view_functions["auth.register"] = _wrap_register(app.view_functions["auth.register"])
    if "auth.login" in app.view_functions:
        app.view_functions["auth.login"] = _wrap_login(app.view_functions["auth.login"])
    if "auth.reset_password" in app.view_functions:
        app.view_functions["auth.reset_password"] = _wrap_reset_password(app.view_functions["auth.reset_password"])

    if "teams.new" in app.view_functions:
        app.view_functions["teams.new"] = _wrap_team_new(app.view_functions["teams.new"])
    if "api.teams_team" in app.view_functions:
        app.view_functions["api.teams_team"] = _wrap_team_api_patch(app.view_functions["api.teams_team"])
    if "api.teams_team_me" in app.view_functions:
        app.view_functions["api.teams_team_me"] = _wrap_team_api_patch(app.view_functions["api.teams_team_me"])

    # 5. Attempt rate limiter
    _install_attempt_guard(app)
