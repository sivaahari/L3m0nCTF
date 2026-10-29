"""The username and e-mail rules, where a player chooses or changes them on the server.

The prototype's pages check a name and an address in the browser; a script posts straight to the calls those pages use, so the same rules stand in front of
them here: the sign-up form (`auth.register`) and CTFd's own API (`PATCH /api/v1/users/me`, the crew's `PATCH /api/v1/users/<id>`). The settings page and
the sign-up page of the theme call the same policies themselves; this is what keeps the API from being the way round them.

All of it follows `signup_rules.enforced()` (always on for a real platform; off in a CTFd test app unless a test turns it on) and the crew is exempt, as it is
everywhere else. A name or an address that is not being changed is not judged again: an account made before a rule may hold one the rule refuses.
"""
import functools

from flask import jsonify, render_template, request

from CTFd.plugins.l3mon_core.signup_rules import enforced
from CTFd.plugins.l3mon_guard.policies.disposable import is_disposable_domain
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used, normalize_email
from CTFd.plugins.l3mon_guard.policies.username import validate_username

MSG_DISPOSABLE = "This email address cannot be used here. Please use a permanent email address."
MSG_EMAIL_USED = "That email has already been used"  # CTFd's own sentence for an address in use

_ENDPOINTS_REGISTER = ("auth.register",)
_ENDPOINTS_API = ("api.users_user_private", "api.users_user_public")  # PATCH /users/me, PATCH /users/<id> (the crew)


def _mark(wrapped):
    wrapped.l3mon_player_rules = True  # lets a test (and a reader) see that the wrapper stands on this endpoint
    return wrapped


def wrap_register(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method == "POST" and enforced():
            raw_name = request.form.get("name", "")
            _, err = validate_username(raw_name)  # the rule only trims the ends, and CTFd's own sign-up trims the name the same way before it stores it
            if err:
                return render_template("register.html", errors=[err], name=raw_name, email=request.form.get("email", "").strip())
        return orig_view(*args, **kwargs)

    return _mark(wrapped)


def _target_user(user_id):
    """The account the call changes: the one in the address (the crew's call) or the asker (`/users/me`)."""
    from CTFd.models import Users
    from CTFd.utils.user import get_current_user

    if user_id is None:
        return get_current_user()
    return Users.query.filter_by(id=user_id).first()


def _refuse(field, message):
    return jsonify({"success": False, "errors": {field: [message]}}), 400


def wrap_user_api_patch(orig_view):
    @functools.wraps(orig_view)
    def wrapped(*args, **kwargs):
        if request.method == "PATCH" and enforced():
            data = request.get_json(silent=True)
            if isinstance(data, dict) and ("name" in data or "email" in data):
                from CTFd.utils.user import is_admin

                target = _target_user(kwargs.get("user_id"))
                target_id = target.id if target is not None else None

                if "name" in data and not isinstance(data["name"], str):
                    return _refuse("name", validate_username("")[1])
                if "name" in data and (target is None or data["name"] != target.name):
                    clean_name, err = validate_username(data["name"], is_crew=is_admin(), exclude_user_id=target_id)
                    if err:
                        return _refuse("name", err)
                    if clean_name != data["name"]:
                        data["name"] = clean_name  # the dictionary CTFd's own view reads next: what is stored is exactly what was checked
                        request._cached_json = (data, data)  # both readings (silent or not) answer with this very dictionary

                if "email" in data and isinstance(data["email"], str) and not is_admin():
                    email = data["email"].strip()
                    if email and (target is None or email.casefold() != (target.email or "").casefold()):
                        if is_disposable_domain(email):
                            return _refuse("email", MSG_DISPOSABLE)
                        _, norm_err = normalize_email(email)
                        if norm_err:
                            return _refuse("email", norm_err)
                        if is_email_disguise_used(email, exclude_user_id=target_id):
                            return _refuse("email", MSG_EMAIL_USED)
        return orig_view(*args, **kwargs)

    return _mark(wrapped)


def install(app):
    for endpoint in _ENDPOINTS_REGISTER:
        if endpoint in app.view_functions:
            app.view_functions[endpoint] = wrap_register(app.view_functions[endpoint])
    for endpoint in _ENDPOINTS_API:
        if endpoint in app.view_functions:
            app.view_functions[endpoint] = wrap_user_api_patch(app.view_functions[endpoint])
