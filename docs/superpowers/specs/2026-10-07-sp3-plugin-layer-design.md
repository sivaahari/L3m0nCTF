# SP3: the plugin layer on CTFd (design)

Status: **draft for the owner's review, 2026-10-07.** It replaces the narrower SP3 in the [build design](2026-10-04-platform-build-design.md) (section 6), which planned a snapshot worker and a realtime gateway. Section 1 is for the owner and the superiors. Sections 2 to 8 are for the builder. No event material, flags or secrets are in this document.

## 1. In one page

**What SP3 is.** The demo you reviewed runs on invented teams in memory. SP3 builds the real thing inside CTFd: the board, the Guide, the Scoreboard (TRP ratings), the notifications and the crew's release control, from real teams, real solves and real scoring. The approved pages (SP6) then sit on top of it and look the same as in the demo.

**What the superiors' answers change**

| Answer | What SP3 does |
|---|---|
| Most challenges dynamic, one or two fixed | Every challenge is one or the other. A dynamic challenge's TRP falls as more studios solve it, down to a floor. Everyone who solved it ends with the same, current value (CTFd works this way and we keep it) |
| Hint costs differ per hint | Each hint carries its own cost. CTFd already supports this |
| Scoreboard freeze: undecided | A switch, **off** until the core decides. Built last, so a "no freeze" decision costs nothing |
| Ties: whoever got there first | CTFd's own rule, kept: the studio whose score last changed earlier ranks higher. The website and the CTFtime feed use the same list, so they cannot disagree |
| Broken challenge | **Revoke:** the solves are set aside (never deleted), their TRP comes off every studio and member, the challenge's value is recalculated, and the record stays (who, when, what they sent) so you can see how it was solved. **Restore** undoes it. Or leave the challenge live. Hiding a challenge alone does not change anyone's TRP |
| Award with a message | An admin gives a studio, or one member, TRP with a message ("bug found"). It counts in the ranking at once |
| Teams of 1 to 4, open to all | CTFd's own team-size limit, set to 4. No new code |
| Two or three admins | CTFd's admin role. No new code |
| Sponsored challenges | One **Sponsored Break** channel. Each programme says "Sponsored by". Counts in the main ranking (option A) |

**Five things I found in CTFd 3.8.8 that the design closes**

1. A studio's public solve list shows the name, category and value of **every challenge it solved, including ones you have held back.**
2. The public list of a studio's awards shows each award's name and message. A bonus note like "found a bug in X" would be public.
3. A notification can be addressed to one studio, but the list is not filtered by studio, so anyone could read it.
4. After CTFd marks a solve wrong, it does **not** recalculate a dynamic challenge's value. Our Revoke does.
5. The hint error says "points", not TRP. Buying a hint, or getting a bonus, counts as a change of score for the tie-break.

**What gets built, in order** (each part is tested before the next starts)

| Part | Delivers | Done when |
|---|---|---|
| 3.0 Demo update | The four visible additions go into the demo first, so you approve how they look: the Sponsored Break channel, a "now 340 TRP (started at 500)" line, a private Bonus line and voided-solve note in the Guide and bell | You have looked at them |
| 3.1 Foundation | Shared parts: the clock (09:00 IST is 03:30 UTC), who counts, what players may see, the tick counter, the new database tables | The tests pass and CTFd's own 676 tests still pass with our plugins |
| 3.2 Release control | Channels and programmes, released / withheld / scheduled, the scheduler, the crew's page, "New on air", and every door that could leak a withheld challenge closed | One test per door; each test fails when its guard is removed |
| 3.3 Scoring | Dynamic and fixed, recalculation, Revoke and Restore, Bonus, private notes, TRP wording, an audit log of every admin action | Scenario tests for decay, bans, revoke, restore, bonus, ties |
| 3.4 Board and ticks | The board data, the live tick, the extra fields on the programme panel and the flag reply | The answers match the demo's, scenario by scenario |
| 3.5 Guide, Scoreboard, notifications | Each member's share, channel progress, the top 100, the bell | Same; the scoreboard equals the CTFtime feed row for row |
| 3.6 Freeze switch | What the demo does while frozen, on a switch | Same tests, with the switch on and off |
| 3.7 Check | A speed check, an independent Opus audit, fixes, the written record | Audit findings fixed with a test each |

Rough size: **15 to 20 working days**, so finished around the end of October if it starts now. The theme (SP6) does not have to wait: the pages follow the contract the demo already fixes, so they can be built against the demo's server while SP3 finishes.

**What I need from you** (defaults apply if you say nothing)

1. When a solve is voided, should the studio be told? *Default: yes, a private line in its Guide with the crew's reason.*
2. Should a bonus message be seen only by that studio and the crew? *Default: yes. Only "Bonus +50" is public, so a message cannot give a challenge away.*
3. Freeze: the core decides. *Default: off.*
4. Show the "12 coming up" count to players? *Default: show.*
5. Starting values for dynamic challenges. *Default: starts at the difficulty value from the author kit (100, 250, 400), falls to 40% of that at the 51st solve. Tune after the rehearsal.*
6. For sponsors, later: the logo files, the wording of "Sponsored by", and who writes each challenge.

**Not in SP3:** the pages and theme (SP6), instances (SP4), anti-abuse (SP5), the story engine (SP7), Google and CTFtime sign-in (SP8), the realtime gateway, and finals mode (SP10).

## 2. What CTFd 3.8.8 does, read from its source

Everything the design relies on, checked in the pinned image (`ghcr.io/ctfd/ctfd:3.8.8`).

| Fact | Consequence |
|---|---|
| `Solves`, `Unlocks` and `Awards` all carry `user_id` and `team_id` | Each member's share needs no schema change |
| A hint unlock stores an `Unlocks` row plus an `Awards` row worth minus the hint's `cost` | Per-hint cost is native, and the spend is in the studio's score |
| A dynamic challenge stores its current `value` on the challenge row; every solver is scored at that value. The value is recalculated only after a solve, counting solves of studios that are not hidden or banned | Retroactive for everyone, as wanted. A ban, a hide or a revoke leaves it stale until we recalculate |
| Marking a submission "incorrect" deletes the `Solves` row and keeps the submission as a `Fails` row | It clears caches but does not recalculate. A `Fails` row also counts against "tries left". CTFd also has a `discard` type that is not counted as an attempt, which is what Revoke uses |
| Standings are ordered by score, then the date of the studio's latest solve **or award**, then the row id | Matches "got there first". A late hint or bonus moves a studio's tie date later |
| `Teams.get_score` sums each member's own solves and awards by `user_id`, while the standings use `team_id` | Every award must have a user, or the two disagree. A team-wide bonus is attached to the captain and kept out of member shares |
| `/api/v1/teams/<id>/solves` and `/awards` are public and are not filtered by challenge state | Leaks 1 and 2 in section 1 |
| `GET /api/v1/notifications` ignores `team_id` | Leak 3. Private notes use our own table |
| The submission lock answers **403** with status `ratelimited`; a paused event answers 403 with status `paused` | Settles the demo's CONFIRM-1. CONFIRM-2 (the ended case) is checked in 3.4 |
| `team_size` and `freeze` are native settings; standings and team lists already honour the freeze | Team size needs no code. The freeze switch only has to cover our own endpoints |
| The nginx layer treats every API write not on a short list as admin-only, from lists generated out of CTFd's source (`python -m l3mon api-rules generate`) | Our admin actions are closed to everyone but the organisers' addresses by default. SP3 adds no player writes, and regenerates the two checked-in lists so the integration test stays green |

## 3. Decisions and the options considered

| # | Question | Options | Choice and why |
|---|---|---|---|
| A | How to split the code | one big plugin; one plugin per job | **Per job:** `l3mon_core` (shared parts), `l3mon_release`, `l3mon_scoring`, `l3mon_board`. Each is tested alone, and a fault in one is easier to find |
| B | Snapshot worker and realtime gateway (the old SP3) | build now; leave out | **Leave out for now.** The approved contract polls a tick every 15 s. Answers are cached in Redis, guarded by a lock so one request recomputes and the rest wait. Fewer parts to fail during the 24 hours. Add the worker only if the speed check misses its target |
| C | How "withheld" works | our own flag; CTFd's `hidden` state | **CTFd's `hidden` state plus guards** on the doors CTFd leaves open, so stock and our endpoints always agree |
| D | A scheduled drop | a background clock; check on each request | **Check on each request** with one conditional database update. The request that wins announces the drop, so it happens once however many workers run |
| E | Revoke | CTFd's "mark incorrect"; delete the solve; our own action | **Our own action.** Sets each solve to `discard`, keeps who, when and what they sent, writes an undo record, recalculates, clears caches, writes the audit line. Restore reverses it exactly |
| F | Bonus | a plain award with the message public; award plus a private note | **Award plus private note.** The award is named "Bonus", so it counts everywhere CTFd counts. The message lives in our table, shown only to that studio and the crew |
| G | Private messages | CTFd notifications; our own table | **Our own table and endpoint.** The bell counts both public and private |
| H | Freeze | always on; always off; switch | **Switch**, off by default, one function used by every query, built last |
| I | Ties | change CTFd's rule; keep it | **Keep it.** The demo's tie rule ignored awards and hint purchases; its documents are corrected to match |
| J | Sponsored channel | mixed in; separate and uncounted; separate and counted | **Separate and counted** (owner's choice). A `sponsored` flag on the channel with a sponsor name and logo, so the board labels it |

## 4. Data

New tables, created by plugin migrations (so they run and roll back with the rest of CTFd):

| Table | Holds |
|---|---|
| `l3mon_channel` | slug, name, accent, picture key, position, kind (`standard` or `sponsored`), sponsor name and logo, release state, release time (UTC) |
| `l3mon_programme` | challenge id, channel, cell, number, slug, release state, release time |
| `l3mon_void` | challenge, team, member, the set-aside submission, when it was solved, when and by whom voided, the reason, when and by whom restored |
| `l3mon_bonus` | award id, team, member, scope (`team` or `member`), the message, who gave it, when |
| `l3mon_note` | studio, title, text, when: a private line for one studio (every void and bonus writes one) |

Settings use CTFd's own `config` table: `l3mon_freeze_enabled` (default off), `l3mon_show_coming_count` (default on). A Redis counter `l3mon:ver` backs the tick; the clients compare it for *difference*, so a restart that resets it is harmless.

The channel and programme data comes from the author kit's `l3mon:` block. SP3 adds an admin-only bulk call (`PUT /api/v1/l3mon/admin/programmes`) for the sync tool and a page to edit it by hand.

## 5. The doors that must stay shut

A withheld programme does not exist for players. One test per door compares the answer with the answer for an id that never existed (request id aside), and **each test is also run with its guard switched off to prove it can fail.**

1. `/api/v1/challenges` (list), `/challenges/<id>`, `/challenges/<id>/solves`, `/challenges/<id>/files`, `/challenges/<id>/hints`
2. `/api/v1/hints/<id>` and `POST /api/v1/unlocks`
3. `/api/v1/teams/<id>/solves`, `/teams/<id>/awards`, `/teams/<id>/fails`, and the same three for users (leaks 1 and 2)
4. The team and user pages (`/teams/<id>`, `/users/<id>`)
5. `/api/v1/scoreboard` and `/scoreboard/top/<n>` (their per-challenge solve lists)
6. Signed file links made while a programme was released
7. Instance routes (SP4 plugs in here) and the flag box: a correct flag for a withheld programme is refused and records nothing
8. Notification text, and the "New on air" lines, which name only counts
9. Our own board, Guide, scoreboard and notification endpoints, which carry only visible programmes, and counts that match what players can see
10. Private notes and bonus messages: another studio's request gets nothing

## 6. How we prove it

| Layer | What it does |
|---|---|
| Unit and integration tests | Run inside the real CTFd image with its test helpers (`tools/run-ctfd-tests.sh`), against MariaDB-compatible rules and Redis |
| Golden scenarios | A neutral made-up world (teams, solves, times, with no event names) is run through the demo and through CTFd. The demo's answers are saved as golden files. CTFd must give the same answers, after ids and times are normalised. Scenarios: before the start, live, paused, ended, each release state, a pull-back, a revoke, a bonus, a tie, a ban, a hide, a frozen board |
| Scoring scenarios | The first N solvers' values against the decay formula; a ban lowers the solve count and the value is recalculated; revoke then restore returns the exact same totals; bonus and hint cost reach the tie-break date; two studios on equal TRP rank by the earlier change |
| Time | The clock is tested at 03:29:59 UTC, 03:30:00, the freeze, and the end, with a fake clock |
| Concurrency | Eight requests at a scheduled drop announce it once; two simultaneous flags for one challenge record one solve; two Revokes of the same challenge void each solve once |
| Switch-off proof | Every security test is run once with its guard removed and must fail |
| Existing suites | CTFd's own 676 tests, our 25 integration tests and the hygiene scan all still pass |
| Speed | On the local stack, relative figures only. Target on staging: board p95 under 150 ms at 400 requests per second, scoreboard within 5 s of a solve |
| Independent audit | An Opus review at the end; findings fixed with a test each |

## 7. Security rules for every endpoint

- A player endpoint reads from the visible set only. No flag, no description, no hint text, no withheld name, slug, number, category, difficulty or value ever leaves the server in a board, Guide or scoreboard answer.
- Admin actions use CTFd's `admins_only`, the CSRF check and the nginx admin-surface rule. Each writes an audit line (who, what, when, why) that no player route can read.
- Every write is one database transaction, repeatable without harm if sent twice, and clears the standings and challenge caches and moves the tick.
- Reasons and messages are text only, length-limited and escaped on output. Nothing is ever rendered as markup from a player's or admin's text.
- No raw SQL built from input. No new player write endpoints.
- A startup check refuses to load the plugins if CTFd's version differs from the pinned one, because several hooks depend on its internals.

## 8. Risks

| Risk | Mitigation |
|---|---|
| A stale dynamic value after a ban, hide, delete or revoke | One `recalculate_dynamic_values()` called after each, and by a periodic check that compares the stored value with the formula and alerts on a mismatch |
| Several web workers act on one drop or one revoke | Conditional updates and locks, with the concurrency tests above |
| A CTFd upgrade changes an internal we hook | The version check, and CTFd's own suite in CI |
| The speed target is missed with no worker | Measure in 3.7. The worker design from the build design is the fallback |
| The freeze decision arrives late | It is the last part and independent of the rest |
| Dynamic values surprise players because TRP moves after they solve | The programme panel says so plainly ("value falls as more studios solve; floor 200"), added to the demo in 3.0 |
