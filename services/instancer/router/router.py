import asyncio
import collections
import ipaddress
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Optional

_log = logging.getLogger("l3mon.instancer.router")

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}\Z")
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB client request cap
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MB backend response streaming cap
IDLE_TIMEOUT_SECONDS = 30
MAX_CONNECTIONS_PER_INSTANCE = 50
MAX_CONNECTIONS_PER_IP = 50
MAX_TOTAL_CONNECTIONS = 1000
GLOBAL_READING_BUDGET = 64 * 1024 * 1024  # 64 MB concurrent reading budget
CHUNK_SIZE = 64 * 1024  # 64 KB

CLOUDFLARE_NETWORKS = (
    ipaddress.ip_network("173.245.48.0/20"),
    ipaddress.ip_network("103.21.244.0/22"),
    ipaddress.ip_network("103.22.200.0/22"),
    ipaddress.ip_network("103.31.4.0/22"),
    ipaddress.ip_network("141.101.64.0/18"),
    ipaddress.ip_network("108.162.192.0/18"),
    ipaddress.ip_network("190.93.240.0/20"),
    ipaddress.ip_network("188.114.96.0/20"),
    ipaddress.ip_network("197.234.240.0/22"),
    ipaddress.ip_network("198.41.128.0/17"),
    ipaddress.ip_network("162.158.0.0/15"),
    ipaddress.ip_network("104.16.0.0/13"),
    ipaddress.ip_network("104.24.0.0/14"),
    ipaddress.ip_network("172.64.0.0/13"),
    ipaddress.ip_network("131.0.72.0/22"),
    ipaddress.ip_network("2400:cb00::/32"),
    ipaddress.ip_network("2606:4700::/32"),
    ipaddress.ip_network("2803:f800::/32"),
    ipaddress.ip_network("2405:b500::/32"),
    ipaddress.ip_network("2405:8100::/32"),
    ipaddress.ip_network("2a06:98c0::/29"),
    ipaddress.ip_network("2c0f:f248::/32"),
)


def is_cloudflare_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in CLOUDFLARE_NETWORKS)
    except ValueError:
        return False


def extract_instance_id(host_header: str, instance_domain: str) -> Optional[str]:
    """Extract and validate the 32-hex instance ID from the Host header (<id>.<instance_domain>).

    Returns None if Host does not match the exact pattern or targets an outside host/IP.
    """
    if not host_header:
        return None

    # Strip port if present
    host = host_header.split(":")[0].strip().lower()
    suffix = "." + instance_domain.split(":")[0].strip().lower()

    if not host.endswith(suffix):
        return None

    prefix = host[: -len(suffix)]
    if not _ID_PATTERN.fullmatch(prefix):
        return None

    return prefix


def sanitize_headers(headers: dict | list[tuple[str, str]], is_websocket: bool = False) -> dict | list[tuple[str, str]]:
    """Strip hop-by-hop headers to prevent proxy bypass, preserving multi-valued headers."""
    if isinstance(headers, dict):
        sanitized_dict = {}
        for k, v in headers.items():
            if k.lower().strip() not in _HOP_BY_HOP:
                sanitized_dict[k] = v
        return sanitized_dict

    sanitized_list = []
    for k, v in headers:
        k_lower = k.lower().strip()
        if k_lower not in _HOP_BY_HOP:
            sanitized_list.append((k.strip(), v.strip()))
    return sanitized_list


DEFAULT_TCP_PORT_START = 30000
DEFAULT_TCP_PORT_COUNT = 200
DEFAULT_TCP_IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_TCP_LIFETIME_CAP_SECONDS = 3600.0
DEFAULT_TCP_MAX_CONNS_PER_INSTANCE = 10
DEFAULT_TCP_MAX_CONNS_PER_IP = 60  # Campus NAT friendly (Lead Item 5)
DEFAULT_TCP_MAX_TOTAL_CONNS = 2000
DEFAULT_TCP_RATE_LIMIT_BPS = 2 * 1024 * 1024  # 2 MB/s
DEFAULT_TCP_RATE_LIMIT_BURST = 4 * 1024 * 1024  # 4 MB
DEFAULT_INSTANCE_POOL = "10.200.0.0/16"
TCP_CONNECT_TIMEOUT_SECONDS = 5.0


def validate_route_target(
    target_ip: str,
    target_port: int,
    pool: ipaddress.IPv4Network | ipaddress.IPv6Network | list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    allow_loopback: bool = False,
    pool_subnet_size: int = 24,
) -> bool:
    """Validate that target_ip:target_port is safe to connect to.

    Target MUST lie strictly inside pool and NOT be the network address, gateway (.1 / +1),
    or broadcast address for its subnet. Refuses public IPs, metadata 169.254.169.254, loopback,
    and private IPs outside pool (e.g. 10.0.0.5).
    """
    if not isinstance(target_port, int) or not (1 <= target_port <= 65535):
        return False
    if not target_ip or not isinstance(target_ip, str):
        return False
    try:
        addr = ipaddress.ip_address(target_ip.strip())
    except ValueError:
        return False

    pools = list(pool) if isinstance(pool, (list, tuple)) else [pool]
    matching_pool = None
    for p in pools:
        if addr in p:
            matching_pool = p
            break
    if matching_pool is None:
        return False

    # Reject overall pool network address and broadcast address
    if addr == matching_pool.network_address:
        return False
    if hasattr(matching_pool, "broadcast_address") and addr == matching_pool.broadcast_address:
        return False

    # Reject host gateway, subnet network address, and broadcast address in instance subnets
    if isinstance(addr, ipaddress.IPv4Address) and not (allow_loopback and addr.is_loopback):
        # Compute specific subnet based on pool_subnet_size (e.g. /24, /28)
        prefix = pool_subnet_size if (isinstance(pool_subnet_size, int) and 16 <= pool_subnet_size <= 30) else 24
        try:
            sub = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
            gw = sub.network_address + 1
            if addr == sub.network_address:
                return False
            if addr == gw:
                return False
            if addr == sub.broadcast_address:
                return False
        except Exception:
            pass

        # Fallback safeguard for /24 standard octets (.0, .1, .255)
        if addr.packed[-1] in (0, 1, 255):
            return False

    # Reject loopback, multicast, link-local, unspecified
    if addr.is_loopback and not allow_loopback:
        return False
    if addr.is_multicast or addr.is_link_local or addr.is_unspecified:
        return False

    return True


class TokenBucketRateLimiter:
    """Token bucket rate limiter enforcing bytes-per-second limit with burst allowance."""

    def __init__(self, rate_bps: int, burst_bytes: int):
        self.rate = rate_bps
        self.burst = burst_bytes
        self.tokens = float(burst_bytes)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, nbytes: int) -> None:
        if nbytes <= 0 or self.rate <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate)
            if self.tokens < nbytes:
                deficit = nbytes - self.tokens
                wait_time = deficit / self.rate
                await asyncio.sleep(wait_time)
                now2 = time.monotonic()
                elapsed2 = now2 - self.last_update
                self.last_update = now2
                self.tokens = min(float(self.burst), self.tokens + elapsed2 * self.rate - nbytes)
            else:
                self.tokens -= nbytes


def get_router_token_for_host(
    host_name: str,
    fallback_token: Optional[str] = None,
    allow_fallback: bool = False,
) -> str:
    """Read host-specific route read token (ROUTER_TOKEN_<NAME_UPPER>).

    In multi-host mode (allow_fallback=False), each listed host MUST have its own token.
    Falling back to the global platform token is refused to prevent token sharing across hosts.
    """
    clean_name = re.sub(r"[^A-Za-z0-9_]", "_", host_name.upper())
    # /run/secrets first, then the host secrets folder (one file per listed host, mounted by compose.instancer-multihost.yml:
    # any number of hosts needs no edit of a compose file), then the environment
    host_dir = os.getenv("L3MON_HOST_SECRETS_DIR", "/run/host-secrets")
    for path in (f"/run/secrets/ROUTER_TOKEN_{clean_name}", os.path.join(host_dir, f"ROUTER_TOKEN_{clean_name}")):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                    if val:
                        return val
            except Exception:
                pass
    env_val = os.getenv(f"ROUTER_TOKEN_{clean_name}", "").strip()
    if env_val:
        return env_val

    if host_name == "default" or allow_fallback:
        if fallback_token:
            return fallback_token
        global_path = "/run/secrets/ROUTER_TOKEN"
        if os.path.exists(global_path):
            try:
                with open(global_path, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                    if val:
                        return val
            except Exception:
                pass
        return os.getenv("ROUTER_TOKEN", "").strip()

    raise ValueError(
        f"Missing per-host router token for host '{host_name}': ROUTER_TOKEN_{clean_name} must be set"
    )


class InstanceRouter:
    """Reverse proxy carrying HTTP traffic to per-team challenge instances without Docker daemon access."""

    def __init__(
        self,
        instance_domain: str = "localhost:8090",
        instancer_url: str = "http://instancer:8300",
        router_token: str = "",
        poll_interval: float = 2.0,
        max_connections_per_instance: int = MAX_CONNECTIONS_PER_INSTANCE,
        max_connections_per_ip: int = MAX_CONNECTIONS_PER_IP,
        max_total_connections: int = MAX_TOTAL_CONNECTIONS,
        global_reading_budget: int = GLOBAL_READING_BUDGET,
        tcp_port_start: int = DEFAULT_TCP_PORT_START,
        tcp_port_count: int = DEFAULT_TCP_PORT_COUNT,
        tcp_idle_timeout: float = DEFAULT_TCP_IDLE_TIMEOUT_SECONDS,
        tcp_lifetime_cap: float = DEFAULT_TCP_LIFETIME_CAP_SECONDS,
        tcp_max_connections_per_instance: int = DEFAULT_TCP_MAX_CONNS_PER_INSTANCE,
        tcp_max_connections_per_ip: int = DEFAULT_TCP_MAX_CONNS_PER_IP,
        tcp_max_total_connections: int = DEFAULT_TCP_MAX_TOTAL_CONNS,
        tcp_rate_limit_bps: int = DEFAULT_TCP_RATE_LIMIT_BPS,
        tcp_rate_limit_burst: int = DEFAULT_TCP_RATE_LIMIT_BURST,
        instance_pool: str = DEFAULT_INSTANCE_POOL,
        tcp_connect_timeout: float = TCP_CONNECT_TIMEOUT_SECONDS,
        allow_loopback_for_tests: bool = False,
        hosts: Optional[list[dict]] = None,
        pool_subnet_size: int = 24,
        trusted_forwarders: Optional[list[str] | str] = None,
    ):
        if not (1024 <= tcp_port_start <= 65535):
            raise ValueError(f"INSTANCER_TCP_PORT_START must be between 1024 and 65535, got {tcp_port_start}")
        if not (1 <= tcp_port_count <= 2000):
            raise ValueError(f"INSTANCER_TCP_PORT_COUNT must be between 1 and 2000, got {tcp_port_count}")
        if tcp_port_start + tcp_port_count - 1 > 65535:
            raise ValueError(
                f"TCP port range [{tcp_port_start}, {tcp_port_start + tcp_port_count - 1}] exceeds 65535"
            )

        if not (isinstance(tcp_max_connections_per_instance, int) and 1 <= tcp_max_connections_per_instance <= 100):
            raise ValueError(
                f"INSTANCER_TCP_MAX_CONNS_PER_INSTANCE must be between 1 and 100, got {tcp_max_connections_per_instance}"
            )
        if not (isinstance(tcp_max_connections_per_ip, int) and 1 <= tcp_max_connections_per_ip <= 1000):
            raise ValueError(
                f"INSTANCER_TCP_MAX_CONNS_PER_IP must be between 1 and 1000, got {tcp_max_connections_per_ip}"
            )
        if not (isinstance(tcp_max_total_connections, int) and 1 <= tcp_max_total_connections <= 10000):
            raise ValueError(
                f"INSTANCER_TCP_MAX_TOTAL_CONNS must be between 1 and 10000, got {tcp_max_total_connections}"
            )
        if not (isinstance(tcp_rate_limit_bps, int) and 1024 <= tcp_rate_limit_bps <= 1024 * 1024 * 1024):
            raise ValueError(
                f"INSTANCER_TCP_RATE_LIMIT_BPS must be between 1024 and 1073741824, got {tcp_rate_limit_bps}"
            )
        if not (isinstance(tcp_rate_limit_burst, int) and 1024 <= tcp_rate_limit_burst <= 1024 * 1024 * 1024):
            raise ValueError(
                f"INSTANCER_TCP_RATE_LIMIT_BURST must be between 1024 and 1073741824, got {tcp_rate_limit_burst}"
            )
        if not (isinstance(tcp_idle_timeout, (int, float)) and 0.1 <= tcp_idle_timeout <= 86400):
            raise ValueError(
                f"INSTANCER_TCP_IDLE_TIMEOUT must be between 0.1 and 86400, got {tcp_idle_timeout}"
            )
        if not (isinstance(tcp_lifetime_cap, (int, float)) and 0.1 <= tcp_lifetime_cap <= 86400):
            raise ValueError(
                f"INSTANCER_TCP_LIFETIME_CAP must be between 0.1 and 86400, got {tcp_lifetime_cap}"
            )

        pools = []
        for p in instance_pool.split(","):
            p_clean = p.strip()
            if p_clean:
                try:
                    pools.append(ipaddress.ip_network(p_clean))
                except ValueError as e:
                    raise ValueError(f"Invalid INSTANCER_INSTANCE_POOL entry '{p_clean}': {e}")
        if not pools:
            raise ValueError("INSTANCER_INSTANCE_POOL cannot be empty")
        self.pool_networks = pools
        self.pool_network = pools[0]

        self.instance_domain = instance_domain
        self.instancer_url = instancer_url
        self.router_token = router_token
        self.poll_interval = poll_interval
        self.max_connections_per_instance = max_connections_per_instance
        self.max_connections_per_ip = max_connections_per_ip
        self.max_total_connections = max_total_connections
        self.global_reading_budget = global_reading_budget

        self.tcp_port_start = tcp_port_start
        self.tcp_port_count = tcp_port_count
        self.tcp_idle_timeout = tcp_idle_timeout
        self.tcp_lifetime_cap = tcp_lifetime_cap
        self.tcp_max_connections_per_instance = tcp_max_connections_per_instance
        self.tcp_max_connections_per_ip = tcp_max_connections_per_ip
        self.tcp_max_total_connections = tcp_max_total_connections
        self.tcp_rate_limit_bps = tcp_rate_limit_bps
        self.tcp_rate_limit_burst = tcp_rate_limit_burst
        self.instance_pool = instance_pool
        self.pool_subnet_size = pool_subnet_size
        self.tcp_connect_timeout = tcp_connect_timeout
        self.allow_loopback_for_tests = allow_loopback_for_tests

        # Hop 2 trusted central router forwarders for X-L3mon-Client-IP extraction
        self.trusted_forwarders: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        fwd_raw = trusted_forwarders if trusted_forwarders is not None else os.getenv("INSTANCER_TRUSTED_FORWARDERS", "")
        if isinstance(fwd_raw, str):
            fwd_items = [x.strip() for x in fwd_raw.split(",") if x.strip()]
        else:
            fwd_items = list(fwd_raw)
        for item in fwd_items:
            try:
                if "/" in item:
                    self.trusted_forwarders.append(ipaddress.ip_network(item, strict=False))
                else:
                    self.trusted_forwarders.append(ipaddress.ip_network(f"{item}/32" if ":" not in item else f"{item}/128", strict=False))
            except ValueError as e:
                raise ValueError(f"Invalid INSTANCER_TRUSTED_FORWARDERS entry: {item!r}") from e

        self.hosts = hosts or []
        self.is_central = bool(hosts)
        self._host_last_poll: dict[str, float] = {}
        self._poll_failing: set[str] = set()  # hosts whose last poll failed (logged once per run of failures)

        self._routes: dict[str, dict] = {}  # instance_id -> route dict
        self._lock = asyncio.Lock()
        self._active_connections_per_inst: dict[str, int] = collections.defaultdict(int)
        self._active_connections_per_ip: dict[str, int] = collections.defaultdict(int)
        self._total_active_connections: int = 0
        self._conn_lock = asyncio.Lock()

        # Global reading budget tracking (N1)
        self._current_reading_bytes: int = 0
        self._budget_lock = asyncio.Lock()

        # TCP Relay state tracking (F1)
        self._tcp_listeners: dict[int, asyncio.Server] = {}
        self._tcp_routes: dict[int, dict] = {}
        self._tcp_connections: dict[int, set[asyncio.Task]] = collections.defaultdict(set)
        self._tcp_conns_per_ip: dict[str, int] = collections.defaultdict(int)
        self._tcp_conns_per_inst: dict[str, int] = collections.defaultdict(int)
        self._total_tcp_connections: int = 0
        self._tcp_lock = asyncio.Lock()
        self._tcp_sync_lock = asyncio.Lock()

        # TCP Metrics (F1.5, no instance IDs as labels)
        self._tcp_metrics_open_relays: int = 0
        self._tcp_metrics_bytes_relayed: int = 0
        self._tcp_metrics_refusals: dict[str, int] = collections.defaultdict(int)
        self._tcp_metrics_connect_failures: int = 0

    def get_route(self, inst_id: str) -> dict | None:
        """Lookup route dictionary for a given instance ID."""
        return self._routes.get(inst_id)

    def is_trusted_forwarder(self, peer_ip: str) -> bool:
        """Check if peer_ip matches any configured trusted forwarder network."""
        try:
            addr = ipaddress.ip_address(peer_ip)
            for net in self.trusted_forwarders:
                if addr in net:
                    return True
        except ValueError:
            pass
        return False

    def resolve_client_ip(self, peer_ip: str, headers: list[tuple[str, str]]) -> str:
        """Resolve real client IP.

        1. If peer_ip is a configured trusted forwarder (central router), trust X-L3mon-Client-IP.
        2. If peer_ip is in Cloudflare's published IP ranges, trust CF-Connecting-IP.
        3. Otherwise, return peer_ip directly.
        """
        # Central router forwarding to Hop 2
        if self.is_trusted_forwarder(peer_ip):
            for k, v in headers:
                if k.lower().strip() == "x-l3mon-client-ip":
                    candidate = v.strip()
                    try:
                        ipaddress.ip_address(candidate)
                        return candidate
                    except ValueError:
                        pass

        # Cloudflare edge
        if is_cloudflare_ip(peer_ip):
            for k, v in headers:
                if k.lower().strip() == "cf-connecting-ip":
                    candidate = v.strip()
                    try:
                        ipaddress.ip_address(candidate)
                        return candidate
                    except ValueError:
                        pass

        return peer_ip

    async def acquire_client_connection(self, peer_ip: str) -> tuple[bool, int, str]:
        """Pre-routing connection rate check and registration."""
        async with self._conn_lock:
            if self._total_active_connections >= self.max_total_connections:
                return False, 503, "Service Busy (Global Limit)"
            if not self.is_trusted_forwarder(peer_ip):
                curr = self._active_connections_per_ip.get(peer_ip, 0)
                if curr >= self.max_connections_per_ip:
                    return False, 503, "Service Busy (IP Limit)"
            curr = self._active_connections_per_ip.get(peer_ip, 0)
            self._total_active_connections += 1
            self._active_connections_per_ip[peer_ip] = curr + 1
            return True, 200, ""

    async def rebind_client_connection(self, old_ip: str, new_ip: str) -> tuple[bool, int, str]:
        """Rebind IP connection slot if Cloudflare CF-Connecting-IP reveals real client IP."""
        if old_ip == new_ip:
            if self.is_trusted_forwarder(old_ip):
                async with self._conn_lock:
                    curr = self._active_connections_per_ip.get(old_ip, 0)
                    if curr > self.max_connections_per_ip:
                        return False, 503, "Service Busy (IP Limit)"
            return True, 200, ""
        async with self._conn_lock:
            curr_new = self._active_connections_per_ip.get(new_ip, 0)
            if curr_new >= self.max_connections_per_ip:
                return False, 503, "Service Busy (IP Limit)"
            # Decrement old
            curr_old = self._active_connections_per_ip.get(old_ip, 1)
            if curr_old <= 1:
                self._active_connections_per_ip.pop(old_ip, None)
            else:
                self._active_connections_per_ip[old_ip] = curr_old - 1
            # Increment new
            self._active_connections_per_ip[new_ip] = curr_new + 1
            return True, 200, ""

    async def release_client_connection(self, ip: str) -> None:
        """Release client connection slot."""
        async with self._conn_lock:
            self._total_active_connections = max(0, self._total_active_connections - 1)
            curr = self._active_connections_per_ip.get(ip, 0)
            if curr <= 1:
                self._active_connections_per_ip.pop(ip, None)
            else:
                self._active_connections_per_ip[ip] = curr - 1

    async def acquire_reading_budget(self, nbytes: int) -> bool:
        """Atomically reserve nbytes from the global reading budget."""
        if nbytes <= 0:
            return True
        async with self._budget_lock:
            if self._current_reading_bytes + nbytes > self.global_reading_budget:
                return False
            self._current_reading_bytes += nbytes
            return True

    async def release_reading_budget(self, nbytes: int) -> None:
        """Atomically release reserved reading budget bytes."""
        if nbytes <= 0:
            return
        async with self._budget_lock:
            self._current_reading_bytes = max(0, self._current_reading_bytes - nbytes)

    def update_routes(self, routes: list[dict]):
        new_routes = {}
        for r in routes:
            inst_id = r.get("id")
            if inst_id:
                new_routes[inst_id] = r
        self._routes = new_routes

    def update_host_routes(
        self, host_name: str, router_url: str, routes: list[dict], now_ts: Optional[float] = None
    ):
        """Update routes received from a challenge host agent (Hop 1 routing).

        Enforces:
        1. ID must be strictly 32 lowercase hex characters (_ID_PATTERN).
        2. Hop 1 destination integrity: target_ip and target_port are taken EXCLUSIVELY
           from the host's configured router_url, NEVER from the agent's route payload.
        3. First-registered ID wins across hosts (preventing route hijacking).
        4. Tracks last successful poll timestamp per host for 30s expiration.
        """
        ts = now_ts if now_ts is not None else time.time()
        parsed_router = urllib.parse.urlparse(router_url)
        hop1_host = parsed_router.hostname or "127.0.0.1"
        hop1_port = parsed_router.port or (443 if parsed_router.scheme == "https" else 80)

        self._host_last_poll[host_name] = ts

        valid_incoming = {}
        for r in routes:
            inst_id = r.get("id")
            if not inst_id or not isinstance(inst_id, str) or not _ID_PATTERN.fullmatch(inst_id):
                _log.warning("Central router rejecting malformed route ID from host %s: %r", host_name, inst_id)
                continue

            existing = self._routes.get(inst_id)
            if existing and existing.get("host_name") != host_name:
                _log.warning(
                    "Central router rejecting duplicate ID %s from host %s; already registered on host %s",
                    inst_id,
                    host_name,
                    existing.get("host_name"),
                )
                continue

            # Hop 1 destination integrity: forward ONLY to configured router_url, ignore untrusted route target
            valid_incoming[inst_id] = {
                "id": inst_id,
                "protocol": r.get("protocol", "http"),
                "host_name": host_name,
                "target_ip": hop1_host,
                "target_port": hop1_port,
                "max_body_bytes": r.get("max_body_bytes", MAX_BODY_BYTES),
                "response_timeout_seconds": r.get("response_timeout_seconds", 120.0),
                "updated_at": ts,
            }

        # Keep routes from other hosts, replace routes from this host
        new_routes = {k: v for k, v in self._routes.items() if v.get("host_name") != host_name}
        new_routes.update(valid_incoming)

        # Expire routes for any host that has not responded in >= 30.0s
        expired_hosts = {h for h, last_ts in self._host_last_poll.items() if ts - last_ts >= 30.0}
        if expired_hosts:
            new_routes = {k: v for k, v in new_routes.items() if v.get("host_name") not in expired_hosts}

        self._routes = new_routes

    def purge_expired_host_routes(self, now_ts: Optional[float] = None):
        """Purge routes belonging to dead hosts that have not responded in >= 30 seconds."""
        ts = now_ts if now_ts is not None else time.time()
        expired_hosts = {h for h, last_ts in self._host_last_poll.items() if ts - last_ts >= 30.0}
        if expired_hosts:
            self._routes = {k: v for k, v in self._routes.items() if v.get("host_name") not in expired_hosts}

    async def acquire_connection(self, inst_id: str) -> tuple[bool, int, str]:
        """Atomically check and acquire an instance connection slot."""
        async with self._conn_lock:
            current = self._active_connections_per_inst.get(inst_id, 0)
            if current >= self.max_connections_per_instance:
                return False, 503, "Service Busy (Instance Limit)"
            self._active_connections_per_inst[inst_id] = current + 1
            return True, 200, ""

    async def release_connection(self, inst_id: str):
        """Atomically release an acquired instance connection slot."""
        async with self._conn_lock:
            current = self._active_connections_per_inst.get(inst_id, 0)
            if current <= 1:
                self._active_connections_per_inst.pop(inst_id, None)
            else:
                self._active_connections_per_inst[inst_id] = current - 1


    async def poll_routes_loop(self):
        """Poll the instancer service /v1/routes endpoint every 2 seconds."""
        parsed = urllib.parse.urlparse(self.instancer_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8300

        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
                req = (
                    f"GET /v1/routes HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    f"Authorization: Bearer {self.router_token}\r\n"
                    f"Accept: application/json\r\n"
                    f"Connection: close\r\n\r\n"
                )
                writer.write(req.encode("utf-8"))
                await writer.drain()

                response_data = await reader.read()
                writer.close()
                await writer.wait_closed()

                parts = response_data.split(b"\r\n\r\n", 1)
                if len(parts) == 2:
                    body = json.loads(parts[1].decode("utf-8"))
                    routes = body.get("routes", [])
                    async with self._lock:
                        self.update_routes(routes)
                    await self.sync_tcp_routes(routes)
            except Exception as e:
                _log.debug("Error polling routes from %s: %s", self.instancer_url, e)

            await asyncio.sleep(self.poll_interval)

    async def poll_central_routes_loop(self):
        """Poll /v1/routes across all configured challenge hosts in central multi-host mode (SP4 / F3)."""
        while True:
            for host in self.hosts:
                hname = host["name"]
                hurl = host["url"]
                rurl = host["router_url"]
                try:
                    token = get_router_token_for_host(hname, allow_fallback=False)
                except ValueError as e:
                    # a listed host without its token: say so (once), skip THIS host, keep polling the others
                    if hname not in self._poll_failing:
                        _log.error("Central router cannot poll host %s: %s", hname, e)
                        self._poll_failing.add(hname)
                    continue
                try:
                    parsed = urllib.parse.urlparse(hurl)
                    h_host = parsed.hostname or "127.0.0.1"
                    h_port = parsed.port or 8300
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(h_host, h_port),
                        timeout=self.poll_interval,
                    )
                    req = (
                        f"GET /v1/routes HTTP/1.1\r\n"
                        f"Host: {h_host}:{h_port}\r\n"
                        f"Authorization: Bearer {token}\r\n"
                        f"Accept: application/json\r\n"
                        f"Connection: close\r\n\r\n"
                    )
                    writer.write(req.encode("utf-8"))
                    await writer.drain()

                    resp_data = await asyncio.wait_for(
                        reader.read(),
                        timeout=self.poll_interval,
                    )
                    writer.close()
                    await writer.wait_closed()

                    parts = resp_data.split(b"\r\n\r\n", 1)
                    if len(parts) == 2:
                        body = json.loads(parts[1].decode("utf-8"))
                        routes = body.get("routes", [])
                        async with self._lock:
                            self.update_host_routes(hname, rurl, routes, now_ts=time.time())
                        if hname in self._poll_failing:
                            _log.info("Central router polls host %s again", hname)
                            self._poll_failing.discard(hname)
                except Exception as e:
                    if hname not in self._poll_failing:  # the first failure of a run of failures is a warning, not a debug line
                        _log.warning("Central router poll failed for host %s: %s", hname, e)
                        self._poll_failing.add(hname)
                    async with self._lock:
                        self.purge_expired_host_routes(time.time())

            await asyncio.sleep(self.poll_interval)

    async def sync_tcp_routes(self, routes: list[dict]) -> None:
        """Synchronize active TCP port listeners with current route table (F1)."""
        async with self._tcp_sync_lock:
            new_tcp_routes: dict[int, dict] = {}
            for r in routes:
                if r.get("protocol") == "tcp":
                    port = r.get("port")
                    if port and isinstance(port, int) and (self.tcp_port_start <= port < self.tcp_port_start + self.tcp_port_count):
                        target_ip = r.get("target_ip")
                        target_port = r.get("target_port")
                        if validate_route_target(
                            target_ip,
                            target_port,
                            self.pool_networks,
                            allow_loopback=self.allow_loopback_for_tests,
                            pool_subnet_size=self.pool_subnet_size,
                        ):
                            new_tcp_routes[port] = r
                        else:
                            _log.warning(
                                "Rejecting invalid/poisoned TCP route target: %s:%s on port %s",
                                target_ip,
                                target_port,
                                port,
                            )
                            async with self._tcp_lock:
                                self._tcp_metrics_refusals["invalid_target"] += 1

            async with self._tcp_lock:
                current_ports = set(self._tcp_listeners.keys())
                desired_ports = set(new_tcp_routes.keys())

                # Ports to close: missing from new routes or target modified
                ports_to_close = set()
                for p in current_ports:
                    if p not in desired_ports:
                        ports_to_close.add(p)
                    else:
                        curr_r = self._tcp_routes.get(p, {})
                        new_r = new_tcp_routes[p]
                        if (
                            curr_r.get("target_ip") != new_r.get("target_ip")
                            or curr_r.get("target_port") != new_r.get("target_port")
                            or curr_r.get("id") != new_r.get("id")
                        ):
                            ports_to_close.add(p)

                ports_to_open = desired_ports - (current_ports - ports_to_close)

            # Close removed/changed listeners outside lock to avoid deadlocks
            for p in ports_to_close:
                server = self._tcp_listeners.pop(p, None)
                if server:
                    server.close()

                # Abort all active relay tasks on this port first so sockets close
                tasks = list(self._tcp_connections.pop(p, set()))
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                if server:
                    try:
                        await asyncio.wait_for(server.wait_closed(), timeout=0.5)
                    except Exception:
                        pass

                self._tcp_routes.pop(p, None)
                _log.info("Closed TCP listener and terminated relays on port %d", p)

            if ports_to_close:
                await asyncio.sleep(0.05)

            # Open new listeners
            for p in ports_to_open:
                route = new_tcp_routes[p]
                try:
                    server = await asyncio.start_server(
                        lambda r, w, port=p: self.handle_tcp_client(r, w, port),
                        "0.0.0.0",
                        p,
                    )
                    self._tcp_listeners[p] = server
                    self._tcp_routes[p] = route
                    _log.info(
                        "Started TCP relay listener on 0.0.0.0:%d -> %s:%s (instance: %s)",
                        p,
                        route.get("target_ip"),
                        route.get("target_port"),
                        route.get("id"),
                    )
                except Exception as e:
                    _log.error("Failed to bind TCP listener on port %d: %s", p, e)

    async def handle_tcp_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, port: int
    ) -> None:
        """Handle incoming TCP connection on an allocated challenge port (F1)."""
        peername = None
        if hasattr(client_writer, "get_extra_info"):
            peername = client_writer.get_extra_info("peername")
        peer_ip = peername[0] if (peername and isinstance(peername, tuple)) else "127.0.0.1"

        inst_id = ""
        route = None
        async with self._tcp_lock:
            route = self._tcp_routes.get(port)
            if not route:
                self._tcp_metrics_refusals["route_closed"] += 1
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass
                return

            inst_id = route.get("id", "")
            if self._total_tcp_connections >= self.tcp_max_total_connections:
                self._tcp_metrics_refusals["global_cap"] += 1
                _log.warning("TCP refusal: global connection cap reached (%d)", self.tcp_max_total_connections)
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass
                return

            curr_ip_conns = self._tcp_conns_per_ip.get(peer_ip, 0)
            if curr_ip_conns >= self.tcp_max_connections_per_ip:
                self._tcp_metrics_refusals["ip_cap"] += 1
                _log.warning("TCP refusal: per-IP cap reached for %s (%d)", peer_ip, self.tcp_max_connections_per_ip)
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass
                return

            curr_inst_conns = self._tcp_conns_per_inst.get(inst_id, 0)
            if curr_inst_conns >= self.tcp_max_connections_per_instance:
                self._tcp_metrics_refusals["instance_cap"] += 1
                _log.warning(
                    "TCP refusal: per-instance cap reached for %s (%d)", inst_id, self.tcp_max_connections_per_instance
                )
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass
                return

            self._total_tcp_connections += 1
            self._tcp_conns_per_ip[peer_ip] = curr_ip_conns + 1
            self._tcp_conns_per_inst[inst_id] = curr_inst_conns + 1
            self._tcp_metrics_open_relays += 1

        curr_task = asyncio.current_task()
        if curr_task is not None:
            self._tcp_connections[port].add(curr_task)

        target_ip = route.get("target_ip")
        target_port = route.get("target_port")
        backend_reader = None
        backend_writer = None

        try:
            try:
                backend_reader, backend_writer = await asyncio.wait_for(
                    asyncio.open_connection(target_ip, target_port),
                    timeout=self.tcp_connect_timeout,
                )
            except Exception as e:
                self._tcp_metrics_connect_failures += 1
                _log.warning("TCP connect failure to target %s:%s: %s", target_ip, target_port, e)
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass
                return

            # Bidirectional relay with token bucket rate limit and backpressure
            rate_limiter = TokenBucketRateLimiter(self.tcp_rate_limit_bps, self.tcp_rate_limit_burst)
            last_activity = time.monotonic()
            chunk_size = 16384  # 16 KB bounded buffer

            async def pipe_client_to_backend():
                nonlocal last_activity
                try:
                    while not client_reader.at_eof():
                        data = await client_reader.read(chunk_size)
                        if not data:
                            break
                        last_activity = time.monotonic()
                        await rate_limiter.consume(len(data))
                        backend_writer.write(data)
                        await backend_writer.drain()
                        self._tcp_metrics_bytes_relayed += len(data)
                except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                    pass
                finally:
                    try:
                        if backend_writer and hasattr(backend_writer, "write_eof") and not backend_writer.is_closing():
                            backend_writer.write_eof()
                    except Exception:
                        pass

            async def pipe_backend_to_client():
                nonlocal last_activity
                try:
                    while not backend_reader.at_eof():
                        data = await backend_reader.read(chunk_size)
                        if not data:
                            break
                        last_activity = time.monotonic()
                        client_writer.write(data)
                        await client_writer.drain()
                        self._tcp_metrics_bytes_relayed += len(data)
                except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                    pass
                finally:
                    try:
                        if client_writer and hasattr(client_writer, "write_eof") and not client_writer.is_closing():
                            client_writer.write_eof()
                    except Exception:
                        pass

            async def monitor_idle():
                while True:
                    await asyncio.sleep(min(2.0, max(0.5, self.tcp_idle_timeout / 4)))
                    if time.monotonic() - last_activity > self.tcp_idle_timeout:
                        _log.info("TCP relay idle timeout (%ds) on port %d", self.tcp_idle_timeout, port)
                        break

            async def run_pipes():
                await asyncio.gather(pipe_client_to_backend(), pipe_backend_to_client())

            task_pipes = asyncio.create_task(run_pipes())
            task_idle = asyncio.create_task(monitor_idle())

            try:
                done, pending = await asyncio.wait(
                    [task_pipes, task_idle],
                    timeout=self.tcp_lifetime_cap,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for t in (task_pipes, task_idle):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(task_pipes, task_idle, return_exceptions=True)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log.warning("TCP relay error on port %d: %s", port, e)
        finally:
            if curr_task is not None and port in self._tcp_connections:
                self._tcp_connections[port].discard(curr_task)
            if backend_writer:
                try:
                    backend_writer.close()
                except Exception:
                    pass
            try:
                client_writer.close()
            except Exception:
                pass

            async with self._tcp_lock:
                self._total_tcp_connections = max(0, self._total_tcp_connections - 1)
                curr_ip = self._tcp_conns_per_ip.get(peer_ip, 0)
                if curr_ip <= 1:
                    self._tcp_conns_per_ip.pop(peer_ip, None)
                else:
                    self._tcp_conns_per_ip[peer_ip] = curr_ip - 1

                curr_inst = self._tcp_conns_per_inst.get(inst_id, 0)
                if curr_inst <= 1:
                    self._tcp_conns_per_inst.pop(inst_id, None)
                else:
                    self._tcp_conns_per_inst[inst_id] = curr_inst - 1

                self._tcp_metrics_open_relays = max(0, self._tcp_metrics_open_relays - 1)

    async def close_all_tcp_listeners(self) -> None:
        """Close all TCP listeners and abort all active connections."""
        for p, server in list(self._tcp_listeners.items()):
            server.close()

        # Cancel all active relay tasks and await cancellation FIRST so sockets close
        for p, tasks in list(self._tcp_connections.items()):
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        self._tcp_connections.clear()

        # Bounded wait for server sockets to finish closing
        for p, server in list(self._tcp_listeners.items()):
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=2.0)
            except Exception:
                pass
        self._tcp_listeners.clear()
        self._tcp_routes.clear()

    def get_tcp_metrics(self) -> dict:
        """Retrieve current TCP metrics snapshot (no instance IDs)."""
        return {
            "open_relays": self._tcp_metrics_open_relays,
            "bytes_relayed": self._tcp_metrics_bytes_relayed,
            "refusals": dict(self._tcp_metrics_refusals),
            "connect_failures": self._tcp_metrics_connect_failures,
        }

    def format_tcp_metrics_prometheus(self) -> str:
        """Render Prometheus format metrics for TCP relays."""
        lines = [
            "# HELP instancer_router_tcp_open_relays Current number of open TCP relays.",
            "# TYPE instancer_router_tcp_open_relays gauge",
            f"instancer_router_tcp_open_relays {self._tcp_metrics_open_relays}",
            "# HELP instancer_router_tcp_bytes_total Total bytes relayed over TCP.",
            "# TYPE instancer_router_tcp_bytes_total counter",
            f"instancer_router_tcp_bytes_total {self._tcp_metrics_bytes_relayed}",
            "# HELP instancer_router_tcp_connect_failures_total Total TCP connect failures to backends.",
            "# TYPE instancer_router_tcp_connect_failures_total counter",
            f"instancer_router_tcp_connect_failures_total {self._tcp_metrics_connect_failures}",
            "# HELP instancer_router_tcp_refusals_total Total TCP connection refusals by reason.",
            "# TYPE instancer_router_tcp_refusals_total counter",
        ]
        for reason, count in sorted(self._tcp_metrics_refusals.items()):
            lines.append(f'instancer_router_tcp_refusals_total{{reason="{reason}"}} {count}')
        return "\n".join(lines) + "\n"

    def format_metrics_prometheus(self) -> str:
        """Render combined Prometheus format metrics for HTTP and TCP."""
        lines = [
            "# HELP instancer_router_http_active_connections Current number of active HTTP client connections.",
            "# TYPE instancer_router_http_active_connections gauge",
            f"instancer_router_http_active_connections {self._total_active_connections}",
            "# HELP instancer_router_http_reading_bytes Current bytes allocated in reading budget.",
            "# TYPE instancer_router_http_reading_bytes gauge",
            f"instancer_router_http_reading_bytes {self._current_reading_bytes}",
            "# HELP instancer_router_routes_total Total active routes known to router.",
            "# TYPE instancer_router_routes_total gauge",
            f"instancer_router_routes_total {len(self._routes)}",
        ]
        return "\n".join(lines) + "\n" + self.format_tcp_metrics_prometheus()

    async def forward_stream(
        self,
        method: str,
        path: str,
        client_headers: list[tuple[str, str]],
        body: bytes,
        client_writer: asyncio.StreamWriter,
        route: Optional[dict] = None,
        client_ip: Optional[str] = None,
    ) -> None:
        """Stream client request to backend instance and stream backend response back to client."""
        if len(body) > MAX_BODY_BYTES:
            await self._refuse_stream(client_writer, 413, "Payload Too Large (Max 10MB)\n")
            return

        host_header = ""
        for hk, hv in client_headers:
            if hk.lower() == "host":
                host_header = hv
                break

        inst_id = extract_instance_id(host_header, self.instance_domain)
        if not inst_id:
            await self._refuse_stream(client_writer, 404, "Instance not found or invalid host\n")
            return

        if route is None:
            route = self._routes.get(inst_id)
        if not route or route.get("protocol") != "http":
            await self._refuse_stream(client_writer, 404, "Instance not found\n")
            return

        chal_cap = route.get("max_body_bytes", MAX_BODY_BYTES)
        if len(body) > chal_cap:
            await self._refuse_stream(client_writer, 413, f"Payload Too Large (Challenge Cap: {chal_cap})\n")
            return

        target_ip = route.get("target_ip")
        target_port = route.get("target_port")
        if not target_ip or not target_port:
            await self._refuse_stream(client_writer, 503, "Instance target unavailable\n")
            return

        acquired, err_status, err_msg = await self.acquire_connection(inst_id)
        if not acquired:
            await self._refuse_stream(client_writer, err_status, f"{err_msg}\n")
            return

        total_timeout = float(route.get("response_timeout_seconds", 120.0))

        backend_writer = None
        try:
            async def _do_forward():
                nonlocal backend_writer
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(target_ip, target_port),
                    timeout=IDLE_TIMEOUT_SECONDS,
                )
                backend_writer = writer

                # Strip hop-by-hop, Content-Length, and incoming X-L3mon-Client-IP
                fwd_headers = []
                for k, v in client_headers:
                    kl = k.lower().strip()
                    if kl not in _HOP_BY_HOP and kl != "content-length" and kl != "x-l3mon-client-ip":
                        fwd_headers.append((k.strip(), v.strip()))

                # Central router forwarding to Hop 2 sets trusted X-L3mon-Client-IP
                if self.is_central or (route and route.get("host_name")):
                    fwd_headers.append(("X-L3mon-Client-IP", client_ip or "127.0.0.1"))

                req_lines = [f"{method} {path} HTTP/1.1"]
                for k, v in fwd_headers:
                    req_lines.append(f"{k}: {v}")
                req_lines.append(f"Content-Length: {len(body)}")
                req_lines.append("Connection: close")
                req_data = "\r\n".join(req_lines).encode("utf-8") + b"\r\n\r\n" + body

                writer.write(req_data)
                await writer.drain()

                # Read backend status line
                status_line_bytes = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
                if not status_line_bytes:
                    await self._refuse_stream(client_writer, 502, "Bad Gateway: Empty backend response\n")
                    return

                status_line = status_line_bytes.decode("utf-8", errors="replace").strip()

                # Read backend response headers (up to 100 header lines)
                backend_resp_headers: list[tuple[str, str]] = []
                content_length_val: Optional[int] = None
                is_chunked: bool = False

                for _ in range(101):
                    header_line = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
                    if not header_line or header_line == b"\r\n":
                        break
                    line_str = header_line.decode("utf-8", errors="replace").strip()
                    if ":" in line_str:
                        hk, hv = line_str.split(":", 1)
                        hk_clean = hk.strip()
                        hv_clean = hv.strip()
                        hkl = hk_clean.lower()
                        if hkl == "transfer-encoding" and "chunked" in hv_clean.lower():
                            is_chunked = True
                        elif hkl not in _HOP_BY_HOP:
                            if hkl == "content-length":
                                try:
                                    content_length_val = int(hv_clean)
                                except ValueError:
                                    pass
                            else:
                                backend_resp_headers.append((hk_clean, hv_clean))

                # Send headers to client
                out_lines = [status_line]
                for hk, hv in backend_resp_headers:
                    out_lines.append(f"{hk}: {hv}")
                if is_chunked:
                    out_lines.append("Transfer-Encoding: chunked")
                elif content_length_val is not None:
                    out_lines.append(f"Content-Length: {content_length_val}")
                out_lines.append("Connection: close")
                head_bytes = "\r\n".join(out_lines).encode("utf-8") + b"\r\n\r\n"

                client_writer.write(head_bytes)
                await client_writer.drain()

                # Stream backend response body with 20 MB cap
                total_streamed = 0
                while True:
                    chunk = await asyncio.wait_for(reader.read(CHUNK_SIZE), timeout=IDLE_TIMEOUT_SECONDS)
                    if not chunk:
                        break
                    total_streamed += len(chunk)
                    if total_streamed > MAX_RESPONSE_BYTES:
                        _log.warning("Backend response exceeded 20MB cap (%d bytes); terminating connection", total_streamed)
                        break
                    client_writer.write(chunk)
                    await client_writer.drain()

            await asyncio.wait_for(_do_forward(), timeout=total_timeout)

        except asyncio.TimeoutError:
            await self._refuse_stream(client_writer, 504, "Gateway Timeout\n")
        except Exception as e:
            _log.warning("Error forwarding to %s:%s: %s", target_ip, target_port, e)
            await self._refuse_stream(client_writer, 502, "Bad Gateway\n")
        finally:
            if backend_writer is not None:
                try:
                    backend_writer.close()
                    await asyncio.wait_for(backend_writer.wait_closed(), timeout=2)
                except Exception:
                    pass
            await self.release_connection(inst_id)

    async def _refuse_stream(self, writer: asyncio.StreamWriter, status: int, text: str) -> None:
        body = text.encode("utf-8")
        status_text = (
            "OK" if status == 200
            else "Not Found" if status == 404
            else "Bad Request" if status == 400
            else "Request Timeout" if status == 408
            else "Payload Too Large" if status == 413
            else "Request Header Fields Too Large" if status == 431
            else "Service Unavailable" if status == 503
            else "Gateway Timeout" if status == 504
            else "Bad Gateway" if status == 502
            else "Error"
        )
        head = f"HTTP/1.1 {status} {status_text}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        writer.write(head.encode("ascii") + body)
        await writer.drain()

    async def handle_http_request(
        self, method: str, path: str, headers: dict | list[tuple[str, str]], body: bytes
    ) -> tuple[int, dict, bytes]:
        """Adapter for backward compatibility with test callers."""
        class _BufferWriter:
            def __init__(self):
                self.data = b""

            def write(self, b):
                self.data += b

            async def drain(self):
                pass

        if isinstance(headers, dict):
            client_headers = list(headers.items())
        else:
            client_headers = headers

        bw = _BufferWriter()
        await self.forward_stream(method, path, client_headers, body, bw)

        parts = bw.data.split(b"\r\n\r\n", 1)
        if len(parts) < 2:
            return 502, {"Content-Type": "text/plain"}, b"Bad Gateway\n"

        head_part = parts[0].decode("utf-8", errors="replace")
        resp_body = parts[1]
        lines = head_part.split("\r\n")
        status = 200
        if lines and len(lines[0].split()) >= 2:
            try:
                status = int(lines[0].split()[1])
            except ValueError:
                status = 200

        resp_headers = {}
        for line in lines[1:]:
            if ":" in line:
                hk, hv = line.split(":", 1)
                resp_headers[hk.strip()] = hv.strip()

        return status, resp_headers, resp_body
