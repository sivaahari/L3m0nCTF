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
| The real platform that serves them | Runs on a laptop (development stack) and in CI; not deployed. It depends on the hosting and technology decision for the platform (decisions D10, D15, D16) |
| The domain | **Known: `l3m0nctf.xyz`.** Still needed: DNS control (who can add the `play` name and the records for the landing page) and, later, the fixed IP of the server |
| Start and finish time of the online round | **Known: 10:00 IST to 22:00 IST on 28 November 2026** |
| A CTFtime organiser account and team | **Needed** (decision D11) |

## 5. Order of work once the domain is known

1. Publish the landing page at `https://l3m0nctf.xyz/` (the domain and the times are already in its settings). This is the "official website".
2. File the event on CTFtime with the values in section 1. Approval is manual and its lead time is not published, so file the day the page is live.
3. After approval: type the callback address and the feed address into the event's edit page, copy the client secret to the platform team, and send CTFtime the server's fixed IP address.
4. Test Login with CTFtime for real, and watch the feed from the outside for a day.
5. After the event: unfreeze, take the final file, and upload it.

## 6. Login with CTFtime: what it does, and how to switch it on

**What a player sees.** A button (the page that has it is part of the platform's pages) sends the player to `https://play.l3m0nctf.xyz/auth/ctftime`, which sends them to CTFtime to approve and to choose which of their CTFtime teams they play for. CTFtime sends them back to the callback address, and they are signed in.

**What the platform does with the answer** (the whole flow is in the header of `plugins/l3mon_ctftime/oauth.py`):

- The account is found by the **CTFtime user number**, never by email. A new account needs an email address that no other account uses, and it starts **unverified**: CTFtime's email may come from an unverified social account, so the platform's own email check still applies before the player can use the board.
- In team mode the CTFtime team becomes a **studio** with the first player as captain and the team's name kept exactly (CTFtime's feed finds a team by its name). Later players of the same CTFtime team join it while there is room. If another studio already has that name, the studio is full, or the limit of studios is reached, the player gets an account and no studio and sets one up the normal way: nothing is taken over.
- A CTFtime sign-in **never opens an administrator's account or a suspended one**, and never makes an account that normal registration would refuse (registration closed, the limit of accounts reached).
- If CTFtime, or the Cloudflare in front of it, answers with an error, a timeout or anything unexpected, no account is made, nobody is signed in, the player is told to use the normal sign-in, and the log says which step failed (never the code, the token or the secret).
- 120 starts and 120 callbacks a minute per address, so a campus can share one address; nginx adds its usual sign-in limit.

**To switch it on, once CTFtime has approved the event:**

1. In `config/event.toml` set the event number CTFtime shows as the client ID, in quotes, then render the settings again:
   ```
   [ctftime]
   client_id = "1234"
   ```
   `python -m l3mon config render ../config/event.toml --out ../deploy/compose/generated`
2. Put the **client secret** CTFtime shows in `.secrets/CTFTIME_CLIENT_SECRET` (the secret and nothing else; the file is yours, the platform only reads it) and add it as a Docker secret in the production override:
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

**What is proved, and what is not.** Proved with a stand-in that speaks the documented answers and fails the ways CTFtime has failed for others: the new player, the teammate, the full studio, the name clash, a taken email, an administrator, a suspended account, closed registration, every malformed profile, a wrong or old or reused state, a cancelled approval, a 403 from Cloudflare, an error, a timeout, a redirect, an unusable token, and two callbacks at once. **Not proved:** what the real CTFtime returns in each field (especially whether `email` and `team` arrive with these scopes), and that it lists only teams the person belongs to. The first real sign-in is the test, and the plugin refuses what it does not understand rather than guessing.
