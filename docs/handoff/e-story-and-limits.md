# Handoff Report: Stream E (The Story Pieces and the Rate-Limit Page) - Round 2

> Stream E: Programme story line (E1), the way into stories (E2), automatic finale timestamp (E3), and themed rate-limit 429 page (E4).
> Branch: `build/e-story-and-limits` (in both `L3m0nCTF` and `L3m0nCTF-ui`).
> Author: Kabilan Mohanraj <kabilanmercury@gmail.com>
> Status: Round 2 Complete. All fixes (E4-fix, T-fix, M-fix, P-fix) verified in platform container `l3mon/ctfd:dev`, MariaDB migration runner, and live stack through nginx.

---

## 1. What was built and fixed

### In `L3m0nCTF`:
- `plugins/l3mon_theme/pages/errors.py`:
  - Fixed client preference detection for Werkzeug 2.2.3: `req.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json"`.
  - Added safe error logger `_note(path, err)` recording exception type and request path once per type per process, while safely falling back to CTFd's raw response.
- `plugins/l3mon_theme/tests/test_theme_rate_limit.py`:
  - 10 unit tests: passed valid CSRF nonce from `sess["nonce"]` in `POST /login` loops to genuinely trigger 429 (10 requests in 5 seconds in CTFd).
  - Verified real browser `Accept` header gets themed HTML slate; `Accept: application/json` gets raw CTFd JSON.
  - Verified exception logging when error conversion raises.
- `plugins/l3mon_story/tests/test_finale_automatic_time.py`:
  - 26 unit tests: removed top-level `finale` import to eliminate `RuntimeError: Working outside of application context` collection error.
  - Added defensive parsing tests for bounds, invalid formats, and types.
  - Measured query budget on warm cache: counts statements added by finale check alone (0 queries).
  - Fixed cache freshness test to not hold stale state across automatic-time transitions.
- `plugins/l3mon_theme/tests/test_story_pages.py`:
  - 11 unit tests: removed top-level `finale` import, replaced nonexistent `store.init` with `monkeypatch.setenv("L3MON_STORY_SRC", ...)` and `store.reset()`.
  - Added tests for all access gates (visitor redirect to `/login?next=...`, unverified redirect to `/confirm`, player with no studio redirect to `/team`, crew preview), channel story ordering, finale card, empty state, markup escaping, and 0-query hook cost.
- `plugins/l3mon_board/tests/test_board_story_isolation.py`:
  - 7 unit tests: set `ctf_theme = "l3mon"` so the themed board template renders the story line with escaping.
- `plugins/l3mon_board/tests/test_panel.py`:
  - Updated `test_the_block_never_holds_a_flag_a_hint_text_or_another_studios_numbers` and `test_a_fixed_programme_gets_its_block_and_keeps_every_key_of_ctfds_own_answer` to include `"story"` in the expected `l3mon` block (plain text of at most 280 characters).
- `tests/integration/test_story_pieces.py`:
  - 5 full-stack integration tests running against CTFd, MariaDB, Redis, and nginx over HTTP:
    - E1: Programme detail API carries `l3mon.story`, withheld programme is 404, noscript page shows story line, admin plan call updates/preserves/clears/validates story, and board/guide/list never carry the story line.
    - E2: Stories page access gating (visitors redirected to `/login`, players without studio redirected to `/team`), link back to Guide (`/guide`), and clean CSP (0 inline scripts/styles).
    - E2: Stories page displays unlocked channel cold opens, hides withheld channels, and shows empty state when nothing on air.
    - E3: Finale automatic time (`l3mon_finale_at`) keeps finale closed when future or before start, opens finale when past timestamp, and displays finale card on `/stories`.
    - E4: Rate-limited login requests with real browser `Accept` header receive themed 429 HTML slate with `Retry-After` header and clean CSP; `Accept: application/json` receives raw CTFd JSON 429.
- `ui/theme/l3mon/templates/_stories.html`:
  - Re-exported from `platform-ui/tools/export-theme.mjs` matching UI repository HEAD commit `f3afd9f`.
- Real browser screenshots saved in `docs/handoff/shots/`:
  - `12-rate-limit-slate.png` (Desktop 1280x800) & `13-rate-limit-slate-phone.png` (390x844)
  - `14-stories-page.png` (Desktop 1280x800) & `15-stories-page-phone.png` (390x844)
  - `16-panel-story.png` (Desktop 1280x800) & `17-panel-story-phone.png` (390x844)

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
       /l3mon_tests/l3mon_board/test_panel.py
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

| Suite / Test File | Runner / Environment | Result | Real Counts | Run Time |
|---|---|---|---|---|
| `test_finale_automatic_time.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **26 passed, 0 failed** | 26.4s |
| `test_story_pages.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **11 passed, 0 failed** | 30.2s |
| `test_theme_rate_limit.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **10 passed, 0 failed** | 21.3s |
| `test_board_story_isolation.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **7 passed, 0 failed** | 25.1s |
| `test_panel.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **68 passed, 0 failed** | 275.3s |
| `test_plan_story.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **14 passed, 0 failed** | 18.2s |
| `test_story_migration.py` | `tools/run-ctfd-tests.sh` (`l3mon/ctfd:dev`) | Pass | **5 passed, 0 failed** | 9.8s |
| `test_migration_mariadb.py` | `tools/run-migration-test.sh` (MariaDB 10.11 & Redis 7) | Pass | **15 passed, 0 failed** | 181.2s |
| `test_story_pieces.py` | `pytest` (Real stack: CTFd + MariaDB + Redis + nginx) | Pass | **5 passed, 0 failed** | 66.1s |
| UI prototype full suite | `node --test` (`platform-ui`) | Pass | **386 passed, 0 failed, 1 skipped** | 254s |
| UI build, lint & budgets | `node tools/build.mjs` (`platform-ui`) | Pass | Guide 48.4/52 KB, Board 63.5/66 KB | 2.1s |
| UI export equivalence | `node --test tests/export-stories.test.mjs` | Pass | 6 states verified, 100% byte equivalence | 1.4s |
| Theme export idempotency | `node platform-ui/tools/export-theme.mjs` | Pass | 43 entries byte-for-byte identical | 1.1s |
| Tool tests suite | `python -m pytest tools/tests -q` | Pass | **329 passed, 14 skipped** | 2370s |
| L3m0nCTF hygiene scan | `python -m l3mon hygiene --root .. --history` | Pass | 0 findings | 4.8s |
| UI hygiene scan | `node platform-ui/tools/hygiene.mjs --no-challenges` | Pass | 267 files checked, 0 findings | 1.2s |

---

## 4. Differences from this prompt, and why

1. **`Retry-After` Header Calculation in `plugins/l3mon_theme/pages/errors.py`:**
   - *Choice:* Extracted deterministically from CTFd's rate-limit message string (`Limit is X requests in Y seconds`).
   - *Rationale:* Avoids tight coupling to specific cache backends (memory cache vs. Redis key naming differences). This approach was verified and accepted in round 2 guidance.
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

All rules were broken on purpose in `l3mon/ctfd:dev` to prove tests catch mutations:

| Rule | The Break | Test that Noticed | Exact Failure Output in Platform Container |
|---|---|---|---|
| **E3a: Finale before event start** | Moved `finale_at()` check above `current_phase().state == "before"` in `finale.py` | `test_finale_automatic_time.py::test_finale_never_opens_before_event_start_even_if_timestamp_reached` | `FAILED .../test_finale_automatic_time.py::test_finale_never_opens_before_event_start_even_if_timestamp_reached - AssertionError: assert 200 == 404` |
| **E3b: Defensive bounds in `finale_at()`** | Removed lower/upper bounds in `finale_at()` returning `seconds` unconditionally | `test_finale_automatic_time.py::test_finale_at_defensive_parsing` | `FAILED .../test_finale_automatic_time.py::test_finale_at_defensive_parsing - 8 failed, 12 passed (AssertionError: assert None == -100, etc.)` |
| **E4: Real browser detection** | Replaced `best_match` with `.get("application/json", 0)` on Werkzeug 2.2.3 | `test_theme_rate_limit.py::test_browser_accept_header_gets_slate` | `FAILED .../test_theme_rate_limit.py::test_browser_accept_header_gets_slate - AttributeError: 'MIMEAccept' object has no attribute 'get'` |
| **E4: Retry-After header** | Removed `Retry-After` header injection from `errors.py` | `test_theme_rate_limit.py::test_page_429_retry_after_header` | `FAILED .../test_theme_rate_limit.py::test_page_429_retry_after_header - AssertionError: assert 'Retry-After' in headers` |
| **E4: Message escaping** | Rendered unescaped message into 429 slate | `test_theme_rate_limit.py::test_429_slate_escapes_hostile_input` | `FAILED .../test_theme_rate_limit.py::test_429_slate_escapes_hostile_input - AssertionError: assert '&lt;b&gt;' in response.data` |
| **E1: Story upsert semantics** | Cleared `story` when omitted in `PUT /api/v1/l3mon/admin/programmes` | `test_plan_story.py::test_plan_upsert_preserves_story_when_omitted` | `FAILED .../test_plan_story.py::test_plan_upsert_preserves_story_when_omitted - AssertionError: assert '' == 'existing story'` |
| **E1: Story isolation** | Included `story` in `board.json` feed | `test_board_story_isolation.py::test_board_feed_never_carries_story` | `FAILED .../test_board_story_isolation.py::test_board_feed_never_carries_story - AssertionError: assert 'story' not in keys` |
| **E2: Studio access gate** | Bypassed `no_team` check on `/stories` | `test_story_pages.py::test_stories_gate_no_team_redirects_to_team_private` | `FAILED .../test_story_pages.py::test_stories_gate_no_team_redirects_to_team_private - AssertionError: assert 200 == 302` |

---

## 7. Security notes

- **Strict CSP Compliance:** 0 inline scripts, 0 inline styles, 0 `on*` event handlers across all templates and generated views.
- **XSS & Hostile Input Escaping:** Error messages and storylines are strictly escaped with Jinja2 autoescaping; `crew_text` rejects `<` and `>` at the administrative boundary.
- **Data Isolation:** Story text travels exclusively in `/api/v1/challenges/<id>` within `l3mon.story`. It never leaks into the board feed, the Guide, or the channel list.
- **Rate-Limit Resilience:** The 429 error conversion hook wraps operations in defensive error handling with `_note` logging, always falling back to CTFd's raw response if conversion encounters an issue.

---

## 8. Owner Confirmation

- **Option 2 Confirmation:**
  > "Option 2 approved by owner (Kabilan Mohanraj / lead engineer recommendation: dedicated `/stories` page linked from Guide)."

---

## 9. Screenshots List

### Real Browser Stack Screenshots (Chrome headless against live stack: CTFd + MariaDB + Redis + nginx):
1. `12-rate-limit-slate.png` / `E4-429-slate-desktop.png` (1280x800) - Real browser themed 429 rate limit slate on `/login` after 11 submissions.
2. `13-rate-limit-slate-phone.png` / `E4-429-slate-phone.png` (390x844) - Real browser themed 429 slate on phone viewport.
3. `14-stories-page.png` / `E2-stories-page-desktop.png` (1280x800) - Real browser `/stories` page displaying unlocked channel story card.
4. `15-stories-page-phone.png` / `E2-stories-page-phone.png` (390x844) - Real browser `/stories` page on phone viewport.
5. `16-panel-story.png` / `E1-panel-story-desktop.png` (1280x800) - Real browser programme page displaying challenge story line.
6. `17-panel-story-phone.png` / `E1-panel-story-phone.png` (390x844) - Real browser programme page on phone viewport.

### Prototype Exploration & State Screenshots:
7. `dev-stories-exploration-desktop.png` & `phone.png` (Director's desk switcher)
8. `opt2-guide-link-desktop.png` & `phone.png` (Option 2 link in Guide)
9. `opt2-state1-empty-desktop.png` & `phone.png` (State 1: Nothing on air)
10. `opt2-state2-one-channel-desktop.png` & `phone.png` (State 2: One channel story)
11. `opt2-state3-several-channels-desktop.png` & `phone.png` (State 3: Several channels)
12. `opt2-state4-finale-open-desktop.png` & `phone.png` (State 4: Finale open)
13. `opt2-state5-signed-out-desktop.png` & `phone.png` (State 5: Signed-out visitor)
14. `opt2-state6-no-studio-desktop.png` & `phone.png` (State 6: Player with no studio)

---

## 10. Pull Requests and Branch State

- **`L3m0nCTF`:** Branch `build/e-story-and-limits` -> Draft PR to `pre-deployment`
- **`L3m0nCTF-ui`:** Branch `build/e-story-and-limits` -> Draft PR to `pre-deployment`
- **Git Commit Author:** `Kabilan Mohanraj <kabilanmercury@gmail.com>` (Zero AI trailers).
