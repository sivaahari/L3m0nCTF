# Runbook 11: Maintenance Mode Activation and Deactivation

## Purpose
Gracefully gate player traffic using static maintenance slates (`maintenance.html`) while preserving access for the operations crew, monitoring systems, and CTFtime feeds.

## Architectural Context
- Maintenance mode is toggled by creating or removing `/etc/l3mon/maintenance`.
- Managed via `tools/ops/maintenance.sh`.
- **Exemptions (traffic that continues to pass through)**:
  1. **Organiser IP Addresses** (`$admin_net = 1`): Organisers can view, test, and edit the platform normally.
  2. **Health Endpoints**: `/healthcheck`, `/l3mon/healthz`, and `/readyz` continue to answer monitoring probes.
  3. **CTFtime Feeds**: `/ctftime/standings.json` continues serving external score collectors.

## Numbered Procedure

### To Enable Maintenance Mode
1. **Determine estimated resumption time** (ISO 8601 format, e.g. `2026-11-28T18:30:00Z`).

2. **Execute maintenance command**:
   ```bash
   tools/ops/maintenance.sh on \
     --until "2026-11-28T18:30:00Z" \
     --message "Scheduled 15-minute challenge maintenance. Scores and sessions are preserved."
   ```

3. **Verify from an external / player IP**:
   ```bash
   curl -i http://127.0.0.1:8080/
   ```
   Must return HTTP 503 with the `maintenance.html` slate and countdown.

4. **Verify API response**:
   ```bash
   curl -i -H "Accept: application/json" http://127.0.0.1:8080/api/v1/l3mon/board
   ```
   Must return HTTP 503 with `{"error": "maintenance", "retry_after": ...}`.

5. **Verify health check bypass**:
   ```bash
   curl -fsS http://127.0.0.1:8080/readyz
   ```
   Must return HTTP 200 `{"status": "ready"}`.

6. **Verify CTFtime feed bypass**:
   ```bash
   curl -fsS http://127.0.0.1:8080/ctftime/standings.json
   ```
   Must return HTTP 200.

---

### To Disable Maintenance Mode
1. **Execute maintenance turn-off command**:
   ```bash
   tools/ops/maintenance.sh off
   ```

2. **Verify player access restored**:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
   ```
   Must return HTTP 200.

## How to know it worked
- On enable: General player browsers see the calm countdown slate with event branding. Organisers continue to see the real site.
- On disable: Competitors immediately load the platform and resume challenge interaction.

## Who to tell
- **Discord Community Lead**: Notify players when maintenance starts and when the platform is back live.

---
- [ ] followed once by someone who did not write it
