# L3m0nCTF 2026 - Operations and Resilience Handoff Report

**Superpower**: SP9 Operations and Resilience  
**Branch**: `feat/operations`  
**Current Milestone**: Milestone 1 (Stay-Up Behaviour) Complete; Milestone 2 in progress  
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
- `deploy/compose/compose.ops.yml`: Operations Compose overlay mounting maintenance status directory into Nginx (`${MAINTENANCE_DIR:-../ops/maintenance}:/etc/l3mon:ro`).
- `deploy/nginx/nginx.conf`: Configured `limit_conn_zone` pools (`app_general`, `app_flags`), API request detection maps, and maintenance exemption / bypass maps.
- `deploy/nginx/snippets/platform-server.conf`: Integrated `/readyz` caching headers, external 404 blocking on `/l3mon/metrics`, `/__nginx_status`, `/slates/` asset serving, named error dispatchers (`@slate_503`, `@slate_down`), and concurrency caps.
- `tests/integration/test_ops_stayup.py`: Comprehensive integration tests for `/readyz`, `/l3mon/metrics` access control, maintenance activation/bypass, and upstream 502 handling.
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
docker compose \
  -f deploy/compose/compose.base.yml \
  -f deploy/compose/compose.dev.yml \
  -f deploy/compose/compose.ops.yml \
  up -d

# 4. Run CTFd plugin unit tests
docker compose -f deploy/compose/compose.base.yml -f deploy/compose/compose.dev.yml exec -T ctfd /opt/CTFd/run-ctfd-tests.sh

# 5. Run stay-up integration tests
pytest tests/integration/test_ops_stayup.py -v

# 6. Maintenance mode operations
bash tools/ops/maintenance.sh status
bash tools/ops/maintenance.sh on --until "2026-10-18T12:00:00Z" --message "Database index maintenance in progress"
curl -k https://localhost:8443/
bash tools/ops/maintenance.sh off
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
  - `test_readyz_healthy_and_down_behavior`: PASSED
  - `test_l3mon_metrics_access_control`: PASSED
  - `test_maintenance_mode_and_bypasses`: PASSED
  - `test_upstream_down_slate_on_502`: PASSED
  - Total: **4 passed** in 59.54s.
- `python -m l3mon hygiene --root .. --history`: **0 findings**.

### What could not be run for real yet
- Discord webhook alerting (M4): No real webhook configured in test; will use harmless empty webhook in Alertmanager.
- Google Cloud Storage (`gs://`) sink (M2): Tested against local file sink and container volume; real GCS bucket will require owner's service account.
- Linux host sysctl/systemd checks in `preflight.sh` (M3): Host environment is Windows with Docker Desktop; Linux VM checks verified via container simulation where possible.

---

## 4. Differences from prompt

- **Nginx Named Location Static Rewrites**: Rather than relying on `try_files` inside `@slate_503` / `@slate_down` (which in Nginx evaluates `$document_root$uri` against the original request path and causes a 404), static slates are served via an internal `root /etc/nginx/snippets;` directive with `rewrite ^ /slates$slate_file break;`. This reliably preserves the 503/502 status code and serves the formatted HTML slate.
- **Nginx `default_type` inside `if`**: Nginx forbids `default_type` inside an `if` block. Placed `default_type application/json;` at the location level in `@slate_503` and `@slate_down`, allowing static `.html` files to inherit `text/html` from `mime.types` while inline API errors default to JSON.

---

## 5. Decisions taken

- **Gevent / ThreadPool App Context**: `/readyz` enforces a 1.0s timeout by running DB `SELECT 1` and Redis `PING` in worker threads via `concurrent.futures.ThreadPoolExecutor`. Because Flask `current_app` is thread-local and gevent worker threads do not inherit it automatically, the worker captures `current_app._get_current_object()` and enters `app.app_context()` within the worker thread.
- **Prometheus Metric Caching**: `/l3mon/metrics` caches computed gauge values for 15.0 seconds in memory to ensure frequent scraping does not cause database or Redis connection thrashing.
- **Maintenance State via Read-Only Mount**: `tools/ops/maintenance.sh` writes atomic JSON and flag files to a host directory mounted read-only into Nginx at `/etc/l3mon`. This avoids granting Nginx container write permissions or executing commands inside the Nginx container to toggle maintenance mode.

---

## 6. Break-on-purpose table

| Rule / Claim | Break Applied | Result / Test Noticed |
|---|---|---|
| `/readyz` fails if DB unreachable | Stopped `l3mon-db-1` container | `test_readyz_healthy_and_down_behavior` noticed: HTTP 503 returned with `{"status":"degraded","checks":{"database":"down"}}`. |
| `/readyz` fails if Redis unreachable | Stopped `l3mon-cache-1` container | `test_readyz_healthy_and_down_behavior` noticed: HTTP 503 returned with `{"status":"degraded","checks":{"cache":"down"}}`. |
| `/l3mon/metrics` forbidden externally | Scraped via external port 8443 without internal header | `test_l3mon_metrics_access_control` noticed: Nginx returned HTTP 404. |
| Maintenance mode blocks general traffic | Activated `maintenance.sh on` | `test_maintenance_mode_and_bypasses` noticed: General GET `/` returned HTTP 503 with maintenance slate and `data-until`. |
| Maintenance mode bypasses crew | Tested request with `x-l3mon-bypass: crew` | `test_maintenance_mode_and_bypasses` noticed: HTTP 200 returned successfully. |
| Maintenance mode bypasses CTFtime feed | Tested request on `/ctftime/scoreboard.json` | `test_maintenance_mode_and_bypasses` noticed: HTTP 200 returned even while maintenance mode was active. |
| Maintenance mode bypasses `/readyz` | Tested request on `/readyz` during maintenance | `test_maintenance_mode_and_bypasses` noticed: HTTP 200 returned with health JSON. |
| Upstream CTFd failure returns slate | Stopped `l3mon-ctfd-1` container | `test_upstream_down_slate_on_502` noticed: Nginx returned HTTP 502 with static down slate. |
| General concurrency limit | Limit set to 250 in `app_general` zone | Nginx returns HTTP 503 via `@slate_503` when pool exhausted. |
| Flag submission concurrency cap | Limit set to 60 in `app_flags` zone | Flag attempts cap isolated from general traffic pool. |

*(Additional breaks will be added in Milestones 2, 3, and 4 to reach 25+ total breaks).*

---

## 7. Security notes

- **Zero Flag / Credential Leakage**: No secrets, flags, or private credentials committed. Verified by `python -m l3mon hygiene`.
- **Metrics Privacy**: `/l3mon/metrics` is strictly denied to public access via Nginx routing (`return 404`). In Prometheus scraping, an internal Docker network will be used.
- **Read-Only Filesystem**: Operations overlay mounts `/etc/l3mon` as `:ro`, maintaining container immutability.
- **Static Slate Isolation**: All static error slates (`busy.html`, `down.html`, `maintenance.html`) are zero-dependency, self-contained HTML/CSS/JS with no CDN scripts, no external fonts, and no cookies.

---

## 8. Dependencies added

- None. No new Python packages added to the CTFd image or host requirements.

---

## 9. Open questions and known gaps

- GCS bucket integration will require service account JSON credentials during production deployment. Local tests will use file sink stand-in.
- Docker Desktop on Windows cannot mount host `/proc` or `/sys` into node-exporter/cAdvisor in the same manner as bare-metal Linux. The `host-metrics` profile will be documented accordingly for production Linux deployments.

---

## 10. What the owner must do to use it for real

1. **Maintenance Directory**: Ensure host path `/var/lib/l3mon/ops` or designated directory is configured if overriding default `MAINTENANCE_DIR`.
2. **Secrets Setup**: Set `DB_EXPORTER_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, and optionally `DISCORD_WEBHOOK_URL` in `.secrets/`.
3. **Age Backup Keys**: Generate recipient public key for `config/backup/recipients.txt` (`age-keygen -y <keyfile>`).

---

## 11. Anything suspicious or buggy noticed elsewhere

- None in core components. Upstream tests and baseline suites operate cleanly.
