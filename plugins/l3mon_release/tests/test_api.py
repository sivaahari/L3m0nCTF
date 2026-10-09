"""The crew's API: read the plan, change what is on air, and load the plan.

    GET /api/v1/l3mon/admin/release       the plan with what is on air right now and the newest audit lines
    PUT /api/v1/l3mon/admin/release       {"changes": [{"kind", "id", "mode", "at"?}], "reason"?}
    PUT /api/v1/l3mon/admin/programmes    {"channels": [...], "programmes": [...]}

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_release
"""
import calendar
import datetime
import json

import pytest
from freezegun import freeze_time

from CTFd.models import Challenges, Notifications, db
from CTFd.plugins.l3mon_core.models import Audit, Channel, Programme
from CTFd.plugins.l3mon_release.reconcile import clear_pull_back_handlers, reconcile, register_pull_back_handler
from CTFd.utils import set_config
from CTFd.utils.security.auth import generate_user_token
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user, register_user

RELEASE = "/api/v1/l3mon/admin/release"
PROGRAMMES = "/api/v1/l3mon/admin/programmes"
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())
T_END = T_START + 24 * 3600
T_LIVE = T_START + 3600  # 10:00 IST
T_LATER = T_LIVE + 2 * 3600  # 12:00 IST


def clock(t):
    return freeze_time(datetime.datetime.utcfromtimestamp(t))


def setup_app():
    clear_pull_back_handlers()
    app = create_ctfd(enable_plugins=True)
    # the tests move the clock to the day of the round; a sign-in made today must still be good then
    app.permanent_session_lifetime = datetime.timedelta(days=3650)
    return app


def started():
    set_config("start", T_START)
    set_config("end", T_END)


def admin_client(app):
    return login_as_user(app, "admin")


def body(r):
    return r.get_json()


def load_plan(admin, extra=None):
    """Two channels and four challenges, through the real sync call."""
    for name in ("alpha", "beta", "gamma", "delta"):
        gen_challenge(db, name=name, state="hidden")
    ids = {c.name: c.id for c in Challenges.query.all()}
    payload = {
        "channels": [
            {"slug": "street", "name": "Mighty Street", "position": 1, "accent": "ch1", "synopsis": "A loud street."},
            {"slug": "break", "name": "Sponsored Break", "position": 7, "kind": "sponsored", "sponsor_name": "Acme", "sponsor_logo": "acme.svg"},
        ],
        "programmes": [
            {"challenge_id": ids["alpha"], "channel": "street", "cell": 0, "number": 101, "slug": "alpha"},
            {"challenge_id": ids["beta"], "channel": "street", "cell": 1, "number": 102, "slug": "beta"},
            {"challenge_id": ids["gamma"], "channel": "street", "cell": 2, "number": 103, "slug": "gamma"},
            {"challenge_id": ids["delta"], "channel": "break", "cell": 0, "number": 701, "slug": "delta"},
        ],
    }
    if extra:
        payload.update(extra)
    r = admin.put(PROGRAMMES, json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    return ids


def plan_ids():
    return {p.slug: p.id for p in Programme.query.all()}, {c.slug: c.id for c in Channel.query.all()}


def states():
    return {c.name: c.state for c in Challenges.query.order_by(Challenges.id)}


def release(admin, *changes, reason=None):
    payload = {"changes": [{"kind": k, "id": i, "mode": m, **({"at": a} if a is not None else {})} for k, i, m, a in changes]}
    if reason is not None:
        payload["reason"] = reason
    return admin.put(RELEASE, json=payload)


# ---- who may ask -------------------------------------------------------------------------------------------------------

def test_a_visitor_and_a_player_are_refused_every_call_and_nothing_changes():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        register_user(app, name="player", email="player@example.com")
        player = login_as_user(app, "player")
        for client in (app.test_client(), player):
            for method, path, payload in (("get", RELEASE, None), ("put", RELEASE, {"changes": []}), ("put", PROGRAMMES, {"channels": [], "programmes": []})):
                r = getattr(client, method)(path, json=payload) if payload is not None else getattr(client, method)(path, headers={"Accept": "application/json"})
                assert r.status_code in (302, 403), (method, path, r.status_code)
        assert {p.release_state for p in Programme.query.all()} == {"withheld"}
    destroy_ctfd(app)


def test_an_administrators_token_works_for_scripts_and_a_session_without_the_csrf_token_does_not():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        from CTFd.models import Users

        token = generate_user_token(Users.query.filter_by(name="admin").first()).value
        script = app.test_client()
        r = script.get(RELEASE, headers={"Authorization": f"Token {token}", "Content-type": "application/json"})
        assert r.status_code == 200 and body(r)["success"] is True, r.get_data(as_text=True)[:400]
        r = script.put(PROGRAMMES, json={"channels": [], "programmes": []}, headers={"Authorization": f"Token {token}"})
        assert r.status_code == 200
        # a signed-in session must send CTFd's CSRF token; sending the JSON as raw data skips the helper that adds it
        raw = json.dumps({"changes": [{"kind": "channel", "id": 1, "mode": "release"}]})
        assert admin.put(RELEASE, data=raw, content_type="application/json").status_code == 403
        assert admin.put(RELEASE, data=raw, content_type="application/json", headers={"CSRF-Token": "wrong"}).status_code == 403
        assert Channel.query.filter_by(release_state="released").count() == 0
    destroy_ctfd(app)


# ---- reading the plan --------------------------------------------------------------------------------------------------

def test_the_view_shows_every_channel_and_programme_with_what_players_see_right_now():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        programmes, channels = plan_ids()
        gen_challenge(db, name="loose", state="visible")
        with clock(T_LIVE):
            assert release(admin, ("channel", channels["street"], "release", None), ("programme", programmes["alpha"], "release", None),
                           ("programme", programmes["beta"], "schedule", T_LATER)).status_code == 200
            r = admin.get(RELEASE)
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
        data = body(r)["data"]
        assert data["now"] == T_LIVE and data["start"] == T_START and data["end"] == T_END and data["phase"]["state"] == "live"
        street, brk = data["channels"]
        assert [street["slug"], brk["slug"]] == ["street", "break"], "channels in channel order"
        assert (street["state"], street["on_air"], street["position"], street["kind"]) == ("released", True, 1, "standard")
        assert (brk["kind"], brk["state"], brk["on_air"]) == ("sponsored", "withheld", False)
        alpha, beta, gamma = street["programmes"]
        assert (alpha["slug"], alpha["name"], alpha["state"], alpha["on_air"], alpha["visible"]) == ("alpha", "alpha", "released", True, True)
        assert (beta["state"], beta["at"], beta["on_air"], beta["visible"]) == ("scheduled", T_LATER, False, False)
        assert beta["at_ist"] == "28 Nov 2026, 12:00 IST"
        assert (gamma["state"], gamma["at"], gamma["on_air"]) == ("withheld", None, False)
        assert data["counts"] == {"channels": 2, "programmes": 4, "on_air": 1, "coming": 3}
        assert [l["id"] for l in data["loose"]] == [Challenges.query.filter_by(name="loose").first().id]
        assert data["audit"][0]["action"] and len(data["audit"]) <= 20
    destroy_ctfd(app)


# ---- changing what is on air -------------------------------------------------------------------------------------------

def test_release_withhold_and_schedule_change_what_players_see_and_each_change_leaves_an_audit_line_with_the_reason():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        programmes, channels = plan_ids()
        with clock(T_LIVE):
            r = release(admin, ("channel", channels["street"], "release", None), ("programme", programmes["alpha"], "release", None),
                        ("programme", programmes["beta"], "schedule", T_LATER), reason="the opening wave")
            assert r.status_code == 200, r.get_data(as_text=True)
            assert states() == {"alpha": "visible", "beta": "hidden", "gamma": "hidden", "delta": "hidden"}
            assert body(r)["data"]["result"] == {"changed": 3, "shown": 1, "hidden": 0}
            lines = Audit.query.filter_by(action="release.set").order_by(Audit.id).all()
            assert [l.target for l in lines] == ["channel:street", "programme:alpha", "programme:beta"]
            assert all(l.actor_name == "admin" and "the opening wave" in l.detail for l in lines)
            assert "withheld -> released" in lines[0].detail and "withheld -> scheduled" in lines[2].detail and "12:00 IST" in lines[2].detail
        with clock(T_LATER):
            admin.get(RELEASE)  # any request after the second applies the drop
            assert states()["beta"] == "visible"
        with clock(T_LATER + 5):
            assert release(admin, ("programme", programmes["alpha"], "withhold", None), reason="found a leak").status_code == 200
            assert states()["alpha"] == "hidden" and Programme.query.get(programmes["alpha"]).release_at is None
    destroy_ctfd(app)


def test_the_same_request_twice_changes_nothing_the_second_time_and_announces_nothing_more():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        programmes, channels = plan_ids()
        with clock(T_LIVE):
            args = (("channel", channels["street"], "release", None), ("programme", programmes["alpha"], "release", None), ("programme", programmes["gamma"], "schedule", T_LATER))
            first = release(admin, *args)
            audit_lines, notes = Audit.query.count(), Notifications.query.count()
            second = release(admin, *args)
        assert body(first)["data"]["result"]["changed"] == 3 and body(second)["data"]["result"] == {"changed": 0, "shown": 0, "hidden": 0}
        assert Audit.query.count() == audit_lines and Notifications.query.count() == notes == 1
    destroy_ctfd(app)


def test_a_release_during_the_round_is_announced_and_a_pull_back_reaches_the_handlers():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        programmes, channels = plan_ids()
        heard = []
        register_pull_back_handler(lambda cids: heard.append(sorted(cids)))
        with clock(T_LIVE):
            release(admin, ("channel", channels["street"], "release", None), ("programme", programmes["alpha"], "release", None), ("programme", programmes["beta"], "release", None))
            assert [n.content for n in Notifications.query.all()] == ["CH 1 · Mighty Street has 2 new programmes."]
            release(admin, ("programme", programmes["alpha"], "withhold", None))
            assert heard == [[ids["alpha"]]]
            release(admin, ("channel", channels["street"], "withhold", None))
            assert heard == [[ids["alpha"]], [ids["beta"]]]
    destroy_ctfd(app)


@pytest.mark.parametrize(
    "payload,field",
    [
        ("not json at all", "body"),
        ([], "body"),
        ({}, "changes"),
        ({"changes": "street"}, "changes"),
        ({"changes": []}, "changes"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release"}] * 201}, "changes"),
        ({"changes": [{"kind": "planet", "id": 1, "mode": "release"}]}, "changes[0].kind"),
        ({"changes": [{"kind": "channel", "id": 99999, "mode": "release"}]}, "changes[0].id"),
        ({"changes": [{"kind": "programme", "id": 99999, "mode": "release"}]}, "changes[0].id"),
        ({"changes": [{"kind": "channel", "id": "one", "mode": "release"}]}, "changes[0].id"),
        ({"changes": [{"kind": "channel", "id": True, "mode": "release"}]}, "changes[0].id"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "explode"}]}, "changes[0].mode"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "schedule"}]}, "changes[0].at"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "schedule", "at": "soon"}]}, "changes[0].at"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "schedule", "at": True}]}, "changes[0].at"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "schedule", "at": T_LIVE - 1}]}, "changes[0].at"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "schedule", "at": T_LIVE + 31 * 86400}]}, "changes[0].at"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release", "at": T_LATER}]}, "changes[0].at"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release"}, {"kind": "channel", "id": 1, "mode": "withhold"}]}, "changes[1]"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release"}], "reason": "x" * 201}, "reason"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release"}], "reason": 42}, "reason"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release"}], "reasons": "typo"}, "reasons"),
        ({"change": [{"kind": "channel", "id": 1, "mode": "release"}]}, "change"),
        ({"changes": [{"kind": "channel", "id": 1, "mode": "release", "when": T_LATER}]}, "changes[0].when"),
    ],
)
def test_every_bad_request_is_refused_with_the_field_named_and_changes_nothing(payload, field):
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        before = (Audit.query.count(), sorted((c.slug, c.release_state) for c in Channel.query.all()))
        with clock(T_LIVE):
            if payload == "not json at all":
                r = admin.put(RELEASE, data="{not json", content_type="application/json", headers={"CSRF-Token": _nonce(admin)})
            else:
                r = admin.put(RELEASE, json=payload)
        assert r.status_code == 400, r.get_data(as_text=True)
        out = r.get_json()
        assert out["success"] is False and field in out["errors"] and isinstance(out["errors"][field], list), out
        assert (Audit.query.count(), sorted((c.slug, c.release_state) for c in Channel.query.all())) == before
    destroy_ctfd(app)


def _nonce(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def test_one_bad_change_in_a_batch_applies_none_of_the_batch():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        programmes, channels = plan_ids()
        with clock(T_LIVE):
            r = release(admin, ("channel", channels["street"], "release", None), ("programme", 99999, "release", None))
        assert r.status_code == 400 and "changes[1].id" in r.get_json()["errors"]
        assert Channel.query.filter_by(release_state="released").count() == 0
    destroy_ctfd(app)


# ---- loading the plan --------------------------------------------------------------------------------------------------

def test_loading_the_plan_creates_everything_off_air_and_running_it_again_changes_nothing():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        assert {c.release_state for c in Channel.query.all()} == {"withheld"} and {p.release_state for p in Programme.query.all()} == {"withheld"}
        assert set(states().values()) == {"hidden"}
        street = Channel.query.filter_by(slug="street").one()
        assert (street.name, street.position, street.accent, street.synopsis, street.kind) == ("Mighty Street", 1, "ch1", "A loud street.", "standard")
        brk = Channel.query.filter_by(slug="break").one()
        assert (brk.kind, brk.sponsor_name, brk.sponsor_logo) == ("sponsored", "Acme", "acme.svg")
        assert Programme.query.filter_by(slug="delta").one().number == 701
        lines = Audit.query.filter_by(action="plan.sync").all()
        assert len(lines) == 1 and "channels +2" in lines[0].detail and "programmes +4" in lines[0].detail
        again = admin.put(PROGRAMMES, json={
            "channels": [{"slug": "street", "name": "Mighty Street", "position": 1, "accent": "ch1", "synopsis": "A loud street."}],
            "programmes": [{"challenge_id": ids["alpha"], "channel": "street", "cell": 0, "number": 101, "slug": "alpha"}],
        })
        assert body(again)["data"]["channels"] == {"created": 0, "updated": 0, "unchanged": 1}
        assert body(again)["data"]["programmes"] == {"created": 0, "updated": 0, "unchanged": 1}
    destroy_ctfd(app)


def test_loading_the_plan_again_updates_the_words_but_never_touches_what_is_on_air():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        programmes, channels = plan_ids()
        with clock(T_LIVE):
            release(admin, ("channel", channels["street"], "release", None), ("programme", programmes["alpha"], "release", None))
            r = admin.put(PROGRAMMES, json={
                "channels": [{"slug": "street", "name": "Mighty Street, renamed", "position": 2, "synopsis": "New words."}],
                "programmes": [{"challenge_id": ids["alpha"], "channel": "street", "cell": 5, "number": 150, "slug": "alpha"}],
            })
        assert r.status_code == 200 and body(r)["data"]["channels"]["updated"] == 1 and body(r)["data"]["programmes"]["updated"] == 1
        street = Channel.query.filter_by(slug="street").one()
        assert (street.name, street.position, street.synopsis, street.release_state) == ("Mighty Street, renamed", 2, "New words.", "released")
        alpha = Programme.query.filter_by(slug="alpha").one()
        assert (alpha.cell, alpha.number, alpha.release_state) == (5, 150, "released")
        assert states()["alpha"] == "visible"
    destroy_ctfd(app)


def test_two_programmes_can_swap_their_cells_and_numbers_in_one_request():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        r = admin.put(PROGRAMMES, json={"programmes": [
            {"challenge_id": ids["alpha"], "channel": "street", "cell": 1, "number": 102, "slug": "alpha"},
            {"challenge_id": ids["beta"], "channel": "street", "cell": 0, "number": 101, "slug": "beta"},
        ]})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert {(p.slug, p.cell, p.number) for p in Programme.query.filter(Programme.slug.in_(["alpha", "beta"]))} == {("alpha", 1, 102), ("beta", 0, 101)}
    destroy_ctfd(app)


def test_moving_a_released_programme_to_a_channel_that_is_off_air_takes_it_off_air_without_an_announcement():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        programmes, channels = plan_ids()
        with clock(T_LIVE):
            release(admin, ("channel", channels["street"], "release", None), ("programme", programmes["alpha"], "release", None))
            before = Notifications.query.count()
            admin.put(PROGRAMMES, json={"programmes": [{"challenge_id": ids["alpha"], "channel": "break", "cell": 5, "number": 101, "slug": "alpha"}]})
        assert states()["alpha"] == "hidden" and Notifications.query.count() == before
    destroy_ctfd(app)


def test_a_challenge_can_be_named_by_its_exact_name_when_the_name_is_unique():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        gen_challenge(db, name="Wrestler Padding", state="hidden")
        r = admin.put(PROGRAMMES, json={
            "channels": [{"slug": "street", "name": "Street", "position": 1}],
            "programmes": [{"challenge_name": "Wrestler Padding", "channel": "street", "cell": 0, "number": 1, "slug": "padding"}],
        })
        assert r.status_code == 200 and Programme.query.one().slug == "padding"
    destroy_ctfd(app)


def _plan_payload(**over):
    base = {"challenge_id": None, "channel": "street", "cell": 0, "number": 1, "slug": "one"}
    base.update(over)
    return base


@pytest.mark.parametrize(
    "mutate,field",
    [
        (lambda p, ids: p["programmes"].append(dict(p["programmes"][0])), "programmes[2]"),  # the same challenge twice
        (lambda p, ids: p["programmes"][1].update(slug=p["programmes"][0]["slug"]), "programmes[1].slug"),
        (lambda p, ids: p["programmes"][1].update(number=p["programmes"][0]["number"]), "programmes[1].number"),
        (lambda p, ids: p["programmes"][1].update(cell=p["programmes"][0]["cell"]), "programmes[1].cell"),
        (lambda p, ids: p["programmes"][0].update(challenge_id=99999), "programmes[0].challenge"),
        (lambda p, ids: p["programmes"][0].update(challenge_id=None, challenge_name="nobody"), "programmes[0].challenge"),
        (lambda p, ids: p["programmes"][0].update(challenge_id=None, challenge_name="twin"), "programmes[0].challenge"),
        (lambda p, ids: p["programmes"][0].update(channel="nowhere"), "programmes[0].channel"),
        (lambda p, ids: p["programmes"][0].update(slug="Bad Slug!"), "programmes[0].slug"),
        (lambda p, ids: p["programmes"][0].update(slug="x" * 49), "programmes[0].slug"),
        (lambda p, ids: p["programmes"][0].update(cell=-1), "programmes[0].cell"),
        (lambda p, ids: p["programmes"][0].update(cell=1000), "programmes[0].cell"),
        (lambda p, ids: p["programmes"][0].update(number=0), "programmes[0].number"),
        (lambda p, ids: p["programmes"][0].update(cell="two"), "programmes[0].cell"),
        (lambda p, ids: p["channels"][0].update(slug="Bad Slug"), "channels[0].slug"),
        (lambda p, ids: p["channels"][0].update(name=""), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(name="n" * 81), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(synopsis="s" * 201), "channels[0].synopsis"),
        (lambda p, ids: p["channels"][0].update(kind="weird"), "channels[0].kind"),
        (lambda p, ids: p["channels"][0].update(kind="sponsored"), "channels[0].sponsor_name"),
        (lambda p, ids: p["channels"][0].update(position=100), "channels[0].position"),
        (lambda p, ids: p["channels"].append(dict(p["channels"][0])), "channels[1].slug"),
        (lambda p, ids: p["channels"].extend({"slug": f"c{i}", "name": "c"} for i in range(21)), "channels"),
        (lambda p, ids: p.update(programmes="all of them"), "programmes"),
        (lambda p, ids: p.update(channels={"slug": "x"}), "channels"),
        (lambda p, ids: p.update(programs=[]), "programs"),  # a typo for programmes must not pass silently
        # free text that a notification or a page could render as markup (found by the independent review)
        (lambda p, ids: p["channels"][0].update(name="<b>Street</b>"), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(name="Street [claim a prize](https://evil.example)"), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(name="Street *bold*"), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(name="see https://evil.example"), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(name="Welcome to {ctf_name}"), "channels[0].name"),
        (lambda p, ids: p["channels"][0].update(synopsis="a `code` span"), "channels[0].synopsis"),
        (lambda p, ids: p["channels"][0].update(kind="sponsored", sponsor_name="Acme _inc_"), "channels[0].sponsor_name"),
    ],
)
def test_a_bad_plan_is_refused_with_every_problem_named_and_changes_nothing(mutate, field):
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        a, b = gen_challenge(db, name="a", state="hidden"), gen_challenge(db, name="b", state="hidden")
        gen_challenge(db, name="twin", state="hidden")
        gen_challenge(db, name="twin", state="hidden")
        payload = {
            "channels": [{"slug": "street", "name": "Street", "position": 1}],
            "programmes": [_plan_payload(challenge_id=a.id), _plan_payload(challenge_id=b.id, cell=1, number=2, slug="two")],
        }
        mutate(payload, {"a": a.id, "b": b.id})
        r = admin.put(PROGRAMMES, json=payload)
        assert r.status_code == 400, r.get_data(as_text=True)
        assert field in r.get_json()["errors"], r.get_json()
        assert Channel.query.count() == 0 and Programme.query.count() == 0 and Audit.query.filter_by(action="plan.sync").count() == 0
    destroy_ctfd(app)


def test_a_number_or_slug_already_used_by_another_programme_is_a_clear_conflict_and_changes_nothing():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        ids = load_plan(admin)
        extra = gen_challenge(db, name="extra", state="hidden")
        before = Programme.query.count()
        r = admin.put(PROGRAMMES, json={"programmes": [{"challenge_id": extra.id, "channel": "street", "cell": 9, "number": 101, "slug": "alpha"}]})
        assert r.status_code == 400
        errors = r.get_json()["errors"]
        assert "programmes[0].number" in errors and "programmes[0].slug" in errors
        assert Programme.query.count() == before
    destroy_ctfd(app)


def test_a_cell_already_used_in_the_channel_is_a_conflict_too():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        load_plan(admin)
        extra = gen_challenge(db, name="extra", state="hidden")
        r = admin.put(PROGRAMMES, json={"programmes": [{"challenge_id": extra.id, "channel": "street", "cell": 0, "number": 500, "slug": "extra"}]})
        assert r.status_code == 400 and "programmes[0].cell" in r.get_json()["errors"]
    destroy_ctfd(app)


def test_plain_names_with_ordinary_punctuation_are_accepted_and_a_challenge_name_with_markup_stays_text_for_the_page_to_escape():
    app = setup_app()
    with app.app_context():
        started()
        admin = admin_client(app)
        chal = gen_challenge(db, name="<b>x</b>", state="hidden")  # a challenge's own name is CTFd's; only the crew's page shows it
        r = admin.put(PROGRAMMES, json={
            "channels": [{"slug": "street", "name": "Chase Club: the (very) late show & more!", "position": 1, "synopsis": "It's a cat, a mouse and a dog."}],
            "programmes": [{"challenge_id": chal.id, "channel": "street", "cell": 0, "number": 1, "slug": "one"}],
        })
        assert r.status_code == 200, r.get_data(as_text=True)
        data = body(admin.get(RELEASE))["data"]
        assert data["channels"][0]["name"] == "Chase Club: the (very) late show & more!"
        assert data["channels"][0]["programmes"][0]["name"] == "<b>x</b>"
    destroy_ctfd(app)
