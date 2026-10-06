# SP0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible, hardened, tested base for the L3m0nCTF platform: the repository skeleton, a CTFd 3.8.8 image with our plugin and theme baked in and its dependency advisories handled, compose stacks, configuration as code, backup and restore, CI, and the checks that prove each of these works.

**Architecture:** The public repo is a monorepo (`docker/`, `deploy/`, `plugins/`, `ui/`, `config/`, `tools/`, `tests/`). The image starts from the official `ghcr.io/ctfd/ctfd:3.8.8` pinned by digest and only adds: upgraded dependencies, our plugin, our theme. CTFd is configured from the environment (its own `PRESET_CONFIGS` and `PRESET_ADMIN_*`), generated from one public example file plus private secrets by a small standard-library Python tool, so a working instance can be rebuilt from an empty database in minutes. nginx, MariaDB and Redis run from pinned official images with our configuration files. Nothing in this plan puts a flag, a secret or story material in the public repo.

**Tech Stack:** Docker and Compose v2; `ghcr.io/ctfd/ctfd:3.8.8` (Python 3.11, Flask 2.1.3, gunicorn, gevent); MariaDB 10.11 LTS; Redis 7; nginx stable-alpine; Python 3.11+ standard library for our tools (`tomllib`, `unittest`-compatible tests run with pytest); GitHub Actions; pip-audit and Trivy for scanning.

## Global Constraints

- **Approved stack (D15, D16, 2026-10-06):** CTFd 3.8.x extended by plugin and theme only, no core edits; MariaDB, Redis, nginx, Centrifugo later (SP3), Cloudflare in front. Official registries only (Docker Hub, GHCR, PyPI, npm), pinned versions, nothing downloaded from other sources.
- **Public repo hygiene:** the repo `sivaahari/L3m0nCTF` is public and must never contain flags, challenge solutions, secrets, database dumps or unreleased story material. Secrets are generated into a git-ignored `.secrets/` directory and read from the environment or files at run time.
- **Git:** commit only as `sivaahari`, no co-author lines; work lands on `pre-deployment` first, `main` only after testing and approval.
- **Plain English only** in every document and string. No Hindi or Hinglish.
- **Dates:** online round is 09:00 IST on 2026-11-28 to 09:00 IST on 2026-11-29 (03:30 UTC both days); finals not decided. Domain `l3m0nctf.xyz`; landing at `https://l3m0nctf.xyz/`, platform at `https://play.l3m0nctf.xyz/`.
- **Targets:** 250+ teams expected, capacity target 1,000 teams; p95 under 300 ms for reads at twice the target (verified later in SP3 and SP9, the stack here must not make that impossible).
- **Security stance:** every container runs as an unprivileged user with a read-only root file system where possible, all capabilities dropped, `no-new-privileges`, memory and pid limits, no published ports except nginx, database and cache on an internal network only.
- **Machine:** Windows 11 with Docker Desktop (4 CPUs, 7.7 GB). Commands in this plan are written for Git Bash; when a path starts with `/` and is meant for the container, prefix the command with `MSYS_NO_PATHCONV=1`.
- **Every task ends with a verification command whose output is recorded in `docs/deploy/verification-log.md`** (date, command, result). A task with no recorded result is not done.

## File Structure

```
.gitattributes                         force LF for scripts, configs and Dockerfiles
.editorconfig                          indentation and final newline rules
.github/workflows/ci.yml               hygiene, tests, image build, audit, scan, integration
config/event.example.toml              the public example of the event settings
deploy/compose/compose.base.yml        services shared by every environment
deploy/compose/compose.dev.yml         local overrides (published port 8080, test secrets from .secrets/)
deploy/nginx/nginx.conf                reverse proxy, limits, headers, SSE, static caching
deploy/mariadb/l3mon.cnf               database settings
deploy/redis/redis.conf                cache settings
docker/ctfd/Dockerfile                 the image
docker/ctfd/requirements.overrides.txt  pinned upgrades of vulnerable dependencies
docker/ctfd/.dockerignore
plugins/l3mon_core/__init__.py         plugin entry (`load(app)`), health endpoint
plugins/l3mon_core/tests/test_plugin_loads.py
tools/l3mon/__init__.py, cli.py        command line: hygiene, config, secrets
tools/l3mon/hygiene.py                 scan the repo for flags, secrets, dumps, story names
tools/l3mon/config.py                  load, validate and render the event settings
tools/l3mon/secrets_gen.py             generate random secrets into .secrets/
tools/tests/test_hygiene.py, test_config.py, test_secrets.py
tools/backup.sh, tools/restore.sh      database and uploads backup and restore
tests/integration/test_stack.py        runs against the compose stack
ui/theme/l3mon/                        minimal theme (falls back to core for everything else)
docs/deploy/local.md                   how to run it on a laptop
docs/deploy/verification-log.md        dated record of every verification
docs/security/dependency-bumps.md      which packages were upgraded and why
```

---

### Task 1: Hygiene scanner and CI skeleton

**Files:**
- Create: `tools/l3mon/__init__.py`, `tools/l3mon/hygiene.py`, `tools/l3mon/cli.py`, `tools/tests/test_hygiene.py`, `.gitattributes`, `.editorconfig`, `.github/workflows/ci.yml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `hygiene.scan(root: Path) -> list[Finding]` where `Finding = (rule: str, path: str, line: int, message: str)`; `python -m l3mon hygiene [--root PATH]` exits 0 clean, 1 with findings.

- [ ] **Step 1: Write the failing tests** (`tools/tests/test_hygiene.py`): a temporary tree with (a) a file containing a flag with 32 hex digits inside its braces, (b) one with a PEM private key header, (c) a `.sql` dump, (d) a `.env` file, (e) a file with a story keyword from the private list passed in through the `--deny-words` argument, (f) a clean file, (g) the allowed format hint with three dots inside the braces. The dangerous strings are built from pieces inside the test file, so the test file itself passes the scanner. Assert rules `flag`, `private-key`, `dump`, `env-file`, `deny-word` fire exactly for a to e, and nothing fires for f and g.
- [ ] **Step 2: Run** `python -m pytest tools/tests/test_hygiene.py -q` and see it fail (module missing).
- [ ] **Step 3: Implement** `hygiene.py`: walk the tree skipping `.git`, `node_modules`, `.secrets`; flag patterns: `L3m0nCTF\{[^.}][^}]{3,}\}` (anything but the `...` hint), PEM private key blocks, AWS and GitHub token shapes (copy the patterns from `private/challenges/l3mon/banlists.py`), file names ending `.sql`, `.dump`, `.sqlite`, `.db`, `.pem`, `.key`, `.env` (not `.env.example`), and any whole-word match of the comma-separated `--deny-words` list. `cli.py` wires `python -m l3mon hygiene`.
- [ ] **Step 4: Run** the tests until they pass; also run `python -m l3mon hygiene --root .` on this repo and fix or allow-list every real finding (expect none).
- [ ] **Step 5: CI skeleton** `.github/workflows/ci.yml` with one job `hygiene` (checkout, setup-python 3.12, `pip install pytest`, `python -m pytest tools/tests -q`, `python -m l3mon hygiene --root .`). Add `.gitattributes` (`* text=auto eol=lf`, `*.png binary`) and `.editorconfig`.
- [ ] **Step 6: Commit** `tools: hygiene scanner for the public repo, and the CI skeleton`.

### Task 2: Event configuration and secrets tool

**Files:**
- Create: `config/event.example.toml`, `tools/l3mon/config.py`, `tools/l3mon/secrets_gen.py`, `tools/tests/test_config.py`, `tools/tests/test_secrets.py`
- Modify: `tools/l3mon/cli.py`, `.gitignore` (add `.secrets/`, `deploy/compose/.env`)

**Interfaces:**
- Produces: `config.load(path) -> dict`, `config.validate(data) -> list[str]` (empty when valid), `config.preset_configs(data) -> dict` (the JSON object for CTFd's `PRESET_CONFIGS`), `secrets_gen.generate(dir: Path, force: bool=False) -> dict[str,str]`; commands `python -m l3mon config validate FILE`, `config render FILE --out DIR` (writes `preset_configs.json`), `secrets generate [--dir .secrets]`.

`config/event.example.toml` (public, no secrets):

```toml
[event]
name = "L3m0nCTF 2026"
description = "Capture the flag by Amrita Vishwa Vidyapeetham, Coimbatore"
domain = "l3m0nctf.xyz"
platform_host = "play.l3m0nctf.xyz"
theme = "l3mon"
start = "2026-11-28T03:30:00Z"   # 09:00 IST
end = "2026-11-29T03:30:00Z"     # 09:00 IST

[teams]
mode = "teams"
size_max = 4

[visibility]
challenges = "private"     # only signed-in, verified players
accounts = "private"
scores = "public"
registration = "public"

[accounts]
verify_emails = true
```

- [ ] **Step 1: Failing tests**: `validate` accepts the example; rejects a missing `[event].name`, an `end` before `start`, a non-UTC time, `mode` outside `teams`/`users`, `size_max` outside 1..8, visibilities outside `public|private|admins`, a `platform_host` that is not under `domain`. `preset_configs` returns `{"setup": true, "ctf_name": ..., "ctf_theme": "l3mon", "user_mode": "teams", "team_size": 4, "start": <epoch>, "end": <epoch>, "challenge_visibility": "private", "account_visibility": "private", "score_visibility": "public", "registration_visibility": "public", "verify_emails": true}` with epoch seconds computed from the UTC times. `secrets_gen.generate` writes `SECRET_KEY` (64 hex), `DATABASE_PASSWORD`, `DATABASE_ROOT_PASSWORD`, `REDIS_PASSWORD` (each 32+ url-safe characters), `PRESET_ADMIN_PASSWORD`, `PRESET_ADMIN_TOKEN` (`ctfd_` plus 64 hex), `FLAG_HMAC_SECRET` (64 hex); each into its own file with mode 0600 (best effort on Windows); refuses to overwrite without `force`; never prints a value.
- [ ] **Step 2: Run** and watch them fail. **Step 3: Implement** with `tomllib`, `datetime`, `secrets`. **Step 4: Run** until green.
- [ ] **Step 5: Verify against the real image** later in Task 7 (CTFd must accept every key in `preset_configs`); record in the verification log.
- [ ] **Step 6: Commit** `tools: event configuration and secrets generation`.

### Task 3: The CTFd image

**Files:**
- Create: `docker/ctfd/Dockerfile`, `docker/ctfd/requirements.overrides.txt`, `docker/ctfd/.dockerignore`
- Test: `tools/verify-image.sh`

```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/ctfd/ctfd@sha256:07cc9788d58b2a18ea4dc2103ff385c053ffc506f57d0c769a99387849aedd01 AS base
USER root
COPY docker/ctfd/requirements.overrides.txt /tmp/overrides.txt
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade -r /tmp/overrides.txt && rm /tmp/overrides.txt
COPY --chown=1001:1001 plugins/ /opt/CTFd/CTFd/plugins/
COPY --chown=1001:1001 ui/theme/l3mon /opt/CTFd/CTFd/themes/l3mon
USER 1001
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthcheck', timeout=4).status==200 else 1)"
```

- [ ] **Step 1: Verify the digest is the official 3.8.8 image** (`docker manifest inspect ghcr.io/ctfd/ctfd:3.8.8`; the Docker Hub image has the same index). Record the digest in the log.
- [ ] **Step 2: First overrides** (`requirements.overrides.txt`, from the pip-audit run of 2026-10-06: 74 advisories in 10 packages): start with the packages whose bump is isolated: `pillow>=12.3.0`, `urllib3>=2.8.0`, `requests>=2.33.0`, `idna>=3.15`, `click>=8.3.3`, `python-dotenv>=1.2.2`, `cryptography>=46.0.7`. Flask 2.1.3, Werkzeug 2.2.3 and pydantic 1.6.2 are handled in Task 4.
- [ ] **Step 3: Build** `docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .` and expect success.
- [ ] **Step 4: Verify** with `tools/verify-image.sh`: runs the image as user 1001, `pip check` is clean, the plugin directory and theme exist, `python -c "import CTFd"` works, and `pip-audit` on the image's installed packages reports only the advisories listed in `docs/security/dependency-bumps.md` (an explicit, reviewed list).
- [ ] **Step 5: Commit** `docker: CTFd 3.8.8 image with upgraded dependencies, our plugin and theme`.

### Task 4: Dependency bumps against CTFd's own test suite

**Files:**
- Modify: `docker/ctfd/requirements.overrides.txt`
- Create: `docs/security/dependency-bumps.md`, `tools/run-ctfd-tests.sh`

- [ ] **Step 1: Baseline.** Run CTFd's own suite inside the **unmodified** image's source with the dev requirements (`pytest`, SQLite, `-n 2` parallel) and record the pass and fail counts. A failure present in the baseline is not ours to fix; record it.
- [ ] **Step 2: Apply the Task 3 overrides**, rerun, and require the same result as the baseline.
- [ ] **Step 3: Flask and Werkzeug.** Try `flask==2.2.5` with `werkzeug==2.3.8`, then `flask==2.3.3` with `werkzeug==3.0.6`, then the newest Werkzeug that the pinned Flask-RESTX and Flask-SQLAlchemy accept. Each attempt: install, `pip check`, rerun the suite. Keep the highest set that matches the baseline. For every advisory that remains, write one line in `docs/security/dependency-bumps.md`: what it is, whether the vulnerable code path is reachable in our deployment (for example the Werkzeug debugger is never enabled, multipart limits are enforced by nginx), and the mitigation.
- [ ] **Step 3: pydantic 1.6.2** (a transitive pin): bump to `1.10.13` or later if the suite stays green, otherwise document.
- [ ] **Step 4: Record** the final matrix (package, old, new, suite result) in the document and in the verification log.
- [ ] **Step 5: Commit** `docker: dependency upgrades verified against CTFd's own tests, with the remaining advisories explained`.

### Task 5: Plugin and theme skeletons

**Files:**
- Create: `plugins/l3mon_core/__init__.py`, `plugins/l3mon_core/tests/test_plugin_loads.py`, `ui/theme/l3mon/templates/.gitkeep` (a theme that overrides nothing yet: CTFd's `THEME_FALLBACK` serves every missing template from `core`), `ui/theme/l3mon/static/.gitkeep`

```python
# plugins/l3mon_core/__init__.py
from flask import Blueprint, jsonify

l3mon = Blueprint("l3mon_core", __name__)


@l3mon.route("/l3mon/healthz")
def healthz():
    return jsonify({"status": "ok", "plugin": "l3mon_core"}), 200


def load(app):
    app.register_blueprint(l3mon)
```

- [ ] **Step 1: Failing test** using CTFd's own test helpers (`create_ctfd`, `destroy_ctfd`) inside the image: GET `/l3mon/healthz` returns 200 and `{"status": "ok"}`; the response has no cookie. Run it through `tools/run-ctfd-tests.sh --plugin l3mon_core`.
- [ ] **Step 2: Implement** the plugin, rebuild the image, rerun.
- [ ] **Step 3: Commit** `plugins: l3mon_core with a health endpoint, and an empty l3mon theme`.

### Task 6: Compose stack, nginx, MariaDB, Redis

**Files:**
- Create: `deploy/compose/compose.base.yml`, `deploy/compose/compose.dev.yml`, `deploy/nginx/nginx.conf`, `deploy/mariadb/l3mon.cnf`, `deploy/redis/redis.conf`, `deploy/compose/.env.example`

Design decisions to implement (each becomes an assertion in Task 7):
1. Services: `ctfd` (our image, `WORKERS=4`, `WORKER_CLASS=gevent`, `REVERSE_PROXY=true`, `TRUSTED_HOSTS` from the event settings, `SERVER_SENT_EVENTS=true`, `UPDATE_CHECK=false`, `SWAGGER_UI=false`, `SESSION_COOKIE_SAMESITE=Lax`, `PERMANENT_SESSION_LIFETIME=28800`, `HTML_SANITIZATION=true`, upload folder on a named volume), `db` (`mariadb:10.11` pinned by digest), `cache` (`redis:7-alpine` pinned by digest, password required), `nginx` (`nginx:stable-alpine` pinned by digest).
2. Networks: `edge` (nginx and ctfd) and `internal: true` (ctfd, db, cache). Only nginx publishes a port (`127.0.0.1:8080` in dev).
3. Every service: `restart: unless-stopped`, `read_only: true` with `tmpfs` for `/tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `mem_limit`, `pids_limit`, healthchecks. Exceptions (MariaDB and Redis need a writable data volume; nginx needs `NET_BIND_SERVICE` only if it binds below 1024, so it listens on 8080 inside) are written down next to the setting.
4. Secrets: files from `.secrets/` mounted as Docker secrets; `ctfd` reads `SECRET_KEY` and database credentials from `_FILE`-style entrypoint wrappers (CTFd has no `_FILE` support, so a tiny `docker/ctfd/entrypoint-secrets.sh` exports them from the files before calling CTFd's own entrypoint; its tests are part of Task 7).
5. nginx: `limit_req` zones (login 10 per minute per address, flag submit 60 per minute per address, general 20 per second), `client_max_body_size 10m`, gzip for text types, security headers (`Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy: same-origin`, `Permissions-Policy`, `X-Frame-Options: DENY`; the Content-Security-Policy arrives with the theme in SP6), the `/events` location without buffering, static files with long cache, `real_ip_header CF-Connecting-IP` from Cloudflare ranges only (a list file the deploy tool refreshes), a catch-all that denies hosts other than `platform_host`, and `/admin` restricted by an `allow` list include.
6. MariaDB: `character-set-server=utf8mb4`, `collation-server=utf8mb4_unicode_ci`, `max_connections=400`, `innodb_buffer_pool_size` as a variable sized by environment, slow query log on, `skip-name-resolve`, bind to the internal network only.
7. Redis: `requirepass` from the secret, `maxmemory 256mb`, `maxmemory-policy allkeys-lru` for the cache role, no persistence in dev.

- [ ] **Step 1: Write the configuration files and compose files.**
- [ ] **Step 2: Lint**: `docker compose -f deploy/compose/compose.base.yml -f deploy/compose/compose.dev.yml config -q` and `docker run --rm -v <repo>:/w nginx:stable-alpine nginx -t -c /w/deploy/nginx/nginx.conf` (with the include paths mounted) both succeed.
- [ ] **Step 3: Bring it up**: `python -m l3mon secrets generate`, `python -m l3mon config render config/event.example.toml --out deploy/compose/generated`, `docker compose ... up -d --wait`; expect every service healthy.
- [ ] **Step 4: Commit** `deploy: compose stack with nginx, MariaDB and Redis, hardened`.

### Task 7: Integration test of the running stack

**Files:**
- Create: `tests/integration/test_stack.py` (standard library `urllib` and `subprocess`, pytest markers `integration`)

Assertions (one test each, all run against `http://127.0.0.1:8080` with the correct `Host` header):
1. `/healthcheck` and `/l3mon/healthz` return 200 through nginx.
2. The preset admin can sign in with the generated password, the preset token authenticates `GET /api/v1/users/me`, and CTFd reports `setup` as complete (no setup wizard) with `ctf_name`, theme `l3mon`, team mode, team size 4, start and end epochs equal to the event file, `verify_emails` true (read through the admin API).
3. Security headers are present on `/`, a request with a wrong `Host` is refused, `/admin` from outside the allow list is refused, a 5th login failure within the rate window gets 429 from nginx.
4. No published ports other than nginx's (`docker compose ps --format json`); the database and cache ports are unreachable from the host.
5. Every container runs as a non-root user, has a read-only root file system and no added capabilities (`docker inspect`).
6. A registration, email-verification-disabled flow is **not** part of SP0 (SP5); but the stack must survive `docker compose restart ctfd` and come back healthy with the same data.
7. Logs contain no password, token or `SECRET_KEY` value (search the combined logs for each generated secret).

- [ ] **Step 1: Write the tests, run them, and fix the stack until all pass.** Record the run in the verification log.
- [ ] **Step 2: Commit** `tests: integration tests for the compose stack`.

### Task 8: Backup and restore, with a drill

**Files:**
- Create: `tools/backup.sh`, `tools/restore.sh`, `docs/deploy/runbook-restore.md`

- [ ] **Step 1: Backup**: `mariadb-dump --single-transaction --routines` piped through `gzip`, the uploads volume as a tar, a manifest with SHA-256 sums and the CTFd and image versions, all written to an output directory and optionally encrypted with `age` when a recipient is given (the `age` package from its official GitHub release or the Alpine package, pinned).
- [ ] **Step 2: Restore**: into an empty database and an empty uploads volume, verify the manifest sums first, refuse to run against a non-empty database without `--force`.
- [ ] **Step 3: Drill**: create a user and a page through the API, back up, destroy the stack including volumes, bring up an empty stack, restore, and assert the user and page are back and the admin token still works. Time it: target under 15 minutes for the dev data set (RTO from the build design).
- [ ] **Step 4: Record** the timing and result; **Commit** `deploy: backup and restore tools with a rehearsed drill`.

### Task 9: CI completion and local documentation

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `docs/deploy/local.md`, `docs/deploy/verification-log.md`

- [ ] **Step 1: Jobs**: `hygiene` (done), `tools` (pytest on `tools/tests`), `image` (build, `pip check`, `pip-audit` against the reviewed advisory list, Trivy scan with `HIGH,CRITICAL` failing unless listed in `docs/security/dependency-bumps.md`), `ctfd-tests` (the Task 4 script), `integration` (compose stack plus `tests/integration`), `compose-lint` (`docker compose config -q`, hadolint for the Dockerfile and shellcheck for scripts, both from their official images).
- [ ] **Step 2: `docs/deploy/local.md`**: five commands from a clean clone to a working local stack, how to read the logs, how to reset, how to run every check, what each check proves.
- [ ] **Step 3: Push** `pre-deployment` and check that every job is green in GitHub Actions; fix and repeat until it is. Record the run URL in the verification log.
- [ ] **Step 4: Commit** `ci: full pipeline, and the local deployment guide`.

---

## Self-review against the spec (build design section 6, SP0)

| SP0 requirement | Task |
|-----------------|------|
| Monorepo skeleton | File Structure, Tasks 1 to 6 |
| CTFd 3.8.8 image with safe dependency bumps, plugins and theme baked in | 3, 4, 5 |
| Compose stacks for dev, staging, production | 6 (dev now; staging and production overrides are added when the Google Cloud hosts exist, they reuse `compose.base.yml`) |
| nginx, MariaDB, Redis configuration | 6 |
| Config as code with a seed tool that rebuilds a working instance from an empty database | 2, 7 (PRESET_CONFIGS and PRESET_ADMIN), 8 |
| CI (lint, unit tests, CTFd's own suite, `pip-audit`, image scan, hygiene scan) | 1, 9 |
| Push to `pre-deployment` updates staging | Not in this plan: staging hosts do not exist yet; a deploy job is added with the Google Cloud setup |
| A restore from backup rehearsed once | 8 |
| MariaDB `max_connections` above workers times pool size; sessions in Redis; `SECRET_KEY`; `WORKERS` matched to cores | 6 (settings), 7 (assertions) |

Open points this plan deliberately leaves to later sub-projects: Centrifugo (SP3), the instancer (SP4), the abuse suite and CAPTCHA (SP5), the real theme and its Content-Security-Policy (SP6), sign-in plugins (SP8), monitoring and load tests (SP9). Staging and production overrides wait for the Google Cloud project (decision D10).
