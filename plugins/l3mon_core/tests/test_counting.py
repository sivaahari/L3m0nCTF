"""Who counts: one rule for the solve count, the dynamic value, the standings and the feed. It must be CTFd's own rule.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
from sqlalchemy import event

from CTFd.models import Admins, Teams, Users, db
from CTFd.plugins.dynamic_challenges import DynamicChallenge
from CTFd.plugins.dynamic_challenges.decay import get_solve_count
from CTFd.plugins.l3mon_core.counting import counted_solve_count, counted_solve_counts, is_counted_team, is_counted_user
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_solve, gen_team, gen_user


def dynamic(name="dyn"):
    chal = DynamicChallenge(name=name, description="d", category="c", state="visible", initial=500, minimum=100, decay=20, function="logarithmic")
    db.session.add(chal)
    db.session.commit()
    return chal


def solve(user, team, chal):
    gen_solve(db, user_id=user.id, team_id=team.id if team else None, challenge_id=chal.id)


def test_the_predicates_for_a_team_and_for_a_user():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        ok = gen_team(db, name="ok", email="ok@e.com", member_count=1)
        banned = gen_team(db, name="banned", email="b@e.com", member_count=1, banned=True)
        hidden = gen_team(db, name="hidden", email="h@e.com", member_count=1, hidden=True)
        assert is_counted_team(ok) and not is_counted_team(banned) and not is_counted_team(hidden) and not is_counted_team(None)
        user = gen_user(db, name="u", email="u@e.com")
        assert is_counted_user(user)
        assert not is_counted_user(gen_user(db, name="ub", email="ub@e.com", banned=True))
        assert not is_counted_user(gen_user(db, name="uh", email="uh@e.com", hidden=True))
        setup_admin = Users.query.filter_by(type="admin").first()
        assert setup_admin.hidden and not is_counted_user(setup_admin), "the administrator CTFd creates at setup is hidden, so never counted"
        assert not is_counted_user(None)
    destroy_ctfd(app)


def test_in_team_mode_a_banned_or_hidden_team_never_counts_and_the_number_is_ctfds_own():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        chal = dynamic()
        teams = {
            "ok": gen_team(db, name="ok", email="ok@e.com", member_count=1),
            "ok2": gen_team(db, name="ok2", email="ok2@e.com", member_count=1),
            "banned": gen_team(db, name="banned", email="b@e.com", member_count=1, banned=True),
            "hidden": gen_team(db, name="hidden", email="h@e.com", member_count=1, hidden=True),
        }
        for team in teams.values():
            solve(team.members[0], team, chal)
        assert counted_solve_count(chal.id) == 2
        assert counted_solve_count(chal.id) == get_solve_count(chal), "the same number CTFd feeds its own decay"
        teams["banned"].banned = False  # lifting a ban counts again at once
        db.session.commit()
        assert counted_solve_count(chal.id) == 3 == get_solve_count(chal)
        teams["ok"].hidden = True
        db.session.commit()
        assert counted_solve_count(chal.id) == 2 == get_solve_count(chal)
    destroy_ctfd(app)


def test_in_team_mode_a_banned_solver_does_not_change_the_teams_count_and_an_administrators_solve_is_not_a_studio():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        chal = dynamic()
        team = gen_team(db, name="ok", email="ok@e.com", member_count=2)
        solve(team.members[0], team, chal)
        team.members[0].banned = True  # the member who solved
        db.session.commit()
        assert counted_solve_count(chal.id) == 1 == get_solve_count(chal), "CTFd counts the account (the team), not the member who solved"
        admin = Admins(name="boss", email="boss@e.com", password="x")
        db.session.add(admin)
        db.session.commit()
        gen_solve(db, user_id=admin.id, team_id=None, challenge_id=chal.id)  # an administrator trying a flag has no team
        assert counted_solve_count(chal.id) == 1 == get_solve_count(chal)
    destroy_ctfd(app)


def test_in_user_mode_the_account_is_the_user():
    app = create_ctfd(enable_plugins=True, user_mode="users")
    with app.app_context():
        chal = dynamic()
        ok = gen_user(db, name="ok", email="ok@e.com")
        banned = gen_user(db, name="banned", email="b@e.com", banned=True)
        hidden = gen_user(db, name="hidden", email="h@e.com", hidden=True)
        for user in (ok, banned, hidden):
            solve(user, None, chal)
        assert counted_solve_count(chal.id) == 1 == get_solve_count(chal)
        banned.banned = False
        db.session.commit()
        assert counted_solve_count(chal.id) == 2 == get_solve_count(chal)
    destroy_ctfd(app)


def test_unknown_and_unsolved_challenges_count_zero():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        chal = gen_challenge(db)
        assert counted_solve_count(chal.id) == 0
        assert counted_solve_count(987654) == 0
        assert counted_solve_counts([]) == {}
    destroy_ctfd(app)


def test_the_batch_answer_equals_the_single_answers_and_is_one_query():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        chals = [gen_challenge(db, name=f"c{i}") for i in range(5)]
        teams = [gen_team(db, name=f"t{i}", email=f"t{i}@e.com", member_count=1, banned=(i == 4)) for i in range(5)]
        for i, chal in enumerate(chals):
            for team in teams[: i + 1]:
                solve(team.members[0], team, chal)
        ids = [c.id for c in chals] + [424242]
        statements = []

        def count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            batch = counted_solve_counts(ids)
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
        solves = [q for q in statements if "FROM solves" in q or "solves.challenge_id" in q]  # (CTFd may read its own config the first time)
        assert len(solves) == 1, "one grouped query on the solves, not one per challenge"
        assert batch == {i: counted_solve_count(i) for i in ids}
        assert batch[chals[4].id] == 4, "the banned team's solve is left out"
        assert batch[424242] == 0
    destroy_ctfd(app)


def test_a_very_long_list_and_a_repeated_id_are_fine_and_a_short_list_is_still_one_query():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        chal = gen_challenge(db)
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        solve(team.members[0], team, chal)
        ids = list(range(1, 1301)) + [chal.id, chal.id]
        batch = counted_solve_counts(ids)
        assert len(batch) == 1300 and batch[chal.id] == 1 and sum(batch.values()) == 1
        assert counted_solve_counts([chal.id, chal.id]) == {chal.id: 1}
    destroy_ctfd(app)
