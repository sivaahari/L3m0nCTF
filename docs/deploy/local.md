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
| `python -m pytest tests/browser -q` (stack running; needs Node 22 and Chrome) | The crew's release page in a real browser: what it draws, a name with markup stays text, Release now asks first, a typed India time becomes the right second, the reason box survives a refresh and is cleared after a change, a phone-width window | about 1 min |
| `tools/run-migration-test.sh` | The tests that need a real MariaDB and Redis, in throwaway containers: the migrations build exactly what the models describe (and run twice, or at the same moment, safely), the tick counter is atomic, and **eight workers meeting one scheduled drop show it, announce it and record it once** | about 3 min |
| `python -m pytest tests/integration/test_restore_drill.py -q --run-drill` | A backup brings a **destroyed** platform back, within the 15-minute budget. **It deletes the local stack's data**, so it only runs when asked | about 1 min |

All of them run on every push in GitHub Actions ([the workflow](../../.github/workflows/ci.yml)): `hygiene`, `lint` (hadolint, shellcheck, compose files), `image` (the checks above plus pip-audit and Trivy against the reviewed list in [dependency-bumps.md](../security/dependency-bumps.md)), `ctfd-suite` and `integration` (including the drill). The image scan also runs every Monday, because new advisories appear without any change here.

## When something goes wrong

- **`Missing generated settings`**: run command 2 above.
- **A container is `unhealthy`**: `tools/compose.sh logs --tail 100 ctfd` (secrets are never logged).
- **The browser shows an empty page or "connection reset"**: you used an address other than `http://localhost:8080`. CTFd answers only to the hosts it trusts, and nginx drops anything else.
- **Port 8080 is taken**: `DEV_PORT=9090 tools/compose.sh up -d --wait`.
- **On Windows, paths like `/opt/venv` are rewritten**: put `MSYS_NO_PATHCONV=1` in front of a `docker run` command that has container paths.
