# Runbook 03: Database Recovery & Point-in-Time Restore (PITR)

## Purpose
Recover MariaDB from unrecoverable corruption, accidental data drop, or storage failure using encrypted backups and binary log replay.

## Symptoms
- `ReadyzFailing` alert fires (`/readyz` returns 503).
- CTFd logs report `OperationalError: (2002, "Can't connect to server")` or `Table doesn't exist`.
- MariaDB container `db` won't start or logs InnoDB corruption.

## Target Recovery Objective
- **RTO (Recovery Time Objective)**: ≤ 15 minutes.
- **RPO (Recovery Point Objective)**: ≤ 5 minutes of data loss (backed by 1-minute binlog shipping).

## Numbered Procedure

1. **Place platform into maintenance mode** to prevent conflicting writes:
   ```bash
   tools/ops/maintenance.sh on --message "Emergency database maintenance in progress."
   ```

2. **Locate the target backup bundle and binary logs**:
   Verify the newest backup archive and binlog files from sink:
   ```bash
   ls -la deploy/ops/backup/full/
   ls -la deploy/ops/backup/binlogs/
   ```

3. **Stop CTFd to ensure no active connections**:
   ```bash
   tools/compose.sh stop ctfd
   ```

4. **Execute Point-in-Time Restore**:
   Run `tools/ops/restore_pitr.sh` with the target backup archive, private key, and optional timestamp target:
   ```bash
   tools/ops/restore_pitr.sh \
     --backup deploy/ops/backup/full/backup-latest.tar.gz.age \
     --key /path/to/private/backup-key.txt \
     --binlog-dir deploy/ops/backup/binlogs \
     --until-time "2026-11-28 15:30:00" \
     --force
   ```
   *Note: If restoring up to the moment of failure, omit `--until-time` to replay all available binlogs.*

5. **Verify database tables and consistency**:
   ```bash
   docker exec $(tools/compose.sh ps -q db) mariadb -u root -p"$(cat .secrets/DB_ROOT_PASSWORD)" -e "SELECT count(*) FROM ctfd.challenges; SELECT count(*) FROM ctfd.solves;"
   ```

6. **Start CTFd and verify application readiness**:
   ```bash
   tools/compose.sh start ctfd
   curl -fsS http://127.0.0.1:8080/readyz
   ```
   Must return HTTP 200 `{"status": "ready"}`.

7. **Disable maintenance mode**:
   ```bash
   tools/ops/maintenance.sh off
   ```

## How to know it worked
- `/readyz` returns HTTP 200 `{"status": "ready"}`.
- Challenge and solve counts match expected state.
- Players can view the board and submit flags.

## Who to tell
- **Event Lead**: Report total recovery time and any gap in replayed transactions.
- **Scoring Lead**: Verify recent solves and determine if any team needs TRP recalculation.

---
- [ ] followed once by someone who did not write it
