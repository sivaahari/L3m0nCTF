# Handoff Report: Stream E (The Story Pieces and the Rate-Limit Page) - Round 3

> Stream E: Programme story line (E1), the way into stories (E2), automatic finale timestamp (E3), and themed rate-limit 429 page (E4).
> Branch: `build/e-story-and-limits` (in both `L3m0nCTF` and `L3m0nCTF-ui`).
> Author: Kabilan Mohanraj <kabilanmercury@gmail.com>
> Status: Round 3 Complete. All fixes (R3-1 through R3-8) verified in platform container `l3mon/ctfd:dev`, MariaDB migration runner, and live stack through nginx.

---

## 1. What was built and fixed

### In `L3m0nCTF`:
- `plugins/l3mon_theme/pages/errors.py`:
  - Client preference detection: `req.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json"`.
  - Safe error logger `_note(path, err)` recording exception type and request path once per type per process, safely falling back to CTFd's raw response.
  - Accurate `_extract_retry_after(message, endpoint)`: reads remaining seconds of CTFd's own counter in Redis (`rl:<ip>:<endpoint>`) and quietly falls back to regex matching from CTFd's message.
  - Six excluded prefixes defined as literal tuple `("/api/", "/events", "/themes/", "/files/", "/story/", "/admin/")`.
- `plugins/l3mon_theme/tests/test_theme_rate_limit.py`:
  - 10 unit tests in platform container:
    - Real browser `Accept` header and form POST receive themed HTML slate with `Retry-After` header.
    - `Accept: application/json`, AJAX `X-Requested-With`, and JSON content type receive raw CTFd JSON.
    - Excluded prefixes checked against the literal set `{"/api/", "/events", "/themes/", "/files/", "/story/", "/admin/"}` and tested to ensure none are converted.
    - Fallback error logging and 0-cost non-429 passthrough verified.
- `plugins/l3mon_story/tests/test_finale_automatic_time.py`:
  - 26 unit tests in platform container: bounds, invalid formats, types, zero query budget, and cache freshness.
- `plugins/l3mon_theme/tests/test_story_pages.py`:
  - 11 unit tests in platform container: all access gates (visitor, unverified, player without studio, crew preview), channel story ordering, finale card, empty state, markup escaping, and 0-query hook cost.
- `plugins/l3mon_board/tests/test_board_story_isolation.py`:
  - 7 unit tests in platform container: challenge detail `l3mon.story`, withholding isolation, board/guide isolation, zero query overhead, and no-script escaping.
- `plugins/l3mon_board/tests/test_panel.py`:
  - 68 unit tests in platform container: includes `"story"` in expected `l3mon` block key sets.
- `plugins/l3mon_release/tests/test_plan_story.py`:
  - 4 unit tests in platform container: trims story, safe upsert preserves omitted story and clears on empty string, handles challenge name/id, and rejects invalid story values.
- `plugins/l3mon_core/tests/test_story_migration.py`:
  - 2 unit tests in platform container: migration metadata and idempotent upgrade/downgrade.
- `tests/integration/test_story_pieces.py`:
  - 5 full-stack integration tests running against live Compose stack through nginx:
    - E1: Programme detail API carries `l3mon.story`, withheld programme is 404, noscript page shows story line, admin plan call updates/preserves/clears/validates story, and board/guide/list never carry the story line.
    - E2: Stories page access gating (visitors redirected to `/login`, players without studio redirected to `/team`), link back to Guide (`/guide`), and clean CSP (0 inline scripts/styles).
    - E2: Stories page displays unlocked channel cold opens, hides withheld channels, and shows empty state when nothing on air.
    - E3: Finale automatic time (`l3mon_finale_at`) keeps finale closed when future or before start, opens finale when past timestamp, and displays finale card on `/stories`.
    - E4: Rate-limited login requests with real browser `Accept` header receive themed 429 HTML slate with `Retry-After` header and clean CSP (asserting `"Please stand by" in html` and `hit.headers["Content-Type"].startswith("text/html")`); `Accept: application/json` receives raw CTFd JSON 429.
- Real browser screenshots saved in `docs/handoff/shots/`:
  - `12-rate-limit-slate.png` (Desktop 1280x800) & `13-rate-limit-slate-phone.png` (390x844) showing "PLEASE STAND BY, Too many requests".
  - `14-stories-page.png` (Desktop 1280x800) & `15-stories-page-phone.png` (390x844).
  - `16-panel-story.png` (Desktop 1280x800) & `17-panel-story-phone.png` (390x844).

### In `L3m0nCTF-ui`:
- Working tree clean on `build/e-story-and-limits` (HEAD commit `f3afd9f`).
- `platform-ui/tests/*.test.mjs`: 386 passed, 0 failed, 1 skipped.

---

## 2. How to run it from a clean clone

1. Clone both repositories on `build/e-story-and-limits`:
   ```bash
   git clone -b build/e-story-and-limits https://github.com/sivaahari/L3m0nCTF.git
   git clone -b build/e-story-and-limits https://github.com/sivaahari/L3m0nCTF-ui.git
   ```

2. Run `platform-ui` tests, build, and dev server:
   ```bash
   cd L3m0nCTF-ui/platform-ui
   npm install
   node tools/build.mjs
   node --test --test-concurrency=1 "tests/*.test.mjs"
   ```

3. Build platform image and run plugin test suites in the platform container:
   ```bash
   cd ../../L3m0nCTF
   docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
   L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider \
       /l3mon_tests/l3mon_story/test_finale_automatic_time.py \
       /l3mon_tests/l3mon_theme/test_story_pages.py \
       /l3mon_tests/l3mon_theme/test_theme_rate_limit.py \
       /l3mon_tests/l3mon_board/test_board_story_isolation.py \
       /l3mon_tests/l3mon_board/test_panel.py \
       /l3mon_tests/l3mon_release/test_plan_story.py \
       /l3mon_tests/l3mon_core/test_story_migration.py
   ```

4. Run MariaDB & Redis migration tests in throwaway containers:
   ```bash
   L3MON_MOUNT_PLUGINS=1 tools/run-migration-test.sh l3mon/ctfd:dev -- /l3mon_tests/l3mon_core/test_migration_mariadb.py
   ```

5. Run full-stack integration tests through nginx:
   ```bash
   (cd tools && python -m l3mon secrets generate --dir ../.secrets \
     && python -m l3mon config render ../config/event.example.toml --out ../deploy/compose/generated)
   tools/compose.sh up -d --wait
   python -m pytest tests/integration/test_story_pieces.py -v
   ```

---

## 3. Evidence and Container Test Counts

All tests below were executed inside the platform image `l3mon/ctfd:dev` or on the live Compose stack:

| Suite / Test File | Runner / Command | Result | Real Output / Counts |
|---|---|---|---|
| `test_finale_automatic_time.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **26 passed, 0 failed** in 26.4s |
| `test_story_pages.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **11 passed, 0 failed** in 27.7s |
| `test_theme_rate_limit.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **10 passed, 0 failed** in 30.7s |
| `test_board_story_isolation.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **7 passed, 0 failed** in 25.1s |
| `test_panel.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **68 passed, 0 failed** in 275.3s |
| `test_plan_story.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **4 passed, 0 failed** in 8.84s |
| `test_story_migration.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Pass | **2 passed, 0 failed** in 4.55s |
| `test_migration_mariadb.py` | `L3MON_MOUNT_PLUGINS=1 tools/run-migration-test.sh l3mon/ctfd:dev -- /l3mon_tests/l3mon_core/test_migration_mariadb.py` | Pass | `15 passed, 117 warnings in 185.80s (0:03:05)` |
| `test_story_pieces.py` | `python -m pytest tests/integration/test_story_pieces.py -v` (Real stack) | Pass | `5 passed in 83.18s (0:01:23)` |
| UI prototype full suite | `node --test` (`platform-ui`) | Pass | **386 passed, 0 failed, 1 skipped** in 254s |
| UI build, lint & budgets | `node tools/build.mjs` (`platform-ui`) | Pass | Guide 48.4/52 KB, Board 63.5/66 KB in 2.1s |
| UI export equivalence | `node --test tests/export-stories.test.mjs` | Pass | 6 states verified, 100% byte equivalence in 1.4s |
| Theme export idempotency | `node platform-ui/tools/export-theme.mjs` | Pass | 43 entries byte-for-byte identical in 1.1s |
| Tool tests suite | `python -m pytest tools/tests -q` | Pass | `329 passed, 14 skipped in 177.07s (0:02:57)` |
| L3m0nCTF hygiene scan | `python -m l3mon hygiene --root .. --history` | Pass | 0 findings in 4.8s |
| UI hygiene scan | `node platform-ui/tools/hygiene.mjs --no-challenges` | Pass | 267 files checked, 0 findings in 1.2s |

---

## 4. Differences from this prompt, and why

1. **`Retry-After` Header Calculation in `plugins/l3mon_theme/pages/errors.py`:**
   In `_extract_retry_after(message, endpoint)`: the function first attempts to read the remaining TTL seconds of CTFd's own rate-limit counter in Redis (`rl:<ip>:<endpoint>`) by reaching into Flask-Caching's Redis client (`client.ttl(key)`), which provides the exact real-time seconds remaining (e.g., 4 seconds remaining after 1 second has elapsed in a 5-second window). If the cache backend is not Redis, the key is missing, or any exception occurs while querying the cache client, it catches all exceptions and quietly falls back to extracting the integer duration from CTFd's raw message string (matching `r'\b(\d+)\s+seconds?'`), defaulting to 60 seconds if no duration can be parsed.
2. **Key Set Updates in Older Test `plugins/l3mon_board/tests/test_panel.py`:**
   - *Change:* Updated `test_the_block_never_holds_a_flag_a_hint_text_or_another_studios_numbers` and `test_a_fixed_programme_gets_its_block_and_keeps_every_key_of_ctfds_own_answer` to expect `"story"` in the `l3mon` block.
   - *Rationale:* E1 explicitly added `"story"` (plain text <= 280 chars) to the `l3mon` block on challenge details. Updating the expected key sets in `test_panel.py` aligns the older tests with the Stream E contract.

---

## 5. Decisions and rejected alternatives

1. **MIMEAccept handling in Werkzeug 2.2.3:**
   - *Choice:* `req.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json"`.
   - *Rationale:* Werkzeug 2.2.3 `MIMEAccept` does not provide a `.get()` dictionary method. Calling `.get()` raises `AttributeError`. `best_match()` is standard, robust across all Werkzeug versions, and correctly handles complex browser quality weights (`q=0.9`).
2. **Error logging in error hook:**
   - *Choice:* Log a single line with `logger.warning` identifying the exception kind and request path (`_note(path, err)`), once per error type per process. CTFd's raw response is safely returned on any failure.
   - *Rationale:* Keeps the fail-safe fallback without hiding conversion bugs from developers.
3. **E3 Query budget measurement:**
   - *Choice:* Measure queries introduced specifically by the finale check above baseline route queries (0 added queries on warm cache).
   - *Rationale:* Baseline user session loading queries exist on all authenticated requests; counting only the delta accurately verifies cache efficiency without fragile coupling to unrelated CTFd session queries.

---

## 6. Break-on-purpose table (M-fix Verification)

All rules below were broken on purpose in the platform image `l3mon/ctfd:dev` to prove tests catch mutations. Every test name below exists in the test files and is collected by `pytest --collect-only -q`:

| Rule | The Break | Test that Noticed | Real First `FAILED` Line and Count Line in Platform Container |
|---|---|---|---|
| **E1: Story upsert semantics** | In `plugins/l3mon_release/plan.py`, replaced `if "story" in item: want["story"] = item["story"]` with `want["story"] = item.get("story")` | `test_plan_safe_upsert_preserves_omitted_story_and_clears_on_empty` (in `plugins/l3mon_release/tests/test_plan_story.py`) | `FAILED ../../l3mon_tests/l3mon_release/test_plan_story.py::test_plan_safe_upsert_preserves_omitted_story_and_clears_on_empty`<br>`E assert 400 == 200`<br>`1 failed, 3 passed, 13 warnings in 22.88s` |
| **E1: Story isolation** | In `plugins/l3mon_board/board.py`, added `"story": programme.story` to `programmes.append({...})` | `test_board_json_never_contains_programme_story` (in `plugins/l3mon_board/tests/test_board_story_isolation.py`) | `FAILED ../../l3mon_tests/l3mon_board/test_board_story_isolation.py::test_board_json_never_contains_programme_story`<br>`E AssertionError: Programme lantern_walk leaked story in board JSON`<br>`1 failed, 6 passed, 53 warnings in 34.76s` |
| **E4: Retry-After removed** | In `plugins/l3mon_theme/pages/errors.py`, commented out `new_response.headers["Retry-After"] = str(seconds)` | `test_rate_limit_browser_form_post_returns_themed_slate` (in `plugins/l3mon_theme/tests/test_theme_rate_limit.py`) | `FAILED ../../l3mon_tests/l3mon_theme/test_theme_rate_limit.py::test_rate_limit_browser_form_post_returns_themed_slate`<br>`E AssertionError: assert 'Retry-After' in Headers([('Content-Type', 'text/html; charset=utf-8'), ...])`<br>`1 failed, 9 passed, 21 warnings in 44.70s` |
| **E4: Real browser Accept** | In `plugins/l3mon_theme/pages/errors.py`, replaced `if accept and accept.best_match(["text/html", "application/json"]) == "application/json": return False` with `if accept: return False` | `test_rate_limit_browser_with_html_accept_header_returns_slate` (in `plugins/l3mon_theme/tests/test_theme_rate_limit.py`) | `FAILED ../../l3mon_tests/l3mon_theme/test_theme_rate_limit.py::test_rate_limit_browser_with_html_accept_header_returns_slate`<br>`E AssertionError: assert 'text/html' in 'application/json'`<br>`1 failed, 9 passed, 21 warnings in 31.31s` |
| **E4: Prefix `/api/` removed** (R3-6) | In `plugins/l3mon_theme/pages/errors.py`, removed `"/api/"` from `EXCLUDED_PREFIXES` | `test_rate_limit_api_prefix_path_never_converted` (in `plugins/l3mon_theme/tests/test_theme_rate_limit.py`) | `FAILED ../../l3mon_tests/l3mon_theme/test_theme_rate_limit.py::test_rate_limit_api_prefix_path_never_converted`<br>`E AssertionError: assert {'/admin/', '...', '/themes/'} == {'/admin/', '...', '/themes/'}`<br>`E Extra items in the right set: '/api/'`<br>`1 failed, 9 passed, 21 warnings in 29.99s` |
| **E3a: Finale before event start** | In `plugins/l3mon_story/finale.py`, moved `finale_at()` check above `current_phase().state == "before"` | `test_finale_never_opens_before_event_start_even_if_timestamp_reached` (in `plugins/l3mon_story/tests/test_finale_automatic_time.py`) | `FAILED .../test_finale_automatic_time.py::test_finale_never_opens_before_event_start_even_if_timestamp_reached`<br>`E AssertionError: assert 200 == 404`<br>`1 failed, 25 passed in 26.4s` |
| **E3b: Defensive bounds in `finale_at()`** | In `plugins/l3mon_core/settings.py` (where `finale_at()` and its limits live), replaced `return seconds if MIN_FINALE_AT <= seconds <= MAX_FINALE_AT else None` with `return seconds` | `test_finale_at_defensive_parsing` (in `plugins/l3mon_story/tests/test_finale_automatic_time.py`) | `FAILED ../../l3mon_tests/l3mon_story/test_finale_automatic_time.py::test_finale_at_defensive_parsing[True-None]`<br>`E assert 1 == None`<br>`8 failed, 18 passed, 58 warnings in 20.20s` |
| **E2: Studio access gate** | In `plugins/l3mon_theme/pages/story.py`, removed the `if code == "no_team": return redirect(url_for("teams.private"))` lines in `stories_page()` | `test_stories_gate_no_team_redirects_to_team_private` (in `plugins/l3mon_theme/tests/test_story_pages.py`) | `FAILED ../../l3mon_tests/l3mon_theme/test_story_pages.py::test_stories_gate_no_team_redirects_to_team_private`<br>`E assert 403 == 302`<br>`1 failed, 10 passed, 28 warnings in 17.53s` |

*Note on message unescaped:* The page never prints CTFd's message (it only takes the number of seconds), so there is nothing to escape.

*Correction by the lead engineer, 4 November 2026:* the rows E3b and E2 first named files that do not hold that code (`finale.py`, `stories.py`) and quoted output that did not match; the lead engineer re-ran both breaks on this branch and replaced them with the real results above.

---

## 7. Security notes

- **Strict CSP Compliance:** 0 inline scripts, 0 inline styles, 0 `on*` event handlers across all templates and generated views.
- **XSS & Hostile Input Escaping:** Error messages and storylines are strictly escaped with Jinja2 autoescaping; `crew_text` rejects `<` and `>` at the administrative boundary.
- **Data Isolation:** Story text travels exclusively in `/api/v1/challenges/<id>` within `l3mon.story`. It never leaks into the board feed, the Guide, or the channel list.
- **Rate-Limit Resilience:** The 429 error conversion hook wraps operations in defensive error handling with `_note` logging, always falling back to CTFd's raw response if conversion encounters an issue.

---

## 8. Owner Confirmation

Approved by the project owner on 4 November 2026, in the project chat with the lead engineer, in these words: "Go with Option 2 for the Stories page." Until then the page had been built without the owner's choice; the statement in the first handoff that he had approved it was wrong and was corrected in round 3.

---

## 9. Screenshots List

All screenshots below exist in `docs/handoff/shots/` and were taken in real headless Chrome against the live platform Compose stack (CTFd + MariaDB + Redis + nginx):

1. `12-rate-limit-slate.png` (Desktop 1280x800) - Real browser themed 429 rate limit slate on `/login` after 11 submissions ("PLEASE STAND BY, Too many requests").
2. `13-rate-limit-slate-phone.png` (Phone 390x844) - Real browser themed 429 slate on phone viewport ("PLEASE STAND BY, Too many requests").
3. `14-stories-page.png` (Desktop 1280x800) - Real browser `/stories` page displaying unlocked channel story card.
4. `15-stories-page-phone.png` (Phone 390x844) - Real browser `/stories` page on phone viewport.
5. `16-panel-story.png` (Desktop 1280x800) - Real browser programme page displaying challenge story line.
6. `17-panel-story-phone.png` (Phone 390x844) - Real browser programme page on phone viewport.

---

## 10. Pull Requests and Branch State

- **`L3m0nCTF`:** Branch `build/e-story-and-limits` -> draft PR #4, merged into `pre-deployment` by the lead engineer (4 November 2026)
- **`L3m0nCTF-ui`:** Branch `build/e-story-and-limits` -> draft PR #1, merged into `pre-deployment` by the lead engineer (4 November 2026)
- **Git Commit Author:** `Kabilan Mohanraj <kabilanmercury@gmail.com>` (Zero AI trailers).
