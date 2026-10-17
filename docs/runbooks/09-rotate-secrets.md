# Runbook 09: Secret Rotation Procedures

## Purpose
Safely rotate system secrets, credentials, and API keys before, during, or after an operational incident.

## Secret Impact Matrix

| Secret Name | Scope & Location | Impact of Rotation | Safe During Round? |
|---|---|---|---|
| `SECRET_KEY` | CTFd session signer | **Logs out all players and crew**; resets session cookies. | **Yes, with warning** |
| `FLAG_HMAC_SECRET` | Dynamic flag HMAC key | **CRITICAL: Invalidates ALL generated and submitted flags** across every challenge and team. | **NO. NEVER ROTATE DURING ROUND.** |
| `DB_PASSWORD` | MariaDB `ctfd` user | Breaks database connection until both MariaDB user and CTFd secret match. | **Yes, with maintenance pause** |
| `DB_ROOT_PASSWORD` | MariaDB root user | Backup scripts fail until updated; no player impact. | **Yes** |
| `REDIS_PASSWORD` | Redis auth key | Breaks Redis cache/sessions until both sides match. | **Yes, logs out active players** |
| `MAIL_PASSWORD` | SMTP provider password | Outbound emails held in queue until updated; no dropped mail. | **Yes** |
| `GRAFANA_ADMIN_PASSWORD`| Grafana dashboard | Affects crew monitoring login only. | **Yes** |
| `DB_EXPORTER_PASSWORD` | MariaDB exporter | MariaDB metrics scrape fails until updated. | **Yes** |
| `INSTANCER_TOKEN` | Instancer auth token | Instance launches fail until both CTFd and instancer agree. | **Yes, brief pause** |
| `DISCORD_WEBHOOK_URL` | Alertmanager routing | Alerts fail to post to Discord (held in Alertmanager). | **Yes** |
| `CTFTIME_CLIENT_SECRET`| CTFtime OAuth | CTFtime sign-in fails until CTFtime dashboard and secret match. | **Yes** |

## Numbered Procedures

### Procedure 1: Rotate `SECRET_KEY` (Invalidate All Sessions)
1. Generate new 32-byte secret:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))" > .secrets/SECRET_KEY
   ```
2. Restart CTFd to apply:
   ```bash
   tools/compose.sh restart ctfd
   ```
3. Verify `/readyz` is 200:
   ```bash
   curl -fsS http://127.0.0.1:8080/readyz
   ```

### Procedure 2: Rotate `DB_PASSWORD` (Zero Data Loss)
1. Generate new database password:
   ```bash
   NEW_PW="$(python -c "import secrets; print(secrets.token_hex(24))")"
   ```
2. Update MariaDB internal user:
   ```bash
   docker exec -i $(tools/compose.sh ps -q db) mariadb -u root -p"$(cat .secrets/DB_ROOT_PASSWORD)" -e \
     "ALTER USER 'ctfd'@'%' IDENTIFIED BY '$NEW_PW'; FLUSH PRIVILEGES;"
   ```
3. Update `.secrets/DB_PASSWORD`:
   ```bash
   echo -n "$NEW_PW" > .secrets/DB_PASSWORD
   ```
4. Recreate CTFd container to pick up the updated secret mount:
   ```bash
   tools/compose.sh up -d --force-recreate --no-deps ctfd
   ```
5. Verify application connectivity:
   ```bash
   curl -fsS http://127.0.0.1:8080/readyz
   ```

### Procedure 3: Rotate `MAIL_PASSWORD`
1. Update `.secrets/MAIL_PASSWORD` with the provider's new SMTP API token/password.
2. Restart `mail-relay` service:
   ```bash
   tools/compose.sh restart mail-relay
   ```
3. Trigger queue delivery and inspect logs:
   ```bash
   tools/compose.sh logs --tail 50 mail-relay
   ```

## How to know it worked
- `/readyz` reports HTTP 200.
- Dependent service logs show clean authentication with no access denied errors.

## Who to tell
- **Event Lead**: Document the rotation in the operations log with exact UTC timestamp.

---
- [ ] followed once by someone who did not write it
