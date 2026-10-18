# SP4: Challenge Instancer Follow-up (Implementation Plan)

Date: 18 October 2026  
Status: Active  
Milestone Target: **Milestone 1b (R1, R2, R4, Handoff & Threat Model)**  

---

## 1. Facts Measured and Read from Codebase

1. **Audit Merge & Baseline**:
   - `origin/pre-deployment` merged into `feat/instancer` cleanly at commit `7cfb9ca`.
   - Baseline instancer tests: **88 passed** in 8.84s (`python -m pytest services/instancer/tests -q`).
   - Baseline tools tests: **299 passed, 12 skipped** in 39.18s (`python -m pytest tools/tests -q`).
   - Audit findings A1–A8 integrated:
     - A1: `assert_safe` verifies `HostConfig.Mounts`, `PublishAllPorts`, group/device/cgroup fields, and numeric non-zero users (`services/instancer/safety.py:30-80`, `test_assert_safe.py:225-280`).
     - A2: `manager.py` cleans up networks on `assert_safe` failure (`services/instancer/manager.py:311-326`).
     - A3: `plugins/l3mon_instances/flag.py:68-80` uses byte comparison for `compare_digest`.
     - A4: `services/instancer/server.py:46-75` uses byte comparison for `INSTANCER_TOKEN` and `ROUTER_TOKEN`.
     - A5: `services/instancer/router/main.py:38-80` bounds headers (< 100 lines, 10s timeout) and rejects bodies > 10MB before reading.
     - A6: Reverted `docker/ctfd/Dockerfile` edits.
     - A7: Fixture folder documented as `fixtures/web`.
     - A8: `plugins/l3mon_instances/api.py:133-145` ignores `ttl_seconds` unless `L3MON_INSTANCES_TTL_OVERRIDE=1`.

2. **Router Deficiencies (R1)**:
   - `services/instancer/router/router.py:165`: `raw_resp = await asyncio.wait_for(reader.read(), timeout=IDLE_TIMEOUT_SECONDS)` reads unbounded backend response bytes into memory. If backend streams infinitely, router exhausts memory (cgroup limit 256MB) and crashes.
   - `services/instancer/router/router.py:181-186`: `resp_headers = {}` collects headers into a plain dictionary, overwriting multiple `Set-Cookie` headers.
   - `services/instancer/router/main.py:85-88`: sends backend headers and blindly appends `Content-Length: {len(resp_body)}`, sending duplicate `Content-Length` headers.
   - `services/instancer/router/main.py:38-60`: does not validate conflicting `Content-Length` and `Transfer-Encoding`, nor chunked request bodies.
   - `services/instancer/router/router.py:25`: `MAX_CONNECTIONS_PER_INSTANCE = 50` is defined but never enforced. No global connection limit exists.
   - `services/instancer/router/router.py:50`: WebSocket upgrade is claimed in docs but closes immediately on response.

3. **Compose & Environment Defaults (R2)**:
   - `deploy/compose/compose.instancer.yml`: Hardcodes `INSTANCER_DEV: "1"`, mounts `challenges.example.toml`, binds router to `127.0.0.1:8090`.
   - Production overlay (`deploy/compose/compose.instancer-production.yml`) does not yet exist.

4. **Manager Locking & Docker Timeouts (R4)**:
   - `services/instancer/manager.py:235`: `with self._lock:` wraps the entire `start_instance` method, including synchronous blocking calls to `self.docker.create_network()`, `connect_network()`, `create_container()`, and `start_container()`. A single slow Docker daemon freezes all concurrent starts, stops, and renewals.
   - Docker client calls in `services/instancer/docker_client.py` lack timeouts on HTTP requests.
   - If instancer crashes between `create_network()` and `create_container()`, orphan networks with label `l3mon.instance` are left behind without container associations.

---

## 2. Key Decisions & Alternatives Rejected

### R1. Router Streaming and Bounds
- **Decision:** Stream backend HTTP responses in 64 KB chunks up to a hard cap of 20 MB (`MAX_RESPONSE_BYTES = 20 * 1024 * 1024`). If response exceeds 20 MB, abort connection immediately.
- **Decision:** Parse and forward headers preserving multi-value headers as `list[tuple[str, str]]`. Filter hop-by-hop headers from both request and response. Ensure exactly one `Content-Length` header is emitted. Reject requests containing both `Transfer-Encoding` and `Content-Length` with 400 Bad Request.
- **Decision:** Enforce active connection tracking: max 50 concurrent connections per instance ID and max 1,000 total connections on the router. Exceeding limits returns HTTP 503 Service Unavailable.
- **Decision:** Remove WebSocket claims from docs and code until a dedicated bidirectional streaming pipe is implemented in Milestone 2.
- *Rejected Alternative:* Buffering responses in temporary files. Adds disk I/O latency and requires writable storage on a read-only container.

### R2. Safe Production Compose Overlay
- **Decision:** Make `INSTANCER_DEV` in `deploy/compose/compose.instancer.yml` default to `"0"` (`${INSTANCER_DEV:-0}`).
- **Decision:** Create `deploy/compose/compose.instancer-production.yml` specifying:
  - `INSTANCER_DEV: "0"`.
  - Manifest mounted from `/etc/l3mon/challenges.toml` (external production path).
  - Production router ports and settings without localhost binding restriction.
  - Zero development tokens or default secrets.
- **Decision:** Add pytest validation running `docker compose config` on both dev and production overlays to assert strict separation.

### R4. Fine-Grained Manager Locking & Concurrency
- **Decision:** Split `start_instance` into three phases:
  1. **Phase 1 (Lock held, ~1ms)**: Validate team quotas, challenge cooldowns, host capacity. Insert an `InstanceRecord` with `state = InstanceState.STARTING` and reserve TCP port.
  2. **Phase 2 (Lock released, Docker calls outside lock)**: Create network, connect router, run `assert_safe`, create container, start container.
  3. **Phase 3 (Lock held, ~1ms)**: Update record to `state = InstanceState.RUNNING`, update IP/CID. On failure, clean up Docker objects, delete reservation, and release port under lock.
- **Decision:** Add 15s timeout to all Docker HTTP client operations.
- **Decision:** Reconcile loop cleans orphan networks on startup: any network matching label `l3mon.network=instance` with no corresponding active container is purged.
- *Rejected Alternative:* Locking per-team only (`threading.Lock()` per team ID). Quotas are global (host memory/slots) and per-team; reservation records under a single global lock for microseconds avoid complex multi-lock ordering and deadlocks.

---

## 3. Interfaces & Data Contracts

### 3.1 Router Response Streaming & Headers
- Streamed chunk size: `64 * 1024` (64 KB).
- Max response bytes: `20 * 1024 * 1024` (20 MB).
- Header structure: `list[tuple[str, str]]` preserving repeated headers (e.g., multiple `Set-Cookie`).
- Hop-by-hop stripped: `connection`, `keep-alive`, `proxy-authenticate`, `proxy-authorization`, `te`, `trailers`, `transfer-encoding`, `upgrade`.

### 3.2 Concurrency Caps
- `MAX_CONNECTIONS_PER_INSTANCE = 50`
- `MAX_TOTAL_CONNECTIONS = 1000`
- Rejection status: `503 Service Unavailable` with body `Service Busy\n`.

### 3.3 Manager Reservation States
- `InstanceState.STARTING`: Temporary reservation. Concurrent requests for same team/challenge get `409` or wait. Counts towards team active quota.
- Cleanup on timeout: If a `STARTING` record is older than 60s without transitioning to `RUNNING`, the reaper marks it failed and frees the slot.

---

## 4. Test Plan (Test First)

### Router Suite (`services/instancer/tests/test_router.py`)
1. `test_router_streams_large_response_without_exceeding_memory`
2. `test_router_aborts_backend_streaming_exceeding_20mb_cap`
3. `test_router_preserves_multiple_set_cookie_headers`
4. `test_router_strips_duplicate_content_length_and_hop_by_hop_headers`
5. `test_router_rejects_conflicting_transfer_encoding_and_content_length`
6. `test_router_enforces_max_connections_per_instance_returning_503`
7. `test_router_enforces_global_connection_cap_returning_503`
8. `test_router_slow_backend_triggers_gateway_timeout`

### Compose Overlay Suite (`tools/tests/test_compose_overlays.py`)
1. `test_dev_overlay_defaults_dev_mode_safely`
2. `test_production_overlay_disables_dev_mode_and_example_manifest`

### Manager Concurrency & Fault Tolerance (`services/instancer/tests/test_manager_concurrency.py`)
1. `test_start_instance_releases_lock_during_docker_calls`
2. `test_concurrent_starts_different_teams_do_not_block_each_other`
3. `test_orphan_network_cleaned_on_startup_reconcile`
4. `test_docker_timeout_cleans_up_reservation_and_resources`

---

## 5. Milestone Execution Steps

1. **Step 1: R1 (Router Hardening)**
   - Write tests in `services/instancer/tests/test_router.py`.
   - Implement bounded streaming, multi-valued headers, chunked/length validation, and connection counters in `router.py` and `main.py`.
   - Verify tests pass.

2. **Step 2: R2 (Production Compose Overlay)**
   - Update `deploy/compose/compose.instancer.yml` with safe defaults (`${INSTANCER_DEV:-0}`).
   - Create `deploy/compose/compose.instancer-production.yml`.
   - Add compose config verification tests.

3. **Step 3: R4 (Manager Concurrency & Failure Recovery)**
   - Write tests in `services/instancer/tests/test_manager_concurrency.py` with mock slow/hanging Docker.
   - Refactor `InstanceManager.start_instance` with `STARTING` reservation and fine-grained lock.
   - Implement orphan network label reconciliation and Docker timeouts.
   - Verify tests pass.

4. **Step 4: Regression & Integration Verification**
   - Run unit tests, tools tests, plugin tests, and `test_instances.py`.
   - Verify hygiene scanner: 0 findings.
   - Update `docs/handoff/instancer.md` and `docs/security/instancer-threat-model.md`.
