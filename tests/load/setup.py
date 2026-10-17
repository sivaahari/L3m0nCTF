#!/usr/bin/env python3
"""tests/load/setup.py - Provision synthetic test accounts and data for k6 load test.

Creates players and studios prefixed with `zzload-`.
Saves credentials to tests/load/users.json for k6 VUs to consume.
Enforces target allow-list validation.
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
    "play.l3m0nctf.xyz",
]


def check_target(base: str) -> str:
    parts = urllib.parse.urlsplit(base)
    host = (parts.netloc or parts.path).lower()
    if not host:
        sys.exit(f"[SAFETY RAIL REFUSAL] Invalid target URL: {base}")
    if host not in ALLOW_LIST and host.split(":")[0] not in ALLOW_LIST:
        sys.exit(f"[SAFETY RAIL REFUSAL] Target '{host}' is NOT in allow-list ({ALLOW_LIST}). Refusing setup.")
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


def setup(base: str, token: str, count: int = 50):
    client = CTFdClient(base, token)
    users = []

    print(f"Setting up {count} test users with prefix 'zzload' on {base} ...")
    for i in range(1, count + 1):
        username = f"zzload_user_{i:03d}"
        email = f"zzload_user_{i:03d}@example.com"
        password = "testpassword123"

        payload = {
            "name": username,
            "email": email,
            "password": password,
            "type": "user",
            "verified": True,
            "hidden": True,
        }
        status, res = client.request("POST", "/api/v1/users", payload)
        if status in (200, 201) and "data" in res:
            user_id = res["data"]["id"]
            users.append({"id": user_id, "name": username, "email": email, "password": password})
        elif status == 400 and "already exists" in str(res):
            users.append({"name": username, "email": email, "password": password})
        else:
            print(f"Warning: could not create {username}: {res}")

    out_file = os.path.join(os.path.dirname(__file__), "users.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)

    print(f"[SETUP COMPLETE] {len(users)} users provisioned and saved to {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Provision zzload accounts for load testing")
    parser.add_argument("--base", default="http://localhost:8080", help="CTFd base URL")
    parser.add_argument("--token-file", default=".secrets/PRESET_ADMIN_TOKEN", help="Admin token file")
    parser.add_argument("--count", type=int, default=50, help="Number of test accounts to create")
    args = parser.parse_args()

    token = ""
    if os.path.exists(args.token_file):
        with open(args.token_file, "r") as f:
            token = f.read().strip()
    if not token:
        token = os.environ.get("ADMIN_TOKEN", "")

    if not token:
        sys.exit("Error: no administrator token available.")

    setup(args.base, token, args.count)


if __name__ == "__main__":
    main()
