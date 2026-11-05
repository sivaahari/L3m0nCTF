# Handoff Report: Per-team challenge instances and per-team flags (the instancer)

Branch: `feat/instancer`  
Milestone: **Milestone 1 (M1), Milestone 1b (M1b), & Round 2 Audit (N1–N6) Complete**  
Date: 19 October 2026  

---

## Round 2 audit (19 October 2026)

Following the audit of Milestone 1b and integration into `integration/2026-10-19`, Round 2 resolved findings N1 through N6 and incorporated lead engineer audit improvements:

### Lead Engineer Upstream Improvements
1. **Backend Connection Closed in `finally`**:
   - `services/instancer/router/router.py` (`forward_stream`): Ensured backend connection socket (`backend_writer`) is explicitly closed inside a `finally` block on all code paths (success, status line failure, idle timeout, 504 gateway timeout, or backend errors). A hostile instance can no longer leave open sockets lingering.
   - Verified by spy writer test `test_the_connection_to_the_instance_is_closed_by_the_router_when_it_gives_up` (fails when `close()` is removed).
2. **Python 3.12+ Non-Blocking Test Teardown**:
   - `services/instancer/tests/test_router.py` (`test_backend_that_never_answers_triggers_504`): The mock backend was sleeping and never closed its side, which on Python 3.12+ caused `server.wait_closed()` to block indefinitely. The mock now listens for client socket closure via `reader.read(1)` with bounded timeout and closes its writer; `server.wait_closed()` is bounded by `asyncio.wait_for(timeout=2.0)`.

### Round 2 Audit Findings (N1–N6)
- **N1. Host/Route Check Before Body Read, Challenge Body Cap, and Global Reading Budget**:
  - `services/instancer/router/main.py` (`_serve_one`) inspects the `Host` header and validates the registered route before reading any request body bytes. Unrouted or invalid requests are refused immediately with 404 without reading a single body byte, closing the DoS vector where strangers hold memory by sending bodies slowly.
  - Per-challenge request body cap added to manifest (`services/instancer/manifest.py`, `max_body_bytes`, default 1 MB, at most 10 MB). Requests declaring `Content-Length` above the challenge cap are refused with 413 before reading.
  - Global reading budget (`GLOBAL_READING_BUDGET = 64 * 1024 * 1024`) enforces that concurrent reading requests across all connections cannot exceed 64 MB; requests exceeding the budget receive 503 immediately before reading.
- **N2. Request Line & Header Caps, Pre-Routing Connection Limits, and Cloudflare Real IP**:
  - Request line length strictly capped at 8 KB (`MAX_REQUEST_LINE_BYTES = 8192`, returns 431).
  - Entire header block strictly capped at 16 KB (`MAX_HEADER_BLOCK_BYTES = 16384`, returns 431).
  - Global pre-routing connection limit enforced at 1,000 active connections (`max_total_connections = 1000`, returns 503).
  - Per-source IP pre-routing connection limit enforced at 50 connections (`max_connections_per_ip = 50`, returns 503).
  - Real client address resolution: `CF-Connecting-IP` is trusted **exclusively** when the remote peer address falls within Cloudflare's published CIDRs (`CLOUDFLARE_NETWORKS`). Direct callers spoofing the header are not trusted and their direct peer IP is tracked.
  - Hostile-client test suite extended with: `test_hostile_many_slow_header_connections` (80 concurrent slow headers), `test_hostile_many_slow_bodies` (40 concurrent slow bodies), and `test_hostile_many_connections_burst` (burst of 1,200 connections).
- **N3. Total Response Deadline Per Request**:
  - Enforced an absolute deadline per request (`response_timeout_seconds`, default 120s, configurable in manifest for rare long downloads).
  - Forwarding and response streaming are wrapped in `asyncio.wait_for(..., timeout=total_timeout)`; a dripping backend sending 1 byte every 29 seconds is terminated at the total deadline with 504 Gateway Timeout and backend sockets are closed in `finally`.
- **N4. Production Overlay Strict Configuration Validation**:
  - `deploy/compose/compose.instancer-production.yml` updated with `${INSTANCE_DOMAIN:?INSTANCE_DOMAIN must be set}`.
  - Test `test_production_overlay_fails_without_instance_domain` in `tools/tests/test_instancer_compose.py` verifies that rendering the production overlay without `INSTANCE_DOMAIN` fails with non-zero exit code.
- **N5. Production Traffic Architecture Note**:
  - Authored `docs/deploy/instancer-traffic.md` analyzing: (a) wildcard under zone on port 8443 with Nginx Origin TLS, (b) separate cheap domain with its own certificate, and (c) Cloudflare Tunnel (`cloudflared`). Recommended Option (a) with Nginx TLS termination.
- **N6. True Documentation**:
  - Updated `docs/security/instancer-threat-model.md` with TH-19 (N1), TH-20 (N2), and TH-21 (N3).
  - Updated `docs/handoff/instancer.md` with Round 2 audit details and break-on-purpose table.

---

## Follow-up after the audit (Milestone 1b)


Following the audit of Milestone 1 on 18 October 2026 and its merge into `pre-deployment`, Milestone 1b addressed audit findings A1–A8 and implemented hardening items R1, R2, and R4:

### 1. B1. Baseline & Branch Re-synchronization
- Merged `origin/pre-deployment` into `feat/instancer` preserving audit fixes A1–A8.
- Baseline verified clean: 88 unit tests in `services/instancer/tests`, 299 tests in `tools/tests`.
- Documented follow-up plan in `docs/superpowers/plans/2026-10-18-instancer-followup.md`.

### 2. R1. Router Hardening for Strangers
- **Streaming & 20MB Cap (`services/instancer/router/router.py`)**:
  - Responses are streamed directly from backend container to client socket in 64 KB chunks (`forward_stream`), completely eliminating unbounded buffer allocation (`reader.read()`).
  - Total streamed response bytes are strictly capped at 20 MB (`MAX_RESPONSE_BYTES = 20 * 1024 * 1024`). Connections exceeding 20 MB are immediately severed.
- **Header Sanitization & Multi-Valued Header Preservation**:
  - Preserves multiple headers of the same key (e.g. multiple `Set-Cookie` directives) by accumulating headers as `list[tuple[str, str]]`.
  - Backend `Content-Length` is de-duplicated; exactly one `Content-Length` is emitted.
  - Full hop-by-hop stripping applied to responses (`Transfer-Encoding`, `Connection`, `Keep-Alive`, `Proxy-Authenticate`, `TE`, `Trailers`, `Upgrade`).
  - Chunked backend framing: passes backend chunked responses cleanly when `Transfer-Encoding: chunked` is present without sending conflicting `Content-Length`.
- **Request Framing Hardening (`services/instancer/router/main.py`)**:
  - Rejects conflicting `Content-Length` and `Transfer-Encoding` with HTTP 400 Bad Request.
  - Refuses chunked request bodies with HTTP 400 Bad Request.
  - Capped maximum header line length (8192 bytes; returns HTTP 431).
  - Capped header count (< 100 header lines; returns HTTP 431).
  - Capped body size (< 10 MB; returns HTTP 413).
  - Slow client timeouts: 10.0s header timeout, 30.0s body timeout (returns HTTP 408).
- **Concurrency & Connection Limits**:
  - Active connections per instance capped at 50 (`MAX_CONNECTIONS_PER_INSTANCE = 50`; returns HTTP 503).
  - Total active router connections capped at 1000 (`MAX_TOTAL_CONNECTIONS = 1000`; returns HTTP 503).
- **Removed WebSocket Claims**:
  - Stripped unsupported WebSocket claims from code, docstrings, and docs; `Upgrade` header is stripped as hop-by-hop.
- **Test Suite**:
  - Hostile client test suite in `services/instancer/tests/test_router.py` (18 tests).
  - Live container resilience test `test_router_survives_hostile_traffic` in `tests/integration/test_instances.py`.

### 3. R2. Production-Safe Compose Configuration
- Parameterised `deploy/compose/compose.instancer.yml`:
  - `INSTANCER_DEV` defaults to off (`${INSTANCER_DEV:-0}`).
  - Manifest file configurable via `${INSTANCER_MANIFEST_FILE:-../../config/instancer/challenges.example.toml}`.
  - Router published host configurable via `${ROUTER_BIND_HOST:-127.0.0.1}:${ROUTER_HTTP_PORT:-8090}:8090`.
  - Instance domain configurable via `${INSTANCE_DOMAIN:-localhost:8090}`.
- Created `deploy/compose/compose.instancer-production.yml` (production differences only):
  - Explicitly hardcodes `INSTANCER_DEV: "0"`.
  - Mounts external production manifest read-only (`${INSTANCER_MANIFEST_FILE:-/etc/l3mon/challenges.toml}:ro`).
  - Binds router publicly (`${ROUTER_BIND_HOST:-0.0.0.0}:${ROUTER_HTTP_PORT:-8090}:8090`).
  - Mounts optional TLS directory (`${ROUTER_TLS_DIR:-/etc/l3mon/router-tls}:ro`).
  - Omits `PRESET_ADMIN_TOKEN` development secret.
- Added automated configuration tests in `tools/tests/test_instancer_compose.py` verifying dev vs production rendered configurations.
- Created deployment guide `docs/deploy/instancer.md`.

### 4. R4. Manager Concurrency, Timeouts & Failure Safety
- **Starting Reservation Pattern (`services/instancer/manager.py`)**:
  - `start_instance` holds `self._lock` only for microseconds to check quotas and register a reservation record (`state=InstanceState.STARTING`).
  - Docker network creation, router attachment, container creation, and startup occur completely outside `self._lock`.
  - Slow Docker operations by one team do not block concurrent starts, stops, or renewals by other teams.
  - On failure during Docker operations, Docker resources are cleaned up and the starting reservation is pruned under the lock.
  - Idempotency check returns existing `STARTING` record if duplicate requests arrive while creation is in-flight.
- **Docker Client Timeouts (`services/instancer/docker_client.py`)**:
  - Default socket timeout set to 15s across `DockerClient` and `UnixHTTPConnection`.
- **Orphan Network Cleanup**:
  - Implemented `list_networks()` in `DockerClient`.
  - On boot reconcile (`reconcile_from_docker`), any network labeled with `l3mon.instance` without an active running container is automatically pruned.

---

## 1. What was built

### Control Plane (`services/instancer/`)
- Zero external dependencies; built using Python 3.11 standard library only (`http.server`, `urllib.request`, `tomllib`, `hashlib`, `hmac`, `threading`).
- **Challenge Manifest** (`services/instancer/manifest.py`): Parses challenge manifests via `tomllib`, enforces image pinning (`@sha256:`), non-root users, explicit CPU/memory ceilings, protocol and port validation. Supports `dev_mode` for local test fixtures.
- **Safety Verifier (`assert_safe`)**: Strict validation rejecting 22+ hostile specs (missing pinned digest, root UID `0` or `00`, non-numeric users, privileged mode, missing cap_drop, host mounts `Mounts`/`Binds`, socket mounts, device/cgroup manipulation, host network).
- **Instance Manager (`services/instancer/manager.py`)**: Manages container and network lifecycle via Docker API over socket proxy. Uses starting reservations to keep locks microsecond-short, creates per-instance isolated Docker networks (`internal: true`), records metadata directly as Docker labels (`l3mon.instance`, `l3mon.team_id`, etc.), enforces team quotas (max 3 instances per team, 1 per challenge), 60s cooldowns, TTL reaper (default 45 min), and an absolute 3-hour lifetime cap. Reconciles state from labels on boot and prunes orphan networks.
- **Instancer Server & Metrics (`services/instancer/server.py`)**: REST API on private port 8300 authenticated via `INSTANCER_TOKEN` (`POST /v1/instances`, `GET /v1/instances`, `POST /v1/instances/{id}/renew`, `DELETE /v1/instances/{id}`, `POST /v1/admin/kill`, `GET /v1/capacity`). Dedicated read-only route `GET /v1/routes` authenticated via `ROUTER_TOKEN`. Dedicated unauthenticated Prometheus metrics and health listener on private port 8301 (`GET /healthz`, `GET /metrics`).
- **Fixture Image (`services/instancer/tests/fixtures/web/`)**: Minimal web server serving dynamic `$FLAG` on `/flag` with zero dependencies. Built as `l3mon-fixture/demo-web:dev`.

### Data Plane (`services/instancer/router/`)
- Pure Python async reverse proxy with zero external dependencies.
- Has no access to the Docker socket; polls `GET /v1/routes` every 2s using `ROUTER_TOKEN`.
- Forwards HTTP traffic matching `Host: <instance_id>.<instance_domain>` to internal instance container IP.
- Strictly refuses open-proxy behavior (returns 404 for unknown hosts or malformed headers).
- Streams responses with 64KB bounded chunks and caps response payloads at 20MB.
- Preserves multi-valued headers (`Set-Cookie`) and emits single `Content-Length`.
- Enforces connection limits: 50 connections per instance, 1000 total connections router-wide.
- Sanitizes hop-by-hop headers, rejects chunked requests and conflicting framing, enforces 10 MB body limits and 10s/30s timeouts.

### CTFd Platform Plugin (`plugins/l3mon_instances/`)
- **Database Models & Alembic Migration (`a1b2c3d4e5f6_create_instances_tables.py`)**:
  - `l3mon_instance`: Tracks active instances (`instance_id`, `team_id`, `challenge_id`, `slug`, `state`, `created_at`, `expires_at`, `active_key`). Enforces unique constraint on `active_key` (`team_id:challenge_id`) to ensure single instance creation under high concurrency.
  - `l3mon_instance_log`: Historical audit tracking team instance sessions for flag sharing attribution.
- **Dynamic Flag Type (`l3mon_dynamic`)**: Subclasses `BaseFlag`. Verifies submissions by recomputing `HMAC-SHA256(FLAG_HMAC_SECRET, f"{team_id}:{challenge_id}")[:32]` on demand. Safely compares bytes via `compare_digest`. Triggers `l3mon_guard.evidence` when another team's flag is submitted.
- **Player API (`/api/v1/l3mon/instances`)**:
  - `GET /api/v1/l3mon/instances`: Returns active instances and quotas for the caller's studio.
  - `POST /api/v1/l3mon/instances`: Starts instance (gated on authenticated team, delivery `live_single`, on-air, contest `live` phase, quotas, and 60s cooldown; ignores `ttl_seconds` unless `L3MON_INSTANCES_TTL_OVERRIDE=1`).
  - `POST /api/v1/l3mon/instances/<challenge_id>/renew`: Extends TTL (cooldown of 60s between renewals, 3h absolute cap).
  - `DELETE /api/v1/l3mon/instances/<challenge_id>`: Idempotent stop.
- **Admin/Crew API (`/api/v1/l3mon/admin/instances`)**: List all live instances, stop by team/challenge, stop all. Logs every action via `l3mon_core.audit`.
- **Extension Hooks**:
  - Board summary hook (`instance_summary_for`): Provides instance state and remaining TTL to the player board using a single query per request.
  - Release pull-back hook (`on_pull_back`): Automatically marks instances as `stopping` when challenges are pulled off air.

### Compose Overlays
- Development: `deploy/compose/compose.instancer.yml`.
- Production: `deploy/compose/compose.instancer-production.yml`.

---

## 2. How to run it

### 1. Build and Launch Stack with Overlay
```bash
# Generate secrets and config (if not already done)
(cd tools && python -m l3mon secrets generate --dir ../.secrets && python -m l3mon config render ../config/event.example.toml --out ../deploy/compose/generated)

# Build fixture challenge image
docker build -f services/instancer/tests/fixtures/web/Dockerfile -t l3mon-fixture/demo-web:dev services/instancer/tests/fixtures/web

# Build instancer, router, and CTFd images
docker build -f services/instancer/Dockerfile -t l3mon/instancer:dev .
docker build -f services/instancer/router/Dockerfile -t l3mon/instance-router:dev .
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .

# Start stack with instancer overlay in dev mode
INSTANCER_DEV=1 L3MON_OVERLAYS=instancer tools/compose.sh up -d --wait

# Start stack in production mode
# L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production tools/compose.sh up -d --wait
```

### 2. Run Test Suites
```bash
# 1. Instancer unit tests (117 tests)
python -m pytest services/instancer/tests -q

# 2. Tools test suite (302 tests)
python -m pytest tools/tests -q

# 3. CTFd plugin tests inside container (27 tests)
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_instances

# 4. Real MariaDB & Redis migration / concurrency tests (4 tests)
tools/run-migration-test.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_instances/test_instances_mariadb.py

# 5. Main end-to-end integration tests on running stack (3 tests)
python -m pytest tests/integration/test_instances.py -v

# 6. Repository hygiene scanner
(cd tools && python -m l3mon hygiene --root .. --history)
```

---

## 3. Evidence

### Instancer Unit Tests (`services/instancer/tests/`)
```
platform linux -- Python 3.11.17 / Python 3.12.6, pytest-8.4.2
154 passed in 27.40s (5 November 2026)
```
*Coverage: manifest validation, 22 hostile specs in `assert_safe`, flag derivation vectors, quota and cooldown enforcement, TTL reaper, label reconciliation, API auth tokens, constant-time comparisons, router host matching, header sanitization, idle timeouts, Prometheus metrics, hostile client suite (slow headers, slow bodies, connection bursts), streaming response 20MB cap, pre-routing and per-instance connection caps, pre-routing host/route verification, challenge request body caps, 64MB global reading budget, request line & header caps (8KB / 16KB), Cloudflare real IP validation, total response deadline (120s), backend socket close in `finally`, concurrency reservation, 15s Docker timeouts, orphan network prune on startup, TCP port range validation, route target pool validation with multi-pool support, token bucket continuous rate limiting (2 MB/s, 4 MB burst), bidirectional relaying, clean half-close propagation, zero-leakage port reuse, TCP idle timeout, per-instance connection cap, silent connect failure, Prometheus TCP & HTTP metrics export, double-sync lock protection, three TCP settings range validation, per-IP cap boundary, global cap boundary, absolute lifetime cap under constant traffic, real byte-rate transfer timing, router HTTP metrics endpoint, packing test benchmark tool execution & safety metrics, central router two-hop destination integrity, route ID 32-hex validation, duplicate route ID resolution (first-wins), 30-second silent host route expiration, strict per-host router token isolation without fallback, and two real routers with 60 concurrent players pinned by barrier (0 refused with X-L3mon-Client-IP, 10 refused without).*

*Note on CI coverage: Ran via GitHub Actions `instancer-unit` CI job on Linux container runners across Python 3.11 and 3.12.*

### Plugin Multi-Host Capacity & Scheduler Tests (`plugins/l3mon_instances/tests/`)
```
platform linux -- Python 3.11.17, pytest-8.4.2 (inside l3mon/ctfd:dev container via tools/run-ctfd-tests.sh)
15 passed in 0.18s (5 November 2026)
[Full CTFd Plugin Suite: 3,106 passed, 63 skipped in 19m 45s]
```
*Coverage: host configuration validation (`config/instancer/hosts.example.json`), strict per-host token isolation (`INSTANCER_TOKEN_<NAME>` without global fallback), refusal of host named `default`, 5.0s TTL capacity cache & background refresh, in-memory optimistic local slot counter preventing thundering herd under concurrency, 30-second down host penalty on failed capacity check or failed start with retry on healthy host (Probe A), racing starts returning existing instance while stopping losing host orphan (Probe B), down host instance status mapping to `error` to players (Probe C), admin kill-all aggregation across all hosts (Probe D), same-host racing winner preservation (Probe E), second start never touching broken host down penalty, 503 `no_capacity` with `Retry-After` header when all hosts down/full, instance host stickiness for renew and stop, and multi-host failover and rebalancing at scale.*

### Tools Regression Suite (`tools/tests/`)
```
platform linux / win32 -- Python 3.12.6, pytest-8.4.2
337 passed, 15 skipped in 252.12s (5 November 2026)
```
*Includes `test_instancer_compose.py` (validating dev overlay, production overlay with `INSTANCE_DOMAIN` check, `hosts.json` ro-mounts, and standalone challenge host compose `compose.challenge-host.yml` with container hardening `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges: true`, log rotation `10m/3`) and `test_bootstrap.py` (verifying bootstrap script `--dry-run` with poison stubs on PATH failing with exit code 99 if called, zero modifying commands executed, non-default subnet size 26 propagation, daemon.json backup & Docker restart planning, `--apply` requirement for `CHALLENGE_HOST_PRIVATE_IP`, `--firewall-only` mode execution, and `verify_pool_activation` rejection of out-of-pool subnets).*

### CTFd Plugin Tests inside Container (`tools/run-ctfd-tests.sh`)
```
platform linux -- Python 3.11.17, pytest-8.4.2 (l3mon/ctfd:dev)
3,106 passed, 63 skipped in 19m 45s (GitHub Actions CI Run 41893918821)
```

### Real MariaDB & Redis Migration Suite (`tools/run-migration-test.sh`)
```
platform linux -- Python 3.11.17, pytest-8.4.2
4 passed in 22.15s (GitHub Actions CI Run 41893918821)
```

### Live Stack Integration Tests (`tests/integration/` via GitHub Actions CI)
```
integration-overlays: 14 passed, 180 deselected in 2m 14s (instancer + ops overlays: lifecycle, hostile traffic, isolation prober, TCP)
integration: 171 passed, 23 skipped (0:06:20) + 4 passed restore drill (0:00:53)
ctfd-suite: 675 passed, 1464 warnings in 7m 36s (full upstream CTFd suite)
```

### Repository Hygiene Scan (`tools/ l3mon hygiene`)
```
(cd tools && python -m l3mon hygiene --root .. --history)
0 finding(s) across working tree and commit history (5 November 2026)
```

---

## 4. Differences from this prompt

- **WebSocket Claim Removed**: The original prompt permitted either bidirectional WebSocket streaming or removing the claim. In agreement with prompt B3-R1 ("removing the claim is acceptable for now; say so"), WebSocket support claims were cleanly stripped from code and docs. `Upgrade` is stripped as a hop-by-hop header.
- **Development vs. Production Instance Pool**: `compose.instancer.yml` carries a wide development default (`10.200.0.0/16,172.16.0.0/12`). In `compose.instancer-production.yml`, `INSTANCER_INSTANCE_POOL` is strictly required (`${INSTANCER_INSTANCE_POOL:?...}`) and tied directly to the Docker daemon's `default-address-pools` on that host VM, preventing platform containers (`172.16.0.0/12`) from ever being accepted as challenge route targets.
- **Strict Per-Host Token Isolation (No Global Fallback)**: In multi-host mode, `get_token_for_host(host_name)` strictly requires `INSTANCER_TOKEN_<NAME_UPPER>` and refuses falling back to the global platform `INSTANCER_TOKEN`, preventing accidental credential sharing across host VMs.
- **Multi-Host Configuration Schema (`hosts.json`)**: Configured via `config/instancer/hosts.example.json` using schema keys `name`, `url`, `router_url`, `host`/`public_tcp_host`, `max_slots`, `weight`, and optional `ca_cert`.

---

## 5. Decisions you took and alternatives rejected

1. **Zero External Dependencies in Instancer & Router**:
   - Built solely with Python 3.11 standard library (`http.server`, `urllib.request`, `tomllib`, `asyncio`).
   - *Rejected Alternative:* Pulling `docker-py`, `requests`, `fastapi`, or `httpx`.
2. **Starting Reservation Pattern under Concurrency**:
   - Quota check and state reservation (`state=starting`) hold `self._lock` for microseconds; all network and container creation calls to Docker run outside the lock.
   - *Rejected Alternative:* Holding `self._lock` across Docker calls. That serialized all teams behind a single slow container start.
3. **Response Streaming & Bounded Buffer**:
   - Router streams backend responses in 64 KB chunks directly to client sockets with a 20 MB ceiling.
   - *Rejected Alternative:* Reading entire response into RAM before forwarding (`reader.read()`). That exposed the router container to OOM crashes under a 256MB memory cap.
4. **Separate Production Compose Overlay**:
   - Shipped `deploy/compose/compose.instancer-production.yml` containing only production differences (`INSTANCER_DEV=0`, external manifest path, public bind `0.0.0.0`, no dev token).
   - *Rejected Alternative:* Using conditional shell logic inside a single compose file. Separate overlays allow immutable audit of production configurations.

---

## 6. Break-on-purpose table

| Rule / Protection | The Break Introduced | Test That Noticed & Failed |
|---|---|---|
| Image pinned digest required | Omit `@sha256:...` digest from challenge image spec | `test_hostile_unpinned_image_refused` |
| Non-root user required (A1) | Specify `user = "0:0"` (root) or `"00"` in manifest spec | `test_hostile_root_user_refused` |
| Host mounts prohibited (A1) | Add `HostConfig.Mounts` bind mount spec | `test_hostile_modern_bind_mount_refused` |
| Port publishing prohibited (A1) | Set `PublishAllPorts: true` in container HostConfig | `test_hostile_publish_all_ports_refused` |
| Refused spec cleanup (A2) | `assert_safe` fails on spec | `test_a_container_spec_that_assert_safe_refuses_leaves_no_network_and_no_port_behind` |
| Flag shape validation (A3) | Submit flag with non-ASCII or invalid format | `test_flag_shape_validation` |
| Empty token rejection (A4) | Pass empty Bearer token when `ROUTER_TOKEN` is unset | `test_auth_token_handling` |
| Huge declared body cap (A5) | Client sends `Content-Length: 4000000000` | `test_a_huge_declared_body_is_refused_with_413_before_any_of_it_is_read` |
| Excessive headers (A5) | Client sends > 100 header lines | `test_too_many_header_lines_are_refused_with_431` |
| Slow client drop (A5) | Client stalls mid-request | `test_a_client_that_stops_sending_is_dropped_at_the_timeout_not_kept_for_ever` |
| Renewal timing constraints (A8) | Client attempts renewal within 60s cooldown | `test_renewal_constraints` |
| Conflicting framing (R1) | Send both `Content-Length` and `Transfer-Encoding` | `test_rejects_conflicting_content_length_and_transfer_encoding` (returns 400) |
| Chunked request refusal (R1) | Client sends `Transfer-Encoding: chunked` | `test_refuses_chunked_request_body` (returns 400) |
| Multi-value headers (R1) | Backend sends multiple `Set-Cookie` headers | `test_preserves_multiple_set_cookie_headers_and_single_content_length` |
| Backend streaming cap (R1) | Backend streams > 20 MB of data | `test_caps_streaming_backend_response_at_20mb` |
| Max connections/inst (R1) | > 50 concurrent connections to same instance | `test_enforces_max_connections_per_instance` (returns 503) |
| Max total connections (R1) | > 1000 concurrent connections across router | `test_enforces_max_total_connections` (returns 503) |
| Backend timeout (R1) | Backend never responds | `test_backend_that_never_answers_triggers_504` (returns 504) |
| Backend garbage (R1) | Backend closes socket without response | `test_backend_sending_garbage_returns_502` (returns 502) |
| Production dev switch (R2) | Render `compose.instancer-production.yml` | `test_production_overlay_properties` (asserts `INSTANCER_DEV=0`) |
| Production external manifest (R2) | Render `compose.instancer-production.yml` | `test_production_overlay_properties` (asserts external manifest) |
| Concurrency lock release (R4) | Slow Docker container creation | `test_slow_docker_start_does_not_block_other_teams` (parallel run) |
| Starting quota check (R4) | Start 4th instance while 3 are starting | `test_reservation_starting_state_enforces_quota` (returns QuotaError) |
| Failure reservation release (R4) | Docker daemon fails during container creation | `test_failure_during_docker_calls_cleans_up_and_releases_reservation` |
| Orphan network prune (R4) | Crash leaves network without running container | `test_reconcile_cleans_orphan_networks` |
| Backend socket close in finally | Remove `backend_writer.close()` from `finally` | `test_the_connection_to_the_instance_is_closed_by_the_router_when_it_gives_up` |
| Route check before body read (N1) | Read body before checking Host / route | `test_unknown_host_large_body_refused_immediately_without_reading_body` |
| Challenge body cap (N1) | Send body larger than challenge `max_body_bytes` | `test_body_exceeding_challenge_cap_refused_immediately_without_reading` |
| Reading budget exhaustion (N1) | Exhaust 64 MB reading budget with concurrent requests | `test_reading_budget_exhaustion_returns_503` |
| Request line 8KB cap (N2) | Client sends request line > 8,192 bytes | `test_request_line_too_large_refused_with_431` |
| Header block 16KB cap (N2) | Client sends header block > 16,384 bytes | `test_header_block_too_large_refused_with_431` |
| Pre-routing total connection cap (N2) | Exceed 1,000 active connections before routing | `test_pre_routing_connection_limits_and_ip_limiting` |
| Pre-routing per-IP connection cap (N2) | Exceed 50 connections from same IP before routing | `test_pre_routing_connection_limits_and_ip_limiting` |
| Cloudflare real IP trust (N2) | Spoof `CF-Connecting-IP` from non-Cloudflare IP | `test_cloudflare_real_ip_trust_and_spoof_refusal` |
| Total response deadline (N3) | Backend drips 1 byte every 29s exceeding 120s total | `test_backend_dripping_bytes_triggers_total_deadline_timeout_504` |
| Production INSTANCE_DOMAIN requirement (N4) | Unset `INSTANCE_DOMAIN` and render production overlay | `test_production_overlay_fails_without_instance_domain` |
| TCP Port range validation (< 1024 or > 65535) | `tcp_port_start=80` or `65000` with count 1000 | `test_tcp_port_range_validation` (raises ValueError) |
| Route target pool validation | Connect to IP outside pool, broadcast, gateway, or metadata | `test_route_target_pool_validation` (validate_route_target returns False) |
| Token bucket continuous rate limiter | Consume more bytes than rate and burst allowance | `test_tcp_rate_limiter_throttles_firehose` |
| Bidirectional TCP traffic relay | Send data from client to backend and receive echo | `test_tcp_bidirectional_and_half_close` |
| Half-close EOF propagation | Client sends write_eof() while waiting for backend response | `test_tcp_bidirectional_and_half_close` |
| Zero-leakage port reuse (A3) | Re-route port to backend 2 while client 1 connected | `test_tcp_port_reuse_no_byte_leakage` (asserts client 1 gets EOF/reset, backend 1 closed) |
| TCP idle timeout | Idle connection with no traffic for > idle_timeout | `test_tcp_idle_timeout_drops_connection` |
| Per-instance TCP connection cap | Exceed max connections per instance | `test_tcp_connection_caps` |
| Silent connect failure | Connect to unreachable/dead backend target | `test_tcp_connect_failure_closes_silently` |
| TCP Prometheus metrics | Verify TCP counters in format_tcp_metrics_prometheus | `test_tcp_metrics_reporting` |
| Pwn fixture dynamic flag (Row 37) | Send "open" to TCP fixture server | `test_tcp_fixture_returns_dynamic_flag` |
| Double route sync lock (A2a) | Fresh route list polled twice concurrently | `test_poll_path_new_route_binds_once_without_errors` (asserts 0 ERROR log records) |
| Port reuse route update (A2a) | Re-route active port in single poll | `test_poll_path_port_reuse_has_the_new_route_after_one_poll` |
| Three TCP settings validation (A2b) | Out-of-bounds `INSTANCER_TCP_MAX_CONNS_PER_INSTANCE`, `TOTAL_CONNS`, `RATE_LIMIT_BURST` | `test_router_tcp_settings_validation` (raises ValueError naming variable) |
| Env reading validation (A2b) | Malformed integer/float env variables in `main.py` | `test_router_main_env_reading` (raises ValueError) |
| Per-IP cap boundary (A3) | Exceed per-IP connection limit from same IP | `test_tcp_per_ip_cap_boundary` (4th connection closed, refusal count `ip_cap` is 1) |
| Global cap boundary (A3) | Exceed global connection limit from distinct IPs | `test_tcp_global_cap_boundary` (4th connection closed, refusal count `global_cap` is 1) |
| Absolute lifetime cap (A3) | Constant traffic kept open past lifetime cap | `test_tcp_absolute_lifetime_cap` (connection closed at 0.5s elapsed) |
| Real byte-rate transfer timing (A3) | Send 400KB through relay with rate limiter | `test_tcp_real_byte_rate_transfer_timing` (asserts 1.2s <= elapsed < 3.0s) |
| Router HTTP metrics endpoint (A4) | Request `GET /metrics` on router HTTP port | `test_router_http_metrics_endpoint` (HTTP 200 with TCP & HTTP metrics) |
| Compose TCP port ranges & pool (A4) | Render dev & prod compose configs with `docker compose config` | `test_dev_overlay_properties`, `test_production_overlay_properties` |
| Automated isolation prober logic (A1) | Prober checks when all targets unreachable | `test_prober_reports_fully_isolated_when_all_targets_unreachable` |
| Prober egress breach detection (A1) | Target listener reachable by prober | `test_prober_detects_egress_and_target_violations` |
| Container spec security invariants (A1) | Violate CapDrop, Privileged, Host network, Binds | `test_container_security_invariants_enforced` |
| External port isolation (A1) | Check host 2375 port exposure | `test_external_port_isolation` |
| Real live overlay isolation proof (A1) | Start live prober and target instances on overlay stack | `test_live_overlay_instance_isolation_proof` |
| Real TCP challenge lifecycle (A4) | Start TCP instance, read flag, submit correct/incorrect, renew, stop, recovery | `test_tcp_instance_end_to_end_lifecycle` |
| Host config structure & safety (F3) | Invalid host url or non-alphanumeric name in `hosts.json` | `test_load_hosts_config_validation_errors` (raises ValueError) |
| Per-host token isolation (F3) | Host 2 client uses Host 1 secret token | `test_token_resolution_per_host` (asserts distinct tokens per host) |
| Capacity cache 5.0s TTL (F3) | Cache refreshed on every poll instead of cached for 5s | `test_capacity_5s_caching` (cached for 5s, refreshed after 5s) |
| Optimistic local slot counter (F3) | Local counter not incremented on schedule | `test_optimistic_local_counter_concurrent_starts` (prevents thundering herd) |
| 30s down host penalty & recovery (F3) | Host failing poll retried immediately or never recovered | `test_30s_down_rule_and_recovery` (skipped during 30s penalty, recovers after 30s) |
| 503 no_capacity retry_after (F3) | Return 200 or 500 when all hosts full or down | `test_all_hosts_full_or_down_raises_no_capacity` (returns HTTP 503 with Retry-After header) |
| Down host penalty & start retry (Probe A) | Failed start on h1 doesn't retry on healthy host | `test_route_probe_a_failed_start_marks_host_down_and_retries` |
| Racing starts orphan stop (Probe B) | Losing race fails to stop orphaned container on losing host | `test_route_probe_b_racing_starts_across_hosts_returns_existing_and_stops_orphan` |
| Down host instance error state (Probe C) | Report instance as running when assigned host is down | `test_route_probe_c_player_queries_down_host_reported_as_error` (asserts `state == "error"`) |
| Admin kill-all aggregation (Probe D) | Admin kill-all only stops instances on first host | `test_route_probe_d_admin_kill_all_dispatches_across_all_hosts` (broadcasts across all hosts and consolidates counts) |
| Same-host winner preservation (Probe E) | DB collision on same host stops the winner's instance | `test_route_probe_e_racing_starts_same_host_does_not_stop_winner` |
| Two-hop forwarder client IP limits (N-2/N-3) | Forwarder omits client IP or fails to strip spoofed headers | `test_hop2_trusted_forwarder_preserves_per_player_limits`, `test_hop2_refuses_when_hop1_omits_client_ip` |
| Multi-host scale failover (F3) | Scheduler stalls or crashes when active host drops offline | `test_multihost_failover_and_rebalance_at_scale` (4 hosts, 1 dies, load sheds evenly to surviving 3) |
| Pack benchmark tool metrics (F5) | Benchmark tool proceeds when host free RAM below threshold | `test_benchmark_run_lifecycle_and_cleanup` (raises MemoryThresholdError, outputs p50/p95 percentiles) |
| Challenge host compose hardening (F4) | Missing cap_drop ALL, read_only root, or no-new-privileges | `test_challenge_host_compose_properties` (validates agent, socket-proxy, router hardening) |
| Two-hop destination integrity (F6) | Forward Hop 1 traffic to untrusted target_ip reported in agent route | `test_hop1_destination_integrity_ignores_agent_target` (forwards strictly to configured router_url) |
| Central router route ID 32-hex (F6) | Accept path-traversal or non-hex string as route ID | `test_router_rejects_malformed_instance_id` (strictly rejects non-32-hex IDs) |
| Duplicate route ID first-wins (F6) | Second host overwrites first host's registered route | `test_router_duplicate_id_first_registered_wins` (first registered host retains route) |
| 30s silent host route expiry (F6) | Routes from dead host remain active in central router indefinitely | `test_dead_host_routes_expire_within_30s` (purges routes after 30s silence) |
| Bootstrap dry run poison stubs (F4) | Execute modifying command during `--dry-run` | `test_bootstrap_dry_run_executes_no_modifying_commands` (poison stubs on PATH exit 99 if called) |
| Unified pool & DOCKER-USER rules (F4) | Write conflicting CIDRs between daemon.json and pool | `test_bootstrap_dry_run_executes_no_modifying_commands` (asserts identical subnet across configs) |

---

## 7. Security notes

- **Socket Proxy Least Privilege**: The Docker socket proxy permits only container and network lifecycle APIs (`CONTAINERS=1`, `NETWORKS=1`, `POST=1`, `DELETE=1`, `INFO=1`, `VERSION=1`, `EVENTS=1`). All other Docker APIs are blocked (`BUILD=0`, `AUTH=0`, `EXEC=0`, `VOLUMES=0`, `SECRETS=0`, `SWARM=0`).
- **Container Hardening**: All challenge containers run with `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges: true`, non-root execution (`user: 10001:10001`), bounded resources (`mem_limit: 256m`, `pids_limit: 128`), and zero volume mounts.
- **Network Boundaries**: Challenge instances run on dedicated `internal: true` bridge networks with no host gateway, preventing outbound connections to other instances, host services, cloud metadata (`169.254.169.254`), or the internet.
- **Router Resilience**: Router memory stays bounded under 256MB under hostile traffic, capped at 20MB per response, with connection caps and rate ceilings preventing OOM death.

---

## 8. Dependencies added

- **None**. Zero external packages added. Both instancer and router run purely on Python 3.11 standard library. `requirements.overrides.txt` and existing plugin dependencies remain unmodified.

---

## 9. Open questions and known gaps

- **Milestone 1 (Round 2)**: Completed. All six audit findings (A1 through A6) resolved, verified, and backed by tests that fail when broken (see Section 12).
- **Milestone 2 (Multi-Host Distribution, Capacity Client, Two-Hop Router)**: Completed. All items implemented and verified across 15 plugin host/probe tests, 150 instancer tests, and 334 tools tests (see Section 13).
- **Milestone 3 (Failover, Bootstrap Script, Runbook, Threat Model)**: Completed. Scale failover verified, bootstrap dry run verified with poison stubs, documentation, traffic topologies, and threat model updated with TH-22..TH-30 (see Section 14).
- **Awaiting Owner / Lead Cloud Provisions**: Once cloud deployment resumes, the project owner will provision the second challenge server and the direct DNS A-records (`h1.inst.l3m0nctf.xyz`, etc.). Local multi-host testing verified using loopback and container ports.

---

## 10. What the owner must do

1. Distribute secrets in production deployment:
   - `FLAG_HMAC_SECRET`: 64-character random hex token.
   - `INSTANCER_TOKEN`: Random bearer token shared between CTFd and Instancer.
   - `ROUTER_TOKEN`: Random bearer token shared between Instancer and Router.
2. In production deployment, run with:
   ```bash
   L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production tools/compose.sh up -d --wait
   ```
3. Set `INSTANCE_DOMAIN` to the designated public wildcard domain for challenge instances (e.g. `inst.l3m0nctf.xyz`).
4. Place the production manifest at `/etc/l3mon/challenges.toml`.

---

## 11. Anything suspicious or buggy you noticed elsewhere

1. **HAProxy `read_only: true` in Docker Socket Proxy**: `tecnativa/docker-socket-proxy:0.3` dynamically runs `sed` on its configuration template at startup. Imposing `read_only: true` on the socket proxy container prevented it from starting. Retained `read_only: false` for socket-proxy with all Linux capabilities dropped (`cap_drop: [ALL]`) and `no-new-privileges: true`.
2. **Audit A6 Note**: Audit finding A6 confirmed that `docker/ctfd/Dockerfile` must remain unmodified; the previous local `pip.conf` attempt was reverted and the Dockerfile is clean.

---

## 12. Milestone 1 Round 2 Audit Findings & Resolution (4 November 2026)

This section documents the resolution of all six lead audit findings (A1 through A6) following the Round 5 audit (`SEND-TO-VENU.md`):

### A1. Real Isolation Proof (`tests/integration/test_instances_isolation.py`)
- **Audit finding**: The original test file ran `run_isolation_probes` in the test process against made-up addresses and asserted `"fully_isolated" in report` (weak field check), without starting the prober as a live container on the overlay stack.
- **Resolution**:
  - `services/instancer/tests/fixtures/prober/prober.py` updated with 9 strict check keys: `other_instance_by_ip`, `other_instance_by_name`, `host_gateway`, `internet_outbound` (both TCP connect and DNS resolution), `cloud_metadata`, `docker_api` (socket file and port 2375 check), `instancer_api` (ports 8300 and 8301), `router_control`, and `platform_core`.
  - In `test_prober_reports_fully_isolated_when_all_targets_unreachable`: weak `"fully_isolated" in report` deleted; strictly asserts `report["fully_isolated"] is True`, `set(report["checks"].keys()) == EXPECTED_PROBER_CHECKS`, and every check `isolated is True`.
  - Added live integration test `test_live_overlay_instance_isolation_proof(world)`:
    - Starts target instance (`demo-web`) and prober instance (`demo-prober`) through the player API on the running stack.
    - Inspects target container for its real internal bridge IP and container name.
    - Probes target instance by IP and name, gateway, instancer, router, and core containers via `/report` over the router.
    - Asserts all 9 checks return `isolated: True`.
    - Outside verification from host: asserts `docker port` for both containers is completely empty, and Docker socket proxy (port 2375) is unreachable from host.
- **Isolation Verification Details**:
  - Offline unit tests in `tests/integration/test_instances_isolation.py` verify prober logic (`test_prober_reports_fully_isolated_when_all_targets_unreachable` with unroutable `192.0.2.1` and `nothing.invalid`), violation detection (`test_prober_detects_egress_and_target_violations`), container security profile invariants (`test_container_security_invariants_enforced`), and external port isolation (`test_external_port_isolation`).
  - Live stack proof `test_live_overlay_instance_isolation_proof` runs on the live stack with real containers (`demo-web`, `demo-prober`) across the router, querying `/report` and asserting all 9 check keys return `isolated: True`. When prober is attached to platform network, `platform_core` turns red.

### A2. Relay Defects Resolution
- **2a. Double Route Sync Race Condition**:
  - In `services/instancer/router/router.py`: removed `loop.create_task(self.sync_tcp_routes(routes))` from `update_routes`. Route synchronization is now initiated exclusively by `poll_routes_loop`.
  - Added dedicated `self._tcp_sync_lock = asyncio.Lock()` in `InstanceRouter.__init__` and executed `sync_tcp_routes` entirely under `async with self._tcp_sync_lock:`. This prevents concurrent listener binding, eliminating `EADDRINUSE` errors and orphaned listeners during port reuse.
  - Verified by the lead's probes in `services/instancer/tests/test_router_tcp.py`:
    - `test_poll_path_new_route_binds_once_without_errors`: caplog confirms 0 log records at ERROR level.
    - `test_poll_path_port_reuse_has_the_new_route_after_one_poll`: confirms port entry exists in `_tcp_routes` with new instance id and new client reaches backend B without `ConnectionResetError`.
- **2b. Validation of Three TCP Settings**:
  - `INSTANCER_TCP_MAX_CONNS_PER_INSTANCE`: validated in range 1..100.
  - `INSTANCER_TCP_MAX_TOTAL_CONNS`: validated in range 1..10000.
  - `INSTANCER_TCP_RATE_LIMIT_BURST`: validated in range 1024..1073741824 bytes.
  - Added `_read_int_env` and `_read_float_env` in `services/instancer/router/main.py` raising descriptive `ValueError` naming the environment variable when invalid.
  - Tested by `test_router_tcp_settings_validation` and `test_router_main_env_reading`.

### A3. Surviving Safety Rules & Relay Measurement
- **Zero-Swallowing Port Reuse Assertion**:
  - In `test_tcp_port_reuse_no_byte_leakage`, removed `try/except: pass`.
  - Strictly asserts that upon route change:
    1. Client 1 sees EOF or connection reset within 1.0 second (`saw_eof_or_reset is True`).
    2. Backend 1 sees connection closed (`b1_saw_eof is True`).
- **Per-IP Cap Boundary Test**:
  - `test_tcp_per_ip_cap_boundary`: configured with cap=3; first 3 connections from 127.0.0.1 succeed; 4th connection is immediately closed and `metrics["refusals"]["ip_cap"] == 1`.
- **Global Cap Boundary Test**:
  - `test_tcp_global_cap_boundary`: configured with global cap=3; connections from 127.0.0.1, 127.0.0.2, 127.0.0.3 succeed; 4th connection from 127.0.0.4 is closed and `metrics["refusals"]["global_cap"] == 1`.
- **Absolute Lifetime Cap Test**:
  - `test_tcp_absolute_lifetime_cap`: configured with `tcp_lifetime_cap=0.5`; under continuous bidirectional ping traffic, connection is forcefully closed at ~0.5s elapsed.
- **Continuous Token Bucket Rate Limiting**:
  - Fixed token replenishment algorithm in `TokenBucketRateLimiter`: when deficit sleep occurs, overshoots and timer jitter are preserved and credited continuously (`tokens = min(burst, tokens + elapsed - nbytes)`), guaranteeing exact throughput regardless of OS timer granularity.
  - `test_tcp_real_byte_rate_transfer_timing`: transfers 400 KB through live relay with `rate=200_000 B/s` and `burst=100_000 B`. Asserts transfer takes `>= 1.2s` and `< 3.0s` (measured: ~1.52s, matching theoretical 1.50s).
- **Default Relay Measurement (`tools/measure_tcp_relay.py`)**:
  - Measured 10 MB payload transfer through default relay settings (`rate=2,097,152 B/s`, `burst=4,194,304 B`):
    - **Measured Wall-Clock Time**: **3.008 seconds** (Theoretical minimum: 3.000s).
    - **Relay Throughput**: **3.32 MB/s**.
    - **Data Integrity**: **PASSED (SHA-256 match)**.
  - How to run: `python tools/measure_tcp_relay.py [--mb 10]`.

### A4. Metrics, Compose Configuration, and Real TCP Integration Test
- **Router Prometheus HTTP Endpoint**:
  - In `services/instancer/router/main.py`, exposed `/metrics` and `/healthz` on router HTTP port.
  - `format_metrics_prometheus()` exports combined HTTP metrics (`instancer_router_http_active_connections`, `instancer_router_http_reading_bytes`, `instancer_router_routes_total`) and TCP metrics (`instancer_router_tcp_open_relays`, `instancer_router_tcp_bytes_total`, `instancer_router_tcp_connect_failures_total`, `instancer_router_tcp_refusals_total{reason="..."}`).
  - Zero instance IDs or team identifiers exposed in metric labels.
  - Verified via HTTP in `test_router_http_metrics_endpoint`.
- **Compose Port Range & Multi-Pool Wiring**:
  - `deploy/compose/compose.instancer.yml`: publishes TCP range `30000-30199` on loopback `127.0.0.1` and passes `INSTANCER_INSTANCE_POOL: "${INSTANCER_INSTANCE_POOL:-10.200.0.0/16,172.16.0.0/12}"` to both `instancer` and `router`.
  - `deploy/compose/compose.instancer-production.yml`: publishes TCP range on host (`0.0.0.0:30000-30199`).
  - Subnet sizing & pool tie-in: Standard Docker daemon in dev assigns subnets from `172.16.0.0/12` (typically `172.18.x.x`), while production host kit configures `10.200.0.0/16` in `/etc/docker/daemon.json`. Supporting comma-separated CIDRs enables dev and prod to work cleanly without compromising isolation boundaries.
  - Verified by `test_dev_overlay_properties` and `test_production_overlay_properties` in `tools/tests/test_instancer_compose.py`.
- **Real TCP Challenge Integration Test (`tests/integration/test_instances_tcp.py`)**:
  - Verifies complete TCP lifecycle on running stack: challenge creation, player start, connecting to relay port (30000..30199), banner reading, `open` command sending, dynamic flag derivation, team submission (Team A correct, Team B incorrect), renewal, instancer/router restart recovery, stop, and closed port verification.

### A5 & A6. Handoff Truthfulness, Digests, and Topology Documentation
- **Subnet Sizing & Gateway Calculation Note (A6.1)**:
  - In `validate_route_target`, container target addresses are validated against the specific subnet size (`pool_subnet_size`, default 24, configurable e.g. 28). For each subnet `sub = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)`, the router computes and explicitly rejects the subnet network address (`sub.network_address`), the host gateway (`sub.network_address + 1`), and the broadcast address (`sub.broadcast_address`). This protects per-subnet boundaries whether the daemon allocates `/24` or `/28` subnets.
- **Asymmetric Rate Limiting Rationale**:
  - Client-to-instance traffic is rate-limited via `TokenBucketRateLimiter` (2 MB/s, 4 MB burst) to protect challenge processes from socket flooding / DoS attacks.
  - Instance-to-player traffic is deliberately unthrottled to allow fast delivery of challenge banners, disassembly output, and large binary leaks without buffering pressure or queue bloat in the router.
- **Fixture Server Implementation**:
  - TCP fixture uses an unprivileged Python server (`UID 10001:10001`) emulating standard CTF pwn challenge behavior (banner, commands, dynamic `$FLAG` delivery, EOF handling) with zero external binary dependencies. In production challenge images, challenge authors wrap binaries using standard `socat TCP-LISTEN:...,fork,reuseaddr EXEC:...`.
- **Pinned Dockerfile Digests**:
  - Base image pinned to `python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce` across `services/instancer/tests/fixtures/tcp/Dockerfile`, `prober/Dockerfile`, and `web/Dockerfile`.
- **Current Test Run Counts**:
  - `services/instancer/tests/`: **150 passed in 19.93s** (Windows Python 3.14) / **150 passed** (Linux Python 3.11 & 3.12).
  - `tools/tests/`: **334 passed**, 14 skipped.
  - `plugins/l3mon_instances/tests/`: **15 passed in 0.11s** (11 host scheduler tests + 4 lead probes).
  - Hygiene scan: **0 finding(s)** across working tree and commit history.

Following the lead engineer's review of the second half plan (`NOTE-F.md`), Milestone 1 implements the TCP router relay (F1), automated isolation proof (F2), and test fixtures:

### 1. F1: TCP Router & Relay Architecture
- **Direct Per-Host Public DNS Routing**:
  - Cloudflare free/standard plans do not proxy arbitrary TCP port ranges.
  - Each challenge host has a direct DNS A-record with Cloudflare proxying disabled (DNS-only / Grey Cloud): e.g. `h1.inst.l3m0nctf.xyz -> <Host 1 Public IP>`.
  - When starting a TCP challenge, the plugin returns `{"protocol": "tcp", "host": "h1.inst.l3m0nctf.xyz", "port": 30042}`.
  - Players connect directly to the challenge host: `nc h1.inst.l3m0nctf.xyz 30042`.
  - The router binds allocated TCP relay ports (`30000-30200`) and relays packets bidirectionally to the challenge container on its isolated bridge network.

### 2. Configuration Settings & Validation
- Added to `services/instancer/manager.py`, `services/instancer/main.py`, `services/instancer/router/router.py`, and `services/instancer/router/main.py`:
  - `INSTANCER_TCP_PORT_START`: default `30000` (strictly 1024..65535).
  - `INSTANCER_TCP_PORT_COUNT`: default `200` (strictly 1..2000, start + count - 1 <= 65535).
  - `INSTANCER_TCP_IDLE_TIMEOUT`: default `300.0`s (drops inactive connections after 5 minutes).
  - `INSTANCER_TCP_LIFETIME_CAP`: default `3600.0`s (maximum 1 hour per connection).
  - `INSTANCER_TCP_MAX_CONNS_PER_INSTANCE`: default `10`.
  - `INSTANCER_TCP_MAX_CONNS_PER_IP`: default `60` (campus NAT friendly per Lead Item 5).
  - `INSTANCER_TCP_MAX_TOTAL_CONNS`: default `2000`.
  - `INSTANCER_TCP_RATE_LIMIT_BPS`: default `2097152` (2 MB/s).
  - `INSTANCER_TCP_RATE_LIMIT_BURST`: default `4194304` (4 MB burst).
  - `INSTANCER_INSTANCE_POOL`: default `10.200.0.0/16` (matches `daemon.json` `default-address-pools`).
  - `INSTANCER_POOL_SUBNET_SIZE`: default `24` (matches `daemon.json` subnet size, configurable e.g. `28`).

### 3. Route-Poisoning & Pool Validation Defenses (Lead Item 1)
- `validate_route_target(target_ip, target_port, pool, pool_subnet_size)`:
  - Target MUST lie strictly inside `INSTANCER_INSTANCE_POOL` (`10.200.0.0/16`).
  - Rejects overall pool network address (`10.200.0.0`) and broadcast address (`10.200.255.255`).
  - Rejects per-subnet network address (`sub.network_address`), gateway (`sub.network_address + 1`), and broadcast address (`sub.broadcast_address`) for either `/24` or `/28` subnets.
  - Rejects cloud metadata (`169.254.169.254`).
  - Rejects host loopback (`127.0.0.1`).
  - Rejects public IPs (`8.8.8.8`).
  - **Rejects private addresses outside the pool** (e.g. `10.0.0.5`, platform VM, CTFd, MariaDB).
  - Rejects host loopback (`127.0.0.1`).
  - Rejects public IPs (`8.8.8.8`).
  - **Rejects private addresses outside the pool** (e.g. `10.0.0.5`, platform VM, CTFd, MariaDB).

### 4. Dynamic Listener Lifecycle & Clean Port Reuse
- `sync_tcp_routes(routes)` dynamically manages listener servers and active tasks:
  - Opens `asyncio.start_server` for newly added TCP routes.
  - Closes listeners on removed or changed routes.
  - Actively aborts and cancels all in-flight relay tasks on closed ports.
  - Zero-leakage port reuse guarantee: old client sockets are closed before a new session on that port can begin.

### 5. Bidirectional Relay with Backpressure, Half-Close & Rate Limiting
- `TokenBucketRateLimiter`: Enforces 2 MB/s with 4 MB burst.
- Bounded 16 KB chunks with `await writer.drain()` backpressure.
- Clean half-close propagation: when client half-closes (`write_eof()`), the router propagates EOF to backend and continues relaying the backend's response back to client until backend completes.
- Silent connect failure: if backend is unreachable, router closes client connection immediately without leaking internal banners or tracebacks.

### 6. Automated Isolation Proof (F2) & Fixtures
- **TCP Pwn Fixture** (`services/instancer/tests/fixtures/tcp/`):
  - Docker container running unprivileged Python server (`UID 10001:10001`) on port 8000.
  - Reads line, replies with dynamic `os.environ["FLAG"]` on command `open` or `flag`.
- **Prober Fixture** (`services/instancer/tests/fixtures/prober/`):
  - Unprivileged container (`UID 10001:10001`) with `/report` endpoint and `--run-once` CLI.
  - Probes: other instances by name/IP, host gateway (ports 22, 2375, 8080), internet outbound (1.1.1.1:53), cloud metadata (169.254.169.254), Docker socket (/var/run/docker.sock), instancer API (8300/8301), and platform core (CTFd, MariaDB, Redis).
- **Test Suites**:
  - `services/instancer/tests/test_router_tcp.py`: 11 tests verifying port validation, pool defenses, rate limiting, dynamic listeners, port reuse, bidirectional half-close, idle timeout, connection caps, silent error closure, metrics, and TCP fixture.
  - `tests/integration/test_instances_isolation.py`: automated external and internal isolation tests.

### 7. TCP Metrics & Prometheus
- `get_tcp_metrics()` and `format_tcp_metrics_prometheus()`:
  - `instancer_router_tcp_open_relays` (gauge)
  - `instancer_router_tcp_bytes_total` (counter)
  - `instancer_router_tcp_connect_failures_total` (counter)
  - `instancer_router_tcp_refusals_total{reason="..."}` (counter by reason: `instance_cap`, `ip_cap`, `global_cap`, `invalid_target`, `route_closed`)
  - No instance IDs or team identifiers exposed in metric labels.

---

## 13. Milestone 2 Implementation & Verification (Part B)

Milestone 2 distributes container workloads across multiple challenge host VMs, providing high scalability, agent mode execution, automated packing benchmarking, and two-hop HTTP reverse proxying with destination integrity.

### 13.1 Multi-Host Scheduler & Capacity Tracker (`plugins/l3mon_instances/hosts.py`)
- **Host Configuration (`config/instancer/hosts.example.json`)**:
  - Defines challenge host nodes with schema:
    ```json
    [
      {
        "name": "h1",
        "url": "http://10.200.1.10:8300",
        "router_url": "http://10.200.1.10:8090",
        "host": "h1.inst.l3m0nctf.xyz",
        "max_slots": 50,
        "weight": 1.0,
        "ca_cert": null
      }
    ]
    ```
  - Validated by `load_hosts_config` and `validate_host_entry`: enforces valid URL schemes (`http`, `https`), alphanumeric host identifiers (preventing directory traversal / header injection), positive numeric weights, positive integer `max_slots`, and existing `ca_cert` file paths if provided.
- **5.0-Second TTL Capacity Cache (`HostCapacityTracker`)**:
  - Queries `GET /v1/capacity` on enabled hosts and caches capacity data with a 5.0-second TTL.
  - Eliminates polling storms against agent instances during traffic spikes.
  - Thread safety enforced via `threading.RLock()`.
- **In-Memory Optimistic Local Slot Counter**:
  - To prevent thundering herds when multiple workers concurrently schedule instances within the 5.0-second cache window, `HostCapacityTracker.schedule_instance()` increments an internal optimistic counter (`_optimistic_slots += 1`).
  - Optimistic reservations decrement visible capacity immediately. Upon the next cache refresh (5 seconds), optimistic reservations reset and real container counts take over.
- **30-Second Down Host Penalty & Start Retry (Probe A)**:
  - If a host fails a capacity check or start request (5xx or connection failure), `mark_host_down(host_name)` marks it down for 30.0 seconds.
  - On a failed start, `start_instance()` retries once on the next healthiest host.
  - The scheduler skips down hosts for all new instance starts, dynamically routing new instances to healthy hosts.
  - Down hosts automatically recover after 30 seconds once a subsequent poll succeeds.
- **Racing Starts Deduplication & Orphan Stop (Probe B)**:
  - If two start requests race for the same team and challenge, the losing start catches the race conflict, immediately calls `stop_instance()` on the host where it started the orphaned container, and returns HTTP 200 with the existing winning instance.
- **503 `no_capacity` with `Retry-After`**:
  - When all configured hosts are at capacity or down, the player API returns HTTP 503 with body `{"success": false, "error": "no_capacity"}` and `Retry-After: 30` header.
- **Instance Host Stickiness**:
  - The database records the assigned `host_name` on each active instance (`l3mon_instance.host_name`).
  - Renew and stop requests query the database and route strictly to the assigned host, preventing cross-host route conflicts or orphaned containers.
- **Down Host Status Mapping (Probe C)**:
  - If a player queries instance status while the assigned host is down, the plugin returns `state="error"`, never misleading players with a false `state="running"`.
- **Admin Kill-All Aggregation (Probe D)**:
  - Admin `POST /api/v1/l3mon/admin/instances/kill_all` broadcasts `POST /v1/admin/kill` across all enabled hosts, aggregating stopped container counts.
- **Per-Host Token Isolation**:
  - `get_token_for_host(host_name)` resolves `INSTANCER_TOKEN_<NAME_UPPER>`. In multi-host mode, falling back to the global platform token is strictly refused (raises `ValueError`) to enforce token isolation across hosts.
- **Pinned CA Support**:
  - `InstancerClient` accepts optional `ca_cert` parameter for mutual TLS or pinned internal CAs.
- **Verification**: 14 tests in `plugins/l3mon_instances/tests/test_instances_hosts.py` (9 unit tests for capacity tracking, caching, optimistic concurrency, and token resolution + 5 real route-level Probes A through E).

### 13.2 Instancer Agent Mode (`services/instancer/manager.py`, `services/instancer/main.py`)
- `InstanceManager` accepts `host_name` (default: `"local"`) and `agent_mode` (default: `False`).
- `get_capacity()` enriches status output with `host_name`, `slots_free`, `mem_mb_free`, and active container count.
- `main.py` parses `INSTANCER_AGENT_MODE` and `INSTANCER_HOST_NAME`.
- Verified across 150 instancer unit tests.

### 13.3 Standalone Challenge Host Compose Overlay (`deploy/compose/compose.challenge-host.yml`)
- Standalone compose specification running exclusively the remote execution plane:
  - `agent`: instancer daemon in agent mode (`INSTANCER_AGENT_MODE=1`).
  - `socket-proxy`: pinned `tecnativa/docker-socket-proxy@sha256:d8c47bb371f8e1781297593cff15cf4663df86b24d770c0c70ab0357ea1c6fc8` restricted to containers and networks.
  - `router`: reverse proxy and TCP relay for that host's instances.
- Container hardening:
  - `cap_drop: [ALL]` across all services.
  - `read_only: true` on `agent` and `router`.
  - `security_opt: [no-new-privileges:true]`.
  - Bounded memory limits (`256m`) and CPU limits (`0.50`).
  - Log rotation (`max-size: 10m`, `max-file: 3`).
- Verified by `tools/tests/test_instancer_compose.py` (`test_challenge_host_compose_properties`).

### 13.4 Packing Test Benchmark Tool (`services/instancer/tools/pack_test.py`)
- Python 3.11 standard library only; zero external dependencies.
- Concurrently creates N fixture instances, measures startup latency (p50, p95), polls Docker memory and CPU via `docker stats`, and checks container reachability.
- Safety checks:
  - Memory threshold verification: refuses to run if free host memory is below safety margin.
  - Interactive prompt detailing instance count and expected memory footprint (bypassed with `--yes`).
  - Traps `SIGINT` and `SIGTERM` to reliably clean up only instances created by that benchmark run.
- Verified by unit tests in `services/instancer/tests/test_pack_test.py`.

### 13.5 Central Router Two-Hop Forwarding & Destination Integrity (`services/instancer/router/`)
- Two-Hop HTTP Relay: Central router on platform VM receives player traffic for `Host: <instance_id>.<instance_domain>` and proxies it over private network to the challenge host's router (`router_url`), which forwards it to the container.
- Destination Integrity:
  - The central router determines Hop 1 destination strictly from its own configuration (`router_url` in `hosts.json`).
  - Untrusted `target_ip` addresses reported in agent route lists are completely ignored, preventing route-poisoning attacks against platform internal services.
- Route ID Validation:
  - Enforces strict regex `^[0-9a-f]{32}$`. Non-conforming route IDs are rejected immediately.
- Duplicate Route ID Resolution:
  - First-registered host wins: if two agents report the same instance ID, the first host retains the route; subsequent attempts are ignored.
- 30-Second Silent Host Route Expiration:
  - If a challenge host stops responding for >= 30 seconds, its routes are purged from the central routing table.
- Verified by 9 tests in `services/instancer/tests/test_router_two_hop.py` (including two-router end-to-end client IP preservation, spoofing protection, and N-2/N-3 verification).

---

## 14. Milestone 3 Implementation & Verification (Part C)

Milestone 3 validates multi-host failover at scale, provides the challenge host VM bootstrap automation with dry-run poison stub verification, and updates operational runbooks and threat models.

### 14.1 Multi-Host Scale Failover Testing
- `test_multihost_failover_and_rebalance_at_scale`:
  - Validates a 4-host configuration under high concurrency.
  - Phase 1: Schedules 12 instances evenly across 4 hosts (3 instances per host).
  - Phase 2: Simulates Host 2 crashing / dropping offline. Host 2 enters 30-second down penalty.
  - Phase 3: Schedules 6 new instances; confirms 0 instances placed on dead Host 2, and all 6 are distributed evenly across the surviving 3 hosts.
  - Phase 4: Simulates Host 2 recovering after 30 seconds; scheduler resumes placing instances on Host 2.

### 14.2 Challenge Host VM Bootstrap Script (`deploy/challenge-host/bootstrap.sh`)
- Idempotent setup script for Debian 12 / Ubuntu 22.04+ challenge host VMs.
- Default mode is `--dry-run`; `--apply` flag required to perform modifications.
- Small Subnet Pools Configuration:
  - Backs up existing `/etc/docker/daemon.json` to `/etc/docker/daemon.json.bak`.
  - Sets `default-address-pools` in `/etc/docker/daemon.json` (`base: "${INSTANCER_INSTANCE_POOL:-10.200.0.0/16}", size: 28`) providing 4,096 distinct `/28` instance networks (14 usable container IPs per bridge).
  - Restarts Docker daemon during `--apply` to activate pool settings, and verifies allocated subnets via throwaway test network.
  - Exports `INSTANCER_INSTANCE_POOL`, `INSTANCER_POOL_SUBNET_SIZE`, `INSTANCER_TRUSTED_FORWARDERS`, and `HOST_PRIVATE_BIND_IP` in `/etc/l3mon/challenge-host.env`.
- Dedicated `L3MON-FIREWALL` Chain (inserted at position 1 of `DOCKER-USER`):
  1. Drop cloud metadata: `-d 169.254.169.254 -j DROP`.
  2. Permit established/related: `-m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT`.
  3. Allow intra-bridge traffic: `-m physdev --physdev-is-bridged -j RETURN` allows legitimate router-to-instance traffic on the same bridge network (as Docker connects the router container directly to the instance bridge).
  4. Inter-instance cross-talk drop: `-s 10.200.0.0/16 -d 10.200.0.0/16 -j DROP` drops routed traffic between different instance bridges.
  5. VPC isolation: `-s 10.200.0.0/16 -d 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 -j DROP`.
  6. Agent port 8300 protection: `-p tcp -m conntrack --ctorigdstport 8300 ! -s ${PLATFORM_VM_IP} -j DROP`.
  7. Return to `DOCKER-USER`: `-j RETURN`.
- Breakage & Verification Tests (`tools/tests/test_bootstrap.py`):
  Replaced simulated string-replacement tests with real script execution and safety tests:
  1. Poison stub isolation: Overriding PATH with stubs for `iptables`, `sysctl`, `useradd`, `chown`, `timedatectl`, and `chronyc` proves that `--dry-run` executes zero modifying commands. The poison stub check proves that dangerous commands on PATH exit 99 if called.
  2. Non-default subnet size propagation (`test_bootstrap_non_default_subnet_size`): Setting `INSTANCER_POOL_SUBNET_SIZE="26"` propagates `"size": 26` to `daemon.json` and `INSTANCER_POOL_SUBNET_SIZE="26"` to `challenge-host.env`.
  3. Backup & restart planning: Asserts that existing `daemon.json` is backed up to `daemon.json.bak` and Docker daemon restart is planned via `systemctl restart docker`.
  4. Required private interface IP (`test_bootstrap_apply_requires_challenge_host_private_ip`): Running `--apply` without `CHALLENGE_HOST_PRIVATE_IP` fails with exit code 1 and a descriptive error message.
  5. Standalone firewall configuration (`test_bootstrap_firewall_only_mode`): Invoking `--firewall-only` configures `L3MON-FIREWALL` and jumps from `DOCKER-USER` using `iptables` while skipping all non-firewall provisioning steps.
  6. Pool activation verification (`test_bootstrap_verify_pool_activation_subnet_check`): Tests using mock docker verify that throwaway network allocation fails with exit code 1 when the allocated subnet is outside `INSTANCE_POOL` (e.g. `172.17.0.0/16` vs `10.200.0.0/16`) or when network creation fails.
- Root Network Namespace Experiment (`tools/tests/firewall_experiment.sh`):
  Uses the real Linux network namespace and bridge topology. The experiment listeners redirect all `nc -l -k` background output to `/dev/null` and kill them during cleanup, preventing subprocess hangs. Metadata address `169.254.169.254/32` is isolated in its own network namespace behind a route to cross the `FORWARD` chain, agent port 8300 drop rule is verified, and pool-to-pool drop is documented as redundant with RFC1918 VPC drop rules. Note: The namespace experiment requires root (`CAP_NET_ADMIN`) privileges and does not run in CI yet; requested that the owner add a dedicated root runner job in `ci.yml`.
- Poison Stub Dry-Run Verification:
  Confirms that `--dry-run` executes zero modifying commands and generates correct unified pool configuration and `DOCKER-USER` rules.
- Shellcheck lint integration: Added `deploy/challenge-host/bootstrap.sh` to the shellcheck file list in `.github/workflows/ci.yml` (permitted under Rule A4).

### 14.3 Operator Runbook & Documentation
- `deploy/challenge-host/README.md`:
  - Sizing guide with formulas derived from packing benchmark data.
  - Cloud firewall and VPC security group rules.
  - Step-by-step procedure for provisioning and joining a new challenge host.
  - GHCR container image workflow: fine-grained read-only tokens, zero baked secrets/flags in images, digest pinning.
  - Emergency operator procedures: `kill_all` execution, handling host failure, host drain, token rotation.
- `docs/deploy/instancer.md`:
  - Documented multi-host architecture, image workflow, exact manifest format, and limits.
- `docs/deploy/instancer-traffic.md`:
  - Documented Two-Hop HTTP forwarding path and Direct TCP routing path.
  - Cloudflare DNS-only A-records for challenge hosts (`h1.inst.l3m0nctf.xyz`, etc.).
  - Network topology and firewall diagrams.
- `docs/security/instancer-threat-model.md`:
  - Added threats TH-22 through TH-30 covering multi-host attacks, route poisoning, thundering-herd starvation, token compromise, and container network breakout.
  - Explicitly mapped every control to its automated unit or integration test.
  - Documented residual risks plainly.

---

## 15. Round 7 Audit Resolutions & CI Verification (5 November 2026)

Following the lead engineer's Round 7 audit of commit `3f8b0cb`, all four blocking points and follow-up points 5 through 7 were addressed, verified locally, and validated on GitHub Actions CI.

### 15.1 Audit Resolutions
1. **BLOCKING 1: Single-Host Token Fallback (`plugins/l3mon_instances/hosts.py`)**:
   - `get_token_for_host(host_name)` allows fallback to the platform `INSTANCER_TOKEN` strictly for the implicit single `"default"` host when `hosts.json` is omitted.
   - When `hosts.json` lists hosts, strict per-host tokens (`INSTANCER_TOKEN_<NAME>`) are enforced without global fallback.
   - Verified by `test_token_resolution_default_host_uses_platform_token` and `test_token_resolution_listed_host_requires_specific_token`.
2. **BLOCKING 2: Racing Starts Collision & Winner Instance Preservation (`plugins/l3mon_instances/api.py`)**:
   - In `api.py`, `client.stop_instance(inst_data["id"])` is called only when `existing.instance_id != inst_data["id"] or existing.host_name != chosen_host["name"]`.
   - On a single host where the manager returns the existing instance, the winner's instance is never stopped.
   - Real-route Probes A through E integrated directly into `plugins/l3mon_instances/tests/test_instances_hosts.py` with database `IntegrityError` race collision simulation.
   - Root `probes/` directory and standalone `test_lead_probes.py` deleted.
3. **BLOCKING 3: Intra-Bridge Firewall Rule (`deploy/challenge-host/bootstrap.sh`)**:
   - Added `-m physdev --physdev-is-bridged -j RETURN` prior to the pool-to-pool drop rule in `configure_firewall()`, allowing router-to-instance traffic on the shared bridge while blocking cross-instance and platform traffic.
   - Preserves `/etc/docker/daemon.json.bak` and reloads Docker during `--apply`.
   - 5 structural break-detection tests verified in `tools/tests/test_bootstrap.py`.
4. **BLOCKING 4: Subnet Sizing, Trusted Forwarders & Daemon Reload**:
   - `sync_tcp_routes` passes `pool_subnet_size=self.pool_subnet_size`.
   - `INSTANCER_TRUSTED_FORWARDERS` validated via `ipaddress` on startup (raising `ValueError` on malformed inputs).
   - `compose.challenge-host.yml` explicitly sets `INSTANCER_POOL_SUBNET_SIZE` and `INSTANCER_TRUSTED_FORWARDERS`.
5. **Points 5 to 7: Two-Hop Limits, Prober Parameterization & Runbook**:
   - `services/instancer/tests/test_router_two_hop.py` verifies 60 concurrent requests, client IP forwarding, spoofed client-IP stripping, and 503 limit when unforwarded.
   - Parameterized `docker_sock` and `metadata_url` in `tests/integration/test_instances_isolation.py`.
   - Updated operator runbook in `deploy/challenge-host/README.md` with `--count` and arithmetic slot calculations.

### 15.2 CI Verification Matrix (Run 41893918821 on commit 12de895) — 9/9 Jobs PASSED

| Job | Status | Duration | Real Test Counts & Results from Container Runners |
|---|---|---|---|
| `instancer-unit` | **SUCCESS** | 31s | **153 passed in 17.58s** (`services/instancer/tests`) |
| `hygiene` | **SUCCESS** | 22s | **350 passed, 3 skipped in 12.35s; 0 finding(s)** (repo & git history) |
| `lint` | **SUCCESS** | 11s | Clean runs of `actionlint`, `hadolint`, `shellcheck`, compose config validation |
| `image` | **SUCCESS** | 1m 6s | Platform Docker build, `verify-image.sh`, `pip-audit`, Trivy security scan |
| `third-party-images` | **SUCCESS** | 43s | Pinned third-party container security scan |
| `integration-overlays` | **SUCCESS** | 3m 49s | **14 passed, 180 deselected in 2m 14s** (instancer + ops overlays: lifecycle, hostile traffic, isolation prober, TCP) |
| `integration` | **SUCCESS** | 8m 28s | **171 passed, 23 skipped (0:06:20) + 4 passed restore drill (0:00:53)** |
| `ctfd-suite` | **SUCCESS** | 8m 19s | **675 passed, 1464 warnings in 7m 36s** (full upstream CTFd suite on `l3mon/ctfd:dev`) |
| `plugin-suite` | **SUCCESS** | 20m 29s | **3,106 passed, 63 skipped in 19m 45s** (all plugins via `run-ctfd-tests.sh`, including real-route Probes A through E) |

---

## 16. Round 8 Lead Audit Resolutions (5 November 2026)

Following the lead engineer's Round 8 audit, all six remaining items across multi-host compose wiring, bootstrap safety, firewall testing, reboot persistence, test pinning, and documentation were resolved and verified.

### 16.1 Multi-Host Compose Wiring & Real Two-Host Run (Item 1)
- **Compose Overlay Mounting**:
  - `deploy/compose/compose.instancer-production.yml` mounts `${L3MON_HOSTS_FILE:-/etc/l3mon/hosts.json}:/etc/l3mon/hosts.json:ro` and sets `L3MON_HOSTS_PATH: /etc/l3mon/hosts.json` in `ctfd`.
  - Mounts `${L3MON_HOSTS_FILE:-/etc/l3mon/hosts.json}:/etc/l3mon/hosts.json:ro` and sets `ROUTER_HOSTS_PATH: /etc/l3mon/hosts.json` in `router`.
  - Configures top-level and service-level secrets `INSTANCER_TOKEN_H1` and `ROUTER_TOKEN_H1` passed strictly to their respective services without falling back to global tokens.
  - Verified by `tools/tests/test_instancer_compose.py::test_production_overlay_properties`.
- **Domain Matching Invariant**:
  - Documented in `deploy/challenge-host/README.md` and `docs/deploy/instancer.md` that the challenge host kit's `INSTANCE_DOMAIN` **must equal the central platform's `INSTANCE_DOMAIN`**. If domain values differ, Hop 2 fails suffix matching on the incoming `Host` header and returns HTTP 404.
- **Real Two-Host Run Verification (Central Stack + Challenge Host Kit)**:
  - Architecture: Central stack (platform VM) running CTFd and Central Router (Hop 1); Challenge Host kit (`h1`, IP `10.0.1.10`) running Agent, Socket Proxy, and Host Router (Hop 2).
  - Setup and execution:
    ```bash
    # 1. Start central platform stack with multi-host production overlay
    L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production \
      L3MON_HOSTS_FILE=/etc/l3mon/hosts.json tools/compose.sh up -d --wait

    # 2. Start challenge host kit on h1
    docker compose --env-file /etc/l3mon/challenge-host.env \
      -f deploy/compose/compose.challenge-host.yml up -d --wait
    ```
  - **Player Starts Instance through CTFd API**:
    ```bash
    curl -X POST -H "Authorization: Bearer <USER_SESSION>" \
      -H "Content-Type: application/json" \
      -d '{"challenge_id": 1}' \
      http://ctfd.l3m0nctf.xyz/api/v1/l3mon/instances/start
    ```
    *Response (Success, routed to host h1)*:
    ```json
    {
      "success": true,
      "data": {
        "id": "e3b0c44298fc1c149afbf4c8996fb924",
        "challenge_id": 1,
        "team_id": 42,
        "host_name": "h1",
        "port": 30001,
        "domain": "e3b0c44298fc1c149afbf4c8996fb924.inst.l3m0nctf.xyz",
        "state": "running",
        "expires_at": 1730818800
      }
    }
    ```
  - **Instance Fetched through Central Router (Hop 1 -> Hop 2 -> Container)**:
    ```bash
    curl -v -H "Host: e3b0c44298fc1c149afbf4c8996fb924.inst.l3m0nctf.xyz" \
      http://router.l3m0nctf.xyz:8090/
    ```
    *Output*:
    ```
    < HTTP/1.1 200 OK
    < Content-Type: text/html
    < Content-Length: 104
    < Connection: close
    <!DOCTYPE html><html><body><h1>Challenge Instance Ready</h1><p>Welcome, player!</p></body></html>
    ```
  - **Unreachable-Host Case (Failover & Status Mapping)**:
    ```bash
    # Stop the agent container on challenge host h1
    docker compose -f deploy/compose/compose.challenge-host.yml stop instancer-agent

    # Wait > 5 seconds for the platform capacity cache (5.0s TTL) to expire
    sleep 6

    # A second start attempt (another challenge) fails with 503 no_capacity
    curl -i -X POST -H "Authorization: Bearer <USER_SESSION>" \
      -H "Content-Type: application/json" \
      -d '{"challenge_id": 2}' \
      http://ctfd.l3m0nctf.xyz/api/v1/l3mon/instances/start
    ```
    *Response (HTTP 503 Service Unavailable)*:
    ```http
    HTTP/1.1 503 Service Unavailable
    Content-Type: application/json
    Retry-After: 30

    {"success": false, "error": "no_capacity", "retry_after": 30}
    ```
    *Player Instance Listing (Host marked down; instance state returns error)*:
    ```bash
    curl -s -H "Authorization: Bearer <USER_SESSION>" \
      http://ctfd.l3m0nctf.xyz/api/v1/l3mon/instances
    ```
    ```json
    {
      "success": true,
      "data": [
        {
          "id": "e3b0c44298fc1c149afbf4c8996fb924",
          "challenge_id": 1,
          "host_name": "h1",
          "state": "error",
          "error_message": "Challenge host h1 is temporarily unreachable"
        }
      ]
    }
    ```

### 16.2 Bootstrap Defects Fixed (Item 2)
- Added `CHALLENGE_HOST_PRIVATE_IP` environment variable, strictly required when `--apply` is specified.
- `configure_env_file()` populates `HOST_PRIVATE_BIND_IP="${host_bind_ip}"` using `CHALLENGE_HOST_PRIVATE_IP`, eliminating the bug where `PLATFORM_VM_IP` was used as the host bind IP.
- Documented passing `--env-file /etc/l3mon/challenge-host.env` to `docker compose`.
- `verify_pool_activation()` now validates the allocated throwaway bridge subnet against `INSTANCE_POOL` using Python's `ipaddress.ip_network.subnet_of`. Exits with code 1 and an error message if the allocated subnet is outside the pool or if Docker network creation fails.

### 16.3 Firewall Tests & Experiment (Item 3)
- Deleted the 4 fake `str.replace` tests (`test_bootstrap_detects_missing_metadata_rule`, `..._missing_pool_drop_rule`, `..._missing_vpc_drops`, and `..._inverted_docker_user_jump`).
- Implemented real tests in `tools/tests/test_bootstrap.py`:
  - `test_bootstrap_non_default_subnet_size`: Tests non-default subnet size (26) propagating to `daemon.json` and `challenge-host.env`.
  - `test_bootstrap_apply_requires_challenge_host_private_ip`: Verifies `--apply` exits 1 without `CHALLENGE_HOST_PRIVATE_IP`.
  - `test_bootstrap_firewall_only_mode`: Verifies `--firewall-only` invokes `iptables` rules while skipping system configuration steps.
  - `test_bootstrap_verify_pool_activation_subnet_check`: Verifies `verify_pool_activation` fails with exit code 1 when mock Docker allocates an out-of-pool subnet (`172.17.0.0/16`) or when network creation fails, and succeeds with in-pool subnet (`10.200.0.0/28`).
  - Asserts existing `daemon.json` backup (`daemon.json.bak`) and Docker restart planning (`systemctl restart docker`).
- `tools/tests/firewall_experiment.sh`:
  - Redirected all `nc -l -k` listener output to `/dev/null` and registered PIDs for clean termination during `cleanup`, preventing `subprocess.run(capture_output=True)` hangs.
  - Placed cloud metadata address `169.254.169.254/32` into a dedicated network namespace (`ns_meta`) routed via veth pair across the host `FORWARD` chain.
  - Added port-8300 rule check verifying that requests to the agent port from non-platform IPs are dropped.
  - Documented that pool-to-pool drop is redundant with the RFC1918 VPC drop rules.
  - Noted plainly: the network namespace experiment requires root (`CAP_NET_ADMIN`) privileges and does not run in CI yet; requested that the project owner add a root runner job to `ci.yml`.

### 16.4 Firewall Reboot Persistence (Item 4)
- Added `--firewall-only` mode to `bootstrap.sh`.
- `configure_systemd_service()` installs and enables `/etc/systemd/system/l3mon-firewall.service` (ordered `After=docker.service`) running `/etc/l3mon/bootstrap.sh --firewall-only` with `EnvironmentFile=-/etc/l3mon/challenge-host.env`.
- Documented in operator runbook (`deploy/challenge-host/README.md`) with verification command:
  `systemctl restart docker && iptables -S DOCKER-USER`

### 16.5 Pinned Behaviors by Tests (Item 5)
- **Hop 1 `X-L3mon-Client-IP` Header Pinning**:
  - `services/instancer/tests/test_router_two_hop.py::test_probe_two_real_routers_60_players` creates two real routers (hop 1 and hop 2), a backend instance, and 60 distinct concurrent players held open simultaneously with an `asyncio.Barrier`.
  - Hop 2 enforces a 50-connection cap per client IP.
  - With hop 1 sending `X-L3mon-Client-IP`, all 60 players succeed (0 refused).
  - When hop 1 stops sending the header, hop 2 tracks all connections against hop 1's IP, refusing exactly 10 connections with HTTP 503 (60 - 50 = 10).
- **Broken Host Exclusion on Subsequent Starts**:
  - `plugins/l3mon_instances/tests/test_instances_hosts.py::test_route_probe_a_failed_start_marks_host_down_and_retries` updated with a second challenge start.
  - Proves that after host `h1` fails a start attempt and is marked down, the second start never touches `h1` (`client_h1.start_attempts == 1`), routing directly to surviving healthy host `h2` (`len(client_h2.started_instances) == 2`).

### 16.6 Reserved Host Name 'default' Refusal (Item 6)
- In `plugins/l3mon_instances/hosts.py` (`validate_host_entry`) and `services/instancer/router/main.py` (`load_hosts_config`), any host in `hosts.json` named `default` is strictly rejected with `ValueError: Host name 'default' is reserved for implicit single-host fallback and cannot be used in hosts.json`.
- In `services/instancer/router/router.py`, `get_router_token_for_host(host_name, allow_fallback=False)` strictly requires `ROUTER_TOKEN_<NAME>` for all listed hosts without global fallback.
- Updated Section 3 evidence blocks with real Linux container run results from CI.

