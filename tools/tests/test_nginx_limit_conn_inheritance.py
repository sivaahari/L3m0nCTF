"""nginx's limit_conn is inherited from the server block only by a location that has no limit_conn of its own.

The busy mode of the operations piece adds `limit_conn app_general 250;` to `location /` and `limit_conn app_flags 60;` to the flag route. Without a
`limit_conn perip ...` beside them those two locations quietly lost the per-address connection cap the server block gives every other location, so one
address could hold the whole global allowance and the site would answer "busy" to everybody else (found by the integration audit, 2026-10-18).
This reads the shared server rules and checks that every location that sets a limit_conn also sets the per-address one."""
from __future__ import annotations

import re
from pathlib import Path

SNIPPET = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "snippets" / "platform-server.conf"


def blocks(text: str):
    """(header, body) for each `location ... { ... }` block, with the braces of the body balanced (no location here nests another)."""
    out = []
    for match in re.finditer(r"^\s*(location\s+[^{]+?)\s*\{", text, flags=re.M):
        depth, i = 1, match.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append((match.group(1).strip(), text[match.end():i - 1]))
    return out


def test_the_server_block_has_the_per_address_cap():
    server_level = SNIPPET.read_text(encoding="utf-8")
    for header, body in blocks(server_level):
        server_level = server_level.replace(body, "")
    assert re.search(r"^\s*limit_conn\s+perip\s+\d+;", server_level, flags=re.M)


def test_every_location_with_a_limit_conn_of_its_own_keeps_the_per_address_cap():
    problems = []
    for header, body in blocks(SNIPPET.read_text(encoding="utf-8")):
        zones = re.findall(r"^\s*limit_conn\s+(\w+)\s+\d+;", body, flags=re.M)
        if zones and "perip" not in zones:
            problems.append(f"{header}: limit_conn on {zones} replaces the server block's `limit_conn perip`, add it")
    assert not problems, "\n".join(problems)


def test_the_two_global_caps_exist_where_the_busy_mode_needs_them():
    text = SNIPPET.read_text(encoding="utf-8")
    by_header = {header: body for header, body in blocks(text)}
    assert "limit_conn app_general" in by_header["location /"]
    assert "limit_conn app_flags" in by_header["location = /api/v1/challenges/attempt"]
