# L3m0nCTF 2026 platform: build design (Task 4)

Status: **draft for owner review, 2026-10-04.** It turns the approved choices (CTFd 3.8.x extended by plugin and theme, code-led visuals, story overlay, the eleven page concepts, CTFtime listing) into a buildable design. It replaces the sketch in [doc 03](../../analysis/03-options-and-recommendation.md) section 3. Story and visual details stay in the private repo; this document carries no story material.

Inputs: [01 CTFd core](../../analysis/01-ctfd-core.md), [02 last year's fork](../../analysis/02-last-year-fork.md), [03 options](../../analysis/03-options-and-recommendation.md), [CTFtime requirements](../../research/ctftime-requirements.md), [CTFtime OAuth and live feed](../../research/ctftime-oauth-and-live-feed.md), and the approved page concepts (private).

## 1. Goal and non-goals

**Goal.** A CTFd-derived platform that runs a 24-hour online Jeopardy prelims for up to 1,000 teams, is listed on CTFtime, carries the story overlay, does not look like CTFd, and is reused for the finals. It is verified end to end before the feature freeze (section 8).

**Non-goals.**
- Editing CTFd's own files. Everything is a plugin, a theme or a service beside it.
- Running challenge code on the web tier.
- User-uploaded avatars, a custom admin UI (the stock admin stays, locked down), a mobile app, an offline or LAN build, payments.

## 2. Fixed points and assumptions

Fixed by your approvals: option A, story as an overlay on an open board, code-led visuals, the eleven page concepts, CTFtime listing, a 1,000-team capacity target, work lands on `pre-deployment` before `main`.

Assumptions. Each one is a place where a different answer changes the design, so they are also listed as decisions in section 10.

| # | Assumption | If it is wrong |
|---|------------|----------------|
| A1 | Prelims start Saturday 2026-11-14, freeze Saturday 2026-11-07 | The calendar in section 7 shifts. Nothing else changes |
| A2 | Hosting is plain Linux VMs with Docker: one app host (8 vCPU, 16 GB or more), two to four challenge hosts (8 vCPU, 16 GB each), one small ops host, a static IP for the app host, and wildcard DNS for instances. The challenge-host count follows the instance target: about 500 live instances at 128 MB each is about 64 GB of RAM | If you have Kubernetes, the instancer choice changes (section 6, SP4). If hosting is weaker, the load and instance targets drop |
| A3 | An official domain exists or will exist within days | Email deliverability, TLS and the CTFtime URL all wait for it. A GitHub or Cloudflare Pages address works as the interim public page |
| A4 | A transactional email provider, with SPF, DKIM and DMARC set up on our sending domain | Verification emails land in spam or are rejected, and registration stalls |
| A5 | Cloudflare (free plan) sits in front of the web and landing hostnames. Instance TCP ports are not proxied | Without it, DDoS protection and edge caching of static files come from nginx alone |
| A6 | You and I build. About 30 authors write challenges in parallel. A named person owns DevOps during the event | Someone must be on call. This is the biggest non-technical gap |
| A7 | About one in five challenges needs a per-team instance. The rest are files or shared services | A higher share raises instancer and host sizing |

## 3. Architecture

```mermaid
flowchart LR
  P[Players] --> CF["Cloudflare<br/>TLS, cache, DDoS"]
  CF --> NX["nginx<br/>static, microcache, limits, error slates"]
  NX --> LP["Landing site<br/>static, deployed separately"]
  NX --> APP["CTFd x N<br/>gunicorn + gevent<br/>theme l3mon + plugins"]
  NX --> RT["Centrifugo<br/>realtime gateway"]
  APP --> DB[(MariaDB)]
  APP --> R[("Redis<br/>cache, sessions,<br/>event stream, snapshots")]
  APP --> S3[("S3-compatible storage<br/>challenge files, presigned URLs")]
  R --> W["Worker<br/>snapshots, first blood,<br/>story, feeds"]
  W --> DB
  W --> R
  W -->|publish| RT
  W -->|atomic write| FEED["standings.json"]
  FEED --> NX
  APP -->|REST + token| INS["Instancer"]
  INS --> CH["Challenge hosts<br/>shared services + per-team instances"]
  OPS["Prometheus, Grafana, Loki, Alertmanager to Discord, status page"] -.-> APP
  OPS -.-> W
  OPS -.-> INS
  OPS -.-> NX
```

**Rules that keep it fast and safe**
1. **The request path stays thin.** Anything derived (scoreboard, board model, first blood, story progress, feeds) is computed by the worker and read from Redis. CTFd workers do CRUD and serve the page.
2. **Scoreboard truth is the database.** The worker takes a snapshot every 5 seconds from CTFd's own standings function, so ranks always match CTFd's rules (tie-break, hidden teams, freeze, brackets). There is no incremental leaderboard to drift.
3. **Events are nudges, not data.** A realtime message says "version 812 is out". Clients refetch with an ETag and mostly get a 304. Payloads stay tiny.
4. **Helpers can fail without taking the player path down.** If the worker or the gateway stops, solves still commit, the board falls back to a slower direct read from the database, and pages fall back to polling. Redis is different: sessions and the cache live there, so it gets persistence, a restart policy, monitoring and a failure drill (section 8).
5. **The web tier has no Docker socket and no route to challenge hosts except the instancer API.**

### Components

| Component | Runs as | Responsibility | State | How it scales |
|-----------|---------|----------------|-------|---------------|
| nginx | container | TLS, static and theme assets with immutable cache headers, microcache for public GETs, request limits, static error slates, `/admin` allow-list | none | one per app host |
| CTFd | image, N gunicorn+gevent workers | Auth, teams, challenges, submissions, admin, REST API | MariaDB, Redis | add workers or a second app host (sessions are in Redis, files in S3) |
| `l3mon` theme | built static assets plus Jinja templates | Every player page | none | served by nginx |
| Plugins | inside the CTFd image | See section 6 | MariaDB tables via plugin migrations | with CTFd |
| Worker | same image, second command | Snapshotter, event handlers, feed writer | Redis, reads MariaDB | one active (Redis lock), one standby |
| Centrifugo | container (Apache-2.0) | Realtime fan-out over WebSocket with HTTP streaming and SSE fallbacks, channel history | Redis | one node is ample for 2,500 connections |
| MariaDB | container or host | System of record | disk | tuned pool, off-host backups |
| Redis | container | Cache, sessions, event stream, snapshots | RAM, persistence on | one node |
| S3-compatible storage | Cloudflare R2 or MinIO | Challenge files, served by presigned URL so downloads never touch Python | objects | provider |
| Instancer | service on each challenge host | Per-team instances, TTL, quotas, limits, per-team flags | Redis and Docker labels | one agent per host, a small scheduler picks the host |
| Ops stack | containers on the ops host | Metrics, logs, alerts, status page | volumes | small |

## 4. Key decisions, with the options considered

| Topic | Options | Choice | Why |
|-------|---------|--------|-----|
| Theme | (a) server-rendered Jinja shell plus small JavaScript islands, (b) a single-page app inside the theme, (c) restyle the stock theme | **(a)** | Forms work without JavaScript, text mode is the same routes, the JavaScript budget is easy to hold, and one theme build ships with the image. A single-page app costs more bytes and breaks the no-JavaScript fallback |
| Realtime | (a) CTFd's built-in event stream, (b) a thin custom service, (c) **adopt Centrifugo** | **(c)** | The built-in stream holds one greenlet per tab and only carries admin notifications. A custom service is code we would have to harden. Centrifugo is Apache-2.0, supports WebSocket, HTTP streaming and SSE, channel history with recovery, a Redis engine and JWT auth |
| Scoreboard load | (a) patch CTFd's cache behaviour, (b) read replica, (c) **snapshot worker plus nginx microcache** | **(c)** | CTFd 3.8.8 clears its standings and challenge caches on every flag submission, wrong ones included, so under load they are nearly always cold. Snapshots decouple ranking cost from submission rate and need no core change |
| Challenge files | (a) nginx X-Accel redirects, (b) **S3-compatible storage with presigned URLs** | **(b)** | CTFd supports an S3 uploader out of the box. Large downloads never occupy a Python worker |
| Instancer | (a) **thin service on Docker hosts**, (b) `ctfd-whale` (Swarm and frp), (c) `ctfd-chall-manager` (Kubernetes and Pulumi) | **(a)** unless you have Kubernetes | Smallest ops surface in six weeks. Revisit if A2 changes |
| Deployment | (a) **compose on VMs, blue/green behind nginx**, (b) Kubernetes | **(a)** | Fewer moving parts for a four-week build and a single event |
| Plugin boundary | patch core, or stay in extension points | **Extension points only**: blueprints, challenge types, config, SQLAlchemy listeners, Flask hooks. Where a public function must be wrapped, a startup check fails loudly if CTFd's version differs from the pinned one | Keeps upgrades possible and the diff against upstream empty |

## 5. Data flows and contracts

### A solve

```mermaid
sequenceDiagram
  participant B as Browser
  participant A as CTFd worker
  participant D as MariaDB
  participant R as Redis stream
  participant W as Worker
  participant G as Centrifugo
  B->>A: POST /api/v1/challenges/attempt
  A->>D: insert solve, commit
  A-->>B: correct, new value
  A->>R: XADD solve event (after commit, best effort)
  R->>W: consume
  W->>W: first blood, story triggers, counters
  W->>G: publish to the team channel, and rare public events
  G-->>B: private event, the tile updates at once
  Note over W,G: scoreboard ticks come from the 5 s snapshot loop, not from each solve
```

The hook is a SQLAlchemy `after_insert` listener on the solve model, so it covers every challenge type. It queues the event during the transaction and publishes only after the commit, and it is wrapped so a failure is logged and never fails the submission. Wrong submissions, hint unlocks and announcements use the same mechanism. A reconciler rebuilds derived state from the database every few minutes, so a lost event only delays a notification.

### Public interfaces added by the plugins

| Interface | Auth | Caching | Notes |
|-----------|------|---------|-------|
| `GET /api/v1/l3mon/board` | session | ETag, private | Channels, tiles, state per tile for this team, solve counts, first-blood flags, meter. One request renders the board. Built from a cached challenge-metadata blob, the latest snapshot, and this team's solved set |
| `GET /api/v1/l3mon/scoreboard` | public | microcache 5 s | From the snapshot. Brackets, search and paging server-side |
| `GET /api/v1/l3mon/ticks` | public | microcache 3 s | `{ver, ts}`. The polling fallback |
| `GET /api/v1/l3mon/realtime/token` | session | none | Short-lived connection token that also grants the channel subscriptions |
| `GET /ctftime/standings.json` | public | static file, 15 s | Written by the worker, served by nginx. See SP8 |
| `GET/POST/DELETE /api/v1/l3mon/instances/...` | session | none | Start, status, renew, stop |
| `GET /text/...` | session or public | per page | Text mode, the no-JavaScript view |
| Stock `/api/v1/challenges/<id>` and `/attempt` | session | none | The programme panel uses these directly |

Stock `/api/v1/challenges`, `/scoreboard` and `/api/v1/scoreboard` stay available for tools but are rate-limited at the edge, and the theme does not call them.

**Contract tests.** The new board and scoreboard endpoints must return the same visible challenge set and the same ranking as the stock endpoints for the same fixtures (hidden, locked, prerequisite, dynamic-value, tie, freeze, bracket cases). This is how we know the reimplementation is faithful.

### Realtime channels

| Channel | Who | Content | History |
|---------|-----|---------|---------|
| `pub:ticks` | everyone | `{ver}` at most once a second | 1 message |
| `pub:events` | everyone | first blood, story milestone, announcement | 50 messages, 10 min |
| `team:ID` | that team only | own solve, hint unlocked, instance expiring, story unlock | 20 messages, 30 min |

Clients are subscribed on the server side through the `channels` claim of the connection token, so there are no per-channel tokens. Fan-out is bounded by design: public channels carry coalesced ticks and rare events, never one message per solve. History only smooths short reconnects. After any reconnect the client refetches the board and scoreboard, so correctness never depends on message recovery.

## 6. Sub-projects

Each gets its own plan, then implementation on `pre-deployment`, then staging verification and your approval. Effort is a rough estimate in working days for one builder and will be reviewed in each plan.

### SP0 Foundation (3 to 4 days)

**Delivers.** Monorepo skeleton (below), a CTFd 3.8.8 image with safe dependency bumps and our plugins and theme baked in, compose stacks for dev, staging and production, nginx and MariaDB and Redis configuration, config as code (`config/event.yaml` and a seed tool that rebuilds a working instance from an empty database in minutes), CI, and the `pre-deployment` to staging deploy.
**Done when.** `docker compose up` gives a working CTFd with the empty `l3mon` theme and plugins loaded, CI is green (lint, unit tests, CTFd's own suite, `pip-audit`, image scan, hygiene scan), a push to `pre-deployment` updates staging, and a restore from backup is rehearsed once.
**Notes.** MariaDB `max_connections` must exceed workers times pool size. Sessions in Redis, `SECRET_KEY` set, `WORKERS` matched to cores. CTFd's own tests run against our plugin set, so upgrades stay safe.

```
.
├─ deploy/       compose files, nginx, mariadb, centrifugo, prometheus, grafana, runbooks, scripts
├─ docker/ctfd/  Dockerfile: pinned CTFd, dependency bumps, plugins, built theme
├─ plugins/      l3mon_core, l3mon_story, l3mon_guard, l3mon_ctftime, l3mon_instances
├─ services/     worker, instancer
├─ ui/           theme sources (Vite, TypeScript), landing site, tile pipeline, design tokens
├─ config/       event.yaml (public example), JSON schemas for every config file
├─ tests/        contract, e2e, load
└─ tools/        the l3mon command line: seed, sync, export
```

The private repo holds the real challenges, story, flags, channel grouping and secrets.

### SP1 Public landing page and CTFtime pack (2 to 3 days, first)

**Delivers.** The static landing page from the approved startup concept: name, dates in UTC and IST, format, rules, prizes, registration link, scoreboard link, contact, organiser, Open Graph tags, schema.org `Event`, a countdown, and the rules and FAQ pages. Deployed separately (Cloudflare Pages with `pre-deployment` previews, or GitHub Pages) so it survives platform outages. A one-page CTFtime submission pack (event text, logo, links, restrictions to tick).
**Done when.** It is public, under 60 KB on first load, passes accessibility checks, and the CTFtime submission can be filed. This unblocks listing, which has an unknown lead time.

### SP2 Author kit and challenge pipeline (4 to 5 days, first)

**Delivers.** In the private challenges repo: a challenge template with `challenge.yml` (ctfcli format plus an `l3mon:` block for channel, order, optional tile image and story hooks), a `solution/solve.py` convention, Dockerfile templates for web, pwn (nsjail), crypto, Web3 (private chain per instance), LLM (guarded proxy), and file-only categories, a review checklist, and CI that lints the schema, checks the flag format, builds the image with hardening flags, starts it, runs `solve.py` against it and requires the flag to match, and scans for secrets and oversize files. A `l3mon sync` command pushes challenges and metadata to staging.
**Done when.** One sample challenge per template passes CI and appears on the staging board, and the 30 authors have a one-page guide. Shared (non-instance) challenges deploy as plain compose projects on a challenge host.

### SP3 Data pipeline: snapshots, board and scoreboard APIs, realtime (5 to 6 days)

**Delivers.** The plugin `l3mon_core` and the worker: the after-commit event hook, the 5 s snapshotter (it calls CTFd's own `get_standings` through the `uncached` original that Flask-Caching keeps on every memoised function, so ranking rules stay CTFd's; to be confirmed against the pinned Flask-Caching 2.3.1 in the plan), the board and scoreboard and ticks endpoints, the realtime token endpoint, Centrifugo configuration, nginx microcache rules, metrics. When the snapshot is older than 30 s the endpoints compute from the database, rate-limited, and an alert fires.
**Done when.** Contract tests pass, p95 for the board endpoint stays under 150 ms at 400 requests per second on the staging host, the scoreboard reflects a solve within 5 s, killing the worker or the gateway degrades gracefully, and the stock endpoints are rate-limited at the edge.

### SP4 Instances and dynamic flags (8 to 10 days)

**Delivers.** The `l3mon_instances` challenge type and the instancer. API: start, status, renew, stop. Quotas (about three live instances per team, one per challenge, restart cool-down). TTL about 45 minutes, renewable. Hardened containers (CPU, memory and pid limits, `no-new-privileges`, all capabilities dropped, read-only root where possible, tmpfs), per-instance networks, egress blocked, no metadata addresses, images from a private registry. HTTP instances through a reverse proxy on wildcard hostnames, TCP instances on a port range. Docker socket only inside the instancer, behind a socket proxy that allows only the verbs it needs. Per-team flags: the instancer injects `HMAC(secret, team, challenge)` formatted as the flag, and CTFd verifies by recomputation, so nothing per-team is stored. Reaper loop, reconcile on boot, admin kill-all.
**Done when.** About 100 instances run on one 16 GB challenge host within their limits (the packing limit is measured, not guessed), the scheduler spreads instances across hosts and the fleet is sized for about 500 live instances, an instance cannot reach another instance, the host or the internet (tested), TTL and renewal behave, and a flag from team A submitted by team B is rejected and recorded as evidence (SP5).
**Fallbacks.** If time runs short: HTTP-only first, TCP and Web3 second. If A2 turns out to be Kubernetes, adopt `ctfd-chall-manager` instead and shrink this to an integration.

### SP5 Anti-abuse suite (5 to 6 days)

**Delivers.** `l3mon_guard`: mandatory email verification, a disposable-domain blocklist, CAPTCHA (Cloudflare Turnstile) on register, login after failures and reset, per-account and per-team limits (CTFd's own per-account submission limit stays authoritative), NAT-aware IP heuristics (campus ranges allow-listed, no IP-only bans), a team-name policy (case-insensitive unique, CTFtime-name hint), an evidence table and an admin review queue (cross-team flag reuse, burst patterns, shared devices). Human decisions only: no automatic bans beyond hard rate limits.
**Done when.** Scripted abuse scenarios (mass registration, credential stuffing, flag brute force, flag sharing) are blocked or logged, and a campus-sized NAT does not trip any limit in the load test.

### SP6 Theme and tile pipeline (15 to 20 days in two phases, the largest)

**Delivers.** The `l3mon` theme for every player page in the approved concepts: landing mirror, login, four-step registration, board, programme panel, teams and team pages, scoreboard, text mode, error slates, live wall, notifications. Sources in `ui/` (Vite, TypeScript, small web components, no framework runtime), design tokens shared with the landing site, and a build-time **tile pipeline** that slices each channel picture into tiles and writes two WebP variants per tile (clear about 6 KB, distorted about 2 KB) plus a manifest. No filters run in the browser in production.
**Mapping to CTFd.** Templates override CTFd's by name (`base`, `challenges`, `scoreboard`, `teams/*`, `users/*`, `settings`, `login`, `register`, `reset_password`, `confirm`, `page`, `errors/*`). CTFd's auth and team logic and rate limits stay as they are. The admin theme is untouched.
**Budgets (CI gates).** Landing 60 KB or less on first load. Board JavaScript 120 KB gzipped or less, CSS 40 KB or less, fonts 90 KB or less. Scoreboard under 30 KB gzipped for 100 rows. Text mode 15 KB or less per page. WCAG AA, keyboard everywhere, reduced motion honoured, a CLEAN switch, state never carried by colour or blur alone, no runtime third-party CDNs.
**Done when.** Every page works at 375, 640, 820 and 1180 px, works without JavaScript where it should, passes axe and the Lighthouse budgets in CI, and the end-to-end scenarios in section 8 pass against it.

### SP7 Story overlay engine (6 to 8 days)

**Delivers.** `l3mon_story`: nodes with triggers (a given solve, a channel count, the global meter), per-team progress in the database with a Redis cache, a global meter computed by the worker, lore unlocks, fragments as secret shares (any K of N reveal the finale), a finale meta-challenge hook, an auto-air fallback at a set time so the story ends even if few teams solve, a story-mode toggle (a plain CTF mode with the story hidden), and an admin editor. The story content itself is private config synced by `l3mon story sync`.
**Done when.** Meter and unlock events reach every client live, a team's unlocks are private to it, the K-of-N threshold and the fallback are covered by tests with a fake clock, and switching story mode off hides every story surface without breaking scoring.

### SP8 CTFtime integration (4 to 5 days)

**Delivers.** `l3mon_ctftime`, following the [research](../../research/ctftime-oauth-and-live-feed.md): the worker writes a minimal `standings.json` every 15 seconds (atomic, valid JSON, scored and visible teams only, freeze respected) which nginx serves directly (B12a); a final results export in the feed format after the freeze lifts (B12b, P0); a "Login with CTFtime" OAuth2 plugin with `profile:read team:read`, linking on CTFtime IDs, no stored tokens, never for admin accounts, and a "Link CTFtime" path from My Team (B21); a mock CTFtime provider for CI (B23); a stable egress IP to give CTFtime. The capture-log and maximal feeds wait for CTFtime's confirmation (B12c).
**Done when.** The feed validates against the published schema including emoji, right-to-left and quote cases, the file is never older than 2 minutes (alert), OAuth passes the mock-provider cases (success, 403, bad state, name clash, size limit), and a real end-to-end test is run the day the event is approved.

### SP9 Operations (6 to 8 days, runs alongside)

**Delivers.** Prometheus, Grafana and Loki with dashboards (event, database, hosts, instances), alerts to Discord (5xx rate, p95 latency, database connections, Redis memory, disk, feed staleness, instancer errors, worker lag), a status page, off-host backups (a full dump every 30 minutes plus binary logs shipped every minute, so about 5 minutes of data at risk) with a restore drill, a mail relay container with a queue so registration never waits on the provider, `/admin` restricted to a VPN or allow-list, secrets handling, runbooks (start-of-event, outage, DDoS, database failover, freeze scoreboard, emergency announcement, kill instances, rotate secrets, roll back a deploy), the k6 load suite, a security review (threat model, configuration checklist, instancer test), and a dress rehearsal.
**Done when.** The load test passes at twice target (section 8), the restore drill meets RTO 15 minutes, every alert has fired once on purpose, and the runbooks have been followed by someone other than their author.

### SP10 Finals mode (2 to 3 days, after the prelims)

Round gating, finalist import and carry-over, per-round boards, campus-IP allowances, and a second CTFtime event. Specified after the prelims, once finalist count and format are known.

## 7. Build order and calendar

```mermaid
gantt
  dateFormat YYYY-MM-DD
  title Build calendar, assuming prelims start on 2026-11-14
  section Foundation
  SP0 repo, image, compose, CI, staging :a1, 2026-10-05, 6d
  SP1 landing page and CTFtime pack :a2, 2026-10-05, 6d
  SP2 author kit :a3, 2026-10-05, 7d
  section Core
  SP3 pipeline and APIs :b1, 2026-10-11, 8d
  SP6 theme: auth, board, panel :b2, 2026-10-11, 12d
  SP4 instancer, HTTP first :b3, 2026-10-12, 10d
  SP5 anti-abuse basics :b4, 2026-10-16, 6d
  Staging open to authors :milestone, msa, 2026-10-18, 0d
  section Identity
  SP7 story engine :c1, 2026-10-20, 10d
  SP8 CTFtime feed and OAuth :c2, 2026-10-22, 6d
  SP6 theme: scoreboard, teams, text, slates, wall :c3, 2026-10-22, 10d
  section Ship
  Production hosting, email, registration opens :d1, 2026-10-24, 7d
  SP9 observability, backups, runbooks :d2, 2026-10-26, 8d
  Load test at 2x, fixes, security review :d3, 2026-11-02, 5d
  Feature freeze :milestone, msf, 2026-11-07, 0d
  Dress rehearsal :d4, 2026-11-08, 5d
  Prelims :milestone, msp, 2026-11-14, 0d
```

**Milestones**

| | Date | What must be true |
|---|------|-------------------|
| M0 | Oct 11 | Landing page public, CTFtime submission filed, author kit v0 in the authors' hands, staging deploys on push |
| M1 | Oct 18 | Staging works end to end with stock pages plus the new data APIs: register, team, sample challenges, solve, scoreboard from the snapshot. Authors are deploying to it. The new theme's login, board and panel follow by Oct 23 |
| M2 | Oct 31 | **Registration is open on production.** Hosting is provisioned, email deliverability tested, anti-abuse basics live. Registration usually opens one to two weeks before a CTFtime event, so production must exist early |
| M3 | Nov 1 | Feature complete on staging (tiers A and B). Load test at twice target begins on Nov 2 |
| M4 | Nov 7 | Feature freeze. Security review done. Every challenge deployed on staging with a passing solver |
| M5 | Nov 13 | Dress rehearsal complete, go or no-go |

**First implementation plan:** SP0, SP1 and SP2 together. They unblock the CTFtime listing, the authors and everything after them. Each later sub-project gets its own plan when its turn comes.

**Capacity check.** Added up, the estimates in section 6 come to about 58 to 75 builder-days. There are about 25 weekdays (34 calendar days) from Oct 5 to the freeze, and the tier A work alone is about 37 to 46 days on the same scale. Code written with me is faster than a solo builder, but review, hosting, DNS, email, the authors' challenges and load testing take the same wall-clock time, so the plan cannot assume tier B ships. Ways to compress, in order:
1. Agree the tiers now, and agree that the freeze date wins over features (D17).
2. Run independent sub-projects in parallel, each in its own git worktree, for example SP0, SP1 and SP2 in week one (needs your go-ahead to use sub-agents, D18).
3. Bring in a DevOps owner for SP9 and production hosting, and one front-end helper for the secondary theme pages.
4. If the load test or the security review fails late, a short slip of the prelims is cheaper than running unverified.

**Scope tiers.** The calendar holds only if the lower tier is allowed to slip.

| Tier | Contents |
|------|----------|
| **A: the prelims cannot run without it** | SP0, SP1, SP2, SP3 (snapshots, board, scoreboard, microcache), SP6 core pages (auth, board, panel, scoreboard, teams, slates), SP5 basics, SP4 for HTTP instances, SP9 basics (alerts, backups and a restore drill, load test, runbooks), the final results export |
| **B: the event's identity, built right after A** | SP7 story overlay (meter, lore, finale), realtime ticks, text mode, the live minimal feed, OAuth, TCP and Web3 instances, dynamic-flag sharing detection, live wall |
| **C: after the prelims** | SP10 finals mode, mascot shuffle, per-channel standings pages, the grid list view, post-event archive, optional LLM hint buddy, dynamic-score optimisation |

## 8. How "verified end to end" is defined

**Layers.** Unit tests per plugin and the worker. Contract tests against stock CTFd endpoints. Integration tests on the compose stack (CTFd, MariaDB, Redis, a mail catcher, a mock CTFtime provider). Playwright end-to-end scenarios. k6 load tests. Accessibility and budget checks. Resilience drills. A dress rehearsal with the authors as players.

**End-to-end scenarios (all must pass in CI on the compose stack, and again on staging before M4)**

| # | Scenario |
|---|----------|
| S1 | Register, verify email, create a team, land on the board |
| S2 | Join with an invite code, team-size limit enforced |
| S3 | Wrong login gives one generic message, throttling shows the slate, CAPTCHA appears after failures |
| S4 | Board renders within budget, channel switching works, signal and filters correct |
| S5 | Open a programme, download a file by presigned URL, unlock a hint (score drops), wrong flag, correct flag: the tile clears, scoreboard updates within 5 s, the CTFtime file within 15 s |
| S6 | Dynamic-value decay changes every solver's score, and our scoreboard equals the stock ranking |
| S7 | Instance: start, connect, TTL, renew, stop, expire, quotas, unique flag, cross-team flag recorded as evidence |
| S8 | Story: meter advances, public unlock reaches all clients, private unlock reaches only one team, K-of-N finale and the fallback work under a fake clock, story mode off hides everything |
| S9 | Kill the gateway: the page falls back to polling, then resumes |
| S10 | Freeze and unfreeze, final export matches the CTFtime schema |
| S11 | CTFtime OAuth against the mock provider (success, 403, bad state, name clash), and an admin account cannot be signed in through it |
| S12 | App down: nginx serves the static slate, and maintenance mode shows the countdown |

**Load gates (at twice the target in the table in doc 03).** About 5,000 concurrent browsers, 800 requests per second mixed, a 4,000-load burst in 60 seconds at start, 140 flag submissions per second. p95 under 300 ms and p99 under 1 s for reads, errors under 0.1%, scoreboard within 5 s, and as many live instances as the staging challenge hosts allow, up to the 500 target. Run on the real staging hardware; on a laptop only relative numbers are meaningful.

**Resilience drills.** Kill a CTFd worker, the worker process, Redis, the gateway and the instancer in turn. Restore the database from backup. Let the feed file go stale and confirm the alert. Fill the disk to 90% and confirm the alert.

**Security.** Threat model for the instancer and the admin surface, `pip-audit` and image scans in CI, a ZAP baseline scan against staging, secrets only outside git, admin routes behind an allow-list.

## 9. Risks

| Risk | Mitigation |
|------|------------|
| Capacity: the estimates are about two to three times the weekdays to the freeze (section 7) | Tiers and the rule that the freeze wins over features, adopt over build (Centrifugo, S3 storage, Turnstile), parallel sub-projects if allowed, extra hands for operations and the secondary pages, authors playtesting in parallel |
| CTFtime listing lead time is unknown | SP1 first, the submission is filed on M0 |
| Production hosting, domain or email arrive late | They are decisions this week (section 10). M2 depends on them |
| Instancer security | Separate hosts, socket proxy, hard limits, egress control, a review before M4 |
| Contract drift between our endpoints and stock CTFd | Contract tests, a version-pinned startup check, CTFd's own suite in CI |
| Load assumptions are wrong | Measure early: a first load run on staging by M1 |
| One bad deploy during the event | Blue/green, rollbacks rehearsed, change freeze from M4 |
| Story or answers leaking | Private repo only, public repo hygiene scan in CI |

## 10. Decisions for you

The first five block work in the next two weeks. IDs continue the list in [PROGRESS.md](../../../PROGRESS.md).

| ID | Decision | My recommendation |
|----|----------|-------------------|
| D15 | Approve this design, including Centrifugo, S3-compatible file storage and Cloudflare in front | Approve |
| D16 | Approve pulling the official images and packages listed in section 11 | Approve |
| D10 | Hosting provider, budget, who has root and who is on call during the event | The layout in A2. Needed by about Oct 14 to provision M2's production stack |
| D13 | Official domain, email sending domain, logo, confirmed dates in UTC | A domain this week. Until then the interim page is public on Pages |
| D11 | Create the CTFtime account and organiser team (only a team member can) | Do it as soon as the landing page is live |
| D12, D14 | Put the CTFtime questions (OAuth and feed research, section 6) to CTFtime, and later send them our static IP | I draft the text, you post it from your GitHub account |
| D2 | Instancer: build thin or adopt | Build thin, unless you have Kubernetes |
| D5 | Permission to reuse last year's plugin code | Write fresh. Reuse ideas, not code, unless the authors agree |
| D8, D9 | Finals continue the story? Myth and religious figures excluded? | Decide before the story config is written |
| D10 (cont.) | Registration eligibility, team size limit, finalist count, sponsors | Open to all teams, team size four, finalist count after the prelims |
| D17 | Accept the scope tiers (section 7) and the rule that the freeze date wins over features | Accept |
| D18 | May I run independent sub-projects in parallel with sub-agents, each in its own git worktree? | Yes for SP0, SP1 and SP2 in week one. I review and merge each before it lands on `pre-deployment`. It costs more tokens, so it is your call |

## 11. Environment and downloads

The machine has 8 cores, about 16 GB RAM and Docker Desktop running (4 CPUs and about 7.7 GB allotted to Docker), so the whole stack can be built and tested locally, at reduced load.

Approving D16 covers pulling from their official registries only: container images (CTFd or its Python base, MariaDB, Redis, nginx, Centrifugo, a mail catcher, Prometheus, Grafana, Loki, k6, MinIO), Python packages for the plugins, worker and instancer, Node packages for the theme build, and Playwright's browser builds for the end-to-end tests. Nothing is downloaded from other sources, and nothing is installed outside the project and Docker.
