# Running the platform on your own computer

This starts the same stack that production will run (CTFd, MariaDB, Redis and nginx, in containers), with development settings: it is reachable only from your own computer, at `http://localhost:8080`.

## You need

- Docker with Compose v2 (`docker compose version` works) and about 4 GB of free memory.
- Python 3.11 or newer (only for the small settings tool).
- Git. **On Windows, run every command in Git Bash**, not in PowerShell or WSL.

## From a fresh clone to a working platform

```bash
git clone https://github.com/sivaahari/L3m0nCTF.git && cd L3m0nCTF && git checkout pre-deployment
(cd tools && python -m l3mon secrets generate --dir ../.secrets \
  && python -m l3mon config render ../config/event.example.toml --out ../deploy/compose/generated)
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
tools/compose.sh up -d --wait
```

1. Clone and switch to the `pre-deployment` branch.
2. Generate **new random secrets** (into `.secrets/`, never committed) and the settings CTFd starts with (into `deploy/compose/generated/`). Nothing is printed. Run it once; it refuses to overwrite existing secrets.
3. Build the platform image (the official CTFd 3.8.8, upgraded libraries, our plugin and theme). About one to two minutes the first time.
4. Start everything and wait until every container reports healthy (the first start also creates the database, which takes about a minute).

Then open <http://localhost:8080/>. It sends you to the sign-in page.

**Signing in as the organiser:** user `organiser`; the password is in `.secrets/PRESET_ADMIN_PASSWORD` (open that file; do not paste it into chat or an issue). The admin pages are at `/admin`. The development stack also has a fixed administrator API token (`.secrets/PRESET_ADMIN_TOKEN`, for scripts and tests; production does not have one). Both the pages and the whole administrator API are open only to *your own computer and private networks*: nginx refuses them from any other address (see [dependency-bumps.md](../security/dependency-bumps.md#who-may-use-the-administrator-api-found-by-the-independent-audit)). Email is not set up in the local stack, so a self-registered player cannot verify an address: use the organiser account, or create players from the admin pages.

## Day-to-day

| I want to | Command |
|-----------|---------|
| See what is running | `tools/compose.sh ps` |
| Follow the logs | `tools/compose.sh logs -f ctfd` (or `nginx`, `db`, `cache`) |
| Stop, keeping the data | `tools/compose.sh stop` (and `tools/compose.sh start` later) |
| Restart one service | `tools/compose.sh restart ctfd` |
| **Wipe everything** (data included) and start clean | `tools/compose.sh down -v`, then `tools/compose.sh up -d --wait` |
| Use another port | `DEV_PORT=9090 tools/compose.sh up -d --wait` |
| Run a command inside the platform with its real settings | `tools/compose.sh exec ctfd l3mon-run /opt/venv/bin/python -c "print('hi')"` |
| Back up (database and uploads) | `tools/backup.sh` (writes `backups/<timestamp>/`, readable by you only; git ignores it) |
| Restore a backup (it **replaces** the database and the uploads; nginx and CTFd are stopped meanwhile and started again) | `tools/restore.sh backups/<timestamp> --force` (`--force` is always required) |

Backups are not encrypted by the script, and they hold players' emails **and their API tokens in clear text**: treat a backup like a password file, keep it on your own disk and never commit it.

If you change anything under `docker/`, `plugins/` or `ui/`, rebuild the image (command 3) and recreate the platform: `tools/compose.sh up -d --wait --force-recreate`.

## Release control: what players can see, and when

The crew decides which channels and programmes (challenges) players can see, and at which minute. Nothing is on air until the crew says so: a channel or programme that has just been loaded starts **withheld**, and a challenge that is on no channel stays hidden for players as soon as any channel exists.

**The page.** Sign in as the organiser and open <http://localhost:8080/admin/l3mon/release> (it is also on the admin menu as "Release control"). Each channel and each programme has three buttons: *Release now* (it asks first: players see the programme at once), *Withhold* and *Schedule* (type a date and time in **India time**; the page converts it). *Withhold every channel* is the panic button. The table at the bottom is the audit trail: who changed what, when, and the reason typed in the box at the top.

**Loading the plan.** Until the author kit's sync exists, a plan is loaded with one call, which can be repeated safely (it creates what is missing and updates words; it never puts anything on air, because a new entry starts withheld. It can take something off air: a challenge on no channel is hidden once any channel exists, and moving a programme to a channel that is off air pulls it back). Names, storylines and sponsor names are plain words: they may not contain `< > [ ] { } * _ \` \\ # | ~` or `//`, because they are shown in a notification and on pages. Save this as `plan.json`, with the real challenge ids (the number in the challenge's address in the admin area):

```json
{
  "channels": [
    {"slug": "street", "name": "Mighty Street", "position": 1, "synopsis": "A loud street."},
    {"slug": "break", "name": "Sponsored Break", "position": 7, "kind": "sponsored", "sponsor_name": "Acme"}
  ],
  "programmes": [
    {"challenge_id": 1, "channel": "street", "cell": 0, "number": 101, "slug": "wrestler-padding"},
    {"challenge_name": "Midnight Mango", "channel": "street", "cell": 1, "number": 102, "slug": "midnight-mango"}
  ]
}
```

```bash
curl -s -X PUT http://localhost:8080/api/v1/l3mon/admin/programmes \
  -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN)" -H "Content-Type: application/json" -d @plan.json
```

The answer lists what was created, updated and left alone, or names every problem by its field and changes nothing. (nginx refuses any request body over 64 KB on this route; a plan of the size we expect, around 150 programmes, is about 25 KB. A bigger one is sent in several calls, first the channels and then the programmes in batches: each call is a safe upsert.) (`.secrets/PRESET_ADMIN_TOKEN` exists on the development stack only; production uses a token made in CTFd, with an expiry.) The same address with `/release` instead of `/programmes` takes `{"changes": [{"kind": "programme", "id": 3, "mode": "release"}, {"kind": "channel", "id": 1, "mode": "schedule", "at": 1795836600}], "reason": "the opening wave"}`; `at` is a UTC epoch second (09:00 IST on 28 November is `1795836600`).

**Good to know.** Hiding a programme in CTFd's own challenge editor counts as a *withhold* in the plan, and trying to show one there that is not on air is refused with a message pointing here: CTFd's challenge state follows the plan and is never set by hand. A scheduled drop happens on the first request at or after its second and is announced once as "New on air" (one line per channel, with a count and no names). After the end of the broadcast every programme on the plan can be read again (to keep something off air after the end, move the end later). The checks for all of this are in the table below.

## Scoring: what a programme is worth, setting solves aside, bonuses

**The page.** Sign in as the organiser and open <http://localhost:8080/admin/l3mon/scoring> (on the admin menu as "Scoring"). The first table lists every challenge with what it is worth now, how a dynamic one is set (start, floor, how many solves it takes to reach the floor), how many studios hold a solve, and a **Check** column that says "formula says 495 TRP" in red when the stored value is not what the formula gives. Under it: the bonus form, the solves set aside, the bonuses given and the latest scoring changes. Times are in India time. The page asks before every change and ignores a second click while the first is on its way.

**A dynamic programme.** It is worth the same to every studio that solved it, and the value falls as more studios solve it (CTFd's own curve: `n - 1` solves once anyone has solved it, down to the floor), so a studio's TRP can fall after it solved something. A challenge falls this way when it is of the *dynamic* type or when its scoring function in CTFd's editor is Linear or Logarithmic (Static is a fixed value); the page treats both alike. Only studios that are not banned and not hidden move the value. The value is put right by itself after anything that changes who counts: a ban, an unban, a hide, an unhide, a deleted studio, user or solve, a member removed from a studio, a solve added by "mark correct" or by hand, CTFd's "mark incorrect" (all in the same request), and once a minute a check compares every value with its formula and fixes (and reports in the audit table) anything the other ways missed. *Recalculate values* does it on demand.

**Setting solves aside (Revoke).** For a broken challenge. Type the reason in the box at the top (**every studio that loses a solve reads it**), press *Set aside solves* on the challenge, confirm. If the programme is still visible to players the question says so: a studio could solve it again at once, so **withhold the programme first** (Release control) when it is broken. Every studio's solve of that challenge stops counting at once: the TRP comes off the studio and its members, the challenge is worth its start value again if nobody holds a solve, and each studio gets a private line "Solve voided" with your reason. **Nothing is deleted**: the submission stays (who sent what, from where, when), and *Put back* returns the exact totals, places and tie-break positions. If a studio solved the challenge again meanwhile it keeps that one solve and its old record is marked *skipped*. Use CTFd's own "mark incorrect" for nothing: it deletes the submission and leaves no record. Pulling a programme off air (release control) is a different thing: it changes nobody's TRP.

**A bonus.** Pick the studio (and, if you like, one member), type the TRP (a whole number from -1000 to 1000, never 0; a negative one is shown as "Adjustment -20 TRP") and a message of up to 200 characters, press *Give bonus*, confirm. It counts everywhere TRP counts. **Only the title** ("Bonus +50 TRP") is public; the message is private to the studio and the crew. A team-wide bonus sits on the captain's account (CTFd sums a team's score from its members' awards); a bonus goes with its account if that account is deleted. The same bonus twice within a minute is refused as a double click. A wrong bonus is removed with CTFd's own award delete (admin, Awards).

**The same from a script** (a token, never a password): `GET /api/v1/l3mon/admin/scoring`, and `POST` to `.../scoring/revoke` `{"challenge_id": 3, "reason": "..."}`, `.../scoring/restore` `{"challenge_id": 3}`, `.../scoring/bonus` `{"team_id": 5, "user_id": 12, "trp": 50, "message": "..."}` (`user_id` is optional) and `.../scoring/recalculate` `{}`. A bad request names its field and changes nothing; "nothing to set aside" and "nothing to restore" answer 409. Reasons and messages are plain sentences: no `<` or `>`, no tabs or line breaks.

## The board: what a studio is told

A signed-in studio reads three things from the platform (the pages of SP6 will draw them; today they are the API): `GET /api/v1/l3mon/board` (the channels, the programmes on air with their TRP, which the studio has solved, its own TRP and place, and for each channel how many programmes are on air, still coming, and how many panels its cold open has), `GET /api/v1/l3mon/ticks` (a small answer the pages ask every 15 seconds and fetch the board again only when it changes) and the stock `GET /api/v1/challenges/<id>` (CTFd's own answer plus one `l3mon` block: difficulty, author, whether the value falls and where from and to, tries left, the studio's own score). The reply to a flag also says more: the value after the solve, the studio's reels, the channel's signal, whether it was first, and a plain reason when it is refused (no tries left, too fast, another flag being checked, paused, ended). A programme that is not on air is not hidden: it is not there, and every answer about it is the answer for an id that never existed. Each programme's difficulty (`warmup` ... `insane`) and delivery (`static_shared`, `static_per_participant`, `live_single`, `live_multi`) are set by the same plan call as before (`PUT /api/v1/l3mon/admin/programmes`, both optional); the author is CTFd's own "attribution" field.

After the end a flag is answered "the broadcast has ended" and nothing is recorded; before the start, while paused and after the end a hint cannot be bought (CTFd alone would allow all three).

## The Guide, the scoreboard and the bell: what a studio is told

Four more read-only answers for a signed-in studio (the pages of SP6 will draw them; today they are the API):

- `GET /api/v1/l3mon/guide`: the studio's own TRP and place, its solves, hints bought and instances running, **what each member brought in** (TRP, solves and their share; hint costs and bonuses are in nobody's line; a solve nobody can be named for is "Earlier solves"), the studio's bonuses added up, its five newest private lines, the progress of every channel, and the story meter. `GET /api/v1/l3mon/guide/epg` is the **programme grid** as markup: a row for each channel, a column for each difficulty, a block for each programme the studio may see (a programme that is not on air is not in it, only a count "N coming up"). The Guide stays open before the start; the grid is empty until then.
- `GET /api/v1/l3mon/scoreboard` and `.../scoreboard/rows`: "TRP ratings", the first 100 studios with TRP above zero. It is **the same list the CTFtime feed publishes**, row for row (both are made by one function), with the studio's own line (its live TRP, its place or none, how many are ranked). While the scoreboard is frozen the list stands still and a studio's own place is hidden; its own TRP stays live.
- `GET /api/v1/notifications?since_id=`: the bell. CTFd's own route, wrapped: a player gets the public news and the lines written for their own studio (a void, a restore, a bonus with the crew's words), each as `{id, title, content}`, oldest first, with no dates and no studio numbers. The crew keeps CTFd's own list. CTFd's own notification page, the detail of one notification (whatever way its number is written) and the event stream `/events` (which would push a line meant for one studio to everyone) are closed to players (404) until the theme brings its own. The numbers in the ticks (`notif_id`, `notif_ver`) follow the same list.
- A bought hint's reply (`POST /api/v1/unlocks`) gains `l3mon: {score, cost}`.

**Two crew settings** (`PATCH /api/v1/configs` with an administrator token): `l3mon_story_meter` (`on` unless switched off; off hides the meter) and `l3mon_story_air_target` (a whole number of solves of all studios that fills the "on air" bar; while unset the bar stays at 0). `l3mon_show_coming_count` also decides whether the grid says "N coming up".

**Try it:** sign in as a player in a browser and open `/api/v1/l3mon/guide` (and the others); or with the crew's token `curl -H "Authorization: Token $T" -H "Content-Type: application/json" .../api/v1/l3mon/scoreboard` (the crew gets the list without a studio line). Everything answers with a strong ETag: send `If-None-Match` with it and an unchanged answer comes back as `304`.

## The scoreboard freeze

The freeze is CTFd's own **Freeze Time** (admin page, Config, Visibility and time) or `PATCH /api/v1/configs {"freeze": <epoch seconds>}` with the organiser's token. It is not in `config/event.toml`: the crew sets it, moves it and lifts it during the round. Leave it empty for a round without a freeze.

**While it is on** (from its time onward) the public ranking, the CTFtime live feed and every other studio's view stand still: nobody can tell from a count, a value, a rank or the page's refresh that another studio has solved. **A programme's TRP stops falling** (the values are held, so a falling value cannot give anything away), a studio's own solves and score are live for that studio, its place says "Ranks frozen", and a hint bought during the freeze costs at once but shows on the public list only when the freeze is lifted. The crew's scoring page says so, and "Recalculate values" is switched off (the call answers 409). Revoke, Restore, a ban, a hide and a bonus all still work: what they change for solves from before the freeze shows on the public list at once (a banned cheater leaves it), and nothing about solves after the freeze can show. **Setting the freeze to a time that has already passed** is allowed: the values are worked out as of that time (the studios that solved before it), so nothing later shows. **Players cannot rate challenges while it is on** (CTFd's public rating totals would show a late solve); the crew can. While it is on, the crew's own CTFd standings also use the held values, so the true totals are seen only after the lift. If the freeze is lifted by a script instead of the admin page, the next page request notices and puts the values right.

**To end it:** once cheating cases are settled and bans applied, **lift the freeze** (empty the field, or move it to a time that has not come). Every value is put right in that request, the public list and the CTFtime feed show the true standings, and every page catches up on its next tick. Only then publish the final standings: the final CTFtime feed says "wait until the scoreboard freeze is lifted" while a freeze is on, because the held values are not final yet.

## Cold-open comics and the finale

A button under a channel's caption opens a short comic for the channel, once the channel has something on air. Only a registered, signed-in player can open it; a visitor is sent to the registration page. The crew can preview any of them: open `/story/<channel slug>` while signed in as the organiser. Since 2026-10-15 the comics are **painted pictures** (six for each channel, drawn by a friend of the organisers, the words painted into them) that the player **turns like pages** (arrow keys, a click or a swipe; nothing moves on by itself), with the same words kept as text for screen readers and for a browser without script.

**Where they come from.** The platform holds no story in its image or its database. The private story repository holds the scripts (`channels/<slug>.json`, the words and the alt text of every picture) and the pictures (`pictures/<slug>/p<n>.webp`), and the build checks them and writes the folder the platform reads, named after the channel slugs of the plan (`test-card`, `street`, `snack`, `gadget`, `ninja`, `chase`, `cubcop`) plus `finale`:

```bash
(cd tools && python -m l3mon story build ../private/story ../private/story/out)
```

The folder holds `<slug>.json` for each story and `pics/<slug>/<id>.webp` for each picture. The build refuses anything that breaks the rules (a picture that is not a plain still WebP of 320 to 4096 pixels a side and at most 400 KB, a link instead of a file, text with markup, limits on length) and writes nothing unless every channel passes; the platform checks each file again when it reads it, and checks each picture against the size and SHA-256 in its story when it looks at the folder and again when it sends the picture (a story with a missing, altered or swapped picture is simply not offered). **Development:** the stack mounts an empty folder, so no comic is on air; to play the real ones, set `L3MON_STORY_SRC` to the built folder before starting the stack. **Deployment:** copy the whole built folder, read-only, to where `L3MON_STORY_DIR` points; pictures are fetched one at a time (`/api/v1/l3mon/story/<slug>/pic/<id>`, 100 to 230 KB each, `private, max-age=300`) behind the same gate as the story.

**The finale.** The story `finale` belongs to no channel. It opens for every registered player when the Guide's on-air meter reaches 100 percent (the meter needs `l3mon_story_air_target` set), or when the crew turns on the setting **`l3mon_finale_open`** (CTFd's own settings: `PATCH /api/v1/configs {"l3mon_finale_open": true}`; set it to `false` to take it back). Before the start nothing opens it; the crew can preview it any time at `/story/finale`. While it is open it is in the list `GET /api/v1/l3mon/story`; the page's own button comes with the theme.

**To look at them without the platform:** `node private/story/tools/serve-preview.mjs` and open <http://localhost:8099/>. It plays the built files with the platform's own player, has a switch that pretends to be a visitor (the button then asks to sign up) and a switch per channel for "on air".

## The checks, and what each one proves

| Command | What it proves | Time |
|---------|----------------|------|
| `python -m pytest tools/tests -q` | Our own tools (hygiene scan, settings validation, secret generation, the accepted-advisories list) behave | seconds |
| `(cd tools && python -m l3mon hygiene --root .. --history)` | The public repository holds no flag, secret, dump or story material: in the files git tracks (flags in any letter case or encoding, tokens, key files, archives, captures, executables) and in every commit ever made. Add `--deny-words-file FILE` for the story words | seconds |
| `(cd tools && python -m l3mon api-rules check)` | The nginx lists of administrator routes match CTFd's source in the image (re-run `generate` after any CTFd upgrade) | seconds |
| `tools/verify-image.sh l3mon/ctfd:dev` | The image runs as an unprivileged user, has no pip or compiler, carries every upgraded package at its pinned version and the two Debian fixes, and the plugin files cannot be changed by the running process | about 30 s |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev` | CTFd's own 676 tests pass **on our image**: the upgraded libraries break nothing | about 7 min |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests` | Our plugin's own tests pass inside the image | seconds |
| `python -m pytest tests/integration -q` (stack running) | The real stack: health routes, headers, cookie flags, unknown hosts get nothing, the admin token works, every event setting is in force, only nginx is published, every container is unprivileged and limited, no secret anywhere in `docker inspect` or the logs, data survives a restart, a forged address never reaches CTFd, rate limits are nginx's own, body-size limits. **The hardening tests** (`test_hardening.py`) restart nginx so that nobody counts as an organiser and prove that every administrator route, any API token, any unlisted write and any multipart body is refused, that a player's own writes still work, that logs are rotated and mask reset tokens, that campus-speed traffic is not limited, and that the database root account cannot sign in from the network | about 90 s |
| `python -m pytest tests/browser/test_story_player_browser.py -q` (needs Node 22 and Chrome; no stack) | The cold-open player: its clock as pure functions, and the whole player in a real browser (it plays a drawn story and a painted one, turns pages by button, key, click and swipe, types, moves on, pauses, ends, can be watched again, can be closed by key, turns sound on only when asked, falls back to a still strip for reduced motion, shows hostile text as text, runs no script from a hostile picture, asks for nothing outside its own site, fits a phone) | about 10 s |
| `python -m pytest tests/browser -q` (stack running; needs Node 22 and Chrome) | The crew's release page and scoring page in a real browser: what they draw, a name or reason with markup stays text, every change asks first, a typed India time becomes the right second, a stale value is flagged and Recalculate puts it right, Revoke and Restore, a double click gives one bonus, what is being typed survives a refresh, a phone-width window | about 1 min |
| `tools/run-migration-test.sh` | The tests that need a real MariaDB and Redis, in throwaway containers: the migrations build exactly what the models describe (and run twice, or at the same moment, safely), the tick counter is atomic, and **eight workers meeting one scheduled drop show it, announce it and record it once**; eight workers revoking or restoring one challenge act once each, a Revoke and a Restore started together end consistent, a worker with an older view of the database still acts on current data, and a ban corrects a dynamic value by the end of its request; a solve in progress and an update of the same challenge never deadlock; eight bonuses at once give one, with or without a channel yet; the freeze; and, on a real Redis, that the flag the scheduler and the minute check share is taken once by eight workers and is taken over when a lost EXPIRE left it without a lifetime. `L3MON_DB_ISOLATION=READ-COMMITTED` runs the throwaway MariaDB at that level (the platform itself stays at the default: part 3.7 measured both); `-- -p no:randomly tests` runs CTFd's own suite on it (on a full run 103 of its tests fail at either level, because a later test's throwaway database lacks our tables once our plugins have been loaded; they pass alone, and the SQLite run passes all 676) | about 8 min |
| `tools/load_probe_run.sh --players 300 --seconds 60` (stack running; **a throwaway stack only**: it makes players, challenges and a plan) | A smoke test for gross mistakes under load; **not** the load test (that is k6 on the real hosts, SP9). It opens the event window, makes 300 players in studios of three and 60 programmes, then plays the event's mix from a container on the stack's own network: every player polls the tick every 15 s and fetches the board and the Guide when it moved, 20 flags a second go out, one in 40 right. It prints requests, errors and p50, p95 and p99 for each endpoint. `--poll 5 --flags 40` makes it three times as hard; `--hammer ticks,board,guide,scoreboard/rows,stock/me` asks one endpoint at a time as fast as 120 players can, which with the container's CPU time gives what one request of each costs (the numbers are in the [verification log](verification-log.md)). `--cleanup` removes what it made. **It refuses to start unless the running stack is the development one** (the development-only administrator token mounted and `localhost` trusted, checked before it changes anything), and the probe itself refuses an address that is not a stack on this machine, a plan with channels it did not make, a platform with more than 25 other accounts, and a prefix a real account could carry (everything it makes starts with `zzprobe`) | about 5 min |
| `python -m pytest tests/integration/test_restore_drill.py -q --run-drill` | A backup brings a **destroyed** platform back, within the 15-minute budget. **It deletes the local stack's data**, so it only runs when asked | about 1 min |

All of them run on every push in GitHub Actions ([the workflow](../../.github/workflows/ci.yml)): `hygiene`, `lint` (hadolint, shellcheck, compose files), `image` (the checks above plus pip-audit and Trivy against the reviewed list in [dependency-bumps.md](../security/dependency-bumps.md)), `ctfd-suite` and `integration` (including the drill). The image scan also runs every Monday, because new advisories appear without any change here.

## When something goes wrong

- **`Missing generated settings`**: run command 2 above.
- **A container is `unhealthy`**: `tools/compose.sh logs --tail 100 ctfd` (secrets are never logged).
- **The browser shows an empty page or "connection reset"**: you used an address other than `http://localhost:8080`. CTFd answers only to the hosts it trusts, and nginx drops anything else.
- **Port 8080 is taken**: `DEV_PORT=9090 tools/compose.sh up -d --wait`.
- **On Windows, paths like `/opt/venv` are rewritten**: put `MSYS_NO_PATHCONV=1` in front of a `docker run` command that has container paths.
