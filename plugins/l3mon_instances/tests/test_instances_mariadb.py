"""The l3mon_instances migrations and concurrency tests on a real MariaDB.

Tested with tools/run-migration-test.sh.
"""
import concurrent.futures
import datetime
import importlib.util
import os
import threading

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, DBAPIError

from CTFd.models import Challenges, Teams, Users, db
from CTFd.plugins.l3mon_instances import models
from CTFd.plugins.migrations import current, upgrade
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_team

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")

REVISION = "a1b2c3d4e5f6"
MODELS = {
    models.L3monInstance.__tablename__: models.L3monInstance,
    models.L3monInstanceLog.__tablename__: models.L3monInstanceLog,
}


def _load_migration():
    here = os.path.dirname(os.path.abspath(models.__file__))
    spec = importlib.util.spec_from_file_location("inst_mig", os.path.join(here, "migrations", "a1b2c3d4e5f6_create_instances_tables.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mariadb_migration_creates_tables_and_matches_models():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        insp = inspect(db.engine)
        table_names = set(insp.get_table_names())
        for name in MODELS:
            assert name in table_names, f"Table {name} missing in MariaDB"

        for name, model in MODELS.items():
            real_cols = {c["name"] for c in insp.get_columns(name)}
            want_cols = {c.name for c in model.__table__.columns}
            assert real_cols == want_cols, f"{name}: columns mismatch"

        # Check unique constraint on active_key in l3mon_instance
        uniques = insp.get_unique_constraints("l3mon_instance")
        unique_cols = [set(u["column_names"]) for u in uniques]
        assert {"active_key"} in unique_cols or {"instance_id"} in unique_cols
    destroy_ctfd(app)


def test_mariadb_migration_run_twice_and_rollback():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        # Running upgrade when already at head is safe
        upgrade(plugin_name="l3mon_instances")
        assert current("l3mon_instances") == REVISION

        mig = _load_migration()
        with db.engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            # Test downgrade
            mig.downgrade(op)

        insp = inspect(db.engine)
        assert "l3mon_instance" not in insp.get_table_names()
        assert "l3mon_instance_log" not in insp.get_table_names()

        # Test upgrade back
        with db.engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            mig.upgrade(op)

        insp2 = inspect(db.engine)
        assert "l3mon_instance" in insp2.get_table_names()
        assert "l3mon_instance_log" in insp2.get_table_names()
    destroy_ctfd(app)


def test_mariadb_eight_workers_same_instance_active_key_race():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="race_team", email="race@example.com")
        chal = gen_challenge(db, name="race_chal")
        db.session.commit()
        t_id, c_id = team.id, chal.id

    barrier = threading.Barrier(8)
    results = []

    def worker(worker_id):
        with app.app_context():
            barrier.wait()
            try:
                now_dt = datetime.datetime.utcnow()
                row = models.L3monInstance(
                    instance_id=f"inst_worker_{worker_id}_{os.urandom(8).hex()}",
                    team_id=t_id,
                    challenge_id=c_id,
                    slug="race-chal",
                    protocol="http",
                    state="running",
                    host_name="default",
                    url=f"http://test-{worker_id}.local/",
                    created_at=now_dt,
                    expires_at=now_dt + datetime.timedelta(minutes=45),
                    active_key=f"{t_id}:{c_id}",
                )
                db.session.add(row)
                db.session.commit()
                results.append((worker_id, True, None))
            except Exception as e:
                db.session.rollback()
                results.append((worker_id, False, str(e)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r[1] is True]
    assert len(successes) == 1, f"Expected exactly 1 success out of 8, got {len(successes)}"

    with app.app_context():
        count = models.L3monInstance.query.filter_by(active_key=f"{t_id}:{c_id}").count()
        assert count == 1
    destroy_ctfd(app)


def test_mariadb_two_starts_different_challenges_no_deadlock():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    with app.app_context():
        team = gen_team(db, name="parallel_team", email="parallel@example.com")
        chal_a = gen_challenge(db, name="chal_a")
        chal_b = gen_challenge(db, name="chal_b")
        db.session.commit()
        t_id = team.id
        ca_id = chal_a.id
        cb_id = chal_b.id

    barrier = threading.Barrier(2)
    results = []

    def start_chal(c_id, name):
        with app.app_context():
            barrier.wait()
            now_dt = datetime.datetime.utcnow()
            row = models.L3monInstance(
                instance_id=f"inst_{name}_{os.urandom(8).hex()}",
                team_id=t_id,
                challenge_id=c_id,
                slug=name,
                protocol="http",
                state="running",
                host_name="default",
                url=f"http://{name}.local/",
                created_at=now_dt,
                expires_at=now_dt + datetime.timedelta(minutes=45),
                active_key=f"{t_id}:{c_id}",
            )
            db.session.add(row)
            db.session.commit()
            results.append((name, True))

    t1 = threading.Thread(target=start_chal, args=(ca_id, "chal_a"))
    t2 = threading.Thread(target=start_chal, args=(cb_id, "chal_b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert all(r[1] for r in results)

    with app.app_context():
        rows = models.L3monInstance.query.filter_by(team_id=t_id).all()
        assert len(rows) == 2
    destroy_ctfd(app)
