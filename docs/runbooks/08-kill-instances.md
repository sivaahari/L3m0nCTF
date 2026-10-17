# Runbook 08: Emergency Instance Purge (Kill All Instances)

## Purpose
Terminates all running ephemeral challenge container instances (SP4 Instancer) in the event of an infrastructure overload, rogue resource consumption, or challenge exploit runaway.

## Architectural Context
- The Instancer runs on the internal network (`http://instancer:8301`).
- The Instancer communicates with CTFd via the `INSTANCER_TOKEN` secret.
- The crew administration route is `POST /api/v1/l3mon/admin/instances/kill-all` (or per-challenge `.../instances/kill?challenge_id=N`).
- Note: This contract is provisioned by the SP4 team; steps below provide the operator invocation and the direct fallback commands on the instancer host.

## Numbered Procedure

### Option A: Standard Operator API (Recommended)
1. **Trigger instance purge via CTFd Admin API**:
   ```bash
   curl -s -X POST "http://127.0.0.1:8080/api/v1/l3mon/admin/instances/kill-all" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" \
     -H "Content-Type: application/json" \
     -d '{"confirm": true, "reason": "emergency load relief"}'
   ```

2. **Verify response**:
   Expected answer:
   ```json
   {"success": true, "data": {"killed_instances": 42, "remaining": 0}}
   ```

### Option B: Direct Fallback on Instancer Daemon
If the CTFd web interface is unreachable or the API call times out:
1. **Connect directly to the instancer management port**:
   ```bash
   curl -s -X POST "http://127.0.0.1:8301/api/v1/instances/cleanup" \
     -H "X-Instancer-Token: $(cat .secrets/INSTANCER_TOKEN)" \
     -H "Content-Type: application/json" \
     -d '{"force": true}'
   ```

2. **Emergency Docker Host Sweep (Extreme Fallback)**:
   If the instancer daemon is unresponsive, run on the challenge worker VM:
   ```bash
   docker ps --filter "label=l3mon.instance=true" -q | xargs -r docker rm -f
   ```

## How to know it worked
- Instancer metrics at `http://instancer:8301/metrics` show `l3mon_active_instances 0`.
- Host memory and CPU usage on the challenge runner VMs drops back to baseline.
- `GET /api/v1/l3mon/guide` for active teams reports 0 running instances.

## Who to tell
- **Challenge Authors**: Notify that ephemeral instances were wiped and will launch cleanly on new player requests.
- **Support Crew**: Advise players that active sessions were reset and they should click "Launch Instance" to obtain a fresh container.

---
- [ ] followed once by someone who did not write it
