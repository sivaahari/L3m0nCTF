"""What a player may see: the same set CTFd's own challenge list shows, never more.

A programme exists for a player only when it is `visible`, and when every prerequisite CTFd knows of has been solved by them;
administrators see everything. A challenge CTFd would show as an anonymous "???" placeholder (an unmet prerequisite with
`anonymize`) is not visible here at all: for us a withheld programme looks exactly like one that does not exist. This one place is
where the release channels and programmes (3.2) tighten the rule, so every later endpoint asks here.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
from sqlalchemy import event

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core.visibility import is_visible, visible_challenge_ids
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_solve, login_as_user, register_user


def test_visible_hidden_locked_and_unknown_states_for_a_player_and_for_an_administrator():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        shown = gen_challenge(db, name="shown", state="visible")
        held = gen_challenge(db, name="held", state="hidden")
        locked = gen_challenge(db, name="locked", state="locked")
        odd = gen_challenge(db, name="odd", state="something-new")
        assert is_visible(shown) and not is_visible(held) and not is_visible(locked) and not is_visible(odd), "only the exact word visible is visible (CTFd also shows an unknown state; we do not)"
        assert all(is_visible(c, admin=True) for c in (shown, held, locked, odd))
        assert visible_challenge_ids() == {shown.id}
        assert visible_challenge_ids(admin=True) == {shown.id, held.id, locked.id, odd.id}
    destroy_ctfd(app)


def test_a_missing_challenge_is_not_visible_to_anyone_and_never_raises():
    assert is_visible(None) is False
    assert is_visible(None, admin=True) is False


def test_prerequisites_hide_a_challenge_until_every_one_is_solved():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        a = gen_challenge(db, name="a")
        b = gen_challenge(db, name="b")
        c = gen_challenge(db, name="c", requirements={"prerequisites": [a.id, b.id]})
        assert visible_challenge_ids() == {a.id, b.id}
        assert not is_visible(c)
        assert visible_challenge_ids(solved_ids={a.id}) == {a.id, b.id}, "one of two is not enough"
        assert visible_challenge_ids(solved_ids={a.id, b.id}) == {a.id, b.id, c.id}
        assert is_visible(c, solved_ids={a.id, b.id}) and not is_visible(c, solved_ids={a.id})
        assert visible_challenge_ids(admin=True) == {a.id, b.id, c.id}
    destroy_ctfd(app)


def test_an_anonymized_placeholder_is_not_visible_and_a_prerequisite_that_does_not_exist_is_ignored_like_ctfd_does():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        a = gen_challenge(db, name="a")
        secret = gen_challenge(db, name="secret", requirements={"prerequisites": [a.id], "anonymize": True})
        ghost = gen_challenge(db, name="ghost", requirements={"prerequisites": [987654]})
        assert visible_challenge_ids() == {a.id, ghost.id}, "no placeholder: withheld looks like missing; a prerequisite nobody can solve is no prerequisite"
        assert visible_challenge_ids(solved_ids={a.id}) == {a.id, secret.id, ghost.id}
    destroy_ctfd(app)


def test_a_prerequisite_that_is_itself_hidden_keeps_the_challenge_hidden_until_it_is_solved():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        held = gen_challenge(db, name="held", state="hidden")
        after = gen_challenge(db, name="after", requirements={"prerequisites": [held.id]})
        assert visible_challenge_ids() == set()
        assert visible_challenge_ids(solved_ids={held.id}) == {after.id}
    destroy_ctfd(app)


def test_it_matches_what_ctfds_own_challenge_list_shows_a_player_before_and_after_a_solve():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        a = gen_challenge(db, name="a")
        b = gen_challenge(db, name="b", requirements={"prerequisites": [a.id]})
        c = gen_challenge(db, name="c", requirements={"prerequisites": [a.id], "anonymize": True})
        d = gen_challenge(db, name="d", requirements={"prerequisites": [b.id, c.id]})
        gen_challenge(db, name="held", state="hidden")
        gen_challenge(db, name="locked", state="locked")
        A, B, C, D = a.id, b.id, c.id, d.id  # a request ends the session, so keep plain numbers
        register_user(app)
        with login_as_user(app) as player:
            def shown():
                rows = player.get("/api/v1/challenges").get_json()["data"]
                return {row["id"] for row in rows if row["type"] != "hidden"}  # CTFd's "hidden" type is the anonymous placeholder

            assert shown() == visible_challenge_ids(solved_ids=set()) == {A}
            gen_solve(db, user_id=2, team_id=None, challenge_id=A)
            assert shown() == visible_challenge_ids(solved_ids={A}) == {A, B, C}
            gen_solve(db, user_id=2, team_id=None, challenge_id=B)
            gen_solve(db, user_id=2, team_id=None, challenge_id=C)
            assert shown() == visible_challenge_ids(solved_ids={A, B, C}) == {A, B, C, D}
    destroy_ctfd(app)


def test_holding_back_or_deleting_a_challenge_changes_the_answer_at_once():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        a = gen_challenge(db, name="a")
        b = gen_challenge(db, name="b")
        assert visible_challenge_ids() == {a.id, b.id}
        a.state = "hidden"
        db.session.commit()
        assert visible_challenge_ids() == {b.id}
        Challenges.query.filter_by(id=b.id).delete()
        db.session.commit()
        assert visible_challenge_ids() == set()
    destroy_ctfd(app)


def test_the_set_is_one_query_however_many_challenges_there_are():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        for i in range(12):
            gen_challenge(db, name=f"c{i}", state="visible" if i % 3 else "hidden")
        statements = []

        def count(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            ids = visible_challenge_ids()
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
        assert len(ids) == 8 and len(statements) == 1
    destroy_ctfd(app)
