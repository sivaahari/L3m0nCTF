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
