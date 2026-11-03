"""Tests for the programme story field in the crew's plan API.

PUT /api/v1/l3mon/admin/programmes
GET /api/v1/l3mon/admin/release
"""
from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user

PROGRAMMES = "/api/v1/l3mon/admin/programmes"
RELEASE = "/api/v1/l3mon/admin/release"


def setup_app():
    return create_ctfd(enable_plugins=True)


def test_plan_accepts_and_trims_story():
    app = setup_app()
    with app.app_context():
        admin = login_as_user(app, "admin")
        chal = gen_challenge(db, name="p1", state="hidden")
        payload = {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {
                    "challenge_id": chal.id,
                    "channel": "street",
                    "cell": 0,
                    "number": 101,
                    "slug": "p1",
                    "story": "   A thrilling pursuit through the alleyways.   ",
                }
            ],
        }
        r = admin.put(PROGRAMMES, json=payload)
        assert r.status_code == 200, r.get_data(as_text=True)

        prog = Programme.query.filter_by(slug="p1").one()
        assert prog.story == "A thrilling pursuit through the alleyways."

        # Verify release_view shows it
        r_rel = admin.get(RELEASE)
        assert r_rel.status_code == 200
        rel_data = r_rel.get_json()["data"]
        items = rel_data["channels"][0]["programmes"]
        assert items[0]["story"] == "A thrilling pursuit through the alleyways."
    destroy_ctfd(app)


def test_plan_safe_upsert_preserves_omitted_story_and_clears_on_empty():
    app = setup_app()
    with app.app_context():
        admin = login_as_user(app, "admin")
        chal = gen_challenge(db, name="p1", state="hidden")
        # Step 1: Initial upload with a story line
        payload1 = {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {
                    "challenge_id": chal.id,
                    "channel": "street",
                    "cell": 0,
                    "number": 101,
                    "slug": "p1",
                    "story": "The detective arrived at dusk.",
                }
            ],
        }
        r1 = admin.put(PROGRAMMES, json=payload1)
        assert r1.status_code == 200
        assert Programme.query.filter_by(slug="p1").one().story == "The detective arrived at dusk."

        # Step 2: Safe upsert without 'story' field keeps existing story line
        payload2 = {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {
                    "challenge_id": chal.id,
                    "channel": "street",
                    "cell": 0,
                    "number": 101,
                    "slug": "p1",
                }
            ],
        }
        r2 = admin.put(PROGRAMMES, json=payload2)
        assert r2.status_code == 200
        assert Programme.query.filter_by(slug="p1").one().story == "The detective arrived at dusk."

        # Step 3: Explicit empty string clears it
        payload3 = {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {
                    "challenge_id": chal.id,
                    "channel": "street",
                    "cell": 0,
                    "number": 101,
                    "slug": "p1",
                    "story": "",
                }
            ],
        }
        r3 = admin.put(PROGRAMMES, json=payload3)
        assert r3.status_code == 200
        assert Programme.query.filter_by(slug="p1").one().story == ""
    destroy_ctfd(app)


def test_plan_story_with_challenge_name_and_challenge_id():
    app = setup_app()
    with app.app_context():
        admin = login_as_user(app, "admin")
        c1 = gen_challenge(db, name="First Challenge", state="hidden")
        c2 = gen_challenge(db, name="Second Challenge", state="hidden")
        payload = {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {
                    "challenge_id": c1.id,
                    "channel": "street",
                    "cell": 0,
                    "number": 101,
                    "slug": "first",
                    "story": "Identified by ID.",
                },
                {
                    "challenge_name": "Second Challenge",
                    "channel": "street",
                    "cell": 1,
                    "number": 102,
                    "slug": "second",
                    "story": "Identified by Name.",
                },
            ],
        }
        r = admin.put(PROGRAMMES, json=payload)
        assert r.status_code == 200
        assert Programme.query.filter_by(slug="first").one().story == "Identified by ID."
        assert Programme.query.filter_by(slug="second").one().story == "Identified by Name."
    destroy_ctfd(app)


def test_plan_refuses_invalid_story_values_and_changes_nothing():
    app = setup_app()
    with app.app_context():
        admin = login_as_user(app, "admin")
        chal = gen_challenge(db, name="p1", state="hidden")
        # Baseline
        base = {
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
            "programmes": [
                {
                    "challenge_id": chal.id,
                    "channel": "street",
                    "cell": 0,
                    "number": 101,
                    "slug": "p1",
                    "story": "Initial story.",
                }
            ],
        }
        admin.put(PROGRAMMES, json=base)
        assert Programme.query.filter_by(slug="p1").one().story == "Initial story."

        bad_values = [
            ("x" * 281, "may be at most 280 characters"),
            ("<script>alert(1)</script>", "may not contain < or >"),
            ("A line\nwith newline", "may not contain control characters"),
            ("A line\twith tab", "may not contain control characters"),
            (12345, "must be text"),
            (True, "must be text"),
        ]

        for bad, expected_msg in bad_values:
            bad_payload = {
                "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
                "programmes": [
                    {
                        "challenge_id": chal.id,
                        "channel": "street",
                        "cell": 0,
                        "number": 101,
                        "slug": "p1",
                        "story": bad,
                    }
                ],
            }
            r = admin.put(PROGRAMMES, json=bad_payload)
            assert r.status_code == 400
            errors = r.get_json()["errors"]
            assert "programmes[0].story" in errors
            assert any(expected_msg in m for m in errors["programmes[0].story"])
            # Existing story row remains untouched
            assert Programme.query.filter_by(slug="p1").one().story == "Initial story."
    destroy_ctfd(app)
