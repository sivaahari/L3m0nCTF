"""Multi-host management and capacity tracking for L3m0nCTF challenge instancers.

Handles:
1. Loading and validating host configurations (config/instancer/hosts.json).
2. Per-host authentication tokens (INSTANCER_TOKEN_<NAME>).
3. 5-second TTL capacity cache with optimistic local counter for concurrent starts.
4. 30-second penalty for hosts failing capacity probes.
5. Host selection based on free capacity and host weights.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Optional
import urllib.parse

from CTFd.plugins.l3mon_instances.client import InstancerClient, InstancerClientError, get_instancer_token, get_instancer_url

_log = logging.getLogger("l3mon.instances.hosts")

_HOST_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_global_tracker: Optional[HostCapacityTracker] = None
_tracker_lock = threading.RLock()


def get_token_for_host(host_name: str, allow_fallback: bool = False) -> str:
    """Retrieve the authentication token for a specific instancer host.

    In multi-host mode (allow_fallback=False), each host MUST have its own token
    (INSTANCER_TOKEN_<NAME_UPPER>). Falling back to the global platform token is
    refused to prevent token sharing across hosts.
    """
    clean_name = re.sub(r"[^A-Za-z0-9_]", "_", host_name.upper())
    specific_secret = f"/run/secrets/INSTANCER_TOKEN_{clean_name}"
    if os.path.exists(specific_secret):
        try:
            with open(specific_secret, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            _log.warning("Could not read secret file %s: %s", specific_secret, e)

    specific_env = os.getenv(f"INSTANCER_TOKEN_{clean_name}", "").strip()
    if specific_env:
        return specific_env

    # The implicit single 'default' host (used when no hosts.json is configured)
    # uses the platform token. Strict per-host tokens apply only to listed hosts.
    if host_name == "default" or allow_fallback:
        return get_instancer_token()

    raise ValueError(
        f"Missing per-host token for host '{host_name}': INSTANCER_TOKEN_{clean_name} must be set"
    )


def validate_host_entry(entry: dict) -> dict:
    """Validate a single host configuration dictionary."""
    if not isinstance(entry, dict):
        raise ValueError(f"Host entry must be a dictionary, got {type(entry).__name__}")

    name = entry.get("name")
    if not name or not isinstance(name, str) or not _HOST_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid host 'name': {name!r}. Must match {_HOST_NAME_PATTERN.pattern}")
    if name.lower() == "default":
        raise ValueError(
            "Host name 'default' is reserved for implicit single-host fallback and cannot be used in hosts.json"
        )

    url = entry.get("url")
    if not url or not isinstance(url, str):
        raise ValueError(f"Host '{name}' missing required 'url'")
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError(f"Host '{name}' invalid 'url': {url!r}")

    router_url = entry.get("router_url")
    if not router_url or not isinstance(router_url, str):
        raise ValueError(f"Host '{name}' missing required 'router_url'")
    parsed_router_url = urllib.parse.urlparse(router_url)
    if parsed_router_url.scheme not in ("http", "https") or not parsed_router_url.netloc:
        raise ValueError(f"Host '{name}' invalid 'router_url': {router_url!r}")

    # Allow either 'public_tcp_host' or 'host'
    tcp_host = entry.get("public_tcp_host") or entry.get("host")
    if not tcp_host or not isinstance(tcp_host, str):
        raise ValueError(f"Host '{name}' missing required 'public_tcp_host' or 'host'")
    # tcp_host must not contain URL scheme or path
    if "://" in tcp_host or "/" in tcp_host:
        raise ValueError(f"Host '{name}' invalid 'public_tcp_host': {tcp_host!r}")

    weight = entry.get("weight", 1.0)
    if not isinstance(weight, (int, float)) or weight <= 0:
        raise ValueError(f"Host '{name}' invalid 'weight': {weight!r}. Must be positive number.")

    max_slots = entry.get("max_slots")
    if max_slots is not None:
        if not isinstance(max_slots, int) or max_slots <= 0:
            raise ValueError(f"Host '{name}' invalid 'max_slots': {max_slots!r}. Must be positive integer.")

    ca_cert = entry.get("ca_cert")
    if ca_cert is not None:
        if not isinstance(ca_cert, str) or not ca_cert:
            raise ValueError(f"Host '{name}' invalid 'ca_cert': {ca_cert!r}")
        if not os.path.isfile(ca_cert):
            raise ValueError(f"Host '{name}' ca_cert file not found: {ca_cert}")

    return {
        "name": name,
        "url": url.rstrip("/"),
        "router_url": router_url.rstrip("/"),
        "public_tcp_host": tcp_host,
        "host": tcp_host,
        "weight": float(weight),
        "max_slots": max_slots,
        "ca_cert": ca_cert,
    }


def load_hosts_config(path: Optional[str] = None) -> list[dict]:
    """Load and validate host configuration from JSON file or return single-host fallback."""
    config_path = path or os.getenv("L3MON_HOSTS_PATH")
    if not config_path:
        for candidate in ("/etc/l3mon/hosts.json", "config/instancer/hosts.json"):
            if os.path.isfile(candidate):
                config_path = candidate
                break

    if not config_path or not os.path.isfile(config_path):
        # Default single-host fallback configuration
        default_host = {
            "name": "default",
            "url": get_instancer_url(),
            "router_url": "http://router:8090",
            "public_tcp_host": os.getenv("INSTANCER_TCP_HOST", "localhost"),
            "host": os.getenv("INSTANCER_TCP_HOST", "localhost"),
            "weight": 1.0,
            "max_slots": None,
            "ca_cert": None,
        }
        return [default_host]

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Hosts configuration must be a JSON array, got {type(data).__name__}")

    if not data:
        raise ValueError("Hosts configuration JSON array must not be empty")

    validated = []
    seen_names = set()
    for entry in data:
        clean = validate_host_entry(entry)
        if clean["name"] in seen_names:
            raise ValueError(f"Duplicate host name in configuration: {clean['name']}")
        seen_names.add(clean["name"])
        validated.append(clean)

    return validated


class HostCapacityTracker:
    """Tracks capacity, caching, down status, and optimistic counters across challenge hosts."""

    def __init__(
        self,
        hosts: Optional[list[dict]] = None,
        config_path: Optional[str] = None,
        cache_ttl: float = 5.0,
        down_duration: float = 30.0,
        time_provider: Optional[Callable[[], float]] = None,
    ):
        self.config_path = config_path
        self._hosts = hosts if hosts is not None else load_hosts_config(config_path)
        self._hosts_by_name = {h["name"]: h for h in self._hosts}
        self.cache_ttl = cache_ttl
        self.down_duration = down_duration
        self._time_provider = time_provider or time.time

        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}
        self._down_until: dict[str, float] = {}
        self._client_factory: Optional[Callable[[dict, str], InstancerClient]] = None

    def now(self) -> float:
        return self._time_provider()

    def set_time_provider(self, fn: Callable[[], float]):
        self._time_provider = fn

    def set_client_factory(self, factory: Optional[Callable[[dict, str], InstancerClient]]):
        self._client_factory = factory

    def get_hosts(self) -> list[dict]:
        return list(self._hosts)

    def get_host(self, host_name: str) -> dict:
        if host_name in self._hosts_by_name:
            return self._hosts_by_name[host_name]
        # Return a fallback representation if unknown
        return {
            "name": host_name,
            "url": get_instancer_url(),
            "router_url": "http://router:8090",
            "public_tcp_host": os.getenv("INSTANCER_TCP_HOST", "localhost"),
            "host": os.getenv("INSTANCER_TCP_HOST", "localhost"),
            "weight": 1.0,
            "max_slots": None,
            "ca_cert": None,
        }

    def get_client_for_host(self, host_name: str) -> InstancerClient:
        host = self.get_host(host_name)
        token = get_token_for_host(host_name)
        if self._client_factory:
            return self._client_factory(host, token)
        return InstancerClient(base_url=host["url"], token=token, ca_cert=host.get("ca_cert"))

    def is_host_down(self, host_name: str, now_ts: Optional[float] = None) -> bool:
        ts = now_ts if now_ts is not None else self.now()
        with self._lock:
            down_until = self._down_until.get(host_name)
            if down_until is not None:
                if ts < down_until:
                    return True
                # Penalty has expired
                self._down_until.pop(host_name, None)
        return False

    def mark_host_down(self, host_name: str, now_ts: Optional[float] = None):
        ts = now_ts if now_ts is not None else self.now()
        with self._lock:
            self._down_until[host_name] = ts + self.down_duration
            _log.warning("Marking instancer host '%s' as DOWN until timestamp %.1f", host_name, ts + self.down_duration)

    def mark_host_up(self, host_name: str):
        with self._lock:
            self._down_until.pop(host_name, None)

    def reset_cache(self):
        with self._lock:
            self._cache.clear()
            self._down_until.clear()

    def get_capacity(self, host_name: str, now_ts: Optional[float] = None) -> dict:
        """Fetch capacity for a host, using 5s cache and tracking errors."""
        ts = now_ts if now_ts is not None else self.now()
        with self._lock:
            cached = self._cache.get(host_name)
            if cached and (ts - cached["timestamp"]) < self.cache_ttl:
                effective_used = cached["slots_used"] + cached["optimistic_slots"]
                effective_free = max(0, cached["slots_total"] - effective_used)
                return {
                    "host_name": host_name,
                    "slots_total": cached["slots_total"],
                    "slots_used": effective_used,
                    "slots_free": effective_free,
                    "mem_mb_total": cached.get("mem_mb_total", 0),
                    "mem_mb_used": cached.get("mem_mb_used", 0),
                    "mem_mb_free": max(0, cached.get("mem_mb_total", 0) - cached.get("mem_mb_used", 0)),
                }

        # Cache miss or expired: fetch from host client
        host = self.get_host(host_name)
        client = self.get_client_for_host(host_name)
        try:
            resp = client.get_capacity()
        except Exception as e:
            self.mark_host_down(host_name, ts)
            raise e

        # Update cache under lock
        with self._lock:
            self.mark_host_up(host_name)
            max_slots = host.get("max_slots") or resp.get("slots_total", 100)
            slots_used = resp.get("slots_used", 0)
            mem_mb_total = resp.get("mem_mb_total", 0)
            mem_mb_used = resp.get("mem_mb_used", 0)

            entry = {
                "slots_total": max_slots,
                "slots_used": slots_used,
                "optimistic_slots": 0,
                "mem_mb_total": mem_mb_total,
                "mem_mb_used": mem_mb_used,
                "timestamp": ts,
            }
            self._cache[host_name] = entry

            return {
                "host_name": host_name,
                "slots_total": max_slots,
                "slots_used": slots_used,
                "slots_free": max(0, max_slots - slots_used),
                "mem_mb_total": mem_mb_total,
                "mem_mb_used": mem_mb_used,
                "mem_mb_free": max(0, mem_mb_total - mem_mb_used),
            }

    def select_host_for_start(self, slug: Optional[str] = None, now_ts: Optional[float] = None) -> dict:
        """Select the least-loaded host for starting a new instance.

        Applies:
        - Down penalty exclusion (30s)
        - 5s TTL capacity cache
        - Weight multiplier
        - Optimistic slot increment for concurrent start protection
        """
        ts = now_ts if now_ts is not None else self.now()

        candidates = []
        for host in self._hosts:
            name = host["name"]
            if self.is_host_down(name, ts):
                continue

            try:
                cap = self.get_capacity(name, ts)
            except Exception as e:
                _log.warning("Capacity probe failed for host '%s': %s", name, e)
                continue

            slots_free = cap.get("slots_free", 0)
            if slots_free <= 0:
                continue

            # Free slots multiplied by weight
            weight = host.get("weight", 1.0)
            score = slots_free * weight
            candidates.append((score, slots_free, host))

        if not candidates:
            raise InstancerClientError(
                503,
                "no_capacity",
                "No instancer host has capacity available",
                retry_after=30,
            )

        # Select host with highest weighted score; tie-break by name for stability
        candidates.sort(key=lambda item: (item[0], item[1], item[2]["name"]), reverse=True)
        chosen_host = candidates[0][2]

        # Optimistically reserve 1 slot locally in memory
        with self._lock:
            cached = self._cache.get(chosen_host["name"])
            if cached:
                cached["optimistic_slots"] += 1

        return chosen_host


def get_host_tracker() -> HostCapacityTracker:
    """Return the global HostCapacityTracker singleton."""
    global _global_tracker
    if _global_tracker is None:
        with _tracker_lock:
            if _global_tracker is None:
                _global_tracker = HostCapacityTracker()
    return _global_tracker


def set_host_tracker(tracker: Optional[HostCapacityTracker]):
    """Override or reset the global HostCapacityTracker singleton."""
    global _global_tracker
    with _tracker_lock:
        _global_tracker = tracker
