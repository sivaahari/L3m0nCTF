"""The sign-in page (SP6 slice 1): where a sign-in goes next, for the theme's form.

CTFd's sign-in view reads the destination from the address only (CTFd/auth.py, login(): request.args.get("next"), checked
with validators.is_safe_url). The theme's sign-in form keeps the destination in a hidden field and posts to /login, so for a
POST to /login with no destination in the address, a `next` from the form is put into the address, where CTFd reads it.
CTFd still makes the safety check itself; a destination it would refuse is left out here as well.
"""
from flask import request
from werkzeug.datastructures import ImmutableMultiDict

from CTFd.utils import validators


def install(app):
    @app.before_request
    def carry_next_from_the_form_into_the_address():
        if request.method != "POST" or request.path != "/login" or request.args.get("next"):
            return None
        wanted = request.form.get("next", "")
        if wanted and validators.is_safe_url(wanted):
            request.args = ImmutableMultiDict([("next", wanted)])
        return None
