# Implementation Plan: Prompt F - The Instancer's Second Half (TCP, Several Challenge Hosts, Host Kit)

**Branch:** `build/f-instancer-second-half`  
**Author:** venu-21 (GitHub) & coding agent  
**Date:** 3 November 2026  
**Status:** Approved by Lead Engineer (incorporating `NOTE-F.md` required changes)  
**Pull Request:** Draft PR #5 (`build/f-instancer-second-half` -> `pre-deployment`)  

---

## 1. Executive Summary & Problem Statement

The first half of the Challenge Instancer subsystem (`services/instancer` and `plugins/l3mon_instances`) delivered ephemeral per-team HTTP instances, HMAC-SHA256 dynamic flag derivation, lifecycle management, and rate limiting.

However, two major gaps prevent the platform from supporting the full contest workload (250–1,000 teams, ~1,600 players):
1. **TCP Pwn Challenges:** All binary exploitation (pwn) challenges in the author kit run as single-port TCP services started with `socat`. The current router (`services/instancer/router/router.py`) is an HTTP-only reverse proxy.
2. **Resource Scaling & Multi-Host Distribution:** Running hundreds of concurrent 256MB challenge containers on the single platform VM risks memory exhaustion, CPU contention, and side-channel leakage against the CTFd core. Challenge containers must run on dedicated external Linux VMs ("challenge hosts") running an instancer agent.

This plan details the design and implementation for:
- **F1: TCP Instance Support:** Dynamic port listeners, bidirectional TCP relay with strict backpressure, rate-limiting, connection caps (including campus NAT allowance), and route-poisoning defenses against the instance network pool.
- **F2: Automated Isolation Proof:** Comprehensive integration suite (`test_instances_isolation.py`) using a dedicated prober fixture.
- **F3: Multi-Host Architecture:** Host capacity probing with optimistic local counters, two-hop private HTTP forwarding (M2), direct TCP routing, and 30s failover.
- **F4: Challenge Host Kit:** Production compose overlay, idempotent Linux VM bootstrap script with explicit `DOCKER-USER` flow controls, runbook, and author image distribution workflow via private GHCR.
- **F5: Packing Test Tool:** Standard-library benchmarking script (`pack_test.py`) measuring instance provisioning density.
- **F6: Threat Model & Documentation:** Updating deployment, traffic, and threat model specifications.

---

## 2. Baseline Facts & Code Audit (F0 Confirmation)

Every line below was verified in the codebase on 3 November 2026:

| Component | File & Line | Current State / Finding |
|---|---|---|
| **TCP Port Allocation** | `services/instancer/manager.py#L121-L122`, `L266-L275` | `tcp_port_start=30000` and `tcp_port_count=200` are hardcoded in `InstanceManager.__init__`. Start assigns first free port and records in `_allocated_tcp_ports` and container label `l3mon.tcp_port`. |
| **TCP Route Exposure** | `services/instancer/manager.py#L562-L577` | `get_routes()` exports `id`, `protocol`, `target_ip`, `target_port`, `port` (the allocated relay port), `max_body_bytes`, and `response_timeout_seconds`. |
| **Router Protocol Support** | `services/instancer/router/router.py#L1-L489` | Pure HTTP reverse proxy on asyncio streams. Ignores routes where `protocol == "tcp"` (`main.py#L143`). No TCP socket listeners or relay loops exist. |
| **Isolation Proof** | `tests/integration/test_instances.py` | Tests end-to-end HTTP instance start and hostile HTTP traffic. No automated prober test exists verifying egress denial, metadata denial (`169.254.169.254`), inter-instance denial, or Docker daemon denial. |
| **Plugin Instancer Client** | `plugins/l3mon_instances/client.py#L1-L113` | Configured via single scalar environment variable `INSTANCER_URL` (`os.getenv("INSTANCER_URL", "http://instancer:8300")`). No host list, capacity query, or failover. |
| **Instance Data Model** | `plugins/l3mon_instances/models.py#L21` | `L3monInstance` already possesses column `host_name = db.Column(db.String(64), default="default")`. No database migration is required. |
| **Compose Overlays** | `deploy/compose/compose.instancer.yml`, `compose.instancer-production.yml` | Defines `instancer`, `socket-proxy`, `router`. Overlays do not expose TCP port ranges (e.g. `30000-30200`). |

---

## 3. Major Architectural Decisions & Lead Review Updates

### Decision 1: Several-Host Architecture & Scheduling (F3)

- **Selected Design:** **Plugin-Side Capacity Scheduler with Local Agent daemons.**
  - Challenge hosts run the same `services/instancer` container in `--agent` mode (or standard mode with its own local `router` and `socket-proxy`).
  - Challenge hosts do NOT hold `FLAG_HMAC_SECRET` and have zero access to MariaDB or Redis. CTFd (via `plugins/l3mon_instances`) computes the dynamic flag and dispatches the start request over a private VPC network to the agent.
  - **Host List:** Stored in `config/instancer/hosts.json` (example `config/instancer/hosts.example.json`):
    ```json
    [
      {
        "name": "h1",
        "url": "http://10.10.0.11:8300",
        "router_url": "http://10.10.0.11:8090",
        "tcp_host": "h1.inst.l3m0nctf.xyz",
        "token_env": "INSTANCER_TOKEN_H1",
        "router_token_env": "ROUTER_TOKEN_H1",
        "max_slots": 150,
        "weight": 1.0
      },
      {
        "name": "h2",
        "url": "http://10.10.0.12:8300",
        "router_url": "http://10.10.0.12:8090",
        "tcp_host": "h2.inst.l3m0nctf.xyz",
        "token_env": "INSTANCER_TOKEN_H2",
        "router_token_env": "ROUTER_TOKEN_H2",
        "max_slots": 150,
        "weight": 1.0
      }
    ]
    ```
  - **Host Selection Algorithm with Optimistic Local Counter (Lead Item 6):**
    1. Check existing instance: If an active instance already exists for `(team_id, challenge_id)`, route to the host matching `instance.host_name`.
    2. Capacity Probe: Periodically poll (every 5 seconds, cached in-process) `GET /v1/capacity` across all configured hosts.
    3. Optimistic Local Counter: To prevent thirty concurrent starts from clustering on the same host within the 5s cache window, the scheduler atomically increments the selected host's in-memory `slots_used` at the instant of choice. The next 5s capacity probe re-synchronizes with true host values.
    4. Sort eligible hosts by available slot ratio: `(slots_total - slots_used) / slots_total`.
    5. Failover & Health: A host that times out or returns HTTP 5xx is marked DOWN for 30 seconds for new starts. If all hosts are down or at 100% capacity, return `503 Service Unavailable` with `error: "no_capacity"` and `retry_after: 30`.
    6. Concurrency & Idempotency: `(team_id, challenge_id)` database unique constraint guarantees exactly one instance is recorded, even if concurrent requests race across hosts.
  - **One Token Per Host (Lead Item 3):** Each host uses distinct environment variables for control (`INSTANCER_TOKEN_H1`) and route inspection (`ROUTER_TOKEN_H1`). Compromise of host 1 never yields credentials for host 2.

---

### Decision 2: HTTP Traffic Routing with Several Hosts (F3 & Lead Item 2, 4)

- **Selected Design:** **Two-Hop Private Forwarding via Platform Router (Moved to M2).**
  - Players connect to `<id>.<instance_domain>` via Cloudflare -> Nginx on Platform VM (Port 8443) -> Central Router (`l3mon-router` on Platform VM).
  - **Strict Hop 1 Destination Integrity (Lead Item 2):**
    - The Central Router polls all configured challenge host agents (`GET /v1/routes` with `ROUTER_TOKEN_H<N>`).
    - The Central Router maps each instance ID to its host, but forwards **strictly to the configured `router_url` of that host** (e.g. `http://10.10.0.11:8090`), NEVER to any target address provided in the agent's route payload.
    - The agent route list supplies the instance `id` only.
    - If two hosts report the same instance ID, the host recorded in the database / local route map takes precedence.
    - Any instance ID that is not 32 lowercase hex characters is rejected immediately.
  - For remote instances, the Central Router streams the HTTP request over the private VPC network to the challenge host's local router, which in turn forwards to the container on its dedicated bridge network.
  - Stale route cleanup: If an agent becomes unreachable, its routes are retained for 30 seconds, then purged.

---

### Decision 3: TCP Traffic Routing with Several Hosts (F1 & F3)

- **Selected Design:** **Direct TCP to Per-Host Public DNS Record.**
  - Cloudflare does not proxy arbitrary TCP ranges on free/standard plans. Therefore, TCP connections cannot pass through Cloudflare.
  - Each challenge host has a direct DNS A-record with Cloudflare proxying disabled (DNS-only / Grey Cloud): e.g., `h1.inst.l3m0nctf.xyz -> <Host 1 Public IP>`.
  - When starting a TCP challenge, the plugin returns `{"protocol": "tcp", "host": "h1.inst.l3m0nctf.xyz", "port": 30042}`.
  - The player runs `nc h1.inst.l3m0nctf.xyz 30042` directly to the challenge host.
  - The challenge host router binds the TCP relay ports (`30000-30200`) and relays packets directly to the challenge container over the container's isolated bridge network.

---

### Decision 4: Author Image Distribution Workflow (F4.4 & Lead Item 7)

- **Selected Workflow: GitHub Container Registry (GHCR) with Pinned SHA-256 Digests.**
  1. **Strict CI Build & Push Only:**
     - Images are built and pushed exclusively by GitHub Actions workflows in the private challenges repository using `GITHUB_TOKEN`.
     - Authors never receive individual write access to the registry, preventing accidental or malicious overwriting of challenge images.
  2. **Private Packages Only:** Every image package in GHCR is marked private.
  3. **Read-Only Fine-Grained Token on Challenge Hosts:**
     - A machine-scoped PAT with `read:packages` only is stored root-only (mode `0600`) at `/etc/l3mon/ghcr-token` (outside the compose directory).
     - The host kit bootstrap logs the host Docker daemon into GHCR: `cat /etc/l3mon/ghcr-token | docker login ghcr.io -u <user> --password-stdin`.
     - Key rotation procedure is detailed in the runbook.
  4. **Threat Model Reality: Zero Flags Baked in Images:**
     - If a challenge host is compromised, the attacker can inspect pulled images on disk.
     - Therefore, **no flag may ever be baked into an image**. All dynamic flags are injected via the `FLAG` environment variable at container creation time.
  5. **Manifest Entry Formatting:**
     - Manifests require `image = "ghcr.io/sivaahari/<repo>/<slug>@sha256:<digest>"`.
     - Written automatically by the lead's sync tool based on the CI build output.
  6. **Offline Fallback (`docker load`):**
     - `docker load` imports images with an image ID (e.g. `sha256:<image_id>`). In standard Docker Engine, running `docker run image@sha256:<digest>` fails unless a repo digest is associated by registry pull.
     - Documented in runbook: For offline airgapped use, images loaded via `docker load` must be explicitly tagged with a local registry prefix or run by name:tag in dev mode (`INSTANCER_DEV=1`).

---

### Decision 5: TCP Relay Byte-Rate & Connection Caps Justification (F1.3 & Lead Items 5, 8)

- **Byte-Rate Sanity Limit:**
  - `INSTANCER_TCP_RATE_LIMIT_BYTES_PER_SEC = 2 * 1024 * 1024` (2 MB/s per connection burstable up to 4 MB token bucket).
  - Configurable via environment variable.
  - Measured locally: A 10 MB payload takes ~3.1 seconds across the burstable relay, allowing rapid delivery of legitimate ropchain / heap-spray payloads while capping sustained runaway floods at 2 MB/s.
- **Per-Address Connection Cap (Lead Item 5):**
  - **TCP:** Default set to **60** concurrent connections per client IP address across all ports (`INSTANCER_TCP_MAX_CONNS_PER_IP = 60`).
  - *Why:* Many student teams and university cohorts compete from behind a single campus NAT gateway or dorm Wi-Fi router. Combined with pwntools multi-threading / race condition scripts opening 5–10 sockets per team, 20 was too restrictive; 60 accommodates several active teams sharing a public IPv4 gateway.
  - **Global TCP Cap:** Maintained at 500 active relay connections per host (`INSTANCER_TCP_MAX_TOTAL_CONNS = 500`).
  - **HTTP:** Maintained at 50 per IP (with option for 100 on platform VM).

---

## 4. Detailed Specification: F1 TCP Router & Relay

### 4.1 Configuration Settings & Validation
In `services/instancer/manager.py` and `services/instancer/router/`:
- `INSTANCER_TCP_PORT_START`: default `30000` (integer between 1024 and 65535).
- `INSTANCER_TCP_PORT_COUNT`: default `200` (integer between 1 and 2000).
- Range constraint: `INSTANCER_TCP_PORT_START + INSTANCER_TCP_PORT_COUNT - 1 <= 65535`.
- `INSTANCER_TCP_IDLE_TIMEOUT`: default `300` (seconds).
- `INSTANCER_TCP_LIFETIME_CAP`: default `3600` (1 hour).
- `INSTANCER_TCP_MAX_CONNS_PER_INSTANCE`: default `5`.
- `INSTANCER_TCP_MAX_CONNS_PER_IP`: default `60` (setting, campus NAT friendly).
- `INSTANCER_TCP_MAX_TOTAL_CONNS`: default `500`.
- `INSTANCER_TCP_RATE_LIMIT_BPS`: default `2097152` (2 MB/s).
- `INSTANCER_INSTANCE_POOL`: default `10.200.0.0/16` (CIDR matching `daemon.json`'s `default-address-pools`).

### 4.2 Dynamic Listener Lifecycle & Port Reuse Cleanliness
- The router dynamically maintains `_tcp_listeners: dict[int, asyncio.Server]`.
- When `update_routes(routes)` detects a newly assigned TCP port:
  - Spawns `asyncio.start_server(handle_tcp_client, "0.0.0.0", port)`.
- When a route is removed (instance reaped/stopped):
  - Closes the listener socket on that port (`server.close()`).
  - Actively aborts and closes all existing active client sockets and backend connections relaying on that port.
  - Clears any buffered frames.
  - **Port Reuse Guarantee:** When another team acquires that same port a minute later, zero bytes from the previous session can leak into the new connection.

### 4.3 Bidirectional Relay with Backpressure & Rate Limiting
```python
class TokenBucketRateLimiter:
    def __init__(self, rate_bps: int = 2 * 1024 * 1024, burst_bytes: int = 4 * 1024 * 1024):
        self.rate = rate_bps
        self.burst = burst_bytes
        self.tokens = burst_bytes
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, nbytes: int):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            if self.tokens < nbytes:
                deficit = nbytes - self.tokens
                wait_time = deficit / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= nbytes
```
- Strict backpressure using bounded buffers (`chunk_size = 16384`) and `await writer.drain()`.
- Clean half-close (`writer.write_eof()`) and reset handling.

### 4.4 Route-Poisoning & Pool Validation Defenses (F1.4 & Lead Item 1)
Before connecting to any `target_ip:target_port`:
1. Parse `ipaddress.ip_address(target_ip)`.
2. Parse `pool = ipaddress.ip_network(os.getenv("INSTANCER_INSTANCE_POOL", "10.200.0.0/16"))`.
3. **Strict Pool Membership Check:** The target IP must lie strictly **inside** `pool`.
   - Reject any address outside `pool`, including public IPs (`8.8.8.8`), cloud metadata (`169.254.169.254`), host loopback (`127.0.0.1`), and **private VPC addresses outside the pool** (e.g. `10.0.0.5`, platform VM, CTFd, MariaDB).
4. **Subnet Boundary Checks:** Reject `pool.network_address`, `pool.broadcast_address`, and the standard `.1` container gateway address.
5. Target port must be between 1 and 65535.

---

## 5. Detailed Specification: F2 Automated Isolation Proof

Write `tests/integration/test_instances_isolation.py` and fixture `services/instancer/tests/fixtures/prober/`.

### 5.1 Prober Fixture Container (`services/instancer/tests/fixtures/prober/`)
- Minimal Alpine/Debian Docker container running an unprivileged Python script (`UID 10001`).
- Contains probing functions that test:
  1. `probe_instance_by_name(other_id)`: DNS lookup and TCP connect. (Must FAIL)
  2. `probe_instance_by_ip(other_ip, port)`: Direct TCP connect across Docker subnets. (Must FAIL)
  3. `probe_host_gateway()`: Connect to default gateway IP on ports 22, 8080, 2375. (Must FAIL)
  4. `probe_internet()`: Connect to `1.1.1.1:53` and DNS lookup `google.com`. (Must FAIL)
  5. `probe_metadata()`: HTTP GET `http://169.254.169.254/computeMetadata/v1/`. (Must FAIL)
  6. `probe_docker_daemon()`: Check `/var/run/docker.sock` existence and connect to `host:2375`. (Must FAIL)
  7. `probe_instancer_api()`: Connect to `instancer:8300` and `8301`. (Must FAIL)
  8. `probe_router_control()`: Connect to router internal interfaces. (Must FAIL)
  9. `probe_ctfd_database_redis()`: Attempt connects to `ctfd:8000`, `db:3306`, `cache:6379`. (Must FAIL)
- Returns structured JSON results on `/report` endpoint or exit code.

### 5.2 External Test Runner Assertions
From outside (the host running pytest):
- Verify only the router's published ports (e.g. 8090, 30000+) are open.
- Verify the internal container ports and Docker socket proxy ports (2375) are unreachable from the external host interface.

---

## 6. Detailed Specification: F3 Several Hosts & Failover

### 6.1 Host Configuration Schema (`config/instancer/hosts.example.json`)
```json
[
  {
    "name": "default",
    "url": "http://instancer:8300",
    "router_url": "http://router:8090",
    "tcp_host": "localhost",
    "token_env": "INSTANCER_TOKEN",
    "router_token_env": "ROUTER_TOKEN",
    "max_slots": 200,
    "weight": 1.0
  }
]
```

### 6.2 Host Scheduler with Optimistic Local Counter
- In `plugins/l3mon_instances/client.py`:
  - `HostManager` loads `hosts.json`.
  - Maintains `_host_health: dict[str, HostStatus]`.
  - Maintains `_optimistic_slots: dict[str, int]`.
  - On `start_instance(team_id, challenge_id, slug)`:
    - Finds least-loaded healthy host considering `(slots_used + _optimistic_slots[host]) / slots_total`.
    - Atomically increments `_optimistic_slots[host]`.
    - If host fails with connection error or HTTP 5xx: marks host down for 30s and retries next best host.
    - Persists selected `host_name` in `L3monInstance.host_name`.
  - On `renew_instance`, `stop_instance`, `get_instance`:
    - Dispatches directly to the host specified in `instance.host_name`.

---

## 7. Detailed Specification: F4 Host Kit & Bootstrap (Lead Item 8)

### 7.1 Compose File (`deploy/compose/compose.challenge-host.yml`)
- Services:
  - `agent` (`services/instancer` in agent mode): port 8300 bound to private IP only.
  - `socket-proxy` (`tecnativa/docker-socket-proxy` pinned by digest): only `CONTAINERS=1`, `NETWORKS=1`.
  - `router` (`services/instancer/router`): port 8090 and port range `30000-30200` published.
- Hardening:
  - `read_only: true`
  - `no-new-privileges: true`
  - `cap_drop: [ALL]`
  - Resource limits (`cpus`, `mem_limit`).

### 7.2 Bootstrap Script (`deploy/challenge-host/bootstrap.sh`)
- Idempotent Bash script.
- Default `--dry-run`; executes actions only with `--apply`.
- Tested with `shellcheck --severity=warning -x`.
- Unified instance pool variable:
  ```bash
  INSTANCER_INSTANCE_POOL="${INSTANCER_INSTANCE_POOL:-10.200.0.0/16}"
  ```
  Writes `default-address-pools` in `/etc/docker/daemon.json` and sets environment variable in `/etc/l3mon/instancer.env`.
- Explicit `DOCKER-USER` iptables rules:
  ```bash
  # 1. Allow established and related traffic
  iptables -A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

  # 2. Allow router to instance pool
  iptables -A DOCKER-USER -s "$ROUTER_IP" -d "$INSTANCER_INSTANCE_POOL" -j ACCEPT

  # 3. Allow agent port 8300 from Platform VM only
  iptables -A DOCKER-USER -p tcp -s "$PLATFORM_VM_IP" --dport 8300 -j ACCEPT

  # 4. Drop all traffic from instance pool to any other network
  iptables -A DOCKER-USER -s "$INSTANCER_INSTANCE_POOL" -j DROP

  # 5. Drop cloud metadata from all bridge interfaces
  iptables -A DOCKER-USER -d 169.254.169.254 -j DROP
  ```
- Tested with `iptables -C` checks and dry-run stub binary validation.

---

## 8. Detailed Specification: F5 Packing Test Tool

`services/instancer/tools/pack_test.py`:
- Standard library only (`urllib.request`, `json`, `time`, `argparse`).
- Arguments: `--url`, `--token`, `--count` (default 10), `--slug`, `--interval`, `--yes`.
- Safety:
  - Checks available system memory via `/proc/meminfo` or `psutil`; aborts if free RAM < 2 GB.
  - Tags all test instances with `l3mon.test_pack=1`.
  - Cleans up all started instances in `finally` block.
- Metrics Output:
  - Table: Instance index, elapsed startup time, p50 latency, p95 latency, host memory used.

---

## 9. Comprehensive Break-on-Purpose Test Matrix (38 Cases)

| Rule # | Invariant Under Test | How It Is Broken | Test That Catches It |
|---|---|---|---|
| 1 | TCP Port Range Min Bound | Set `INSTANCER_TCP_PORT_START=80` (<1024) | `test_tcp_port_range_validation` |
| 2 | TCP Port Range Max Bound | Set `INSTANCER_TCP_PORT_START=65000, COUNT=1000` (>65535) | `test_tcp_port_range_validation` |
| 3 | TCP Port Count Max Bound | Set `INSTANCER_TCP_PORT_COUNT=2500` (>2000) | `test_tcp_port_range_validation` |
| 4 | Closed Port on Missing Route | Don't close listener on route deletion | `test_tcp_listener_closes_on_route_removal` |
| 5 | Port Reuse Zero Leakage | Don't terminate old connections on port reuse | `test_tcp_port_reuse_no_byte_leakage` |
| 6 | Idle Timeout Disconnect | Disable idle timeout timer | `test_tcp_idle_timeout_drops_connection` |
| 7 | Lifetime Cap Disconnect | Disable connection lifetime cap | `test_tcp_lifetime_cap_enforced` |
| 8 | Per-Instance Concurrency Cap | Remove `max_connections_per_instance` check | `test_tcp_max_connections_per_instance_refused` |
| 9 | Per-IP Concurrency Cap (60) | Remove `max_connections_per_ip` check | `test_tcp_max_connections_per_ip_refused` |
| 10 | Global Concurrency Cap | Remove `max_total_connections` check | `test_tcp_global_connections_cap_refused` |
| 11 | Byte-Rate Sanity Limit | Remove token bucket rate limiter | `test_tcp_rate_limiter_throttles_firehose` |
| 12 | Connect Timeout to Instance | Remove connect timeout | `test_tcp_connect_timeout_aborts_fast` |
| 13 | Failed Connect Silent Close | Send verbose error banner on connect failure | `test_tcp_connect_failure_closes_silently` |
| 14 | Pool Defense: Metadata Denied | Feed route with `target_ip="169.254.169.254"` | `test_route_poisoning_metadata_rejected` |
| 15 | Pool Defense: Loopback Denied | Feed route with `target_ip="127.0.0.1"` | `test_route_poisoning_loopback_rejected` |
| 16 | Pool Defense: Gateway Denied | Feed route with container bridge gateway IP (`10.200.0.1`) | `test_route_poisoning_gateway_rejected` |
| 17 | Pool Defense: Public IP Denied | Feed route with `target_ip="8.8.8.8"` | `test_route_poisoning_public_ip_rejected` |
| 18 | Pool Defense: Private Outside Pool | Feed route with `target_ip="10.0.0.5"` (platform VM IP) | `test_route_poisoning_private_outside_pool_rejected` |
| 19 | Pool Defense: Docker Socket Denied | Feed route targeting Docker socket proxy IP:port | `test_route_poisoning_docker_proxy_rejected` |
| 20 | Pool Defense: CTFd Core Denied | Feed route targeting CTFd internal IP | `test_route_poisoning_ctfd_ip_rejected` |
| 21 | Hop 1 Address Integrity | Central router forwards to agent-supplied target IP | `test_hop1_ignores_agent_supplied_target_address` |
| 22 | Duplicated ID Across Hosts | Two hosts return same instance ID | `test_duplicated_instance_id_precedence` |
| 23 | Non-Hex Instance ID | Agent returns malformed ID (`slug_123`) | `test_malformed_instance_id_rejected` |
| 24 | Half-Close Clean Handling | Omit `writer.write_eof()` propagation | `test_tcp_half_close_propagated` |
| 25 | Backpressure Bounded Buffers | Use unbounded `read()` without drain | `test_tcp_backpressure_memory_bounded` |
| 26 | Host Selection Least Loaded | Invert capacity comparator (pick most loaded) | `test_host_scheduler_picks_least_loaded` |
| 27 | Optimistic Slot Increment | Remove optimistic local slot counter | `test_concurrent_starts_optimistic_counter` |
| 28 | Host Down 30s Failover | Don't mark failed host down for 30s | `test_host_scheduler_failover_on_unreachable` |
| 29 | All Hosts Down Handling | Don't return 503 `no_capacity` on total outage | `test_host_scheduler_all_down_returns_503` |
| 30 | Dead Host Route Expiry (30s) | Central router never purges dead host routes | `test_central_router_purges_dead_host_routes_after_30s` |
| 31 | Per-Host Route Token Check | Use wrong token for Host 2 route poll | `test_per_host_route_tokens_enforced` |
| 32 | Poisoned Agent Capacity Reply | Return negative or string `slots_used` from agent | `test_malformed_agent_capacity_rejected` |
| 33 | Poisoned Agent Route Body | Return route with invalid types from agent | `test_malformed_agent_route_rejected` |
| 34 | Idempotent Multi-Host Racing | Disable DB unique constraint on instance start | `test_concurrent_start_yields_single_instance` |
| 35 | Pack Tool RAM Margin Guard | Remove free memory preflight check | `test_pack_tool_refuses_low_ram` |
| 36 | Bootstrap Dry-Run Safety | Execute commands during `--dry-run` (tested via stub trap) | `test_bootstrap_dry_run_executes_nothing_stub_trap` |
| 37 | Pwn Fixture Clean Flag Read | Return static flag instead of dynamic `$FLAG` | `test_tcp_fixture_returns_dynamic_flag` |
| 38 | Prober Egress Detection | Permit outbound ping in prober network | `test_prober_detects_egress_violation` |

---

## 10. Milestone Roadmap & Execution Schedule

- **Milestone 1 (Due 10 November):**
  - Commit approved plan update incorporating `NOTE-F.md`.
  - F1: Implement TCP port configuration (`INSTANCER_TCP_PORT_START`, `INSTANCER_TCP_PORT_COUNT`), `INSTANCER_INSTANCE_POOL` validation, per-IP cap (60), rate limiting (2 MB/s, 4 MB burst), dynamic listener lifecycle, port reuse clean slate, and bidirectional relay in `services/instancer/router/`.
  - F1: Build TCP pwn test fixture (`services/instancer/tests/fixtures/tcp/`).
  - F1: Write TCP unit test suite with mock streams and route poisoning checks.
  - F2: Build prober test fixture (`services/instancer/tests/fixtures/prober/`) and write `tests/integration/test_instances_isolation.py`.
  - Start Part 2 in `docs/handoff/instancer.md`.
  - Push branch and verify CI checks.
- **Milestone 2 (Due 14 November):**
  - F3: Two-hop HTTP private forwarding on Central Router with 30s dead-host route purge.
  - F3: Single remote host agent mode and plugin capacity client with optimistic counter.
  - F4: Author `deploy/compose/compose.challenge-host.yml`.
  - F5: Build `services/instancer/tools/pack_test.py` and run local benchmarks.
  - Verify Draft PR #5 CI checks.
- **Milestone 3 (Due 18 November):**
  - F3: Multi-host scheduler and 30s failover.
  - F4: Author `deploy/challenge-host/bootstrap.sh` (with `iptables` rules and dry-run stub tests) and `README.md`.
  - F6: Update `docs/deploy/instancer.md`, `docs/deploy/instancer-traffic.md`, and `docs/security/instancer-threat-model.md`.
  - Final review, break-on-purpose audit, and handoff submission before 19 November hard stop.
