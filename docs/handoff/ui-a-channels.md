# Handoff, stream A: the channels board, a programme's own page, the bell's page (milestone 1)

Branch `ui/a-channels` in both repositories. Written 22 October 2026. Plan: `docs/superpowers/plans/2026-10-22-ui-a-channels.md`. Contract keys: `platform-ui/docs/contract-a-channels.md` (UI repository).
**Milestone 1 covers the board shell, the programme's own page (without the panel's data), the notifications page and the bell's page. The programme panel's data (`pn`), the recorded-reply client tests, instances both ways, CONFIRM-1 and CONFIRM-2, and the full browser run are milestone 2.**

## 1. What was built

UI repository (`platform-ui/`):
- `theme-pages/board.json`, `theme-pages/notifications.json`: the two pages registered for the exporter.
- `src/pages/notifications.html`, `src/partials/noscript-board.html`, `server/api-board.mjs`, `server/notices.mjs`: `board.items` is `board.entries` and `nt.items` is `nt.lines` (Jinja reads an attribute before a key, so `items` was the dictionary's method).
- `src/js/channels.js` (new, pure): the channel key (`no`, or the rows when the numbers cannot be told apart); `src/js/api.js` applies it to the board reply and the panel's reply; `src/js/board.js` obeys `show_coming`; `src/js/instance.js` opens a real instance's address; `src/js/flag.js` the report box names the contact.
- `tests/theme-a-board.test.mjs`, `tests/theme-a-helpers.mjs`: equivalence (27 contexts); `tests/theme-a-client.test.mjs`: the channel key (also against every golden moment); `tests/export-theme.test.mjs`: page lists follow the registry.
- `tools/golden-board.mjs`: the golden pairs; `tools/smoke-real-board.mjs`: the scripted browser run on the real stack (first slice).
- `docs/contract-a-channels.md`.

Public repository:
- `plugins/l3mon_theme/pages/board.py`: pure builders (`board_view`, `notice_lines`, `channel_keys`, `crawl_text`), the values (`board_page`, `notifications_page`, `crawl`, `start_text`), the views `/board` and `/board/<slug>` (CTFd's decorators and body), the pre-start page, and the `/notifications` hook placed before the board plugin's guard.
- `ui/theme/l3mon/templates/challenges.html`, `notifications.html`: adapters. Generated: `_board.html`, `_notifications.html`, the bundles.
- Tests in `plugins/l3mon_theme/tests/`: `test_board_golden.py`, `test_board_pages.py`, `test_notifications_page.py`, `test_board_cost.py`, helper `theme_a_world.py`, fixture `golden/board-pairs.json`; `tests/integration/test_theme_a.py`.
- Two older theme tests changed (see 4).

## 2. How to run it from a clean clone

Both repositories side by side, branch `ui/a-channels`. The stack: see `docs/deploy/local.md`. Export: `cd L3m0nCTF-ui/platform-ui && node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon`. The UI equivalence tests need Jinja2 on `python`. If port 8080 is taken: `DEV_PORT=9090 tools/compose.sh up -d --wait` and `L3MON_TEST_PORT=9090`. Browser run: `node tools/smoke-real-board.mjs --origin http://localhost:9090 --shots <folder>` (rewrites the stack's settings file for the event window and restores it).

## 3. Evidence (22 October 2026)

| Check | Result |
|---|---|
| UI whole suite, baseline before any change | 334 tests, 333 pass, 1 skipped |
| UI whole suite, final | EVIDENCE-UI |
| UI build and budgets (`node tools/build.mjs`) | passes, notifications 38.9 of 44 KB |
| Export identical twice | identical (diff of two output folders) |
| Theme tests, working tree mounted | 510 passed before the two adjusted tests, all pass after; EVIDENCE-THEME |
| Golden: 273 board pairs, 25 notification lists, plus unit cases | 378 passed |
| Page, notifications and cost tests | board pages 29, notifications 11, cost 30 pass |
| Whole plugin suite, fresh image | EVIDENCE-PLUGINS |
| CTFd's own suite | NOT RUN in milestone 1 (about 7 minutes; run before milestone 2) |
| Integration through nginx (`test_theme_a.py`, 7 tests) | 7 passed on port 9090; the rest of the integration suite: NOT RUN in milestone 1 |
| Browser run on the real stack (`smoke-real-board.mjs`) | 8 of 8 pass (Chrome headless) |
| `node tools/smoke.mjs` (prototype browser checks) | NOT RUN in milestone 1 |
| UI hygiene scan | fails on the untouched baseline too: "expected the nine challenge.toml files under ../challenges, found 0" (environmental, the private monorepo layout) |
| Public hygiene scan | 0 findings |

## 4. Differences from the prompt, and why

- `board.items` and `nt.items` renamed (item 1 of the plan); the shared contract document must follow (section 8).
- `export-theme.test.mjs`, `test_theme_site_links.py` and `test_theme_values.py` (lead-owned) were edited minimally: they listed the exact pages or bundles, which breaks when any stream adds a page; `/board` is no longer an alias.
- Jinja2 and pytest were installed into a throw-away virtual environment outside both repositories (they are needed by the UI equivalence tests and the integration tests on the host).
- Port 9090 instead of 8080 on this machine (another program holds 8080).

## 5. Decisions and rejected alternatives

- A channel is known by `no`; rejected: using the accent number (`ch3`), which would need a second field in every template.
- `/board` runs CTFd's decorators and body (unwrapped through `__wrapped__`); rejected: copying the listing. The crew (no studio) are served without the team redirect.
- The notifications page is a `before_request` hook inserted before the board plugin's guard; rejected: editing `notices.py`.
- Request-local state lives in `request.environ`, not `flask.g` (a held application context made `g` outlive requests in tests).
- Owner's decisions: flags and hints need script; the report box names the contact; the crawl shows the newest public notifications; before the start a signed-in player sees our "not on air yet" page.

## 6. Break-on-purpose

| Rule | The break | The test that noticed |
|---|---|---|
| A held-back programme is a 404 | no abort | `test_a_programme_that_is_held_back_or_locked_or_unknown_is_a_missing_page_alike` |
| JavaScript's rounding | Python's `round` | `test_board_golden.py` (1 failed) |
| A channel is known by `no` | known by its row | `test_board_golden.py` (275 failed) |
| The notifications page is served before the guard | hook appended after | `test_a_player_with_no_news_gets_the_themes_quiet_page` |
| `/board` has CTFd's gates | view not wrapped | `test_a_visitor_is_sent_to_sign_in_from_every_address` |
| The board is built once | a second reader and no cache | `test_a_board_request_builds_the_board_once` (3 failed) |
| The notifications page needs a signed-in player | gate off | `test_every_gate_of_the_player_pages_applies_to_the_notifications_page` |
| Newest 50 only | 500 | `test_board_golden.py` (2 failed) |
| The crew need no studio | stock body for the crew | `test_the_crew_sees_what_a_player_sees...` |
| The coming count follows the setting | always shown | `test_the_coming_count_follows_the_crews_setting` |
| State is per request | state kept in a module dictionary | `test_a_locked_programme_opens_for_the_studio_that_solved_its_prerequisite_and_only_for_it` |
| A slug must be plain | pattern accepts anything | **not noticed**: `find_programme` compares with real slugs, so the pattern is defence in depth with no observable effect |

The first run of this table was invalid (the runner used WSL's bash and nothing ran); it was redone with Git Bash and each row requires pytest to report "N failed".

## 7. Security notes

Threats: a hostile name, story, sponsor or notification (escaped by the engine; tested with markup in each); a slug that reveals a withheld programme (same status, same page, same number of statements as an unknown slug); a notification of another studio (the viewer's own list only); the stock page `/notifications` and `/events` stay closed to everyone the page does not serve; no inline script, style or event attribute on any page (tested). Residual: the pre-start page is served to any signed-in player with a studio (by decision); the crawl shows public notifications only.

## 8. Changes asked of the lead or the owner

- Lead: update `template-contract.md` section 4 (`board.entries`, `nt.lines`, the channel key, `show_coming`); take over the three edited shared tests.
- Owner (answered): flags and hints need script; report box; crawl; pre-start page. Still needed: the wording of the sentence a no-script player sees where the flag form would be; the event's contact address.

## 9. Screenshots (outside the repositories)

`D:\CTF_Sanjai\work\_shots\`: `board-desktop.png`, `notifications-desktop.png` (1440 px). Mobile and every state: milestone 2.

## 10. Open questions and known gaps

- The programme panel's no-script page (`pn`) is not ported yet (`pn` is null); the golden tests cover the board only.
- With no channels at all the board shows its frame with "Channels" and nothing on it.
- A category outside the prototype's eleven has no filter chip.
- Channel numbers outside 0 to 99, or repeated, make the art not match (documented, tested).

## 11. Noticed elsewhere (not fixed)

- `tools/hygiene.mjs` needs the monorepo's `challenges` folder.
- `tools/golden.mjs` writes to a path of the old monorepo layout.
- Every integration test that opens port 8080 itself cannot run where another program holds it.
