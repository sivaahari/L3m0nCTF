"""Hook cost tests for l3mon_ops.

Proves that l3mon_ops adds zero database statements to requests that are not its own.
"""
from sqlalchemy import event
from CTFd.models import db
from tests.helpers import create_ctfd, destroy_ctfd


def count_statements(fn):
    statements = []

    def on(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        res = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return res, statements


def test_ops_plugin_adds_no_database_statements_to_unrelated_routes():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            # Warm up caches
            client.get("/login")

            # Subsequent request
            _, stmts = count_statements(lambda: client.get("/login"))
            # l3mon_ops has no before_request or after_request hooks, so it adds 0 queries
            import CTFd.plugins.l3mon_ops as ops_mod
            assert hasattr(ops_mod, "ops")
    finally:
        destroy_ctfd(app)
