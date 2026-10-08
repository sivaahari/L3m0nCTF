# SP3 part 3.1: the foundation inside CTFd (implementation plan)

> **For agentic workers:** work task by task. Every test is written and **shown failing** before the code that makes it pass. All Python runs inside the platform image (`l3mon/ctfd:dev`), against CTFd's own test helpers, through `tools/run-ctfd-tests.sh`. No CTFd source is edited.

**Goal:** the shared parts every later SP3 part stands on: the clock, who counts, what players may see, the tick counter, the new database tables, the version guard and the two settings. No endpoints and no player-visible change.

**Architecture:** everything lives in the existing `l3mon_core` plugin as small modules, each with one job and one test file: `versions.py` (the guard), `settings.py`, `clock.py`, `counting.py`, `visibility.py`, `tick.py`, `models.py` and `migrations/`. The later plugins (`l3mon_release`, `l3mon_scoring`, `l3mon_board`) import these and never repeat them. The design is [the SP3 design](../specs/2026-10-07-sp3-plugin-layer-design.md) (sections 3 to 5 and 7); the demo's rules are the reference (`private/platform-ui/server/solves.mjs` `isCounted`, `world.phase`).

**Tech stack:** Python 3.11 in the CTFd 3.8.8 image, Flask-SQLAlchemy 2.x (CTFd's `db`), CTFd's plugin migrations (`CTFd.plugins.migrations.upgrade`), Flask-Caching (Redis in production), pytest, freezegun (already in the image through CTFd's helpers), MariaDB 10.11 and Redis 7 for the two tests that need the real thing.

## Global constraints

- CTFd is pinned at **3.8.8** (image digest in `docker/ctfd/Dockerfile`). The plugins refuse to load on any other version.
- Times are **naive UTC** in the database, like CTFd's. The event starts at **09:00 IST on 28 November 2026, which is 03:30 UTC**; IST is UTC+5:30 with no daylight saving.
- CTFd decides "started" with `now > start` and "live" with `start < now < end`; the clock agrees with it (so a flag is accepted exactly when CTFd would accept it).
- A counted account is the one CTFd scores (the team in team mode, the user in user mode) and is **not banned, not hidden**, exactly CTFd's own filter (the tests compare our numbers with CTFd's decay input). One predicate serves solves, dynamic values, standings and the CTFtime feed. Administrators are kept out by CTFd itself (hidden at setup, no team).
- A player may see a programme only when it is **visible** in CTFd's own state and every prerequisite CTFd knows of is solved (the same set as CTFd's own challenge list, tested against `/api/v1/challenges`); administrators see everything. The release channels and programmes (3.2) tighten this in this one place.
- No raw SQL built from input, no new player write endpoints, nothing rendered as markup.
- Plugin files stay root-owned and read-only in the image; nothing writes to them at run time.

## File structure

| File | Responsibility |
|------|----------------|
| `plugins/l3mon_core/versions.py` | `PINNED_CTFD`, `check_ctfd_version()` |
| `plugins/l3mon_core/settings.py` | `show_coming_count()` (default on). There is no freeze switch: the freeze is CTFd's own `freeze` time |
| `plugins/l3mon_core/clock.py` | `IST`, `to_ist`, `ist_text`, `utc_text`, `ist_to_epoch`, `Phase`, `phase_at`, `window`, `current_phase` |
| `plugins/l3mon_core/counting.py` | `is_counted_team`, `is_counted_user`, `counted_solve_count`, `counted_solve_counts` |
| `plugins/l3mon_core/visibility.py` | `is_visible`, `visible_challenge_ids` |
| `plugins/l3mon_core/tick.py` | `Tick` (atomic on Redis), `tick`, `signature()` (the number players poll: counter plus phase plus later plugins' parts), `add_signature_part`, `install(app)` (bump once per committed change that players can see, bulk deletes included, at the end of the request inside a web request) |
| `plugins/l3mon_core/models.py` | `Channel`, `Programme`, `Void`, `Bonus`, `Note` (the five tables of design section 4) |
| `plugins/l3mon_core/migrations/` | one revision that creates the five tables (idempotent) and drops them on downgrade |
| `plugins/l3mon_core/__init__.py` | `load(app)`: guard, migrations, models, tick listeners, then the existing blueprint |
| `plugins/l3mon_core/tests/test_*.py` | one file per module, plus `test_migration_mariadb.py` |
| `tools/run-ctfd-tests.sh` | new `L3MON_MOUNT_PLUGINS=1`: test the working-tree plugins without rebuilding the image |
| `tools/run-migration-test.sh` | a throwaway MariaDB and Redis, then the two real-database tests |

## Task 1: the version guard

**Interfaces:** `PINNED_CTFD = "3.8.8"`; `check_ctfd_version(found=None)` raises `RuntimeError` naming both versions when they differ.

- [ ] Test (`test_versions.py`): the shipped image passes; `monkeypatch.setattr(CTFd, "__version__", "3.9.0")` then `create_ctfd(enable_plugins=True)` raises `RuntimeError` mentioning `3.8.8` and `3.9.0`; with plugins off the version is not checked.
- [ ] Run it, see it fail; write `versions.py` and call it first in `load(app)`; run it green.

## Task 2: the setting

**Interfaces:** `show_coming_count() -> bool` (key `l3mon_show_coming_count`, default True). Values `1 true yes on` (any case) mean on; any other value that is set means off; unset or empty gives the default. **No freeze switch** (changed after the review): CTFd freezes its own standings whenever `freeze` is set, so the freeze is that one time.

- [ ] Test (`test_settings.py`): the default; each accepted spelling; garbage is off; an empty value is unset; a change shows at once (CTFd's `set_config` clears its cache); there is no second freeze flag.
- [ ] See it fail; write `settings.py`; green.

## Task 3: the clock

**Interfaces:** `to_ist(t)`; `phase_at(t, start, end, paused=False, freeze=None) -> Phase(state, frozen)` with `state` in `before | live | paused | ended`; `before` while `t <= start`, `ended` from `t >= end`, otherwise `paused` or `live`; `frozen` is true from the freeze time on; a missing start, end or freeze is open. `current_phase(t=None)` reads CTFd's `start`, `end`, `paused` and `freeze`, the way CTFd reads them (a negative end is long past). `ist_to_epoch(2026, 11, 28, 9, 0)` is `2026-11-28T03:30:00Z`. `ist_text` gives `28 Nov 2026, 09:00 IST`, `utc_text` gives `28 Nov 2026, 03:30 UTC`.

- [ ] Test (`test_clock.py`), pure and in CTFd with freezegun: 09:00 IST equals 03:30 UTC and midnight rolls the date the right way; at **03:29:59, 03:30:00, 03:30:01** UTC the state is before, before, live (CTFd's `ctf_started()` and `ctftime()` agree at each instant, asserted in the same test); live in the middle; one second before the end live, at the end ended (CTFd's `ctf_ended()` agrees one second after); paused only while the window is open; frozen from the freeze second (CTFd's own freeze time, and the same second CTFd's standings stop counting); no start and no end is live; garbage config values do not raise.
- [ ] See it fail; write `clock.py`; green.

## Task 4: who counts

**Interfaces:** `is_counted_team(team)`; `is_counted_user(user)` (also false for administrators); `counted_solve_count(challenge_id)`; `counted_solve_counts(ids)` returns `{id: n}` in one grouped query. Team mode counts solves of counted teams, user mode solves of counted users.

- [ ] Test (`test_counting.py`): a banned team, a hidden team, a banned member's solve (user mode), an administrator's solve and a team with all members counted; the count equals CTFd's own dynamic value input (`DynamicChallenge` calculates from the same rows); the batch answer equals the single answers and uses one query (counted with SQLAlchemy's event hook); unknown ids give 0.
- [ ] See it fail; write `counting.py`; green.

## Task 5: what players may see

**Interfaces:** `is_visible(challenge, admin=False, solved_ids=())`: true for `state == "visible"` with every prerequisite that names an existing challenge in `solved_ids`, and for any state when `admin`; `visible_challenge_ids(admin=False, solved_ids=())`: one query. An anonymized placeholder is not visible.

- [ ] Test (`test_visibility.py`): visible, hidden, locked and unknown states; prerequisites (one of two, an anonymized placeholder, a prerequisite that does not exist, one that is hidden); **the same ids as CTFd's own `/api/v1/challenges` for a player before and after solves**; admin sees all; deleting or hiding changes the answer at once; a missing challenge is false; one query.
- [ ] See it fail; write `visibility.py`; green.

## Task 6: the tick counter

**Interfaces:** `Tick(backend_factory, key="l3mon:ver")`; `.value()` creates the key at a random start (with the same INCRBY that later bumps it; cachelib's `add` stores a pickle Redis cannot increment, found against a real Redis) and returns it; `.bump()` adds one (`inc`, atomic on Redis) and returns the new value, repairing a key lost in between or one the cache cannot increment; `tick = Tick(lambda: cache.cache)`; `signature(t=None)` joins the counter, the phase and frozen state and the parts added by `add_signature_part`; `install(app)` listens on the session class: `after_flush` and the bulk update and delete events note a change to a watched model (scores, solves, awards, unlocks, challenges and their hints, files, flags and tags, teams, users, brackets, notifications, settings and our own five tables); `after_commit` bumps **once** (at the end of the request inside a web request, so CTFd's own caches are cleared first) and forgets; only a whole-transaction rollback forgets, a rolled-back savepoint does not. A `Fails` row, a `Tracking` row and a read never bump.

- [ ] Test (`test_tick.py`): `value()` is stable until a bump; `bump()` returns `value() + 1`; two commits bump twice, two objects in one commit bump once, a rollback bumps nothing, a rolled-back savepoint keeps the earlier change, a read bumps nothing, a wrong flag (`Fails`) bumps nothing, a setting, a notification and our own tables bump; **bulk deletes and updates, and an admin's API delete of a challenge and of a user, bump**; inside a request the bump comes after CTFd cleared its config cache; **the signature changes with the phase when nothing was committed**; a lost or poisoned key repairs itself; on a real Redis through Flask-Caching's own class and prefix (`L3MON_TEST_REDIS_URL`, skipped without it): 50 threads add exactly 50, the key never expires, a flush gives a fresh random start, 20 workers meeting a missing key all get a big number, a pickled key is repaired.
- [ ] See it fail; write `tick.py`; green; call `install(app)` from `load`.

## Task 7: the five tables and their migration

**Interfaces:** the columns of the design's section 4: `l3mon_channel` (slug, name, accent, picture_key, position, kind `standard|sponsored`, sponsor_name, sponsor_logo, release_state `released|withheld|scheduled`, release_at), `l3mon_programme` (challenge_id unique and cascading, channel_id, cell, number unique, slug unique, release_state, release_at), `l3mon_void` (challenge_id, team_id, user_id, submission_id, solved_at, voided_at, voided_by, reason, outcome `open|restored|skipped|superseded`, restored_at, restored_by), `l3mon_bonus` (award_id, team_id, user_id, scope `team|member`, message, given_by, given_at), `l3mon_note` (team_id, title, text, created_at).

- [ ] Test on SQLite (`test_models.py`): each table takes a row and refuses a duplicate slug, number or `(channel, cell)` and a word no column may hold; defaults (a programme is released, a channel is `standard`); the five tables exist after `create_ctfd(enable_plugins=True)`. (What a deleted challenge, administrator or team takes with it is proved on MariaDB only: SQLite does not enforce foreign keys.)
- [ ] Test on MariaDB (`test_migration_mariadb.py`, skipped without `TESTING_DATABASE_URL` pointing at MariaDB): the migration creates exactly the columns (order, types, lengths, nullability), foreign keys with their ON DELETE, unique keys and CHECK constraints the models describe; the database refuses every bad row (all five CHECKs); the cascade rules; running it twice changes nothing; **two workers running it at the same moment both succeed (five rounds)**; the downgrade drops all five and leaves CTFd's tables alone, and rolling back is manual (downgrade, then forget the recorded revision, and the next start builds them again).
- [ ] See both fail; write `models.py` and the migration; green on SQLite and on MariaDB.

## Task 8: the tools, the wiring, the whole suite and the record

- [ ] `tools/run-ctfd-tests.sh`: with `L3MON_MOUNT_PLUGINS=1`, mount `plugins/l3mon_*` read-only over the image's copies. `tools/run-migration-test.sh`: start `mariadb:10.11` and `redis:7-alpine` on a throwaway network, wait for both, run the two real-database tests in the platform image, remove everything even on failure.
- [ ] Rebuild the image (`docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .`), run **CTFd's own suite** (`tools/run-ctfd-tests.sh l3mon/ctfd:dev`, 676 tests) and the plugin tests, then the stack's integration tests.
- [ ] `python -m l3mon hygiene` finds nothing; `PROGRESS.md` and `docs/deploy/verification-log.md` record the results; commit and push.

**Done when:** every new test failed first and passes now; the real-MariaDB migration and the real-Redis counter tests pass; CTFd's own 676 tests, our existing plugin tests and the stack's integration tests still pass with the plugins loaded; nothing a player can see has changed.
