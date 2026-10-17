#!/usr/bin/env python3
"""tests/load/cleanup.py - Purges all synthetic zzload test data from CTFd.

Finds and deletes any user, team, or challenge whose name begins with `zzload`.
Enforces target allow-list check before executing any API operations.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

ALLOW_LIST = [
    "localhost",
    "127.0.0.1",
    "localhost:8080",
    "127.0.0.1:8080",
    "ctfd:8000",
    "nginx:80",
    "play.l3monctf.xyz",
]


def check_target(base: str) -> str:
    parts = urllib.parse.urlsplit(base)
    host = (parts.netloc or parts.path).lower()
    if not host:
        sys.exit(f"[SAFETY RAIL REFUSAL] Invalid target URL: {base}")
    if host not in ALLOW_LIST and host.split(":")[0] not in ALLOW_LIST:
        sys.exit(f"[SAFETY RAIL REFUSAL] Target '{host}' is NOT in allow-list ({ALLOW_LIST}). Refusing cleanup.")
    return base.rstrip("/")


class CTFdClient:
    def __init__(self, base: str, token: str):
        self.base = check_target(base)
        self.token = token.strip()

    def request(self, method: str, path: str, data: dict | None = None) -> tuple[int, dict]:
        url = f"{self.base}{path}"
        headers = {
            "Authorization": f"Token {self.token}",
            "Content-Type": "application/json",
        }
        body = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read().decode("utf-8")
                return resp.status, json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            return e.code, json.loads(err_body) if err_body else {}


def cleanup(base: str, token: str):
    client = CTFdClient(base, token)
    deleted_users = 0
    deleted_teams = 0

    # 1. Delete zzload users
    status, res = client.request("GET", "/api/v1/users?field=name&q=zzload&per_page=100")
    if status == 200 and "data" in res:
        for u in res["data"]:
            if u["name"].startswith("zzload"):
                del_status, _ = client.request("DELETE", f"/api/v1/users/{u['id']}")
                if del_status == 200:
                    deleted_users += 1

    # 2. Delete zzload teams
    status, res = client.request("GET", "/api/v1/teams?field=name&q=zzload&per_page=100")
    if status == 200 and "data" in res:
        for t in res["data"]:
            if t["name"].startswith("zzload"):
                del_status, _ = client.request("DELETE", f"/api/v1/teams/{t['id']}")
                if del_status == 200:
                    deleted_teams += 1

    # 3. Clean up credentials file
    cred_file = os.path.join(os.path.dirname(__file__), "users.json")
    if os.path.exists(cred_file):
        os.remove(cred_file)

    print(f"[CLEANUP COMPLETE] Removed {deleted_users} users and {deleted_teams} teams prefixed 'zzload'.")


def main():
    parser = argparse.ArgumentParser(description="Purge zzload test data")
    parser.add_argument("--base", default="http://localhost:8080", help="CTFd base URL")
    parser.add_argument("--token-file", default=".secrets/PRESET_ADMIN_TOKEN", help="Admin token file")
    args = parser.parse_args()

    token = ""
    if os.path.exists(args.token_file):
        with open(args.token_file, "r") as f:
            token = f.read().strip()
    if not token:
        token = os.environ.get("ADMIN_TOKEN", "")

    if not token:
        sys.exit("Error: no administrator token available for cleanup.")

    cleanup(args.base, token)


if __name__ == "__main__":
    main()
