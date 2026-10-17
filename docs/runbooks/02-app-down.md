# Runbook 02: Application Down (502 / 504 / Worker Crash)

## Purpose
Restore CTFd application services when Nginx returns 502 Bad Gateway or 504 Gateway Timeout, or when the `ctfd` container crashes.

## Symptoms
- Alertmanager fires `High5xxRate` or `ReadyzFailing`.
- Browsers see the static "restarting" slate (`down.html`) with an auto-retry spinner.
- API callers receive `{"error": "restarting"}` with HTTP 502/504.
- `curl -I http://127.0.0.1:8080/readyz` returns 502, 503, or connection refused.

## Numbered Procedure

1. **Check container running state**:
   ```bash
   tools/compose.sh ps ctfd
   ```
   If state is `Exited` or `Restarting`, inspect the exit reason:
   ```bash
   docker inspect --format 'ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}} Error={{.State.Error}}' $(tools/compose.sh ps -q ctfd)
   ```

2. **Inspect recent CTFd logs for exceptions or crashes**:
   ```bash
   tools/compose.sh logs --tail 200 ctfd
   ```
   Common causes:
   - `OOMKilled=true`: Container exceeded memory limit (default 1.5GB). Check memory spike in Grafana.
   - MariaDB connection drop: Check DB logs (see Runbook 03).
   - Redis connection timeout: Check Redis logs (see Runbook 04).

3. **Restart the CTFd service cleanly**:
   ```bash
   tools/compose.sh restart ctfd
   ```

4. **Verify container health and readiness**:
   ```bash
   tools/compose.sh ps ctfd
   curl -fsS http://127.0.0.1:8080/readyz
   ```
   Must return HTTP 200 `{"status": "ready"}` within 30 seconds.

5. **If CTFd remains stuck or fails to boot**:
   Force-recreate the container without touching data volumes:
   ```bash
   tools/compose.sh up -d --force-recreate --no-deps ctfd
   ```

## How to know it worked
- `curl -fsS http://127.0.0.1:8080/readyz` returns HTTP 200 `{"status": "ready"}`.
- Nginx access logs show incoming requests completing with HTTP 200 instead of 502/504.
- Player browsers stop showing the "restarting" slate and load the board.

## Who to tell
- **Platform Owner**: Report downtime duration and whether OOM or upstream error was the root cause.
- **Support Crew**: Inform that application is recovered and players can resume submissions.

---
- [ ] followed once by someone who did not write it
