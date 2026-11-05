"""The central router and the tokens of the listed hosts (lead engineer, 5 November 2026).

Found by the lead: the token lookup was outside the `try` of the poll loop, so ONE listed host without its ROUTER_TOKEN_<NAME> raised ValueError, the poll task
died, and the router went on serving with no error line and no further route updates for ANY host. Now one bad host is reported once and skipped; the
others keep being polled; and the router refuses to start when a listed host has no token at all.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from services.instancer.router.router import InstanceRouter, get_router_token_for_host

ID_A = "a" * 32
ID_B = "b" * 32


async def _agent(reader, writer):
    """A challenge host's agent: GET /v1/routes answers one route whose id depends on the bearer token it was given."""
    head = await reader.read(4096)
    token = ""
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"authorization:"):
            token = line.split(b"Bearer", 1)[1].strip().decode()
    rid = ID_A if token == "tok-h1" else ID_B
    body = json.dumps({"routes": [{"id": rid, "protocol": "http"}]}).encode()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    await writer.drain()
    writer.close()


def _hosts(port_h1: int, port_h2: int) -> list[dict]:
    return [
        {"name": "h1", "url": f"http://127.0.0.1:{port_h1}", "router_url": "http://127.0.0.1:9"},
        {"name": "h2", "url": f"http://127.0.0.1:{port_h2}", "router_url": "http://127.0.0.1:9"},
    ]


@pytest.mark.asyncio
async def test_poll_loop_survives_a_listed_host_without_a_token_and_says_so_once(monkeypatch, caplog):
    monkeypatch.setenv("ROUTER_TOKEN_H1", "tok-h1")
    monkeypatch.delenv("ROUTER_TOKEN_H2", raising=False)
    monkeypatch.setenv("L3MON_HOST_SECRETS_DIR", "/nonexistent-host-secrets")
    server = await asyncio.start_server(_agent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    router = InstanceRouter(hosts=_hosts(port, port), instance_domain="inst.test", poll_interval=0.2)
    caplog.set_level(logging.INFO, logger="l3mon.instancer.router")
    task = asyncio.create_task(router.poll_central_routes_loop())
    try:
        await asyncio.sleep(1.5)
        assert not task.done(), f"the poll task died: {task.exception() if task.done() else ''}"
        assert ID_A in router._routes, "h1 (which has its token) is still polled"
        assert ID_B not in router._routes, "h2 has no token: nothing is learned from it"
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "h2" in r.getMessage()]
        assert len(errors) == 1, [r.getMessage() for r in caplog.records]
        assert "ROUTER_TOKEN_H2" in errors[0].getMessage()
    finally:
        task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_poll_loop_keeps_polling_the_others_when_one_host_does_not_answer(monkeypatch, caplog):
    monkeypatch.setenv("ROUTER_TOKEN_H1", "tok-h1")
    monkeypatch.setenv("ROUTER_TOKEN_H2", "tok-h2")
    monkeypatch.setenv("L3MON_HOST_SECRETS_DIR", "/nonexistent-host-secrets")
    server = await asyncio.start_server(_agent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    router = InstanceRouter(hosts=_hosts(port, 1), instance_domain="inst.test", poll_interval=0.2)  # h2 points at a port nobody listens on
    caplog.set_level(logging.INFO, logger="l3mon.instancer.router")
    task = asyncio.create_task(router.poll_central_routes_loop())
    try:
        await asyncio.sleep(1.5)
        assert not task.done()
        assert ID_A in router._routes
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "h2" in r.getMessage()]
        assert len(warnings) == 1, "a run of failures is reported once, not every 2 seconds"
    finally:
        task.cancel()
        server.close()


def test_router_token_is_read_from_the_host_secrets_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("L3MON_HOST_SECRETS_DIR", str(tmp_path))
    monkeypatch.delenv("ROUTER_TOKEN_H3", raising=False)
    (tmp_path / "ROUTER_TOKEN_H3").write_text("folder-router-token\n", encoding="utf-8")
    assert get_router_token_for_host("h3") == "folder-router-token"
    with pytest.raises(ValueError, match="ROUTER_TOKEN_H4"):
        get_router_token_for_host("h4")


@pytest.mark.asyncio
async def test_router_refuses_to_start_when_a_listed_host_has_no_token(tmp_path, monkeypatch):
    from services.instancer.router import main as router_main

    hosts_file = tmp_path / "hosts.json"
    hosts_file.write_text(json.dumps(_hosts(8300, 8300)), encoding="utf-8")
    monkeypatch.setenv("ROUTER_HOSTS_PATH", str(hosts_file))
    monkeypatch.setenv("L3MON_HOST_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("ROUTER_TOKEN_H1", "tok-h1")
    monkeypatch.delenv("ROUTER_TOKEN_H2", raising=False)
    with pytest.raises(ValueError, match="ROUTER_TOKEN_H2"):
        await asyncio.wait_for(router_main.main(), timeout=10)
