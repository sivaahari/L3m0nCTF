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

## Not in this part

The real load test and the dress rehearsal (SP9), the pages (SP6), instances (SP4), the abuse checks (SP5).
