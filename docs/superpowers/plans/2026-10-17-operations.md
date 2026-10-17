# SP9: Operations and Resilience (Implementation Plan)

> Built test-first, verified on the real stack, and proved with intentional failure injection. Every rule, limit, and failure mode has a test that catches it.

**Goal.** Deliver the operations and resilience subsystem for L3m0nCTF 2026 across four milestones (M1 Stay-up behaviour, M2 Survive a disaster, M3 Be operable, M4 Be observable):
1. **Health and busy behaviour**: `plugins/l3mon_ops` (`/readyz`, internal `/l3mon/metrics`), nginx Level 1 busy mode (concurrency cap, dedicated flag submission cap, static error slates) and Level 3 maintenance mode (flag file, crew bypass, countdown).
2. **Backups & point-in-time recovery**: encrypted (`age` public key), frequent (30m full dump, 1m binlog shipping), off-host capable (`file://` and `gs://`), with a verified ≤15m restore drill and textfile collector heartbeat.
3. **Internal mail relay**: queueing SMTP smarthost, rate limits, no open relay, queue monitoring.
4. **Monitoring & alerts**: Prometheus, Grafana, Loki + log shipper, Alertmanager to Discord, exporters (node, cAdvisor, Redis, MariaDB read-only user, internal nginx `stub_status`, blackbox), promtool-tested alert rules, and `fire_drill.sh`.
5. **Load testing & host preflight**: k6 load test suite with strict target allow-lists and `zzload` cleanup; read-only `preflight.sh`.
6. **Runbooks & dress-rehearsal kit**: complete operational runbooks and synthetic drill runner.

**Freeze Date & Timeline**:
- 17 October 2026: Plan committed, M1 initiated.
- Milestone 1 (M1, target 24 October): Stay-up behaviour (plugin, nginx busy/maintenance, slates, tests).
- Milestone 2 (M2, target 29 October): Survive a disaster (encrypted backups, binlogs, restore drill, mail relay).
- Milestone 3 (M3, target 3 November): Be operable (runbooks, preflight, dress rehearsal, k6 suite).
- Milestone 4 (M4, target 7 November): Be observable (monitoring overlay, exporters, alert rules, fire drill, handoff).
- 21 November 2026: Feature freeze.

---

## 1. Baseline Measurements and Verified Facts

Measured on 17 October 2026 on the local development environment (Git Bash MINGW64 on Windows, Docker Compose v2, Python 3.12, CTFd 3.8.8 image `l3mon/ctfd:dev`):

| Scope | Command | Result |
|---|---|---|
| Repository Hygiene | `(cd tools && python -m l3mon hygiene --root .. --history)` | **0 finding(s)** |
| Tools Test Suite | `python -m pytest tools/tests -q` | **291 passed, 12 skipped** in 23.89s |
| Integration Suite (Stock Stack) | `python -m pytest tests/integration -q` | **115 passed, 4 skipped** in 305.99s |
| CTFd Plugin Test Runner | `run-ctfd-tests.sh l3mon/ctfd:dev /l3mon_tests/l3mon_ctftime` | **132 passed** in 194.57s |

### Architectural Facts from Existing Files
- **CTFd Version Guard** (`plugins/l3mon_core/versions.py:8-22`): pinned to CTFd `3.8.8`. Plugins must invoke `check_ctfd_version()` at initialization.
- **Nginx Architecture** (`deploy/nginx/nginx.conf:55-116`):
  - Cloudflare RealIP mapping (`cloudflare-realip.conf`).
  - Organiser IP detection (`geo $admin_net` via `admin-nets.conf`).
  - Administration surface gating (`map $api_admin_surface` blocks unauthorized non-player writes with 403).
  - Rate limiting zones (`general: 100r/s`, `login: 120r/m`, `flags: 1200r/m`, `perip: 300 conns`).
  - Upstream `ctfd_app` pointing to `ctfd:8000` with keepalive 64.
- **Nginx Server Directives** (`deploy/nginx/snippets/platform-server.conf:1-115`):
  - Health checks: `/healthcheck` and `/l3mon/healthz` (hide `Set-Cookie`, `Cache-Control: no-store`, `access_log off`).
  - Static snippets mount: `compose.base.yml:150` mounts `../nginx/snippets:/etc/nginx/snippets:ro`. Putting slates under `deploy/nginx/snippets/slates/` guarantees they are present across base, dev, and production without changing base compose.
- **MariaDB Configuration** (`deploy/mariadb/l3mon.cnf:1-26`):
  - `max_connections = 400`, `innodb_buffer_pool_size = 384M`.
  - Currently no `log-bin` enabled. Point-in-time recovery requires enabling binary logging.
- **Backup & Restore Baseline** (`tools/backup.sh:1-49`, `tools/restore.sh:1-45`):
  - `backup.sh`: dumps MariaDB (`mariadb-dump --single-transaction --routines --events`) to `db.sql.gz`, tars `/var/uploads` to `uploads.tar.gz`, verifies dump header/trailer, writes sha256 checksums to `manifest.json`.
  - `restore.sh`: checks sha256 checksums, stops `nginx ctfd`, drops & recreates `ctfd` DB, loads SQL, unpacks uploads, flushes Redis cache, restarts services.
  - Drill (`tests/integration/test_restore_drill.py:1-139`): automated recovery drill asserting data integrity and measuring recovery time against a 15-minute budget.
- **Mail Configuration** (`config/event.example.toml:31-39`, `deploy/compose/compose.production.yml:22-30`):
  - CTFd expects `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USEAUTH`, `MAIL_USERNAME`, `MAIL_TLS`, `MAIL_SSL`, `MAILFROM_ADDR`.
  - Secret `MAIL_PASSWORD` mounted at `/run/secrets/MAIL_PASSWORD`.

---

## 2. Milestone 1: Stay-up Behaviour (M1)

### 2.1 Plugin `plugins/l3mon_ops`
- **Purpose**: Minimal operational endpoints running inside CTFd.
- **Routes**:
  - `GET /readyz`:
    - Checks DB connectivity with `SELECT 1` with a strict 1-second timeout.
    - Checks Redis connectivity with `PING` with a strict 1-second timeout.
    - If both succeed: returns `200 {"status": "ready"}` with `Cache-Control: no-store`.
    - If either fails or times out: returns `503 {"status": "not_ready"}` with `Cache-Control: no-store`.
    - Leaks zero details: no exception strings, no hostnames, no stack traces.
    - No cookies set.
  - `GET /l3mon/metrics`:
    - Prometheus text format (`text/plain; version=0.0.4; charset=utf-8`).
    - Gated by network: Nginx blocks this route for all external traffic (returns 404). Only reachable via internal Docker network (`ops` or `internal`).
    - Handles CTFd `TRUSTED_HOSTS` constraint: Prometheus scrape configuration or internal proxy passes trusted `Host` header.
    - Cheap counters/gauges cached for 15 seconds:
      - `l3mon_event_phase{phase="..."} 1` (from `l3mon_core.clock.current_phase()`)
      - `l3mon_tick_value` (current tick integer)
      - `l3mon_solves_last_minute`
      - `l3mon_wrong_attempts_last_minute`
      - `l3mon_registrations_last_minute`
      - `l3mon_build_info{version="..."} 1`
    - Strictly **no** team names, user IDs, challenge slugs, or flags in labels or values.
  - **Overhead**: Zero query overhead on other routes (`test_ops_hooks.py` ensures 0 queries added to standard requests).

### 2.2 Nginx Busy Mode (Level 1) & Maintenance Mode (Level 3)
- **Level 1 (Busy Mode / Load Shedding)**:
  - Global application concurrency limit: `limit_conn_zone $server_name zone=app_conn:10m;` or dedicated queue limit.
  - Dedicated flag submission limit: `/api/v1/challenges/attempt` has its own concurrency pool so flag submissions are never starved by high-volume page requests.
  - Excess requests receive `503 Service Unavailable` with `Retry-After: 15`.
  - Content-negotiated error responses:
    - HTML request (browser): Serves static `/etc/nginx/snippets/slates/busy.html` featuring a calm layout, event name, retry bar, `<meta http-equiv="refresh" content="15">`, and `$request_id`.
    - API request (`/api/...` or `Accept: application/json`): Serves `{"error": "busy", "retry_after": 15}`.
  - Upstream failures (502 Bad Gateway / 504 Gateway Timeout when CTFd is restarting or down):
    - HTML: Serves static `/etc/nginx/snippets/slates/down.html` with auto-retry.
    - API: Serves `{"error": "restarting"}`.
- **Level 3 (Maintenance Mode)**:
  - Flag file: `/etc/l3mon/maintenance` (managed via `tools/ops/maintenance.sh`).
  - When flag file is present:
    - Serves `/etc/nginx/snippets/slates/maintenance.html` (or JSON for API).
    - Exemption 1: Organisers (`$admin_net = 1`) pass through unimpeded to continue operations.
    - Exemption 2: Health routes (`/healthcheck`, `/l3mon/healthz`, `/readyz`) pass through to monitor underlying health.
    - Exemption 3: CTFtime feeds (`/ctftime/...`) pass through.
- **The Slates** (`deploy/nginx/snippets/slates/`):
  - `busy.html`, `down.html`, `maintenance.html`, and `slate.css`.
  - WCAG AA contrast, responsive (tested at 375px), system fonts, zero inline script/style, zero external assets.
  - `Cache-Control: no-store`.

### 2.3 Maintenance CLI (`tools/ops/maintenance.sh`)
- Syntax: `tools/ops/maintenance.sh on|off [--until <ISO time>] [--message "<plain text>"]`
- Writes atomically via temporary file and `mv` to `/etc/l3mon/maintenance`.
- Removes flag file cleanly on `off`.

---

## 3. Milestone 2: Survive a Disaster (M2)

### 3.1 Point-in-Time Recovery & Binary Logging
- **MariaDB Configuration Update** (`deploy/mariadb/l3mon.cnf`):
  - Enable binary logging: `log-bin = /var/lib/mysql/mariadb-bin`
  - Binary log format: `binlog_format = ROW`
  - Retention: `expire_logs_days = 2` (or `binlog_expire_logs_seconds = 172800`)
- **Automated Backup & Shipping** (`tools/ops/backup_loop.sh` & `tools/ops/ship_binlogs.sh`):
  - Full dump (DB + uploads via existing `tools/backup.sh`) every 30 minutes.
  - MariaDB binlogs shipped every 60 seconds via `mariadb-binlog` / flush.
  - Encryption with `age`:
    - Uses public key in `config/backup/recipients.txt` (example provided in `config/backup/recipients.example.txt`).
    - Private key is never placed on the server or in repository.
  - Pluggable sinks:
    - `file://<dir>` (local filesystem, used for tests and verification).
    - `gs://<bucket>` (Google Cloud Storage, VM service account write-only).
  - Heartbeat metrics written to textfile collector:
    - `l3mon_backup_last_success_timestamp_seconds`
    - `l3mon_backup_duration_seconds`
    - `l3mon_backup_size_bytes`
- **PITR Restore Script** (`tools/ops/restore_pitr.sh`):
  - Reconstructs state to a target timestamp:
    1. Unpacks & decrypts latest full backup prior to target time.
    2. Runs `tools/restore.sh --force`.
    3. Replays decrypted binary logs up to `--until-time` using `mariadb-binlog`.
- **Restore Drill Automated Verification** (`tests/integration/test_ops_backup.py --run-drill`):
  - Times end-to-end recovery against a 15-minute SLA.

### 3.2 Internal Mail Relay
- **Architecture**:
  - Container `mail-relay` on `internal` network.
  - Image: Minimal, official, digest-pinned image (e.g. `boky/postfix` or Alpine postfix daemon).
  - Open only to CTFd on `internal` network. External connection attempts refused.
  - Spools mail to disk with retry backoff for 24+ hours.
  - Smarthost outbound delivery via STARTTLS/TLS using `MAIL_PASSWORD` secret.
  - Rate limiting outbound delivery (per-minute and daily caps) to prevent provider throttling.
  - Exposes queue length metric for Prometheus monitoring.
- **Overlay Integration** (`compose.ops.yml`):
  - Sets `MAIL_SERVER=mail-relay`, `MAIL_PORT=25`, clears `MAIL_USEAUTH`, `MAIL_TLS`, `MAIL_SSL`.
  - Test harness includes mock SMTP sink container for integration testing.

---

## 4. Milestone 3: Be Operable (M3)

### 4.1 Host Pre-flight Script (`tools/ops/preflight.sh`)
- Executable, read-only system audit:
  - Free disk space (>20% free) and Docker json-log rotation active.
  - Time synchronization verification (`chronyc tracking` or `timedatectl`).
  - Private secrets validation (mode 0700/0600 on `.secrets/`, no leaked credentials).
  - Secret immutability warning for `FLAG_HMAC_SECRET`.
  - Port listening audit (`ss -ltnp` / `netstat`: only 8080 or 8443 exposed).
  - Metadata address block verification (`169.254.169.254` unreachable from containers).
  - Outbound network egress check for CTFd (internal containers cannot route to general internet).
  - Docker Compose service health state (`tools/compose.sh ps`).
  - Backup freshness (<2 hours old).
  - Mail queue state.
  - Maintenance mode status.
  - Execution of `python -m l3mon deploy check`.
- Passes `shellcheck`, clean POSIX compatibility for Linux and Git Bash.

### 4.2 Load Testing Suite (`tests/load/`)
- Powered by official pinned `grafana/k6` container image.
- **Target Allow-List Safety Guard**: Refuses to run if target host is not in allow-list (`localhost`, `127.0.0.1`, `play.l3m0nctf.xyz` when explicitly added).
- **Test Account Isolation**: All generated test accounts prefixed `zzload_*`.
- Automated cleanup subcommand/script.
- Scenarios:
  1. Registration burst (4,000 page loads in 60s).
  2. Tick polling (15s intervals with jitter, board/guide refetch on tick increment).
  3. Flag submission burst (up to 140 attempts/s).
  4. Scoreboard view contention.
- Outputs structured JSON performance metrics (p50, p95, p99, error rates).

### 4.3 Operational Runbooks (`docs/runbooks/*.md`)
Step-by-step, copy-paste executable commands with expected outputs and escalation paths:
1. `01-event-start.md`: Pre-flight checklist, health verification, opening the doors.
2. `02-app-down.md`: CTFd worker crashes, restart procedures, log inspection.
3. `03-db-disaster-restore.md`: Database corruption, PITR restoration from backup + binlogs.
4. `04-redis-failure.md`: Cache failure, session invalidation impact, recovery steps.
5. `05-ddos-and-flood.md`: Under Attack mode, rate adjustments, busy mode activation.
6. `06-scoreboard-freeze.md`: Freeze procedure, verification, unfreeze and standings publication.
7. `07-emergency-announcement.md`: Crew notification API broadcast instructions.
8. `08-instance-management.md`: Emergency instance cleanup (`POST /api/v1/l3mon/admin/instances`).
9. `09-secret-rotation.md`: Impact matrix and procedures for rotating credentials.
10. `10-rollback-deployment.md`: Container rollback and image reversion.
11. `11-maintenance-mode.md`: Maintenance mode activation and crew bypass verification.
12. `12-ctftime-standings.md`: Final standings validation and feed publication.
13. `13-crew-access.md`: Adding/managing organiser accounts safely.
14. `14-contacts.md`: Escalation hierarchy template.

### 4.4 Dress-Rehearsal Kit (`docs/runbooks/dress-rehearsal.md`, `tests/integration/test_ops_drills.py`)
- Timeline from T-60m to T+12h.
- Automated fault injection drills (kill CTFd worker, kill Redis, kill MariaDB, simulate disk fill, stale CTFtime feed).

---

## 5. Milestone 4: Be Observable (M4)

### 5.1 Monitoring Overlay (`deploy/compose/compose.ops.yml`)
- **Prometheus**:
  - Image pinned by digest.
  - Scrapes CTFd `/l3mon/metrics`, node-exporter, cAdvisor, Redis exporter, MariaDB exporter, Nginx exporter, blackbox exporter.
  - Retention 7 days.
- **Alertmanager**:
  - Pinned by digest.
  - Routes alerts to Discord webhook via `DISCORD_WEBHOOK_URL` secret.
  - Fails safe if secret is empty (holds alerts in memory, no crashing).
- **Grafana**:
  - Anonymous access disabled.
  - Admin password provisioned via `GRAFANA_ADMIN_PASSWORD`.
  - JSON dashboards provisioned for: Event Overview, Database & Redis, Hosts & Containers, Mail & Backups.
- **Loki & Promtail**:
  - Pinned by digest.
  - Scrapes nginx and CTFd logs, preserves token masking, 7-day retention.
- **Host Metrics Profile (`host-metrics`)**:
  - `node-exporter`, `cAdvisor`, `promtail` placed under profile `host-metrics`.
  - Minimum necessary read-only host mounts (`/proc:ro`, `/sys:ro`, `/var/lib/docker/containers:ro`).
  - Base monitoring stack operates fully without host profile.
- **Exporters**:
  - `nginx-exporter`: reads internal-only `stub_status` at `127.0.0.1:8080/__nginx_status`.
  - `mysqld-exporter`: connects with read-only user `l3mon_exporter` using `DB_EXPORTER_PASSWORD`.
  - `redis-exporter`: connects with `REDIS_PASSWORD` (works with `rename-command CONFIG ""`).
  - `blackbox-exporter`: probes `/readyz` and external endpoints.

### 5.2 Alerting Rules & Validation
- Alert rules in `deploy/ops/prometheus/alerts/*.yml`:
  - `High5xxRate`: 5xx errors > 1% over 2m.
  - `HighLatency`: p95 request duration > 500ms over 5m.
  - `DatabaseConnectionsHigh`: connections > 80% of `max_connections`.
  - `RedisMemoryHigh`: memory > 85% of limit.
  - `DiskSpaceLow`: host disk > 80% (warning) and > 90% (critical).
  - `ContainerRestarting`: container restarts detected.
  - `ReadyzFailing`: `/readyz` probe returning non-200.
  - `CTFtimeFeedStale`: CTFtime feed > 2 minutes stale.
  - `BackupStale`: newest successful backup > 2 hours old.
  - `MailQueueBacklog`: mail queue depth > 50 messages.
  - `CertificateExpiring`: TLS cert expiring in < 14 days.
- All alert rules verified via `promtool test rules`.
- **Alert Fire Drill** (`tools/ops/fire_drill.sh`): Fires every alert once on purpose to verify end-to-end delivery pipeline.

---

## 6. Break-on-Purpose Verification Matrix (≥ 25 scenarios)

| # | Protection / Limit / Rule | Intentional Break | Catching Test |
|---|---|---|---|
| 1 | `/readyz` DB failure | Stop DB container or corrupt DB socket | `test_ops_readyz.py::test_readyz_reports_503_on_db_down` |
| 2 | `/readyz` Redis failure | Stop Redis container or invalidate auth | `test_ops_readyz.py::test_readyz_reports_503_on_redis_down` |
| 3 | `/readyz` 1-second timeout | Inject 2s sleep in DB query | `test_ops_readyz.py::test_readyz_times_out_strictly` |
| 4 | `/readyz` zero error leakage | Inspect 503 response body | `test_ops_readyz.py::test_readyz_leaks_no_traceback_or_host` |
| 5 | `/l3mon/metrics` internal only | Request metrics from external IP via Nginx | `test_ops_metrics.py::test_metrics_forbidden_externally` |
| 6 | `/l3mon/metrics` no player data | Inspect exported Prometheus labels | `test_ops_metrics.py::test_metrics_has_no_sensitive_labels` |
| 7 | Zero hook overhead | Assert DB statement count on standard requests | `test_ops_hooks.py::test_hook_cost_zero_statements` |
| 8 | Busy mode 503 status | Saturate connection limit with slow requests | `test_ops_busy.py::test_busy_returns_503_with_retry_after` |
| 9 | Busy mode HTML slate | Request with `Accept: text/html` during saturation | `test_ops_busy.py::test_busy_serves_html_slate` |
| 10 | Busy mode API JSON | Request with `Accept: application/json` during saturation | `test_ops_busy.py::test_busy_serves_json_error` |
| 11 | Flag submission isolation | Saturate general pool, submit flag | `test_ops_busy.py::test_flag_submission_not_starved` |
| 12 | Upstream crash down slate | Terminate CTFd process | `test_ops_busy.py::test_upstream_down_serves_down_slate` |
| 13 | Maintenance mode gating | Write `/etc/l3mon/maintenance` | `test_ops_maintenance.py::test_maintenance_blocks_general` |
| 14 | Maintenance crew bypass | Request with organiser IP (`$admin_net=1`) | `test_ops_maintenance.py::test_crew_bypasses_maintenance` |
| 15 | Maintenance health bypass | Query `/readyz` and `/l3mon/healthz` during maintenance | `test_ops_maintenance.py::test_health_bypasses_maintenance` |
| 16 | Maintenance CTFtime bypass | Query `/ctftime/standings.json` during maintenance | `test_ops_maintenance.py::test_ctftime_bypasses_maintenance` |
| 17 | Encrypted backup requirement | Attempt decrypt without recipient key | `test_ops_backup.py::test_backup_encryption_enforced` |
| 18 | Backup manifest checksum | Tamper with 1 byte of `db.sql.gz` | `test_ops_backup.py::test_tampered_backup_rejected` |
| 19 | Binlog replay PITR | Delete record, replay binlogs to T-1s | `test_ops_backup.py::test_pitr_restores_exact_state` |
| 20 | Backup textfile heartbeat | Check timestamp metric freshness | `test_ops_backup.py::test_heartbeat_updated_on_success` |
| 21 | Mail relay no open relay | Attempt SMTP connection from edge network | `test_ops_mail.py::test_mail_relay_rejects_external` |
| 22 | Mail relay spooling | Stop upstream mail provider container | `test_ops_mail.py::test_mail_spools_when_upstream_down` |
| 23 | Mail relay rate limits | Burst 200 emails | `test_ops_mail.py::test_mail_rate_limit_enforced` |
| 24 | k6 target allow-list | Execute k6 against non-whitelisted domain | `test_ops_load.py::test_k6_refuses_unauthorized_target` |
| 25 | k6 cleanup prefix | Check accounts created by test | `test_ops_load.py::test_k6_names_strictly_zzload` |
| 26 | Preflight disk check | Simulate disk threshold >80% | `test_ops_preflight.py::test_preflight_catches_disk_full` |
| 27 | Preflight HMAC immutability | Mutate `FLAG_HMAC_SECRET` | `test_ops_preflight.py::test_preflight_warns_hmac_change` |
| 28 | Alertmanager empty secret | Remove webhook URL from secret | `test_ops_monitoring.py::test_alertmanager_fails_safe` |

---

## 7. Interfaces and Concrete Contracts

### 7.1 Endpoint Specifications
- **`GET /readyz`**:
  - Request: Empty body, no cookies required.
  - Response (Healthy): `200 OK`, `{"status": "ready"}`, `Content-Type: application/json`, `Cache-Control: no-store`.
  - Response (Unhealthy): `503 Service Unavailable`, `{"status": "not_ready"}`, `Content-Type: application/json`, `Cache-Control: no-store`.
- **`GET /l3mon/metrics`**:
  - Request: Internal IP only (Nginx returns 404 to non-internal requests).
  - Response: `200 OK`, `Content-Type: text/plain; version=0.0.4; charset=utf-8`, `Cache-Control: no-store`.
- **Static Slates (`/etc/nginx/snippets/slates/`)**:
  - `busy.html`: HTML5, `Retry-After: 15`, meta-refresh, request ID displayed.
  - `down.html`: HTML5, auto-retry, request ID displayed.
  - `maintenance.html`: HTML5, countdown display, request ID displayed.
  - JSON equivalents: `{"error": "busy", "retry_after": 15}`, `{"error": "restarting"}`, `{"error": "maintenance"}`.

### 7.2 Tool CLI Specifications
- `tools/ops/maintenance.sh on [--until <ISO>] [--message "<text>"]`
- `tools/ops/maintenance.sh off`
- `tools/ops/backup.sh [--sink <file://path|gs://bucket>]`
- `tools/ops/restore_pitr.sh --backup <path> --until-time <ISO> --force`
- `tools/ops/preflight.sh [--json]`
- `tools/ops/fire_drill.sh [--alert <name>|--all]`
- `tests/load/run.sh --scenario <name> --target <url>`
