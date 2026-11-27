"""The certificate table on a real MariaDB: the migration builds what models.py says, the keys hold, a second run changes nothing, the
downgrade drops only this table, and the import and the one notification behave with two workers at once.

SQLite never runs plugin migrations (CTFd calls create_all there). It needs a MariaDB; tools/run-migration-test.sh starts a throwaway one:

    L3MON_MOUNT_PLUGINS=1 tools/run-migration-test.sh -- /l3mon_tests/l3mon_certificates/test_certificates_mariadb.py
"""
import importlib.util
import os
import threading

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from CTFd.models import Notifications, Users, db
from CTFd.plugins.l3mon_certificates import loader, models, release
from CTFd.plugins.migrations import current, upgrade
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_user

URL = os.getenv("TESTING_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("mysql"), reason="needs a real MariaDB (tools/run-migration-test.sh)")
REVISION = "a7c1e5d9b201"
TABLE = "l3mon_certificate"


def _migration():
    here = os.path.dirname(os.path.abspath(models.__file__))
    spec = importlib.util.spec_from_file_location("l3mon_certificates_first", os.path.join(here, "migrations", f"{REVISION}_add_the_certificate_table.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True)
    yield app
    destroy_ctfd(app)


def _op():
    conn = db.engine.connect()
    return conn, Operations(MigrationContext.configure(conn))


def test_loading_the_plugin_creates_the_table_as_the_model_says_and_records_the_revision(app):
    with app.app_context():
        assert current("l3mon_certificates") == REVISION
        insp = inspect(db.engine)
        columns = {c["name"]: c for c in insp.get_columns(TABLE)}
        assert set(columns) == {c.name for c in models.Certificate.__table__.columns}
        assert all(not columns[n]["nullable"] for n in ("user_id", "code", "username", "imported_at"))
        uniques = {u["name"]: u["column_names"] for u in insp.get_unique_constraints(TABLE)}
        assert uniques == {"uq_l3mon_certificate_user": ["user_id"], "uq_l3mon_certificate_code": ["code"]}
        fks = insp.get_foreign_keys(TABLE)
        assert [(f["referred_table"], f["options"].get("ondelete")) for f in fks] == [("users", "CASCADE")]


def test_the_database_refuses_a_second_code_for_a_player_and_a_code_twice(app):
    with app.app_context():
        a, b = gen_user(db, name="a1", email="a1@example.com"), gen_user(db, name="b1", email="b1@example.com")
        db.session.add(models.Certificate(user_id=a.id, username="a1", code="L3M0N26-PAR-BCDFG"))
        db.session.commit()
        for row in (models.Certificate(user_id=a.id, username="a1", code="L3M0N26-PAR-HJKLM"), models.Certificate(user_id=b.id, username="b1", code="L3M0N26-PAR-BCDFG")):
            db.session.add(row)
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()


def test_running_the_migration_again_changes_nothing_and_the_downgrade_drops_only_this_table(app):
    with app.app_context():
        before = set(inspect(db.engine).get_table_names())
        conn, op = _op()
        _migration().upgrade(op=op)
        assert set(inspect(db.engine).get_table_names()) == before
        _migration().downgrade(op=op)
        conn.close()
        assert before - set(inspect(db.engine).get_table_names()) == {TABLE}
        set_config("l3mon_certificates_alembic_version", None)
        upgrade(plugin_name="l3mon_certificates")
        assert TABLE in inspect(db.engine).get_table_names()


def test_two_workers_announcing_at_once_send_one_notification(app):
    with app.app_context():
        user = gen_user(db, name="c1", email="c1@example.com")
        loader.load({"format": loader.FORMAT, "base_url": "https://verify.l3m0nctf.xyz", "codes": [{"user_id": user.id, "username": "c1", "code": "L3M0N26-PAR-BCDFG"}]}, apply=True)
        set_config(release.KEY_AT, "1000")
    errors = []

    def worker():
        try:
            with app.app_context():
                release.announce()
                db.session.remove()
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with app.app_context():
        assert errors == []
        assert Notifications.query.filter(Notifications.title.like("%Certificates are ready%")).count() == 1
        assert Users.query.count() >= 1
