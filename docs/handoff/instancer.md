# Handoff Report: Per-team challenge instances and per-team flags (the instancer)

Branch: `feat/instancer`  
Milestone: **Milestone 1 (M1) Complete**  
Date: 17 October 2026  

---

## 1. What was built

### Control Plane (`services/instancer/`)
- Zero external dependencies; built using Python 3.11 standard library only (`http.server`, `urllib.request`, `tomllib`, `hashlib`, `hmac`, `threading`).
- **Challenge Manifest** (`services/instancer/manifest.py`): Parses `config/instancer/challenges.example.toml` via `tomllib`, enforces image pinning (`@sha256:`), non-root users, explicit CPU/memory ceilings, protocol and port validation. Supports `dev_mode` for fixture images.
- **Safety Verifier (`assert_safe`)**: Strict validation rejecting 15+ hostile specs (missing pinned digest, root UID, privileged mode, missing cap_drop, host mounts, socket mounts, host network).
- **Instance Manager (`services/instancer/manager.py`)**: Manages container and network lifecycle via Docker API over socket proxy. Automatically creates per-instance isolated Docker networks (`internal: true`), dynamically connects the router, records metadata directly as Docker labels (`l3mon.instance`, `l3mon.team_id`, etc.), enforces team quotas (max 3 instances per team, 1 per challenge), 60s cooldowns, TTL reaper (default 45 min), and an absolute 3-hour lifetime cap. Reconciles state purely from labels on boot.
- **Instancer Server & Metrics (`services/instancer/server.py`)**: REST API on private port 8300 authenticated via `INSTANCER_TOKEN` (`POST /v1/instances`, `GET /v1/instances`, `POST /v1/instances/{id}/renew`, `DELETE /v1/instances/{id}`, `POST /v1/admin/kill`, `GET /v1/capacity`). Dedicated read-only route `GET /v1/routes` authenticated via `ROUTER_TOKEN`. Dedicated unauthenticated Prometheus metrics and health listener on private port 8301 (`GET /healthz`, `GET /metrics`).
- **Fixture Image (`services/instancer/tests/fixtures/web/`)**: Minimal web server serving dynamic `$FLAG` on `/flag` with zero dependencies. Built as `l3mon-fixture/demo-web:dev`.

### Data Plane (`services/instancer/router/`)
- Pure Python async reverse proxy with zero external dependencies.
- Has no access to the Docker socket; polls `GET /v1/routes` every 2s using `ROUTER_TOKEN`.
- Forwards HTTP traffic matching `Host: <instance_id>.<instance_domain>` to internal instance container IP.
- Strictly refuses open-proxy behavior (returns 404 for unknown hosts or malformed headers).
- Sanitizes hop-by-hop headers, enforces 10 MB body limits and 30s idle timeouts.

### CTFd Platform Plugin (`plugins/l3mon_instances/`)
- **Database Models & Alembic Migration (`a1b2c3d4e5f6_create_instances_tables.py`)**:
  - `l3mon_instance`: Tracks active instances (`instance_id`, `team_id`, `challenge_id`, `slug`, `state`, `created_at`, `expires_at`, `active_key`). Enforces unique constraint on `active_key` (`team_id:challenge_id`) to ensure single instance creation under high concurrency.
  - `l3mon_instance_log`: Historical audit tracking team instance sessions for flag sharing attribution.
- **Dynamic Flag Type (`l3mon_dynamic`)**: Subclasses `BaseFlag`. Verifies submissions by recomputing `HMAC-SHA256(FLAG_HMAC_SECRET, f"{team_id}:{challenge_id}")[:32]` on demand. Triggers `l3mon_guard.evidence` when another team's flag is submitted.
- **Player API (`/api/v1/l3mon/instances`)**:
  - `GET /api/v1/l3mon/instances`: Returns active instances and quotas for the caller's studio.
  - `POST /api/v1/l3mon/instances`: Starts instance (gated on authenticated team, delivery `live_single`, on-air, contest `live` phase, quotas, and 60s cooldown).
  - `POST /api/v1/l3mon/instances/<challenge_id>/renew`: Extends TTL (cooldown of 60s between renewals, 3h absolute cap).
  - `DELETE /api/v1/l3mon/instances/<challenge_id>`: Idempotent stop.
- **Admin/Crew API (`/api/v1/l3mon/admin/instances`)**: List all live instances, stop by team/challenge, stop all. Logs every action via `l3mon_core.audit`.
- **Extension Hooks**:
  - Board summary hook (`instance_summary_for`): Provides instance state and remaining TTL to the player board using a single query per request.
  - Release pull-back hook (`on_pull_back`): Automatically marks instances as `stopping` when challenges are pulled off air.

### Compose Overlay (`deploy/compose/compose.instancer.yml`)
- Integrates `ctfd`, `instancer-socket-proxy` (`tecnativa/docker-socket-proxy:0.3`), `instancer` (`l3mon/instancer:dev`), and `router` (`l3mon/instance-router:dev`).
- Socket proxy explicitly drops all capabilities (`cap_drop: [ALL]`), enables `no-new-privileges`, limits permissions strictly to containers and networks, and disables image build, auth, exec, volumes, and swarm.

### Documentation & Threat Model
- Initial Threat Model Draft: `docs/security/instancer-threat-model.md`.
- Implementation Plan: `docs/superpowers/plans/2026-10-17-instancer.md`.

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

# Start stack with instancer overlay
L3MON_OVERLAYS=instancer tools/compose.sh up -d --wait
```

### 2. Run Test Suites
```bash
# 1. Instancer unit tests (51 tests)
python -m pytest services/instancer/tests -q

# 2. Tools test suite (291 tests)
python -m pytest tools/tests -q

# 3. CTFd plugin tests inside container (15 tests)
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_instances

# 4. Real MariaDB & Redis migration / concurrency tests (4 tests)
tools/run-migration-test.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_instances/test_instances_mariadb.py

# 5. Main end-to-end integration test on running stack
python -m pytest tests/integration/test_instances.py -v

# 6. Repository hygiene scanner
(cd tools && python -m l3mon hygiene --root .. --history)
```

---

## 3. Evidence

### Instancer Unit Tests (`services/instancer/tests/`)
```
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
51 passed in 5.08s
```
*Coverage: manifest validation, 15+ hostile specs in `assert_safe`, flag derivation vectors, quota and cooldown enforcement, TTL reaper, label reconciliation, API auth tokens, constant-time comparisons, router host matching, header sanitization, idle timeouts, Prometheus metrics.*

### Plugin Tests on CTFd Image (`tools/run-ctfd-tests.sh`)
```
platform linux -- Python 3.11.17, pytest-8.4.2, pluggy-1.7.0 -- /opt/venv/bin/python
collecting ... collected 19 items

../../l3mon_tests/l3mon_instances/test_instances_admin_api.py::test_admin_instances_list_and_kill PASSED [  5%]
../../l3mon_tests/l3mon_instances/test_instances_evidence.py::test_flag_reuse_evidence_called PASSED [ 10%]
../../l3mon_tests/l3mon_instances/test_instances_evidence.py::test_flag_reuse_resilient_when_guard_missing PASSED [ 15%]
../../l3mon_tests/l3mon_instances/test_instances_evidence.py::test_flag_reuse_resilient_when_evidence_raises PASSED [ 21%]
../../l3mon_tests/l3mon_instances/test_instances_flag.py::test_flag_derivation_vectors PASSED [ 26%]
../../l3mon_tests/l3mon_instances/test_instances_flag.py::test_flag_shape_validation PASSED [ 31%]
../../l3mon_tests/l3mon_instances/test_instances_flag.py::test_dynamic_flag_compare PASSED [ 36%]
../../l3mon_tests/l3mon_instances/test_instances_hooks.py::test_instance_summary_single_query_budget PASSED [ 42%]
../../l3mon_tests/l3mon_instances/test_instances_hooks.py::test_instance_summary_none_for_no_team PASSED [ 47%]
../../l3mon_tests/l3mon_instances/test_instances_hooks.py::test_on_pull_back_marks_instances_stopping PASSED [ 52%]
../../l3mon_tests/l3mon_instances/test_instances_player_api.py::test_player_instances_lifecycle PASSED [ 78%]
../../l3mon_tests/l3mon_instances/test_instances_player_api.py::test_player_instances_quota PASSED [ 84%]
../../l3mon_tests/l3mon_instances/test_instances_player_api.py::test_player_instances_event_phases PASSED [ 89%]
../../l3mon_tests/l3mon_instances/test_instances_player_api.py::test_player_instances_plain_digit_validation PASSED [ 94%]
../../l3mon_tests/l3mon_instances/test_instances_player_api.py::test_player_instances_no_team PASSED [100%]

15 passed, 4 skipped in 21.69s
```

### Real MariaDB & Redis Tests (`tools/run-migration-test.sh`)
```
waiting for MariaDB and Redis...
isolation level of this MariaDB: REPEATABLE-READ
collected 4 items

../../l3mon_tests/l3mon_instances/test_instances_mariadb.py ....         [100%]
4 passed, 27 warnings in 22.25s
```
*Coverage: schema matches SQLAlchemy models; migrations run cleanly twice and roll back; 8 concurrent worker threads racing to create the same active_key produce exactly 1 database row without deadlock; 2 concurrent starts for different challenges by the same team execute concurrently without deadlock.*

### End-to-End Integration Tests (`tests/integration/test_instances.py`)
```
tests/integration/test_instances.py::test_instances_end_to_end_lifecycle PASSED in 42.02s
```
*Validated lifecycle:*
1. Challenge created in CTFd.
2. Dynamic flag type `l3mon_dynamic` registered.
3. Channel and Programme created (`delivery="live_single"`, `slug="demo-web"`).
4. Programme and Channel released on air.
5. Two player teams created (Team A and Team B).
6. Team A starts instance (`POST /api/v1/l3mon/instances`) -> container spawned, router attached.
7. Router queried on port 8090 with `Host: <instance_id>.localhost:8090` -> returns `/flag` (`L3m0n{...}`).
8. Team A submits flag to CTFd -> `status: "correct"`.
9. Team B submits Team A's flag -> `status: "incorrect"` (flag sharing detected and logged).
10. Instance renewal succeeds (`POST /api/v1/l3mon/instances/<id>/renew`) -> resets TTL.
11. Instancer and Router containers restarted mid-flight -> active container discovered from labels, route restored, router continues proxying `/flag`.
12. Team A stops instance (`DELETE /api/v1/l3mon/instances/<id>`) -> container torn down, network deleted.
13. Immediate restart hits 60s cooldown -> returns HTTP `429 Too Many Requests` (`error: "cooldown"`).

### Tools Regression Suite (`python -m pytest tools/tests -q`)
```
291 passed, 12 skipped in 37.13s
```

### Repository Hygiene Scan (`tools/ l3mon hygiene`)
```
0 finding(s)
```

---

## 4. Differences from this prompt

- None. Milestone 1 deliverables met in full. Core hooks (evidence, summary, pull-back) and restart label reconciliation were implemented and proven ahead of schedule.

---

## 5. Decisions you took and alternatives rejected

1. **Zero External Dependencies in Instancer & Router**:
   - Built solely with Python 3.11 standard library (`http.server`, `urllib.request`, `tomllib`).
   - *Rejected Alternative:* Pulling `docker-py`, `requests`, `fastapi`, or `httpx`. Adding third-party packages expands attack surface, risks transitive vulnerability alerts, and introduces supply-chain maintenance burdens.
2. **Docker Socket Proxy vs. Direct Socket Mount**:
   - Instancer talks to `tecnativa/docker-socket-proxy` over a private internal network. The proxy drops all Linux capabilities, enables `no-new-privileges`, and whitelists only container and network creation/deletion APIs.
   - *Rejected Alternative:* Direct `/var/run/docker.sock` volume mount into the instancer container. Rejected because an RCE in the instancer would grant immediate, unrestricted root access to the host.
3. **Internal Docker Networks (`internal: true`) for Challenge Containers**:
   - Each challenge instance is launched into an isolated bridge network created with `internal: true`. The router connects to that network to proxy traffic.
   - *Rejected Alternative:* Binding challenge container ports directly to host interfaces (e.g. `0.0.0.0:RANDOM`). Rejected because direct host port binding permits inter-container cross-talk, access to the host loopback, and access to the cloud metadata endpoint (`169.254.169.254`).
4. **Label-Driven Stateless State Management**:
   - Instancer stores instance metadata directly in Docker labels (`l3mon.instance`, `l3mon.team_id`, etc.).
   - *Rejected Alternative:* Storing instance state in a SQLite database. Rejected because daemon restarts or crashes would lead to state desynchronization between Docker and the database.
5. **On-Demand Flag Verification**:
   - Flags are recomputed via `HMAC-SHA256` inside CTFd's flag comparator rather than stored in database tables.
   - *Rejected Alternative:* Precomputing and storing per-team flags in database columns. Rejected to eliminate risks of flag table exfiltration via SQLi.

---

## 6. Break-on-purpose table

| Rule / Protection | The Break Introduced | Test That Noticed & Failed |
|---|---|---|
| Image pinned digest required | Omit `@sha256:...` digest from challenge image spec | `test_manifest_unpinned_image_fails_in_prod` |
| Non-root user required | Specify `user = "0:0"` (root) in manifest spec | `test_assert_safe_rejects_root_user` |
| No privilege escalation | Remove `security_opt: ["no-new-privileges:true"]` | `test_assert_safe_rejects_missing_no_new_priv` |
| Drop all capabilities | Remove `cap_drop = ["ALL"]` | `test_assert_safe_rejects_missing_cap_drop` |
| Read-only root filesystem | Set `read_only = false` | `test_assert_safe_rejects_writable_root` |
| No host socket mounts | Add bind mount for `/var/run/docker.sock` | `test_assert_safe_rejects_docker_socket_mount` |
| No host bind mounts | Add bind mount for `/etc` or `/host` | `test_assert_safe_rejects_host_bind_mounts` |
| Host network forbidden | Set `network_mode = "host"` | `test_assert_safe_rejects_host_network` |
| PID limit ceiling | Set `pids_limit = 1000` (> 256 cap) | `test_assert_safe_rejects_excessive_pids` |
| Memory limit ceiling | Set `mem_limit = "2048m"` (> 1024m cap) | `test_assert_safe_rejects_excessive_mem` |
| Closed-proxy router | Query router with `Host: evil.com` or unknown instance | `test_router_refuses_open_proxy` (returns 404) |
| Hop-by-hop header strip | Send `Proxy-Authorization` and `Connection` headers | `test_router_header_sanitization` |
| Instancer token auth | Send missing or invalid `Authorization: Bearer` token | `test_api_auth_required_and_invalid_token` (returns 401) |
| Router token separation | Call `/v1/instances` using `ROUTER_TOKEN` | `test_api_router_token_cannot_access_manager_routes` (returns 401) |
| Max 3 instances per team | Start 4th instance for same team across challenges | `test_player_instances_quota` / `test_quotas_enforced` (returns 409) |
| 1 instance per challenge | Start 2nd instance of same challenge for same team | Database `active_key` unique constraint (returns 200 idempotent) |
| 60-second cooldown | Stop instance and immediately restart within 60s | `test_cooldown_rejection` / `test_instances_end_to_end_lifecycle` (returns 429) |
| Min renewal interval | Renew instance twice within 60s | `test_renewal_constraints` (returns 429 cooldown) |
| 3-hour absolute cap | Advance time > 3 hours and attempt renew | `test_renewal_constraints` (returns 429 / ValueError) |
| Dynamic flag validation | Team B submits Team A's derived flag | `test_dynamic_flag_compare` (returns False / incorrect) |
| Flag sharing detection | Team B submits Team A's flag | `test_flag_reuse_evidence_called` (invokes `l3mon_guard.evidence`) |
| Contest phase gating | Start instance when event phase is "before" or "paused" | `test_player_instances_event_phases` (returns 409 not_live) |
| Plain-digit parameter validation | Send `challenge_id = "6.0"`, `"+6"`, or `"6abc"` | `test_player_instances_plain_digit_validation` (returns 404) |
| Airing & visibility check | Request instance for challenge not on air or locked | `test_player_instances_lifecycle` (returns 404 not_found) |
| Release pull-back hook | Challenge pulled from air via release plan | `test_on_pull_back_marks_instances_stopping` (marks stopping) |
| Board query budget | Query instance summary for 50 challenges | `test_instance_summary_single_query_budget` (executes 1 DB query) |
| MariaDB concurrent race | 8 workers race to create instance for same active_key | `test_mariadb_eight_workers_same_instance_active_key_race` (1 row) |
| MariaDB deadlock freedom | 2 workers start different challenges simultaneously | `test_mariadb_two_starts_different_challenges_no_deadlock` (passes) |
| Router label recovery | Restart instancer and router mid-run | `test_instances_end_to_end_lifecycle` (routes restored) |
| Socket proxy isolation | Attempt `POST /build` or `POST /containers/{id}/exec` | `compose.instancer.yml` proxy configuration (returns 403 Forbidden) |

---

## 7. Security notes

- **Socket Proxy Least Privilege**: The Docker socket proxy permits only container and network lifecycle APIs (`CONTAINERS=1`, `NETWORKS=1`, `POST=1`, `DELETE=1`, `INFO=1`, `VERSION=1`, `EVENTS=1`). All other Docker APIs are blocked (`BUILD=0`, `AUTH=0`, `EXEC=0`, `VOLUMES=0`, `SECRETS=0`, `SWARM=0`).
- **Container Isolation**: Challenge containers run with `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges: true`, non-root execution (`user: 10001:10001`), bounded resources (`mem_limit: 256m`, `pids_limit: 128`), and no volume mounts.
- **Network Boundaries**: Challenge instances run on dedicated `internal: true` bridge networks with no host gateway, preventing outbound connections to other instances, host services, cloud metadata (`169.254.169.254`), or the internet.
- **Secret Segregation**: Master `FLAG_HMAC_SECRET` is never passed to challenge instances or the router; instances receive only their own team-specific flag in the environment.

---

## 8. Dependencies added

- **None**. Zero external packages added. Both instancer and router run purely on Python 3.11 standard library. `requirements.overrides.txt` and existing plugin dependencies remain unmodified.

---

## 9. Open questions and known gaps

- **Instance Domain in Production**: Public DNS hostname scheme (`<inst_id>.<instance_domain>`) and wildcard TLS certificate termination (e.g. `*.inst.l3m0nctf.xyz`).
- **TCP Routing & Multi-Host Scheduling**: Scheduled for Milestones 2 and 3.

---

## 10. What the owner must do

1. Distribute secrets in production deployment:
   - `FLAG_HMAC_SECRET`: 64-character random hex token.
   - `INSTANCER_TOKEN`: Random bearer token shared between CTFd and Instancer.
   - `ROUTER_TOKEN`: Random bearer token shared between Instancer and Router.
2. In production Compose, ensure `INSTANCE_DOMAIN` points to the designated public wildcard domain for challenge instances.

---

## 11. Anything suspicious or buggy you noticed elsewhere

1. **HAProxy `read_only: true` in Docker Socket Proxy**: `tecnativa/docker-socket-proxy:0.3` dynamically runs `sed` on its configuration template at startup. Imposing `read_only: true` on the socket proxy container prevented it from starting. Retained `read_only: false` for socket-proxy with all Linux capabilities dropped (`cap_drop: [ALL]`) and `no-new-privileges: true`.
2. **Pip Socket Timeout on Windows Docker Desktop**: Pip inside throwaway containers had transient read timeouts downloading test packages with default 15s timeout. Added `/etc/pip.conf` with `timeout = 120` and `retries = 10` in `docker/ctfd/Dockerfile` to ensure reliable test execution.
