# Production checklist (what must be true before the platform faces the internet)

The development stack ([local guide](local.md)) is safe on one laptop. These are the things that are **not** done by it and
must be decided and tested for the real deployment on Google Cloud behind Cloudflare (sub-project SP9). Most came from the
independent audit of the platform foundation. Each line says how to prove it; a line is done only when its proof has been run
and written in the [verification log](verification-log.md).

## 1. The edge: Cloudflare and the origin

| Requirement | Why | Proof |
|-------------|-----|-------|
| Cloudflare SSL mode **Full (strict)** with an origin certificate, and nginx (or a Cloudflare Tunnel) speaks TLS to Cloudflare | Without it, passwords and session cookies cross the internet between Cloudflare and the server in clear text. Today nginx listens on plain HTTP 8080 only | `curl -I http://<origin-ip>` is refused or redirected; a packet capture on the VM shows no clear-text HTTP from outside |
| **Authenticated Origin Pulls** (a client certificate that only our Cloudflare zone presents), or a Cloudflare Tunnel | Allowing only Cloudflare's address ranges does not prove a request came through *our* zone: any Cloudflare customer can send traffic to our origin through Cloudflare | A request through another Cloudflare account is refused (403/closed) |
| The firewall is a **Google VPC firewall rule** (not `ufw` on the host) | Docker's published ports bypass `ufw` | The origin port answers only from Cloudflare's ranges, tested from a machine outside |
| `X-Forwarded-Proto` and the HSTS header come from Cloudflare's header only (nginx `map` change), once only Cloudflare can reach the origin | Today nginx believes the header from any client | A request with a forged `X-Forwarded-Proto: https` does not get HSTS |
| **Cloudflare cache rule: cache only static theme files** (and, if wanted, the CTFtime feed); bypass the cache for everything else | CTFd's Flask 2.1.3 can omit `Vary: Cookie` (CVE-2023-30861), and Cloudflare ignores `Vary: Cookie` anyway. A cached page with a `Set-Cookie` could hand one visitor's session to another | Two different sessions request `/login` and `/api/v1/challenges`: no `cf-cache-status: HIT` on either; the theme's static files do HIT |

## 2. Who counts as an organiser

| Requirement | Why | Proof |
|-------------|-----|-------|
| `ADMIN_NETS_FILE` points at a file with **explicit organiser addresses and no private range** (start from `deploy/nginx/snippets/admin-nets.production.example.conf`) | The development list allows every private range; behind a tunnel or Docker, *every visitor* can arrive from a private address, which would open the admin area to everyone | `tests/integration/test_hardening.py` (the "outside" tests) pass against the production file; a test already refuses a production example that lists a private range |
| nginx sees the **real client address**: check `$remote_addr` in the access log for a request through Cloudflare | The admin list, the rate limits and CTFd's own records all use it | The access log shows the visitor's address, not a Docker gateway; the forged-header test passes on staging |
| **No `PRESET_ADMIN_TOKEN`** in production (it lives only in `compose.dev.yml`). Organisers use tokens created in CTFd, with an expiry | CTFd accepts that one token from anywhere, forever, until the environment changes | `docker compose config` for the production files has no `PRESET_ADMIN_TOKEN`; the token test from the dev stack fails there |
| The admin area on a **separate host name behind a second factor** (for example Cloudflare Access) | A stolen administrator session cookie is still valid from any address nginx lets through to the admin pages | Without the second factor, `/admin` and the admin API are unreachable even with a valid session |
| `unlimited-nets.conf` lists the campus address(es) for the finals | A hundred players behind one address would otherwise share one set of limits | A load test from one address simulating 100 players is not limited |

## 3. Cookies and sessions

| Requirement | Why | Proof |
|-------------|-----|-------|
| `L3MON_SECURE_COOKIES=true` in the production override | Switches the session cookie to `__Host-session` with `Secure`; nobody sets it yet | A staging test expects `Set-Cookie: __Host-session=...; Secure; HttpOnly; SameSite=Lax` |

## 4. The virtual machine

| Requirement | Why | Proof |
|-------------|-----|-------|
| The VM runs as a **dedicated service account with no roles** | The default Compute Engine account has default scopes, including read access to Cloud Storage in the project | `gcloud compute instances describe` shows the account and no scopes |
| **Containers cannot reach the metadata server** (`169.254.169.254`): a rule in `DOCKER-USER` | Any code execution inside CTFd could otherwise ask for the VM's credentials | `docker exec ctfd python -c "urllib.request.urlopen('http://169.254.169.254', timeout=3)"` fails |
| CTFd has **no general internet access** (only what it needs: the mail relay, CTFtime and Google for sign-in) | Same reason; also stops a compromised app calling out | The egress test from the container reaches only the allowed hosts |
| Secrets: `.secrets/` is owned by a dedicated deploy user, folder 0700, and **copied to a safe place by hand** (the `FLAG_HMAC_SECRET` especially) | Losing it during the round would change every team's flag | A written procedure, and a restore of `.secrets` from the copy on a fresh VM |
| The disk is sized for the logs (rotation is on: 5 files of 50 MB per service) and the clock is synced | A full disk stops the database; flag times depend on the clock | `df` and `chronyc tracking` in the runbook check |

## 5. Backups

| Requirement | Why | Proof |
|-------------|-----|-------|
| Backups are **encrypted before they leave the VM** and stored off-host in a bucket the VM can write to but **not read** | A backup holds every user's email and API token in clear text; a stolen VM must not give access to old backups | An attempt to read the bucket with the VM's account fails; a restore from the encrypted copy works on a fresh VM |
| A scheduled backup and a **restore drill on staging** within the week before the round | A backup nobody has restored is a hope | The drill (`tests/integration/test_restore_drill.py --run-drill`) passes in under 15 minutes on staging |

## 6. Supply chain and checks

| Requirement | Why | Proof |
|-------------|-----|-------|
| Run the whole CI workflow **by hand** before the freeze (**2026-11-21**) and the day before the round | Scheduled runs only start once the workflow is on `main`; new advisories appear without any change | A green run linked in the verification log |
| `HYGIENE_DENY_WORDS` is set as a repository secret (the story words, one per line) | The story-word check cannot run without it, and it says so in the log | The hygiene job shows no "did NOT run" warning |
| Pinned images and actions are refreshed on purpose and re-scanned | Pins stop silent changes but also stop silent fixes | The scan jobs pass with the new digests |
