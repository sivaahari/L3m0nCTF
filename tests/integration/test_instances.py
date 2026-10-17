"""Integration tests for per-team challenge instances and dynamic flags on the running stack.

Tests:
1. Contest live window management.
2. Challenge and Programme setup (delivery="live_single", slug="demo-web").
3. Player API starts instance (POST /api/v1/l3mon/instances).
4. Traffic route through Router container (port 8090) reaches instance /flag endpoint.
5. Dynamic flag validation: Team A submits derived flag to CTFd -> correct.
6. Flag reuse detection: Team B submits Team A's flag -> incorrect.
7. Instance renewal (POST /api/v1/l3mon/instances/{id}/renew).
8. Instancer restart recovery: active containers recovered from Docker labels.
9. Router recovery: router continues routing after restart.
10. Instance stop (DELETE /api/v1/l3mon/instances/{id}) and 60s cooldown refusal (429).
"""
from __future__ import annotations

import http.client
import json
import os
import secrets as pysecrets
import subprocess
import time
import urllib.parse
import urllib.request

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

PRESET = t.ROOT / "deploy" / "compose" / "generated" / "preset_configs.json"
PROGRAMMES_API = "/api/v1/l3mon/admin/programmes"
RELEASE_API = "/api/v1/l3mon/admin/release"
ROUTER_PORT = 8090


def admin(method: str, path: str, body=None):
    headers = {
        "Authorization": f"Token {t.secret('PRESET_ADMIN_TOKEN')}",
        "Content-Type": "application/json",
    }
    status, _, data = t.request(
        path,
        method=method,
        headers=headers,
        body=json.dumps(body).encode() if body is not None else None,
    )
    try:
        return status, json.loads(data)
    except ValueError:
        return status, data


def post_json(session: t.Session, path: str, body: dict):
    token = session.nonce("/challenges")
    req = urllib.request.Request(
        f"http://127.0.0.1:{t.PORT}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Host": t.HOST,
            "Content-Type": "application/json",
            "CSRF-Token": token,
        },
    )
    try:
        response = session.opener.open(req, timeout=20)
    except Exception as e:
        response = e
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode("utf-8", "replace")


def delete_json(session: t.Session, path: str):
    token = session.nonce("/challenges")
    req = urllib.request.Request(
        f"http://127.0.0.1:{t.PORT}{path}",
        data=b"{}",
        method="DELETE",
        headers={
            "Host": t.HOST,
            "Content-Type": "application/json",
            "CSRF-Token": token,
        },
    )
    try:
        response = session.opener.open(req, timeout=20)
    except Exception as e:
        response = e
    raw = response.read()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw.decode("utf-8", "replace")


def ctfd_overlays(current: str) -> str:
    """Every overlay the stack runs with, and `instancer` as well.

    Integration audit: the test used to recreate ctfd with `instancer` alone, which on a stack started with
    identity,instancer,ops dropped the operations overlay's host names (ctfd, localhost); Prometheus and the readiness
    probe reach the application as `ctfd:8000`, so from then on every scrape got a 500 ("Host is not trusted").
    """
    names = [name for name in current.split(",") if name]
    if "instancer" not in names:
        names.append("instancer")
    return ",".join(names)


def test_recreating_ctfd_keeps_every_overlay_the_stack_was_started_with():
    assert ctfd_overlays("identity,instancer,ops") == "identity,instancer,ops"
    assert ctfd_overlays("ops") == "ops,instancer"
    assert ctfd_overlays("") == "instancer"
    assert ctfd_overlays("ops,") == "ops,instancer"


def restart_ctfd():
    t.compose(
        "up", "-d", "--wait", "--force-recreate", "--no-deps", "ctfd",
        timeout=400,
        env={"L3MON_OVERLAYS": ctfd_overlays(os.environ.get("L3MON_OVERLAYS", ""))},
    )
    t.wait_healthy("ctfd", 180)


def make_player(name: str):
    password = pysecrets.token_urlsafe(18)
    status, out = admin("POST", "/api/v1/users", {
        "name": name,
        "email": f"{name}@example.com",
        "password": password,
        "type": "user",
        "verified": True,
    })
    assert status == 200, out
    user_id = out["data"]["id"]

    session = t.Session()
    answer = session.open("/login", {
        "name": name,
        "password": password,
        "_submit": "Submit",
        "nonce": session.nonce("/login"),
    })
    assert answer.status == 302, f"sign-in failed: {answer.status}"

    answer = session.open("/teams/new", {
        "name": f"{name}-team",
        "password": pysecrets.token_urlsafe(12),
        "_submit": "Create",
        "nonce": session.nonce("/teams/new"),
    })
    assert answer.status == 302, f"team creation failed: {answer.status}"

    status, who = admin("GET", f"/api/v1/users/{user_id}")
    return session, user_id, who["data"]["team_id"]


def request_router(host_header: str, path: str = "/flag", timeout: int = 10):
    conn = http.client.HTTPConnection("127.0.0.1", ROUTER_PORT, timeout=timeout)
    conn.request("GET", path, headers={"Host": host_header})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def in_ctfd(code: str) -> str:
    out = subprocess.run([
        "docker", "exec", t.container_id("ctfd"),
        "l3mon-run", "/opt/venv/bin/python", "-c", code
    ], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-600:]
    return out.stdout


CLEANUP = "\n".join([
    "from CTFd import create_app",
    "from CTFd.models import Notifications, db",
    "from CTFd.plugins.l3mon_core.models import Audit, Channel",
    "from CTFd.plugins.l3mon_instances.models import L3monInstance, L3monInstanceLog",
    "from CTFd.cache import clear_challenges, clear_standings, cache",
    "app = create_app()",
    "with app.app_context():",
    "    try:",
    "        L3monInstance.query.delete()",
    "        L3monInstanceLog.query.delete()",
    "    except Exception:",
    "        pass",
    "    Channel.query.delete()",
    "    Notifications.query.filter(Notifications.title == 'New on air').delete()",
    "    Audit.query.delete()",
    "    db.session.commit()",
    "    clear_challenges(); clear_standings(); cache.delete('l3mon:release:next')",
    "    print('CLEANED')",
])


@pytest.fixture(scope="module")
def world():
    original = PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    try:
        restart_ctfd()
        in_ctfd(CLEANUP)
        yield
    finally:
        try:
            in_ctfd(CLEANUP)
        except Exception:
            pass
        PRESET.write_bytes(original)
        restart_ctfd()


def test_instances_end_to_end_lifecycle(world):
    # 1. Create Challenge in CTFd
    status, out = admin("POST", "/api/v1/challenges", {
        "name": "Demo Web Instanced",
        "category": "Web",
        "description": "Solve the web challenge",
        "value": 100,
        "state": "visible",
        "type": "standard",
    })
    assert status == 200, out
    chal_id = out["data"]["id"]

    # 2. Add Dynamic Flag for this challenge
    status, out = admin("POST", "/api/v1/flags", {
        "challenge_id": chal_id,
        "content": "",
        "type": "l3mon_dynamic",
        "data": "",
    })
    assert status == 200, out

    # 3. Create Channel and Programme with delivery="live_single" and slug="demo-web"
    chan_slug = f"chan-{pysecrets.token_hex(4)}"
    status, out = admin("PUT", PROGRAMMES_API, {
        "channels": [{"slug": chan_slug, "name": "Live Channel", "position": 1}],
        "programmes": [{
            "challenge_id": chal_id,
            "channel": chan_slug,
            "cell": 0,
            "number": 201,
            "slug": "demo-web",
            "delivery": "live_single",
        }],
    })
    assert status == 200, out

    # Release channel and programme
    status, view = admin("GET", RELEASE_API)
    assert status == 200, view
    chan_id = view["data"]["channels"][0]["id"]
    prog_id = view["data"]["channels"][0]["programmes"][0]["id"]
    status, _ = admin("PUT", RELEASE_API, {
        "changes": [
            {"kind": "channel", "id": chan_id, "mode": "release"},
            {"kind": "programme", "id": prog_id, "mode": "release"},
        ]
    })
    assert status == 200

    # 4. Make two players (Team A and Team B)
    alice_name = f"alice_{pysecrets.token_hex(4)}"
    bob_name = f"bob_{pysecrets.token_hex(4)}"
    session_a, uid_a, team_a = make_player(alice_name)
    session_b, uid_b, team_b = make_player(bob_name)

    instance_id = None
    try:
        # 5. Team A starts instance
        status, resp = post_json(session_a, "/api/v1/l3mon/instances", {"challenge_id": chal_id})
        assert status == 201, resp
        assert resp["success"] is True
        data = resp["data"]
        assert data["protocol"] == "http"
        assert data["state"] == "running"
        assert data["url"] is not None

        parsed = urllib.parse.urlparse(data["url"])
        host_header = parsed.netloc
        instance_id = parsed.netloc.split(".")[0]

        # 6. Wait for Router to learn route (polls every 2s) and query /flag
        time.sleep(3)
        router_status, flag_bytes = request_router(host_header, "/flag")
        assert router_status == 200, f"Router returned {router_status}: {flag_bytes}"
        flag_text = flag_bytes.decode("utf-8").strip()
        assert flag_text.startswith("L3m0n" + "{") and flag_text.endswith("}")

        # 7. Team A submits flag to CTFd -> CORRECT
        status, attempt_resp = post_json(session_a, "/api/v1/challenges/attempt", {
            "challenge_id": chal_id,
            "submission": flag_text,
        })
        assert status == 200, attempt_resp
        assert attempt_resp["data"]["status"] == "correct"

        # 8. Team B submits Team A's flag -> INCORRECT (flag sharing detection)
        status, attempt_b = post_json(session_b, "/api/v1/challenges/attempt", {
            "challenge_id": chal_id,
            "submission": flag_text,
        })
        assert status == 200, attempt_b
        assert attempt_b["data"]["status"] == "incorrect"

        # 9. Renew instance
        status, renew_resp = post_json(session_a, f"/api/v1/l3mon/instances/{chal_id}/renew", {})
        assert status == 200, renew_resp
        assert renew_resp["success"] is True

        # 10. Restart Instancer and Router - test recovery from labels
        subprocess.run(["docker", "restart", "l3mon-instancer-1"], check=True)
        time.sleep(2)
        subprocess.run(["docker", "restart", "l3mon-router-1"], check=True)
        time.sleep(4)

        # Confirm router still serves the instance after restart
        router_status, flag_after = request_router(host_header, "/flag")
        assert router_status == 200
        assert flag_after.decode("utf-8").strip() == flag_text

        # 11. Team A stops instance
        status, stop_resp = delete_json(session_a, f"/api/v1/l3mon/instances/{chal_id}")
        assert status == 200, stop_resp

        # 12. Immediate restart hits 60s cooldown (429)
        status, cd_resp = post_json(session_a, "/api/v1/l3mon/instances", {"challenge_id": chal_id})
        assert status == 429, cd_resp
        assert cd_resp["error"] == "cooldown"

    finally:
        # Cleanup
        if instance_id:
            delete_json(session_a, f"/api/v1/l3mon/instances/{chal_id}")
        admin("DELETE", f"/api/v1/challenges/{chal_id}")
        admin("DELETE", f"/api/v1/users/{uid_a}")
        admin("DELETE", f"/api/v1/users/{uid_b}")
