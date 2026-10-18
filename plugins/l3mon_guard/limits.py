"""Rate limiting, campus network allowlisting, and abuse heuristics (SP5 B4.6, B4.8).

Rules:
- Per-team flag attempt limit (default 40 flag attempts/min across challenges)
- Answers 429 with Retry-After when team limit exceeded
- Records 'burst' evidence once per window
- Campus allow-list (L3MON_GUARD_CAMPUS_NETS) ignores address-based heuristics
- 'shared_address': >= 3 distinct teams from one non-campus address in 10 min
- 'registration_burst': >= threshold registrations from one non-campus address in 10 min
- Never block on an address alone (address only produces evidence)
"""
import ipaddress
import logging
import os
import time

from CTFd.cache import cache
from CTFd.plugins.l3mon_guard.evidence import record

_log = logging.getLogger("l3mon.guard")

TEAM_ATTEMPT_WINDOW = 60  # seconds
TEAM_ATTEMPT_LIMIT_DEFAULT = 40
SHARED_ADDRESS_WINDOW = 600  # 10 minutes
SHARED_TEAMS_THRESHOLD = 3
REG_BURST_WINDOW = 600  # 10 minutes
REG_BURST_THRESHOLD_DEFAULT = 5


def get_team_attempt_limit() -> int:
    raw = os.environ.get("L3MON_GUARD_TEAM_LIMIT", "").strip()
    try:
        return int(raw) if raw else TEAM_ATTEMPT_LIMIT_DEFAULT
    except ValueError:
        return TEAM_ATTEMPT_LIMIT_DEFAULT


def get_reg_burst_threshold() -> int:
    raw = os.environ.get("L3MON_GUARD_REG_BURST_THRESHOLD", "").strip()
    try:
        return int(raw) if raw else REG_BURST_THRESHOLD_DEFAULT
    except ValueError:
        return REG_BURST_THRESHOLD_DEFAULT


def get_campus_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = os.environ.get("L3MON_GUARD_CAMPUS_NETS", "").strip()
    if not raw:
        return []
    nets = []
    for cidr in raw.split(","):
        cidr = cidr.strip()
        if cidr:
            try:
                nets.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                _log.warning("l3mon_guard: invalid campus network CIDR '%s'", cidr)
    return nets


def is_campus_ip(ip_str: str) -> bool:
    """Check if client IP belongs to any configured campus network CIDR."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False

    for net in get_campus_networks():
        if ip in net:
            return True
    return False


def _get_backend_client():
    backend = cache.cache
    client = getattr(backend, "_write_client", None) or getattr(backend, "_read_client", None)
    return backend, client


def check_and_record_team_attempt(team_id: int) -> tuple[bool, int]:
    """Check if team has exceeded flag attempt limit.

    Returns (is_allowed, retry_after_seconds).
    """
    if not team_id:
        return True, 0

    limit = get_team_attempt_limit()
    key = f"guard:team_attempts:{team_id}"
    backend, client = _get_backend_client()

    if client is not None:
        prefix = getattr(backend, "key_prefix", "")
        full_key = prefix + key
        pipe = client.pipeline()
        pipe.incr(full_key)
        pipe.ttl(full_key)
        results = pipe.execute()
        count = int(results[0])
        ttl = int(results[1])
        if ttl == -1:
            client.expire(full_key, TEAM_ATTEMPT_WINDOW)
            ttl = TEAM_ATTEMPT_WINDOW
        elif ttl <= 0:
            ttl = TEAM_ATTEMPT_WINDOW
    else:
        # Fallback for SimpleCache / in-memory test cache
        count = backend.inc(key)
        if count is None:
            backend.set(key, 1, timeout=TEAM_ATTEMPT_WINDOW)
            count = 1
        ttl = TEAM_ATTEMPT_WINDOW

    if count > limit:
        # Record burst evidence once per window (deduplicated)
        dedup_key = f"guard:team_burst_rec:{team_id}"
        if not backend.get(dedup_key):
            record("burst", team_id=team_id, detail=f"attempts={count} in {TEAM_ATTEMPT_WINDOW}s")
            backend.set(dedup_key, 1, timeout=TEAM_ATTEMPT_WINDOW)
        return False, max(1, ttl)

    return True, 0


def track_shared_address(team_id: int, client_ip: str) -> None:
    """Track team participation from client IP; record evidence if >= 3 teams share non-campus IP."""
    if not team_id or not client_ip or is_campus_ip(client_ip):
        return

    backend, client = _get_backend_client()
    key = f"guard:ip_teams:{client_ip}"
    dedup_key = f"guard:shared_rec:{client_ip}"

    if client is not None:
        prefix = getattr(backend, "key_prefix", "")
        full_key = prefix + key
        client.sadd(full_key, str(team_id))
        client.expire(full_key, SHARED_ADDRESS_WINDOW)
        team_count = client.scard(full_key)
    else:
        teams = backend.get(key) or set()
        teams.add(team_id)
        backend.set(key, teams, timeout=SHARED_ADDRESS_WINDOW)
        team_count = len(teams)

    if team_count >= SHARED_TEAMS_THRESHOLD:
        if not backend.get(dedup_key):
            record("shared_address", team_id=team_id, detail=f"distinct_teams={team_count} on non-campus IP")
            backend.set(dedup_key, 1, timeout=SHARED_ADDRESS_WINDOW)


def track_registration_burst(client_ip: str) -> None:
    """Track registration from client IP; record evidence if burst threshold exceeded."""
    if not client_ip or is_campus_ip(client_ip):
        return

    threshold = get_reg_burst_threshold()
    key = f"guard:reg_burst:{client_ip}"
    dedup_key = f"guard:reg_burst_rec:{client_ip}"
    backend, client = _get_backend_client()

    if client is not None:
        prefix = getattr(backend, "key_prefix", "")
        full_key = prefix + key
        pipe = client.pipeline()
        pipe.incr(full_key)
        pipe.ttl(full_key)
        results = pipe.execute()
        count = int(results[0])
        ttl = int(results[1])
        if ttl == -1 or ttl <= 0:
            client.expire(full_key, REG_BURST_WINDOW)
    else:
        count = backend.inc(key)
        if count is None:
            backend.set(key, 1, timeout=REG_BURST_WINDOW)
            count = 1

    if count >= threshold:
        if not backend.get(dedup_key):
            record("registration_burst", detail=f"registrations={count} in {REG_BURST_WINDOW}s")
            backend.set(dedup_key, 1, timeout=REG_BURST_WINDOW)
