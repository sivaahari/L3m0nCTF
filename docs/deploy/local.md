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

**Signing in as the organiser:** user `organiser`; the password is in `.secrets/PRESET_ADMIN_PASSWORD` (open that file; do not paste it into chat or an issue). The admin pages are at `/admin`. Email is not set up in the local stack, so a self-registered player cannot verify an address: use the organiser account, or create players from the admin pages.

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
| Back up (database and uploads) | `tools/backup.sh` (writes `backups/<timestamp>/`, which git ignores) |
| Restore a backup into a running, **empty** platform | `tools/restore.sh backups/<timestamp>` (add `--force` to replace existing data) |

Backups are not encrypted by the script: they hold players' emails, so keep them on your own disk and never commit them.

If you change anything under `docker/`, `plugins/` or `ui/`, rebuild the image (command 3) and recreate the platform: `tools/compose.sh up -d --wait --force-recreate`.

## The checks, and what each one proves

| Command | What it proves | Time |
|---------|----------------|------|
| `python -m pytest tools/tests -q` | Our own tools (hygiene scan, settings validation, secret generation, the accepted-advisories list) behave | seconds |
| `(cd tools && python -m l3mon hygiene --root ..)` | The public repository holds no flag, secret, dump or story material | seconds |
| `tools/verify-image.sh l3mon/ctfd:dev` | The image runs as an unprivileged user, has no pip or compiler, carries every upgraded package at its pinned version and the two Debian fixes, and the plugin files cannot be changed by the running process | about 30 s |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev` | CTFd's own 676 tests pass **on our image**: the upgraded libraries break nothing | about 7 min |
| `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests` | Our plugin's own tests pass inside the image | seconds |
| `python -m pytest tests/integration -q` (stack running) | The real stack: health routes, headers, cookie flags, unknown hosts get nothing, the admin token works, every event setting is in force, only nginx is published, every container is unprivileged and limited, no secret in logs or `docker inspect`, data survives a restart, rate limits, body-size limits | about 50 s |
| `python -m pytest tests/integration/test_restore_drill.py -q --run-drill` | A backup brings a **destroyed** platform back, within the 15-minute budget. **It deletes the local stack's data**, so it only runs when asked | about 1 min |

All of them run on every push in GitHub Actions ([the workflow](../../.github/workflows/ci.yml)): `hygiene`, `lint` (hadolint, shellcheck, compose files), `image` (the checks above plus pip-audit and Trivy against the reviewed list in [dependency-bumps.md](../security/dependency-bumps.md)), `ctfd-suite` and `integration` (including the drill). The image scan also runs every Monday, because new advisories appear without any change here.

## When something goes wrong

- **`Missing generated settings`**: run command 2 above.
- **A container is `unhealthy`**: `tools/compose.sh logs --tail 100 ctfd` (secrets are never logged).
- **The browser shows an empty page or "connection reset"**: you used an address other than `http://localhost:8080`. CTFd answers only to the hosts it trusts, and nginx drops anything else.
- **Port 8080 is taken**: `DEV_PORT=9090 tools/compose.sh up -d --wait`.
- **On Windows, paths like `/opt/venv` are rewritten**: put `MSYS_NO_PATHCONV=1` in front of a `docker run` command that has container paths.
