# What to give CTFtime: the addresses and values for the event listing

This is the one page for the people who register L3m0nCTF 2026 on CTFtime. It lists every value the form and the event's edit page ask for, which of them we can give today, and what is still missing. The reasoning behind each value is in [CTFtime OAuth and the live JSON feed](research/ctftime-oauth-and-live-feed.md) and [Hosting on CTFtime](research/ctftime-requirements.md).

The event's domain is **`l3m0nctf.xyz`** (given on 2026-10-06). The public landing page lives at `https://l3m0nctf.xyz/` and the platform (sign-in, board, scoreboard, feeds) at `https://play.l3m0nctf.xyz/`. Keeping the platform on its own subdomain keeps its sign-in cookies away from the landing page. If you would rather run the platform on the main domain, only the three addresses in section 2 change.

## 1. The event listing form (`ctftime.org/event/mail/`)

| CTFtime asks for | Value | Status |
|------------------|-------|--------|
| Name | L3m0nCTF 2026 | ready |
| Official website | `https://l3m0nctf.xyz/` (the public landing page) | domain known, the page must be live before filing |
| Format | Jeopardy | ready |
| Dates | **28 November 2026, 10:00 IST (04:30 UTC) to 22:00 IST (16:30 UTC), 12 hours, online** (changed on 2026-10-10; the first plan was 24 hours from 09:00 IST) | ready |
| Location | On-line for the online round. The finals are on campus in Coimbatore for selected teams | ready; finals dates are still to be announced |
| Organiser team | A TIFAC-CORE team on CTFtime | needs a team member to create or pick it (decision D11) |
| Logo | `https://l3m0nctf.xyz/icon-512.png` | ready once the page is live |
| Prizes, restrictions, contact | The organisers' decision | not decided yet |
| Description | The paragraph in the landing repo's submission pack | ready (needs the registration and rules links) |

## 2. The two addresses the platform must provide

These are what CTFtime asks the organisers for once the event is approved. They are the "endpoints" for sign-in and for the live scores.

| Purpose | Address | What it is |
|---------|---------|------------|
| **Login with CTFtime: the OAuth endpoint (callback URL)** | `https://play.l3m0nctf.xyz/auth/ctftime/callback` | Where CTFtime sends a player back after they approve the sign-in. It is typed into the event's edit page, and it must match exactly (same scheme, domain, path, no trailing slash). CTFtime shows the **client ID** (it is the CTFtime event number) and the **client secret** on that same page once the event is approved; they go to the platform team, never into a document or chat. |
| **Live scoreboard feed** | `https://play.l3m0nctf.xyz/ctftime/standings.json` | A public address, no login. CTFtime reads it every 60 seconds. The platform answers from the same standings as the public scoreboard and lets caches keep the answer for 15 seconds. |
| Final results (after the 12 hours) | `https://play.l3m0nctf.xyz/ctftime/final-standings.json` | The same format, taken once the scoreboard is unfrozen. It is uploaded to CTFtime by form. **This is the part that gives teams their rating points,** so it matters more than the live feed. |

### What the live feed looks like

The smallest format CTFtime documents, on purpose: CTFtime warns against bigger ones until it announces them.

```json
{
  "standings": [
    { "pos": 1, "team": "Team name exactly as registered", "score": 4200 },
    { "pos": 2, "team": "Another team", "score": 3900 }
  ]
}
```

Rules we follow: only teams with a score above zero, no hidden, banned or staff teams, positions and ties exactly as the public scoreboard, the frozen standings while the scoreboard is frozen, correct escaping of every character in team names, and an alarm if the file is more than two minutes old.

## 3. What CTFtime needs from the server side

- **A fixed outgoing IP address** for the platform. CTFtime sits behind a firewall that has blocked sign-in token requests from unknown servers before; the address may need to be allow-listed on their side. We send it to CTFtime once the hosting is decided.
- **Team names must match exactly** between our platform and CTFtime, or results cannot be matched. Login with CTFtime avoids the problem for teams that use it.
- **Sign-in only works while the event is upcoming or running on CTFtime**, so it can only be tested for real after approval. We test against a stand-in CTFtime until then.

## 4. Where each address stands today

| Item | Today |
|------|-------|
| The three addresses and the feed format | **Decided, as above** |
| The live feed and the final results file in the real platform | **Built and tested** (plugin `l3mon_ctftime`, public repo): the exact format above, the platform's own standings and freeze, no cookie, a 15-second shared cache. The final file is served only after the event has ended **and** an organiser publishes it (after cheating cases are settled): `PATCH /api/v1/configs` with `{"l3mon_final_standings_published": true}`. 11 tests inside CTFd, every rule broken once on purpose to prove the tests notice, and an integration test through nginx |
| The sign-in callback in the real platform | **Built and tested against a stand-in CTFtime** (plugin `l3mon_ctftime`, public repo, 2026-10-10): `GET /auth/ctftime` and `GET /auth/ctftime/callback`, off until the event number and the secret are set (section 6). It has **not** been tried against the real CTFtime: that needs the approved event |
| The real platform that serves them | **The production front door is built and was tried for real on 2026-10-17** (`deploy/compose/compose.production.yml`, `deploy/nginx/templates-production/`): a whole production-mode stack was started with throwaway certificates and answered over HTTPS exactly as it must (the live feed, no cookie; `__Host-session` with `Secure`; a request without Cloudflare's certificate refused; the sign-in address redirecting to CTFtime with the callback above). **Not deployed anywhere public yet:** it needs a server, the DNS name `play.l3m0nctf.xyz`, Cloudflare in front and a few files that only the machine's owner can make (section 7) |
| The domain | **Known: `l3m0nctf.xyz`.** Still needed: DNS control (who can add the `play` name and the records for the landing page) and, later, the fixed IP of the server |
| Start and finish time of the online round | **Known: 10:00 IST to 22:00 IST on 28 November 2026** |
| A CTFtime organiser account and team | **Needed** (decision D11) |

## 5. Order of work once the domain is known

(Section 7 is the same list with the exact commands, and says which steps are the computer's and which are a person's.)

1. Publish the landing page at `https://l3m0nctf.xyz/` (the domain and the times are already in its settings). This is the "official website".
2. File the event on CTFtime with the values in section 1. Approval is manual and its lead time is not published, so file the day the page is live.
3. After approval: type the callback address and the feed address into the event's edit page, copy the client secret to the platform team, and send CTFtime the server's fixed IP address.
4. Test Login with CTFtime for real, and watch the feed from the outside for a day.
5. After the event: unfreeze, take the final file, and upload it.

## 6. Login with CTFtime: what it does, and how to switch it on

**What a player sees.** A button (the page that has it is part of the platform's pages) sends the player to `https://play.l3m0nctf.xyz/auth/ctftime`, which sends them to CTFtime to approve and to choose which of their CTFtime teams they play for. CTFtime sends them back to the callback address, and they are signed in.

**What the platform does with the answer** (the whole flow is in the header of `plugins/l3mon_ctftime/oauth.py`):

- The account is found by the **CTFtime user number**, never by email. A new account needs an email address that no other account uses, and it starts **unverified**: CTFtime's email may come from an unverified social account, so the platform's own email check still applies before the player can use the board.
- In team mode the **first** player of a CTFtime team to sign in gets a **studio** made for the team, with that player as captain and the team's name kept exactly (CTFtime's feed finds a team by its name). **Nobody is ever put into a studio that already exists**: a CTFtime team's list of members is not the event's roster (it includes former members), so a later player of the same team gets an account and no studio and joins with the captain's invite, like anyone else. A studio is made only for a new account (a member the crew removed does not come back by signing in again), and not when the crew has closed the making of studios, when another studio already has the name, or at the limit of studios. Nobody can join a studio made this way by name and password: its password is a secret nobody is told.
- A CTFtime sign-in **never opens an administrator's account or a suspended one**, and never makes an account that normal registration would refuse (registration closed, a registration code set, the email allow-list or block-list, the limit of accounts reached). Names that draw nothing (blank fillers, invisible characters, no letter or digit at all) are replaced by `ctftime-<number>`.
- **Watch for two things while the event is open.** Anyone who is on the CTFtime team can be the first to sign in and so become captain (the crew can fix a captain; a player cannot), and anyone can register a studio under a CTFtime team's exact name before the real team signs in (the log says so, with the CTFtime team number and name). Both are the same exposure that normal registration has, and both need the crew to look. Someone can also put another person's email address on a CTFtime profile: that makes an unverified account holding the address, which the crew clears on request.
- If CTFtime, or the Cloudflare in front of it, answers with an error, a timeout or anything unexpected, no account is made, nobody is signed in, the player is told to use the normal sign-in, and the log says which step failed (never the code, the token or the secret).
- 120 starts and 120 callbacks a minute per address (a HEAD request counts like a GET), so a campus can share one address; the database connection is let go before CTFtime is called, so a slow CTFtime cannot hold up flag submissions. (nginx's sign-in limit counts only POSTs, so it does not apply to these two GET routes; its general limit does.) CTFd's own OAuth routes `/oauth` and `/redirect`, which find accounts by email and share the same columns, are closed in nginx.

**To switch it on, once CTFtime has approved the event:**

1. In `config/event.toml` set the event number CTFtime shows as the client ID, in quotes, then render the settings again:
   ```
   [ctftime]
   client_id = "1234"
   ```
   `python -m l3mon config render ../config/event.toml --out ../deploy/compose/generated`
2. Put the **client secret** CTFtime shows in `.secrets/CTFTIME_CLIENT_SECRET` (the secret and nothing else; readable by the CTFd user inside the container, so mode 0644 like the other secret files, or the routes stay off and the log says "no CTFtime secret can be read" once at start) and add it as a Docker secret in the production override (in production the secret is a file, never an environment variable; the variable `CTFTIME_CLIENT_SECRET` exists for development and tests):
   ```
   secrets:
     CTFTIME_CLIENT_SECRET:
       file: ../../.secrets/CTFTIME_CLIENT_SECRET
   services:
     ctfd:
       secrets: [CTFTIME_CLIENT_SECRET]
   ```
3. Recreate the platform container (`tools/compose.sh up -d --wait --force-recreate --no-deps ctfd`). Until both the number and the secret are there, both routes answer 404, so nothing is visible and nothing can be probed.
4. Check from outside: `curl -sI https://play.l3m0nctf.xyz/auth/ctftime` answers 302 with a `Location` at `oauth.ctftime.org/authorize` that carries the callback address above, the scopes `profile:read team:read` and a 32-character `state`.
5. Sign in with a real CTFtime account (the event must still be upcoming or running on CTFtime). If CTFtime answers the token request with a 403, send CTFtime the server's fixed IP address (section 3).

**What is proved, and what is not.** Proved with a stand-in that speaks the documented answers and fails the ways CTFtime has failed for others: the new player, the teammate, the full studio, the name clash, a taken email, an administrator, a suspended account, closed registration, every malformed profile, a wrong or old or reused state, a cancelled approval, a 403 from Cloudflare, an error, a timeout, a redirect, an unusable token, two callbacks at once, a registration code, the email lists, names that draw nothing, and a database connection held while CTFtime is asked. **Not proved:** what the real CTFtime returns in each field (especially whether `email` and `team` arrive with these scopes), and that it lists only teams the person belongs to. The first real sign-in is the test, and the plugin refuses what it does not understand rather than guessing.

## 7. What to do, in order: registering the event, then putting the two addresses online

### 7a. Registering the event on CTFtime (a person does this; nothing here waits for the server)

The two addresses of section 2 are **not** asked for on the form. CTFtime asks for them on the event's own page after it has approved the event. So the event can be filed today.

1. **Sign in at <https://ctftime.org>** with Google, GitHub, Twitter or Facebook (there is no password account). Use the account of the person who will be the contact for the event: CTFtime's replies go to it.
2. **Be a member of an organiser team.** If an Amrita team already exists on CTFtime (search "Amrita" under Teams; the college's earlier event, XiomaraCTF, is listed there), ask its captain to add you. If not, make one at <https://ctftime.org/team/new/>: a team name (suggestion: `TIFAC-CORE Amrita`), the country (India), the website `https://l3m0nctf.xyz/`. The person who creates it is its captain.
3. **Open the form <https://ctftime.org/event/mail/>** (the "Post info about your CTF" form on <https://ctftime.org/for-organizers/>) and enter exactly the values of section 1. Copy them from this page; the English description is in the landing repository's submission pack, and the times must be **in UTC**:
   - Name: `L3m0nCTF 2026`
   - Official website: `https://l3m0nctf.xyz/` (live since 2026-10-08)
   - Logo: `https://l3m0nctf.xyz/icon-512.png`
   - Start: `2026-11-28 04:30 UTC` (10:00 IST). Finish: `2026-11-28 16:30 UTC` (22:00 IST)
   - Format: Jeopardy. Online. Restrictions: open to everyone, teams of 1 to 4
   - Organiser team: the one from step 2, exactly as it is spelled on CTFtime
   - Prizes and description: the prize text the organisers decide (or "to be announced"), then the description
4. **Press send.** The form is an email to CTFtime's moderators; approval is by hand and CTFtime does not publish how long it takes. Keep the confirmation email. Ask in the same message (or at <https://ctftime.org/feedback>) the questions of `docs/research/ctftime-requirements.md`, section 10: how long approval takes, and whether the live feed is supported today.
5. **When CTFtime approves**, the team gets a **management page** (<https://ctftime.org> -> Manage CTFs -> Existing CTF events -> the event). On it:
   1. In the OAuth setting, type the callback address `https://play.l3m0nctf.xyz/auth/ctftime/callback` exactly (same scheme, domain and path, no slash at the end).
   2. In the scoreboard-feed setting, type `https://play.l3m0nctf.xyz/ctftime/standings.json`.
   3. Copy the **client ID** (the event number) and the **client secret** and hand them to the platform team **privately**: not in a document, an email thread or a chat. The platform team puts the number in `config/event.toml` and the secret in `.secrets/CTFTIME_CLIENT_SECRET` (section 6).
   4. Send CTFtime the server's fixed outgoing IP address if its sign-in request is blocked (section 3).
6. **After the event:** once the crew has published the final file, upload the results at *Manage CTFs -> Existing CTF events -> Manage results* (the file is `https://play.l3m0nctf.xyz/ctftime/final-standings.json`). **Without it, teams get no rating points.**

### 7b. Putting the two addresses online (the platform team; the earliest useful date is the week the event is approved, the latest is 7 November)

CTFtime reads the feed once the event is approved, and the sign-in can only be tried for real after approval. What must exist by then, in this order:

| # | Step | Who | Where it is written |
|---|------|-----|---------------------|
| 1 | The money decision and the Google Cloud account (decision D10) | superiors | `docs/deploy/platform-deployment.md`, section 2 and Part B |
| 2 | Cloudflare controls `l3m0nctf.xyz` (the landing page already runs there) and the record `play` points at the server | owner | the same guide, A2 and C5 |
| 3 | The VM with a fixed address, the firewall that lets in only Cloudflare, and the origin certificate and client-certificate authority in `/etc/l3mon/tls` | owner | Parts B and C |
| 4 | On the VM: the code at a release tag, `secrets generate`, `config render`, the shell settings file, the image | platform team | Part D, D1 to D5 |
| 5 | `python -m l3mon deploy check` ends with `0 blocked`, then `tools/compose.sh up -d --wait` | platform team | D6 (this is the computer's check of the list above) |
| 6 | Check from outside: the feed answers, the sign-in address is a 404 until the secret is set, the admin pages are refused | platform team | D8 and section 6, step 4 |
| 7 | After approval: the event number and the secret, recreate the platform container, try the sign-in with a real CTFtime account | platform team | section 6 |

The mail service (guide A3) is **not** needed for CTFtime to read the feed or for the sign-in route to exist; it is needed before players can register, because every account is verified by email.
