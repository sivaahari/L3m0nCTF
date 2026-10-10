"""The doors: a bonus message and a void reason are for one studio and the crew. No public route says them.

Since SP3 part 3.5 the studio's own bell and Guide DO carry its private lines (that is what they are for): studio A may read them there and
nowhere else; studio B, a visitor and the crew's lists never do.

The world: studio A solved a programme; the crew set the solve aside with a reason (a private line for A) and later gave A a bonus
with a message (another private line). Every route a player or a visitor can reach that lists scores, awards, solves, news, pages or
feeds is then asked as A, as another studio B, as a visitor who is not signed in, and as a crew member, and none of the answers may
contain either marker. A CONTROL shows the checks are not vacuous: the award's public title ("Bonus +50 TRP") IS in A's award list.
Two tamperings (the message put into the award, the reason put into a public notification) must be caught by the same scan, so the
scan is proved able to fail.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
from types import SimpleNamespace

import pytest

from CTFd.models import Awards, Users, db
from CTFd.plugins.l3mon_core.models import Bonus, Note, Void
from CTFd.plugins.l3mon_scoring import bonus, voids
from scoring_world import attempt, fixed, make_app, team_client
from tests.helpers import destroy_ctfd, gen_notification, login_as_user

MARKERS = ("MARKERVOID", "MARKERBONUS", "checker broke", "found a bug")
FLAG = "FLAG-DOOR"
GUIDE, EPG = "/api/v1/l3mon/guide", "/api/v1/l3mon/guide/epg"
SCOREBOARD, ROWS = "/api/v1/l3mon/scoreboard", "/api/v1/l3mon/scoreboard/rows"
ITS_OWN = {"studio A": {"/api/v1/notifications", GUIDE}}  # the studio's own bell and Guide are where it reads its private lines


@pytest.fixture()
def world():
    app = make_app()
    with app.app_context():
        cid = fixed("door", value=100, flag=FLAG).id
        admin = login_as_user(app, "admin")
        a = team_client(app, "alice", "studio-a")
        b = team_client(app, "bob", "studio-b")
        assert attempt(a, cid, FLAG) == "correct"
        ids = SimpleNamespace(
            cid=cid, a_user=Users.query.filter_by(name="alice").first().id, b_user=Users.query.filter_by(name="bob").first().id,
            a_team=Users.query.filter_by(name="alice").first().team_id, b_team=Users.query.filter_by(name="bob").first().team_id,
        )
        voids.revoke(cid, "MARKERVOID the checker broke", actor=Users.query.filter_by(name="admin").first())
        bonus.give(ids.a_team, trp=50, message="MARKERBONUS found a bug in the lobby", actor=Users.query.filter_by(name="admin").first())
        visitor = app.test_client()
        yield SimpleNamespace(app=app, admin=admin, a=a, b=b, visitor=visitor, ids=ids)
    destroy_ctfd(app)


def routes(ids):
    return [
        "/api/v1/notifications", "/notifications",
        f"/api/v1/teams/{ids.a_team}", f"/api/v1/teams/{ids.a_team}/awards", f"/api/v1/teams/{ids.a_team}/solves", f"/api/v1/teams/{ids.a_team}/fails",
        f"/api/v1/users/{ids.a_user}", f"/api/v1/users/{ids.a_user}/awards", f"/api/v1/users/{ids.a_user}/solves", f"/api/v1/users/{ids.a_user}/fails",
        "/api/v1/users/me", "/api/v1/users/me/awards", "/api/v1/users/me/solves", "/api/v1/users/me/fails", "/api/v1/users/me/submissions",
        "/api/v1/teams/me", "/api/v1/teams/me/awards", "/api/v1/teams/me/solves", "/api/v1/teams/me/fails",
        "/api/v1/scoreboard", "/api/v1/scoreboard/top/10", "/scoreboard",
        f"/teams/{ids.a_team}", f"/users/{ids.a_user}", "/team", "/user", "/challenges",
        "/api/v1/challenges", f"/api/v1/challenges/{ids.cid}", f"/api/v1/challenges/{ids.cid}/solves",
        "/ctftime/standings.json", "/ctftime/final-standings.json",
        GUIDE, EPG, SCOREBOARD, ROWS,
    ]


def scan(world):
    """-> [(viewer, route, marker)] for every marker found in any answer."""
    found = []
    viewers = {"studio A": world.a, "studio B": world.b, "visitor": world.visitor, "crew": world.admin}
    extra_for_crew = ["/api/v1/awards", "/api/v1/submissions", "/api/v1/users", "/api/v1/teams"]
    for viewer, client in viewers.items():
        for route in routes(world.ids) + (extra_for_crew if viewer == "crew" else []):
            if route in ITS_OWN.get(viewer, ()):
                continue
            body = client.get(route).get_data(as_text=True)
            found += [(viewer, route, m) for m in MARKERS if m.lower() in body.lower()]
    for viewer in ("studio A", "studio B", "visitor"):
        client = viewers[viewer]
        for table in ("l3mon_note", "l3mon_bonus", "l3mon_void", "l3mon_audit"):
            target = {"l3mon_note": Note, "l3mon_bonus": Bonus, "l3mon_void": Void}.get(table)
            row_id = target.query.first().id if target is not None else 1
            r = client.post("/api/v1/unlocks", json={"target": row_id, "type": table})
            found += [(viewer, f"POST unlocks type={table} -> {r.status_code}", m) for m in MARKERS if m.lower() in r.get_data(as_text=True).lower()]
    return found


def test_the_setup_really_holds_both_private_lines(world):
    assert {n.title for n in Note.query.filter_by(team_id=world.ids.a_team).all()} == {"Solve voided", "Bonus +50 TRP"}
    assert Void.query.one().reason.startswith("MARKERVOID") and Bonus.query.one().message.startswith("MARKERBONUS")


def test_the_control_the_awards_public_title_is_in_the_list(world):
    for client in (world.a, world.b, world.visitor):
        listed = client.get(f"/api/v1/teams/{world.ids.a_team}/awards").get_json()["data"]
        assert [(a["name"], a["value"], a["description"]) for a in listed] == [("Bonus +50 TRP", 50, "")]


def test_no_route_says_a_message_or_a_reason(world):
    assert scan(world) == []


def test_control_the_studio_reads_its_own_private_lines_in_its_bell_and_its_guide_and_in_no_other_studios(world):
    for route in ITS_OWN["studio A"]:
        text = world.a.get(route).get_data(as_text=True)
        assert "MARKERVOID" in text and "MARKERBONUS" in text, route
        for other in (world.b, world.visitor, world.admin):
            assert "MARKER" not in other.get(route).get_data(as_text=True), route


def test_the_scan_catches_a_message_put_into_the_award(world):
    Awards.query.update({"description": "MARKERBONUS found a bug in the lobby"})
    db.session.commit()
    leaks = scan(world)
    assert any(route == f"/api/v1/teams/{world.ids.a_team}/awards" for _, route, _ in leaks), "the scan can fail"


def test_the_scan_catches_a_reason_put_into_a_public_notification(world):
    gen_notification(db, title="Solve voided", content="MARKERVOID the checker broke")
    leaks = scan(world)
    assert any(route == "/api/v1/notifications" for _, route, _ in leaks), "the scan can fail"


def test_the_scan_reaches_real_content_not_just_login_redirects(world):
    """Each viewer is answered by most routes with content, so 'no marker found' is not 'nothing was looked at'."""
    viewers = {"studio A": world.a, "studio B": world.b, "visitor": world.visitor, "crew": world.admin}
    ok = {name: sum(client.get(route).status_code == 200 for route in routes(world.ids)) for name, client in viewers.items()}
    print("ROUTES ANSWERED 200:", ok, "of", len(routes(world.ids)))
    assert ok["studio A"] >= 24 and ok["studio B"] >= 24 and ok["crew"] >= 24
    assert ok["visitor"] >= 5, "a visitor reaches the public ones (the scoreboard, the feeds)"
