"""The pictures and the icon are served at the addresses the pages use (SP6 plumbing).

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
"""
import os
import re

import pytest

from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    yield app
    destroy_ctfd(app)


def exported_art(app):
    folder = os.path.join(app.root_path, "themes", "l3mon", "static", "art")
    return sorted(os.listdir(folder))


def test_every_picture_the_stylesheet_names_is_served_with_a_year_of_cache(app):
    names = exported_art(app)
    assert len(names) == 16
    with app.test_client() as client:
        sheet = client.get("/login").get_data(as_text=True)
        css = re.search(r'href="(/themes/l3mon/static/app\.[0-9a-f]{10}\.css)"', sheet).group(1)
        body = client.get(css).get_data(as_text=True)
        named = sorted(set(re.findall(r'url\("/art/([^"]+)"\)', body)))
        assert named == names, "the stylesheet names exactly the exported pictures"
        for name in named:
            r = client.get(f"/art/{name}")
            assert r.status_code == 200 and r.mimetype == "image/svg+xml", name
            assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
            assert client.head(f"/art/{name}").status_code == 200


@pytest.mark.parametrize("bad", ["ch0.clear.0000000000.svg", "..%2f..%2fconfig.ini", "ch0.clear.97c9beab2d.svg.bak", "CH0.clear.97c9beab2d.svg", "ch0.clear.97c9beab2d", "a.b.c.svg"])
def test_only_the_names_the_exporter_writes_can_be_asked_for(app, bad):
    with app.test_client() as client:
        assert client.get(f"/art/{bad}").status_code == 404


def test_the_art_address_answers_get_and_head_only(app):
    name = exported_art(app)[0]
    with app.test_client() as client:
        assert client.post(f"/art/{name}").status_code in (404, 405)
        assert client.delete(f"/art/{name}").status_code in (404, 405)


def test_the_icon_is_served_and_the_pages_link_it(app):
    with app.test_client() as client:
        r = client.get("/favicon.svg")
        assert r.status_code == 200 and r.mimetype == "image/svg+xml"
        assert "max-age=86400" in r.headers["Cache-Control"]
        assert 'href="/favicon.svg"' in client.get("/login").get_data(as_text=True)
