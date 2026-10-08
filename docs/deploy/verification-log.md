# Verification log

A dated record of every check that proves a piece of the platform works. A task in the [SP0 plan](../superpowers/plans/2026-10-06-sp0-foundation.md) is done only when its verification is written here with the command and the result.

## 2026-10-06

### Facts established before building (read from the official image)

| Fact | How it was checked | Result |
|------|--------------------|--------|
| The official CTFd 3.8.8 image is published on GHCR and Docker Hub with the same content | `docker manifest inspect` on both | Same index; pulled `ghcr.io/ctfd/ctfd:3.8.8`, digest `sha256:07cc9788d58b2a18ea4dc2103ff385c053ffc506f57d0c769a99387849aedd01` |
| It runs as user 1001, listens on 8000, starts `flask db upgrade` then gunicorn with gevent workers, and refuses more than one worker without a `SECRET_KEY` | `docker image inspect`, the entrypoint script | Confirmed |
| Settings come from `config.ini` or the environment; `PRESET_ADMIN_*` and `PRESET_CONFIGS` allow a fully automated setup, and a preset setting cannot be changed from the admin page | `/opt/CTFd/CTFd/config.ini` | Confirmed |
| Its pinned Python packages carry known advisories | `pip-audit -r requirements.txt` (pip-audit from PyPI, in a throwaway `python:3.11-slim-bookworm` container) | 74 advisories in 10 packages: click, cryptography, flask, idna, pillow, pydantic, python-dotenv, requests, urllib3, werkzeug |

### Task 1: hygiene scanner and CI skeleton

| Command | Result |
|---------|--------|
| `python -m pytest tools/tests/test_hygiene.py -q` | 11 passed (after first seeing the tests fail with the module missing) |
| `cd tools && python -m l3mon hygiene --root ..` | 0 findings in the public repository |

### Task 2: event configuration and secrets tool

| Command | Result |
|---------|--------|
| `python -m pytest tools/tests -q` | 38 passed, 1 skipped (the POSIX permission-bit test does not apply on Windows); written test-first |
| `python -m l3mon config validate config/event.example.toml` | valid; `config render` writes one-line `preset_configs.json` |
| `python -m l3mon secrets generate` | seven secret files, values never printed, second run refuses to overwrite without `--force` |
| The settings are accepted by a real CTFd 3.8.8 | verified in Task 7 |

### Tasks 3 and 5: the image, the plugin and the theme

| Command | Result |
|---------|--------|
| `docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .` | built from `ghcr.io/ctfd/ctfd@sha256:07cc9788...aedd01` |
| `tools/verify-image.sh l3mon/ctfd:dev` | 15 checks pass: runs as user 1001, health check present, `pip check` clean, plugin and theme in place, plugin files not writable by the running user, the seven upgraded packages at their pinned versions, no compiler in the image |
| `pip-audit` on the CTFd 3.8.8 pins | 74 advisories in 10 packages |
| The same after the isolated upgrades (`docker/ctfd/requirements.overrides.txt`) | 21 advisories left in 3 packages (flask 2.1.3, werkzeug 2.2.3, pydantic 1.6.2); see Task 4 |
| `tools/run-ctfd-tests.sh ghcr.io/ctfd/ctfd:3.8.8` (CTFd's own suite, baseline, unmodified image) | 676 passed in 6 min 40 s |
| The same with the isolated upgrades applied | 676 passed in 6 min 39 s: identical to the baseline |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests` | 7 passed: the health route, the route is absent when plugins are off, CTFd's own health check, the empty theme falls back to core, the bare address redirects, secure `__Host-` cookies switch on by environment and stay plain without it |

### Tasks 6 and 7: the compose stack and its integration tests

| Command | Result |
|---------|--------|
| `tools/compose.sh up -d --build --wait` | all four services healthy: CTFd (non-root 1001), MariaDB 10.11.19 (user 999), Redis 7.4.11 (user 999), nginx 1.30.5 unprivileged (user 101); all read-only root file systems, all capabilities dropped |
| `python -m pytest tests/integration -q` | 22 passed in 50 s: health routes without cookie, unknown Host gets no answer, security headers once each and HSTS only over HTTPS, session cookie flags, bare address goes to sign-in, theme and event name, forwarded addresses not trusted, preset admin token and password work, every preset setting is in force inside CTFd, only nginx publishes a port (loopback only), every container unprivileged and limited, database and cache only on the internal network, code not writable, no secret in logs or in `docker inspect`, data survives an application restart, sign-in rate limit |

### Task 8: backup and restore, with a drill

| Command | Result |
|---------|--------|
| `python -m pytest tests/integration/test_restore_drill.py -q --run-drill` | 3 passed. The drill creates a page and an uploaded file, backs up, **destroys the whole stack including its volumes**, starts an empty platform (the page and file are gone), restores, and checks the page, the file download and the administrator token. **Finished in 57 seconds** (budget 15 minutes). A backup with a damaged database file is refused with a checksum message and changes nothing; a restore into a database that already holds data is refused unless `--force` is given |
| Known gap | The backup is not encrypted by the script; encrypted off-host copies and a scheduled backup belong to SP9 |


### Task 4: dependency upgrades checked against CTFd's own tests

Full analysis, with every remaining advisory explained: [dependency-bumps.md](../security/dependency-bumps.md).

| Command | Result |
|---------|--------|
| `tools/run-ctfd-tests.sh ghcr.io/ctfd/ctfd:3.8.8 OVERRIDES` with Flask 2.3.3 and Werkzeug 3.0.6 | **636 failed**, 40 passed (`'CTFdFlask' object has no attribute 'session_cookie_name'`) |
| The same with Flask 2.2.5, Werkzeug 2.3.8 and pydantic 1.10 | 9 failed, 667 passed |
| The same with Flask 2.2.5 alone | 7 failed, 669 passed |
| The same with Werkzeug 2.3.8 alone | fails (Flask 2.1.3's test client cannot use it) |
| The isolated upgrades plus setuptools 84.0.0, pydantic 1.10.26 and pip 26.2.1 | 676 passed in 6 min 7 s |
| **Decision** | Flask 2.1.3 and Werkzeug 2.2.3 stay; every other fixable advisory is upgraded; pip is removed from the image |
| `docker build` of the final image, then `tools/verify-image.sh l3mon/ctfd:dev` | 23 checks pass, including: the 9 pinned packages, the base interpreter's setuptools, pip absent from both interpreters, the two Debian fixes (`libpcre2-8-0`, `perl-base`), no apt lists left behind |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev` (CTFd's own suite on **our final image**) | **676 passed** in 6 min 11 s: identical to the unmodified image |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests` | 7 passed (the plugin's own tests) |
| Trivy on the final image (`--scanners vuln`, all severities) | The only fixable HIGH or CRITICAL findings are Flask CVE-2023-30861 and Werkzeug CVE-2024-34069 (explained in the analysis). The two Debian packages that had 3 CRITICAL and 5 HIGH findings are clean |
| pip-audit on the final image's packages | only Flask (2 advisories) and Werkzeug (about 9 advisories) remain |
| `python -m pytest tests/integration -q` after recreating the stack on the final image | 25 passed, 3 skipped (the destructive drill). New: every proxied response carries `Vary: Cookie`; only `/admin` and `/api/v1/files` accept a body over 1 MB (a 2 MB body to `/login` is 413, 11 MB to the files API is 413, 2 MB to the files API reaches CTFd); `/console` and the debugger probe URL show no debugger |
| `python -m pytest tools/tests -q` and `python -m l3mon hygiene --root ..` | 43 passed, 1 skipped; 0 findings |

Changes made because of what was found: nginx now caps request bodies at 1 MB (10 MB only for the admin pages and the files API) and adds `Vary: Cookie` to every proxied response; the CTFd test runner restores pip inside its own throwaway container (the platform image has none).

### Task 9: the full CI pipeline, on a clean Linux machine

| Run | Result |
|-----|--------|
| First run ([37479796791](https://github.com/sivaahari/L3m0nCTF/actions/runs/37479796791)) | `hygiene`, `lint`, `image` and `third-party-images` passed; **`integration` failed**: MariaDB could not read its secret (`/run/secrets/DATABASE_PASSWORD: Permission denied`). On Linux, Compose mounts each secret file with the owner and mode it has on the host, and the services run as their own unprivileged users (CTFd 1001, MariaDB 999, Redis 999), so owner-only (0600) files are unreadable to them. Docker Desktop on Windows hides this. **A real defect that would have hit the first Linux deployment, found by CI** |
| Fix | `secrets generate` now makes the folder private (0700) and the files readable (0644); `config render` makes `preset_configs.json` readable whatever the umask. Tests added for both (they run on Linux in CI) |
| Second run ([37480189589](https://github.com/sivaahari/L3m0nCTF/actions/runs/37480189589)) | **All six jobs green**: `hygiene` 11 s, `lint` 15 s (actionlint, hadolint, shellcheck, compose files with generated settings), `image` 62 s (build, `verify-image.sh`, pip-audit with the reviewed list, Trivy against the reviewed list), `third-party-images` 36 s (MariaDB, Redis and nginx as pinned), `integration` 140 s (the stack started from a clean checkout, 25 integration tests, **and the restore drill on Linux**), `ctfd-suite` 509 s (CTFd's own 676 tests on our image) |

The local checks behind these jobs were run first on Windows with Docker Desktop: hadolint 2.12.0 (one warning, now explained in the Dockerfile), shellcheck 0.10.0 (warnings and errors clean; the info-level notes are intended single quotes), actionlint 1.7.7, `docker compose config -q`, pip-audit 2.9.0 with the reviewed list (0 findings, 10 ignored), Trivy 0.75.0 on the platform image and on the three pinned third-party images (rc 0 with the reviewed lists).

New and changed files: `.github/workflows/ci.yml`, `docker/ctfd/accepted-advisories.txt` and `deploy/compose/accepted-advisories.txt` (the reviewed lists, kept in step with [dependency-bumps.md](../security/dependency-bumps.md) by `tools/tests/test_advisories.py`), `docs/deploy/local.md`.

### Hardening round after the independent audit

An independent Opus audit of the platform foundation (report in the private repo, `security/audit-sp0-2026-10-06.md`) found 2 High, 5 Medium and 5 Low issues and listed what it checked and found sound. What was fixed, and how each fix was proved:

| Finding | Fix | Proof |
|---------|-----|-------|
| **H1** the admin allow-list covered only `/admin`; the admin REST API and the never-expiring preset token worked from any address | nginx now treats any request with an `Authorization` header, any route CTFd guards with `@admins_only` (generated from CTFd's own source: `python -m l3mon api-rules generate`), and any unlisted write to `/api/v1` (fails closed) as the administrator surface, answered only for the organisers' addresses. `PRESET_ADMIN_TOKEN` moved to `compose.dev.yml` (dev and CI only) | `tests/integration/test_hardening.py`: nginx restarted so nobody is an organiser; every one of the 80+ generated admin routes, any token, any unlisted write and the admin pages answer nginx's 403; a player's own writes still reach CTFd; public pages and reads still work. Mutation check: switching off the refusal made 4 of those tests fail. **The first run of the new tests found a bug in my own rule generator** (an admin route `/teams/<id>/members` also matched a player's `/teams/me/members`); fixed by excluding the fixed word `me`, with unit tests in `tools/tests/test_api_rules.py` |
| **H2** (the dependency page was wrong) CTFd's CSRF check parses the form of every non-JSON POST before checking who asks, so Werkzeug's multipart parser was reachable by anyone with up to 10 MB | nginx refuses every `multipart/form-data` request from non-organiser addresses (415, before the body is read), caps every other body at 64 KB (10 MB only for `/admin` and the files API), and the page is corrected | the "outside" tests above (multipart to `/login`, `/register`, flag attempt, settings; a 5 MB anonymous upload to the files API); mutation check: removing the refusal failed the multipart test |
| **M1** logs not rotated; gunicorn's access log recorded query strings; reset and confirmation tokens are in the path | `max-size 50m, max-file 5` on every service; gunicorn access log off; nginx masks `/reset_password/..` and `/confirm/..`; MariaDB slow log off by default | tests: all four containers have rotation; a probe with a query string and two token paths shows `[token]` and never the secret parts in any log |
| **M2** per-address limits would throttle a campus or a mobile carrier's shared address | general 100 requests per second (burst 200), sign-in 120 per minute (burst 40), flags 1200 per minute, 300 connections and 150 event streams per address; `unlimited-nets.conf` skips the limits for listed addresses | test: 150 page loads in a row from one address are not limited; the flood test now requires nginx's own 429 page (CTFd's limiter also answers 429, which hid this before) |
| **M3** the admin list was never tested from a denied address; the dev list would be dangerous in production | admin list is now a file (`ADMIN_NETS_FILE`) with a production example that has no private range | the "outside" tests; a test refuses a production example listing a private, loopback or link-local range |
| **M4** hygiene scanner gaps (git history, binaries, encodings, token shapes, skipped folders; story words never checked in CI) | scans the files git tracks or would add, binaries too, flags in any case, URL, HTML, hex, base64 and UTF-16 form, more token shapes (CTFd, Google, GitHub fine-grained), archives, captures and executables by name, `--history` for every commit; CI scans the history and runs the story-word check when the secret `HYGIENE_DENY_WORDS` is set (and warns loudly when it is not) | 20 new unit tests, including a history scan that finds a secret deleted from the tree; the repository and its whole history scan clean |
| **M5, L4** TLS and origin authentication, Cloudflare cache rules, the metadata server, egress, service account, secure cookies, off-host backups | written as testable requirements in [production-checklist.md](production-checklist.md) (these belong to the deployment, SP9) | each line names its proof |
| **L1** backups hold live tokens and were world-readable; restore ran against a live site; "without --force" could never succeed | `umask 077`; restore stops nginx and CTFd, replaces the database and the uploads, clears the cache and starts them again; `--force` is always required; the dump's trailer is checked | the restore drill passes with a token that lives only in the database (the old check used the environment token, which proves nothing); a backup is private to its owner; a restore without `--force` changes nothing |
| **L2** tests that could pass while the property was broken | forwarded address now proved through the address CTFd records; the sign-in flood needs nginx's own 429; code ownership checked; the whole `docker inspect` JSON searched for secrets | the strengthened tests pass |
| **L3** CI pinning and vacuous passes | actions pinned by commit, tool images by digest, no Docker socket for Trivy, pip-audit finds the packages itself and fails below 50 audited, manual `workflow_dispatch` added (scheduled runs only start from `main`) | actionlint clean; the pip-audit step run locally audits 67 packages |
| **L5, Info** passwords in process arguments; MariaDB root reachable from the network; `.secrets` folder edge case | `REDISCLI_AUTH` and `MYSQL_PWD` instead of arguments; root only from inside the MariaDB container (`MARIADB_ROOT_HOST=localhost`); the secrets tool fails if it cannot make its folder private | tests: root cannot sign in from the CTFd container; the Redis password is in no process argument list |

Local results after the round: tool tests 63 passed (2 skipped on Windows); integration tests 40 passed (4 skipped: the destructive drill, run separately and passed: 4 passed in 85 s); hygiene 0 findings in the files and in the history; hadolint, shellcheck (warnings and errors) and actionlint clean.

### The CTFtime feeds (plugin `l3mon_ctftime`)

| Command | Result |
|---------|--------|
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests` (now mounts every plugin's tests) | 18 passed (7 for `l3mon_core`, 11 for the feeds) inside a real CTFd: empty event, plugin off gives 404, ranking and tie-break exactly as CTFd's own (teams and users mode), exactly the keys `pos`, `team`, `score`, hidden, banned and non-positive accounts never appear and positions have no gaps, any characters in names round-trip as plain ASCII, no email or member name in the body, the live feed is frozen with the scoreboard and the final one is not, the final feed needs **both** the end of the event and the organisers' go-ahead, an account banned after the freeze is left out of the final file, identical answers with or without a cookie |
| Mutation check: break the plugin five ways (final feed frozen, banned kept, non-positive kept, end-of-event check skipped, go-ahead check skipped) | each break made exactly the matching test fail |
| `python -m pytest tests/integration -q` | 41 passed, 4 skipped (drill). New: through nginx the live feed is JSON of the documented shape with **no cookie, no `Vary: Cookie`**, `public, max-age=15`; the final feed is 404 with `no-store` until published; both feeds work from addresses that are not organisers' |
| `tools/verify-image.sh` | the feed plugin is in the image |

## 2026-10-07

### Owner review of the platform pages: release control, the programme grid, no easter eggs, flag format `L3m0n{...}`

| Check | Result |
|-------|--------|
| Platform pages, unit and HTTP tests (`node --test --test-concurrency=1 "tests/*.test.mjs"`, private repo `platform-ui`) | 254 passed. New: 19 release-control tests (a test for every door a withheld programme could leak through, scheduled drops to the second, the tick, the news line, the desk page) and the programme grid and fragment tests |
| Platform pages, browser suite in headless Chrome (`node tools/smoke.mjs`) | 4,471 checks. First full run 4,470 of 4,470 before the audit fixes; the run after them had 3 timing-sensitive checks fail once under load (login target size at 320 px, a banner follow-up, a layout-shift reading) and every one passed on a quiet rerun (198 and 226 checks). New: the Guide grid at every viewport, live swap of the grid with the keyboard focus kept, release control on the board, the Guide and the pages |
| Landing page: `node --test` and `node tools/smoke.mjs` | 28 unit tests; 458 browser checks, including a check that no easter egg is left |
| Author kit: `python -m pytest l3mon` | 67 passed (new: the kit and the shared helper agree on `L3m0n{...}` and the old opening is refused) |
| Nine sample challenges, `python -m l3mon check-all` | all pass with `L3m0n{...}`: seven on this machine, and Secret_Sauce, Relic_Lock and Relay_Bench's compiler tests in Linux containers (scapy's Windows driver hangs on this machine, and gcc is missing). Secret_Sauce needed its XOR key changed from 7 to 5 bytes for the 6-byte opening; the lint caught a leftover mention in its README |
| Public repo: `python -m pytest tools/tests`, `python -m tools.l3mon.cli hygiene` and with `--history` | 65 passed, 2 skipped; 0 findings in the files and in the history, with both openings now looked for in every encoding |
| Independent Opus audit of release control and the Guide grid | no leak of a withheld programme found on any route. Found and fixed: the no-script forms told a signed-out visitor which slugs were released; the desk could release early (Enter key, a time in the past); keyboard focus was lost when the grid refreshed; counts disagreed after a pull-back; the on-air meter counted solves of hidden programmes; the Guide showed totals before the start; instances of a pulled-back programme were switched off lazily; the tick moved without a visible change. Left as owner decisions or SP3 checks (written in `private/platform-ui/docs/api-contract.md`, section 8): points kept when a solved challenge is pulled back, and CTFd's own team pages and solve lists |
| Pentest bundle rebuilt (`python tools/pentest_bundle.py --landing-dist private/landing/dist --verify`) | the new landing page without eggs, demo flags in the new format: 13 of 13 end-to-end checks pass. The 2026-10-06 bundle was deleted from this machine |

### Second owner review: TRP, the header, Scoreboard and Notifications, one Filter button, the Guide, the mascot

| Check | Result |
|-------|--------|
| Platform pages, unit and HTTP tests (`node --test --test-concurrency=1 "tests/*.test.mjs"`, private repo `platform-ui`) | 279 passed. New: the header (banner, order, no Help or CLEAN), the notifications page (escaping, order, empty state), the scoreboard (the same list as the CTFtime feed, ETag and 304, the freeze, before the start, the 100-row limit, gates), TRP in every reply and page, the one Filter button, per-member contributions (release control applied, the member who sent the flag), the tick's own count while frozen |
| Platform pages, browser suite in headless Chrome (`node tools/smoke.mjs`) | 4,652 checks pass. The first full run on the new pages was 4,613 of 4,652: 28 were the bell asking for the news of somebody still setting up (fixed: the bell is for players), 3 were links 43 px high at the smaller base font (fixed), and the rest were test wording (CLEAN, Help, points) and one layout-shift reading that did not repeat on a quiet rerun |
| Landing page: `node --test` and `node tools/smoke.mjs` | 28 unit tests; 457 browser checks. One idle-CPU reading (3.7% against the 3% limit) failed once while the machine was busy and passed on the rerun (185 of 185). The mascot's headroom makes the TV a little narrower on tall screens; one hero check changed with it (at 1920x1280 the TV is now height-limited, so the side wings show, and the width share asked for there is 70%, not 75%) |
| Independent Opus audit of the round | 13 findings (3 medium, 10 low), all fixed or documented: the open Filter panel could cover the tile that took keyboard focus; the scoreboard replayed its entry animation at every refresh; the bell stopped counting after the demo's "Reset the world"; a hidden studio saw 0 TRP and "Ranks frozen"; the studio's own place could claim a rank the list does not have; a teammate's solve did not refresh an open page while the scoreboard was frozen; no-script hint form said "points"; plus stale documents, one empty check and missing size budgets. No leak of a withheld programme, a post-freeze solve, another studio's members or an unescaped name was found |
| Public repo: `python -m pytest tools/tests`, hygiene in the files and in the history | 65 passed, 2 skipped; 0 findings |
| Pentest bundle rebuilt (`python tools/pentest_bundle.py --landing-dist private/landing/dist --verify`) | 13 of 13 end-to-end checks pass with the new landing page; the earlier bundle of the same day was replaced |

### SP3 part 3.0: the demo shows the superiors' answers (values that fall, bonus, void and restore, private notes, the Sponsored Break)

| Check | Result |
|-------|--------|
| Platform pages, unit and HTTP tests (private repo `platform-ui`) | 305 passed. New: the curve against CTFd's formula, values frozen while the scoreboard is frozen, the tie rule with hint purchases and bonuses, bonus and void and restore (totals, one solve for each studio, outcomes), private notes (list, newest id, page, Guide, tick), the desk page and its JSON call, the Sponsored Break everywhere, input limits, the no-script pages. Privacy checks were proved able to fail by switching the filter off |
| Platform pages, browser suite in headless Chrome (`node tools/smoke.mjs`) | 5,090 checks pass: every older page at every size, and new suites for the eight channel cards (one row from 1280 px, two rows of four from 768 to 1279 px, a Sponsored tag that overlaps nothing, checked at 1100, 1200, 1279 and 1280 px too), the value line, the Guide following a bonus, a void, a restore and a sixth message by itself, long unbroken crew text, and the scoring desk. A first run showed 2 failures and a stall while the machine was busy; both suites passed alone and in the final full run |
| Independent Opus review | 12 findings: 2 high, 4 medium, 5 low, 1 note. All fixed with a test that failed first, or documented. High: restore could leave two solves for one studio (now one for each studio, with an outcome on every record); a crew action moved the shared news counter, which showed other studios how many private lines it wrote and, while frozen, how many studios had solved a programme (ids and the news version are now per viewer). Medium: void and restore disagreed while frozen; the Sponsored tag touched the channel number between 1100 and 1250 px; long unbroken text widened the notifications page and the bell; the Guide missed a sixth message with the same title. Low: sponsor missing from the no-script pages, the restore record, input checks (booleans, hex, control and direction characters), stale statements in the documents. Documented, not changed: the solve index is rebuilt only when solves change, so the real plugin must recalculate on a ban (the demo has no such action) |
| Landing page unit tests | 28 passed |
| Public repo hygiene (`python -m l3mon hygiene`) | 0 findings |
| Landing page on Cloudflare (checked 2026-10-07) | 16 files identical to the build, security headers, 404 with a 404 status, caching, no third-party requests, countdown correct, `?preview` ignored on a real address |

### Owner review of 2026-10-08: header, channel storylines, Search & filter, the lemon's peek, the sponsors area, the rules text

| Check | Result |
|-------|--------|
| Platform pages, unit and HTTP tests (private repo `platform-ui`) | 308 passed. New or changed: the header has no clock, no LIVE card and no studio chip, and the connection pill starts hidden and empty; every channel has a storyline that names no programme and no track, and it is sent only once the channel has something on air; the Search & filter button, the hint and the count on each track chip; the match meter (one match still lights one block) |
| Platform pages, browser suite in headless Chrome | 5,097 checks pass. New: the storyline follows the channel, each track chip shows the true count for the channel, the meter follows the search, the offline notice is spoken once however many polls fail and the return is spoken once, the score is read from the server now that the chip is gone, a right-to-left studio name on the Guide |
| Landing page unit tests | 41 passed. New: the sponsors in the config (every refusal), the cards (every word escaped, every link out safe), the build copies the logos and stops for a missing, oversize or unsafe one, a logo must really be the image its name says, an SVG logo passes only as a plain drawing (26 hostile examples refused, two real ones accepted), the generated `_headers`, and the text of the home and Rules pages (open to all, teams of 1 to 4, the beginner category, easy to extreme, bonus TRP for a bug report, "we are listing it on CTFtime", no AI-assistant line) |
| Landing page, browser suite | 461 checks pass, including the lemon: behind the set at rest, in front of it on hover, back behind when the pointer leaves, in front on a tap |
| Independent Opus review | 13 findings: 0 high, 3 medium, 9 low, 1 question. All fixed with a test, or answered. Medium: the SVG logo check was a list of bad words that many spellings got past (now an allowlist, plus a second policy and a sandbox for `/sponsors/*`); a channel's storyline could hint at a programme the crew still holds back (now free of programme and track words and sent only once the channel is on air; a test caught my own draft line matching a programme name); the offline notice was spoken again at every poll (now spoken once per state change, and the return is spoken) |
| Public repo hygiene (`python -m l3mon hygiene`) | 0 findings |
| `l3m0nctf.xyz` on Cloudflare (checked 2026-10-08, before this round's build was uploaded) | Answers 200 over HTTPS with the security headers, a real 404, and `/rules/`, `/robots.txt`, `/sitemap.xml`, the calendar file and the share card. It still serves the earlier build. The old `workers.dev` address now answers 404. `http://` is reset on the campus network, so the redirect to HTTPS could not be confirmed from here (check it from a phone). `www.l3m0nctf.xyz` has no DNS record. The web manifest was served as `application/octet-stream`; the next build's `_headers` gives it `application/manifest+json` |
