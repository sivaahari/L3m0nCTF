# SP3 part 3.5: the Guide, the Scoreboard and the bell (implementation plan)

> **For agentic workers:** build test first, one task at a time, with the mutation checks of the verification section. Steps use checkboxes.

**Goal:** the studio's own numbers (what each member brought in, the channel progress, the programme grid, the story meter), the public ranking ("TRP ratings", the same list as the CTFtime feed), and the bell (the public news plus the lines written for the studio), as the endpoints the approved demo already fixes, so that the theme (SP6) can be built against either.

**Architecture:** the new endpoints live in `plugins/l3mon_board` next to the board (`guide.py`, `epg.py`, `scoreboard.py`, and the merged news in `news.py`), because they share its gate, its answer shapes, its test world and its golden scenarios. The ranking function is shared with the CTFtime feed through `l3mon_core` so the two can never disagree. The two HTML fragments (the programme grid and the scoreboard's rows) are made by Jinja templates in the plugin with autoescaping, the same markup as the demo's.

**Tech stack:** CTFd 3.8.8 plugin API, SQLAlchemy 1.4, Flask, Jinja, pytest in the CTFd image; the golden answers come from the approved demo (`private/platform-ui/tools/golden.mjs`).

## Global constraints

- Every user-visible score word is **TRP**. The board JSON never carries a field called `story` (the Guide's `story` block is the meter, as the contract says).
- A programme the viewer may not see is absent: no id, slug, name, category, difficulty or value, in any answer or in a count that would give it away.
- A private line (a void, a restore, a bonus message) is read only by its own studio and the crew; no id, count or version of another studio's lines moves for anyone else.
- Nothing here writes, except that a hint purchase's reply gains two fields.
- Every answer carries `X-Request-Id`; the Guide, the scoreboard and the fragments carry a strong ETag and answer 304; `Cache-Control: private, no-cache`.
- While the scoreboard is frozen nothing here gives the live ranking away (3.4 made the board safe; 3.6 completes the freeze): `place` is `null`, the public rows are CTFd's frozen standings with the solve counts of the freeze, and a studio's own score is live.

## What the contract says (private repo, `platform-ui/docs/api-contract.md`, "Guide", "scoreboard and notifications")

| Endpoint | Answer |
|---|---|
| `GET /api/v1/l3mon/guide` | `{ver, phase, banner, epg_sig, team: {score, place, of, solves, hints_used, instances_live, members: [{id, name, captain, you, solves, trp, pct}], bonus, notes: [{title, content}], by_channel: [{channel, name, sponsor, solved, total}]} or null, story: {reels, reels_needed, on_air} or null}` |
| `GET /api/v1/l3mon/guide/epg` | the programme grid's markup (`text/html`), ETag `"e<epg_sig>"`, empty before the start |
| `GET /api/v1/l3mon/scoreboard` | `{ver, phase, banner, total, shown, rows_sig, me: {name, score, pos, of} or null}` |
| `GET /api/v1/l3mon/scoreboard/rows` | one `<li class="sb-row">` for each studio of the first 100; ETag `"r<rows_sig>"` |
| `GET /api/v1/notifications?since_id=` | `[{id, title, content}]` newer than `since_id`, for this viewer: the public news and the lines written for their own studio, one id sequence with gaps; no dates, no team ids; 400 `invalid`, 401, 403 |
| `POST /api/v1/unlocks` | the stock answer plus `l3mon: {score, cost}` |

## Measured in CTFd 3.8.8 (probe of 2026-10-10, kept as tests in `test_board_ctfd_facts.py`)

| Fact | Consequence |
|---|---|
| `GET /api/v1/notifications` needs no sign-in at all and lists **every** notification with `date`, `team_id`, `user_id`, `html`; `?team_id=` filters it for anybody; `GET /api/v1/notifications/<id>` and `HEAD` are open the same way; the stock page `/notifications` lists them for any signed-in player | A line addressed to one studio is readable by everyone, and dates are public. The platform's own lines never use that column (they are in `l3mon_note`), but the door is closed anyway: list and detail are wrapped for everyone but the crew |
| a bad `since_id` is 400 `{"errors": {"since_id": "value is not a valid integer"}}` | the contract's 400 `invalid` replaces it |
| `get_standings(admin=False)` rows are `account_id, name, score, oauth_id, bracket*`, best first, with no date and no solve count; a studio with no score is not in the list; hidden and banned studios are left out; the freeze is applied | the ranking is CTFd's own, and the solve count is a second small query |
| a hint purchase makes an `Unlocks` row and an `Awards` row named "Hint N", value minus the cost, category `hints`; the unlock reply is `{id, date, target, team_id, type, user_id}` | the Guide's `hints_used` counts unlocks; a bonus is the award with category `bonus` |
| `Solves.user_id` is the member who sent the flag; `Teams.captain_id` names the captain | what each member brought in, and "Earlier solves" for a member who is gone |

## Decisions

1. **One plugin.** The Guide, the scoreboard and the bell go into `l3mon_board` (shared gate, shapes, world and golden scenarios).
2. **The bell's ids are times.** A viewer's list holds CTFd's public notifications, anything CTFd addresses to the viewer's studio or account, and the studio's own private lines (`l3mon_note`). The two tables have separate id counters, so the id a page sees is `milliseconds since the epoch x 1000 + 500 for a private line + the line's place among the viewer's own lines of the same kind written in the same millisecond`: it grows with the time written, never moves when an older line is removed, is unique, stays below 2^53 and, being a count of what the viewer may read and never a row number, tells nothing about the lines written for other studios (the independent review found the first version, which used the row number, leaked that). The id of a line is not CTFd's own; `since_id` compares these ids. `notif_ver` is a hash of the viewer's whole list (ids, titles, texts), so an edit is a change too. A line committed late can land under an id a page already holds; pages count what they have not shown by id. For the crew the list and the numbers are CTFd's own.
3. **The stock routes are wrapped, not replaced:** for the crew `GET /api/v1/notifications` stays CTFd's (they need the raw rows); for everybody else the list is the contract's (401 for a visitor), `HEAD` the same, **anything under `/api/v1/notifications/` answers as a missing id** (CTFd reads the text after the slash as a number leniently), and the stock page `/notifications` and the event stream `/events` are closed (404) until the theme brings its own (SP6).
4. **The ranking is shared.** `l3mon_core.standings.ranked()` turns CTFd's standings into positions once; the CTFtime feed and the scoreboard both use it, so "the scoreboard equals the CTFtime feed row for row" holds by construction and by a test that reads both endpoints.
5. **Solve counts are counted solves**: a studio's `Solves` rows, before the freeze while frozen (as the standings are), including a programme the crew has pulled back since (the TRP stays, so the count does).
6. **The Guide counts as the board does**: a member's or the studio's solves are those of programmes the viewer may see and that are on the plan (`visible_challenge_ids`), a member's TRP is the programme's current value, hint costs are in nobody's line, and a solve nobody can be named for (the member left the studio or was banned) is one line "Earlier solves" with id 0.
7. **The story meter** has two settings: `l3mon_story_meter` (default on; off makes `story` null) and `l3mon_story_air_target` (default 0: `on_air` is 0 until the crew sets a target). `reels_needed` is a third of the programmes on the plan, at least 1, and 0 before the start.
8. **The "coming up" count** follows `l3mon_show_coming_count` in the grid's markup too: off, the row does not say how many are still to come.
9. **The fragments are rendered with Jinja autoescaping**; a name with markup is shown as text.
10. **The unlock reply** gains `l3mon: {score, cost}` (the studio's live score after the purchase and what the hint cost).

## File structure

| File | Responsibility |
|---|---|
| `plugins/l3mon_core/standings.py` | `ranked(standings)`: positions of the studios with a score above zero (shared by the feed and the scoreboard) |
| `plugins/l3mon_core/settings.py` | `story_meter()`, `story_air_target()` beside `show_coming_count()` |
| `plugins/l3mon_ctftime/__init__.py` | the feed's rows come from `standings.ranked` |
| `plugins/l3mon_board/news.py` | the viewer's merged list, its ids, `state_for` |
| `plugins/l3mon_board/notices.py` | the wrapped stock routes (list, head, detail, page) |
| `plugins/l3mon_board/scoreboard.py` | rows, data, ETag, fragment |
| `plugins/l3mon_board/guide.py` | the studio's block, the members, the story meter, the banner, data, ETag |
| `plugins/l3mon_board/epg.py` | the grid model, its signature and markup |
| `plugins/l3mon_board/templates/l3mon_board/{epg_row,scoreboard_row}.html` | the two fragments |
| `plugins/l3mon_board/replies.py` | the unlock reply's extras |
| `plugins/l3mon_board/api.py` | the four routes |
| tests | `test_notices.py`, `test_scoreboard.py`, `test_guide.py`, `test_epg.py`, `test_unlocks.py`, additions to `test_golden.py`, `test_board_doors.py`, `test_board_ctfd_facts.py`; `tests/integration/test_guide.py`; `tests/browser` is not needed (no page) |
| private repo | `platform-ui/tools/golden.mjs` records the new asks; contract section 12 |

## Tasks

1. **Facts as tests** (`test_board_ctfd_facts.py`): the open notifications routes and what they leak, the 400 text, the standings shape, the unlock rows.
2. **Shared ranking and settings** (`standings.ranked`, the feed uses it; `story_meter`, `story_air_target`).
3. **The bell** (`news.py`, `notices.py`): the merged list and ids; the wrapped routes; the doors (a visitor, another studio's line, a team-addressed notification, the detail route, HEAD, the page); `notif_id` and `notif_ver` in the board and the tick; `since_id`.
4. **The scoreboard** (`scoreboard.py`, the template): rows equal the feed; `me`; frozen; before the start; the top 100 and the note; names as text; ETags and 304.
5. **The Guide** (`guide.py`, `epg.py`, the template): the studio's block, members, notes, bonus, hints, channels, story, banner, the grid, `epg_sig`; frozen; before the start; crew (`team: null`).
6. **The unlock reply** (`replies.py`).
7. **Golden scenarios**: the demo's tool records the Guide, the scoreboard, the grid, the rows and the bell for every step of the scenario; the real platform must give the same after ids become names.
8. **Doors and the numbers**: a scan of every new route as the studio, another studio, a visitor and the crew for what must never appear; the query count does not grow with the programmes.
9. **Mutation checks, the independent review, the stack run, the records** (contract section 12, `docs/deploy/local.md`, PROGRESS, the verification log).

## What stays for later parts

The full freeze (3.6: the held tick, values standing still, the studio's own later solves in every count), instances (`hooks.instance_summary` fills `instances_live`, SP4), the pages themselves (SP6), and the real `/notifications` page.

## What changed while building (2026-10-10)

The plan was followed task by task, test first. These things were found by doing the work and are now part of the design:

1. **Python rounds a half to the even number, JavaScript rounds it up.** A bar of 50 of 400 TRP is 12.5 percent: the approved demo draws 13, Python's `round` gives 12. `figures.js_round` does what the demo does, and a test pins 12.5 to 13.
2. **The old scoring "doors" test assumed no route ever shows a studio its own private lines.** That was true until this part; the bell and the Guide exist to show them. The test now proves the studio reads them there and nobody else does (another studio, a visitor, the crew's lists), with a control, and the matching integration tests changed the same way.
3. **The golden scenarios found no defect this time, only four differences that are by design**, now written down in the compared form: the crew's wording of "Solve voided" and "Solve restored" (the real platform sends the crew's reason, not the demo's longer sentences), the release control's public "New on air" lines (one for each change the crew sends, channels numbered by position), the numbering of the bell, and the place of a studio whose TRP is zero or below (none, on the Guide, the board and the scoreboard alike).
4. **CTFd's standings are cached for a minute** (`get_team_standings`), and the tests that count database queries counted CTFd's own queries until the cache was warmed first.
5. **The scoreboard uses `get_standings` (live), not the cached function**, so it equals the CTFtime feed by construction. The feed was also made empty before the start, as the scoreboard is.
6. **`phase.state` is one word** (before, live, paused, ended), so "the end beats a pause" is no rule at all; only the freeze is a flag. Two of my own deliberate breaks turned out to change nothing and were dropped.
7. **Mutation checks** (123 deliberate breaks, one at a time): 116 were caught at once; seven were not and each told something: three tests were too weak (the signature ignoring a solve that is worth nothing, a bar for a tiny share, a hint instance that is only starting), two of my breaks were wrong (an anchor that did not match; dead code before a return) and are fixed and caught now, and two change nothing (a status check that the shape check already makes redundant, and a defensive commit that CTFd's own view has already made).

## The independent review (2026-10-10)

An independent reviewer (Opus, cold read of a frozen copy, read-only, measured findings, SQLite in the image and a throwaway MariaDB 10.11) went through the code: **1 high, 2 medium, 3 low, 6 notes.** Every finding that could be fixed was fixed test first; each fix was then broken on purpose to see that a test noticed (all caught).

| Finding | What it was | What was done |
|---|---|---|
| **H1, high** | The guard for one notification matched only digits, but CTFd reads the text after the slash leniently: `/api/v1/notifications/+1`, `/1.0`, `/1e0`, with a space around the number and, on MariaDB, even `/1abc` returned the whole row (content, studio, date) to a visitor and to another studio. | **Everything under `/api/v1/notifications/` is refused** as an id that never existed (401 for a visitor first). Thirteen spellings are tested for a visitor, a studio and another studio, and the integration test asks through nginx. |
| **M1, medium** | The bell's ids carried `row id % 500`; both tables count their rows across all studios, so a studio could count how many private lines (for example a Revoke's) were written for the others in between. | The tie-break is the line's place among the viewer's own lines of the same kind in the same millisecond, never a row number. Tests: alone in its millisecond, the id has only the private mark; within one millisecond the places count the viewer's own lines. |
| **M2, medium** | `/events` (server-sent events) pushes each notification made through the API, an addressed one included, to every signed-in account. | `/events` is 404 for everyone but the crew (the stock theme used it; the new theme polls the tick). Found by reading, not run live. |
| L1, low | A line can get an id under one a page already holds (the id is the time written, not committed; a public line sorts under a private one in the same millisecond). | **Accepted by design and written down**: the docstring no longer claims otherwise, and the contract tells the pages to count what they have not shown by id, not only by the highest id seen (SP6). |
| L2, low | The crew's `notif_id` was time-based while the crew's list is CTFd's, so the crew's `since_id` never worked. | The crew's numbers are CTFd's own; tested. |
| L3, low | The guard's refusals had no envelope and no request id. | They are the envelope with a request id (the stock page and `/events` stay CTFd's plain 404). |
| N1 | The id says when the line was written. | Said plainly in the contract and the docstring; not a secret. |
| N2 | A bonus is the awards of category `bonus`; MariaDB ignores case there. | Accepted: a hand-made award with that category counts. |
| N3 | The "coming up" switch hides only the grid's words; totals still give the count. | The owner's open decision of part 3.4. |
| N4 | Before the start the scoreboard was empty but the CTFtime feed listed a bonus given beforehand. | Fixed: the feed is empty before the start too; tested. |
| N5, N6 | Dynamic values still move the frozen rows (part 3.6); `instances_live` asks the hook for each live programme (SP4 should answer in one query). | Recorded for those parts. |

The reviewer also checked and found right: query counts flat from 3 to 150 studios (scoreboard 5, rows 5, Guide 16, grid 10, tick 8, bell 6), names with markup, right-to-left marks and emoji rendered as text, the frozen scoreboard on MariaDB, Decimal sums, and the trailing-slash forms of the list. **Not checked by the reviewer**: gevent concurrency, anything through nginx, the value decay while frozen, a member moved between studios by an admin edit.

