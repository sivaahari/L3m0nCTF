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
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
117 passed in 10.51s (19 October 2026)
```
*Coverage: manifest validation, 22 hostile specs in `assert_safe`, flag derivation vectors, quota and cooldown enforcement, TTL reaper, label reconciliation, API auth tokens, constant-time comparisons, router host matching, header sanitization, idle timeouts, Prometheus metrics, hostile client suite (slow headers, slow bodies, connection bursts), streaming response 20MB cap, pre-routing and per-instance connection caps, pre-routing host/route verification, challenge request body caps, 64MB global reading budget, request line & header caps (8KB / 16KB), Cloudflare real IP validation, total response deadline (120s), backend socket close in `finally`, concurrency reservation, 15s Docker timeouts, orphan network prune on startup.*

### Tools Regression Suite (`tools/tests/`)
```
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
302 passed, 12 skipped in 30.65s (19 October 2026)
```
*Includes `test_instancer_compose.py` validating dev overlay vs production overlay rendered configs with `docker compose config` and verifying that production overlay fails when `INSTANCE_DOMAIN` is unset.*

### CTFd Plugin Tests inside Container (`tools/run-ctfd-tests.sh`)
```
platform linux -- Python 3.11.17, pytest-8.4.2
23 passed, 4 skipped in 26.38s (18 October 2026)
```

### Real MariaDB & Redis Migration Suite (`tools/run-migration-test.sh`)
```
platform linux -- Python 3.11.17, pytest-8.4.2
4 passed in 22.15s (18 October 2026)
```

### Live Stack Integration Tests (`tests/integration/test_instances.py`)
```
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
tests/integration/test_instances.py::test_recreating_ctfd_keeps_every_overlay_the_stack_was_started_with PASSED [ 33%]
tests/integration/test_instances.py::test_instances_end_to_end_lifecycle PASSED [ 66%]
tests/integration/test_instances.py::test_router_survives_hostile_traffic PASSED [100%]
3 passed in 51.54s (18 October 2026)
```

### Repository Hygiene Scan (`tools/ l3mon hygiene`)
```
(cd tools && python -m l3mon hygiene --root .. --history)
0 finding(s) (19 October 2026)
```

---

## 4. Differences from this prompt

- **WebSocket Claim Removed**: The original prompt permitted either bidirectional WebSocket streaming or removing the claim. In agreement with prompt B3-R1 ("removing the claim is acceptable for now; say so"), WebSocket support claims were cleanly stripped from code and docs. `Upgrade` is stripped as a hop-by-hop header.

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
| Image pinned digest required | Omit `@sha256:...` digest from challenge image spec | `test_manifest_unpinned_image_fails_in_prod` |
| Non-root user required (A1) | Specify `user = "0:0"` (root) or `"00"` in manifest spec | `test_assert_safe_rejects_root_user` |
| Host mounts prohibited (A1) | Add `HostConfig.Mounts` bind mount spec | `test_assert_safe_rejects_modern_mounts` |
| Port publishing prohibited (A1) | Set `PublishAllPorts: true` in container HostConfig | `test_assert_safe_rejects_publish_all_ports` |
| Refused spec cleanup (A2) | `assert_safe` fails on spec | `test_rejected_spec_cleans_up_network` |
| Non-ASCII flag safe (A3) | Submit flag with non-ASCII unicode byte | `test_dynamic_flag_compare_non_ascii_resilient` |
| Empty token rejection (A4) | Pass empty Bearer token when `ROUTER_TOKEN` is unset | `test_api_auth_required_and_invalid_token` |
| Huge declared body cap (A5) | Client sends `Content-Length: 4000000000` | `test_a_huge_declared_body_is_refused_with_413_before_any_of_it_is_read` |
| Excessive headers (A5) | Client sends > 100 header lines | `test_too_many_header_lines_are_refused_with_431` |
| Slow client drop (A5) | Client stalls mid-request | `test_a_client_that_stops_sending_is_dropped_at_the_timeout_not_kept_for_ever` |
| Player TTL override ignored (A8) | Player API sends `ttl_seconds=31536000` | `test_player_api_ignores_ttl_override` |
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

- **TCP Routing & Isolation Suite (Milestone 2)**: Relaying for TCP challenges and automated prober isolation tests scheduled for Milestone 2.
- **Multi-Host Capacity & Agent Failover (Milestone 3)**: Scheduled for Milestone 3.

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
