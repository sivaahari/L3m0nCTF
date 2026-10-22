"""The theme's pictures and icon, at the addresses the pages use (SP6 plumbing).

The prototype's stylesheet names its channel pictures as /art/<scene>.<sheet>.<hash>.svg and every page links /favicon.svg. CTFd serves a
theme's files only under /themes/<theme>/static/, so these two addresses are answered here, from the same folder (static/art and
static/favicon.svg, written by the exporter). A name is the exact shape the exporter writes, so nothing outside that folder can be asked
for. The hashed pictures never change under their name (a year, immutable); the icon may.
"""
import os
import re

from flask import abort, send_from_directory

from CTFd.plugins.l3mon_theme.values import THEME

ART_NAME = re.compile(r"^[a-z0-9-]+\.[a-z]+\.[0-9a-f]{10}\.svg\Z")
YEAR = 31536000


def install(app):
    def folder():
        return os.path.join(app.root_path, "themes", THEME, "static")

    @app.route("/art/<name>", methods=["GET", "HEAD"], endpoint="l3mon_theme_art")
    def art(name):
        if not ART_NAME.match(name):
            abort(404)
        response = send_from_directory(os.path.join(folder(), "art"), name, mimetype="image/svg+xml", max_age=YEAR)
        response.headers["Cache-Control"] = f"public, max-age={YEAR}, immutable"
        return response

    @app.route("/favicon.svg", methods=["GET", "HEAD"], endpoint="l3mon_theme_favicon")
    def favicon():
        response = send_from_directory(folder(), "favicon.svg", mimetype="image/svg+xml", max_age=86400)
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
