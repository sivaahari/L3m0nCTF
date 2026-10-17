# Go-live progress log

The running record of the push to put the platform online and register the event on CTFtime. **Keep it current**: after every finished step add one line to section 3. It holds no secret and must never hold one (no password, key, token, webhook address, card number, recovery code or CTFtime secret). A person or an AI assistant taking over reads this file first.

Last updated: 2026-10-18 04:15 IST by the lead engineer (an AI assistant). **Update this line when you edit the file.**

## 1. The two goals and their deadlines

| Goal | Deadline | State |
|---|---|---|
| File the event on CTFtime (a form; it needs **no** server) | **2026-10-18 12:00 IST** | **Not yet filed.** The sheet is `01-CTFTIME-REGISTRATION-SHEET.md` (owner's desktop folder `go-live`) |
| Platform online at `https://play.l3m0nctf.xyz` with the two CTFtime addresses working | as early as possible (CTFtime reads them after it approves the event) | **Not started.** The guide is `docs/deploy/go-live-runbook.md` |

## 2. What is done, and what is not

| Item | State | Notes |
|---|---|---|
| The three teammate branches (`feat/instancer`, `feat/identity`, `feat/operations`) audited and merged | **Done** on the branch `integration/2026-10-18` | No merge conflicts. The audit is `docs/deploy/audit-2026-10-18.md` |
| Defects found by the audit fixed (test first) | **Done** for the small ones | The larger ones are in two follow-up prompts for the teammates |
| Whole test suite on the rebuilt merged images | **Done**: integration suite 145 passed, 1 skipped, 0 failed | Section 4 has every layer |
| The merged code published as `pre-deployment` and tagged `release-2026-10-18` | **Done** (04:10 IST): `pre-deployment` and the tag point at commit `79fa972` | The VM downloads this tag. Check: `git ls-remote --tags https://github.com/sivaahari/L3m0nCTF.git release-2026-10-18` prints two lines. GitHub's own automated check (CI) was still running when this was written; its "third-party images" job has been red for days for a known reason (finding F-022) |
| Landing page redeployed with the cartoon pictures | **Not done** (owner's `npx wrangler deploy`) | Does not block CTFtime. The live page at 02:10 still showed the older version |
| Hosting: Google Cloud project, network, firewall, VM | Not started | Part 2 of the runbook |
| Hosting: Cloudflare certificates, DNS, rules, Access | Not started | Part 3. **Open question: who holds the Cloudflare login for `l3m0nctf.xyz`?** |
| The platform on the VM, checks from outside | Not started | Parts 4 to 6 |
| Login with CTFtime switched on | Waits for CTFtime's approval | Part 7 |
| Optional pieces on the VM (`identity`, `ops` overlays) | Later this week | Part 8. The `instancer` overlay stays **off** (development mode, unfinished router) |
| Theme (the new look of the pages) | The owner's own work, not part of this release | The platform shows CTFd's own pages until it lands |

## 3. Log of what was done (add one line per step; newest at the bottom)

| Date and time (IST) | Who | Step | Result |
|---|---|---|---|
| 2026-10-18 01:56 | assistant | Fetched the three teammate branches | all based on the same commit; no folder conflicts |
| 2026-10-18 02:00 to 02:30 | assistant | Merged, built, read the security-critical code, fixed defects test first | see the audit report |
| 2026-10-18 02:31 | assistant | Whole integration suite on the three-overlay stack | 143 passed, 1 skipped (5 failures in the first run were caused by running the suite with overlays set or by a restart in progress; each passed when run in its proper conditions) |
| 2026-10-18 02:36 | assistant | Rehearsed Part 5 of the runbook in a fresh clone with throwaway certificates | `deploy check`: 0 blocked, 2 to do (CTFtime, mail), 8 in place. Found and fixed one tool bug on the way (`deploy check` looked in the wrong folder) |
| 2026-10-18 03:25 to 04:05 | assistant | Final full integration run, triage of two runs with failures | Found and fixed: the load test's allow-lists disagreed (the real smoke run was refused); a test recreated `ctfd` without the `ops` overlay (monitoring got errors 500); monitoring tests checked once; production-compose tests depended on the shell. Final run: **145 passed, 1 skipped, 0 failed**. Runbook Part 8 now says to load `production.env` in every new SSH session |
| 2026-10-18 04:10 | assistant | Pushed `integration/2026-10-18` to `pre-deployment` (fast-forward, 32 commits) and created the tag `release-2026-10-18` | both at `79fa972`; hygiene scan 0 findings |
| | | | |

## 4. Final numbers of the verification (filled in when the run ends)

All run on images rebuilt from the final code, on a stack started with `identity,instancer,ops` (18 October 2026, 04:05 IST):

| Layer | Result |
|---|---|
| Image hardening checks (`tools/verify-image.sh`) | 32 of 32 ok |
| Hygiene scan of the files and the whole history | 0 findings |
| Command-line tools tests | 299 passed, 12 skipped |
| Instancer service unit tests | 88 passed |
| Plugin suite inside the image (all ten plugins) | 1,774 passed, 56 skipped; the four new plugins on the final image: 89 passed, 14 skipped |
| CTFd's own tests with our plugins loaded | 676 passed |
| Real MariaDB and Redis tests | 69 passed (existing) + 14 passed (new plugins) |
| **Whole integration suite on the three-overlay stack** | **145 passed, 1 skipped, 0 failed** (9 min 14 s) |

The restore drill, the operations drills, the load test at twice the target, the real backup bucket, the real mail provider and real Cloudflare and Google are **not** done (they need the server or destroy data). See `docs/deploy/audit-2026-10-18.md`, section 3.

## 5. Decisions already taken (do not reopen without the owner)

| Decision | Value |
|---|---|
| Hosting | Google Cloud Compute Engine, `asia-south1-a`, `e2-standard-4` now and `e2-standard-8` for the load test and the event; the free-trial credit, upgraded to a paid account so nothing stops mid-round |
| Edge | Cloudflare: Full (strict), Authenticated Origin Pulls with our own client-certificate authority (switched on **before** the first visitor), cache only `/themes/`, Access on the admin host |
| Day-one stack | `L3MON_ENV=production`, no overlays. `identity` and `ops` later; **never `instancer` on a server yet** |
| Admin access | `/admin` only from addresses in `/etc/l3mon/admin-nets.conf` (explicit, no private ranges), and on `crew.l3m0nctf.xyz` behind Cloudflare Access |
| Release | Deploy the tag `release-2026-10-18`, never a moving branch |

## 6. Open questions for the owner

1. Who holds the Cloudflare login for `l3m0nctf.xyz`?
2. Does the Google account already have a billing account, and does its owner agree to upgrade it (decision D10)?
3. Which public IP addresses should be allowed to open `/admin`?
4. Which mail service (the relay needs its host, port, login and a daily limit that matches the plan)?
5. Which age **public** key encrypts the backups (the private half must never be on the server)?
6. The campus networks (for `L3MON_GUARD_CAMPUS_NETS`).
