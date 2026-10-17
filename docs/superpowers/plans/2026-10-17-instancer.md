# SP4: Per-team challenge instances and per-team flags (implementation plan)

> **For agentic workers:** work milestone by milestone, task by task. Every test is written and **shown failing** before the code that makes it pass. All Python runs inside the platform image (`l3mon/ctfd:dev`) against CTFd's own test helpers (`tools/run-ctfd-tests.sh` or `tools/run-migration-test.sh`). A security test is only accepted when it has also been **shown failing with its guard switched off** (recorded in the break-on-purpose table). Commit with plain, descriptive messages and no co-author lines, on `feat/instancer`. Never commit secrets or real flags.

**Goal:** Provide isolated, ephemeral per-team challenge instances (web and pwn) with recomputed HMAC-SHA256 per-team flags, strict container isolation, quota enforcement, automatic TTL reaping, crash resilience from Docker labels, and seamless integration into the L3m0nCTF board, release engine, and guard evidence system.

**Architecture:**
1. **`services/instancer/`**: Microservice listening on private port 8300 (auth via `INSTANCER_TOKEN`) and metrics on 8301. Communicates with Docker through a hardened socket proxy (`tecnativa/docker-socket-proxy`). Enforces `assert_safe()` on all container specs, applies labels as source of truth, manages dedicated internal networks per container, attaches the router, and reaps expired containers.
2. **`services/instancer/router/`**: Network-isolated reverse proxy without Docker access. Polls `GET /v1/routes` (auth via `ROUTER_TOKEN`) every 2 seconds. Routes HTTP by `Host: <instance_id>.<instance_domain>` and relays TCP across published ports (30000+). Strictly enforces closed-proxy constraints.
3. **`plugins/l3mon_instances/`**: CTFd platform plugin. Implements:
   - Player APIs (`/api/v1/l3mon/instances`) with strict state/phase/quota/cooldown gates.
   - Dynamic per-team flag type `l3mon_dynamic` verified via HMAC-SHA256 recomputation.
   - Flag sharing detection and evidence dispatch to `l3mon_guard.evidence`.
   - Hooks for board display (`instance_summary`) and release pull-back.
   - Admin/crew routes (`/api/v1/l3mon/admin/instances`) and audit logs.
   - Multi-host capacity scheduling and failover.
4. **Deploy & Ops**: Compose overlays (`deploy/compose/compose.instancer.yml`, `compose.challenge-host.yml`), host bootstrap script (`deploy/challenge-host/bootstrap.sh`), threat model, and handoff documentation.

---

## 1. Facts Measured and Read from Codebase

1. **Platform Image & Packaging**:
   - `docker/ctfd/Dockerfile`: Base image is pinned by digest `ghcr.io/ctfd/ctfd@sha256:07cc9788d58b2a18ea4dc2103ff385c053ffc506f57d0c769a99387849aedd01`.
   - Lines 33–37 copy `plugins/` to `/opt/CTFd/CTFd/plugins/` with `chown root:root` and mode 755/644, compiled with `compileall`. Running process is `USER 1001` (read-only filesystem).
   - No packages can be added to the CTFd image (`docker/ctfd/requirements.overrides.txt` is frozen).
2. **CTFd Version & Base Environment**:
   - `CTFd.plugins.l3mon_core.versions.check_ctfd_version()` enforces CTFd 3.8.8.
   - Python 3.11, Flask 2.1.3, Werkzeug 2.2.3, SQLAlchemy 1.4.
3. **Clock & Phase**:
   - `CTFd.plugins.l3mon_core.clock.now()` returns naive UTC datetime.
   - `CTFd.plugins.l3mon_core.clock.current_phase()` returns `"before"`, `"live"`, `"paused"`, or `"ended"`.
4. **Programmes, Visibility & Airing**:
   - `CTFd.plugins.l3mon_core.models.Programme` has `challenge_id`, `slug` (`^[a-z0-9][a-z0-9_-]{0,47}\Z`), `difficulty`, `delivery`.
   - Instance delivery type is `live_single`.
   - `CTFd.plugins.l3mon_core.airing.is_on_air(challenge_id)` and `CTFd.plugins.l3mon_core.visibility.is_visible(challenge_id, user_id=None, team_id=None)`.
5. **Concurrency & Coordination**:
   - `CTFd.plugins.l3mon_core.claim.claim(key, seconds)`: Single worker execution across multi-worker deployments.
   - `CTFd.plugins.l3mon_core.audit.record(action, target, detail="", actor=None)`.
   - `CTFd.plugins.l3mon_core.text.plain_text(s)` for plain text sanitization.
6. **API Contracts & Envelopes**:
   - `CTFd.plugins.l3mon_board.envelope.ok(data, ...)` -> `{"success": true, "data": ...}`.
   - `CTFd.plugins.l3mon_board.envelope.fail(code, status, message, ...)` -> `{"success": false, "error": code, "message": message, ...}`.
   - `CTFd.plugins.l3mon_board.viewer.player`, `viewer.current()`, `viewer.current_team()`.
7. **Board & Reconcile Hooks**:
   - `CTFd.plugins.l3mon_board.hooks.instance_summary`: callable `(team, challenge_id) -> dict | None` returning `{"state": "starting|running|stopping|expired|error", "expires_in": int, "ends": int}`.
   - `CTFd.plugins.l3mon_release.reconcile.register_pull_back_handler(fn)`: registers callback `fn(programme_ids)` when challenges leave air.
8. **Flag System Internals**:
   - CTFd flag classes subclass `CTFd.plugins.flags.BaseFlag` and define static `compare(chal_key_obj, provided)`.
   - Submitting team is retrieved via `CTFd.utils.user.get_current_team()` within `compare`.
   - Registered in `CTFd.plugins.flags.FLAG_CLASSES["l3mon_dynamic"]`.
9. **Secrets**:
   - `FLAG_HMAC_SECRET`, `INSTANCER_TOKEN`, `ROUTER_TOKEN` generated in `.secrets/`.
   - Container secrets mounted at `/run/secrets/<NAME>` with trailing/leading whitespace stripped.

---

## 2. Key Decisions & Alternatives Rejected

1. **Microservice Separation (Instancer vs. Router vs. CTFd)**:
   - *Decision:* Separate the instancer control plane from the router data plane. The instancer accesses Docker via a restricted socket proxy; the router has zero Docker access and only proxies HTTP/TCP traffic.
   - *Rejected Alternative:* Combining router and manager in one container. Rejected because an RCE in a challenge instance reaching the router would directly expose Docker control APIs.
2. **Container Safety Validation (`assert_safe`)**:
   - *Decision:* Strict centralized function `assert_safe(spec)` verifying 15+ hostile configurations before passing any container creation request to Docker. Spec creation is purely programmatic from the allow-listed manifest.
   - *Rejected Alternative:* Trusting input from API callers or allowing arbitrary image names/flags. Rejected because callers (even CTFd) must never execute arbitrary containers on the host.
3. **Network Isolation via Dedicated Internal Networks**:
   - *Decision:* Every instance runs on its own isolated Docker network created with `internal: true`. The router connects to that network dynamically on start and disconnects on stop. Instance containers expose ports internally without publishing them to the host.
   - *Rejected Alternative:* Publishing random host ports directly on instances. Rejected because internal networks prevent host-level port collisions and eliminate network routes to host, metadata service (169.254.169.254), or internet.
4. **State Storage & Recovery via Docker Labels**:
   - *Decision:* Store instance metadata (`l3mon.instance`, `l3mon.team_id`, `l3mon.challenge_id`, `l3mon.slug`, `l3mon.expires_at`, `l3mon.protocol`, `l3mon.port`) directly as container and network labels. On restart, the instancer reconciles state purely from labels.
   - *Rejected Alternative:* External state database (e.g. SQLite/PostgreSQL) in the instancer. Rejected to keep the service stateless, lean, and immune to state desynchronization with Docker daemon.
5. **Per-Team Flag HMAC Recomputation**:
   - *Decision:* Recompute HMAC-SHA256 `hex[:32]` on demand using `/run/secrets/FLAG_HMAC_SECRET` and `f"{team_id}:{challenge_id}"`. The challenge hosts only receive the individual team flag in `FLAG` env, never the master HMAC key.
   - *Rejected Alternative:* Pre-generating flags in a database table or sending the master secret to challenge hosts. Rejected to prevent secret leaks upon container escape.
6. **Flag Reuse Detection (`l3mon_guard`)**:
   - *Decision:* When a flag is submitted that does not match the current team's flag but matches the flag format `^L3m0n\{[0-9a-f]{32}\}\Z`, recompute candidate flags against teams that have held instances for that challenge (bounded to 2,000 teams). Safely invoke `CTFd.plugins.l3mon_guard.evidence.record` in a protected `try/except`.
   - *Rejected Alternative:* Scanning all teams across all challenges globally, or failing the submission request if the evidence recorder fails. Rejected for query budget and resilience.
7. **Router Implementation**:
   - *Decision:* Lightweight Python async proxy (`aiohttp` / `asyncio`) or minimal C/Go reverse proxy. Non-root, pinned dependencies, no external socket. Enforces strict hostname matching (`<id>.<instance_domain>`), WebSocket proxying, idle timeout, max body size (10 MB), and explicit open-proxy denial.
   - *Rejected Alternative:* Nginx reload-on-change. Reloading nginx config every 2 seconds under high churn causes worker leaks and dropped connections.

---

## 3. Interfaces & Data Contracts

### 3.1 Per-Team Flag Derivation
- Key: UTF-8 string from `/run/secrets/FLAG_HMAC_SECRET` (whitespace stripped).
- Message: `f"{team_id}:{challenge_id}"`.
- Output: `"L3m0n" + "{" + hmac.new(key, msg, hashlib.sha256).hexdigest()[:32] + "}"`.
- Test Vectors:
  - Key `test-secret-value`:
    - Team 7, Challenge 42 -> 32 hex chars `94fa853e968cd402000a376fae89470d`
    - Team 8, Challenge 42 -> 32 hex chars `d6c753d8dea03a34b65ee9e4676ce3c3`
    - Team 7, Challenge 43 -> 32 hex chars `435b00a57ad252e91c15db1238e489f6`

### 3.2 Instancer Internal API (Port 8300, Bearer Auth)
- `POST /v1/instances`: Body `{"team_id": int, "challenge_id": int, "slug": str, "flag": str}` -> `201 {"instance": {...}}` or `200 {"instance": {...}}` (idempotent). Errors: `404` (unknown slug), `409` (quota), `429` (cooldown), `503` (capacity).
- `GET /v1/instances?team_id=<id>` -> `{"instances": [...]}`.
- `GET /v1/instances/<id>` -> `{"instance": {...}}` or `404`.
- `POST /v1/instances/<id>/renew` -> `200 {"instance": {...}}` or `429` (min 60s since last renew or absolute 3h cap).
- `DELETE /v1/instances/<id>` -> `204`.
- `POST /v1/admin/kill`: Body `{"team_id"?: int, "challenge_id"?: int}` -> `{"stopped": int}`.
- `GET /v1/capacity` -> `{"slots_total": int, "slots_used": int, "mem_mb_total": int, "mem_mb_used": int}`.
- `GET /v1/routes` (Bearer `ROUTER_TOKEN`) -> `{"routes": [{"id": str, "protocol": str, "target_ip": str, "target_port": int, "port": int | null}]}`.
- Port 8301 (no auth): `GET /healthz`, `GET /metrics`.

### 3.3 Instance Schema
```json
{
  "id": "32_lowercase_hex_chars",
  "team_id": 7,
  "challenge_id": 42,
  "slug": "demo-web",
  "state": "starting|running|stopping|expired|error",
  "protocol": "http|tcp",
  "url": "http://<id>.<instance_domain>/",
  "host": "localhost",
  "port": 30001,
  "created_at": 1700000000,
  "expires_at": 1700002700,
  "renewals": 0
}
```

### 3.4 CTFd Player & Crew API (`plugins/l3mon_instances`)
- `GET /api/v1/l3mon/instances` -> `{"success": true, "data": {"instances": [...], "limits": {"live": n, "max": 3, "cooldown_seconds": 60}}}`
- `POST /api/v1/l3mon/instances` with `{"challenge_id": int}` -> starts or returns instance.
- `POST /api/v1/l3mon/instances/<int:challenge_id>/renew`
- `DELETE /api/v1/l3mon/instances/<int:challenge_id>`
- Admin:
  - `GET /api/v1/l3mon/admin/instances` -> all active instances.
  - `POST /api/v1/l3mon/admin/instances/kill` -> kill by team/challenge/all with audit log.

### 3.5 Database Schema (`l3mon_instances`)
- Table `l3mon_instance`:
  - `id`: Integer primary key.
  - `instance_id`: String(32), unique, indexed.
  - `team_id`: Integer, indexed.
  - `challenge_id`: Integer, indexed.
  - `host_name`: String(64).
  - `state`: String(16) (`starting`, `running`, `stopping`, `expired`, `error`).
  - `protocol`: String(8).
  - `url`: String(255).
  - `host`: String(128).
  - `port`: Integer, nullable.
  - `created_at`: DateTime.
  - `expires_at`: DateTime.
  - `stopped_at`: DateTime, nullable.
  - `last_renewed_at`: DateTime, nullable.
  - `renewals`: Integer default 0.
  - Unique constraint on active instances to enforce single instance per team per challenge.
- Table `l3mon_instance_log`:
  - Historical lookup table for flag sharing detection (maps `(team_id, challenge_id)` to past allocations).

---

## 4. Test Strategy

### 4.1 Unit Tests (`services/instancer/tests/`)
1. `test_flag_derivation.py`: Verify test vectors for HMAC derivation, hex slicing, case sensitivity, whitespace stripping.
2. `test_manifest.py`: Valid manifests, pinned digest validation, slug regex compliance, limit validation, reject unpinned/root.
3. `test_assert_safe.py`: 15+ hostile specs (privileged, capabilities, host network/pid/ipc, bind mounts, volume mounts, socket mounts, unauthorized security opts, unpinned images outside dev, root user, excessive RAM/CPU/pids).
4. `test_quotas_and_ttl.py`: Quota counters, cooldown timing, renewal spacing (60s min), absolute max lifetime (3h).
5. `test_auth_tokens.py`: Missing, invalid, valid tokens; constant-time comparison.
6. `test_router_http.py`: Host header routing, non-existent host static 404, hop-by-hop header stripping, WebSocket handshake forwarding, closed-proxy checks (`localhost`, private IP, `169.254.169.254`).
7. `test_router_tcp.py`: Port allocation, byte relay, timeout, connection limit.

### 4.2 Plugin Tests (`plugins/l3mon_instances/tests/`)
1. `test_instances_player_api.py`: Lifecycle (start, get, renew, stop), unauthorized access, player without team, locked/withheld challenges, event phases (before, live, paused, ended), plain-digit validation.
2. `test_instances_flag.py`: Dynamic flag comparison, team matching, whitespace handling, case sensitivity.
3. `test_instances_evidence.py`: Flag reuse detection trigger, graceful fallback when `l3mon_guard` missing or raising.
4. `test_instances_hooks.py`: Single-query budget ceiling test for `instance_summary`, release pull-back handler immediate return without blocking.
5. `test_instances_admin_api.py`: Crew routes, audit logging, filtering.

### 4.3 MariaDB & Concurrency Tests (`tools/run-migration-test.sh`)
1. `test_instances_migration_mariadb.py`: Upgrades, downgrades, idempotence, constraint verification on real MariaDB.
2. Concurrency test: 8 simultaneous requests starting the same challenge for one team produce exactly one instance and one database row.

### 4.4 Integration & Isolation Tests (`tests/integration/test_instances.py`)
1. End-to-end instance launch, HTTP routing through instance router, `/flag` verification via dynamic flag submission.
2. Team A vs. Team B flag submission refusal and evidence logging.
3. Recovery from Docker labels after simulated instancer restart.
4. Full isolation matrix: Prober container attempts to reach adjacent instances, host, gateway, internet, metadata IP (169.254.169.254), Docker daemon socket/ports, CTFd, Redis, MariaDB; all connections refused.

### 4.5 Break-on-Purpose Verification
- At least 30 deliberate rule breaks toggled in code, confirming specific test failures before restoration.

---

## 5. Milestone Breakdown

- **Milestone 1 (by Oct 24): HTTP Instances on One Host, End to End**
  - Instancer core service (manifest, `assert_safe`, API, Docker label manager, TTL reaper).
  - Router service (HTTP routing, open-proxy prevention).
  - Plugin `plugins/l3mon_instances` (models, migrations, player API, dynamic flag type).
  - Compose overlay `deploy/compose/compose.instancer.yml`.
  - Unit tests, plugin tests, and integration test suite.
  - First draft of threat model and handoff report.
- **Milestone 2 (by Oct 31): TCP, Hooks, Isolation Proof, Crew API**
  - TCP port-based routing.
  - Board hook (`instance_summary`) and release pull-back hook.
  - Flag reuse evidence bridge.
  - Crew admin API and audit log tracking.
  - Rigorous network isolation test suite.
  - 30-item break-on-purpose validation table.
- **Milestone 3 (by Nov 5): Multi-Host, Packing, Host Bootstrap, Final Documentation**
  - Multi-host capacity scheduling and failover.
  - Host bootstrap script (`deploy/challenge-host/bootstrap.sh`).
  - Packing test script (`services/instancer/tools/pack_test.py`).
  - Final threat model (`docs/security/instancer-threat-model.md`) and operations guide (`docs/deploy/instancer.md`).
