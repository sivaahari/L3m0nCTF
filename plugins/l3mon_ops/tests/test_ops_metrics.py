"""Tests for l3mon_ops /l3mon/metrics endpoint.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_ops/test_ops_metrics.py
"""
import os
import re
from unittest import mock

from tests.helpers import create_ctfd, destroy_ctfd


def test_metrics_returns_prometheus_format_with_expected_gauges():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            r = client.get("/l3mon/metrics")
            assert r.status_code == 200
            assert "text/plain" in r.mimetype
            text = r.get_data(as_text=True)

            assert "l3mon_event_phase{" in text
            assert "l3mon_tick_value " in text
            assert "l3mon_solves_last_minute " in text
            assert "l3mon_wrong_attempts_last_minute " in text
            assert "l3mon_registrations_last_minute " in text
            assert "l3mon_build_info{" in text
    finally:
        destroy_ctfd(app)


def test_metrics_leaks_no_team_user_flag_or_challenge_names():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            r = client.get("/l3mon/metrics")
            assert r.status_code == 200
            text = r.get_data(as_text=True)

            labels = re.findall(r"\{([^}]+)\}", text)
            for label_str in labels:
                for pair in label_str.split(","):
                    key = pair.split("=")[0].strip()
                    assert key in ("phase", "version"), f"Unexpected sensitive label: {key}"

            assert "L3m0n" not in text
            assert "flag" not in text.lower() or "wrong flag" in text.lower()
    finally:
        destroy_ctfd(app)


def test_metrics_is_cached_for_15_seconds():
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            import CTFd.plugins.l3mon_ops as ops_mod
            ops_mod._last_metrics_body = ""
            ops_mod._last_metrics_time = 0.0

            with mock.patch("CTFd.plugins.l3mon_ops._render_metrics", return_value="test_metrics_marker 1\n") as mock_render:
                r1 = client.get("/l3mon/metrics")
                assert r1.status_code == 200
                assert "test_metrics_marker 1" in r1.get_data(as_text=True)
                assert mock_render.call_count == 1

                # Immediate second call must use cache
                r2 = client.get("/l3mon/metrics")
                assert r2.status_code == 200
                assert "test_metrics_marker 1" in r2.get_data(as_text=True)
                assert mock_render.call_count == 1
    finally:
        destroy_ctfd(app)


def test_metrics_reflects_l3mon_build_environment_variable(monkeypatch):
    monkeypatch.setenv("L3MON_BUILD", "2026-10-17-commit123")
    app = create_ctfd(enable_plugins=True)
    try:
        with app.app_context(), app.test_client() as client:
            import CTFd.plugins.l3mon_ops as ops_mod
            ops_mod._last_metrics_body = ""
            ops_mod._last_metrics_time = 0.0

            r = client.get("/l3mon/metrics")
            assert r.status_code == 200
            text = r.get_data(as_text=True)
            assert 'l3mon_build_info{version="2026-10-17-commit123"} 1' in text
    finally:
        destroy_ctfd(app)
