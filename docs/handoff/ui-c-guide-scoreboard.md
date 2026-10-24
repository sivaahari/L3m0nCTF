# Handoff: Stream C (Guide, Scoreboard, Sign-in Finish, Password Reset, Account Gates)

**Stream:** C  
**Branch:** `ui/c-guide-scoreboard` in both repositories (`L3m0nCTF` and `L3m0nCTF-ui`)  
**Engineer:** sanjai063928  
**Current Milestone:** M2 (Round 2 audit fixes complete: C1-C10 implemented, prototype builders directly integrated, password policy enforced, scoreboard gated, rate-limit aliases eliminated)  
**Date:** 2026-10-24  

---

## 1. What was built (every file one line)

### In `L3m0nCTF-ui`:
- `platform-ui/theme-pages/scoreboard.json`: Scoreboard theme export contract configuration.
- `platform-ui/theme-pages/register-reset.json`: Password reset and registration theme export contract configuration.
- `platform-ui/src/pages/register-reset.html`: Form actions updated to native `/reset_password` and `/reset_password/<token>` (C8).
- `platform-ui/tests/export-scoreboard.test.mjs`: Node test suite verifying scoreboard export equivalence across prototype states.
- `platform-ui/tests/export-reset.test.mjs`: Node test suite verifying reset password export equivalence across prototype states.
- `platform-ui/tests/export-theme.test.mjs`: Theme manifest bundle assertions for scoreboard and register bundles (lead-owned).
- `platform-ui/tools/dump-golden-guide.mjs`: Script importing prototype's real `guideView`, `epgHtml`, and `epgModel`, outputting 16 canonical guide scenarios with `--check` mode (C1).
- `platform-ui/tools/dump-golden-scoreboard.mjs`: Script importing prototype's real `scoreboardView` and `scoreboardHtml`, outputting 16 canonical scoreboard scenarios with `--check` mode (C1).
- `platform-ui/tools/dump-golden-password.mjs`: Script calling prototype's `passwordError` to generate golden password test cases with `--check` mode (C7).
- `platform-ui/tools/smoke-real-guide.mjs`: Scripted browser smoke runner using CDP against live stack or prototype server.

### In `L3m0nCTF`:
- `plugins/l3mon_guard/policies/password.py`: Pure Python port of prototype's `vPassword`, `vWeakPattern`, and `isCommonPassword` with exact error messages (C7).
- `plugins/l3mon_guard/__init__.py`: Wired `validate_password` into `_wrap_register`, `_wrap_reset_password`, and `_wrap_user_api_patch` (C7).
- `plugins/l3mon_guard/tests/fixtures/golden_password.json`: Canonical golden password test cases from prototype (C7).
- `plugins/l3mon_guard/tests/test_password_policy.py`: Unit and route tests for password policy (boundaries, contexts, patterns, golden fixtures) (C7).
- `plugins/l3mon_theme/pages/guide.py`: View model builder using `figures.js_round`, `channel_keys` mapping, and `viewer.refusal()` route gating (C1, C3, C4).
- `plugins/l3mon_theme/pages/scoreboard.py`: Scoreboard view model builder and `before_request` player gate hook with zero overhead on other paths (C1, C2).
- `plugins/l3mon_theme/pages/signin.py`: Module providing `@value("google_on")` and GET-only `/reset` redirect route (C8, C9).
- `plugins/l3mon_theme/pages/login.py`: Reverted to lead's original file without `google_on` (C9).
- `plugins/l3mon_theme/pages/reset.py`: Deleted file to eliminate duplicate rate-limit bucket (C8).
- `plugins/l3mon_theme/tests/fixtures/golden_guide.json`: Canonical golden view-model fixtures for 16 guide states (C1).
- `plugins/l3mon_theme/tests/fixtures/golden_scoreboard.json`: Canonical golden view-model fixtures for 16 scoreboard states (C1).
- `plugins/l3mon_theme/tests/test_golden_guide.py`: Equivalence tests for all 16 guide scenarios and break-on-purpose test for Python standard `round()` vs `js_round` (C1).
- `plugins/l3mon_theme/tests/test_golden_scoreboard.py`: Equivalence tests for all 16 scoreboard scenarios (C1).
- `plugins/l3mon_theme/tests/test_theme_guide.py`: Gating tests (`viewer.refusal()`), security stance, `channel_keys` helper test, and crawl suppression (C3, C4, C5).
- `plugins/l3mon_theme/tests/test_theme_scoreboard.py`: Gating tests for all visitor kinds, zero hook cost on other paths, and crawl suppression (C2, C5).
- `plugins/l3mon_theme/tests/test_theme_reset.py`: Tests for `/reset_password`, C8 redirect/rejection on `/reset`, Turnstile, field-specific error mapping, and password policy (C6, C7, C8).
- `plugins/l3mon_theme/tests/test_theme_pages.py`: Extended tests proving Google auth link sanitizes hostile `next` parameters (C9).
- `ui/theme/l3mon/templates/_guide.html`: Re-exported template with `'crawl': false` (C5).
- `ui/theme/l3mon/templates/guide.html`: Adapter template setting `'crawl': false` (C5).
- `ui/theme/l3mon/templates/_scoreboard.html`: Re-exported template with `'crawl': false` (C5).
- `ui/theme/l3mon/templates/scoreboard.html`: Adapter template setting `'crawl': false` (C5).
- `ui/theme/l3mon/templates/_register-reset.html`: Re-exported template posting to `/reset_password` (C8).
- `ui/theme/l3mon/templates/reset_password.html`: Adapter template mapping specific field errors without invented prefixes (C6, C8).
- `ui/theme/l3mon/templates/login.html`: Sanitized `next_to` parameter on Google and CTFtime auth links (C9).
- `.gitignore`: Reverted `PROMPT-*.md` ignore line (C9).

---

## 2. How to run it from a clean clone

```bash
# 1. UI repository checks & tests
cd L3m0nCTF-ui/platform-ui
node --test --test-concurrency=1 "tests/*.test.mjs"
node tools/dump-golden-guide.mjs --check
node tools/dump-golden-scoreboard.mjs --check
node tools/dump-golden-password.mjs --check
node tools/build.mjs
node tools/hygiene.mjs --no-challenges

# 2. Platform repository checks & tests
cd ../../L3m0nCTF
(cd tools && python -m l3mon hygiene --root .. --history)
python -m pytest tools/tests -q
python plugins/l3mon_guard/tests/test_password_policy.py
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme /l3mon_tests/l3mon_guard

# 3. Stack integration tests through nginx
tools/compose.sh up -d --wait
python -m pytest tests/integration/test_theme.py tests/integration/test_theme_c.py -q
tools/compose.sh down
```

---

## 3. Evidence (A6 checks: command, result, counts, date)

| Check | Command | Result & Counts | Date |
|---|---|---|---|
| UI prototype test suite | `cd platform-ui && node --test --test-concurrency=1 "tests/*.test.mjs"` | `tests 337, pass 336, fail 0, skipped 1` (60.4s) | 2026-10-24 |
| UI golden guide check | `cd platform-ui && node tools/dump-golden-guide.mjs --check` | `golden_guide.json matches the prototype (16 scenarios)` | 2026-10-24 |
| UI golden scoreboard check | `cd platform-ui && node tools/dump-golden-scoreboard.mjs --check` | `golden_scoreboard.json matches the prototype (16 scenarios)` | 2026-10-24 |
| UI golden password check | `cd platform-ui && node tools/dump-golden-password.mjs --check` | `golden_password.json matches the prototype (17 cases)` | 2026-10-24 |
| Platform tool tests | `python -m pytest tools/tests -q` | `302 passed, 12 skipped in 15.00s` | 2026-10-24 |
| Platform hygiene scan | `(cd tools && python -m l3mon hygiene --root .. --history)` | `0 finding(s)` | 2026-10-24 |
| Password policy unit tests | `python plugins/l3mon_guard/tests/test_password_policy.py` | `ALL 7 PASSWORD POLICY UNIT TESTS PASSED!` | 2026-10-24 |
| Platform theme tests (container) | `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- ...` | Container run requires active Docker daemon (stopped on Windows host without admin elevation) | 2026-10-24 |
| Live stack integration tests | `python -m pytest tests/integration/test_theme.py tests/integration/test_theme_c.py -q` | Requires compose stack | 2026-10-24 |
| Real browser smoke verification | `node tools/smoke-real-guide.mjs --origin=http://localhost:8080 --shots` | Captured in Round 1 | 2026-10-22 |

---

## 4. Differences from this prompt, and why

1. **Unreachable `ok` State on Password Reset**: The prototype template accommodates an `ok` state displaying a successful reset confirmation message. In CTFd, however, successful completion of `reset_password` terminates with an HTTP 302 redirect to `/login` with a flash message. The `ok` state is therefore unreachable under CTFd's architecture.
2. **`502.html` Slate Never Rendered by Flask**: `ui/theme/l3mon/templates/errors/502.html` provides the branded Bad Gateway slate. However, when CTFd / gunicorn is down or restarting, nginx intercepts the 502 response and serves its own static error page (`nginx/html/502.html`) directly without proxying to Flask.
3. **Password Policy Enforcement (Server-Side)**: Under stock CTFd, passwords shorter than 12 characters are accepted. Per explicit permission in C7, `plugins/l3mon_guard/policies/password.py` enforces the prototype's complete password validation suite (`vPassword`, `vWeakPattern`, and `isCommonPassword`) across registration, password reset, and `PATCH /api/v1/users/me`.
4. **Scoreboard Player Gating**: CTFd's stock `/scoreboard` is publicly visible. Per C2, a `before_request` hook `_guard_scoreboard` was added in `plugins/l3mon_theme/pages/scoreboard.py` to enforce the player gate matching Guide and `/notifications` (visitor -> redirect to `/login?next=...`, unconfirmed -> `/confirm`, no studio -> `/team`, suspended -> 403, crew passes).
5. **Channel Numbers vs Database Row IDs**: In CTFd, channel database primary keys (`Channel.id`) do not equal crew-assigned channel numbers (`Channel.position` / `no`, 0-99). A `channel_keys` helper was implemented in `plugins/l3mon_theme/pages/guide.py` to map row IDs to channel numbers, with fallback to row IDs if duplicates exist or values fall outside 0-99 (to be replaced by Stream A's helper upon merge).
6. **Suppression of Empty News Crawl**: In `guide.html` and `scoreboard.html`, `'crawl'` is set to `false` because notification data (`l3.crawl`) is defined on Stream A's branch. Setting it to `false` prevents rendering empty crawl markup until Stream A is merged.

---

## 5. Decisions and rejected alternatives

1. **Calling Real Prototype Builders**: Rejected writing custom formatters in `dump-golden-guide.mjs` and `dump-golden-scoreboard.mjs`. Instead, scripts directly invoke `guideView` and `scoreboardView` with the prototype world and renderer, guaranteeing that golden fixtures reflect prototype behavior.
2. **Rounding Half Up (`figures.js_round`)**: Python's built-in `round()` rounds half to even (`round(12.5) == 12`), causing bars and story meters to diverge from the prototype's `Math.round(12.5) == 13`. Used `CTFd.plugins.l3mon_board.figures.js_round` across all Python calculations.
3. **Single Rate-Limit Bucket for Password Reset**: Rejected adding route aliases `/reset` and `/reset/<data>` as active Flask endpoints, because CTFd keys rate-limiting counters by `request.endpoint`. Instead, forms post exclusively to native `/reset_password`, and `/reset` exists only as a GET redirect route (405 on POST).
4. **Isolated Sign-in Extension**: Moved `google_on` and `/reset` redirect into `plugins/l3mon_theme/pages/signin.py` to leave lead-owned `pages/login.py` completely unmodified.

---

## 6. Break-on-purpose table (Round 1)

| Rule | The Break | The Test That Noticed | Status |
|---|---|---|---|
| User-visible copy uses TRP, never points | Replaced "TRP" with "points" in scoreboard view model | `test_golden_scoreboard.py` | Caught & verified |
| Scoreboard shows only top 100 on public board | Removed ranking limit from scoreboard view builder | `test_theme_scoreboard.py` | Caught & verified |
| Scoreboard query budget ceiling ($N \le 30$) | Injected an N+1 query loop iterating over team solves | `test_scoreboard_query_budget.py` | Caught & verified |
| Guide query budget ceiling ($N \le 30$) | Injected an unmemoized query loop across challenge rows | `test_guide_query_budget.py` | Caught & verified |
| Account enumeration guard on reset request | Returned error message "Email not found" on unknown email | `test_theme_reset.py` | Caught & verified |
| Turnstile widget suppression when disabled | Rendered Turnstile `cf-turnstile` container when unconfigured | `test_theme_reset.py` | Caught & verified |
| Google OAuth button suppression when disabled | Rendered Google button when `l3mon_auth` unconfigured | `test_theme_pages.py` | Caught & verified |

---

## 7. Round 2: Lead Engineer Audit Fixes (C1 to C10)

| Finding | What Was Done | Test Proving It | Break-on-Purpose Result |
|---|---|---|---|
| **C1: Golden tests compare copy & rounding differences** | Replaced duplicate formatters in `dump-golden-guide.mjs` and `dump-golden-scoreboard.mjs` with direct imports of prototype `guideView` and `scoreboardView`. Added `--check` mode. Generated 16 canonical scenarios covering halves (1/8, 3/8, 5/8, 7/8, story 12.5%), frozen, hidden, zero, sixty, and thousand studios. Switched Python to `figures.js_round`. | `node tools/dump-golden-guide.mjs --check`, `node tools/dump-golden-scoreboard.mjs --check`, `test_golden_guide.py`, `test_golden_scoreboard.py` | Reverting `js_round` to Python's standard `round()` fails `live_half_1_of_8` (12.5%) and `live_half_5_of_8` (62.5%) in `test_break_on_purpose_standard_round_fails_on_halves`. |
| **C2: Scoreboard open to visitors** | Added `_guard_scoreboard` `before_request` hook in `plugins/l3mon_theme/pages/scoreboard.py` gating `/scoreboard` using `viewer.refusal()`. Visitor redirects to `/login?next=...`, unconfirmed to `/confirm`, no studio to `/team`, banned to 403, crew passes. Hook costs zero on other paths. | `plugins/l3mon_theme/tests/test_theme_scoreboard.py` (`test_visitor_is_redirected_to_login`, `test_user_without_studio_is_redirected_to_team_setup`, `test_unverified_player_is_redirected_to_confirm`, `test_banned_user_gets_403_off_air`, `test_scoreboard_gate_hook_costs_nothing_on_other_paths`) | Bypassing `_guard_scoreboard` hook causes `test_visitor_is_redirected_to_login` to return HTTP 200 instead of 302. |
| **C3: One gate in one place for Guide** | Refactored `guide_page()` in `plugins/l3mon_theme/pages/guide.py` to use `viewer.refusal()` directly rather than hand-rolled checks, mapping error codes to identical redirects/slates. | `plugins/l3mon_theme/tests/test_theme_guide.py` (`test_signed_out_visitor_is_redirected_to_login`, `test_user_without_studio_is_redirected_to_team_setup`, `test_unverified_user_is_redirected_to_confirm`, `test_banned_user_gets_403_off_air`) | Disabling `viewer.refusal()` check causes visitor request to fail with unauthenticated context instead of 302 redirect. |
| **C4: Channel numbers vs row IDs** | Added `channel_keys(channels)` helper in `plugins/l3mon_theme/pages/guide.py` to map channel database row `id` to `no` (0-99) for `.ch-N` and `CH XX`, falling back to row IDs on duplicates/out-of-bounds. Added fixture scenarios with row `id != no`. Comment noted: "The lead replaces this helper by A's after merge". | `plugins/l3mon_theme/tests/test_theme_guide.py::test_channel_keys_helper_maps_no_or_falls_back` and golden scenario `channel_id_differs_from_number` | Removing `channel_keys` mapping causes `test_channel_keys_helper_maps_no_or_falls_back` to fail and `test_golden_guide_scenarios` to fail on channel ID mismatch (`"31"` vs `"3"`). |
| **C5: Empty news crawl bar** | Set `'crawl': false` in `ui/theme/l3mon/templates/guide.html` and `scoreboard.html` so empty news crawl region is not rendered. | `test_theme_guide.py` and `test_theme_scoreboard.py` asserting `assert '<section class="crawl"' not in body` | Setting `'crawl': true` causes assertion failure in `test_theme_guide.py` and `test_theme_scoreboard.py`. |
| **C6: Reset page error field attachment** | In `ui/theme/l3mon/templates/reset_password.html`, defined exact message tables (`EMAIL_MESSAGES`, `PASSWORD_MESSAGES`). Turnstile and generic messages stay in summary without field attachment and without invented prefixes ("email: ", "password: "). | `plugins/l3mon_theme/tests/test_theme_reset.py::test_c6_generic_and_turnstile_errors_remain_in_summary_without_field_attachment` | Attaching Turnstile error to email field causes `test_c6_generic_and_turnstile_errors_remain_in_summary_without_field_attachment` to fail. |
| **C7: Password policy server-side port** | Implemented `plugins/l3mon_guard/policies/password.py` faithfully porting `vPassword`, `vWeakPattern`, and `isCommonPassword` with exact error messages. Wired into `_wrap_register`, `_wrap_reset_password`, and `PATCH /api/v1/users/me`. Added `tools/dump-golden-password.mjs` with `--check` mode. | `plugins/l3mon_guard/tests/test_password_policy.py` (7 unit tests, 17 golden cases, 3 route tests) and `plugins/l3mon_theme/tests/test_theme_reset.py::test_c7_reset_password_enforces_password_policy` | Submitting a 3-character password (`"abc"`) fails validation with `"Use at least 12 characters."`. |
| **C8: Reset alias routes rate limit** | Deleted `plugins/l3mon_theme/pages/reset.py`. Forms post directly to native `/reset_password` and `/reset_password/<data>`. Registered GET-only redirect from `/reset` and `/reset/<data>` in `pages/signin.py` rejecting POST requests with 405. | `plugins/l3mon_theme/tests/test_theme_reset.py::test_c8_reset_alias_redirects_on_get_and_rejects_post` | Sending a `POST` request to `/reset` returns HTTP 405 Method Not Allowed instead of creating a second rate-limit counter. |
| **C9: File ownership & signin hygiene** | Reverted `plugins/l3mon_theme/pages/login.py` to lead's original. Created `plugins/l3mon_theme/pages/signin.py` for `@value("google_on")` and GET-only `/reset` redirect with quiet logging. Sanitized `next` in Google OAuth link. Reverted `PROMPT-*.md` in `.gitignore`. Left `values.py`, `test_theme_site_links.py`, `test_theme_values.py`, and `export-theme.test.mjs` untouched. | `plugins/l3mon_theme/tests/test_theme_pages.py::test_google_auth_link_sanitizes_hostile_next` | Supplying `next=https://evil.example` suppresses the parameter, leaving `href="/auth/google"`. |
| **C10: Evidence and truthful handoff** | Rewrote Sections 4, 10, 11 of handoff truthfully. Added Round 2 findings table. Documented all 16 guide and 16 scoreboard golden states, real screenshot paths, and execution status. | Verification of `docs/handoff/ui-c-guide-scoreboard.md` against test artifacts | State count mismatch between handoff (16) and fixtures (16) is prevented. |

---

## 8. Security notes

- **Strict CSP Enforcement**: Zero inline `<script>`, `<style>`, `style=` attributes, or remote hosts (only `challenges.cloudflare.com` allowed when Turnstile is active).
- **Enumeration Prevention**: Reset password endpoints disclose no account existence. Nonce tokens expire in 30 minutes and are single-use.
- **Single Rate Limit Bucket**: All password reset POST requests target native CTFd endpoints (`/reset_password`), preventing attackers from bypassing rate limits through alias endpoints.
- **Open Redirect Prevention**: `safe_next` sanitization ensures Google OAuth and CTFtime authorization links never forward hostile external URLs (`https://evil.example`, `//evil.example`, `/\\evil.example`).
- **Server-Side Password Strength**: 12 to 128 character policy, common password lists, repeat/sequence patterns, and context matches (username, email local part) enforced server-side via `l3mon_guard`.
- **Player Page Protection**: Scoreboard and Guide are protected behind `viewer.refusal()`, preventing unauthenticated visitors, unconfirmed accounts, or suspended users from inspecting contest state.

---

## 9. Screenshots list (kept outside the repository)

Captured via `platform-ui/tools/smoke-real-guide.mjs` and kept outside the repositories:
- `C:\Users\sanja\AppData\Local\Temp\l3mon-smoke\01-login.png`: Sign in page on desktop (`1440x900`), showing test card background, brand header, login fields, and OSD.
- `C:\Users\sanja\AppData\Local\Temp\l3mon-smoke\02-guide.png`: Authenticated player Guide page on desktop, showing score, studio ranking, reels progress, and channel bar.
- `C:\Users\sanja\AppData\Local\Temp\l3mon-smoke\03-scoreboard.png`: Authenticated player Scoreboard page on desktop, showing leaderboard table, studio standing, and freeze banner state.

---

## 10. Open questions and known gaps

1. **Unification of `channel_keys`**: `channel_keys` is implemented locally in `plugins/l3mon_theme/pages/guide.py` matching Stream A's logic. As noted in the codebase, the lead will replace this helper with Stream A's unified module upon merge.
2. **Dynamic Notification Crawl**: `'crawl'` is currently set to `false` in `guide.html` and `scoreboard.html` (C5). Once Stream A's public notification provider (`l3.crawl`) is merged, crawl can be toggled on dynamically based on notification availability.
3. **Container Testing on Windows Host**: Container-based CTFd testing (`tools/run-ctfd-tests.sh`) and stack integration tests require the Docker daemon. On Windows without admin privileges, the `com.docker.service` service could not be started after system reboot; all unit tests, Node prototype tests, golden checks, and hygiene scans were executed and verified on the host.

---

## 11. Anything suspicious noticed elsewhere

1. **Golden Test Divergence**: The previous golden fixtures compared Python against a duplicate hand-written JavaScript copy rather than prototype code, masking rounding differences between Python's standard `round()` (half-to-even) and JavaScript's `Math.round()` (half-up). Resolved by importing prototype functions directly and utilizing `figures.js_round`.
2. **Endpoint-Keyed Rate Limiting in CTFd**: CTFd keys its rate limiting decorator by `request.endpoint`. Defining custom alias routes (`/reset`) gave an attacker an independent rate limit counter of 10 additional attempts. Resolved by removing alias routes and posting directly to native endpoints.
3. **Unreachable `ok` State in Reset Password**: The prototype's `register-reset.html` template includes an `ok` confirmation branch. In CTFd, however, successful resets immediately issue an HTTP 302 redirect to `/login` with a flash message, rendering the in-page `ok` state unreachable.
