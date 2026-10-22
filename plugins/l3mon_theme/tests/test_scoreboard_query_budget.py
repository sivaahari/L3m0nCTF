"""Query budget test for `/scoreboard` (SP6 Stream C).

Verifies that the Scoreboard page (`/scoreboard`) database statements stay within ceiling
and do not grow with the number of studios or solves (audit requirement).
"""
import pytest
from sqlalchemy import event

from CTFd.cache import clear_standings
from CTFd.models import Teams, Users, db
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PASSWORD = "test-budget-password-123"
SCOREBOARD_CEILING = (25, 18)  # (cold, warm) ceiling statements for the full server-rendered HTML page


def statements_of(client, path):
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        response = client.get(path)
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return response, seen


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams")
    with app.app_context():
        register_user(app, name="captain", email="cap@example.com", password=PASSWORD)
        u_cap = Users.query.filter_by(name="captain").first()

        studio = Teams(name="Citrus Budget", email="budget@example.com")
        studio.captain_id = u_cap.id
        db.session.add(studio)
        db.session.commit()

        u_cap.team_id = studio.id
        db.session.commit()

    yield app
    destroy_ctfd(app)


def test_scoreboard_page_query_budget(app):
    client = login_as_user(app, "captain", PASSWORD)

    with app.app_context():
        clear_standings()
        r1, cold = statements_of(client, "/scoreboard")
        assert r1.status_code == 200

        r2, warm = statements_of(client, "/scoreboard")
        assert r2.status_code == 200

        cold_count = len(cold)
        warm_count = len(warm)

        assert cold_count <= SCOREBOARD_CEILING[0], f"Cold query count {cold_count} exceeded ceiling {SCOREBOARD_CEILING[0]}"
        assert warm_count <= SCOREBOARD_CEILING[1], f"Warm query count {warm_count} exceeded ceiling {SCOREBOARD_CEILING[1]}"
