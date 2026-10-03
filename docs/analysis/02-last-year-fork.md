# Last year's platform: what was built and what hurt

Review of last year's L3m0nCTF repository (a public fork of CTFd 3.8.0). The commit history runs from 2025-11-30 to 2025-12-22: 54 commits under three committer identities. This is a review of the code and deployment files only. It says nothing about how the event itself went beyond what the files show, and it is meant as a set of lessons rather than criticism: a small team shipped a working event in three weeks.

## 1. What was built

| Area | What is in the fork |
|------|---------------------|
| Core | Effectively stock 3.8.0. Two files differ: a whitespace-only change in `views.py` and a duplicated `attribution` field in `schemas/challenges.py` |
| Themes | Four custom themes (`stargaze` 353 files, `lemon-stars` 169, `galaxy-space` 162, `fractals` 30), four files edited in `core`, one in `admin`, and the deprecated core theme deleted. The look was a dark star-field with lemon-yellow accents |
| `docker_challenges` | Per-team containers across several Docker hosts over the Docker API (TLS optional), domain mapping, health monitoring, dynamic flags. One 3,285-line file |
| `mana_system` | Caps concurrent instances per team, calculated from live containers rather than stored. The idea comes from the ctfer.io chall-manager |
| `anti_cheat` | Batch detectors for duplicate flags, brute force, IP sharing (3 users per IP), solve-sequence similarity and timing similarity, with an alert dashboard |
| `server_monitor` | Admin metrics dashboard |
| `subquestionchallenge`, `geo_challenges`, `ctfd-notifier` | Multi-part questions, a Leaflet map challenge type and a Discord/Telegram/Twitter notifier, all third-party code. The `geo_challenges` license file is the size of the GPL-3.0 text. Confirm before reuse |
| Content | `test_challenges/` with eight sample scaffolds (TCP, web, RSA, pwn, dynamic flag and others), an `Archive/` of challenge assets, and a flag format of `L3m0n{...}` |

## 2. Deployment as shipped

| Item | Finding |
|------|---------|
| Workers | `WORKERS=1` in `docker-compose.yml`, so one gunicorn worker served everything |
| Edge | `nginx` with `worker_processes 4`, TLS 1.2/1.3 and `gzip on` with default types (so JS, CSS and JSON go uncompressed). `proxy_cache off` and `proxy_buffering off` for every path. No static serving, no microcache, no edge rate limiting, `client_max_body_size 4G` |
| Data tier | `mariadb:10.11` with durability flags set, but default credentials (on an internal-only network). `redis:4` with AOF |
| Web container | `user: root` with **`/var/run/docker.sock` mounted**. Any code execution in CTFd or a plugin is full host control |
| Container limits | The container-create calls set port bindings, `RestartPolicy: unless-stopped` and a network. No memory, CPU, pids, capability or read-only settings were found |
| Deploys | `deploy.sh` runs `docker-compose down` then `up -d --build`, so every deploy is downtime |
| State | Site config, homepage, sponsors and settings were patched into the database with SQL and Python scripts (`update_homepage.sql`, `restore_sponsors.sql`, `settings_recovery.sql`, `ctfd_recovery_*.sql`). The recovery files suggest at least one incident where configuration had to be rebuilt |

## 3. The four pain points, mapped to evidence

**Load and outages**
- One worker, an unprofiled edge and every asset served by Python (see [S1, S8 in doc 01](01-ctfd-core.md)).
- A very old Redis, and sessions stored in it (S6).
- Downtime on every deploy.

**Challenge hosting**
- The Docker socket inside a root web container.
- No resource limits on spawned containers, so one team's fork bomb or memory leak can starve the host.
- Orchestration, cleanup and health checks run inside the web process, in a 3,285-line file with no tests.
- The mana quota idea is sound and worth keeping.

**Cheating and abuse**
- The detector heuristics are reasonable, but detection is batch-scanned and triggered from an admin route, so it runs when someone remembers.
- "3 users per IP" is the wrong default for on-campus play behind one NAT, and for shared labs.

**Look and authoring**
- Four theme iterations in three weeks is a sign of design churn without a design system.
- The homepage was stored as a database page with Jinja tags that never rendered, so the screenshot in the repo shows `{{ Configs.ctf_name }}` and `{% if not authed() %}` as literal text and a broken logo.
- The repository holds no authoring pipeline: no CI, no `challenge.yml` convention, and no tests that touch the custom plugins. The only authoring material is a README with build commands for the sample scaffolds. Authors may have had other tooling outside the repo.

**Process**
- Most commit messages are "update". There was no PR flow and no tests for the custom code.
- The upstream root `LICENSE` and `README` are missing from the fork's root. Apache-2.0 expects the license text to travel with redistributed copies.

## 4. Repository hygiene

The public repository history contains database material (a raw MariaDB data-directory backup, SQL dumps and a Redis snapshot) and a curl cookie jar. These should never be in a public repo. The specific counts and paths have been given to the project owner privately instead of being published here. Recommended: purge them from history and rotate anything reused.

## 5. Reuse assessment

| Component | Verdict | Reason |
|-----------|---------|--------|
| Mana quota concept | Keep | Calculated rather than stored, so no desync. Re-implement inside the new instancer |
| Anti-cheat heuristics | Adapt | Keep the ideas. Make them incremental, NAT-aware and evidence-preserving, with an admin review queue |
| `docker_challenges` | Rewrite as an external service | No Docker socket in the web tier, hard resource limits, per-team networks, TTLs and tests |
| `server_monitor` | Replace | Prometheus and Grafana do this better. Keep a small admin widget at most |
| `ctfd-notifier` | Keep, slimmed | A Discord webhook for first blood is enough |
| `subquestionchallenge`, `geo_challenges` | Evaluate | Useful challenge types. Check licenses first |
| Themes | Drop | The new design replaces them |

Code reuse needs the original authors' permission, because the repository has no license file. Ask them.

## 6. Requirements this adds for 2026

1. Config, theme and pages in git, never patched into a live database.
2. A hardened, reproducible deployment with real workers, static serving, backups and a tested restore.
3. An instancer outside the web tier, with limits, quotas, TTLs and per-team flags.
4. Per-account limits and NAT-aware abuse detection.
5. An author kit with CI so 30 authors can work in parallel.
6. A private home for challenges and story, plus a repo hygiene check in CI.
7. A design system, so the look is decided once.
