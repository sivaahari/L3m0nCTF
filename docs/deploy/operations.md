# L3m0nCTF 2026 - Operations and Observability Deployment Guide

This document is the operational deployment guide for the L3m0nCTF 2026 platform. It details host pre-flight verification, the monitoring stack, Prometheus alerting rules, host-level metrics profiling, maintenance mode, backups, and fire drills.

---

## 1. Quick Start

### Starting the Platform with the Operations Overlay
The operations subsystem is packaged as a standalone Compose overlay: `deploy/compose/compose.ops.yml`. It is added to the base deployment without modifying shared Compose files:

```bash
# Start the full stack with the operations overlay
L3MON_OVERLAYS=ops tools/compose.sh up -d --wait
```

### Initializing the Database Exporter User
The MariaDB Prometheus exporter (`mysqld-exporter`) uses a dedicated read-only user (`l3mon_exporter`) with `PROCESS, REPLICATION CLIENT, SELECT` privileges:

```bash
bash tools/ops/init_exporter_user.sh
```

---

## 2. Monitoring & Observability Stack

The monitoring overlay spins up 8 core services (all hardened, unprivileged, non-root, and pinned by digest):

| Service | Image | Network | Port / Binding | Function |
|---|---|---|---|---|
| **prometheus** | `prom/prometheus:v2.54.1` | `ops`, `internal`, `edge` | `127.0.0.1:9090` | Time-series metrics engine (7-day retention) |
| **alertmanager** | `prom/alertmanager:v0.27.0` | `ops` | `127.0.0.1:9093` | Alert routing with Discord webhook integration |
| **grafana** | `grafana/grafana:11.2.0` | `ops` | `127.0.0.1:3000` | Operations dashboards (anonymous access disabled) |
| **loki** | `grafana/loki:3.1.1` | `ops` | Internal (3100) | Log aggregation engine (7-day retention) |
| **blackbox-exporter** | `prom/blackbox-exporter:v0.25.0` | `ops`, `edge`, `internal` | Internal (9115) | HTTP/TCP probing (`/readyz`, CTFtime feed) |
| **nginx-exporter** | `nginx/nginx-prometheus-exporter:1.3.0` | `ops`, `edge` | Internal (9113) | Scraping Nginx `/__nginx_status` stub |
| **mysqld-exporter** | `prom/mysqld-exporter:v0.15.1` | `ops`, `internal` | Internal (9104) | MariaDB thread, buffer, query metrics |
| **redis-exporter** | `oliver006/redis_exporter:v1.63.0-alpine` | `ops`, `internal` | Internal (9121) | Redis memory, command, connection metrics |

### Host Metrics Profile (`host-metrics`)
Host-level metric collectors require access to host directories (`/proc`, `/sys`, `/var/lib/docker`) and are strictly isolated inside the `host-metrics` Compose profile:

```bash
# Start the platform with both the operations overlay and the host-metrics profile
COMPOSE_PROFILES=host-metrics L3MON_OVERLAYS=ops tools/compose.sh up -d --wait
```

The `host-metrics` profile includes:
1. **node-exporter** (`prom/node-exporter:v1.8.2`):
   - Mounts `/proc:/host/proc:ro` and `/sys:/host/sys:ro`.
   - Collects host CPU, memory, filesystem usage, and backup heartbeat metrics via `--collector.textfile.directory`.
2. **cadvisor** (`gcr.io/cadvisor/cadvisor:v0.49.1`):
   - Mounts rootfs and Docker socket paths read-only to monitor container resource usage.
3. **promtail** (`grafana/promtail:3.1.1`):
   - Reads Docker JSON log files from `/var/lib/docker/containers` and ships logs to Loki.
   - Automatically masks authorization and reset tokens before shipping.

> [!NOTE]
> When `host-metrics` is not active, the core platform and monitoring stack run normally. The host-level alert rules (`DiskSpaceWarning`, `DiskSpaceCritical`, `BackupStale`) evaluate only when their corresponding metrics exist and do not fire false alarms when the profile is absent.

---

## 3. Operational Alerts & Discord Integration

### Operational Alerts (13 Rules)
All alert rules are defined in `deploy/ops/prometheus/alerts/l3mon_alerts.yml` and verified with `promtool`:

1. **High5xxRate** (`critical`): Nginx 5xx rate > 5% over 2m -> Runbook `02-app-down.md`
2. **HighLatency** (`warning`): Nginx p95 request duration > 1.0s over 5m -> Runbook `05-ddos-attack.md`
3. **DatabaseConnectionsHigh** (`warning`): MariaDB connections > 80% capacity -> Runbook `03-database-recovery.md`
4. **RedisMemoryHigh** (`warning`): Redis memory consumption > 80% limit -> Runbook `04-redis-down.md`
5. **DiskSpaceWarning** (`warning`): Host disk usage > 80% -> Runbook `01-event-start.md`
6. **DiskSpaceCritical** (`critical`): Host disk usage > 90% -> Runbook `01-event-start.md`
7. **ContainerRestarting** (`warning`): Container restart count increasing -> Runbook `02-app-down.md`
8. **ReadyzFailing** (`critical`): CTFd `/readyz` probe returning non-200 -> Runbook `02-app-down.md`
9. **CTFtimeFeedStale** (`warning`): CTFtime scoreboard feed probe failing -> Runbook `12-ctftime-standings.md`
10. **BackupStale** (`warning`): Last backup older than 2 hours -> Runbook `03-database-recovery.md`
11. **MailQueueGrowing** (`warning`): Mail queue length > 50 messages -> Runbook `01-event-start.md`
12. **CertificateExpiring** (`warning`): TLS certificate expires within 14 days -> Runbook `01-event-start.md`
13. **InstancerErrorRateHigh** (`warning`): Challenge instance spawn failures -> Runbook `08-kill-instances.md`

### Discord Webhook & Fallback
- In production, set the Discord webhook URL in `.secrets/DISCORD_WEBHOOK_URL`.
- If `.secrets/DISCORD_WEBHOOK_URL` is empty, Alertmanager automatically switches to `alertmanager-nowebhook.yml`, discarding external dispatches while keeping all alerts fully queryable via UI and API.

### Alert Fire Drill
To verify that every alert can successfully be received and queried in Alertmanager, run the fire drill:

```bash
bash tools/ops/fire_drill.sh
```

---

## 4. Provisioned Dashboards

Grafana is provisioned with 4 operational dashboards in `deploy/ops/grafana/dashboards/`:

1. **Overview** (`l3mon-overview`): Request rates, p50/p95 latency, 4xx/5xx error rates, active solves, wrong attempts, and registration velocity.
2. **Database & Cache** (`l3mon-database`): MariaDB threads, query throughput, buffer pool utilization, Redis memory usage, hit rates, and connected clients.
3. **Hosts & Containers** (`l3mon-hosts`): Host CPU, RAM, disk utilization, and per-container CPU/memory profiles.
4. **Mail & Backups** (`l3mon-mail-backups`): Mail queue length, dispatch rates, delivery latency, backup success timestamps, size, and duration.

Access Grafana locally:
- URL: `http://127.0.0.1:3000`
- User: `admin`
- Password: in `.secrets/GRAFANA_ADMIN_PASSWORD`

---

## 5. Host Pre-Flight Check

Before opening the gates on event day, run the pre-flight verification script on the production host:

```bash
bash tools/ops/preflight.sh
```

The script evaluates:
- Adequate disk space (> 20 GB free).
- Docker log rotation configuration.
- System clock synchronization with NTP via `chrony`.
- Permissions of `.secrets/` (ensuring non-world-readable).
- Reminder that `FLAG_HMAC_SECRET` must remain immutable throughout the event.
- Network port bindings (only edge ports 80/443 exposed publicly).
- Cloud metadata IP (`169.254.169.254`) blocked from containers.
- Container internet egress isolation for CTFd.
- Container health status for all Compose services.
- Backup recency (< 2 hours).
- Mail relay queue state.
- Maintenance mode flag status (must be OFF).
- Platform configuration audit (`python -m l3mon deploy check`).

---

## 6. Maintenance & Disaster Recovery Operations

### Maintenance Mode
```bash
# Check status
bash tools/ops/maintenance.sh status

# Enable maintenance mode with ETA countdown and custom message
bash tools/ops/maintenance.sh on --until "2026-11-28T10:00:00Z" --message "Scheduled maintenance"

# Disable maintenance mode
bash tools/ops/maintenance.sh off
```

### Encrypted Backups
```bash
# Run 30-minute full database + uploads backup
bash tools/ops/backup.sh --full --recipients config/backup/recipients.txt --sink gs://l3monctf-backups-2026

# Run 1-minute MariaDB binary log shipping
bash tools/ops/backup.sh --binlog --recipients config/backup/recipients.txt --sink gs://l3monctf-backups-2026
```

### Point-in-Time Recovery (PITR)
```bash
# Restore to a specific point in time
bash tools/ops/restore_pitr.sh --sink gs://l3monctf-backups-2026 --key /path/to/private.key --until "2026-11-28 14:32:00" --force
```
