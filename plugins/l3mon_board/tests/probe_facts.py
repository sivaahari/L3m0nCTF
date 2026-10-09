"""PROBE, not a test (the file name has no test_ prefix, so a normal run skips it). It measures how CTFd 3.8.8 answers a player in the
places SP3 part 3.4 depends on (before the start, after the end, a pause, tries, the freeze, a studio with no score), so the design is
written from facts. Run it with the output shown:

    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -s -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board/probe_facts.py
"""
import datetime
import json
import time


from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Solves, Teams, Users, db
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, login_as_user, register_user


def log(*parts):
    print("PROBE", *parts)


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


def show(label, r):
    body = r.get_data(as_text=True)
    log(label, r.status_code, r.headers.get("Content-Type"), body[:300].replace("\n", " "))


def test_probe():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        base = int(time.time())
        chal = gen_challenge(db, name="probe", value=100, state="visible")
        limited = gen_challenge(db, name="limited", value=100, state="visible", max_attempts=2)
        gen_flag(db, chal.id, content="F")
        gen_flag(db, limited.id, content="F")
        cid, lid = chal.id, limited.id
        alice = make_team_client(app, "alice", "studio-a")
        make_team_client(app, "bob", "studio-b")
        clear_challenges()

        log("--- BEFORE THE START (start in 1000 s)")
        set_config("start", base + 1000)
        set_config("end", base + 5000)
        show("GET challenge", alice.get(f"/api/v1/challenges/{cid}"))
        show("GET missing", alice.get("/api/v1/challenges/99999"))
        show("GET list", alice.get("/api/v1/challenges"))
        show("POST attempt right", alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"}))
        show("POST attempt missing", alice.post("/api/v1/challenges/attempt", json={"challenge_id": 99999, "submission": "F"}))

        log("--- LIVE")
        set_config("start", base - 1000)
        set_config("end", base + 5000)
        show("POST wrong", alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "no"}))
        show("POST wrong limited 1", alice.post("/api/v1/challenges/attempt", json={"challenge_id": lid, "submission": "no"}))
        show("POST wrong limited 2", alice.post("/api/v1/challenges/attempt", json={"challenge_id": lid, "submission": "no"}))
        show("POST wrong limited 3 (no tries)", alice.post("/api/v1/challenges/attempt", json={"challenge_id": lid, "submission": "no"}))
        show("POST right on the locked-out one", alice.post("/api/v1/challenges/attempt", json={"challenge_id": lid, "submission": "F"}))

        log("--- PAUSED")
        set_config("paused", True)
        show("GET challenge paused", alice.get(f"/api/v1/challenges/{cid}"))
        show("POST attempt paused", alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"}))
        set_config("paused", False)

        log("--- ENDED, view_after_ctf off")
        set_config("start", base - 5000)
        set_config("end", base - 10)
        log("view_after_ctf default:", repr(db.session.execute(db.text("select value from config where `key`='view_after_ctf'")).scalar() if False else "n/a"))
        show("GET challenge ended", alice.get(f"/api/v1/challenges/{cid}"))
        show("GET list ended", alice.get("/api/v1/challenges"))
        show("POST attempt ended", alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"}))

        log("--- ENDED, view_after_ctf on")
        set_config("view_after_ctf", True)
        show("GET challenge ended+view", alice.get(f"/api/v1/challenges/{cid}"))
        before = Solves.query.count()
        r = alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"})
        show("POST right ended+view", r)
        log("solves recorded by that:", Solves.query.count() - before)
        r = alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "wrong"})
        show("POST wrong ended+view", r)
        set_config("view_after_ctf", False)

        log("--- place of a studio with no score, standings size")
        set_config("start", base - 1000)
        set_config("end", base + 5000)
        clear_standings()
        a, b = Teams.query.filter_by(name="studio-a").first(), Teams.query.filter_by(name="studio-b").first()
        log("alice place/score:", a.get_place(), a.get_score(), "bob place/score:", b.get_place(), b.get_score())
        from CTFd.utils.scores import get_standings

        log("standings rows:", [(r.name, int(r.score)) for r in get_standings()])
        # a solve for alice
        alice.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"})
        clear_standings()
        a, b = Teams.query.filter_by(name="studio-a").first(), Teams.query.filter_by(name="studio-b").first()
        log("after alice solves: alice place/score:", a.get_place(), a.get_score(), "bob place/score:", b.get_place(), b.get_score())
        log("standings rows:", [(r.name, int(r.score)) for r in get_standings()])

        log("--- the freeze and the solve counts")
        from CTFd.utils.challenges import get_solve_counts_for_challenges

        late_user = Users.query.filter_by(name="bob").first()
        set_config("freeze", base - 100)  # frozen a hundred seconds ago
        db.session.add(Solves(user_id=late_user.id, team_id=late_user.team_id, challenge_id=cid, ip="127.0.0.1", provided="F", date=datetime.datetime.utcfromtimestamp(base - 50)))
        db.session.commit()
        clear_challenges()
        clear_standings()
        log("solve counts (player view):", get_solve_counts_for_challenges(admin=False), "admin view:", get_solve_counts_for_challenges(admin=True))
        log("alice place/score with freeze:", Teams.query.filter_by(name="studio-a").first().get_place(), Teams.query.filter_by(name="studio-a").first().get_score())
        log("bob place/score with freeze:", Teams.query.filter_by(name="studio-b").first().get_place(), Teams.query.filter_by(name="studio-b").first().get_score())
        log("bob score admin=True:", Teams.query.filter_by(name="studio-b").first().get_score(admin=True))
        set_config("freeze", None)

        log("--- unauthenticated and the visibility setting")
        anonymous = app.test_client()
        show("anon GET challenge", anonymous.get(f"/api/v1/challenges/{cid}"))
        show("anon POST attempt", anonymous.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"}))
        set_config("challenge_visibility", "public")
        show("anon GET challenge (public)", anonymous.get(f"/api/v1/challenges/{cid}"))
        set_config("challenge_visibility", "private")

        log("--- an account with no studio")
        register_user(app, name="carol", email="carol@example.com")
        carol = login_as_user(app, "carol")
        show("carol GET challenge", carol.get(f"/api/v1/challenges/{cid}"))
        show("carol GET list", carol.get("/api/v1/challenges"))
        show("carol POST attempt", carol.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "F"}))

        log("--- the admin")
        admin = login_as_user(app, "admin")
        show("admin GET challenge", admin.get(f"/api/v1/challenges/{cid}"))
        log("keys of the challenge answer:", sorted(json.loads(admin.get(f"/api/v1/challenges/{cid}").get_data(as_text=True))["data"].keys()))
        log("keys of one list row:", sorted(json.loads(alice.get("/api/v1/challenges").get_data(as_text=True))["data"][0].keys()))
    destroy_ctfd(app)
