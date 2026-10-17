"""Tests for l3mon_instances player API (/api/v1/l3mon/instances)."""
import calendar
import datetime
from unittest import mock

import pytest
from freezegun import freeze_time

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_instances.client import InstancerClientError, set_client_override
from CTFd.plugins.l3mon_instances.models import L3monInstance
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user, register_user

PROGRAMMES_API = "/api/v1/l3mon/admin/programmes"
RELEASE_API = "/api/v1/l3mon/admin/release"
T_START = calendar.timegm(datetime.datetime(2026, 11, 28, 4, 30, 0).timetuple())
T_LIVE = T_START + 3600
T_END = T_START + 12 * 3600


class FakeInstancerClient:
    def __init__(self):
        self.instances = {}
        self.next_id = 1
        self.stopped = []

    def request(self, method, path, body=None):
        if method == "POST" and path == "/v1/instances":
            inst_id = f"mockinst{self.next_id:024d}"
            self.next_id += 1
            now_ts = T_LIVE
            exp_ts = now_ts + (body.get("ttl_seconds") or 2700)
            inst = {
                "id": inst_id,
                "team_id": body["team_id"],
                "challenge_id": body["challenge_id"],
                "slug": body["slug"],
                "state": "running",
                "protocol": "http",
                "url": f"http://{inst_id}.instances.l3monctf.org/",
                "host": None,
                "port": None,
                "created_at": now_ts,
                "expires_at": exp_ts,
                "renewals": 0,
            }
            self.instances[inst_id] = inst
            return 201, {"instance": inst}

        elif method == "POST" and path.endswith("/renew"):
            inst_id = path.split("/")[3]
            if inst_id in self.instances:
                inst = self.instances[inst_id]
                inst["renewals"] += 1
                inst["expires_at"] += 2700
                return 200, {"instance": inst}
            raise InstancerClientError(404, "not_found", "Instance not found")

        elif method == "DELETE":
            inst_id = path.split("/")[3]
            self.stopped.append(inst_id)
            if inst_id in self.instances:
                self.instances[inst_id]["state"] = "stopped"
            return 204, {}

        elif method == "POST" and path == "/v1/admin/kill":
            count = len(self.instances)
            self.instances.clear()
            return 200, {"stopped": count}

        return 404, {}


def _setup_world(app):
    set_config("start", T_START)
    set_config("end", T_END)
    app.permanent_session_lifetime = datetime.timedelta(days=3650)

    with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
        admin = login_as_user(app, "admin")
        chal1 = gen_challenge(db, name="Live Chal 1", state="visible")
        chal2 = gen_challenge(db, name="Live Chal 2", state="visible")
        chal3 = gen_challenge(db, name="Live Chal 3", state="visible")
        chal4 = gen_challenge(db, name="Live Chal 4", state="visible")
        chal_static = gen_challenge(db, name="Static Chal", state="visible")
        db.session.commit()

        c1_id = chal1.id
        c2_id = chal2.id
        c3_id = chal3.id
        c4_id = chal4.id
        cs_id = chal_static.id

        r1 = admin.put(PROGRAMMES_API, json={
            "channels": [{"slug": "web", "name": "Web Channel", "position": 1}],
            "programmes": [
                {"challenge_id": c1_id, "channel": "web", "cell": 0, "number": 101, "slug": "chal-1", "delivery": "live_single"},
                {"challenge_id": c2_id, "channel": "web", "cell": 1, "number": 102, "slug": "chal-2", "delivery": "live_single"},
                {"challenge_id": c3_id, "channel": "web", "cell": 2, "number": 103, "slug": "chal-3", "delivery": "live_single"},
                {"challenge_id": c4_id, "channel": "web", "cell": 3, "number": 104, "slug": "chal-4", "delivery": "live_single"},
                {"challenge_id": cs_id, "channel": "web", "cell": 4, "number": 105, "slug": "chal-static", "delivery": "static_shared"},
            ],
        })
        assert r1.status_code == 200, r1.get_data(as_text=True)

        release = [{"kind": "channel", "id": Channel.query.one().id, "mode": "release"}] + [
            {"kind": "programme", "id": p.id, "mode": "release"} for p in Programme.query.all()
        ]
        r2 = admin.put(RELEASE_API, json={"changes": release})
        assert r2.status_code == 200, r2.get_data(as_text=True)

        # Create team client
        register_user(app, name="alice", email="alice@example.com")
        alice = login_as_user(app, "alice")
        alice.get("/team")
        with alice.session_transaction() as s:
            nonce = s.get("nonce")
        alice.post("/teams/new", data={"name": "team_alpha", "password": "password", "nonce": nonce})

    return c1_id, c2_id, c3_id, c4_id, cs_id, alice


def test_player_instances_lifecycle():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c1, c2, c3, c4, cs, alice = _setup_world(app)
        fake_client = FakeInstancerClient()
        set_client_override(fake_client)

        with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
            with mock.patch("CTFd.plugins.l3mon_instances.api.get_flag_secret", return_value="test-secret"):
                # 1. Start instance
                r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1})
                assert r.status_code == 201
                body = r.get_json()
                assert body["success"] is True
                assert body["data"]["challenge_id"] == c1
                assert body["data"]["state"] == "running"
                assert body["data"]["protocol"] == "http"

                # 2. Idempotent start returns existing instance (200)
                r_dup = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1})
                assert r_dup.status_code == 200
                assert r_dup.get_json()["data"]["challenge_id"] == c1

                # 3. GET /instances lists live instances and limits
                r_get = alice.get("/api/v1/l3mon/instances")
                assert r_get.status_code == 200
                get_body = r_get.get_json()["data"]
                assert len(get_body["instances"]) == 1
                assert get_body["limits"]["live"] == 1
                assert get_body["limits"]["max"] == 3
                assert get_body["limits"]["cooldown_seconds"] == 60

                # 4. Renew instance
                r_renew = alice.post(f"/api/v1/l3mon/instances/{c1}/renew", json={})
                assert r_renew.status_code == 200
                assert r_renew.get_json()["success"] is True

                # 5. Stop instance
                r_stop = alice.delete(f"/api/v1/l3mon/instances/{c1}", json={})
                assert r_stop.status_code == 200
                assert r_stop.get_json()["success"] is True

                # 6. Idempotent stop returns 200
                r_stop2 = alice.delete(f"/api/v1/l3mon/instances/{c1}", json={})
                assert r_stop2.status_code == 200

                # 7. Immediate restart hits 60s cooldown (429)
                r_cd = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1})
                assert r_cd.status_code == 429
                assert r_cd.get_json()["error"] == "cooldown"
                assert "retry_after" in r_cd.get_json()

    set_client_override(None)
    destroy_ctfd(app)


def test_player_instances_quota():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c1, c2, c3, c4, cs, alice = _setup_world(app)
        fake_client = FakeInstancerClient()
        set_client_override(fake_client)

        with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
            with mock.patch("CTFd.plugins.l3mon_instances.api.get_flag_secret", return_value="test-secret"):
                # Start 3 instances (the max quota)
                assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1}).status_code == 201
                assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c2}).status_code == 201
                assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c3}).status_code == 201

                # 4th instance must fail with quota error (409)
                r_quota = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c4})
                assert r_quota.status_code == 409
                assert r_quota.get_json()["error"] == "quota"

    set_client_override(None)
    destroy_ctfd(app)


def test_player_instances_event_phases():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c1, c2, c3, c4, cs, alice = _setup_world(app)
        fake_client = FakeInstancerClient()
        set_client_override(fake_client)

        with mock.patch("CTFd.plugins.l3mon_instances.api.get_flag_secret", return_value="test-secret"):
            # Before contest
            with freeze_time(datetime.datetime.utcfromtimestamp(T_START - 100)):
                r_before = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1})
                assert r_before.status_code == 409
                assert r_before.get_json()["error"] == "not_live"

            # After contest
            with freeze_time(datetime.datetime.utcfromtimestamp(T_END + 100)):
                r_after = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1})
                assert r_after.status_code == 409
                assert r_after.get_json()["error"] == "not_live"

            # Paused contest
            with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
                set_config("paused", True)
                r_paused = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1})
                assert r_paused.status_code == 409
                assert r_paused.get_json()["error"] == "not_live"
                set_config("paused", False)

    set_client_override(None)
    destroy_ctfd(app)


def test_player_instances_plain_digit_validation():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c1, c2, c3, c4, cs, alice = _setup_world(app)
        fake_client = FakeInstancerClient()
        set_client_override(fake_client)

        with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
            # Invalid IDs must return 404 (not_found)
            invalid_ids = ["6.0", "6e0", "6abc", "+6", " 6", "6\n", ""]
            for bad_id in invalid_ids:
                r_post = alice.post("/api/v1/l3mon/instances", json={"challenge_id": bad_id})
                assert r_post.status_code == 404, f"Failed on POST with {bad_id!r}"
                assert r_post.get_json()["error"] == "not_found"

                if bad_id:
                    r_renew = alice.post(f"/api/v1/l3mon/instances/{bad_id}/renew", json={})
                    assert r_renew.status_code == 404, f"Failed on RENEW with {bad_id!r}"
                    assert r_renew.get_json()["error"] == "not_found"

                    r_del = alice.delete(f"/api/v1/l3mon/instances/{bad_id}", json={})
                    assert r_del.status_code == 404, f"Failed on DELETE with {bad_id!r}"
                    assert r_del.get_json()["error"] == "not_found"

            # Static challenge delivery must return 404
            r_static = alice.post("/api/v1/l3mon/instances", json={"challenge_id": cs})
            assert r_static.status_code == 404

            # Non-existent challenge id must return 404
            r_nonexist = alice.post("/api/v1/l3mon/instances", json={"challenge_id": 99999})
            assert r_nonexist.status_code == 404

    set_client_override(None)
    destroy_ctfd(app)


def test_player_instances_no_team():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    app.permanent_session_lifetime = datetime.timedelta(days=3650)
    with app.app_context():
        set_config("start", T_START)
        set_config("end", T_END)
        chal = gen_challenge(db, name="Live Chal")
        chal_id = chal.id
        db.session.commit()

        with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
            register_user(app, name="solo_player", email="solo@example.com")
            solo = login_as_user(app, "solo_player")

            # Player without a team gets 403 no_team
            r_get = solo.get("/api/v1/l3mon/instances")
            assert r_get.status_code == 403
            assert r_get.get_json()["error"] == "no_team"

            r_post = solo.post("/api/v1/l3mon/instances", json={"challenge_id": chal_id})
            assert r_post.status_code == 403
            assert r_post.get_json()["error"] == "no_team"

    destroy_ctfd(app)


def test_a_player_cannot_choose_how_long_their_instance_lives():
    """Found by the integration audit: the player API forwarded the request's `ttl_seconds` to the instancer, so the only thing between a player and an
    instance that lives for a year was the instancer's development switch. The field is ignored unless the platform itself is run in a test mode."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c1, c2, c3, c4, cs, alice = _setup_world(app)
        seen = []

        class Spy(FakeInstancerClient):
            def request(self, method, path, body=None):
                if method == "POST" and path == "/v1/instances":
                    seen.append(dict(body))
                return super().request(method, path, body)

        set_client_override(Spy())
        with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
            with mock.patch("CTFd.plugins.l3mon_instances.api.get_flag_secret", return_value="test-secret"):
                r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1, "ttl_seconds": 31536000})
                assert r.status_code == 201
        assert seen and "ttl_seconds" not in seen[0], seen
        assert r.get_json()["data"]["ends"] - T_LIVE <= 2700 if "ends" in r.get_json()["data"] else True
    set_client_override(None)
    destroy_ctfd(app)


def test_the_ttl_override_exists_only_when_the_test_switch_is_on(monkeypatch):
    monkeypatch.setenv("L3MON_INSTANCES_TTL_OVERRIDE", "1")
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c1, c2, c3, c4, cs, alice = _setup_world(app)
        seen = []

        class Spy(FakeInstancerClient):
            def request(self, method, path, body=None):
                if method == "POST" and path == "/v1/instances":
                    seen.append(dict(body))
                return super().request(method, path, body)

        set_client_override(Spy())
        with freeze_time(datetime.datetime.utcfromtimestamp(T_LIVE)):
            with mock.patch("CTFd.plugins.l3mon_instances.api.get_flag_secret", return_value="test-secret"):
                assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c1, "ttl_seconds": 120}).status_code == 201
        assert seen and seen[0].get("ttl_seconds") == 120
    set_client_override(None)
    destroy_ctfd(app)
