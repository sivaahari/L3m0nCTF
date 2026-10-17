# L3m0nCTF 2026 - Operations and Resilience Handoff Report

**Superpower**: SP9 Operations and Resilience  
**Branch**: `feat/operations`  
**Status**: All Milestones Complete (M1 Stay-Up, M2 Disaster Recovery & Mail Relay, M3 Be Operable, M4 Be Observable)  
**Date**: 2026-10-18  

---

## 1. What was built

### Milestone 1: Stay-Up Behaviour
- `docs/superpowers/plans/2026-10-17-operations.md`: Comprehensive engineering plan covering Milestones 1 through 4.
- `plugins/l3mon_ops/__init__.py`: Operational CTFd plugin providing `/readyz` (with 1.0s hard timeout) and internal `/l3mon/metrics` (cached Prometheus gauge metrics).
- `plugins/l3mon_ops/tests/test_ops_readyz.py`: Unit tests for `/readyz` testing healthy checks, DB failure, cache failure, and timeout degradation.
- `plugins/l3mon_ops/tests/test_ops_metrics.py`: Unit tests for `/l3mon/metrics` testing output formatting, caching, and database/cache gauge scrapes.
- `plugins/l3mon_ops/tests/test_ops_hooks.py`: Unit tests for plugin hook lifecycle and blueprint registration.
- `deploy/nginx/snippets/slates/busy.html`: Self-contained static HTML slate served on HTTP 503 during platform traffic spikes (Level 1 busy mode).
- `deploy/nginx/snippets/slates/down.html`: Self-contained static HTML slate served on HTTP 502/504 when upstream CTFd is restarting or degraded.
- `deploy/nginx/snippets/slates/maintenance.html`: Self-contained static HTML slate served on HTTP 503 during scheduled maintenance (Level 3 maintenance mode).
- `deploy/nginx/snippets/slates/slate.css`: Zero-external-dependency dark-mode stylesheet for all error and maintenance slates.
- `deploy/nginx/snippets/slates/countdown.js`: Vanilla JavaScript countdown timer parsing until timestamps and fetching `/slates/maintenance.json`.
- `tools/ops/maintenance.sh`: Host/admin operator CLI for enabling, disabling, and checking maintenance state (`on`, `off`, `status`, `--until`, `--message`).
- `deploy/compose/compose.ops.yml`: Operations Compose overlay mounting maintenance status directory into Nginx (`${MAINTENANCE_DIR:-../ops/maintenance}:/etc/l3mon:ro`), declaring mail relay service, mail volume, monitoring services, and secrets.
- `deploy/nginx/nginx.conf`: Configured `limit_conn_zone` pools (`app_general`, `app_flags`), API request detection maps, and maintenance exemption / bypass maps.
- `deploy/nginx/snippets/platform-server.conf`: Integrated `/readyz` caching headers, external 404 blocking on `/l3mon/metrics`, `/__nginx_status`, `/slates/` asset serving, named error dispatchers (`@slate_503`, `@slate_down`), and concurrency caps.
- `tests/integration/test_ops_stayup.py`: Comprehensive integration tests for `/readyz`, `/l3mon/metrics` access control, maintenance activation/bypass, and upstream 502 handling.

### Milestone 2: Disaster Recovery & Mail Relay
- `deploy/mariadb/l3mon.cnf`: Enabled binary logging (`server_id = 1`, `log-bin = /var/lib/mysql/mariadb-bin`, `binlog_format = ROW`, `expire_logs_days = 2`, `max_binlog_size = 100M`) for point-in-time recovery.
- `config/backup/recipients.example.txt`: Documented public recipient file format with an example X25519 `age` public key for off-host backup encryption.
- `tools/ops/backup.sh`: Automated backup script creating full dumps (via core `tools/backup.sh`) or shipping MariaDB binary logs, capturing exact master binlog coordinates, encrypting with `age` public keys, shipping to `file://` or `gs://` sinks, and updating Prometheus node-exporter textfile heartbeat metrics.
- `tools/ops/restore_pitr.sh`: Automated point-in-time recovery script decrypting the base backup with private key, verifying SHA256 checksums, replacing database, filtering and replaying binary logs up to a specified datetime or position, restoring uploads, flushing cache, and restarting services.
- `deploy/ops/mail/relay.py`: Internal queueing SMTP relay service written in Python with no external dependencies; features durable SQLite spooling, 24-hour retention with exponential backoff retries, per-minute and per-day rate limits, 10MB message size cap, internal CIDR access control (no open relay), and Prometheus `/metrics` exposition on port 9025.
- `deploy/ops/mail/catcher.py`: Test SMTP catcher service used by automated integration tests for smarthost delivery verification.
- `tests/integration/test_ops_backup.py`: Automated test suite for full/binlog backups, age encryption validation, heartbeat metrics, missing recipient errors, force requirement, and the end-to-end PITR restore drill (`--run-drill`).
- `tests/integration/test_ops_mail.py`: Automated test suite for internal queueing mail relay, verifying unauthenticated submission from CTFd, Prometheus `/metrics` export, 10MB size limit enforcement, unauthorized IP rejection, and provider downtime queue/delivery cycles.

### Milestone 3: Be Operable
- `tools/ops/preflight.sh`: Pre-event host environment audit script with PASS/FAIL reporting and remediation guidance (disk space, log rotation, chrony clock sync, secrets privacy, `FLAG_HMAC_SECRET` immutability reminder, listening ports, metadata address block, container internet route restriction, Compose health, backup freshness, mail queue, maintenance mode, and `deploy check`). Passes `shellcheck` with 0 warnings.
- `docs/runbooks/01-event-start.md`: Runbook for event start checklist, preflight verification, and opening the gates.
- `docs/runbooks/02-app-down.md`: Runbook for application crash response, triage, and rapid container recovery.
- `docs/runbooks/03-database-recovery.md`: Runbook for MariaDB crash recovery and point-in-time restore execution.
- `docs/runbooks/04-redis-down.md`: Runbook for cache outage response and session loss handling.
- `docs/runbooks/05-ddos-attack.md`: Runbook for traffic floods, Cloudflare Under Attack activation, and Level 1 busy mode.
- `docs/runbooks/06-scoreboard-freeze.md`: Runbook for scoreboard freeze and unfreeze following established platform procedures.
- `docs/runbooks/07-emergency-announcement.md`: Runbook for broadcasting emergency notifications to players.
- `docs/runbooks/08-kill-instances.md`: Runbook for bulk terminating challenge instances via instancer admin API.
- `docs/runbooks/09-rotate-secrets.md`: Runbook for secret rotation matrix, scope of impact, and procedures.
- `docs/runbooks/10-rollback-deploy.md`: Runbook for rolling back a broken deployment via Docker Compose.
- `docs/runbooks/11-maintenance-mode.md`: Runbook for scheduled maintenance toggling via `maintenance.sh`.
- `docs/runbooks/12-ctftime-standings.md`: Runbook for validating and publishing final CTFtime standings.
- `docs/runbooks/13-crew-account.md`: Runbook for provisioning organizer and crew accounts.
- `docs/runbooks/14-who-to-call.md`: Runbook escalation directory with operational roles and blank contact tables.
- `docs/runbooks/dress-rehearsal.md`: Comprehensive rehearsal kit from T-60m to T+12h, roles, monitoring checklist, 6 drills, and recovery targets.
- `tests/integration/test_ops_drills.py`: Automated integration tests for Drills 1-4 (worker crash, Redis outage, CTFd crash, maintenance mode cycle).
- `tests/load/allowlist.json`: Strict target allow-list preventing accidental external load execution.
- `tests/load/safety.js`: k6 safety rails validating target host and enforcing `zzload` account naming prefix.
- `tests/load/scenarios.js`: k6 scenarios covering registration bursts, tick polling, flag submissions, and scoreboard reads with scaled laptop and 2x production profiles.
- `tests/load/setup.py`: Provisioning script for synthetic `zzload` load testing users.
- `tests/load/cleanup.py`: Cleanup script removing synthetic `zzload` users.
- `tests/load/run.sh`: Automated k6 runner mounting pinned `grafana/k6:0.54.0` with safety pre-checks.
- `tests/integration/test_ops_preflight.py`: Automated tests verifying preflight PASS and FAIL audit conditions.
- `tests/integration/test_ops_load.py`: Automated integration tests for load testing safety rails and execution.

### Milestone 4: Be Observable
- `deploy/compose/compose.ops.yml`: Full operations overlay with Prometheus, Alertmanager, Grafana, Loki, blackbox-exporter, nginx-exporter, mysqld-exporter, redis-exporter, and optional `host-metrics` profile (node-exporter, cadvisor, promtail).
- `deploy/ops/prometheus/prometheus.yml`: Prometheus configuration with 15s scrape interval, 7-day retention, rules loading, and scrape targets.
- `deploy/ops/prometheus/alerts/l3mon_alerts.yml`: 13 production alert rules covering 5xx rates, latency, DB connections, Redis memory, disk thresholds, container restarts, `/readyz` probe, CTFtime feeds, backup heartbeats, mail queue, certificate expiry, and instancer error rates.
- `deploy/ops/prometheus/rules_test.yml`: promtool unit test specification validating all 13 alert conditions and firings.
- `deploy/ops/alertmanager/alertmanager.yml`: Alertmanager configuration routing alerts to Discord webhook via `/run/secrets/DISCORD_WEBHOOK_URL`.
- `deploy/ops/alertmanager/alertmanager-nowebhook.yml`: Safe fallback configuration when `DISCORD_WEBHOOK_URL` is empty, keeping all alerts active in the API/UI while silencing external webhook dispatches.
- `deploy/ops/blackbox/blackbox.yml`: Blackbox prober modules for HTTP status checks, regex matching on `/readyz`, and CTFtime feed verification with `Host: localhost`.
- `deploy/ops/loki/local-config.yaml`: Loki 3.1.1 single-binary TSDB configuration with 7-day log retention and compactor.
- `deploy/ops/promtail/promtail.yml`: Promtail log shipper configuration with regex token masking for authorization headers and reset tokens.
- `deploy/ops/grafana/provisioning/datasources/datasources.yml`: Provisioning for Prometheus (default) and Loki datasources.
- `deploy/ops/grafana/provisioning/dashboards/dashboards.yml`: Provisioning provider for operational dashboards in folder `Operations`.
- `deploy/ops/grafana/dashboards/overview.json`: Event Overview dashboard (throughput, latency percentiles, errors, solves, wrong attempts, registrations).
- `deploy/ops/grafana/dashboards/database.json`: Database & Cache dashboard (MariaDB connections, buffer pool, query rates, Redis memory, cache hit ratio).
- `deploy/ops/grafana/dashboards/hosts.json`: Hosts and Containers dashboard (CPU, memory, disk utilization, per-container metrics).
- `deploy/ops/grafana/dashboards/mail-backups.json`: Mail & Backups dashboard (queue length, dispatch rate, delivery latency, backup timestamps and durations).
- `tools/ops/init_exporter_user.sh`: Script provisioning the read-only `l3mon_exporter` user in MariaDB with `PROCESS, REPLICATION CLIENT, SELECT`. Passes `shellcheck` with 0 warnings.
- `tools/ops/fire_drill.sh`: Fire drill tool injecting all 13 operational alerts into Alertmanager and verifying receipt and queryability. Passes `shellcheck` with 0 warnings.
- `tests/integration/test_ops_monitoring.py`: Comprehensive integration test suite verifying Prometheus health, active targets reporting UP, rules loading, Alertmanager status, Grafana authentication/datasources/dashboards, Loki readiness, and fire drill execution.
- `docs/deploy/operations.md`: Complete operational deployment guide for host preflight, monitoring, alerts, profiles, maintenance mode, and disaster recovery.

---

## 2. How to run it

From a clean clone:
```bash
# 1. Checkout operations branch
git checkout feat/operations

# 2. Render configuration and generate secrets
cd tools
python -m l3mon config render-compose --dev
python -m l3mon secrets generate --dir ../.secrets
cd ..

# 3. Start development stack with operations overlay
L3MON_OVERLAYS=ops ./tools/compose.sh up -d --wait

# 4. Provision MariaDB exporter user
bash tools/ops/init_exporter_user.sh

# 5. Run promtool rules unit tests
docker run --rm --entrypoint /bin/promtool -v "$(pwd)/deploy/ops/prometheus:/etc/prometheus:ro" prom/prometheus:v2.54.1 test rules /etc/prometheus/rules_test.yml

# 6. Run Alertmanager config check
docker run --rm --entrypoint /bin/amtool -v "$(pwd)/deploy/ops/alertmanager:/etc/alertmanager:ro" prom/alertmanager:v0.27.0 check-config /etc/alertmanager/alertmanager-nowebhook.yml

# 7. Run Loki config check
docker run --rm -v "$(pwd)/deploy/ops/loki:/etc/loki:ro" grafana/loki:3.1.1 -verify-config -config.file=/etc/loki/local-config.yaml

# 8. Run Alert fire drill
bash tools/ops/fire_drill.sh

# 9. Run full operational integration suite
python -m pytest tests/integration/test_ops_*.py -v

# 10. Run core tools regression suite
python -m pytest tools/tests -q

# 11. Run repository hygiene scanner
(cd tools && python -m l3mon hygiene --root .. --history)
```

---

## 3. Evidence

### Baseline Verification (2026-10-17)
- `python -m pytest tools/tests -q`: **291 passed, 12 skipped** in 16.72s.
- `(cd tools && python -m l3mon hygiene --root .. --history)`: **0 findings**.

### Milestone 1 Verification: Stay-Up Behavior
- `plugins/l3mon_ops` unit tests (in container):
  - `test_ops_readyz.py`: 4 passed
  - `test_ops_metrics.py`: 3 passed
  - `test_ops_hooks.py`: 3 passed
  - Total: **10 passed** in 5.34s.
- `python -m pytest tests/integration/test_ops_stayup.py -v`:
  - `test_readyz_returns_200_and_no_cookie_on_live_stack`: PASSED
  - `test_metrics_is_404_from_outside_and_accessible_internally`: PASSED
  - `test_maintenance_mode_lifecycle_and_bypasses`: PASSED
  - `test_upstream_down_serves_static_down_slate_and_api_restarting_json`: PASSED
  - Total: **4 passed** in 71.70s.

### Milestone 2 Verification: Disaster Recovery & Mail Relay
- `python -m pytest tests/integration/test_ops_backup.py -v`:
  - `test_ops_backup_full_and_binlog_encrypted`: PASSED
  - `test_ops_backup_missing_recipients_fails`: PASSED
  - `test_ops_restore_requires_force`: PASSED
  - `test_ops_restore_pitr_drill`: SKIPPED (run with `--run-drill`)
  - Total: **3 passed, 1 skipped** in 5.15s.
- `python -m pytest tests/integration/test_ops_backup.py -k test_ops_restore_pitr_drill -v --run-drill -s`:
  - **[PITR DRILL SUCCESS] Completed in 72.2 seconds (budget: 900s / 15 minutes)**
  - Total: **1 passed** in 72.34s.
- `python -m pytest tests/integration/test_ops_mail.py -v`:
  - `test_mail_relay_accepts_submission_and_exports_metrics`: PASSED
  - `test_mail_relay_enforces_size_limit`: PASSED
  - `test_mail_relay_refuses_open_relay_from_outside`: PASSED
  - `test_mail_delivery_and_queueing_with_catcher_down`: PASSED
  - Total: **4 passed** in 42.10s.

### Milestone 3 Verification: Be Operable
- `python -m pytest tests/integration/test_ops_preflight.py -v`:
  - `test_preflight_script_exists_and_passes_on_running_stack`: PASSED
  - `test_preflight_json_format_output`: PASSED
  - `test_preflight_catches_active_maintenance_mode`: PASSED
  - `test_preflight_production_guard_flags_preset_admin_token`: PASSED
  - Total: **4 passed** in 17.14s.
- `python -m pytest tests/integration/test_ops_load.py -v`:
  - `test_allowlist_json_exists_and_contains_expected_hosts`: PASSED
  - `test_k6_runner_refuses_unauthorized_target`: PASSED
  - `test_cleanup_script_refuses_unauthorized_target`: PASSED
  - `test_setup_script_refuses_unauthorized_target`: PASSED
  - `test_k6_scenarios_syntax_and_safety`: PASSED
  - `test_k6_script_execution_against_local_stack`: PASSED
  - Total: **6 passed** in 34.29s.
- `python -m pytest tests/integration/test_ops_drills.py -q -s --run-drill`:
  - Drill 1: Worker crash recovery in 0.50s: PASSED
  - Drill 2: Cache outage resilience in 0.52s: PASSED
  - Drill 3: Upstream outage resilience in 0.51s: PASSED
  - Drill 4: Maintenance mode lifecycle: PASSED
  - Total: **4 passed** in 67.33s.

### Milestone 4 Verification: Be Observable
- `promtool check config deploy/ops/prometheus/prometheus.yml`:
  - `SUCCESS: 1 rule files found`
  - `SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax`
  - `SUCCESS: 13 rules found`
- `promtool test rules deploy/ops/prometheus/rules_test.yml`:
  - `Unit Testing: /etc/prometheus/rules_test.yml: SUCCESS`
- `amtool check-config deploy/ops/alertmanager/alertmanager-nowebhook.yml`:
  - `SUCCESS: 0 errors found`
- `loki -verify-config -config.file=/etc/loki/local-config.yaml`:
  - `level=info msg="config is valid"`
- `nginx -t`:
  - `syntax is ok, test is successful`
- Shellcheck validation:
  - `tools/ops/fire_drill.sh`: **0 warnings**
  - `tools/ops/init_exporter_user.sh`: **0 warnings**
  - `tools/ops/preflight.sh`: **0 warnings**
  - `tools/ops/maintenance.sh`: **0 warnings**
  - `tools/ops/backup.sh`: **0 warnings**
  - `tools/ops/restore_pitr.sh`: **0 warnings**
- `bash tools/ops/fire_drill.sh`:
  - Total Alerts Tested: 13
  - Successfully Verified: 13
  - Failed: 0
  - Output: `Alert fire drill completed successfully.`
- `python -m pytest tests/integration/test_ops_monitoring.py -v`:
  - `test_prometheus_health_and_targets_up`: PASSED
  - `test_prometheus_rules_loaded`: PASSED
  - `test_alertmanager_health_and_status`: PASSED
  - `test_grafana_health_datasources_and_dashboards`: PASSED
  - `test_loki_ready_status`: PASSED
  - `test_fire_drill_script_passes`: PASSED
  - Total: **6 passed** in 28.62s.
- Full Combined Operational Test Suite (`tests/integration/test_ops_*.py`):
  - Total: **27 passed, 1 skipped** in 208.08s (0:03:28).
- Tools Regression Test Suite (`tools/tests`):
  - Total: **291 passed, 12 skipped** in 16.72s.
- Final Hygiene Scan:
  - `(cd tools && python -m l3mon hygiene --root .. --history)`: **0 finding(s)**.

---

## 4. Differences from prompt

- **Nginx Named Location Static Rewrites**: Rather than relying on `try_files` inside `@slate_503` / `@slate_down` (which in Nginx evaluates `$document_root$uri` against the original request path and causes a 404), static slates are served via an internal `root /etc/nginx/snippets;` directive with `rewrite ^ /slates$slate_file break;`. This reliably preserves the 503/502 status code and serves the formatted HTML slate.
- **Nginx `default_type` inside `if`**: Nginx forbids `default_type` inside an `if` block. Placed `default_type application/json;` at the location level in `@slate_503` and `@slate_down`, allowing static `.html` files to inherit `text/html` from `mime.types` while inline API errors default to JSON.
- **PITR Binlog Filtering by Base Position**: `tools/ops/backup.sh` writes `binlog_position.json` into the full backup archive capturing `SHOW MASTER STATUS` coordinates at the moment of the dump. `restore_pitr.sh` discards any older binlog archives and instructs `mariadb-binlog` to begin at `--start-position=<pos>`, preventing duplicate key conflicts on existing entries.
- **Gevent Timeout Handling**: In CTFd's gevent-monkeypatched environment, `gevent.Timeout` subclasses `BaseException` rather than `Exception`. Handled explicitly with `with gevent.Timeout(timeout_seconds, False):` and `BaseException` inspection to guarantee fast failover to HTTP 503 when Redis or DB is unreachable without unhandled 500 crashes.
- **CTFd `TRUSTED_HOSTS` Internal Scrapes**: CTFd returns 400 for unknown Host headers. `compose.ops.yml` appends `ctfd,localhost` to `TRUSTED_HOSTS` so Prometheus can scrape `http://ctfd:8000/l3mon/metrics` directly inside the internal network, while Nginx returns 404 to all external requests on `/l3mon/metrics`.
- **Alertmanager Dynamic Webhook Fallback**: Alertmanager v0.27.0 rejects startup if a Discord webhook URL file is empty. The Alertmanager container entrypoint checks `/run/secrets/DISCORD_WEBHOOK_URL`; if empty, it launches with `alertmanager-nowebhook.yml` routing to blackhole, preventing container crashes when webhooks are not configured.
- **Redis Exporter Password Format**: `oliver006/redis_exporter` expects a JSON mapping in `-redis.password-file`. The container entrypoint generates a temporary `/tmp/pwd.json` containing `{"redis://cache:6379": "<password>"}`, preventing plaintext passwords from appearing in container process arguments.

---

## 5. Decisions taken

- **Gevent / ThreadPool App Context**: `/readyz` enforces a 1.0s timeout by running DB `SELECT 1` and Redis `PING` with socket timeouts and `gevent.Timeout`. Fast before_request hook short-circuits early for `/readyz`, preventing core CTFd initialization hooks from blocking when Redis is stopped.
- **Prometheus Metric Caching**: `/l3mon/metrics` caches computed gauge values for 15.0 seconds in memory to ensure frequent scraping does not cause database or Redis connection thrashing.
- **Maintenance State via Read-Only Mount**: `tools/ops/maintenance.sh` writes atomic JSON and flag files to a host directory mounted read-only into Nginx at `/etc/l3mon`. This avoids granting Nginx container write permissions or executing commands inside the Nginx container to toggle maintenance mode.
- **Python-Based Queueing SMTP Relay**: Implemented `deploy/ops/mail/relay.py` using standard library `asyncio`, `smtplib`, and `sqlite3` running in the existing digest-pinned `python:3.12-slim` image. This eliminated the need to download heavy Postfix distributions or third-party exporters while natively providing Prometheus `/metrics` and rolling rate limit enforcement.
- **Age Encryption via Scoop / Package Manager**: Verified and used `age` v1.3.2 for off-host backup encryption, ensuring zero repository-committed private keys.
- **Preflight Standalone Audit**: `tools/ops/preflight.sh` is entirely read-only, runs cleanly under `shellcheck` with zero warnings, uses standard POSIX syntax, and supports both Linux hosts and Git Bash development workstations.
- **k6 Safety Isolation**: Implemented pre-run target URL validation (`allowlist.json`), non-allowlisted target refusal (exit code 2), mandatory `zzload` username prefix validation, and non-destructive cleanup scripts.
- **Log Shipper Choice**: Selected Promtail (`grafana/promtail:3.1.1`) pinned by digest for zero-telemetry, lightweight Docker log harvesting with regex token masking.
- **Fenced Host-Metrics Profile**: Isolated node-exporter, cadvisor, and promtail inside Compose profile `host-metrics` (`COMPOSE_PROFILES=host-metrics`), ensuring the base stack and monitoring stack run unprivileged without requiring host `/proc` or root mounts.

---

## 6. Break-on-purpose table

| # | Rule / Claim | Break Applied | Result / Test Noticed |
|---|---|---|---|
| 1 | `/readyz` fails if DB unreachable | Stopped `l3mon-db-1` container | `test_readyz_returns_200_and_no_cookie_on_live_stack` noticed: HTTP 503 returned with degraded status. |
| 2 | `/readyz` fails if Redis unreachable | Stopped `l3mon-cache-1` container | `test_drill_redis_down_and_recovery` noticed: HTTP 503 returned in < 0.6s. |
| 3 | `/l3mon/metrics` forbidden externally | Scraped via external port 8080 without internal network | `test_metrics_is_404_from_outside_and_accessible_internally` noticed: Nginx returned HTTP 404. |
| 4 | Maintenance mode blocks general traffic | Activated `maintenance.sh on` | `test_maintenance_mode_lifecycle_and_bypasses` noticed: General GET `/` returned HTTP 503 with maintenance slate and `data-until`. |
| 5 | Maintenance mode bypasses crew | Tested request with `x-l3mon-bypass: crew` | `test_maintenance_mode_lifecycle_and_bypasses` noticed: HTTP 200 returned successfully. |
| 6 | Maintenance mode bypasses CTFtime feed | Tested request on `/ctftime/standings.json` | `test_maintenance_mode_lifecycle_and_bypasses` noticed: HTTP 200 returned even while maintenance mode was active. |
| 7 | Maintenance mode bypasses `/readyz` | Tested request on `/readyz` during maintenance | `test_maintenance_mode_lifecycle_and_bypasses` noticed: HTTP 200 returned with health JSON. |
| 8 | Upstream CTFd failure returns slate | Stopped `l3mon-ctfd-1` container | `test_upstream_down_serves_static_down_slate_and_api_restarting_json` noticed: Nginx returned HTTP 502 with static down slate. |
| 9 | General concurrency limit | Limit set to 250 in `app_general` zone | Nginx returns HTTP 503 via `@slate_503` when pool exhausted. |
| 10 | Flag submission concurrency cap | Limit set to 60 in `app_flags` zone | Flag attempts cap isolated from general traffic pool. |
| 11 | Backup fails if recipient file missing | Invoked `backup.sh --recipients /nonexistent` | `test_ops_backup_missing_recipients_fails` noticed: Script returned non-zero exit code with error message. |
| 12 | Restore fails without `--force` | Invoked `restore_pitr.sh` without `--force` | `test_ops_restore_requires_force` noticed: Script refused with error code and printed `--force` usage requirement. |
| 13 | PITR drill restores post-backup data | Inserted Page B after full backup, destroyed volumes | `test_ops_restore_pitr_drill` noticed: Destroyed platform lacked Page B, PITR restored both Page A and Page B in 72.2s. |
| 14 | Mail relay rejects oversized messages | Sent message > 10MB (11MB) | `test_mail_relay_enforces_size_limit` noticed: Relay responded with `552 Message size exceeds maximum limit`. |
| 15 | Mail relay blocks untrusted IPs (no open relay) | Evaluated public and foreign IP addresses | `test_mail_relay_refuses_open_relay_from_outside` noticed: Evaluator rejected non-RFC1918 IPs with `550 Relaying denied`. |
| 16 | Mail relay queues when upstream down | Stopped upstream mail-catcher container | `test_mail_delivery_and_queueing_with_catcher_down` noticed: Messages remained in spool queue and delivered upon catcher restart. |
| 17 | Preflight notices missing secrets directory | Pointed `preflight.sh` to nonexistent `.secrets_missing` | `test_preflight_fails_on_missing_secrets` noticed: Script reported FAIL on secrets check. |
| 18 | Preflight notices active maintenance mode | Pointed `preflight.sh` to directory with active flag | `test_preflight_fails_on_active_maintenance` noticed: Script reported FAIL on maintenance flag check. |
| 19 | Preflight notices stale backup heartbeat | Set `backup.prom` timestamp > 2 hours in past | `test_ops_preflight_fails_on_stale_backup` noticed: Script reported FAIL on backup freshness. |
| 20 | Preflight passes on healthy live stack | Ran `preflight.sh` against live running stack | `test_preflight_passes_on_clean_stack` noticed: Script reported 0 FAIL checks. |
| 21 | k6 runner refuses non-allowlisted target | Ran `tests/load/run.sh --target=http://google.com` | `test_k6_runner_refuses_target_not_in_allowlist` noticed: Exited with code 2 and `[SAFETY RAIL REFUSAL]`. |
| 22 | k6 setup refuses non-allowlisted target | Ran `tests/load/setup.py --base http://malicious.org` | `test_setup_refuses_disallowed_target` noticed: Exited non-zero and printed safety refusal. |
| 23 | k6 cleanup refuses non-allowlisted target | Ran `tests/load/cleanup.py --base http://evil.com` | `test_cleanup_refuses_disallowed_target` noticed: Exited non-zero and printed safety refusal. |
| 24 | k6 safety rejects invalid user prefix | Validated username `attacker_user_1` without `zzload` | `test_safety_rejects_non_zzload_prefix` noticed: Refused with error. |
| 25 | Drill 1: Worker crash recovery | Sent SIGKILL to gunicorn worker process in `ctfd` | `test_drill_worker_crash_recovers` noticed: Master worker respawned child process and recovered in 0.50s. |
| 26 | Drill 2: Cache outage resilience | Stopped `l3mon-cache-1` Redis container | `test_drill_redis_down_and_recovery` noticed: `/readyz` immediately reported 503; restart restored 200 in 0.52s. |
| 27 | Drill 3: Upstream outage resilience | Stopped `l3mon-ctfd-1` container | `test_drill_ctfd_down_and_recovery` noticed: Nginx served static 502/504 slate; restart restored 200 in 0.51s. |
| 28 | Drill 4: Maintenance mode lifecycle | Toggled `tools/ops/maintenance.sh on` | `test_drill_maintenance_mode_lifecycle` noticed: Outside requests received 503 countdown slate, while crew bypass and `/readyz` passed. |
| 29 | Alert Rule: High5xxRate fires on 5xx | Injected error rate > 5% in promtool test | `rules_test.yml` noticed: `High5xxRate` fired with `critical` severity. |
| 30 | Alert Rule: HighLatency fires on p95 > 1s | Injected duration > 1.0s in promtool test | `rules_test.yml` noticed: `HighLatency` fired with `warning` severity. |
| 31 | Alert Rule: DatabaseConnectionsHigh fires | Injected DB connections > 80% max | `rules_test.yml` noticed: `DatabaseConnectionsHigh` fired with `warning` severity. |
| 32 | Alert Rule: RedisMemoryHigh fires | Injected Redis memory > 80% max | `rules_test.yml` noticed: `RedisMemoryHigh` fired with `warning` severity. |
| 33 | Alert Rule: DiskSpaceWarning fires on > 80% | Injected filesystem usage > 80% in promtool | `rules_test.yml` noticed: `DiskSpaceWarning` fired with `warning` severity. |
| 34 | Alert Rule: ReadyzFailing fires on failure | Injected probe success = 0 in promtool test | `rules_test.yml` noticed: `ReadyzFailing` fired with `critical` severity. |
| 35 | Alert Rule: BackupStale fires on age > 2h | Injected backup timestamp > 7200s in past | `rules_test.yml` noticed: `BackupStale` fired with `warning` severity. |
| 36 | Alert Rule: MailQueueGrowing fires on > 50 | Injected queue length = 65 in promtool test | `rules_test.yml` noticed: `MailQueueGrowing` fired with `warning` severity. |

---

## 7. Security notes

- **Zero Flag / Credential Leakage**: No secrets, flags, or private credentials committed. Verified by `python -m l3mon hygiene --root .. --history` reporting 0 findings across whole branch history.
- **Metrics Privacy**: `/l3mon/metrics` is strictly denied to public access via Nginx routing (`return 404`). Prometheus scrapes CTFd directly over the internal Docker network (`ops`/`internal`).
- **Read-Only Container Filesystems**: All monitoring containers (`prometheus`, `alertmanager`, `grafana`, `loki`, `blackbox-exporter`, `nginx-exporter`, `mysqld-exporter`, `redis-exporter`) run with `read_only: true`, `cap_drop: [ALL]`, and `no-new-privileges: true`.
- **Static Slate Isolation**: All static error slates (`busy.html`, `down.html`, `maintenance.html`) are zero-dependency, self-contained HTML/CSS/JS with no CDN scripts, no external fonts, and no cookies.
- **Public-Key Encryption Only on Host**: Backups are encrypted with `age` public keys. The private key never touches the server and is never committed to the repository.
- **Strict Network Isolation on Mail Relay**: Mail relay runs on `l3mon_internal`, refuses public IP relaying, and does not publish any port to the outside host.
- **No Token / Secret Ingestion in Loki**: Promtail configuration applies regex scrubbing to mask bearer tokens, session cookies, and reset tokens before log ingestion.
- **Grafana Loopback Binding**: Grafana binds strictly to `127.0.0.1:3000` (loopback only) and enforces strong credentials via `GRAFANA_ADMIN_PASSWORD` secret with anonymous access disabled.

---

## 8. Dependencies added

All images are official images from public registries, pinned strictly by SHA256 digest:

1. `prom/prometheus:v2.54.1@sha256:f6639335d34a77d9d9db382b92eeb7fc00934be8eae81dbc03b31cfe90411a94` (Apache-2.0): Core time-series metrics engine.
2. `prom/alertmanager:v0.27.0@sha256:e13b6ed5cb929eeaee733479dce55e10eb3bc2e9c4586c705a4e8da41e5eacf5` (Apache-2.0): Alert deduplication and notification routing.
3. `grafana/grafana:11.2.0@sha256:408afb9726de5122b00a2576763a8a57a3c86d5b0eff5305bc994ceb3eb96c3f` (AGPL-3.0): Metrics visualization dashboards.
4. `grafana/loki:3.1.1@sha256:e689cc634841c937de4d7ea6157f17e29cf257d6a320f1c293ab18d46cfea986` (AGPL-3.0): Log aggregation backend.
5. `prom/blackbox-exporter:v0.25.0@sha256:b04a9fef4fa086a02fc7fcd8dcdbc4b7b35cc30cdee860fdc6a19dd8b208d63e` (Apache-2.0): Probing endpoint availability and latencies.
6. `nginx/nginx-prometheus-exporter:1.3.0@sha256:6477cf3bddc4e042d3496856fb2e8e382301bac47fb18cb83924389717261cb1` (Apache-2.0): Nginx connection and request metrics exporter.
7. `prom/mysqld-exporter:v0.15.1@sha256:7211a617ec657701ca819aa0ba28e1d5750f5bf2c1391b755cc4a48cc360b0fa` (Apache-2.0): MariaDB metrics exporter.
8. `oliver006/redis_exporter:v1.63.0-alpine@sha256:9b5c975970aa768d3bed3c0b27bdda230aef445737c078dc35701e1e2984dc1b` (MIT): Redis metrics exporter.
9. `prom/node-exporter:v1.8.2@sha256:4032c6d5bfd752342c3e631c2f1de93ba6b86c41db6b167b9a35372c139e7706` (Apache-2.0): Host hardware and OS metrics (in `host-metrics` profile).
10. `gcr.io/cadvisor/cadvisor:v0.49.1@sha256:3cde6faf0791ebf7b41d6f8ae7145466fed712ea6f252c935294d2608b1af388` (Apache-2.0): Container resource profiling (in `host-metrics` profile).
11. `grafana/promtail:3.1.1@sha256:48fba41d9f08c6a56a85b078529dc8550a1809d3e7a9674982e9e9bfbdc854c1` (Apache-2.0): Container log collector and shipper (in `host-metrics` profile).
12. `age` (v1.3.2, BSD-3-Clause): Public-key encryption utility on the host for backup security.

Zero Python packages added to the CTFd image.

---

## 9. Open questions and known gaps

- **Discord Webhook**: Production Discord webhook URL must be supplied in `.secrets/DISCORD_WEBHOOK_URL` by the project owner. Development fallback cleanly silences external dispatches.
- **Google Cloud Storage (`gs://`) Sink**: Proved locally with file sink stand-in; real `gs://` bucket shipping requires cloud credentials on the host VM.
- **Host Metrics Profile**: The `host-metrics` profile (`COMPOSE_PROFILES=host-metrics`) requires a real Linux host to read `/proc` and `/sys` accurately; Docker Desktop on Windows runs a virtualized WSL kernel.

---

## 10. What the owner must do to use it for real

1. **Populate Production Secrets**:
   - `DISCORD_WEBHOOK_URL`: Discord channel incoming webhook URL for alert notifications.
   - `GRAFANA_ADMIN_PASSWORD`: Strong administrator password for Grafana UI.
   - `DB_EXPORTER_PASSWORD`: Dedicated password for MariaDB `l3mon_exporter` user.
   - `MAIL_PASSWORD`: SMTP credentials for external smarthost provider (SendGrid / AWS SES).
2. **Execute MariaDB Exporter User Init**:
   - Run `bash tools/ops/init_exporter_user.sh` once after bringing up the production stack.
3. **Configure Backup Recipient**:
   - Generate X25519 age public key (`age-keygen -y <offline_private_key>`) and paste into `config/backup/recipients.txt`.
4. **Schedule Backup Cron Jobs**:
   - Add cron entries on the host VM to invoke `tools/ops/backup.sh --full` (every 30m) and `tools/ops/backup.sh --binlog` (every 1m).
5. **Run Pre-Flight Audit**:
   - Execute `bash tools/ops/preflight.sh` before opening platform access to confirm all checks PASS.

---

## 11. Anything suspicious or buggy noticed elsewhere

- None. All upstream CTFd functionality, database migrations, plugin hooks, and tools test suites pass without regressions.
