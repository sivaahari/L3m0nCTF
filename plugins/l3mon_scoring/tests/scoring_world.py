"""Shared helpers for the scoring tests: a team-mode app, challenges of both kinds, studios, solves at chosen times and the numbers
CTFd itself reports (standings, a studio's score, a challenge's stored value).

Not a test file. Imported by name from the tests next to it.
"""
import datetime

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import Challenges, Solves, Teams, Users, db
from CTFd.plugins.dynamic_challenges import DynamicChallenge
from tests.helpers import create_ctfd, gen_challenge, gen_flag, gen_team, login_as_user, register_user

T0 = datetime.datetime(2026, 11, 28, 4, 0, 0)  # an hour into the round


def make_app():
    return create_ctfd(user_mode="teams", enable_plugins=True)


def dynamic(name="dyn", initial=500, minimum=200, decay=15, function="logarithmic", state="visible", flag=None):
    challenge = DynamicChallenge(
        name=name, category="cat", description="d", initial=initial, minimum=minimum, decay=decay, function=function,
        state=state, type="dynamic",
    )
    db.session.add(challenge)
    db.session.commit()
    if flag:
        gen_flag(db, challenge.id, content=flag)
    clear_challenges()
    return challenge


def fixed(name="fix", value=100, state="visible", flag=None):
    challenge = gen_challenge(db, name=name, value=value, state=state)
    if flag:
        gen_flag(db, challenge.id, content=flag)
    return challenge


def studio(name, members=2):
    """A studio of `members` accounts; the first is the captain."""
    team = gen_team(db, name=name, email=f"{name}@example.com", member_count=members)
    return team


def solve(team, challenge, minutes=0, member=0):
    """A correct submission by one of the studio's members, `minutes` after T0 (so ties are ours to arrange)."""
    user = sorted(team.members, key=lambda u: u.id)[member]
    row = Solves(user_id=user.id, team_id=team.id, challenge_id=challenge.id, ip="127.0.0.1", provided="right", date=T0 + datetime.timedelta(minutes=minutes))
    db.session.add(row)
    db.session.commit()
    clear_standings()
    clear_challenges()
    return row


def stored_value(challenge_id) -> int:
    db.session.rollback()
    return db.session.query(Challenges.value).filter_by(id=challenge_id).scalar()


def standings():
    """[(team name, TRP)] as CTFd ranks them for an administrator (every studio, in order)."""
    from CTFd.utils.scores import get_standings

    clear_standings()
    return [(r.name, int(r.score)) for r in get_standings(admin=True)]


def score_of(team_id) -> int:
    clear_standings()
    db.session.expire_all()
    return Teams.query.filter_by(id=team_id).first().get_score(admin=True)


def nonce_of(client):
    with client.session_transaction() as sess:
        return sess.get("nonce")


def team_client(app, user, team):
    """A signed-in player who created a studio through the real routes."""
    register_user(app, name=user, email=f"{user}@example.com")
    client = login_as_user(app, user)
    client.get("/team")
    r = client.post("/teams/new", data={"name": team, "password": "password", "nonce": nonce_of(client)})
    assert r.status_code == 302, r.status_code
    return client


def attempt(client, challenge_id, flag):
    r = client.post("/api/v1/challenges/attempt", json={"challenge_id": challenge_id, "submission": flag})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["status"]


def team_of(user_name) -> Teams:
    return Teams.query.filter_by(id=Users.query.filter_by(name=user_name).first().team_id).first()
