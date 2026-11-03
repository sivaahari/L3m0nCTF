# Implementation Plan: Prompt F - The Instancer's Second Half (TCP, Several Challenge Hosts, Host Kit)

**Branch:** `build/f-instancer-second-half`  
**Author:** venu-21 (GitHub) & coding agent  
**Date:** 3 November 2026  
**Status:** Milestone 1 Plan - For Lead Engineer Review  

---

## 1. Executive Summary & Problem Statement

The first half of the Challenge Instancer subsystem (`services/instancer` and `plugins/l3mon_instances`) delivered ephemeral per-team HTTP instances, HMAC-SHA256 dynamic flag derivation, lifecycle management, and rate limiting.

However, two major gaps prevent the platform from supporting the full contest workload (250–1,000 teams, ~1,600 players):
1. **TCP Pwn Challenges:** All binary exploitation (pwn) challenges in the author kit run as single-port TCP services started with `socat`. The current router (`services/instancer/router/router.py`) is an HTTP-only reverse proxy.
2. **Resource Scaling & Multi-Host Distribution:** Running hundreds of concurrent 256MB challenge containers on the single platform VM risks memory exhaustion, CPU contention, and side-channel leakage against the CTFd core. Challenge containers must run on dedicated external Linux VMs ("challenge hosts") running an instancer agent.

This plan details the design and implementation for:
- **F1: TCP Instance Support:** Dynamic port listeners, bidirectional TCP relay with strict backpressure, rate-limiting, connection caps, and route-poisoning defenses.
- **F2: Automated Isolation Proof:** Comprehensive integration suite (`test_instances_isolation.py`) using a dedicated prober fixture.
- **F3: Multi-Host Architecture:** Host capacity probing, scheduling, failover, two-hop private HTTP forwarding, and direct TCP routing.
- **F4: Challenge Host Kit:** Production compose overlay, idempotent Linux VM bootstrap script, runbook, and author image distribution workflow.
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

## 3. Major Architectural Decisions & Alternatives Considered

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
        "tcp_host": "h1.inst.l3m0nctf.xyz",
        "token_env": "INSTANCER_TOKEN_H1",
        "max_slots": 150,
        "weight": 1.0
      },
      {
        "name": "h2",
        "url": "http://10.10.0.12:8300",
        "tcp_host": "h2.inst.l3m0nctf.xyz",
        "token_env": "INSTANCER_TOKEN_H2",
        "max_slots": 150,
        "weight": 1.0
      }
    ]
    ```
  - **Host Selection Algorithm:**
    1. Check existing instance: If an active instance already exists for `(team_id, challenge_id)`, route to the host matching `instance.host_name`.
    2. Capacity Probe: Periodically poll (every 5 seconds, cached in-process) `GET /v1/capacity` across all configured hosts.
    3. Sort eligible hosts by available slot ratio: `(slots_total - slots_used) / slots_total`.
    4. Failover & Health: A host that times out or returns HTTP 5xx is marked DOWN for 30 seconds for new starts. If all hosts are down or at 100% capacity, return `503 Service Unavailable` with `error: "no_capacity"` and `retry_after: 30`.
    5. Concurrency & Idempotency: `(team_id, challenge_id)` database unique constraint guarantees exactly one instance is recorded, even if concurrent requests race across hosts.
- **Rejected Alternatives:**
  - *Central Master Daemon (Hierarchical Orchestrator):* Running an instancer master that proxies requests to worker instancers introduces a single point of failure, complex two-layer state synchronization, and extra private RPC latency. Having the CTFd plugin directly select the host leverages existing database transactions and simplifies the architecture.
  - *Dynamic DNS Round-Robin:* DNS has TTL caching latency and cannot adapt to instantaneous memory pressure or sudden container failures.

---

### Decision 2: HTTP Traffic Routing with Several Hosts (F3)

- **Selected Design:** **Two-Hop Private Forwarding via Platform Router.**
  - Players connect to `<id>.<instance_domain>` via Cloudflare -> Nginx on Platform VM (Port 8443) -> Central Router (`l3mon-router` on Platform VM).
  - The Central Router polls all configured challenge host agents (`GET /v1/routes` with `ROUTER_TOKEN`). It maps each instance ID to its assigned host agent: `id -> target_host_ip:8090`.
  - For remote instances, the Central Router streams the HTTP request over the private VPC network to the challenge host's local router, which in turn forwards to the container on its dedicated bridge network.
  - Stale route cleanup: If an agent becomes unreachable, its routes are retained for 30 seconds, then purged.
- **Rejected Alternatives:**
  - *Cloudflare Direct to Remote Hosts:* Cloudflare Universal SSL does not support multi-level subdomains (`<id>.h1.inst.l3m0nctf.xyz`). Terminating on the central platform VM preserves the single wildcard SSL cert (`*.l3m0nctf.xyz`) and Authenticated Origin Pulls (AOP) security.

---

### Decision 3: TCP Traffic Routing with Several Hosts (F1 & F3)

- **Selected Design:** **Direct TCP to Per-Host Public DNS Record.**
  - Cloudflare does not proxy arbitrary TCP ranges on free/standard plans. Therefore, TCP connections cannot pass through Cloudflare.
  - Each challenge host has a direct DNS A-record with Cloudflare proxying disabled (DNS-only / Grey Cloud): e.g., `h1.inst.l3m0nctf.xyz -> <Host 1 Public IP>`.
  - When starting a TCP challenge, the plugin returns `{"protocol": "tcp", "host": "h1.inst.l3m0nctf.xyz", "port": 30042}`.
  - The player runs `nc h1.inst.l3m0nctf.xyz 30042` directly to the challenge host.
  - The challenge host router binds the TCP relay ports (`30000-30200`) and relays packets directly to the challenge container over the container's isolated bridge network.
- **Rejected Alternatives:**
  - *SSH Port Forwarding / SOCKS5:* Unnecessarily complicated for contestants and incompatible with standard pwn automation scripts (pwntools).
  - *Central TCP Multiplexing via SNI:* Plain netcat/socat pwn exploits do not send TLS ClientHello with SNI. Port-per-instance is the only reliable method for raw TCP challenges.

---

### Decision 4: Author Image Distribution Workflow (F4.4 Recommendation)

- **Evaluation of Options:**
  1. *Option (a) - Local Private Registry on Challenge Host:*
     - A private Docker `registry:2` container runs on `127.0.0.1:5000` on the challenge host.
     - Authors push images via SSH port forwarding (`ssh -L 5000:localhost:5000 user@host`).
     - Pros: Completely self-hosted, no external registry costs or rate limits.
     - Cons: Fragile author coordination, images must be pushed separately to each challenge host VM.
  2. *Option (b) - GitHub Container Registry (GHCR) with Read-Only Host Token (RECOMMENDED):*
     - The project uses `ghcr.io/sivaahari/<repo>/<slug>@sha256:<digest>`.
     - A machine-scoped GitHub Personal Access Token (PAT) with `read:packages` is stored in `.secrets/GHCR_READ_TOKEN` on each challenge host.
     - The host kit bootstrap logs Docker into GHCR.
     - Images are built once by authors or CI and pulled by all challenge hosts.
     - Pros: Centralized, high availability, immutable SHA-256 digests, multi-host consistency.
     - Cons: Requires internet access during initial pull (or pre-pull before competition starts).
  3. *Option (c) - `docker load` from Archive with Local Digest Verification:*
     - Challenge images are exported to `.tar.gz` and copied via SCP.
     - Tested fact: `docker load` preserves image ID, but `docker run name@sha256:<digest>` fails if the repo digest was not populated by a registry pull unless tagged specifically.
- **Recommendation:** **Option (b) (GHCR with pinned SHA-256 digests)** as primary workflow for production, with Option (c) (`docker load` with pinned image ID tagging) as offline fallback.

---

### Decision 5: TCP Relay Byte-Rate Sanity Limit Justification (F1.3)

- **Requirement:** Stop firehoses/network flood DoS while permitting heap-spraying exploits.
- **Analysis:**
  - Pwn exploits spraying large heap chunks (e.g. 10MB of ropchains or memory layouts) typically transmit 1–5 MB within a few seconds over a single connection.
  - A malicious player running an unconstrained `cat /dev/urandom | nc` firehose over 5 concurrent connections can saturate a 1 Gbps host link and exhaust router memory buffers.
- **Chosen Default:**
  - `INSTANCER_TCP_RATE_LIMIT_BYTES_PER_SEC = 2 * 1024 * 1024` (2 MB/s per connection burstable up to 4 MB token bucket).
  - Configurable via environment variable.
  - This easily permits standard pwn payloads (64KB–1MB) to transfer in < 500ms, while capping runaway loops at 2 MB/s.

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
- `INSTANCER_TCP_MAX_CONNS_PER_IP`: default `20`.
- `INSTANCER_TCP_MAX_TOTAL_CONNS`: default `500`.
- `INSTANCER_TCP_RATE_LIMIT_BPS`: default `2097152` (2 MB/s).

### 4.2 Dynamic Listener Lifecycle & Port Reuse Cleanliness
- The router dynamically maintains `_tcp_listeners: dict[int, asyncio.Server]`.
- When `update_routes(routes)` detects a newly assigned TCP port:
  - Spawns `asyncio.start_server(handle_tcp_client, "0.0.0.0", port)`.
- When a route is removed (instance reaped/stopped):
  - Closes the listener socket on that port (`server.close()`).
  - Actively aborts and closes all existing active client sockets and backend connections relaying on that port.
  - Clears any buffered frames.
  - **Port Reuse Guarantee:** When another team acquires that same port a minute later, zero bytes from the previous session can leak into the new connection.

### 4.3 Bidirectional Relay with Backpressure
```python
async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rate_limiter, tracker):
    chunk_size = 16384  # 16 KB bounded buffer
    while not reader.at_eof():
        data = await reader.read(chunk_size)
        if not data:
            break
        await rate_limiter.consume(len(data))
        writer.write(data)
        await writer.drain()  # Strict backpressure
```
- Handles `asyncio.IncompleteReadError`, connection resets (`ECONNRESET`), and half-close (`writer.write_eof()`) cleanly.

### 4.4 Route-Poisoning & SSRF Defenses (F1.4)
Before connecting to any `target_ip:target_port`:
1. Parse `ipaddress.ip_address(target_ip)`.
2. Reject non-private IP addresses (`not ip.is_private` or `ip.is_loopback` or `ip.is_multicast`).
3. Reject cloud metadata address: `169.254.169.254` (link-local).
4. Reject host loopback: `127.0.0.0/8`, `::1`.
5. Reject reserved Docker subnet gateway addresses: must match container network CIDR pattern (`172.16.0.0/12` or `10.0.0.0/8`), but never the `.1` gateway IP.
6. Target port must be between 1 and 65535.

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
- Exposes findings on an HTTP endpoint `/report` or via container exit status / stdout.

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
    "tcp_host": "localhost",
    "token_env": "INSTANCER_TOKEN",
    "max_slots": 200,
    "weight": 1.0
  }
]
```

### 6.2 Host Scheduler & Failover Logic
- In `plugins/l3mon_instances/client.py`:
  - `HostManager` loads `hosts.json`.
  - Maintains `_host_health: dict[str, HostStatus]`.
  - On `start_instance(team_id, challenge_id, slug)`:
    - Finds least-loaded healthy host.
    - If host fails with connection error or HTTP 5xx: marks host down for 30s and retries next best host.
    - Persists selected `host_name` in `L3monInstance.host_name`.
  - On `renew_instance`, `stop_instance`, `get_instance`:
    - Dispatches directly to the host specified in `instance.host_name`.

---

## 7. Detailed Specification: F4 Host Kit & Bootstrap

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
- Installs Docker Engine from official upstream repo.
- Configures `/etc/docker/daemon.json` with `default-address-pools` (e.g. `base: 10.200.0.0/16`, `size: 28`).
- Configures `DOCKER-USER` iptables rules dropping metadata (`169.254.169.254`) and RFC1918 traffic from challenge bridges.
- Sets kernel limits (`vm.max_map_count = 262144`, `fs.file-max = 2097152`).

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

## 9. Planned Break-on-Purpose Test Matrix (30+ Cases)

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
| 9 | Per-IP Concurrency Cap | Remove `max_connections_per_ip` check | `test_tcp_max_connections_per_ip_refused` |
| 10 | Global Concurrency Cap | Remove `max_total_connections` check | `test_tcp_global_connections_cap_refused` |
| 11 | Byte-Rate Sanity Limit | Remove token bucket rate limiter | `test_tcp_rate_limiter_throttles_firehose` |
| 12 | Connect Timeout to Instance | Remove connect timeout | `test_tcp_connect_timeout_aborts_fast` |
| 13 | Failed Connect Silent Close | Send verbose error banner on connect failure | `test_tcp_connect_failure_closes_silently` |
| 14 | SSRF Defense: Metadata Denied | Feed route with `target_ip="169.254.169.254"` | `test_route_poisoning_metadata_rejected` |
| 15 | SSRF Defense: Loopback Denied | Feed route with `target_ip="127.0.0.1"` | `test_route_poisoning_loopback_rejected` |
| 16 | SSRF Defense: Gateway Denied | Feed route with container bridge gateway IP | `test_route_poisoning_gateway_rejected` |
| 17 | SSRF Defense: Public IP Denied | Feed route with `target_ip="8.8.8.8"` | `test_route_poisoning_public_ip_rejected` |
| 18 | SSRF Defense: Docker Socket Denied | Feed route targeting Docker socket proxy IP:port | `test_route_poisoning_docker_proxy_rejected` |
| 19 | SSRF Defense: CTFd Core Denied | Feed route targeting CTFd internal IP | `test_route_poisoning_ctfd_ip_rejected` |
| 20 | Half-Close Clean Handling | Omit `writer.write_eof()` propagation | `test_tcp_half_close_propagated` |
| 21 | Backpressure Bounded Buffers | Use unbounded `read()` without drain | `test_tcp_backpressure_memory_bounded` |
| 22 | Host Selection Least Loaded | Invert capacity comparator (pick most loaded) | `test_host_scheduler_picks_least_loaded` |
| 23 | Host Down Failover | Don't mark failed host down for 30s | `test_host_scheduler_failover_on_unreachable` |
| 24 | All Hosts Down Handling | Don't return 503 `no_capacity` on total outage | `test_host_scheduler_all_down_returns_503` |
| 25 | Host Cache Expiry | Set cache TTL to 0 or infinite | `test_capacity_cache_ttl_revalidation` |
| 26 | Poisoned Agent Capacity Reply | Return negative or string `slots_used` from agent | `test_malformed_agent_capacity_rejected` |
| 27 | Poisoned Agent Route Body | Return route with invalid types from agent | `test_malformed_agent_route_rejected` |
| 28 | Idempotent Multi-Host Racing | Disable DB unique constraint on instance start | `test_concurrent_start_yields_single_instance` |
| 29 | Pack Tool RAM Margin Guard | Remove free memory preflight check | `test_pack_tool_refuses_low_ram` |
| 30 | Bootstrap Dry-Run Safety | Execute commands during `--dry-run` | `test_bootstrap_dry_run_executes_nothing` |
| 31 | Pwn Fixture Clean Flag Read | Return static flag instead of dynamic `$FLAG` | `test_tcp_fixture_returns_dynamic_flag` |
| 32 | Prober Egress Detection | Permit outbound ping in prober network | `test_prober_detects_egress_violation` |

---

## 10. Milestone Roadmap & Execution Schedule

- **M1 (Due 10 November):**
  - Author and commit implementation plan `docs/superpowers/plans/2026-11-03-f-instancer-second-half.md`.
  - F1: Build TCP port configuration, dynamic listeners, and bidirectional TCP relay in `services/instancer/router/`.
  - F1: Build TCP pwn test fixture (`services/instancer/tests/fixtures/tcp/`).
  - F1: Write TCP unit test suite with mock streams and route poisoning checks.
  - F2: Build prober test fixture (`services/instancer/tests/fixtures/prober/`) and write `tests/integration/test_instances_isolation.py`.
  - Start Part 2 in `docs/handoff/instancer.md`.
- **M2 (Due 14 November):**
  - F3: Build single remote host agent mode and plugin capacity client.
  - F4: Author `deploy/compose/compose.challenge-host.yml`.
  - F5: Build `services/instancer/tools/pack_test.py` and run local benchmarks.
  - Open draft pull request with green CI checks.
- **M3 (Due 18 November):**
  - F3: Multi-host scheduler and 30s failover.
  - F4: Author `deploy/challenge-host/bootstrap.sh` and `README.md`.
  - F6: Update `docs/deploy/instancer.md`, `docs/deploy/instancer-traffic.md`, and `docs/security/instancer-threat-model.md`.
  - Final review and handoff submission before 19 November hard stop.
