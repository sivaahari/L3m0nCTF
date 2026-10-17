# Runbook 04: Redis Failure & Session Recovery

## Purpose
Handle crashes or memory exhaustion of the Redis cache (`cache` service) and manage the impact of session invalidation.

## Architecture Context & Player Impact
- **Architecture Note**: Redis operates as an in-memory cache and session store with **no persistence** (`save ""` / no RDB or AOF).
- **Player Impact**: When Redis restarts, **all active user sessions are invalidated immediately**.
  - Logged-in players will experience their next request redirecting to the sign-in page.
  - No database records, scores, solves, or flags are lost.
  - Players must sign back in using their registered credentials or CTFtime OAuth.

## Symptoms
- Alertmanager fires `ReadyzFailing` (since `/readyz` fails when `cache` fails to reply to `PING`).
- CTFd logs report `redis.exceptions.ConnectionError` or `TimeoutError`.
- Players attempting to access protected endpoints receive 302 redirects to `/login`.

## Numbered Procedure

1. **Check Redis container status and memory usage**:
   ```bash
   tools/compose.sh ps cache
   docker inspect --format 'State={{.State.Status}} OOMKilled={{.State.OOMKilled}}' $(tools/compose.sh ps -q cache)
   ```

2. **Inspect Redis container logs**:
   ```bash
   tools/compose.sh logs --tail 100 cache
   ```
   Check for:
   - `OOM command not allowed when used memory > 'maxmemory'`: Redis exceeded memory limits.
   - Crash or core dump traces.

3. **Restart the Redis service**:
   ```bash
   tools/compose.sh restart cache
   ```

4. **Verify Redis responsiveness**:
   Check health using `redis-cli ping` with password:
   ```bash
   docker exec $(tools/compose.sh ps -q cache) redis-cli -a "$(cat .secrets/REDIS_PASSWORD)" ping
   ```
   Must answer `PONG`.

5. **Verify platform readiness**:
   ```bash
   curl -fsS http://127.0.0.1:8080/readyz
   ```
   Must return HTTP 200 `{"status": "ready"}`.

6. **Flush stale CTFd worker connection pools if needed**:
   If CTFd workers hold stale broken connections to Redis, restart CTFd workers:
   ```bash
   tools/compose.sh restart ctfd
   ```

## How to know it worked
- `curl -fsS http://127.0.0.1:8080/readyz` returns HTTP 200 `{"status": "ready"}`.
- Signing in at `http://play.l3m0nctf.xyz/login` succeeds and sets a valid session cookie.
- Ticks and board endpoints resume answering normally.

## Who to tell
- **Event Lead & Support Crew**: Alert the community team to notify players in Discord: *"Temporary session refresh: please sign back into your account; all scores and solves are intact."*

---
- [ ] followed once by someone who did not write it
