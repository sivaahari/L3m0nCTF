"""PROBE, not a test (the file name has no test_ prefix, so a normal run skips it). It measures how CTFd 3.8.8 behaves in the places
SP3 part 3.3 depends on, so the design is written from facts. Run it with the output shown:

    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -s -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring/probe_facts.py
"""
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from CTFd.models import Challenges, Solves, Submissions, Teams, Users, db
from tests.helpers import create_ctfd, destroy_ctfd, gen_flag, login_as_user, register_user

SEEN = []


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


def value_of(cid):
    db.session.rollback()
    return db.session.query(Challenges.value).filter_by(id=cid).scalar()


def standings():
    from CTFd.cache import clear_standings
    from CTFd.utils.scores import get_standings

    clear_standings()
    return [(r.name, int(r.score)) for r in get_standings(admin=True)]


def listeners():
    def after_flush(session, ctx):
        for obj in session.dirty:
            if isinstance(obj, (Teams, Users)):
                h = inspect(obj).attrs
                changed = [k for k in ("banned", "hidden") if h[k].history.has_changes()]
                if changed:
                    SEEN.append(f"flush: {type(obj).__name__} {obj.id} changed {changed}")
        for obj in session.deleted:
            SEEN.append(f"flush: deleted {type(obj).__name__} {getattr(obj, 'id', '?')}")

    def bulk_delete(ctx):
        SEEN.append(f"bulk delete of {ctx.mapper.class_.__name__}")

    def bulk_update(ctx):
        SEEN.append(f"bulk update of {ctx.mapper.class_.__name__}")

    event.listen(Session, "after_flush", after_flush)
    event.listen(Session, "after_bulk_delete", bulk_delete)
    event.listen(Session, "after_bulk_update", bulk_update)


def drain(label):
    log(label, "events:", SEEN[:] or "none")
    SEEN.clear()


def test_probe():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    listeners()
    with app.app_context():
        admin = login_as_user(app, "admin")
        r = admin.post("/api/v1/challenges", json={
            "name": "dyn", "category": "c", "description": "d", "value": 500, "initial": 500, "minimum": 200, "decay": 15,
            "function": "logarithmic", "type": "dynamic", "state": "visible"})
        assert r.status_code == 200, r.get_data(as_text=True)
        cid = r.get_json()["data"]["id"]
        gen_flag(db, cid, content="FLAG-DYN")
        fixed = admin.post("/api/v1/challenges", json={"name": "fix", "category": "c", "description": "d", "value": 100, "type": "standard", "state": "visible"}).get_json()["data"]["id"]
        gen_flag(db, fixed, content="FLAG-FIX")

        clients = {n: make_team_client(app, f"u{n}", f"team{n}") for n in "ABCD"}
        values = []
        for n, c in clients.items():
            res = c.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "FLAG-DYN"}).get_json()["data"]["status"]
            values.append((n, res, value_of(cid)))
        log("value after each solve (A,B,C,D):", values)
        log("standings:", standings())
        drain("after the four solves")

        # 1. CTFd's "mark incorrect"
        sub_b = Solves.query.filter_by(challenge_id=cid, team_id=Users.query.filter_by(name="uB").first().team_id).first()
        sid = sub_b.id
        r = admin.patch(f"/api/v1/submissions/{sid}", json={"type": "incorrect"})
        log("mark incorrect ->", r.status_code, r.get_json().get("success"), "| value now", value_of(cid))
        drain("mark incorrect")
        row = db.session.execute(db.text("select id, type from submissions where id=:i"), {"i": sid}).fetchall()
        row2 = db.session.execute(db.text("select id from solves where id=:i"), {"i": sid}).fetchall()
        log("submission row after mark incorrect:", row, "| solves row:", row2)
        log("standings:", standings())

        # 2. ban a team
        tid_c = Users.query.filter_by(name="uC").first().team_id
        r = admin.patch(f"/api/v1/teams/{tid_c}", json={"banned": True})
        log("ban team C ->", r.status_code, "| value now", value_of(cid), "(formula wants the count of counted solves)")
        drain("ban team C")
        log("standings:", standings())
        r = admin.patch(f"/api/v1/teams/{tid_c}", json={"banned": False})
        drain("unban team C")
        r = admin.patch(f"/api/v1/teams/{tid_c}", json={"hidden": True})
        log("hide team C ->", r.status_code, "| value now", value_of(cid))
        drain("hide team C")
        r = admin.patch(f"/api/v1/teams/{tid_c}", json={"hidden": False})
        drain("unhide team C")

        # 3. delete a team
        tid_d = Users.query.filter_by(name="uD").first().team_id
        before = db.session.query(Solves).filter_by(team_id=tid_d).count()
        r = admin.delete(f"/api/v1/teams/{tid_d}", json={})
        log("delete team D ->", r.status_code, "| its solves before:", before, "after:", db.session.query(Solves).filter_by(team_id=tid_d).count(), "| value now", value_of(cid))
        drain("delete team D")

        # 4. delete a user (user C's account)
        uid_c = Users.query.filter_by(name="uC").first().id
        r = admin.delete(f"/api/v1/users/{uid_c}", json={})
        log("delete user C ->", r.status_code, "| value now", value_of(cid))
        drain("delete user C")

        # 5. delete a submission
        sid_a = Solves.query.filter_by(challenge_id=cid, team_id=Users.query.filter_by(name="uA").first().team_id).first().id
        r = admin.delete(f"/api/v1/submissions/{sid_a}", json={})
        log("delete A's solve submission ->", r.status_code, "| value now", value_of(cid))
        drain("delete submission")
        log("standings:", standings())

        # 6. a Core revoke: delete the solves row only, flip the type
        a = clients["A"]
        res = a.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "FLAG-DYN"}).get_json()["data"]["status"]
        a_team = Users.query.filter_by(name="uA").first().team_id
        s = Solves.query.filter_by(challenge_id=cid, team_id=a_team).first()
        sid = s.id
        log("A solved again:", res, "solve id", sid, "| value", value_of(cid))
        with app.app_context():
            db.session.execute(db.text("delete from solves where id=:i"), {"i": sid})
            db.session.execute(db.text("update submissions set type='discard' where id=:i"), {"i": sid})
            db.session.commit()
        from CTFd.cache import clear_challenges, clear_standings

        clear_standings()
        clear_challenges()
        log("after the core revoke: standings", standings(), "| Solves loaded:", Solves.query.filter_by(id=sid).count(), "| Submissions loaded:", [type(x).__name__ for x in Submissions.query.filter_by(id=sid).all()])
        log("A's score:", Teams.query.get(a_team).get_score(admin=True), "| attempts left view:", a.get(f"/api/v1/challenges/{cid}").get_json()["data"].get("attempts"), "solves:", a.get(f"/api/v1/challenges/{cid}").get_json()["data"].get("solves"))
        res = a.post("/api/v1/challenges/attempt", json={"challenge_id": cid, "submission": "FLAG-DYN"}).get_json()["data"]
        log("A may solve again after a revoke:", res)
        log("submissions of A on this challenge:", db.session.execute(db.text("select id, type from submissions where team_id=:t and challenge_id=:c order by id"), {"t": a_team, "c": cid}).fetchall())

        # 7. awards
        team_a = Teams.query.get(a_team)
        captain = team_a.captain_id
        log("A: captain id", captain, "members", [m.id for m in team_a.members])
        before = team_a.get_score(admin=True)
        r = admin.post("/api/v1/awards", json={"user_id": captain, "team_id": a_team, "name": "Bonus +50 TRP", "description": "", "value": 50, "category": "bonus"})
        log("award ->", r.status_code, r.get_json())
        from CTFd.cache import clear_standings as cs

        cs()
        db.session.expire_all()
        log("A's score before/after the award:", before, Teams.query.get(a_team).get_score(admin=True), "| standings", standings())
        pub = a.get(f"/api/v1/teams/{a_team}/awards").get_json()
        log("public awards list:", pub)

        # 8. the hint-cost message
        hint = admin.post("/api/v1/hints", json={"challenge_id": fixed, "content": "h", "cost": 999999}).get_json()
        log("hint created:", hint.get("success"))
        r = a.post("/api/v1/unlocks", json={"target": hint["data"]["id"], "type": "hints"})
        log("too-dear hint ->", r.status_code, r.get_json())
    destroy_ctfd(app)
