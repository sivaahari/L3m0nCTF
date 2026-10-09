"""The migrations on a real MariaDB: the six tables, their constraints, running them twice or at the same moment, and rolling them back.

SQLite (the default test database) never runs plugin migrations (CTFd calls create_all there), so this is the only proof that
production gets the schema models.py describes. It needs a MariaDB; tools/run-migration-test.sh starts a throwaway one:

    tools/run-migration-test.sh
"""
import importlib.util
import os
import threading

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError  # MariaDB reports a failed CHECK as an OperationalError, a key or a foreign key as an IntegrityError

from CTFd.cache import cache
from CTFd.models import Challenges, Configs, Teams, db
from CTFd.plugins.l3mon_core import models
from CTFd.plugins.migrations import current, upgrade
from CTFd.utils import _get_config, get_config, set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_award, gen_challenge, gen_solve, gen_team, gen_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

FIRST = "7a41c0d2b1e3"
REVISION = "b3d95f0a6c12"  # the head: the second revision adds the audit table
VERSION_KEY = "l3mon_core_alembic_version"
MODELS = {m.__tablename__: m for m in (models.Channel, models.Programme, models.Void, models.Bonus, models.Note, models.Audit)}
FIRST_FIVE = {name for name in MODELS if name != "l3mon_audit"}


def _load(file_name, module_name):
    here = os.path.dirname(os.path.abspath(models.__file__))
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(here, "migrations", file_name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_module():
    """The first revision (the five tables)."""
    return _load("7a41c0d2b1e3_create_the_l3mon_tables.py", "l3mon_first_revision")


def audit_module():
    """The second revision (the audit table)."""
    return _load("b3d95f0a6c12_add_the_audit_table.py", "l3mon_second_revision")


def run_on_connection(fn):
    with db.engine.begin() as conn:
        fn(Operations(MigrationContext.configure(conn)))


def tables():
    return set(inspect(db.engine).get_table_names())


def forget_the_recorded_revision():
    """What an operator does after a manual downgrade: CTFd's runner only goes forward from the revision it has recorded."""
    Configs.query.filter_by(key=VERSION_KEY).delete()
    db.session.commit()
    cache.delete_memoized(_get_config, VERSION_KEY)


def test_loading_the_plugin_creates_the_six_tables_and_records_the_head_revision():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        assert set(MODELS) <= tables()
        assert current("l3mon_core") == REVISION
    destroy_ctfd(app)


def test_the_tables_say_what_the_models_say():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        insp = inspect(db.engine)
        for name, model in MODELS.items():
            real = {c["name"]: c for c in insp.get_columns(name)}
            want = {c.name: c for c in model.__table__.columns}
            assert set(real) == set(want), name
            assert list(real) == list(want), f"{name}: the columns are in the same order"
            for col, spec in want.items():
                assert bool(real[col]["nullable"]) == bool(spec.nullable), f"{name}.{col} nullable"
                assert real[col]["type"].python_type == spec.type.python_type, f"{name}.{col} type"
                length = getattr(spec.type, "length", None)
                if length:
                    assert real[col]["type"].length == length, f"{name}.{col} length"
            real_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], (fk.get("options") or {}).get("ondelete")) for fk in insp.get_foreign_keys(name)}
            want_fks = {(tuple(fk.parent.name for fk in c.elements), c.elements[0].column.table.name, c.ondelete) for c in model.__table__.foreign_key_constraints}
            assert real_fks == want_fks, f"{name} foreign keys"
            real_unique = {tuple(u["column_names"]) for u in insp.get_unique_constraints(name)}
            want_unique = {tuple(c.name for c in u.columns) for u in model.__table__.constraints if u.__class__.__name__ == "UniqueConstraint"}
            want_unique |= {(c.name,) for c in model.__table__.columns if c.unique}
            assert real_unique == want_unique, f"{name} unique constraints"
            real_checks = {c["name"] for c in insp.get_check_constraints(name)}
            want_checks = {c.name for c in model.__table__.constraints if c.__class__.__name__ == "CheckConstraint"}
            assert real_checks == want_checks, f"{name} check constraints"
    destroy_ctfd(app)


def test_the_database_itself_refuses_what_the_models_refuse():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        ch = models.Channel(slug="street", name="Street")
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        db.session.add(ch)
        db.session.commit()
        a, b = gen_challenge(db, name="a"), gen_challenge(db, name="b")
        award = gen_award(db, user_id=team.members[0].id, team_id=team.id, value=5)
        db.session.add(models.Programme(challenge_id=a.id, channel_id=ch.id, cell=0, number=1, slug="alpha"))
        db.session.commit()
        uid = team.members[0].id
        bad_rows = [
            models.Channel(slug="street", name="again"),  # slug
            models.Channel(slug="odd", name="odd", kind="weird"),  # check: kind
            models.Channel(slug="odd2", name="odd", release_state="maybe"),  # check: channel release state
            models.Programme(challenge_id=a.id, channel_id=ch.id, cell=1, number=2, slug="beta"),  # one challenge, one programme
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=0, number=2, slug="beta"),  # cell
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=1, slug="beta"),  # number
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="alpha"),  # slug
            models.Programme(challenge_id=b.id, channel_id=ch.id, cell=1, number=2, slug="beta", release_state="soon"),  # check: programme release state
            models.Programme(challenge_id=b.id, channel_id=99999, cell=1, number=2, slug="beta"),  # no such channel
            models.Void(challenge_id=a.id, team_id=team.id, user_id=uid, reason="r", outcome="vanished"),  # check: void outcome
            models.Void(challenge_id=a.id, team_id=team.id, user_id=uid, reason=None),  # a reason is required
            models.Bonus(award_id=award.id, team_id=team.id, user_id=uid, scope="everyone", message="m"),  # check: bonus scope
            models.Bonus(award_id=99999, team_id=team.id, user_id=uid, scope="team", message="m"),  # no such award
            models.Note(team_id=99999, title="t", text="x"),  # no such studio
            models.Audit(actor_id=99999, actor_name="x", action="a", target="t", detail=""),  # no such user
            models.Audit(actor_name=None, action="a", target="t", detail=""),  # a name is required
        ]
        for row in bad_rows:
            db.session.add(row)
            with pytest.raises(DBAPIError):
                db.session.commit()
            db.session.rollback()
    destroy_ctfd(app)


def test_deleting_a_challenge_a_giver_or_a_team_removes_what_hangs_on_it_and_keeps_the_audit_trail():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="t", email="t@e.com", member_count=1)
        uid = team.members[0].id
        boss = gen_user(db, name="boss", email="boss@e.com")
        chal = gen_challenge(db)
        ch = models.Channel(slug="street", name="Street")
        db.session.add(ch)
        db.session.commit()
        db.session.add(models.Programme(challenge_id=chal.id, channel_id=ch.id, cell=0, number=1, slug="alpha"))
        solve = gen_solve(db, user_id=uid, team_id=team.id, challenge_id=chal.id)
        award = gen_award(db, user_id=uid, team_id=team.id, value=25)
        db.session.add(models.Void(challenge_id=chal.id, team_id=team.id, user_id=uid, submission_id=solve.id, voided_by=boss.id, reason="leaked"))
        db.session.add(models.Bonus(award_id=award.id, team_id=team.id, user_id=uid, scope="team", message="bug", given_by=boss.id))
        db.session.add(models.Note(team_id=team.id, title="t", text="x"))
        db.session.add(models.Audit(actor_id=boss.id, actor_name="boss", action="release.set", target="programme:alpha", detail="released"))
        db.session.commit()
        chal_id, team_id, boss_id = chal.id, team.id, boss.id

        # an administrator who gave a bonus and voided a solve leaves: both records stay, with nobody as the author
        db.session.execute(text("DELETE FROM users WHERE id = :i"), {"i": boss_id})
        db.session.commit()
        assert models.Bonus.query.one().given_by is None and models.Void.query.one().voided_by is None
        line = models.Audit.query.one()
        assert line.actor_id is None and line.actor_name == "boss", "the audit line outlives the administrator and still says who"

        # the challenge goes: its programme and its voids go with it
        Challenges.query.filter_by(id=chal_id).delete()
        db.session.commit()
        assert models.Programme.query.count() == 0 and models.Void.query.count() == 0
        assert models.Bonus.query.count() == 1 and models.Note.query.count() == 1, "a bonus and a note are about the studio, not the challenge"

        # the studio goes (CTFd first lets its members go, as its own delete does): its bonuses and its notes go with it
        db.session.execute(text("UPDATE teams SET captain_id = NULL WHERE id = :t"), {"t": team_id})
        db.session.execute(text("UPDATE users SET team_id = NULL WHERE team_id = :t"), {"t": team_id})
        Teams.query.filter_by(id=team_id).delete()
        db.session.commit()
        assert models.Bonus.query.count() == 0 and models.Note.query.count() == 0
        assert models.Channel.query.count() == 1, "a channel does not depend on any of this"
    destroy_ctfd(app)


def test_running_the_migration_again_one_after_another_changes_nothing():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = tables()
        upgrade(plugin_name="l3mon_core", force_all=True)
        upgrade(plugin_name="l3mon_core", force_all=True)
        run_on_connection(lambda op: migration_module().upgrade(op=op))
        run_on_connection(lambda op: audit_module().upgrade(op=op))
        assert tables() == before and current("l3mon_core") == REVISION
    destroy_ctfd(app)


@pytest.mark.parametrize("which", ["first", "second"])
def test_two_workers_running_a_migration_at_the_same_moment_both_succeed(which):
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        module = migration_module() if which == "first" else audit_module()
        ours = FIRST_FIVE if which == "first" else {"l3mon_audit"}
        for round_number in range(5):
            run_on_connection(lambda op: module.downgrade(op=op))
            assert not (ours & tables())
            barrier, errors = threading.Barrier(2), []

            def worker():
                try:
                    with app.app_context():  # a worker of its own: its own application context and its own connection
                        with db.engine.begin() as conn:
                            op = Operations(MigrationContext.configure(conn))
                            barrier.wait()
                            module.upgrade(op=op)
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"round {round_number}: {errors}"
            assert ours <= tables(), f"round {round_number}"
    destroy_ctfd(app)


def test_the_second_downgrade_drops_only_the_audit_table_and_a_database_at_the_first_revision_gets_it_at_the_next_start():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = tables()
        run_on_connection(lambda op: audit_module().downgrade(op=op))
        assert before - tables() == {"l3mon_audit"}, "only the audit table is gone; the five first tables stay"
        run_on_connection(lambda op: audit_module().downgrade(op=op))  # a second downgrade is harmless
        # a platform that was running part 3.1 has the first revision recorded: the next start adds the audit table, once
        set_config("l3mon_core_alembic_version", FIRST)
        assert current("l3mon_core") == FIRST
        upgrade(plugin_name="l3mon_core")
        assert tables() == before and current("l3mon_core") == REVISION
    destroy_ctfd(app)


def test_the_downgrades_drop_the_six_tables_and_nothing_else_and_a_forgotten_revision_brings_them_back_at_the_next_start():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        before = tables()
        run_on_connection(lambda op: audit_module().downgrade(op=op))
        run_on_connection(lambda op: migration_module().downgrade(op=op))
        after = tables()
        assert before - after == set(MODELS), "exactly our six tables are gone"
        assert after <= before and "challenges" in after and "teams" in after
        run_on_connection(lambda op: migration_module().downgrade(op=op))  # a second downgrade is harmless
        # CTFd's runner only goes forward from the revision it recorded, so after a manual downgrade the recorded revision must be
        # forgotten too (the documented rollback), and then the next start builds the tables again
        assert current("l3mon_core") == REVISION
        upgrade(plugin_name="l3mon_core")
        assert not (set(MODELS) & tables()), "with the revision still recorded the runner rightly does nothing"
        forget_the_recorded_revision()
        assert current("l3mon_core") is None
        upgrade(plugin_name="l3mon_core")
        assert tables() == before and current("l3mon_core") == REVISION and get_config("user_mode") == "teams"
    destroy_ctfd(app)
