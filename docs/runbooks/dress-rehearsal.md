# Operational Dress-Rehearsal Kit & Resilience Drill Guide

## Purpose
A full simulation protocol for the L3m0nCTF 2026 operations crew. It defines the timeline from T-60m to T+12h, participant roles, what to monitor on Grafana dashboards, and an ordered series of live resilience drills to verify alerts and recovery times.

---

## 1. Timeline & Operational Run of Show

| Time (IST) | Phase | Operational Activities | Dashboard to Watch |
|---|---|---|---|
| **09:00 (T-60m)** | Call to Stations | Crew briefing, communication channels opened (Discord, Signal). Run `tools/ops/preflight.sh`. | Host & Containers |
| **09:30 (T-30m)** | Warm-up & Verification | Verify Alertmanager connection. Confirm MariaDB binlogs are shipping every 60s. | Mail & Backups |
| **09:45 (T-15m)** | Synthetic Traffic Test | Run lightweight k6 smoke test (100 synthetic users polling ticks). Verify p95 < 100ms. | Event Overview |
| **10:00 (T-0)** | **Doors Open** | Contest begins. Monitor registration surge and initial channel unlocks. | Event Overview |
| **11:00 (T+1h)** | Early Game | Monitor flag submission rate and dynamic scoring calculations. | Database & Redis |
| **14:00 (T+4h)** | Scheduled Rehearsal Drills | Execute Resilience Drills 1 & 2 (Worker crash and Redis recovery). | Event Overview / Alerts |
| **16:00 (T+6h)** | Mid-Game Check | Review backup heartbeat freshness (<30m). Check mail queue length. | Mail & Backups |
| **18:00 (T+8h)** | High Concurrency Test | Synthetic load probe injects flag bursts (up to 140 attempts/s). | Event Overview |
| **20:00 (T+10h)** | **Scoreboard Freeze** | Activate freeze time via Runbook 06. Confirm CTFtime feed and public board stand still. | Event Overview |
| **21:55 (T+11h55m)**| Pre-Close Warning | Issue final 5-minute broadcast warning via notification bell (Runbook 07). | Event Overview |
| **22:00 (T+12h)** | **Broadcast Ends** | Challenge submissions close automatically. Review any unresolved dispute tickets. | Event Overview |
| **22:30 (Post)** | Results Publication | Lift scoreboard freeze. Publish final CTFtime standings feed (Runbook 12). | CTFtime Feed |
| **23:00 (Wrap)** | Post-Event Archive | Run full encrypted off-host backup. Archive container logs. | Mail & Backups |

---

## 2. Roles and Responsibilities

- **Incident Commander (IC)**: Holds decision authority for schedule pauses, maintenance mode, or freezes.
- **Platform Engineer (Infra)**: Observes VM memory, CPU, disk, Nginx concurrency, and executes compose restarts.
- **Database Administrator (DBA)**: Monitors connection pool saturation, slow queries, and binlog replication.
- **Challenge Lead**: Coordinates author fixes, challenge pulling (`l3mon_release`), and solve voiding (`l3mon_scoring`).
- **Communications Lead**: Posts updates to Discord `#announcements` and the platform notification bell.

---

## 3. Resilience Drills Protocol

Run these drills during the pre-event dress rehearsal to prove that every failure mode is detected by an alert and recovers within its target budget:

| Drill # | Simulated Failure | Execution Command | Expected Alert | Expected Impact | Target Recovery Time |
|---|---|---|---|---|---|
| **1** | **CTFd Worker Crash** | Kill single gunicorn worker process in `ctfd` | `HighLatency` (transient) | Gunicorn master immediately spawns replacement worker; zero dropped requests. | < 3 seconds |
| **2** | **Redis Cache Crash** | `tools/compose.sh restart cache` | `ReadyzFailing` | Active user sessions reset; players must re-sign in; zero data loss. | < 10 seconds |
| **3** | **Application Down** | `tools/compose.sh stop ctfd` | `ReadyzFailing`, `High5xxRate` | Nginx serves static `down.html` slate; API receives `{"error": "restarting"}`; healthz remains 200. | < 30 seconds |
| **4** | **MariaDB Database Down** | `tools/compose.sh stop db` | `ReadyzFailing`, `High5xxRate` | `/readyz` returns 503; Nginx sheds load with static slates; recovered upon DB restart. | < 45 seconds |
| **5** | **Disk Space Fill** | Create temporary 10GB file on host partition | `DiskSpaceLow` (>80% / >90%) | Pre-flight warns/fails; Alertmanager fires warning to Discord. | Immediate on `rm` |
| **6** | **Stale CTFtime Feed** | Pause feed update job or touch mock timestamp > 2m | `CTFtimeFeedStale` | Alertmanager triggers alert; feed regenerates on next scoreboard poll. | < 2 minutes |

---

## 4. Automated Execution on Development Stack

The drills are automated for local development stack verification in:
```bash
python -m pytest tests/integration/test_ops_drills.py -q --run-drill
```
*(By design, these drills only run when `--run-drill` or `L3MON_DRILL=1` is provided).*

---
- [ ] followed once by someone who did not write it
