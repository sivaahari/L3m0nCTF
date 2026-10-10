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

## Cold-open comics

A button under a channel's caption opens a short comic for the channel, once the channel has something on air. Only a registered, signed-in player can open it; a visitor is sent to the registration page. The crew can preview any of them: open `/story/<channel slug>` while signed in as the organiser.

**Where they come from.** The platform holds no story in its image or its database. The private story repository builds checked files, one per channel, named after the channel's slug in the plan (`test-card`, `street`, `snack`, `gadget`, `ninja`, `chase`, `cubcop`):

```bash
(cd tools && python -m l3mon story build ../private/story ../private/story/out)
```

The build refuses a script or a picture that breaks the rules (no script, style, outside address or event handler in the art; limits on length and size) and writes nothing unless every channel passes; the platform checks each file again when it reads it. **Development:** the stack mounts an empty folder, so no comic is on air; to play the real ones, set `L3MON_STORY_SRC` before starting the stack: `L3MON_STORY_SRC=../../private/story/out tools/compose.sh up -d --wait`. **Production:** the folder is a read-only volume named `story`; copy the compiled files in with a one-off container (`docker run --rm -v l3mon_story:/to -v "$PWD/private/story/out":/from:ro alpine sh -c 'for f in /from/*.json; do n=$(basename "$f"); [ "$n" = manifest.json ] && continue; cp "$f" "/to/.$n.part" && mv "/to/.$n.part" "/to/$n"; done'`: the manifest stays behind, and each file is copied under a name the platform ignores and then renamed, so it is never read half written) and the platform picks them up within 20 seconds. A file that fails the checks is simply not available and the log says why once.

**To look at them without the platform:** `node private/story/tools/serve-preview.mjs` and open <http://localhost:8099/>. It plays the compiled files with the platform's own player, has a switch that pretends to be a visitor (the button then asks to sign up) and a switch per channel for "on air".

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
| `python -m pytest tests/browser/test_story_player_browser.py -q` (needs Node 22 and Chrome; no stack) | The cold-open player: its clock as pure functions, and the whole player in a real browser (it types, moves on, pauses, ends, can be watched again, can be closed by key, turns sound on only when asked, falls back to a still strip for reduced motion, shows hostile text as text, runs no script from a hostile picture, asks for nothing outside its own site, fits a phone) | about 10 s |
| `python -m pytest tests/browser -q` (stack running; needs Node 22 and Chrome) | The crew's release page and scoring page in a real browser: what they draw, a name or reason with markup stays text, every change asks first, a typed India time becomes the right second, a stale value is flagged and Recalculate puts it right, Revoke and Restore, a double click gives one bonus, what is being typed survives a refresh, a phone-width window | about 1 min |
| `tools/run-migration-test.sh` | The tests that need a real MariaDB and Redis, in throwaway containers: the migrations build exactly what the models describe (and run twice, or at the same moment, safely), the tick counter is atomic, and **eight workers meeting one scheduled drop show it, announce it and record it once**; eight workers revoking or restoring one challenge act once each, a Revoke and a Restore started together end consistent, a worker with an older view of the database still acts on current data, and a ban corrects a dynamic value by the end of its request; a solve in progress and an update of the same challenge never deadlock; eight bonuses at once give one, with or without a channel yet | about 4 min |
| `python -m pytest tests/integration/test_restore_drill.py -q --run-drill` | A backup brings a **destroyed** platform back, within the 15-minute budget. **It deletes the local stack's data**, so it only runs when asked | about 1 min |

All of them run on every push in GitHub Actions ([the workflow](../../.github/workflows/ci.yml)): `hygiene`, `lint` (hadolint, shellcheck, compose files), `image` (the checks above plus pip-audit and Trivy against the reviewed list in [dependency-bumps.md](../security/dependency-bumps.md)), `ctfd-suite` and `integration` (including the drill). The image scan also runs every Monday, because new advisories appear without any change here.

## When something goes wrong

- **`Missing generated settings`**: run command 2 above.
- **A container is `unhealthy`**: `tools/compose.sh logs --tail 100 ctfd` (secrets are never logged).
- **The browser shows an empty page or "connection reset"**: you used an address other than `http://localhost:8080`. CTFd answers only to the hosts it trusts, and nginx drops anything else.
- **Port 8080 is taken**: `DEV_PORT=9090 tools/compose.sh up -d --wait`.
- **On Windows, paths like `/opt/venv` are rewritten**: put `MSYS_NO_PATHCONV=1` in front of a `docker run` command that has container paths.
