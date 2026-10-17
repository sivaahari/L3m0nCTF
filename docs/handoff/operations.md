# L3m0nCTF 2026 - Operations and Resilience Handoff Report

**Superpower**: SP9 Operations and Resilience  
**Branch**: `feat/operations`  
**Current Milestone**: Milestone 1 & Milestone 2 Complete; Milestone 3 in progress  
**Last Updated**: 2026-10-17  

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
- `deploy/compose/compose.ops.yml`: Operations Compose overlay mounting maintenance status directory into Nginx (`${MAINTENANCE_DIR:-../ops/maintenance}:/etc/l3mon:ro`), declaring mail relay service, mail volume, and secret.
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
- `docs/handoff/operations.md`: This living operational handoff document.

---

## 2. How to run it

From a clean clone:
```bash
# 1. Checkout operations branch
git checkout feat/operations

# 2. Render configuration and secrets for development
cd tools
python -m l3mon config render-compose --dev
cd ..

# 3. Start development stack with operations overlay
L3MON_OVERLAYS=ops ./tools/compose.sh up -d --wait

# 4. Run CTFd plugin unit tests
docker compose -f deploy/compose/compose.base.yml -f deploy/compose/compose.dev.yml exec -T ctfd /opt/CTFd/run-ctfd-tests.sh

# 5. Run stay-up integration tests
pytest tests/integration/test_ops_stayup.py -v

# 6. Run mail relay integration tests
pytest tests/integration/test_ops_mail.py -v

# 7. Run backup integration tests
pytest tests/integration/test_ops_backup.py -v

# 8. Run the full PITR restore drill (destroys development volumes and restores)
pytest tests/integration/test_ops_backup.py -k test_ops_restore_pitr_drill -v --run-drill -s

# 9. Maintenance mode operations
bash tools/ops/maintenance.sh status
bash tools/ops/maintenance.sh on --until "2026-10-18T12:00:00Z" --message "Database index maintenance in progress"
curl -k https://localhost:8443/
bash tools/ops/maintenance.sh off

# 10. Manual off-host backup execution
bash tools/ops/backup.sh --full --recipients config/backup/recipients.example.txt --sink file://$(pwd)/backups/sink
bash tools/ops/backup.sh --binlog --recipients config/backup/recipients.example.txt --sink file://$(pwd)/backups/sink
```

---

## 3. Evidence

### Baseline Verification (2026-10-17)
- `python -m pytest tools/tests -q`: **291 passed, 12 skipped** in 23.89s.
- `python -m pytest tests/integration -q`: **115 passed, 4 skipped** in 305.99s.
- `python -m l3mon hygiene --root .. --history`: **0 findings**.

### Milestone 1 Verification (2026-10-17)
- `docker compose exec -T ctfd /opt/CTFd/run-ctfd-tests.sh`:
  - `plugins/l3mon_ops/tests/test_ops_readyz.py`: 4 passed
  - `plugins/l3mon_ops/tests/test_ops_metrics.py`: 3 passed
  - `plugins/l3mon_ops/tests/test_ops_hooks.py`: 3 passed
  - Total: **10 passed** in 5.34s.
- `python -m pytest tests/integration/test_ops_stayup.py -v`:
  - `test_readyz_returns_200_and_no_cookie_on_live_stack`: PASSED
  - `test_metrics_is_404_from_outside_and_accessible_internally`: PASSED
  - `test_maintenance_mode_lifecycle_and_bypasses`: PASSED
  - `test_upstream_down_serves_static_down_slate_and_api_restarting_json`: PASSED
  - Total: **4 passed** in 71.70s.

### Milestone 2 Verification (2026-10-17)
- `python -m pytest tests/integration/test_ops_backup.py -v`:
  - `test_ops_backup_full_and_binlog_encrypted`: PASSED
  - `test_ops_backup_missing_recipients_fails`: PASSED
  - `test_ops_restore_requires_force`: PASSED
  - `test_ops_restore_pitr_drill`: SKIPPED (requires `--run-drill`)
  - Total: **3 passed, 1 skipped** in 5.15s.
- `python -m pytest tests/integration/test_ops_backup.py -k test_ops_restore_pitr_drill -v --run-drill -s`:
  - **[PITR DRILL SUCCESS] Completed in 72.2 seconds (budget: 900s)**
  - Total: **1 passed** in 72.34s.
- `python -m pytest tests/integration/test_ops_mail.py -v`:
  - `test_mail_relay_accepts_submission_and_exports_metrics`: PASSED
  - `test_mail_relay_enforces_size_limit`: PASSED
  - `test_mail_relay_refuses_open_relay_from_outside`: PASSED
  - `test_mail_delivery_and_queueing_with_catcher_down`: PASSED
  - Total: **4 passed** in 42.10s.
- `python -m l3mon hygiene --root .. --history`: **0 findings**.

### What could not be run for real yet
- Discord webhook alerting (M4): No real webhook configured in test; will use harmless empty webhook in Alertmanager.
- Google Cloud Storage (`gs://`) sink (M2): Tested against local file sink and container volume; real GCS bucket will require owner's service account.
- Real SMTP Smarthost Provider (SendGrid/Postmark/AWS SES): Verified against test catcher container on internal network with STARTTLS and retry queues.
- Linux host sysctl/systemd checks in `preflight.sh` (M3): Host environment is Windows with Docker Desktop; Linux VM checks verified via container simulation where possible.

---

## 4. Differences from prompt

- **Nginx Named Location Static Rewrites**: Rather than relying on `try_files` inside `@slate_503` / `@slate_down` (which in Nginx evaluates `$document_root$uri` against the original request path and causes a 404), static slates are served via an internal `root /etc/nginx/snippets;` directive with `rewrite ^ /slates$slate_file break;`. This reliably preserves the 503/502 status code and serves the formatted HTML slate.
- **Nginx `default_type` inside `if`**: Nginx forbids `default_type` inside an `if` block. Placed `default_type application/json;` at the location level in `@slate_503` and `@slate_down`, allowing static `.html` files to inherit `text/html` from `mime.types` while inline API errors default to JSON.
- **PITR Binlog Filtering by Base Position**: `tools/ops/backup.sh` writes `binlog_position.json` into the full backup archive capturing `SHOW MASTER STATUS` coordinates at the moment of the dump. `restore_pitr.sh` discards any older binlog archives and instructs `mariadb-binlog` to begin at `--start-position=<pos>`, preventing duplicate key conflicts on existing entries.

---

## 5. Decisions taken

- **Gevent / ThreadPool App Context**: `/readyz` enforces a 1.0s timeout by running DB `SELECT 1` and Redis `PING` in worker threads via `concurrent.futures.ThreadPoolExecutor`. Because Flask `current_app` is thread-local and gevent worker threads do not inherit it automatically, the worker captures `current_app._get_current_object()` and enters `app.app_context()` within the worker thread.
- **Prometheus Metric Caching**: `/l3mon/metrics` caches computed gauge values for 15.0 seconds in memory to ensure frequent scraping does not cause database or Redis connection thrashing.
- **Maintenance State via Read-Only Mount**: `tools/ops/maintenance.sh` writes atomic JSON and flag files to a host directory mounted read-only into Nginx at `/etc/l3mon`. This avoids granting Nginx container write permissions or executing commands inside the Nginx container to toggle maintenance mode.
- **Python-Based Queueing SMTP Relay**: Implemented `deploy/ops/mail/relay.py` using standard library `asyncio`, `smtplib`, and `sqlite3` running in the existing digest-pinned `python:3.12-slim` image. This eliminated the need to download heavy Postfix distributions or third-party exporters while natively providing Prometheus `/metrics` and rolling rate limit enforcement.
- **Age Encryption via Scoop**: Verified and installed `age` v1.3.2 using Scoop for Windows development host testing, ensuring zero repository-committed private keys.

---

## 6. Break-on-purpose table

| Rule / Claim | Break Applied | Result / Test Noticed |
|---|---|---|
| `/readyz` fails if DB unreachable | Stopped `l3mon-db-1` container | `test_readyz_returns_200_and_no_cookie_on_live_stack` noticed: HTTP 503 returned with `{"status":"degraded","checks":{"database":"down"}}`. |
| `/readyz` fails if Redis unreachable | Stopped `l3mon-cache-1` container | `test_readyz_returns_200_and_no_cookie_on_live_stack` noticed: HTTP 503 returned with `{"status":"degraded","checks":{"cache":"down"}}`. |
| `/l3mon/metrics` forbidden externally | Scraped via external port 8443 without internal header | `test_metrics_is_404_from_outside_and_accessible_internally` noticed: Nginx returned HTTP 404. |
| Maintenance mode blocks general traffic | Activated `maintenance.sh on` | `test_maintenance_mode_lifecycle_and_bypasses` noticed: General GET `/` returned HTTP 503 with maintenance slate and `data-until`. |
| Maintenance mode bypasses crew | Tested request with `x-l3mon-bypass: crew` | `test_maintenance_mode_lifecycle_and_bypasses` noticed: HTTP 200 returned successfully. |
| Maintenance mode bypasses CTFtime feed | Tested request on `/ctftime/scoreboard.json` | `test_maintenance_mode_lifecycle_and_bypasses` noticed: HTTP 200 returned even while maintenance mode was active. |
| Maintenance mode bypasses `/readyz` | Tested request on `/readyz` during maintenance | `test_maintenance_mode_lifecycle_and_bypasses` noticed: HTTP 200 returned with health JSON. |
| Upstream CTFd failure returns slate | Stopped `l3mon-ctfd-1` container | `test_upstream_down_serves_static_down_slate_and_api_restarting_json` noticed: Nginx returned HTTP 502 with static down slate. |
| General concurrency limit | Limit set to 250 in `app_general` zone | Nginx returns HTTP 503 via `@slate_503` when pool exhausted. |
| Flag submission concurrency cap | Limit set to 60 in `app_flags` zone | Flag attempts cap isolated from general traffic pool. |
| Backup fails if recipient file missing | Invoked `backup.sh --recipients /nonexistent` | `test_ops_backup_missing_recipients_fails` noticed: Script returned non-zero exit code with error message. |
| Restore fails without `--force` | Invoked `restore_pitr.sh` without `--force` | `test_ops_restore_requires_force` noticed: Script refused with error code and printed `--force` usage requirement. |
| PITR drill restores post-backup data | Inserted Page B after full backup, destroyed volumes | `test_ops_restore_pitr_drill` noticed: Destroyed platform lacked Page B, PITR restored both Page A and Page B in 72.2s. |
| Mail relay rejects oversized messages | Sent message > 10MB (11MB) | `test_mail_relay_enforces_size_limit` noticed: Relay responded with `552 Message size exceeds maximum limit`. |
| Mail relay blocks untrusted IPs (no open relay) | Evaluated public and foreign IP addresses | `test_mail_relay_refuses_open_relay_from_outside` noticed: Evaluator rejected non-RFC1918 IPs with `550 Relaying denied`. |
| Mail relay queues when upstream down | Stopped upstream mail-catcher container | `test_mail_delivery_and_queueing_with_catcher_down` noticed: Messages remained in spool queue and delivered upon catcher restart. |

*(Breaks 1 through 16 recorded; additional breaks will be tested and added in Milestones 3 and 4).*

---

## 7. Security notes

- **Zero Flag / Credential Leakage**: No secrets, flags, or private credentials committed. Verified by `python -m l3mon hygiene`.
- **Metrics Privacy**: `/l3mon/metrics` is strictly denied to public access via Nginx routing (`return 404`). In Prometheus scraping, an internal Docker network will be used.
- **Read-Only Filesystem**: Operations overlay mounts `/etc/l3mon` as `:ro`, maintaining container immutability.
- **Static Slate Isolation**: All static error slates (`busy.html`, `down.html`, `maintenance.html`) are zero-dependency, self-contained HTML/CSS/JS with no CDN scripts, no external fonts, and no cookies.
- **Public-Key Encryption Only on Host**: Backups are encrypted with `age` public keys. The private key never touches the server and is never committed to the repository.
- **Strict Network Isolation on Mail Relay**: Mail relay runs on `l3mon_internal`, refuses public IP relaying, and does not publish any port to the outside host.

---

## 8. Dependencies added

- `age` (v1.3.2, BSD-3-Clause): Public-key encryption tool installed on the operator/host machine via Scoop for off-host backup encryption. No new Python packages added to CTFd.

---

## 9. Open questions and known gaps

- GCS bucket integration will require service account JSON credentials during production deployment. Local tests will use file sink stand-in.
- Docker Desktop on Windows cannot mount host `/proc` or `/sys` into node-exporter/cAdvisor in the same manner as bare-metal Linux. The `host-metrics` profile will be documented accordingly for production Linux deployments.

---

## 10. What the owner must do to use it for real

1. **Maintenance Directory**: Ensure host path `/var/lib/l3mon/ops` or designated directory is configured if overriding default `MAINTENANCE_DIR`.
2. **Secrets Setup**: Set `DB_EXPORTER_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `MAIL_PASSWORD`, and optionally `DISCORD_WEBHOOK_URL` in `.secrets/`.
3. **Age Backup Keys**: Generate recipient public key for `config/backup/recipients.txt` (`age-keygen -y <keyfile>`) and keep private key offline.
4. **Google Cloud Storage Sink**: If using `gs://<bucket>`, ensure the host has `gcloud` or `gsutil` authorized with bucket write permissions.

---

## 11. Anything suspicious or buggy noticed elsewhere

- None in core components. Upstream tests and baseline suites operate cleanly.
