# SP3 part 3.2: release control (implementation plan)

> **For agentic workers:** work task by task. Every test is written and **shown failing** before the code that makes it pass. All Python runs inside the platform image (`l3mon/ctfd:dev`) against CTFd's own test helpers (`tools/run-ctfd-tests.sh`, or the faster long-lived container described in the verification log). A security test is only accepted when it has also been **shown failing with its guard switched off**. Commit as `sivaahari` with no co-author line, on `pre-deployment`.

**Goal:** the crew decides which channels and programmes players can see, and when. A withheld programme does not exist for a player on any route. A scheduled drop happens on time, once, however many workers run, and is announced as "New on air".

**Architecture:** a new plugin `l3mon_release` holds the machinery (reconcile, scheduler, door guards, admin API, the crew's page); `l3mon_core` gains the shared rule ("what is on air") that every later endpoint asks, and the audit table every admin action writes to. CTFd's own challenge `state` is the one switch underneath (design decision C): it is **derived** from the release plan, so CTFd's stock endpoints and ours cannot disagree. Doors that CTFd leaves open get small guards.

**Tech stack:** Python 3.11 in the CTFd 3.8.8 image, Flask blueprints, Flask-SQLAlchemy (CTFd's `db`), CTFd's plugin migrations, Flask-Caching (Redis in production), freezegun, pytest, a real MariaDB 10.11 and Redis 7 for the concurrency and migration tests.

## What probing stock CTFd 3.8.8 found (2026-10-09)

A challenge was made visible, solved by team A (a hint unlocked, a wrong flag tried, a rating given, a file link made), then set to `hidden`, and every route was asked as team A and as team B (who never saw it). The probe is a scratch script; its result is the table below, and the door tests of Task 6 re-prove each row.

| Route | Stock CTFd with the challenge `hidden` |
|---|---|
| `GET /api/v1/challenges` (also with `q`, `name`, `category`) | not listed |
| `GET /api/v1/challenges/<id>`, `/solves`, `/solution`; `GET /api/v1/hints/<id>`; `POST /api/v1/unlocks`; `POST /api/v1/challenges/attempt`; `PUT /ratings` | the same answer as for an id that never existed |
| `/challenges/<id>/files`, `/tags`, `/topics`, `/hints`, `/flags`, `/requirements`, `/ratings` (GET); `/api/v1/statistics/*`, `/awards`, `/submissions`, `/comments`, `/tags`, `/topics`, `/files`, `/flags`, `/hints`, `/unlocks`, `/solutions` | administrators only (the same redirect as a missing id) |
| `GET /api/v1/scoreboard`, `/scoreboard/top/<n>` | no name, id or category (`challenge_id` is null); the step values of a team's score remain, because points are kept on a pull-back |
| **`GET /api/v1/teams/<id>/solves`, `/users/<id>/solves`, and the `me` lists** | **OPEN: name, category and value of the hidden challenge, to any signed-in player** |
| **`GET /api/v1/teams/<id>/awards`, `/users/<id>/awards`, and the `me` lists** | **OPEN: "Hint for <name>"** |
| **`/teams/<id>`, `/users/<id>`, `/team`, `/user` (pages)** | **OPEN: name and category in the solve list** |
| **`GET /files/<path>` (plain, or with a signed link)** | **OPEN: served to any signed-in visitor, and the signed link works for anyone, with no check of the challenge's state** |
| **`GET /share/solve?...` and `POST /api/v1/shares`** | **OPEN: any signed-in player can sign a link for any user and challenge; the link, public, prints the challenge name and value** |

The design (section 5) listed the doors from memory; this table replaces it. Four rows are new findings (the files, the social share, the `me` lists and the pages).

## Global constraints

- CTFd is pinned at 3.8.8; both plugins refuse to load on any other version. `l3mon_release` needs `l3mon_core` (it loads first, because CTFd loads plugins in name order).
- Times are naive UTC in the database; the round starts at 09:00 IST on 28 November 2026 (03:30 UTC). A scheduled time is an epoch second (UTC) in the API and shown in IST and UTC in the page.
- A programme is **on air** when its channel is released, it is released, and it is visible in CTFd, **or the broadcast has ended** (then every programme can be read, as in the demo). A `scheduled` entry whose time has come counts as released; a `scheduled` entry without a time counts as withheld.
- **Default deny:** once any channel exists (release control is in use), a challenge that is in no channel is not on air. With no channels at all (CTFd's own test suite, a fresh install) nothing changes from stock CTFd.
- A withheld programme's name, slug, number, category, difficulty, value, files, hints and flags never leave the server for a player: every door answers as for an id that never existed (status, body shape; request id aside).
- Every admin write is one transaction, repeatable without harm, clears CTFd's challenge and standings caches, moves the tick, writes an audit line (who, what, when, why), and is refused without CTFd's CSRF token and admin session. No raw SQL from input; text is length-limited and escaped on output.
- Nothing announces a drop before the start or after the end.
- Plugin files stay root-owned and read-only in the image; the crew's page loads its script and style from the plugin's assets (no inline script, so a Content-Security-Policy can be added later).

## Decisions made in this part (and why)

1. **`Challenges.state` is derived from the release plan, never set by hand.** Stock endpoints read it, so deriving it keeps them right with no per-route code. A `before_flush` hook (a) refuses to reveal a programme that is not on air (the state is forced back to `hidden`), (b) treats a hide done in CTFd's own challenge editor as a **withhold** in the plan (otherwise the next reconcile would show it again), and writes an audit line either way. A challenge editor `PATCH` that tries to reveal gets a plain 400 first.
2. **The scheduler is a check on each request, with one conditional update per challenge.** `UPDATE challenges SET state=... WHERE id=? AND state<>?` is the claim: only the request whose update changed a row announces that drop. A cached "next event" second (and the `end` setting it was computed from) makes the check free until something is due.
3. **Ended means everything is on air**, applied by the same reconcile at the `end` second, and undone if the crew later moves `end` later.
4. **The audit table moves from 3.3 to this part** (second migration of `l3mon_core`), because the first admin actions are release actions and the rule is that each one leaves a line.
5. **Social sharing is closed outright** (`/share/*`, `POST /api/v1/shares` answer 404): the platform has no use for it, and the endpoint lets a player sign a share for any user and challenge.
6. **nginx treats `/api/v1/l3mon/admin/` as administrator-only**, though the hand-written list lets players write under `/api/v1/l3mon/`; the admin prefix is added to the admin list (a new hand-kept file) and a test proves a non-organiser address is refused at nginx.
7. **Deferred, said plainly:** a by-hand editor for the plan itself (create a channel, place a challenge in a cell) and the `l3mon` command-line sync. This part ships the bulk call they will use (`PUT /api/v1/l3mon/admin/programmes`) and the release page. A plan is loaded with `curl` and an admin token until the author kit's `l3mon:` block exists.

## File structure

| File | Responsibility |
|---|---|
| `plugins/l3mon_core/models.py`, `migrations/b3d95f0a6c12_add_the_audit_table.py` | `Audit` model and a second revision (`l3mon_audit`); `Channel` and `Programme` default to `withheld` |
| `plugins/l3mon_core/audit.py` | `record(action, target, detail)`, `recent(limit)` |
| `plugins/l3mon_core/airing.py` | `release_active()`, `entry_on_air(state, at, t)`, `is_on_air(challenge_id, t)`, `on_air_ids(t=None)`, `to_epoch(moment)` |
| `plugins/l3mon_core/visibility.py` | now also asks `airing` (the one place every later endpoint asks) |
| `plugins/l3mon_release/__init__.py` | `load(app)`: guard, hooks, blueprint, menu entry |
| `plugins/l3mon_release/reconcile.py` | `desired_states`, `reconcile`, `announce`, `register_pull_back_handler`, the `before_flush` enforcement |
| `plugins/l3mon_release/scheduler.py` | the cached next event, `maybe_apply`, the request hook |
| `plugins/l3mon_release/guards.py` | the model getters, the file route, the social routes |
| `plugins/l3mon_release/plan.py` | validation and upsert for the bulk call |
| `plugins/l3mon_release/api.py` | `GET`/`PUT /api/v1/l3mon/admin/release`, `PUT /api/v1/l3mon/admin/programmes`, the page route |
| `plugins/l3mon_release/templates/l3mon_release/release.html`, `assets/release.js`, `assets/release.css` | the crew's page |
| `plugins/l3mon_release/tests/` | one file per module, `test_doors.py`, `test_scheduler_mariadb.py` |
| `deploy/nginx/snippets/api-admin-routes-l3mon.conf`, `deploy/nginx/nginx.conf` | the admin prefix; a hardening test |

## Task 1: the audit table and safe defaults (`l3mon_core`)

**Interfaces:** `Audit(id, at, actor_id, actor_name, action, target, detail)`; `audit.record(action, target, detail="", actor=None) -> Audit` (added to the session, not committed; the actor is the signed-in user, or the word `system`; `detail` is clipped to 500 characters); `audit.recent(limit=20) -> list[dict]` newest first. `Channel.release_state` and `Programme.release_state` default to `withheld`.

**Tests:** a line records who, what, target, detail and time; a deleted administrator keeps the name; an over-long detail is clipped, never raises; `recent` is newest first and limited; no model default can put a new channel or programme on air; the second migration builds `l3mon_audit` exactly as the model says, applies on top of the first, is safe to run twice and by two workers at once, and its downgrade drops only that table (real MariaDB).

## Task 2: what is on air (`l3mon_core`)

**Interfaces:** `release_active() -> bool` (any channel exists); `entry_on_air(state, release_at, t) -> bool`; `is_on_air(challenge_id, t=None)`; `on_air_ids(t=None) -> set[int]` (all programme challenges once the broadcast has ended; otherwise those whose channel and programme are on air; empty when release control is in use and nothing is on air); `visibility.visible_challenge_ids` and `is_visible` additionally require membership of `on_air_ids` when release is active.

**Tests:** a table of state, time and clock (before, at the second, after); both levels needed; a channel withheld hides all of its programmes whatever they say; ended reveals everything on the plan; a challenge in no channel is not on air while any channel exists and is when none does; a `scheduled` entry without a time is withheld; unknown words never count as on air; administrators see everything; prerequisites still apply; the answer matches CTFd's own challenge list (`/api/v1/challenges`) for a player, in a scenario with every state.

## Task 3: reconcile, announce, enforce (`l3mon_release`)

**Interfaces:** `desired_states(t=None) -> dict[int, str]` (`visible` or `hidden` per challenge, empty when release is not active); `reconcile(t=None) -> Result(shown, hidden)` (one conditional update per challenge, then one commit, the caches cleared, the tick moved, the next event refreshed, pull-back handlers called with the ids that went from visible to hidden, and "New on air" announced for those that went the other way when the phase is live or paused); `register_pull_back_handler(fn)`; the `before_flush` hook of decision 1.

**Tests:** states follow the plan (released, withheld, scheduled before and after its time); reconcile twice changes nothing and announces nothing the second time; one notification per call, one line per channel in channel order, counts only, no programme name (`CH 3 · Gadget Galaxy has 2 new programmes.`), title `New on air`, pushed on the events stream; none before the start, none after the end; a programme pulled back and released again is announced again; pull-back handlers get exactly the ids that were visible; a failing handler is logged and never stops the reconcile; setting `visible` by hand on a withheld programme ends `hidden` with an audit line; hiding by hand in CTFd turns the plan entry into `withhold` and the next reconcile keeps it hidden; a challenge created with no plan entry is hidden; deleting a challenge or a channel leaves nothing dangling.

## Task 4: the scheduler (`l3mon_release`)

**Interfaces:** `next_event(t) -> int | 0` (the earliest scheduled time after `t`, or the `end` second while the broadcast is live), cached under `l3mon:release:next` together with the `end` it was computed from; `maybe_apply()` (cheap: one cache read; applies a due reconcile; never raises); `install(app)` registers the request hook (not for static or theme assets).

**Tests (fake clock):** nothing happens before the second, the drop happens on the first request at or after it; a scheduled channel and a scheduled programme in different orders; the end reveals everything and the new `end` setting hides it again; a request after a restart (empty cache) computes it; a broken reconcile does not break the page; static requests skip it; **on a real MariaDB: eight workers racing one drop produce one notification and one flip**, and a worker whose update changed nothing announces nothing.

## Task 5: the admin API (`l3mon_release`)

**Interfaces:** `GET /api/v1/l3mon/admin/release` -> every channel with its programmes, each with the stored `state`, `at` (epoch) and `at_ist`, whether it is `on_air` for players now, counts, and the 20 newest audit lines. `PUT /api/v1/l3mon/admin/release` with `{"changes": [{"kind": "channel"|"programme", "id": n, "mode": "release"|"withhold"|"schedule", "at": epoch?}], "reason": "..."}` (up to 200 changes; one transaction; one reconcile). `PUT /api/v1/l3mon/admin/programmes` with `{"channels": [...], "programmes": [...]}` (idempotent upsert; a new entry starts `withheld`; refuses duplicate slugs, numbers and cells with a list of problems; changes nothing on any error).

**Tests:** a player and an anonymous visitor are refused as for any admin route; a missing or wrong CSRF token is refused; every validation (unknown id, bad mode, schedule without a time, a time in the past or more than 30 days away, a reason over 200 characters, a body that is not JSON, too many changes); idempotence (the same request twice leaves the same plan and one audit line each time); an unknown id changes nothing from the rest of the batch; one audit line per change with the reason; the sync creates, updates and leaves alone as asked, never changes a release state, refuses a challenge placed twice; the answer carries no player data; numbers in the answer match the plan.

## Task 6: close the doors (`l3mon_release`)

**Interfaces:** `guards.install(app)` and the switches `guards.ON = {lists, files, shares, scoreboard}`: replaces `Users`/`Teams` `get_solves`, `get_fails`, `get_awards` with versions that, in a web request from anybody but an administrator, drop entries for challenges the viewer may not see (a hint award is matched through the hint id in its name; an award in category `hints` that cannot be matched is dropped); a request hook for the file route that answers 404 for a file of a challenge that is not visible and on air, with or without a token; a request hook that answers 404 for `/share/*` and `POST /api/v1/shares`; a response hook that blanks the challenge id of solves the viewer may not see in `/api/v1/scoreboard/top/<n>`. All do nothing while release is not active. Each is one function the tests switch off.

**Tests:** for every row of the table above (five guards: the fifth, the score history, was found by the test of the scoreboard row), as a player who never saw the programme and as the team that solved it before the pull-back: the answer equals the answer for an id that never existed (status and body shape), and a **control** (the same request for an on-air programme) succeeds, so a test cannot pass by being refused for another reason; the flag box records no submission; a signed file link made while released stops working; the team and user pages and the `me` lists show nothing of it; the administrator still sees everything; **each test is run a second time with its guard switched off and must fail**.

## Task 7: the crew's page (`l3mon_release`)

**Interfaces:** `GET /admin/l3mon/release` (administrators only) renders `release.html`, which extends CTFd's admin base and loads `/plugins/l3mon_release/assets/release.js`; the menu entry "Release control"; the page reads and writes only through Task 5's API, shows each channel with its programmes and a state chip (released, withheld, scheduled for `<IST time>`), buttons Release now, Withhold, and Schedule (a date and time in India, sent as an epoch second), a preview of what players see now (counts), the newest audit lines, and a "Withhold everything" button behind a confirmation.

**Tests:** the page renders for an administrator and is refused to a player; it carries no inline script; every name from the database is escaped (a channel named with markup stays text); the script file is served; a browser check of the page against the running stack is part of Task 8.

## Task 8: the wiring, the whole suite and the record

nginx: `api-admin-routes-l3mon.conf` (`/api/v1/l3mon/admin` is administrator-only for every method), included beside the generated list; `tests/integration/test_hardening.py` gains the check from a non-organiser address; `python -m l3mon api-rules check` still passes. Rebuild the image. Run: the plugin suite, the real-MariaDB tests (migration, scheduler race), CTFd's own 676 tests with the plugins loaded, the stack rebuilt from nothing, the integration tests, the hygiene scan. Update the design (section 5: the verified door inventory; section 4: the audit table), `docs/api-contract.md` section 8 of the demo (private repo) with the doors that were found, `PROGRESS.md` and `docs/deploy/verification-log.md`. Then the independent Opus review, the fixes (a test each), and one commit on `pre-deployment`.

## What changed while building (2026-10-09)

The plan was followed task by task, test first, each security test shown failing with its guard off. These things were found by doing the work and are now part of the design:

1. **A fifth door.** The score history of `/api/v1/scoreboard/top/<n>` carries the id of every solved challenge (only awards have none), so a pulled-back programme's id and value leaked. The test for the scoreboard row failed against the first guards; `guards.ON["scoreboard"]` blanks the id. The step values stay: points are kept on a pull-back.
2. **The `me` lists.** CTFd calls its own-data lists with `admin=True`, meaning "ignore the freeze", not "is staff". The list guard therefore filters on the signed-in viewer being an administrator, and not at all outside a web request.
3. **CTFd's own helper ignores `admins_only`.** `register_plugin_assets_directory(..., admins_only=True)` takes the argument and never uses it, so the page's script and style are served by our own admin-only route (with a path-traversal test).
4. **A drop found by a player's request is the system's.** The end-to-end test showed the audit line naming the player whose request happened to notice the due drop. `audit.record(system=True)` and `reconcile(system=True)` (the scheduler's call) fix that, with a unit test.
5. **After the end.** The plan puts every programme on air again at the `end` second, but CTFd only lets players open challenges after the end when `view_after_ctf` is on. The event settings now set it (`tools/l3mon/config.py`).
6. **Earlier tests moved.** Six tests of 3.1 built "visible in CTFd, withheld in the plan" by creating a challenge after a channel existed, which the hand guard now (rightly) refuses; they create the challenge first and set the state with a bulk update, which is what a missed reconcile would leave. The model tests accept either database's error type (MariaDB reports a failed CHECK differently from SQLite).
7. **The event window cannot be changed through the admin API** (it is a preset), so the end-to-end test renders a window around the present into the generated settings, restarts CTFd, and restores both afterwards.
8. **The independent review** (one High, one Medium, eight Low, four questions) changed the work after the first green runs:
   - **High: a stale view of the plan.** Under MariaDB's default REPEATABLE READ a worker's reconcile could judge the plan from a view older than another worker's drop, skip the change, and leave CTFd showing a programme the plan says is withheld (or announce a drop the crew had just cancelled). Everything that changes the plan or acts on it now takes a row lock first (`reconcile.serialize()`: end the read transaction, then `SELECT ... FOR UPDATE` on the lowest channel), so it reads current data and never runs at once with another. A two-thread MariaDB test reproduces the bug and passes with the lock.
   - **Medium: a sixth door.** CTFd's unlock route takes `type` as any table name; a withheld id answered differently from a missing one and the size of our tables could be counted, the audit trail included. Any type but `hints` and `solutions` is now CTFd's own 404.
   - **A seventh: `next_id`** in the challenge JSON (blanked unless visible), and `view_self_submissions` pinned off in the event settings.
   - **Names that render as markup.** The channel name went raw into the "New on air" notification, which CTFd renders as markdown. The plan now refuses markup characters and `//` in channel names, storylines and sponsor names, and the announcement drops them again for a row that got there another way.
   - Unknown keys in a request are refused (a typo no longer passes silently); the scheduler's record lives 30 s instead of 300; the page builds its controls once (a refresh no longer empties the reason box), clears the reason after a change, and asks before "Release now"; the IST conversion is now checked in a real browser (`tests/browser`), not by reading the source; the unmatched hint-award rule has a test; three docs that the code contradicted were corrected.
   - Known and left: hand edits in CTFd's editor do not take the plan lock (a window of milliseconds); a pull-back that meets a dynamic challenge's solve of the same challenge can deadlock in InnoDB and one of the two requests fails (the crew repeats the call; a player resubmits); after the end CTFd answers attempts without recording them and hints are free; a hide in CTFd's editor after the end is undone at the next reconcile (the plan shows everything after the end; to keep something off air then, move the end).
9. **Deferred (unchanged):** a by-hand editor for the plan itself, the `l3mon` command-line sync, and removing a channel or programme from the plan (today: withhold it, or delete the challenge, which removes its programme).
