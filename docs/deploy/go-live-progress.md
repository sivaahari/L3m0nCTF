# Go-live progress log

The running record of the push to put the platform online and register the event on CTFtime. **Keep it current**: after every finished step add one line to section 3. It holds no secret and must never hold one (no password, key, token, webhook address, card number, recovery code or CTFtime secret). A person or an AI assistant taking over reads this file first.

Last updated: 2026-10-18 02:40 IST by the lead engineer (an AI assistant). **Update this line when you edit the file.**

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
| Whole test suite on the rebuilt merged images | **Running** at the time of writing (see section 4 for the final numbers) | |
| The merged code published as `pre-deployment` and tagged `release-2026-10-18` | **Not yet** | The VM downloads this tag. Do not start Part 4 of the runbook until it exists: `git ls-remote --tags https://github.com/sivaahari/L3m0nCTF.git release-2026-10-18` must print a line |
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
| | | | |

## 4. Final numbers of the verification (filled in when the run ends)

VERIFICATION_PLACEHOLDER

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
