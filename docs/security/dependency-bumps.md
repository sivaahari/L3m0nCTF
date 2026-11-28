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
| pillow | 12.2.0 | 13.0.0 | 12.3.0 fixed the first advisory; 13.0.0 (2026-11-03) fixes the 39 later ones (PYSEC-2026-4326 to 4343 and 21 GitHub advisories) |
| certifi | 2025.8.3 | 2026.10.23 | PYSEC-2026-4320, and CTFd 3.8.9's own security fix: the bundle held a root certificate that must not be trusted. We stay on CTFd 3.8.8 (the plugins refuse any other version); 3.8.9 contains only this and a `pip` bump, and our image has no `pip` |
| python-dotenv | 0.13.0 | 1.2.4 | advisory fixed |
| requests | 2.32.4 | 2.34.2 | advisory fixed |
| urllib3 | 2.6.0 | 2.8.0 | advisories fixed |
| pydantic | 1.6.2 | 1.10.26 | advisory fixed (same major version, which CTFd needs) |
| setuptools | 79.0.1 | 84.2.1 (both Python installs) | its bundled copies of `wheel` and `jaraco.context` carried advisories (CVE-2026-24049, CVE-2026-23949); 84.0.0 itself carried GHSA-xmj3-9gf7-h52w, fixed in 84.1.0 |
| pip | 24.0 | removed | not needed at run time; its bundled libraries only ever trigger scanner findings |
| libpcre2-8-0 (Debian) | 10.42-1+deb12u1 | 10.42-1+deb12u2 | HIGH fixed |
| perl-base (Debian) | 5.36.0-7+deb12u3 | 5.36.0-7+deb12u4 | 3 CRITICAL and 4 HIGH fixed |
| libssl3 and openssl (Debian) | 3.0.22-1~deb12u1 | 3.0.22-1~deb12u2 | HIGH CVE-2026-84782 fixed (Debian published the fix after 2026-10-06; it was an accepted advisory until then) |
| apt and libapt-pkg6.0 (Debian) | 2.6.1 | 2.6.2 | HIGH CVE-2026-109518 fixed (an attacker on the network path while apt downloads packages; Debian published 2.6.2 on 2026-11-05; apt only runs when the image is built, not while the platform runs, but the fix costs nothing, so it is taken instead of accepted) |

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

## Who may use the administrator API (found by the independent audit)

nginx's admin allow-list used to protect only the `/admin` pages, but almost every administrator action goes through the REST API under `/api/v1`, which every address could reach (and the preset administrator token works from anywhere and never expires). Now nginx treats these as the **administrator surface** and answers them only for the organisers' addresses (`ADMIN_NETS_FILE`, development default in `deploy/nginx/snippets/admin-nets.conf`):

1. any request with an `Authorization` header (API tokens are for organisers and tools, never for a player's browser);
2. any route CTFd itself guards with `@admins_only`, whatever the method (the list is read from CTFd's own source: `python -m l3mon api-rules generate`, and a test fails when it is out of date after a CTFd upgrade);
3. any write (POST, PUT, PATCH, DELETE) to `/api/v1` that is not on the short list of writes a signed-in player makes (flag attempts, own profile, own tokens, own team, hint unlocks, ratings). **This fails closed:** a write route added later is admin-only until someone lists it.

The preset administrator token (`PRESET_ADMIN_TOKEN`) exists only in the development and CI stack (`compose.dev.yml`). Production must not set it: organiser tokens are created inside CTFd, expire, and can be revoked. Remaining decision for production (SP9): put the admin area on a separate host name behind a second factor, because a stolen administrator *session* is still valid from anywhere that nginx lets the admin pages through.

## The advisories that remain (Python)

| Advisory | What it is | Reachable on our deployment? | What stops it |
|----------|-----------|------------------------------|---------------|
| Flask CVE-2023-30861 and CVE-2026-27205 (fixed in 2.2.5 or later) | Some responses that use the session omit `Vary: Cookie`. A **shared cache that stores responses together with their `Set-Cookie`** could then hand one visitor's session cookie to another | **No**, unless a shared cache is added. Our nginx has no cache (`proxy_cache` is never turned on) | nginx adds `Vary: Cookie` to every proxied response (tested), but note that **Cloudflare ignores `Vary: Cookie`**, so this is only a second line. The real control is the Cloudflare cache rule: cache only static theme files, bypass the cache for everything else (a requirement in [the production checklist](../deploy/production-checklist.md)) |
| Werkzeug CVE-2024-34069 (fixed in 3.0.3) | The interactive debugger can run code on a developer's machine | **No.** The debugger exists only when Flask runs in debug mode. CTFd runs under gunicorn and debug mode is never switched on | `/console` (the debugger's address) answers 404 through nginx (tested) |
| Werkzeug CVE-2024-49766, CVE-2025-66221, CVE-2026-21860, CVE-2026-27199, CVE-2026-102598 | `safe_join` can be tricked on **Windows** (UNC paths, device names such as `NUL`) | **No.** Our servers run Linux | none needed; the code path is Windows only |
| Werkzeug CVE-2023-46136 and CVE-2024-49767 (fixed in 3.0.1 and 3.0.6) | A crafted multipart upload makes the form parser use a lot of time or memory | **Yes in principle, and for anyone**: CTFd's CSRF check reads the form of every non-JSON POST before it checks who is asking, so the parser runs on an anonymous visitor's body (an earlier version of this page wrongly said only administrators could reach it; the independent audit corrected that) | nginx **refuses every `multipart/form-data` request** from any address that is not an organiser's (415, before the body is read; players never upload anything), caps every other body at **64 KB** (10 MB only for `/admin` and the files API, and those are admin-only), limits requests per address, and the CTFd container has a memory limit and restarts if it is killed. All tested in `tests/integration/test_hardening.py`. Residual risk: low (only an organiser's own address can still reach the parser with a large body) |
| Mako CVE-2026-102991 (Trivy, MEDIUM) | Path traversal in Mako's template lookup **on Windows** | **No.** Linux, and Mako is only used by the database migration tool for its own scripts, never for visitor input | none needed |

`/api/v1/files` is on the generated administrator list (next section): from any other address nginx answers 403 before the body is read, so the larger body limit applies to the organisers only.

## The advisories that remain (Debian base image)

These Debian 12 packages have **no fixed version in Debian yet** (Trivy, 2026-10-06). The numbers below are the findings Trivy lists with no fix available.

| Package | Severity | Findings |
|---------|----------|----------|
| libsqlite3-0 | 1 CRITICAL, 2 HIGH | CVE-2025-7458 (CRITICAL); CVE-2026-11822, CVE-2026-11824 (HIGH) |
| zlib1g | 1 CRITICAL | CVE-2023-45853 |
| util-linux family (bsdutils, libblkid1, libmount1, libsmartcols1, libuuid1, mount, util-linux, util-linux-extra) | HIGH | CVE-2026-53613 and four others |
| ncurses (libncursesw6, libtinfo6, ncurses-base, ncurses-bin) | 1 HIGH | CVE-2025-69720 |
| libsystemd0, libudev1 | 1 HIGH | CVE-2026-16742 |
| gzip, libacl1, perl-base | 1 HIGH each | CVE-2026-41992, CVE-2026-54369, CVE-2026-9538 |

What limits the damage in the meantime: the container runs as an unprivileged user with a read-only root file system, all capabilities dropped and `no-new-privileges`; its database and cache sit on an internal network; only nginx is reachable from outside; and our plugin does not call these tools or libraries directly. They are checked again at every build and again before the freeze (see below). If a fixed Debian package appears, the next build picks it up through the `apt-get ... --only-upgrade` step or a rebuild on a newer base image.

## The other images in the stack (MariaDB, Redis, nginx)

Scanned the same way on 2026-10-06, again on 2026-11-03 and once more on 2026-11-04 (`--ignore-unfixed`, HIGH and CRITICAL). The pinned digests in [`compose.base.yml`](../../deploy/compose/compose.base.yml) are the newest the projects publish for these tags (all three were moved on 2026-11-03: the old nginx digest carried three nginx findings, CVE-2026-15176, CVE-2026-93479 and CVE-2026-97205, a libX11 and a libtiff one, all fixed in the new image).

| Image | Result | Explanation |
|-------|--------|-------------|
| `nginxinc/nginx-unprivileged:stable-alpine` | brotli-libs CVE-2026-111497 (CRITICAL), curl and libcurl CVE-2026-92392, libexpat CVE-2026-117610, CVE-2026-118370 and, since 2026-11-04, CVE-2026-113351 (HIGH; a denial of service through memory tracking in `XML_ParserReset`, fixed in libexpat 2.10.0-r0) | Fixed Alpine packages exist but are not in the image yet. **None of them is loaded by nginx**: the binary links only musl, pcre2, OpenSSL and zlib (`ldd /usr/sbin/nginx`), our configuration loads no module (the image-filter module is what needs freetype, fontconfig and so brotli and expat), and the health check uses `wget`, not `curl`. Accepted until upstream rebuilds the image |
| `redis:7-alpine` | OpenSSL CVE-2026-84782 and CVE-2026-75804 (HIGH) | Fixed Alpine packages exist but are not in the image yet. Redis in our stack has **TLS switched off** and listens only on the private internal network, so it never starts a TLS or QUIC handshake. Accepted until upstream rebuilds the image |
| `mariadb:10.11` | OpenSSL CVE-2026-84782 (HIGH), apt and libapt-pkg6.0 CVE-2026-109518 (HIGH, since 2026-11-05), and 22 findings in `gosu` (a small Go helper whose standard library is old) | Same reasoning for OpenSSL: no TLS in our stack, private network only. `gosu` is how the image's default entrypoint drops privileges; **our container starts directly as user 999** (`user: "999:999"`), so it never runs. The scan skips that one file, and nothing else |

**2026-11-04:** the CI scan turned red on every branch because of one new libexpat advisory (CVE-2026-113351). The floating tag `stable-alpine` still points to the pinned digest (checked the same day), so no rebuilt image exists yet; it is accepted for the reason above (nginx links no libexpat, and `fontconfig`, which needs it, serves only the unused image-filter module). If advisories in this image keep recurring before the event, the cleaner cure is the `stable-alpine-slim` variant of the same nginx (1.30.6): it has no freetype, fontconfig, brotli or expat packages at all. It was **not** switched now: it changes the tested image, so it needs the integration tests and the restore drill first.

**2026-11-09 (looked at 2026-11-14):** the scan turned red on the same pinned nginx digest because of three more fixable HIGH advisories on Alpine 3.24.2: musl (and musl-utils) CVE-2026-107891, zlib CVE-2026-85091 and libexpat CVE-2026-115506. The floating tag `stable-alpine` still points to the pinned digest (checked 2026-11-14), so no rebuilt image exists. These two libraries, unlike expat, **are** linked by nginx, so it was checked on the pinned image instead of assumed: CVE-2026-107891 is in musl's `regcomp`, and the nginx binary imports neither `regcomp` nor `regexec` (it compiles patterns with PCRE2, from our own configuration at start-up); CVE-2026-85091 is in zlib's `gz_vacate`, which belongs to the `gzopen`/`gzwrite` file functions, and the nginx binary imports none of the `gz*` functions (it calls only `deflate` and `inflate` on streams); `ldd /usr/sbin/nginx` still shows only musl, OpenSSL, pcre2 and zlib, and our configuration has no `load_module` line (the dynamic modules on disk are never loaded). Accepted until upstream rebuilds the image; the cure is the same as above (a new digest, then remove the identifiers).

**2026-11-14:** a fresh scan of the same pinned nginx digest also reports three HIGH advisories in `libxslt` (CVE-2026-125350, CVE-2026-87636 and CVE-2026-9947; fixed in libxslt 1.1.43-r4, not yet in the image). `libxslt` is what the image's XSLT filter module needs; the nginx binary does not link it (`ldd /usr/sbin/nginx` lists only musl, OpenSSL, pcre2 and zlib) and our configuration has no `load_module` line, so it is never loaded. Accepted for the same reason as the libexpat ones. This image keeps collecting advisories in libraries nginx does not use: after the event the cleaner cure named on 2026-11-04 (the `stable-alpine-slim` variant, which does not ship freetype, fontconfig, brotli, expat or xslt) should be taken, with the integration tests and the restore drill.

**2026-11-05:** a new advisory in Debian and Ubuntu's `apt` (CVE-2026-109518, HIGH: an attacker on the network path while apt downloads packages; fixed in apt 2.6.2 on Debian 12 and 2.4.15ubuntu0.1 on Ubuntu 22.04) turned two scans red. **The platform image takes the fix** (`docker/ctfd/Dockerfile` upgrades `apt` and `libapt-pkg6.0`, and `tools/verify-image.sh` checks the version). **The MariaDB image cannot be patched by us**: its floating tag still points to the pinned digest (checked the same day, apt 2.4.14), so it is accepted: apt never runs while the database runs (the container starts directly as user 999 with `mariadbd`; nothing is installed or downloaded), and the database sits on an internal network with no route out. The cure is to re-pin the digest when MariaDB publishes a rebuilt image, and then remove the identifier.

**2026-11-19 (nginx itself, six advisories, two of them CRITICAL):** the scan of the pinned nginx digest (nginx 1.30.6) reports six advisories in nginx, all fixed in nginx 1.30.7. The official `nginx` image has 1.30.7 since 2026-11-18, but the `nginxinc/nginx-unprivileged` flavour we pin has no 1.30.7 build yet (its `stable-alpine` tag is still the build of 2026-10-19, checked on Docker Hub on 2026-11-19). What each one needs, and what we do about it (every request in the production log of 19 November arrived as HTTP/2.0, so Cloudflare does speak HTTP/2 to our server and the HTTP/2 entries were reachable in principle):

| Advisory | Severity | What it needs | On our deployment |
|----------|----------|---------------|-------------------|
| CVE-2026-121810 | CRITICAL | a configuration that uses **HTTP/3** (a worker crash, possibly code execution) | **Not configured:** we have no `quic` listener |
| CVE-2026-123007 | CRITICAL | a configuration that uses **HTTP/2**; undisclosed traffic makes the flow-control window overflow (a worker crash, memory corruption, possibly code execution) | **Closed by configuration:** HTTP/2 is switched off between Cloudflare and nginx (`http2` is not enabled; Cloudflare negotiates HTTP/1.1) |
| CVE-2026-123633 | CRITICAL | **HTTP/2** for client connections and a proxy to a backend; a crafted request causes a use-after-free when the worker is short of connections | **Closed by configuration:** same, HTTP/2 off |
| CVE-2026-113425 | HIGH | nginx configured as a **mail proxy** | **Not configured:** we have no `mail` block |
| CVE-2026-118899 | HIGH | `listen ... ssl` with **TLS 1.3** enabled; a crafted TLS connection makes a worker loop forever while it reads a request body (a denial of service) | **Closed by configuration:** only TLS 1.2 is offered (`ssl_protocols TLSv1.2;` in the http block and in both server blocks; Cloudflare negotiates down) |
| CVE-2026-123634 | HIGH | a proxy whose target name holds variables, together with **SSI or mirror** subrequests that resolve the same name | **Not configured:** our proxy targets are fixed upstreams, and we use neither SSI nor `mirror` |

Cost of the two switches: Cloudflare opens somewhat more connections to our server (HTTP/1.1 does not multiplex) and the hop uses TLS 1.2. Players still get HTTP/2 and HTTP/3 from Cloudflare's edge; only the hop to our server changed. This stays until the image is updated, with a deadline of **2026-11-25**; `tools/tests/test_nginx_origin_hardening.py` keeps the configuration as described. When the image is updated: change the digest, run the scan, the integration tests and the restore drill, **remove these identifiers** from the reviewed list, and switch HTTP/2 and TLS 1.3 back on only if they are wanted.

The reviewed identifiers are in [`deploy/compose/accepted-advisories.txt`](../../deploy/compose/accepted-advisories.txt); the CI job `third-party-images` fails on any other fixable HIGH or CRITICAL finding. When upstream publishes a rebuilt image: update the digest, run the scan, the integration tests and the restore drill, and remove the identifiers it fixes.

**2026-11-21:** the scan turned red on the same pinned nginx digest (Alpine 3.24.2) because of one more fixable HIGH advisory, **libxml2 CVE-2026-86144** (libxml2 2.13.9-r2, fixed in 2.13.9-r3). Checked on the pinned image instead of assumed: libxml2 is required by the image's optional `nginx-module-njs` and XSLT filter module (`apk info -r libxml2`); `ldd /usr/sbin/nginx` lists no xml library, so the nginx binary does not link it; and neither our configuration nor the image's has a `load_module` line, so the dynamic modules on disk (`ngx_http_xslt_filter_module.so`, `ngx_http_js_module.so`, ...) are never loaded. Same class as the libxslt entries of 2026-11-14. Accepted with the same deadline as the nginx 1.30.7 advisories, **2026-11-25**: when the image is updated, change the digest, run the scan, the integration tests and the restore drill, and remove these identifiers.

**2026-11-27:** the weekly scan found six more HIGH advisories in the same libxml2 2.13.9-r2 of the pinned nginx digest: **CVE-2026-11979** (fixed in 2.13.9-r5) and **CVE-2026-86138, CVE-2026-86139, CVE-2026-86140, CVE-2026-86142 and CVE-2026-86143** (fixed in 2.13.9-r4). The reasoning of 2026-11-21 was checked again on the pinned image (not assumed): `apk info -r libxml2` lists only `nginx-module-xslt`, `libxslt` and `nginx-module-njs`; `ldd /usr/sbin/nginx` lists no xml library; there is no `load_module` line anywhere in `/etc/nginx`, so `ngx_http_xslt_filter_module.so` and `ngx_http_js_module.so` (on disk, never loaded) cannot run. Accepted for the qualifiers of 2026-11-28 with a new review date, **2026-12-05** (before the finals): change the digest, run the scan, the integration tests and the restore drill, and remove these identifiers. The same scan also found **libpam CVE-2025-6020** in the platform image's Debian layer (libpam0g, libpam-modules, libpam-modules-bin, libpam-runtime 1.5.2-6+deb12u2, fixed in 1.5.2-6+deb12u3): that one is not accepted but fixed, by upgrading the four packages in `docker/ctfd/Dockerfile` (`verify-image.sh` checks the minimum version).

**2026-11-28:** the scan of the platform image found one more fixable HIGH advisory in its Debian layer, **zstd CVE-2026-124851** (information disclosure through uninitialised memory; `libzstd1` 1.5.4+dfsg2-5, fixed in 1.5.4+dfsg2-5+deb12u1). It is not accepted but fixed: `libzstd1` is added to the packages upgraded in `docker/ctfd/Dockerfile`, and `verify-image.sh` checks the minimum version. The CI job `image` had been red on this one finding since the morning of the qualifiers; it was left until the event was over so that no image was rebuilt while players were on the platform.

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
