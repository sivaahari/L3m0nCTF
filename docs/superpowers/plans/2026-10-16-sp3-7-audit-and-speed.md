# SP3 part 3.7: the speed check, the isolation decision and the audit of the whole plugin layer (implementation plan)

> Built test first, one task at a time, with the independent review at the end. Measure, then decide; nothing here is changed on a feeling.

**Goal.** Before the pages (SP6), the instances (SP4) and the abuse checks (SP5) are built on top of it, prove that the plugin layer built in parts 3.1 to 3.6 is fast enough for the event's load and safe in how it fits together, decide whether CTFd's database connection should run at READ COMMITTED, and fix what the measurements and a cold read of the whole layer turn up.

**Why now.** Each part was built and reviewed on its own. Nobody has yet looked at the layer as a whole: how many database statements a page costs, whether one plugin's hook costs the others something, whether two plugins can disagree about the same fact (the tick, the caches, who counts), and whether every door that CTFd leaves open has been closed once and only once. Later parts add load (a theme fetching these endpoints, instances), so this is the cheapest moment.

## What counts as fast enough (from the build design, section 1 of the options paper)

| Target | Value |
|---|---|
| Sustained mixed reads | 250 to 400 requests per second |
| Flag submissions, peak | about 70 per second |
| Latency | p95 under 300 ms and p99 under 1 s for reads |
| Errors | under 0.1% |
| Scoreboard freshness | within 5 s (here: the tick, 15 s polls, a refetch only when it changed) |

The real load test needs the real hosts and belongs to SP9 (k6, twice the target). This part measures what can be measured on the development stack: the cost of one request in database statements and milliseconds, and how that cost grows with studios and programmes.

## Tasks

1. **The query budget** (`plugins/l3mon_board/tests/test_query_budget.py`). A world of 60 programmes on 8 channels and 40, then 200 studios with solves, hints and bonuses. For each player endpoint (`ticks`, `board`, the programme panel, `guide`, `guide/epg`, `scoreboard`, `scoreboard/rows`, the bell, the story list) count the SQL statements a request makes (a SQLAlchemy event on the engine) and assert that the count does **not grow with the number of studios** and grows at most linearly with programmes. Anything that does is an N+1: fix it (one grouped query, or a cache keyed by the tick) with a test that was red first.
2. **The cost of the hooks.** The same count for a request that is *not* ours (a CTFd page, a static file, a wrong flag): our `before_request` and `after_request` hooks (the minute check, the lift watcher, the ratings door, the tick, the doors) must add at most a small constant number of statements, and none for static files.
3. **A local load probe** (`tools/load_probe.py`, standard library only). Four gevent workers behind nginx on the compose stack; 300 simulated players polling `ticks` every 15 s (with jitter), fetching the board and Guide when the tick moves, and 20 flag submissions a second for a minute, against a world of 60 programmes. Record p50, p95, p99 and errors per endpoint. This is a smoke test for gross mistakes, not the load test; the numbers go into the verification log with the machine's size.
4. **The isolation decision.** Today the connection runs at MariaDB's default (REPEATABLE READ) and the plugins work around it (`l3mon_core/locks.py`, the commit before a locking read in `freeze.py`, the second cache clear). READ COMMITTED would remove the cause of those stale-view findings (found by the independent reviews of 3.2, 3.3 and 3.6) and reduces gap locking, but it changes behaviour for CTFd's own code. Measure on a real MariaDB 10.11: (a) CTFd's own 676 tests, (b) our MariaDB suite (eight workers revoking, restoring, solving, lifting), (c) the lock-wait and deadlock counters (`SHOW ENGINE INNODB STATUS`, `information_schema.INNODB_METRICS`) under the load probe, each at both levels. Decide on the numbers: change it (one setting, set where CTFd creates its engine options, with a startup check that the level is what we expect) only if (a) and (b) pass unchanged and (c) is no worse; otherwise keep the default and record why.
5. **The audit of the whole layer.** A cold read by an independent reviewer of the six plugins together, with four questions: (a) is every door CTFd leaves open closed exactly once (the list of doors in the plans against the routes in CTFd's source in the image; `python -m l3mon api-rules check`); (b) can two plugins disagree (the tick, the caches, who counts, the clock, the freeze and the release plan), including the order their hooks run in; (c) does anything log or return a secret, a flag, a story word, or the true value during a freeze; (d) what happens when Redis, the database or the story folder is slow, full or gone for a minute (the request must fail softly or loudly, never wrongly); (e) every pattern of the form `^...$` in the other plugins (the release plan's slugs, the settings, the CTFtime names and ids): in Python `$` also matches before a final line break, which the review of the pictures found in the story plugin; each one is either changed to `\Z` or shown to be checked again afterwards.
6. **Records**: this plan with what changed, the verification log, PROGRESS, and the contract for any answer that changed.

## What was measured, what was found, what changed (2026-10-16)

Every number below was measured on the development machine (a laptop: 4 CPUs and 8 GB shared with Docker Desktop, the probe and everything else), against the compose stack with MariaDB 10.11, Redis 7, nginx and four gevent workers. It is a smoke test for gross mistakes and a way to size the host, not the load test (SP9, k6 on the real hosts).

### 1. The query budget (`test_query_budget.py`)

No player endpoint asks the database more for more studios, more programmes on air, more solves, or a bigger studio (except as below). Measured with 4 studios and 8 programmes, then 29 studios, then 40 programmes with 30 solves of the viewer, then a studio of five players (statements for the first request after the caches were emptied / for the same request again):

| Endpoint | cold / warm | | Endpoint | cold / warm |
|---|---|---|---|---|
| `ticks` | 12 / 8 | | `scoreboard` | 8 / 5 |
| `board` | 17 / 13 | | `scoreboard/rows` | 8 / 5 |
| `guide` | 20 / 16 | | `bell` | 6 / 6 |
| `guide/epg` | 10 / 10 | | `story` list | 9 / 9 |

About 4 of those statements in every request are CTFd's own (the account, twice, and the studio, each with its custom-field joins). The one growth that exists is CTFd's: it works out a studio's score member by member (two statements each) when its cache is cold, so a full studio of four players costs 8 more on a cold request. It is bounded, and the test says so. Each endpoint also has a ceiling a little above today's cost, so a change that doubles one is noticed.

### 2. The cost of the hooks (`test_hook_cost.py`) — one defect found and fixed

A second app in CTFd's safe mode (none of our plugins) is the stock figure. Before the fix our hooks added **one database statement to every request**, a static file and the login page included: the release plugin's share door asked "is release control in use" before it had looked at the path. It now looks at the path first. Seven kinds of request (a path nobody serves, the login page, the challenges page, the player's own studio, a wrong flag, CTFd's challenge list, CTFd's scoreboard) now cost exactly what stock CTFd costs.

### 3. The load probe (`tools/load_probe.py`, `tools/load_probe_run.sh`) — one defect found and fixed

The probe makes 300 players in 100 studios and 60 programmes (15 of them dynamic), then plays the event's mix from a container on the stack's own network (nginx's limit of 100 requests a second for one address would otherwise stop it).

**The scheduler stampede.** The release scheduler keeps a record in the cache that lasts 30 seconds. When it ended, every request that arrived before the first reconcile finished ran a whole reconcile of its own, one after the other behind the plan's row lock. A lock watcher on MariaDB showed ten or more requests waiting on `SELECT ... FROM l3mon_channel ... FOR UPDATE` at once: **95 lock waits in one run, 150 seconds of waiting in all, the longest 4.45 seconds**, every 30 seconds. Now one request takes a flag (`claim` in `plugins/l3mon_core/claim.py`) and the others carry on at once. The same run afterwards, with 240 solves in it: **no lock wait and no deadlock at all.** (The flag was later reworked after the audit, see 5.)

**What a request costs** (120 players asking one endpoint as fast as they can, the CPU time read from the containers' cgroups; the application is the bottleneck, MariaDB uses 3 to 6 ms of CPU per request):

| Request | ms of CPU in CTFd | | Request | ms of CPU in CTFd |
|---|---|---|---|---|
| `/l3mon/healthz` (no account) | 4.1 | | `ticks` | 20.0 |
| stock `users/me` | 11.2 | | `board` | 36.8 |
| stock `challenges` list | 14.6 | | `guide` | 41.8 |
| stock `scoreboard` | 6.8 | | `scoreboard/rows` | 23.4 |
| | | | `bell` | 15.0 |

At the event's own mix (300 players poll every 15 seconds, fetch the board and the Guide when the tick moved, 20 flags a second) the probe made **75 requests a second and used 2.8 cores of CTFd and 0.45 of MariaDB: 37 ms of CTFd CPU per request on average**, about 27 requests a second per core; the machine was at its limit (the latencies in that run, p95 0.9 s for the tick and 1.7 s for the board, are the latencies of a saturated laptop, not a promise). The board and the Guide are half of the CTFd CPU of the mix. A player makes about 0.25 requests a second, so the plan's target of 250 to 400 requests a second is 1000 to 1600 players; at 37 ms that is **9 to 15 cores of CTFd** on a machine of this speed, plus the database (a sixth of that), Redis and nginx. A cloud core may be faster or slower than this laptop's; the real figure comes from the k6 test (SP9). What leadership needs to decide before the host is chosen is in `docs/remaining-work.md`.

Where the cost is: the platform itself is not the main cost (a bare route is 4.5 ms and CTFd's own signed-in read 11.5 ms). The board and the Guide cost 37 to 42 ms because each makes 12 to 15 statements, and every statement through PyMySQL (a driver written in Python) costs roughly 1 to 2 ms of CPU (an estimate from the counts and the totals, not a profile). The levers, none of which is taken yet: fewer statements for the board and the Guide (the plan, the on-air set and the programme rows are read three times); a short per-studio memory of the board and the Guide keyed by the tick (three players of a studio then cost one); a longer poll interval (30 seconds halves the polling); a C database driver in the image; more cores.

### 4. The isolation decision: keep REPEATABLE READ

Measured on a real MariaDB 10.11 at both levels (`L3MON_DB_ISOLATION=READ-COMMITTED tools/run-migration-test.sh`):

| | REPEATABLE READ (the default) | READ COMMITTED |
|---|---|---|
| Our suites that need a real MariaDB and Redis (84 tests: the migrations, the tick, eight workers racing a drop, a Revoke, a Restore, a bonus, the freeze) | 84 passed | 84 passed |
| CTFd's own suite on that MariaDB | 573 passed, 103 failed | 573 passed, 103 failed: **the same 103** |
| Those 103 failed tests alone | 103 passed | 103 passed |
| Lock waits and deadlocks under the probe at three times the polling and twice the flags (4000 to 4300 requests, no error) | 0 and 0 | 0 and 0 |

The 103 are a fault of the experiment, not of either level: they fail on the full run only (after a test has loaded our plugins, a later test's throwaway database has no `l3mon_channel` table), and the SQLite run of the same suite (676 passed) does not show it. So READ COMMITTED passes everything REPEATABLE READ passes and gives no measurable gain, because the one source of waits we found was the stampede and it is fixed. **Decision: no change** (a changed setting under the whole platform without a measured gain is risk without reward). The lever stays: `transaction-isolation = READ-COMMITTED` in `deploy/mariadb/l3mon.cnf`, proved safe by the runs above.

### 5. The audit of the whole layer (independent, read-only, measured on SQLite)

**Verdict before the fixes: 0 high, 3 medium, 4 low, 6 notes.** The doors are each closed once; the plugins agree on the tick, the caches, who counts and the clock; no secret, flag, story word or true frozen value is logged or returned. Every finding was reproduced as a failing test first, then fixed, then broken on purpose:

| # | Finding | Fix |
|---|---|---|
| M1 | A programme locked by a prerequisite showed its slug, number, channel and author again when its id was spelled leniently (`6.0`, `6e0`, on MariaDB `6abc`): CTFd compares the id as text in SQL where our door used `int()` (3.4's M1 reopened) | The door lets only plain digits through for a player (anything else is a missing programme); it now also covers the list of studios that solved a programme and one of its hints, which still answered for a locked one (L1) |
| M2 | The scheduler's new flag could be left with no lifetime (cachelib's Redis `add` is SETNX then EXPIRE; a lost second command leaves the key for ever, and Redis's volatile-lru never evicts it) and then no scheduled drop would ever be applied. The minute check had the same weakness before this part | One helper, `claim(key, seconds)`: the flag holds the time it was taken, and a flag older than its period (or from the future) is free. Used by both. Proved on a real Redis, including the lost EXPIRE |
| M3 | The load probe could change a real stack: the script rewrote the event window before checking anything, and the probe replaced the plan, released everything, and deleted accounts named `lp-*` | The script checks first that the running stack is the development one (the development-only administrator token mounted, `localhost` trusted); the probe refuses an address that is not on this machine, a plan with a channel it did not make, a platform with more than 25 other accounts, and an everyday prefix; it releases only its own ids, uses names that start with `zzprobe`, and never writes a setting |
| L1 | Stock routes still treating a locked programme as existing (solves, hints, the unlock reply, the list with `anonymize`) | Solves and hints closed (M1). **Accepted:** the list with `anonymize` is CTFd's own feature and nothing of ours sets it; the unlock reply differs only in 403 against 404 |
| L2 | The flag reply read the challenge with `int()`, so on MariaDB `1_5` was challenge 1 to CTFd and 15 to the reply | A reply is made only for a whole JSON number or plain digits |
| L3 | A request that skips the scheduler can carry the new tick with the old data (a comment claimed pages did not need the result) | The board, the Guide and the scoreboard read the tick before their data; the comment is corrected |
| L4 | A cache error right after a reconcile's commit skipped the tick, the announcement and the record | Wrapped like the other steps after a commit |
| N1 | The notification guard asked `is_admin()` (a cache read) on every GET before looking at the path | Path first |
| N2 | `str.isdigit()` accepts superscripts, which `int()` refuses: an administrator's typo in an award name made every award list a 500 | Plain digits only |
| N3 | Ten `$` patterns left in the story format (drawn art) | `\Z` |
| N4 | nginx's write detector did not count a write whose path held a decoded `%0A` (`.` does not cross a line break), so the rule "a write nobody listed is for the organisers' addresses only" skipped it (CTFd's own `@admins_only` still held) | `(?s)` and `\z`; an integration test sends six such writes |
| N5, N6 | A hint purchase moves the tick while frozen (by design, the demo does the same); a test-harness date artefact | none needed |

Not covered by the reviewer, covered here afterwards: a real Redis for M2 (`test_claim_redis.py`, 3 tests on a throwaway Redis), nginx's newline handling (the integration test, shown to fail on the old rule), and the isolation decision (section 4). The MariaDB-specific readings of an id (`6abc`, `1_5`) are closed by refusing anything but plain digits, which no database reads differently.

### Left for later parts

The k6 load test on the real hosts and the host's size (SP9, with leadership's decision on the host); the levers of section 3 if that test says the host is too small; a crew control for the finale's trigger and its button (SP6); the pages' own use of `/ticks` (SP6); the instances (SP4).

## Not in this part

The real load test and the dress rehearsal (SP9), the pages (SP6), instances (SP4), the abuse checks (SP5).
