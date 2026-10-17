"""Tests for l3mon_ops /readyz healthcheck endpoint.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_ops/test_ops_readyz.py
"""
import json
import time
from unittest import mock

from tests.helpers import create_ctfd, destroy_ctfd


def test_readyz_returns_200_and_status_ready_when_healthy():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            r = client.get("/readyz")
            assert r.status_code == 200
            data = json.loads(r.get_data(as_text=True))
            assert data == {"status": "ready"}
            assert r.mimetype == "application/json"
            assert r.headers.get("Cache-Control") == "no-store"
            # CTFd sets an anonymous session on every request; nginx proxy_hide_header Set-Cookie
            # strips it in production (verified in integration tests).
    finally:
        destroy_ctfd(app)


def test_readyz_returns_503_and_status_not_ready_when_db_fails():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            with mock.patch("CTFd.plugins.l3mon_ops._check_db_quick", return_value=False):
                r = client.get("/readyz")
                assert r.status_code == 503
                data = json.loads(r.get_data(as_text=True))
                assert data == {"status": "not_ready"}
                assert r.headers.get("Cache-Control") == "no-store"
                # Leaks no error details or hostnames
                assert list(data.keys()) == ["status"]
    finally:
        destroy_ctfd(app)


def test_readyz_returns_503_and_status_not_ready_when_redis_fails():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            with mock.patch("CTFd.plugins.l3mon_ops._check_redis_quick", return_value=False):
                r = client.get("/readyz")
                assert r.status_code == 503
                data = json.loads(r.get_data(as_text=True))
                assert data == {"status": "not_ready"}
                assert r.headers.get("Cache-Control") == "no-store"
                assert list(data.keys()) == ["status"]
    finally:
        destroy_ctfd(app)


def test_readyz_strictly_times_out_and_returns_503_when_db_hangs():
    app = create_ctfd(enable_plugins=True)
    try:
        def hanging_check():
            time.sleep(1.5)
            return True

        with app.app_context(), app.test_client() as client:
            with mock.patch("CTFd.plugins.l3mon_ops._check_db_quick", side_effect=hanging_check):
                start = time.time()
                r = client.get("/readyz")
                elapsed = time.time() - start
                assert r.status_code == 503
                data = json.loads(r.get_data(as_text=True))
                assert data == {"status": "not_ready"}
                # Enforces approximately 1-second timeout
                assert elapsed < 1.8
    finally:
        destroy_ctfd(app)


def test_readyz_strictly_times_out_and_returns_503_when_redis_hangs():
    app = create_ctfd(enable_plugins=True)
    try:
        def hanging_check():
            time.sleep(1.5)
            return True

        with app.app_context(), app.test_client() as client:
            with mock.patch("CTFd.plugins.l3mon_ops._check_redis_quick", side_effect=hanging_check):
                start = time.time()
                r = client.get("/readyz")
                elapsed = time.time() - start
                assert r.status_code == 503
                data = json.loads(r.get_data(as_text=True))
                assert data == {"status": "not_ready"}
                assert elapsed < 1.8
    finally:
        destroy_ctfd(app)


def test_readyz_does_not_depend_on_host_names_it_invents(monkeypatch):
    """Found by the integration audit: the checks connected to a host called `db` and another called `cache` whatever the platform's own settings said, so
    the endpoint answered 503 on any machine where those names do not exist (every test run, and any deployment with other names). The platform's own
    database and cache are asked; a host name is only tried first when the platform's own settings (DATABASE_HOST, REDIS_HOST) name one."""
    for name in ("DB_HOST", "DB_PORT", "DATABASE_HOST", "DATABASE_PORT", "REDIS_HOST", "REDIS_PORT"):
        monkeypatch.delenv(name, raising=False)
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            assert client.get("/readyz").status_code == 200
            # a configured host that does not answer is "not ready", quickly
            monkeypatch.setenv("DATABASE_HOST", "192.0.2.1")  # TEST-NET-1: nothing answers there
            monkeypatch.setenv("DATABASE_PORT", "3306")
            start = time.time()
            r = client.get("/readyz")
            assert r.status_code == 503 and time.time() - start < 1.8
    finally:
        destroy_ctfd(app)
