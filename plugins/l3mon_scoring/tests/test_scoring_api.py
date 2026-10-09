"""The crew's scoring API: read the numbers, set a challenge's solves aside and put them back, give a bonus, recalculate.

    GET  /api/v1/l3mon/admin/scoring              every challenge's value against its formula, the voids, the bonuses, the audit lines
    POST /api/v1/l3mon/admin/scoring/revoke       {"challenge_id", "reason"}
    POST /api/v1/l3mon/admin/scoring/restore      {"challenge_id", "reason"?}
    POST /api/v1/l3mon/admin/scoring/bonus        {"team_id", "user_id"?, "trp", "message"}
    POST /api/v1/l3mon/admin/scoring/recalculate  {}

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import json

import pytest

from CTFd.models import Awards, Challenges, Solves, Users, db
from CTFd.plugins.l3mon_core.models import Audit, Bonus, Note, Void
from CTFd.plugins.l3mon_scoring import values
from CTFd.utils.security.auth import generate_user_token
from scoring_world import dynamic, fixed, make_app, solve, stored_value, studio
from tests.helpers import destroy_ctfd, login_as_user, register_user

BASE = "/api/v1/l3mon/admin/scoring"
REASON = "The checker accepted a wrong answer"
POSTS = [("revoke", {"challenge_id": 1, "reason": REASON}), ("restore", {"challenge_id": 1}), ("bonus", {"team_id": 1, "trp": 5, "message": "ok"}), ("recalculate", {})]


class Api:
    pass


@pytest.fixture()
def api():
    app = make_app()
    a = Api()
    a.app = app
    with app.app_context():
        a.admin = login_as_user(app, "admin")
        dyn, fix = dynamic("dyn"), fixed("fix", value=100)
        a.dyn, a.fix = dyn.id, fix.id
        a.teams = [studio(n) for n in ("alpha", "beta", "gamma")]
        a.team_ids = [t.id for t in a.teams]
        for minute, team in enumerate(a.teams):
            solve(team, dyn, minutes=minute)
        solve(a.teams[0], fix, minutes=5)
        values.recalculate()
        db.session.commit()
        yield a
    destroy_ctfd(app)


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def out(r):
    return r.get_json()


# ---- who may ask -------------------------------------------------------------------------------------------------------------------

def test_a_visitor_and_a_player_are_refused_every_call_and_nothing_changes(api):
    register_user(api.app, name="player", email="player@example.com")
    player = login_as_user(api.app, "player")
    before = (Solves.query.count(), Void.query.count(), Awards.query.count(), Audit.query.count())
    for client in (api.app.test_client(), player):
        r = client.get(BASE, headers={"Accept": "application/json"})
        assert r.status_code in (302, 403)
        for name, payload in POSTS:
            r = client.post(f"{BASE}/{name}", json=payload)
            assert r.status_code in (302, 403), (name, r.status_code)
    assert (Solves.query.count(), Void.query.count(), Awards.query.count(), Audit.query.count()) == before


def test_an_administrators_token_works_for_scripts_and_a_session_without_the_csrf_token_does_not(api):
    token = generate_user_token(Users.query.filter_by(name="admin").first()).value
    script = api.app.test_client()
    r = script.get(BASE, headers={"Authorization": f"Token {token}", "Content-type": "application/json"})
    assert r.status_code == 200 and out(r)["success"] is True
    r = script.post(f"{BASE}/bonus", json={"team_id": api.team_ids[0], "trp": 5, "message": "ok"}, headers={"Authorization": f"Token {token}"})
    assert r.status_code == 200
    raw = json.dumps({"challenge_id": api.dyn, "reason": REASON})
    assert api.admin.post(f"{BASE}/revoke", data=raw, content_type="application/json").status_code == 403
    assert api.admin.post(f"{BASE}/revoke", data=raw, content_type="application/json", headers={"CSRF-Token": "wrong"}).status_code == 403
    assert Void.query.count() == 0


# ---- the view ---------------------------------------------------------------------------------------------------------------------

def test_the_view_shows_every_challenge_against_its_formula_and_ctfds_own_numbers(api):
    r = api.admin.get(BASE)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    data = out(r)["data"]
    by_name = {c["name"]: c for c in data["challenges"]}
    assert by_name["dyn"] == {
        "id": api.dyn, "name": "dyn", "state": "visible", "type": "dynamic", "value": 495, "initial": 500, "floor": 200, "decay": 15, "function": "logarithmic",
        "solves": 3, "held": 3, "wanted": 495, "open_voids": 0,
    }
    assert by_name["fix"] == {
        "id": api.fix, "name": "fix", "state": "visible", "type": "fixed", "value": 100, "initial": None, "floor": None, "decay": None, "function": None,
        "solves": 1, "held": 1, "wanted": 100, "open_voids": 0,
    }
    assert data["voids"] == [] and data["bonuses"] == [] and data["audit"] == []
    assert isinstance(data["now"], int)
    assert data["restore_note"] == "The crew put your solve back; it counts again.", "the page shows what a studio is told when no reason is typed"
    assert [t["name"] for t in data["teams"]] == ["alpha", "beta", "gamma"]
    alpha = data["teams"][0]
    assert set(alpha) == {"id", "name", "captain_id", "banned", "hidden", "members"} and alpha["banned"] is False and alpha["hidden"] is False
    assert len(alpha["members"]) == 2 and set(alpha["members"][0]) == {"id", "name"} and alpha["captain_id"] in {m["id"] for m in alpha["members"]}


def test_a_banned_studio_still_holds_its_solve_but_does_not_count(api):
    from CTFd.models import Teams

    Teams.query.filter_by(id=api.team_ids[1]).update({"banned": True})
    db.session.commit()
    row = {c["name"]: c for c in out(api.admin.get(BASE))["data"]["challenges"]}["dyn"]
    assert (row["solves"], row["held"]) == (2, 3), "revoke sets aside all three; only two move the value"
    assert {t["name"]: t["banned"] for t in out(api.admin.get(BASE))["data"]["teams"]}["beta"] is True


def test_the_view_marks_a_value_that_is_not_its_formulas(api):
    db.session.query(Challenges).filter_by(id=api.dyn).update({"value": 3})
    db.session.commit()
    by_name = {c["name"]: c for c in out(api.admin.get(BASE))["data"]["challenges"]}
    assert (by_name["dyn"]["value"], by_name["dyn"]["wanted"]) == (3, 495)


def test_the_view_carries_no_flag_text_address_or_player_secret(api):
    api.admin.post(f"{BASE}/revoke", json={"challenge_id": api.dyn, "reason": REASON})
    dump = json.dumps(out(api.admin.get(BASE))["data"])
    for forbidden in ("127.0.0.1", "@example.com", "password", '"provided"', '"ip"', '"email"'):
        assert forbidden not in dump, forbidden


# ---- the actions ------------------------------------------------------------------------------------------------------------------

def test_revoke_and_restore_through_the_api(api):
    r = api.admin.post(f"{BASE}/revoke", json={"challenge_id": api.dyn, "reason": REASON})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert out(r)["data"] == {"challenge_id": api.dyn, "name": "dyn", "voided": 3, "teams": ["alpha", "beta", "gamma"], "value_before": 495, "value_after": 500}
    view = out(api.admin.get(BASE))["data"]
    assert {c["name"]: c["open_voids"] for c in view["challenges"]} == {"dyn": 3, "fix": 0}
    assert [(v["challenge"], v["team"], v["outcome"], v["reason"], v["voided_by"]) for v in view["voids"]] == [("dyn", n, "open", REASON, "admin") for n in ("gamma", "beta", "alpha")]
    assert {"id", "challenge_id", "team_id", "user", "solved_at", "voided_at", "restored_at", "restored_by"} <= set(view["voids"][0])
    assert [a["action"] for a in view["audit"]] == ["scoring.revoke"] and view["audit"][0]["actor"] == "admin"
    assert Solves.query.filter_by(challenge_id=api.dyn).count() == 0

    r = api.admin.post(f"{BASE}/restore", json={"challenge_id": api.dyn, "reason": "fixed now"})
    assert r.status_code == 200
    assert out(r)["data"] == {"challenge_id": api.dyn, "name": "dyn", "restored": 3, "skipped": 0, "superseded": 0, "value_before": 500, "value_after": 495}
    view = out(api.admin.get(BASE))["data"]
    assert {v["outcome"] for v in view["voids"]} == {"restored"} and all(v["restored_by"] == "admin" and v["restored_at"] for v in view["voids"])
    assert [a["action"] for a in view["audit"]] == ["scoring.restore", "scoring.revoke"]


def test_bonus_through_the_api(api):
    r = api.admin.post(f"{BASE}/bonus", json={"team_id": api.team_ids[1], "trp": 50, "message": "Found a bug in the lobby"})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    data = out(r)["data"]
    assert (data["team_id"], data["trp"], data["title"], data["scope"]) == (api.team_ids[1], 50, "Bonus +50 TRP", "team")
    view = out(api.admin.get(BASE))["data"]
    assert [(b["team"], b["scope"], b["trp"], b["title"], b["message"], b["given_by"]) for b in view["bonuses"]] == [("beta", "team", 50, "Bonus +50 TRP", "Found a bug in the lobby", "admin")]
    assert view["audit"][0]["action"] == "scoring.bonus"
    member = sorted(api.teams[2].members, key=lambda u: u.id)[1].id
    r = api.admin.post(f"{BASE}/bonus", json={"team_id": api.team_ids[2], "user_id": member, "trp": -10, "message": "Shared a hint"})
    assert r.status_code == 200 and out(r)["data"]["title"] == "Adjustment -10 TRP"
    assert Bonus.query.count() == 2 and Note.query.count() == 2


def test_recalculate_puts_every_value_right_and_says_what_it_changed(api):
    db.session.query(Challenges).filter_by(id=api.dyn).update({"value": 7})
    db.session.commit()
    r = api.admin.post(f"{BASE}/recalculate", json={})
    assert r.status_code == 200
    assert out(r)["data"] == {"changed": [{"id": api.dyn, "name": "dyn", "old": 7, "new": 495}]}
    assert stored_value(api.dyn) == 495
    assert Audit.query.filter_by(action="scoring.recalculate").count() == 1
    again = api.admin.post(f"{BASE}/recalculate", json={})
    assert out(again)["data"] == {"changed": []} and Audit.query.filter_by(action="scoring.recalculate").count() == 1


# ---- bad requests -----------------------------------------------------------------------------------------------------------------

BAD = [
    ("revoke", {"reason": REASON}, "challenge_id", 400),
    ("revoke", {"challenge_id": "x", "reason": REASON}, "challenge_id", 400),
    ("revoke", {"challenge_id": 1}, "reason", 400),
    ("revoke", {"challenge_id": 1, "reason": ""}, "reason", 400),
    ("revoke", {"challenge_id": 1, "reason": "<b>"}, "reason", 400),
    ("revoke", {"challenge_id": 99999, "reason": REASON}, "challenge_id", 404),
    ("revoke", {"challenge_id": 1, "reason": REASON, "reasons": "typo"}, "reasons", 400),
    ("restore", {"challenge_id": 1}, "challenge_id", 409),
    ("restore", {"challenge_id": 99999}, "challenge_id", 404),
    ("restore", {"challenge_id": 1, "why": "typo"}, "why", 400),
    ("bonus", {"team_id": 1, "message": "ok"}, "trp", 400),
    ("bonus", {"team_id": 1, "trp": 0, "message": "ok"}, "trp", 400),
    ("bonus", {"team_id": 1, "trp": 5000, "message": "ok"}, "trp", 400),
    ("bonus", {"team_id": 1, "trp": 5}, "message", 400),
    ("bonus", {"trp": 5, "message": "ok"}, "team_id", 400),
    ("bonus", {"team_id": 99999, "trp": 5, "message": "ok"}, "team_id", 404),
    ("bonus", {"team_id": 1, "user_id": 99999, "trp": 5, "message": "ok"}, "user_id", 400),
    ("bonus", {"team_id": 1, "trp": 5, "message": "ok", "amount": 5}, "amount", 400),
    ("recalculate", {"challenge_id": 1}, "challenge_id", 400),
]


@pytest.mark.parametrize("name, payload, field, status", BAD)
def test_every_bad_request_is_refused_with_the_field_named_and_changes_nothing(api, name, payload, field, status):
    before = (Solves.query.count(), Void.query.count(), Awards.query.count(), Note.query.count(), Audit.query.count())
    r = api.admin.post(f"{BASE}/{name}", json=payload)
    assert r.status_code == status, r.get_data(as_text=True)
    body = out(r)
    assert body["success"] is False and field in body["errors"] and isinstance(body["errors"][field], list), body
    assert r.headers["Cache-Control"] == "no-store"
    assert (Solves.query.count(), Void.query.count(), Awards.query.count(), Note.query.count(), Audit.query.count()) == before


@pytest.mark.parametrize("raw", ["{not json", "[]", "42", '"text"', "null"])
def test_a_body_that_is_not_a_json_object_is_refused(api, raw):
    for name, _ in POSTS:
        r = api.admin.post(f"{BASE}/{name}", data=raw, content_type="application/json", headers={"CSRF-Token": nonce_of(api.admin)})
        assert r.status_code == 400, (name, r.status_code)
        assert out(r)["success"] is False and "body" in out(r)["errors"]
    assert Void.query.count() == 0 and Awards.query.count() == 0


def test_a_second_revoke_and_a_double_bonus_answer_409_through_the_api(api):
    assert api.admin.post(f"{BASE}/revoke", json={"challenge_id": api.dyn, "reason": REASON}).status_code == 200
    assert api.admin.post(f"{BASE}/revoke", json={"challenge_id": api.dyn, "reason": REASON}).status_code == 409
    one = {"team_id": api.team_ids[0], "trp": 5, "message": "ok"}
    assert api.admin.post(f"{BASE}/bonus", json=one).status_code == 200
    r = api.admin.post(f"{BASE}/bonus", json=one)
    assert r.status_code == 409 and "message" in out(r)["errors"]
    assert Awards.query.count() == 1


def test_a_standard_challenge_with_a_scoring_function_is_shown_and_checked_like_a_dynamic_one(api):
    from scoring_world import decaying

    std = decaying("std")
    for minute, team in enumerate(api.teams):
        solve(team, std, minutes=minute)
    db.session.query(Challenges).filter_by(id=std.id).update({"value": 9})
    db.session.commit()
    row = {c["name"]: c for c in out(api.admin.get(BASE))["data"]["challenges"]}["std"]
    assert (row["type"], row["initial"], row["floor"], row["decay"], row["function"]) == ("dynamic", 500, 200, 15, "logarithmic")
    assert (row["value"], row["wanted"], row["solves"]) == (9, 495, 3), "the page would flag it"
    r = api.admin.post(f"{BASE}/recalculate", json={})
    assert out(r)["data"]["changed"] == [{"id": std.id, "name": "std", "old": 9, "new": 495}]


def test_the_view_says_whether_a_challenge_is_visible_to_players_so_the_page_can_warn_before_a_revoke(api):
    from scoring_world import fixed

    fixed("held back", value=10, state="hidden")
    states = {c["name"]: c["state"] for c in out(api.admin.get(BASE))["data"]["challenges"]}
    assert states == {"dyn": "visible", "fix": "visible", "held back": "hidden"}
