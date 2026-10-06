# Dependency upgrades and the advisories that remain

Checked on 2026-10-06 against the official CTFd 3.8.8 image (`sha256:07cc9788d58b2a18ea4dc2103ff385c053ffc506f57d0c769a99387849aedd01`). Scanners used: `pip-audit` (Python Packaging Advisory Database) and Trivy (operating-system and Python packages).

## In short

- The official CTFd image ships libraries with **74 known advisories** (10 packages) and two operating-system packages with HIGH and CRITICAL ones.
- We upgraded everything that could be upgraded **without breaking CTFd's own 676 tests**: the same 676 tests pass on our image as on the unmodified one.
- What is left is **Flask 2.1.3 and Werkzeug 2.2.3** (every newer Flask breaks CTFd's tests, see the experiments below) and some Debian packages that have no fixed version yet. Each remaining advisory is explained below: whether it can be reached on our deployment and what stops it.
- The image no longer contains `pip` (nothing needs it when the platform runs).

## What was upgraded

| Package | CTFd 3.8.8 | Our image | Why |
|---------|-----------|-----------|-----|
| click | 8.2.1 | 8.5.0 | advisory fixed |
| cryptography | 45.0.6 | 50.0.2 | advisories fixed (pulls in cffi 2.1.1) |
| idna | 3.10 | 3.20 | advisory fixed |
| pillow | 12.2.0 | 12.3.0 | advisory fixed |
| python-dotenv | 0.13.0 | 1.2.4 | advisory fixed |
| requests | 2.32.4 | 2.34.2 | advisory fixed |
| urllib3 | 2.6.0 | 2.8.0 | advisories fixed |
| pydantic | 1.6.2 | 1.10.26 | advisory fixed (same major version, which CTFd needs) |
| setuptools | 79.0.1 | 84.0.0 (both Python installs) | its bundled copies of `wheel` and `jaraco.context` carried advisories (CVE-2026-24049, CVE-2026-23949) |
| pip | 24.0 | removed | not needed at run time; its bundled libraries only ever trigger scanner findings |
| libpcre2-8-0 (Debian) | 10.42-1+deb12u1 | 10.42-1+deb12u2 | HIGH fixed |
| perl-base (Debian) | 5.36.0-7+deb12u3 | 5.36.0-7+deb12u4 | 3 CRITICAL and 4 HIGH fixed |

The pins are in [`docker/ctfd/requirements.overrides.txt`](../../docker/ctfd/requirements.overrides.txt) and the operating-system step is in [`docker/ctfd/Dockerfile`](../../docker/ctfd/Dockerfile). `tools/verify-image.sh` checks every one of them on the built image.

## The experiments (CTFd's own test suite, 676 tests)

Run with `tools/run-ctfd-tests.sh IMAGE [OVERRIDES]` (a throwaway container; the test tools never enter an image).

| Change tried | Result |
|--------------|--------|
| None (the unmodified image) | **676 passed** |
| The isolated upgrades (click, cryptography, idna, pillow, python-dotenv, requests, urllib3) | **676 passed** |
| + setuptools 84.0.0 + pydantic 1.10.26 | **676 passed** |
| Our final image (all of the above, pip removed, Debian fixes) | **676 passed** |
| Flask 2.3.3 + Werkzeug 3.0.6 | 636 failed (`'CTFdFlask' object has no attribute 'session_cookie_name'`: CTFd's own Flask class uses an attribute that Flask 2.3 removed) |
| Flask 2.2.5 + Werkzeug 2.3.8 | 9 failed (file export, custom fields, cache tests, logout, session invalidation) |
| Flask 2.2.5 alone | 7 failed (the same family: file export, custom fields, cache tests, session invalidation) |
| Werkzeug 2.3.8 alone (Flask 2.1.3) | fails: Flask 2.1.3's test client cannot work with it |

So Flask 2.1.3 and Werkzeug 2.2.3 stay. Hand-patching CTFd's source to fit a newer Flask is not done: it would end the guarantee that we run the code CTFd's own tests approve, and CTFd 3.8.8 is what the whole plan is built on. When CTFd publishes a release that moves to a newer Flask, we upgrade to it (the procedure at the end of this page).

## The advisories that remain (Python)

| Advisory | What it is | Reachable on our deployment? | What stops it |
|----------|-----------|------------------------------|---------------|
| Flask CVE-2023-30861 and CVE-2026-27205 (fixed in 2.2.5 or later) | Some responses that use the session omit `Vary: Cookie`. A **shared cache that stores responses together with their `Set-Cookie`** could then hand one visitor's session cookie to another | **No**, unless a shared cache is added. Our nginx has no cache (`proxy_cache` is never turned on) | nginx adds `Vary: Cookie` to every proxied response (tested in `tests/integration`). The Cloudflare setup (SP9) must cache only static theme files and must bypass the cache for everything else; that is a requirement of the Cloudflare setup (SP9) |
| Werkzeug CVE-2024-34069 (fixed in 3.0.3) | The interactive debugger can run code on a developer's machine | **No.** The debugger exists only when Flask runs in debug mode. CTFd runs under gunicorn and debug mode is never switched on | `/console` (the debugger's address) answers 404 through nginx (tested) |
| Werkzeug CVE-2024-49766, CVE-2025-66221, CVE-2026-21860, CVE-2026-27199, CVE-2026-102598 | `safe_join` can be tricked on **Windows** (UNC paths, device names such as `NUL`) | **No.** Our servers run Linux | none needed; the code path is Windows only |
| Werkzeug CVE-2023-46136 and CVE-2024-49767 (fixed in 3.0.1 and 3.0.6) | A crafted multipart upload makes the form parser use a lot of time or memory | **In principle yes**, on any address that accepts a POST | nginx refuses any body over **1 MB** everywhere except `/admin` and `/api/v1/files` (10 MB, administrators only, see below), limits requests per address (30 per second general, a few per minute for sign-in), and the CTFd container has a memory limit and restarts if it is killed. All tested in `tests/integration`. Residual risk: low |
| Mako CVE-2026-102991 (Trivy, MEDIUM) | Path traversal in Mako's template lookup **on Windows** | **No.** Linux, and Mako is only used by the database migration tool for its own scripts, never for visitor input | none needed |

`/api/v1/files` accepts only an administrator's token or session; a request without one is refused by CTFd after nginx lets the body through, so the larger limit applies to the one place that needs it.

## The advisories that remain (Debian base image)

These Debian 12 packages have **no fixed version in Debian yet** (Trivy, 2026-10-06). The numbers below are the findings Trivy lists with no fix available.

| Package | Severity | Findings |
|---------|----------|----------|
| libsqlite3-0 | 1 CRITICAL, 2 HIGH | CVE-2025-7458 (CRITICAL); CVE-2026-11822, CVE-2026-11824 (HIGH) |
| zlib1g | 1 CRITICAL | CVE-2023-45853 |
| util-linux family (bsdutils, libblkid1, libmount1, libsmartcols1, libuuid1, mount, util-linux, util-linux-extra) | HIGH | CVE-2026-53613 and four others |
| libssl3, openssl | 1 HIGH | CVE-2026-84782 |
| ncurses (libncursesw6, libtinfo6, ncurses-base, ncurses-bin) | 1 HIGH | CVE-2025-69720 |
| libsystemd0, libudev1 | 1 HIGH | CVE-2026-16742 |
| gzip, libacl1, perl-base | 1 HIGH each | CVE-2026-41992, CVE-2026-54369, CVE-2026-9538 |

What limits the damage in the meantime: the container runs as an unprivileged user with a read-only root file system, all capabilities dropped and `no-new-privileges`; its database and cache sit on an internal network; only nginx is reachable from outside; and our plugin does not call these tools or libraries directly. They are checked again at every build and again before the freeze (see below). If a fixed Debian package appears, the next build picks it up through the `apt-get ... --only-upgrade` step or a rebuild on a newer base image.

## The other images in the stack (MariaDB, Redis, nginx)

Scanned the same way on 2026-10-06 (`--ignore-unfixed`, HIGH and CRITICAL). The pinned digests in [`compose.base.yml`](../../deploy/compose/compose.base.yml) are the newest the projects publish for these tags; nothing newer exists to upgrade to.

| Image | Result | Explanation |
|-------|--------|-------------|
| `nginxinc/nginx-unprivileged:stable-alpine` | clean | none |
| `redis:7-alpine` | OpenSSL CVE-2026-84782 and CVE-2026-75804 (HIGH) | Fixed Alpine packages exist but are not in the image yet. Redis in our stack has **TLS switched off** and listens only on the private internal network, so it never starts a TLS or QUIC handshake. Accepted until upstream rebuilds the image |
| `mariadb:10.11` | OpenSSL CVE-2026-84782 (HIGH), and 22 findings in `gosu` (a small Go helper whose standard library is old) | Same reasoning for OpenSSL: no TLS in our stack, private network only. `gosu` is how the image's default entrypoint drops privileges; **our container starts directly as user 999** (`user: "999:999"`), so it never runs. The scan skips that one file, and nothing else |

The reviewed identifiers are in [`deploy/compose/accepted-advisories.txt`](../../deploy/compose/accepted-advisories.txt); the CI job `third-party-images` fails on any other fixable HIGH or CRITICAL finding. When upstream publishes a rebuilt image: update the digest, run the scan, the integration tests and the restore drill, and remove the identifiers it fixes.

## How to repeat this

```bash
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
tools/verify-image.sh l3mon/ctfd:dev                 # every pin and every property of the image
tools/run-ctfd-tests.sh l3mon/ctfd:dev               # CTFd's own 676 tests on our image (about 7 minutes)
docker save l3mon/ctfd:dev -o ctfd.tar               # then scan it
docker run --rm -v "$PWD:/scan" aquasec/trivy image --input /scan/ctfd.tar --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL \
  --ignorefile /scan/docker/ctfd/accepted-advisories.txt
```

Expected: no output other than the reviewed advisories (without `--ignorefile`, the only fixable HIGH or CRITICAL findings are Flask CVE-2023-30861 and Werkzeug CVE-2024-34069, both explained above). Anything else is a new finding and fails the CI job `image`, whose ignore list (`docker/ctfd/accepted-advisories.txt`) a test keeps in step with this page.

## When this page must be revisited

- Before the content freeze (**2026-11-21**) and again the day before the round: re-run the commands above and read the new findings.
- When CTFd publishes a new release: try it with the same experiments; if it moves to a newer Flask, adopt it and shrink the table.
- When a shared cache is added anywhere in front of the platform (the Flask rows above stop being "No").
