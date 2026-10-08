"""The five tables of the SP3 design (section 4), as models, on SQLite. The real-database proof is test_migration_mariadb.py.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from CTFd.models import db
from CTFd.plugins.l3mon_core.models import Bonus, Channel, Note, Programme, Void
from tests.helpers import create_ctfd, destroy_ctfd, gen_award, gen_challenge, gen_solve, gen_team

TABLES = {"l3mon_channel", "l3mon_programme", "l3mon_void", "l3mon_bonus", "l3mon_note"}


def app_in_teams_mode():
    return create_ctfd(enable_plugins=True, user_mode="teams")


def channel(slug="ch1", **kw):
    row = Channel(slug=slug, name=kw.pop("name", slug.title()), **kw)
    db.session.add(row)
    db.session.commit()
    return row


def test_the_five_tables_exist_once_the_plugin_is_loaded():
    app = app_in_teams_mode()
    with app.app_context():
        assert TABLES <= set(inspect(db.engine).get_table_names())
    destroy_ctfd(app)


def test_a_channel_has_sensible_defaults_and_a_unique_slug():
    app = app_in_teams_mode()
    with app.app_context():
        ch = channel("street", name="Mighty Street", accent="ch1")
        assert (ch.kind, ch.release_state, ch.position) == ("standard", "released", 0)
        assert ch.release_at is None and ch.sponsor_name is None and ch.synopsis is None
        with pytest.raises(IntegrityError):
            channel("street", name="Another")
        db.session.rollback()
    destroy_ctfd(app)


def test_a_sponsored_channel_keeps_its_sponsor_and_its_storyline():
    app = app_in_teams_mode()
    with app.app_context():
        ch = channel("break", kind="sponsored", sponsor_name="Acme", sponsor_logo="acme.svg", synopsis="We will be right back.")
        assert (ch.kind, ch.sponsor_name, ch.sponsor_logo, ch.synopsis) == ("sponsored", "Acme", "acme.svg", "We will be right back.")
    destroy_ctfd(app)


@pytest.mark.parametrize(
    "model,kwargs",
    [
        (Channel, dict(slug="x", name="X", kind="weird")),
        (Channel, dict(slug="y", name="Y", release_state="maybe")),
    ],
)
def test_a_channel_refuses_a_kind_or_a_release_state_nobody_defined(model, kwargs):
    app = app_in_teams_mode()
    with app.app_context():
        db.session.add(model(**kwargs))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
    destroy_ctfd(app)


def test_a_programme_belongs_to_one_challenge_one_channel_cell_and_has_a_unique_number_and_slug():
    app = app_in_teams_mode()
    with app.app_context():
        ch = channel()
        a, b, c = (gen_challenge(db, name=n) for n in "abc")
        db.session.add(Programme(challenge_id=a.id, channel_id=ch.id, cell=0, number=1, slug="alpha"))
        db.session.commit()
        p = Programme.query.one()
        assert p.release_state == "released" and p.release_at is None
        for bad in (
            dict(challenge_id=a.id, channel_id=ch.id, cell=1, number=2, slug="beta"),  # the same challenge twice
            dict(challenge_id=b.id, channel_id=ch.id, cell=0, number=2, slug="beta"),  # the same cell
            dict(challenge_id=b.id, channel_id=ch.id, cell=1, number=1, slug="beta"),  # the same number
            dict(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="alpha"),  # the same slug
            dict(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="beta", release_state="soon"),  # not a state
        ):
            db.session.add(Programme(**bad))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()
        # another channel may use the same cell
        other = channel("other")
        db.session.add(Programme(challenge_id=c.id, channel_id=other.id, cell=0, number=3, slug="gamma"))
        db.session.commit()
        assert Programme.query.count() == 2
    destroy_ctfd(app)


def test_a_void_records_who_when_and_why_and_starts_open():
    app = app_in_teams_mode()
    with app.app_context():
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        chal = gen_challenge(db)
        solve = gen_solve(db, user_id=team.members[0].id, team_id=team.id, challenge_id=chal.id)
        db.session.add(Void(challenge_id=chal.id, team_id=team.id, user_id=team.members[0].id, submission_id=solve.id, solved_at=solve.date, voided_by=None, reason="the answer leaked"))
        db.session.commit()
        v = Void.query.one()
        assert v.outcome == "open" and isinstance(v.voided_at, datetime.datetime) and v.restored_at is None and v.restored_by is None
        db.session.add(Void(challenge_id=chal.id, team_id=team.id, user_id=None, reason="x", outcome="vanished"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        db.session.add(Void(challenge_id=chal.id, team_id=team.id, user_id=None, reason=None))
        with pytest.raises(IntegrityError):
            db.session.commit()  # a reason is required
        db.session.rollback()
    destroy_ctfd(app)


def test_a_bonus_is_tied_to_one_award_and_is_for_a_team_or_a_member():
    app = app_in_teams_mode()
    with app.app_context():
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        uid = team.members[0].id
        award = gen_award(db, user_id=uid, team_id=team.id, name="Bonus", value=50)
        db.session.add(Bonus(award_id=award.id, team_id=team.id, user_id=uid, scope="team", message="found a bug", given_by=None))
        db.session.commit()
        b = Bonus.query.one()
        assert isinstance(b.given_at, datetime.datetime) and b.scope == "team"
        for bad in (
            dict(award_id=award.id, team_id=team.id, user_id=uid, scope="team", message="again"),  # one note per award
            dict(award_id=gen_award(db, user_id=uid, team_id=team.id, value=1).id, team_id=team.id, user_id=uid, scope="everyone", message="m"),
        ):
            db.session.add(Bonus(**bad))
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()
    destroy_ctfd(app)


def test_a_note_belongs_to_a_studio_and_stamps_its_time():
    app = app_in_teams_mode()
    with app.app_context():
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        db.session.add(Note(team_id=team.id, title="Bonus +50", text="Thanks for the bug report."))
        db.session.commit()
        n = Note.query.one()
        assert (n.title, n.text) == ("Bonus +50", "Thanks for the bug report.") and isinstance(n.created_at, datetime.datetime)
    destroy_ctfd(app)
