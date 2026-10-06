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
