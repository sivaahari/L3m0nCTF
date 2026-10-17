"""Tests for l3mon_instances crew admin API (/api/v1/l3mon/admin/instances)."""
import calendar
import datetime
from unittest import mock

import pytest

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.models import Audit
from CTFd.plugins.l3mon_instances.client import set_client_override
from CTFd.plugins.l3mon_instances.models import L3monInstance
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user, register_user


class MockAdminInstancerClient:
    def __init__(self):
        self.killed = []

    def request(self, method, path, body=None):
        if method == "POST" and path == "/v1/admin/kill":
            self.killed.append(body or {})
            return 200, {"stopped": 2}
        return 404, {}


def test_admin_instances_list_and_kill():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        register_user(app, name="player", email="player@example.com")
        player = login_as_user(app, "player")

        chal1 = gen_challenge(db, name="Admin Chal 1")
        chal2 = gen_challenge(db, name="Admin Chal 2")
        c1_id = chal1.id
        c2_id = chal2.id

        team1 = Teams(name="team_adm1", email="team_adm1@example.com")
        team2 = Teams(name="team_adm2", email="team_adm2@example.com")
        db.session.add_all([team1, team2])
        db.session.commit()
        t1_id = team1.id
        t2_id = team2.id

        now_dt = datetime.datetime.utcnow()
        expires = now_dt + datetime.timedelta(minutes=45)

        inst1 = L3monInstance(
            instance_id="instadm0100000000000000000000000",
            team_id=t1_id,
            challenge_id=c1_id,
            slug="adm-chal-1",
            protocol="http",
            state="running",
            host_name="default",
            created_at=now_dt,
            expires_at=expires,
            active_key=f"{t1_id}:{c1_id}",
        )
        inst2 = L3monInstance(
            instance_id="instadm0200000000000000000000000",
            team_id=t2_id,
            challenge_id=c2_id,
            slug="adm-chal-2",
            protocol="http",
            state="running",
            host_name="default",
            created_at=now_dt,
            expires_at=expires,
            active_key=f"{t2_id}:{c2_id}",
        )
        db.session.add_all([inst1, inst2])
        db.session.commit()

        # 1. Non-admin is refused (302 or 403)
        assert player.get("/api/v1/l3mon/admin/instances").status_code in (302, 403)
        assert player.post("/api/v1/l3mon/admin/instances/kill", json={}).status_code == 403

        # 2. Admin lists instances
        r_list = admin.get("/api/v1/l3mon/admin/instances")
        assert r_list.status_code == 200
        instances_data = r_list.get_json()["data"]["instances"]
        assert len(instances_data) == 2

        # 3. Filter by team_id
        r_filter_team = admin.get(f"/api/v1/l3mon/admin/instances?team_id={t1_id}")
        assert r_filter_team.status_code == 200
        assert len(r_filter_team.get_json()["data"]["instances"]) == 1

        # 4. Filter with invalid param
        assert admin.get("/api/v1/l3mon/admin/instances?team_id=1abc").status_code == 400

        # 5. Kill instances with mock client
        mock_client = MockAdminInstancerClient()
        set_client_override(mock_client)

        r_kill = admin.post("/api/v1/l3mon/admin/instances/kill", json={"team_id": t1_id})
        assert r_kill.status_code == 200
        assert r_kill.get_json()["data"]["stopped"] == 2

        # Verify audit row created
        audit_row = Audit.query.filter_by(action="instances.kill").order_by(Audit.id.desc()).first()
        assert audit_row is not None
        assert f"team:{t1_id}" in audit_row.target

        # Verify DB rows updated
        db.session.expire_all()
        i1 = L3monInstance.query.filter_by(instance_id="instadm0100000000000000000000000").one()
        assert i1.state == "stopping"
        assert i1.active_key is None

        set_client_override(None)

    destroy_ctfd(app)
