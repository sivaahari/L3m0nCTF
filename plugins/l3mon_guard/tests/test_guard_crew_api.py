"""Tests for crew evidence API and review page (SP5 B4.7).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_crew_api.py
"""
import datetime
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

from CTFd.models import Users, db
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_core.models import Audit
from CTFd.plugins.l3mon_guard.evidence import record
from CTFd.plugins.l3mon_guard.models import Evidence
from CTFd.utils.security.auth import generate_user_token


def _admin_headers():
    admin_user = Users.query.filter_by(name="admin").first()
    token = generate_user_token(admin_user).value
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def test_crew_evidence_requires_admin():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        register_user(app, name="player", email="player@example.com")
        with app.test_client() as anonymous:
            r = anonymous.get("/api/v1/l3mon/admin/guard/evidence")
            assert r.status_code in (302, 303, 403)

        with login_as_user(app, name="player") as client:
            r = client.get("/api/v1/l3mon/admin/guard/evidence")
            assert r.status_code in (302, 403)
    destroy_ctfd(app)


def test_crew_evidence_listing_and_filtering():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        record("burst", detail="burst on team 1")
        record("flag_reuse", detail="reuse between 2 and 1")

        with login_as_user(app, name="admin") as admin:
            # All evidence
            r = admin.get("/api/v1/l3mon/admin/guard/evidence")
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert len(data) >= 2

            # Filter by kind
            r_kind = admin.get("/api/v1/l3mon/admin/guard/evidence?kind=burst")
            assert r_kind.status_code == 200
            data_kind = r_kind.get_json()["data"]
            assert len(data_kind) == 1
            assert data_kind[0]["kind"] == "burst"
    destroy_ctfd(app)


def test_crew_evidence_update_writes_audit():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        record("shared_address", detail="3 teams on IP")
        row = Evidence.query.filter_by(kind="shared_address").first()
        assert row is not None

        headers = _admin_headers()
        with app.test_client() as client:
            r = client.post(
                f"/api/v1/l3mon/admin/guard/evidence/{row.id}",
                json={"status": "reviewed", "note": "Verified harmless campus proxy."},
                headers=headers,
            )
            assert r.status_code == 200
            res = r.get_json()
            assert res["success"] is True
            assert res["data"]["status"] == "reviewed"

            # Verify in DB
            db.session.expire_all()
            updated = Evidence.query.filter_by(id=row.id).first()
            assert updated.status == "reviewed"
            assert updated.note == "Verified harmless campus proxy."

            # Verify audit trail
            audit_entry = Audit.query.filter_by(target=f"evidence:{row.id}").first()
            assert audit_entry is not None
            assert "reviewed" in audit_entry.detail
    destroy_ctfd(app)


def test_crew_evidence_prune_removes_old_rows():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        # Insert one old row and one recent row
        old_time = datetime.datetime.utcfromtimestamp(now() - 45 * 86400)
        old_row = Evidence(kind="burst", detail="old", created_at=old_time, status="new")
        db.session.add(old_row)
        db.session.commit()

        record("burst", detail="recent")

        headers = _admin_headers()
        with app.test_client() as client:
            r = client.delete("/api/v1/l3mon/admin/guard/evidence?older_than_days=30", headers=headers)
            assert r.status_code == 200
            data = r.get_json()["data"]
            assert data["pruned"] >= 1

            # Assert old row is deleted and recent remains
            assert Evidence.query.filter_by(detail="old").first() is None
            assert Evidence.query.filter_by(detail="recent").first() is not None
    destroy_ctfd(app)


def test_crew_guard_page_renders_for_admin():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with login_as_user(app, name="admin") as admin:
            r = admin.get("/admin/l3mon/guard")
            assert r.status_code == 200
            assert "Anti-abuse evidence review" in r.get_data(as_text=True)
            assert "guard-app" in r.get_data(as_text=True)
    destroy_ctfd(app)
