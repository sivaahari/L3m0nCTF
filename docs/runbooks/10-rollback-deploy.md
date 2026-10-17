# Runbook 10: Rollback Deployment

## Purpose
Revert a failed software update or broken plugin rollout to the previous verified release image tag or commit without data loss.

## Architectural Context
- The CTFd platform image is versioned via image digest and release tags (`L3MON_IMAGE_TAG`, e.g. `20261120-1` -> `20261118-2`).
- Nginx provides zero-downtime proxy buffering during upstream recreation.
- State is preserved in external volumes (`l3mon_dbdata`, `l3mon_uploads`).
- **Rule**: Never run `down -v` during a rollback; keep volumes intact.

## Numbered Procedure

1. **Identify the previous working image tag or commit**:
   Check Git history and Docker image catalog:
   ```bash
   docker images "l3mon/ctfd"
   git log -n 5 --oneline
   ```

2. **Engage temporary maintenance pause (if database schema is impacted)**:
   If rolling back a migration:
   ```bash
   tools/ops/maintenance.sh on --message "Applying platform update rollback. Resuming shortly."
   ```

3. **Revert image tag configuration**:
   Set `L3MON_IMAGE_TAG` to the previous stable release in your environment or override:
   ```bash
   export L3MON_IMAGE_TAG="stable-tag"
   ```
   *(Or check out the previous stable git commit in the release directory).*

4. **Recreate CTFd container with the target image**:
   ```bash
   tools/compose.sh up -d --force-recreate --no-deps ctfd
   ```

5. **Verify schema and database compatibility**:
   Inspect CTFd startup logs to ensure SQLAlchemy maps tables properly:
   ```bash
   tools/compose.sh logs --tail 100 ctfd
   ```
   Confirm absence of schema mismatch or migration errors.

6. **Verify application readiness**:
   ```bash
   curl -fsS http://127.0.0.1:8080/readyz
   curl -fsS http://127.0.0.1:8080/l3mon/healthz
   ```
   Must return HTTP 200.

7. **Verify player endpoints**:
   ```bash
   curl -s http://127.0.0.1:8080/api/v1/l3mon/ticks | jq .
   ```

8. **Deactivate maintenance mode**:
   ```bash
   tools/ops/maintenance.sh off
   ```

## How to know it worked
- CTFd container is running the reverted image digest (`docker inspect $(tools/compose.sh ps -q ctfd) --format '{{.Image}}'`).
- `/readyz` returns HTTP 200 `{"status": "ready"}`.
- Challenge pages and scoring functions load without error.

## Who to tell
- **Event Lead & Engineering Owner**: Inform that release was rolled back and provide the error trace from the aborted release.

---
- [ ] followed once by someone who did not write it
