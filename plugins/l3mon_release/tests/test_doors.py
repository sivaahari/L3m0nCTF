"""The doors: a programme that is not on air does not exist for a player, on any route.

The world: team A solved a programme (after unlocking its hint, failing it once, rating it, and getting a signed link to its file),
and the crew then pulled it back. Team B never saw it. Every route that could mention it is asked, as team A, as team B and (for
files) as a visitor holding a link. Each answer must equal the answer for something that never existed, and each check carries a
CONTROL: the same request about a programme that IS on air must succeed, so a check cannot pass by being refused for another
reason. Every guard is then switched off in turn, and its check must fail: a test that cannot fail proves nothing.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_release
"""
import calendar
import datetime
import os
from types import SimpleNamespace

import pytest
from freezegun import freeze_time

from CTFd.models import Challenges, Submissions, Tags, Users, db
from CTFd.plugins.l3mon_release import guards
from CTFd.plugins.l3mon_release.reconcile import clear_pull_back_handlers
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_file, gen_flag, gen_hint, login_as_user, register_user

PROGRAMMES = "/api/v1/l3mon/admin/programmes"
RELEASE = "/api/v1/l3mon/admin/release"
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())
T_LIVE = T_START + 3600

SECRET = {"name": "Wombat Ledger", "category": "Marsupial", "description": "burrow-notes"}
OPEN = {"name": "Koala Lantern", "category": "Arboreal", "description": "eucalyptus-notes"}
MARKERS = ("Wombat", "Marsupial", "burrow", "den-clue", "wombat.txt", "FLAG-SECRET")
MISSING_FILE = "/files/00000000000000000000000000000000/never-existed.txt"


def clock():
    return freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE))


def leaks(text):
    return [m for m in MARKERS if m.lower() in text.lower()]


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def make_team_client(app, user, team):
    register_user(app, name=user, email=f"{user}@example.com")
    client = login_as_user(app, user)
    client.get("/team")
    r = client.post("/teams/new", data={"name": team, "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 302, r.status_code
    return client


def put_file(app, location, text):
    full = os.path.join(app.config["UPLOAD_FOLDER"], location)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as handle:
        handle.write(text)


def build_world(app):
    """Everything is done on the day of the round, with a clock frozen at 10:00 IST (the caller holds the clock)."""
    clear_pull_back_handlers()
    set_config("start", T_START)
    set_config("end", T_START + 24 * 3600)
    admin = login_as_user(app, "admin")
    secret = gen_challenge(db, state="hidden", value=321, **SECRET)
    other = gen_challenge(db, state="hidden", value=111, **OPEN)
    third = gen_challenge(db, state="hidden", value=50, name="Quokka Spare", category="Spare", description="spare-notes")
    gen_flag(db, secret.id, content="FLAG-SECRET")
    gen_flag(db, other.id, content="FLAG-OPEN")
    db.session.add(Tags(challenge_id=secret.id, value="burrow-tag"))
    secret_hint = gen_hint(db, secret.id, content="den-clue", cost=0)
    open_hint = gen_hint(db, other.id, content="branch-clue", cost=0)
    loc_secret, loc_open = "a" * 32 + "/wombat.txt", "b" * 32 + "/koala.txt"
    for loc, text in ((loc_secret, "secret body"), (loc_open, "open body")):
        put_file(app, loc, text)
    gen_file(db, loc_secret, challenge_id=secret.id)
    gen_file(db, loc_open, challenge_id=other.id)
    db.session.commit()
    ids = SimpleNamespace(secret=secret.id, open=other.id, third=third.id, secret_hint=secret_hint.id, open_hint=open_hint.id)

    r = admin.put(PROGRAMMES, json={
        "channels": [{"slug": "street", "name": "Mighty Street", "position": 1}],
        "programmes": [
            {"challenge_id": ids.secret, "channel": "street", "cell": 0, "number": 101, "slug": "wombat"},
            {"challenge_id": ids.open, "channel": "street", "cell": 1, "number": 102, "slug": "koala"},
            {"challenge_id": ids.third, "channel": "street", "cell": 2, "number": 103, "slug": "spare"},
        ],
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    from CTFd.plugins.l3mon_core.models import Channel, Programme

    release = [{"kind": "channel", "id": Channel.query.one().id, "mode": "release"}] + [{"kind": "programme", "id": p.id, "mode": "release"} for p in Programme.query.all()]
    assert admin.put(RELEASE, json={"changes": release}).status_code == 200

    alice = make_team_client(app, "alice", "team-a")
    bob = make_team_client(app, "bob", "team-b")
    uid_a = Users.query.filter_by(name="alice").first().id
    tid_a = Users.query.filter_by(name="alice").first().team_id

    # while it was on air, team A used it fully
    detail = alice.get(f"/api/v1/challenges/{ids.secret}").get_json()["data"]
    secret_url = detail["files"][0]
    open_url = alice.get(f"/api/v1/challenges/{ids.open}").get_json()["data"]["files"][0]
    assert alice.post("/api/v1/unlocks", json={"target": ids.secret_hint, "type": "hints"}).status_code == 200
    assert alice.post("/api/v1/unlocks", json={"target": ids.open_hint, "type": "hints"}).status_code == 200
    assert alice.post("/api/v1/challenges/attempt", json={"challenge_id": ids.secret, "submission": "wrong"}).get_json()["data"]["status"] == "incorrect"
    assert alice.post("/api/v1/challenges/attempt", json={"challenge_id": ids.secret, "submission": "FLAG-SECRET"}).get_json()["data"]["status"] == "correct"
    assert alice.post("/api/v1/challenges/attempt", json={"challenge_id": ids.open, "submission": "FLAG-OPEN"}).get_json()["data"]["status"] == "correct"
    assert alice.put(f"/api/v1/challenges/{ids.secret}/ratings", json={"value": 1, "review": "burrow-review"}).status_code == 200

    # and then the crew pulled it back
    Programme_secret = Programme.query.filter_by(challenge_id=ids.secret).one()
    assert admin.put(RELEASE, json={"changes": [{"kind": "programme", "id": Programme_secret.id, "mode": "withhold"}], "reason": "found a leak"}).status_code == 200
    assert Challenges.query.get(ids.secret).state == "hidden" and Challenges.query.get(ids.open).state == "visible"
    return SimpleNamespace(
        app=app, admin=admin, alice=alice, bob=bob, anonymous=app.test_client(), ids=ids, uid_a=uid_a, tid_a=tid_a,
        secret_url=secret_url, open_url=open_url, secret_plain=f"/files/{loc_secret}", open_plain=f"/files/{loc_open}",
    )


def _diff(a, b):
    import difflib

    x, y = a.get_data(as_text=True).splitlines(), b.get_data(as_text=True).splitlines()
    return " / ".join(line for line in difflib.unified_diff(x, y, lineterm="", n=0) if not line.startswith(("---", "+++", "@@")))[:300]


def call(client, method, path, body=None):
    fn = getattr(client, method.lower())
    if body is not None:
        return fn(path, json=body)
    return fn(path)


def same_answer(a, b, a_path="", b_path=""):
    """The same status and the same body, ignoring the request path that CTFd's error pages echo back."""

    def body(r, path):
        text = r.get_data(as_text=True)
        for needle in sorted(path.split("|"), key=len, reverse=True):  # the full address with its query, then the bare path
            text = text.replace(needle, "PATH") if needle else text
        return text

    return a.status_code == b.status_code and body(a, a_path) == body(b, b_path)


# ---- the checks (each returns normally when the door is shut, and raises AssertionError when it is open) ------------------------

def check_id_doors(w):
    """CTFd's own routes by id: with the challenge `hidden` they must answer exactly as for an id that never existed."""
    i = w.ids
    doors = [  # method, path template, body template, viewers the control applies to
        ("GET", "/api/v1/challenges/{c}", None, ("alice", "bob")),
        ("GET", "/api/v1/challenges/{c}/solves", None, ("alice", "bob")),
        ("GET", "/api/v1/challenges/{c}/solution", None, ("alice", "bob")),
        ("GET", "/api/v1/hints/{h}", None, ("alice", "bob")),
        ("POST", "/api/v1/unlocks", {"target": "{h}", "type": "hints"}, ("bob",)),
        ("POST", "/api/v1/challenges/attempt", {"challenge_id": "{c}", "submission": "FLAG-SECRET"}, ("alice", "bob")),
        ("PUT", "/api/v1/challenges/{c}/ratings", {"value": 1, "review": "x"}, ("alice",)),
    ]

    def fill(value, c, h):
        if isinstance(value, dict):
            return {k: fill(v, c, h) for k, v in value.items()}
        if isinstance(value, str):
            return {"{c}": c, "{h}": h}.get(value, value.replace("{c}", str(c)).replace("{h}", str(h)))
        return value

    submissions_before = Submissions.query.count()
    for name in ("alice", "bob"):
        viewer = getattr(w, name)
        for method, path, body, controls in doors:
            real_path, gone_path = fill(path, i.secret, i.secret_hint), fill(path, 99999, 99999)
            real = call(viewer, method, real_path, fill(body, i.secret, i.secret_hint))
            gone = call(viewer, method, gone_path, fill(body, 99999, 99999))
            assert same_answer(real, gone, real_path, gone_path), f"{name} {method} {path}: {real.status_code} vs {gone.status_code} for an id that never existed"
            assert not leaks(real.get_data(as_text=True)), f"{name} {method} {path} leaks {leaks(real.get_data(as_text=True))}"
            if name in controls:
                control_body = fill(body, i.open, i.open_hint)
                if method == "POST" and path.endswith("/attempt"):
                    control_body = {"challenge_id": i.open, "submission": "FLAG-OPEN"}
                control = call(viewer, method, fill(path, i.open, i.open_hint), control_body)
                assert control.status_code == 200 or (method == "POST" and control.status_code == 400), f"control {name} {method} {path}: {control.status_code}"
                assert not same_answer(control, gone, fill(path, i.open, i.open_hint), gone_path), f"control {name} {method} {path} is refused like a missing id, so this check proves nothing"
    assert Submissions.query.count() == submissions_before + 1, "the control flag may be recorded once (bob's correct one); nothing is recorded for the withheld programme"


LIST_PATHS = [  # path, viewer who may use it
    ("/api/v1/teams/{t}/solves", "bob"), ("/api/v1/teams/{t}/awards", "bob"), ("/api/v1/teams/{t}/fails", "bob"),
    ("/api/v1/users/{u}/solves", "bob"), ("/api/v1/users/{u}/awards", "bob"), ("/api/v1/users/{u}/fails", "bob"),
    ("/api/v1/teams/me/solves", "alice"), ("/api/v1/teams/me/awards", "alice"), ("/api/v1/teams/me/fails", "alice"),
    ("/api/v1/users/me/solves", "alice"), ("/api/v1/users/me/awards", "alice"), ("/api/v1/users/me/fails", "alice"),
    ("/teams/{t}", "bob"), ("/users/{u}", "bob"), ("/team", "alice"), ("/user", "alice"),
]


def check_list_doors(w):
    for template, who in LIST_PATHS:
        path = template.format(t=w.tid_a, u=w.uid_a)
        for name in {who, "alice"}:
            r = call(getattr(w, name), "GET", path)
            assert r.status_code == 200, (name, path, r.status_code)
            text = r.get_data(as_text=True)
            assert not leaks(text), f"{name} GET {path} shows {leaks(text)}"
    # controls: what IS on air is still listed, for the same people on the same routes
    for path, needle in (
        (f"/api/v1/teams/{w.tid_a}/solves", "Koala Lantern"), (f"/api/v1/users/{w.uid_a}/solves", "Koala Lantern"),
        (f"/api/v1/teams/{w.tid_a}/awards", "Koala Lantern"), (f"/api/v1/users/{w.uid_a}/awards", "Koala Lantern"),
        (f"/teams/{w.tid_a}", "Koala Lantern"), (f"/users/{w.uid_a}", "Koala Lantern"),
    ):
        text = call(w.bob, "GET", path).get_data(as_text=True)
        assert needle in text, f"control: bob should still see the programme that is on air in {path}"
    for path in ("/api/v1/teams/me/solves", "/api/v1/users/me/solves", "/team", "/user"):
        assert "Koala Lantern" in call(w.alice, "GET", path).get_data(as_text=True), f"control: alice's own {path}"


def check_file_door(w):
    for name in ("alice", "bob", "anonymous"):
        viewer = getattr(w, name)
        gone = call(viewer, "GET", MISSING_FILE)  # the error page names the visitor, so each visitor is compared with their own
        assert gone.status_code == 404
        for url in (w.secret_url, w.secret_plain):
            if name == "anonymous" and "token=" not in url:
                continue
            real = call(viewer, "GET", url)
            assert real.status_code == 404 and same_answer(real, gone, url + "|" + url.split("?")[0], MISSING_FILE), f"{name} GET {url[:60]}: {real.status_code} {_diff(real, gone)}"
    for name, url in (("alice", w.open_url), ("bob", w.open_url), ("anonymous", w.open_url), ("bob", w.open_plain)):
        control = call(getattr(w, name), "GET", url)
        assert control.status_code == 200 and control.get_data() == b"open body", f"control: {name} should still get the file that is on air ({control.status_code})"


def check_share_door(w):
    for challenge_id in (w.ids.secret, w.ids.open):  # the feature is closed outright, for what is on air too
        r = call(w.bob, "POST", "/api/v1/shares", {"type": "solve", "user_id": w.uid_a, "challenge_id": challenge_id})
        assert r.status_code == 404, f"a share can be made for challenge {challenge_id}: {r.status_code}"
    link = f"/share/solve?user_id={w.uid_a}&challenge_id={w.ids.secret}&mac={_mac(w.uid_a, w.ids.secret)}"
    r = call(w.anonymous, "GET", link)
    assert r.status_code == 404 and not leaks(r.get_data(as_text=True)), f"a public share link answers {r.status_code}"


def _mac(user_id, challenge_id):
    from CTFd.utils.security.signing import hmac

    return hmac(f"solve-{user_id}-{challenge_id}")


def check_scoreboard(w):
    for path in ("/api/v1/scoreboard", "/api/v1/scoreboard/top/10"):
        r = call(w.bob, "GET", path)
        assert r.status_code == 200 and not leaks(r.get_data(as_text=True)), path
    top = call(w.bob, "GET", "/api/v1/scoreboard/top/10").get_json()["data"]
    shown = {s["challenge_id"] for team in top.values() for s in team["solves"]}
    assert w.ids.secret not in shown, "the id of a programme that is not on air is in the score history"
    assert w.ids.open in shown, "control: the id of a programme that is on air stays in it"
    steps = [s["value"] for team in top.values() for s in team["solves"]]
    assert 321 in steps, "the points stay: they were earned, and the team's total still includes them"


UNLOCK_TYPES = ["challenges", "l3mon_programme", "l3mon_channel", "l3mon_audit", "l3mon_void", "users", "teams", "flags", "tags", "nonsense"]


def check_unlock_types(w):
    """CTFd's unlock route takes `type` as ANY table name and checks the challenge's state only for types that have one, so a
    withheld id could be told from a missing one, and the size of our tables counted. Both answers must be CTFd's own 404."""
    for name in ("alice", "bob"):
        viewer = getattr(w, name)
        baseline = call(viewer, "POST", "/api/v1/unlocks", {"type": "hints", "target": 99999})
        assert baseline.status_code == 404, baseline.status_code
        for kind in UNLOCK_TYPES:
            for target in (w.ids.secret, w.ids.open, 1, 99999):
                r = call(viewer, "POST", "/api/v1/unlocks", {"type": kind, "target": target})
                assert same_answer(r, baseline, "/api/v1/unlocks", "/api/v1/unlocks"), f"{name}: unlock type {kind!r} target {target} answered {r.status_code}: {r.get_data(as_text=True)[:80]}"


def check_next_id(w):
    """An administrator can point a challenge's 'next challenge' at any other one; the id must not reach a player unless that
    challenge is on air for them."""
    Challenges.query.filter_by(id=w.ids.open).update({"next_id": w.ids.secret})
    db.session.commit()
    for name in ("alice", "bob"):
        shown = call(getattr(w, name), "GET", f"/api/v1/challenges/{w.ids.open}").get_json()["data"]
        assert shown["next_id"] is None, f"{name} is told the id of a programme that is not on air: {shown['next_id']}"
    Challenges.query.filter_by(id=w.ids.open).update({"next_id": w.ids.third})
    db.session.commit()
    for name in ("alice", "bob"):
        shown = call(getattr(w, name), "GET", f"/api/v1/challenges/{w.ids.open}").get_json()["data"]
        assert shown["next_id"] == w.ids.third, "control: the id of a programme that is on air stays"


def check_award_matching(w):
    """A hint award is matched to its challenge through the hint id in its name; one that cannot be matched is dropped, one for a
    challenge on air stays, and an award that is not a hint award is untouched."""
    from CTFd.models import Awards

    for name, category in (("Hint 99999", "hints"), ("Hint", "hints"), ("not-a-hint-name", "hints"), ("Bonus", "bonus")):
        db.session.add(Awards(user_id=w.uid_a, team_id=w.tid_a, name=name, description=f"d-{name}", value=0, category=category))
    db.session.commit()
    text = call(w.bob, "GET", f"/api/v1/teams/{w.tid_a}/awards").get_data(as_text=True)
    assert "Hint 99999" not in text and "not-a-hint-name" not in text and '"Hint"' not in text, "an award that cannot be matched must not be shown"
    assert "Koala Lantern" in text, "control: the hint award for the programme on air is shown"
    assert '"Bonus"' in text, "an award that is not a hint award is untouched"


def check_crew_sees_everything(w):
    assert "Wombat" in call(w.admin, "GET", f"/api/v1/teams/{w.tid_a}/solves").get_data(as_text=True)
    assert call(w.admin, "GET", f"/api/v1/challenges/{w.ids.secret}").status_code == 200
    assert call(w.admin, "GET", w.secret_plain).get_data() == b"secret body"
    assert "Wombat" in call(w.admin, "GET", f"/teams/{w.tid_a}").get_data(as_text=True)


# ---- the tests -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def world():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    app.permanent_session_lifetime = datetime.timedelta(days=3650)
    with app.app_context(), clock():
        guards.ON.update({"lists": True, "files": True, "shares": True, "scoreboard": True, "unlocks": True, "next_id": True})
        yield build_world(app)
        guards.ON.update({"lists": True, "files": True, "shares": True, "scoreboard": True, "unlocks": True, "next_id": True})
    destroy_ctfd(app)


def test_the_routes_by_id_answer_as_for_an_id_that_never_existed(world):
    check_id_doors(world)


def test_the_solve_award_and_profile_lists_never_name_a_programme_that_is_not_on_air(world):
    check_list_doors(world)


def test_a_file_of_a_programme_that_is_not_on_air_is_gone_with_or_without_a_link_made_earlier(world):
    check_file_door(world)


def test_social_sharing_is_closed_outright(world):
    check_share_door(world)


def test_the_scoreboard_never_names_a_programme_that_is_not_on_air(world):
    check_scoreboard(world)


def test_the_crew_still_sees_everything(world):
    check_crew_sees_everything(world)


# ---- each guard, switched off, must make its check fail ---------------------------------------------------------------------

def test_with_the_derived_state_undone_the_id_doors_open(world):
    Challenges.query.filter_by(id=world.ids.secret).update({"state": "visible"})  # what a missing reconcile would leave
    db.session.commit()
    with pytest.raises(AssertionError):
        check_id_doors(world)


def test_an_unlock_of_any_other_kind_of_row_is_a_404_like_a_missing_id(world):
    check_unlock_types(world)


def test_the_next_challenge_link_never_names_a_programme_that_is_not_on_air(world):
    check_next_id(world)


def test_hint_awards_that_cannot_be_matched_are_dropped_and_other_awards_are_untouched(world):
    check_award_matching(world)


@pytest.mark.parametrize(
    "guard,check",
    [("lists", check_list_doors), ("files", check_file_door), ("shares", check_share_door), ("scoreboard", check_scoreboard),
     ("unlocks", check_unlock_types), ("next_id", check_next_id)],
)
def test_with_a_guard_switched_off_its_check_fails(world, guard, check):
    check(world)  # shut
    guards.ON[guard] = False
    try:
        with pytest.raises(AssertionError):
            check(world)  # open
    finally:
        guards.ON[guard] = True
