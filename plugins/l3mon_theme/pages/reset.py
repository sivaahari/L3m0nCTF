"""Password reset page routes and helpers (SP6 Stream C).

Maps `/reset` and `/reset/<data>` to CTFd's core `auth.reset_password` view function,
allowing the prototype forms to post directly to `/reset` and `/reset/<token>`.
"""


def install(app):
    """Add /reset and /reset/<data> URL rules pointing to CTFd's auth.reset_password view."""
    view_func = app.view_functions.get("auth.reset_password")
    if view_func:
        app.add_url_rule("/reset", endpoint="l3mon_theme_reset_ask", view_func=view_func, methods=["GET", "POST"])
        app.add_url_rule("/reset/<data>", endpoint="l3mon_theme_reset_token", view_func=view_func, methods=["GET", "POST"])
