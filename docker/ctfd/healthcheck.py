"""The container's health check: ask CTFd's own /healthcheck route from inside, with a Host header CTFd trusts.

CTFd only answers for the host names in TRUSTED_HOSTS, and "127.0.0.1" is not one of them, so the request names the first one.
"""
import os
import sys
import urllib.request

hosts = [h.strip().lstrip(".") for h in os.environ.get("TRUSTED_HOSTS", "").split(",") if h.strip()]
request = urllib.request.Request("http://127.0.0.1:8000/healthcheck", headers={"Host": hosts[0] if hosts else "localhost"})
try:
    with urllib.request.urlopen(request, timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
