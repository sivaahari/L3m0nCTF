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
