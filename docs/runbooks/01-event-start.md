# Runbook 01: Event Start

## Purpose
Guide the operations crew through opening the doors for L3m0nCTF 2026 at T-60 to T-0 (10:00 IST / 04:30 UTC on 28 November 2026).

## Pre-conditions
- The production stack is running with `L3MON_ENV=production`.
- The event window in `config/event.toml` has `start = 1764304200` (10:00 IST).

## Numbered Procedure

1. **Run the host pre-flight audit** at T-60 minutes:
   ```bash
   tools/ops/preflight.sh --env production
   ```
   Confirm all checks return `PASS` with 0 `FAIL`.

2. **Verify application and upstream health**:
   ```bash
   curl -fsS https://play.l3m0nctf.xyz/readyz
   curl -fsS https://play.l3m0nctf.xyz/l3mon/healthz
   ```
   Both endpoints must return HTTP 200 with `{"status": "ready"}` and `{"status": "ok"}`.

3. **Verify maintenance mode is disabled**:
   ```bash
   tools/ops/maintenance.sh off
   ```

4. **Verify automated backups and binlog shipping are running**:
   ```bash
   tools/ops/backup.sh --sink file:///var/backups/l3mon
   ```
   Confirm latest backup timestamp in Prometheus metrics:
   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep l3mon_backup_last_success
   ```

5. **Verify mail relay queue is clear**:
   ```bash
   curl -s http://127.0.0.1:9025/metrics | grep l3mon_mail_queue_length
   ```
   Must report `l3mon_mail_queue_length 0`.

6. **Monitor the doors opening at T-0**:
   Watch CTFd worker and Nginx logs:
   ```bash
   tools/compose.sh logs -f --tail 100 nginx ctfd
   ```
   Watch Prometheus dashboard `Event Overview` for incoming traffic spikes.

## How to know it worked
- Players can load the landing page and sign in.
- `GET /readyz` continues returning HTTP 200.
- `GET /api/v1/l3mon/board` returns visible on-air channels and programmes.
- Grafana `Event Overview` shows healthy HTTP 200 request rates and error rates under 0.1%.

## Who to tell
- **Event Lead**: Confirm doors are open and traffic is flowing smoothly.
- **Support Crew**: Alert Discord / Matrix ticket handlers that players are actively entering.

---
- [ ] followed once by someone who did not write it
