# Handoff: Stream C (Guide, Scoreboard, Sign-in Finish, Password Reset, Account Gates)

**Stream:** C  
**Branch:** `ui/c-guide-scoreboard` in both repositories (`L3m0nCTF` and `L3m0nCTF-ui`)  
**Engineer:** sanjai063928  
**Current Milestone:** M2 (All pages end-to-end, full A6 test suites passing, theme export verified)  
**Date:** 2026-10-22  

---

## 1. What was built (every file one line)

### In `L3m0nCTF-ui`:
- `platform-ui/theme-pages/scoreboard.json`: Scoreboard theme export contract configuration.
- `platform-ui/theme-pages/register-reset.json`: Password reset and registration theme export contract configuration.
- `platform-ui/src/pages/register-reset.html`: Turnstile CAPTCHA partial inclusion directive in reset form.
- `platform-ui/tests/export-scoreboard.test.mjs`: Node test suite verifying scoreboard export equivalence across 8 prototype states.
- `platform-ui/tests/export-reset.test.mjs`: Node test suite verifying reset password export equivalence across 8 prototype states.
- `platform-ui/tests/export-theme.test.mjs`: Updated theme manifest bundle assertions for scoreboard and register bundles.
- `platform-ui/tools/dump-golden-scoreboard.mjs`: Script to generate canonical scoreboard golden fixtures for Python tests.

### In `L3m0nCTF`:
- `plugins/l3mon_theme/pages/guide.py`: View model builder and `@value` hooks (`guide`, `guide_banner`, `guide_clock`) for `/guide`.
- `plugins/l3mon_theme/pages/scoreboard.py`: View model builder and `@value` hooks (`scoreboard`, `scoreboard_banner`) for `/scoreboard`.
- `plugins/l3mon_theme/pages/reset.py`: Route aliases for `/reset` and `/reset/<data>` pointing to `auth.reset_password`.
- `plugins/l3mon_theme/pages/login.py`: Dynamic value `@value("google_on")` reading `l3mon_auth.oauth.configured()`.
- `plugins/l3mon_theme/tests/fixtures/golden_guide.json`: Canonical golden HTML fixtures for 8 guide states.
- `plugins/l3mon_theme/tests/fixtures/golden_scoreboard.json`: Canonical golden HTML fixtures for 8 scoreboard states.
- `plugins/l3mon_theme/tests/test_golden_guide.py`: Equivalence tests asserting Jinja2 render matches prototype golden HTML.
- `plugins/l3mon_theme/tests/test_golden_scoreboard.py`: Equivalence tests asserting Jinja2 render matches prototype golden HTML.
- `plugins/l3mon_theme/tests/test_theme_guide.py`: CTFd client integration tests for `/guide` (visitor, authenticated, frozen, empty states).
- `plugins/l3mon_theme/tests/test_theme_scoreboard.py`: CTFd client integration tests for `/scoreboard` (signed-out, unverified, frozen, empty states).
- `plugins/l3mon_theme/tests/test_theme_reset.py`: CTFd client integration tests for `/reset_password` and `/reset` (Turnstile, enumeration safety, token consumption).
- `plugins/l3mon_theme/tests/test_guide_query_budget.py`: Query count ceiling test for `/guide`.
- `plugins/l3mon_theme/tests/test_scoreboard_query_budget.py`: Query count ceiling test for `/scoreboard`.
- `plugins/l3mon_theme/tests/test_theme_pages.py`: Extended tests verifying Google button presence based on `google_on` state.
- `ui/theme/l3mon/templates/_guide.html`: Exported prototype template for guide.
- `ui/theme/l3mon/templates/guide.html`: Adapter template extending `_guide.html` and providing CTFd block overrides.
- `ui/theme/l3mon/templates/_scoreboard.html`: Exported prototype template for scoreboard.
- `ui/theme/l3mon/templates/scoreboard.html`: Adapter template extending `_scoreboard.html` and providing CTFd block overrides.
- `ui/theme/l3mon/templates/_register-reset.html`: Exported prototype template for reset password.
- `ui/theme/l3mon/templates/reset_password.html`: Adapter template extending `_register-reset.html` supporting token reset, Turnstile, and errors.
- `ui/theme/l3mon/templates/login.html`: Updated login template wiring Google OAuth button.
- `ui/theme/l3mon/templates/errors/401.html`: Branded Unauthorized slate matching prototype 401 state.
- `ui/theme/l3mon/templates/errors/502.html`: Branded Bad Gateway / restart slate matching prototype 502 state.
- `ui/theme/l3mon/static/guide.9fe0fae8a3.js`: Theme JavaScript bundle for guide.
- `ui/theme/l3mon/static/scoreboard.d7e0d7c4af.js`: Theme JavaScript bundle for scoreboard.
- `ui/theme/l3mon/static/register.ac2c8d8d6c.js`: Theme JavaScript bundle for reset password.
- `ui/theme/l3mon/static/bundles.json`: Theme bundle manifest registering all entrypoints.
- `docs/superpowers/plans/2026-10-22-ui-c-guide-scoreboard.md`: Plan of record and gates mapping.

---

## 2. How to run it from a clean clone

```bash
# 1. UI repository checks & tests
cd L3m0nCTF-ui/platform-ui
node --test --test-concurrency=1 "tests/*.test.mjs"
node tools/build.mjs
node tools/hygiene.mjs --no-challenges

# 2. Platform repository checks & tests
cd ../../L3m0nCTF
(cd tools && python -m l3mon hygiene --root .. --history)
python -m pytest tools/tests -q
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
```

---

## 3. Evidence (A6 checks: command, result, counts, date)

| Check | Command | Result & Counts | Date |
|---|---|---|---|
| UI prototype test suite | `cd platform-ui && node --test --test-concurrency=1 "tests/*.test.mjs"` | `tests 337, pass 336, fail 0, skipped 1` (59.7s) | 2026-10-22 |
| UI export idempotence | `node tools/export-theme.mjs --out /tmp/a && node tools/export-theme.mjs --out /tmp/b && diff -r /tmp/a /tmp/b` | Byte-for-byte identical across all 31 files | 2026-10-22 |
| UI build & budgets | `cd platform-ui && node tools/build.mjs` | All 21 page budgets pass | 2026-10-22 |
| UI hygiene scan | `cd platform-ui && node tools/hygiene.mjs --no-challenges` | `215 files checked, no problems` | 2026-10-22 |
| Platform theme tests (all) | `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme` | `94 passed, 169 warnings in 61.21s` | 2026-10-22 |
| Platform tool tests | `python -m pytest tools/tests -q` | `302 passed, 12 skipped in 11.82s` | 2026-10-22 |
| Platform hygiene scan | `(cd tools && python -m l3mon hygiene --root .. --history)` | `0 finding(s)` | 2026-10-22 |

---

## 4. Differences from this prompt, and why

1. **CTFd Password Reset Token Flow**: CTFd's core `reset_password` implementation uses cache nonces (`reset_password_<nonce>`) rather than database tokens. It terminates active user sessions (`clear_user_session(user.id)`) upon reset completion, but leaves `user.verified` unchanged. The adapter template `reset_password.html` cleanly adapts to CTFd's token handling without modifying any CTFd core files.
2. **Dual Reset URLs**: Both `/reset` and `/reset/<data>` are routed to `auth.reset_password` via `plugins/l3mon_theme/pages/reset.py:install(app)` while preserving CTFd's native `/reset_password` and `/reset_password/<data>`.

---

## 5. Decisions and rejected alternatives

1. **Pure View-Model Projection**: `plugins/l3mon_theme/pages/guide.py` and `scoreboard.py` consume pre-aggregated payloads from `l3mon_board.guide.build()` and `l3mon_board.scoreboard.read()`. They do not run additional SQL queries, maintaining strict $O(1)$ query budgets ($N \le 30$).
2. **Google Button Configuration Guard**: Rather than unconditionally rendering the Google login button, `@value("google_on")` inspects `l3mon_auth.oauth.configured()`. When unconfigured, the button and divider are omitted without breaking layout.
3. **Turnstile CAPTCHA Integration**: Reset password integrates `l3mon_guard` Turnstile CAPTCHA widget dynamically via `<!--if:captcha.sitekey--><!--include:captcha--><!--endif-->`, allowing Turnstile to be toggled on/off transparently.
4. **Standalone Gate Pages**: `errors/401.html` and `errors/502.html` provide self-contained, branded error slates matching prototype visuals with zero inline script or style violations.

---

## 6. Break-on-purpose table

| Rule | The Break | The Test That Noticed | Status |
|---|---|---|---|
| User-visible copy uses TRP, never points | Replaced "TRP" with "points" in scoreboard view model | `test_golden_scoreboard.py::test_scoreboard_golden_matches` | Caught & verified |
| Scoreboard shows only top 100 on public board | Removed ranking limit from scoreboard view builder | `test_theme_scoreboard.py::test_scoreboard_renders_leaderboard_for_authenticated_user` | Caught & verified |
| Scoreboard query budget ceiling ($N \le 30$) | Injected an N+1 query loop iterating over team solves | `test_scoreboard_query_budget.py::test_scoreboard_page_query_budget` | Caught & verified |
| Guide query budget ceiling ($N \le 30$) | Injected an unmemoized query loop across challenge rows | `test_guide_query_budget.py::test_guide_page_query_budget` | Caught & verified |
| Account enumeration guard on reset request | Returned error message "Email not found" on unknown email | `test_theme_reset.py::test_reset_post_does_not_reveal_email_existence` | Caught & verified |
| Turnstile widget suppression when disabled | Rendered Turnstile `cf-turnstile` container when unconfigured | `test_theme_reset.py::test_reset_page_renders_without_turnstile_when_unconfigured` | Caught & verified |
| Google OAuth button suppression when disabled | Rendered Google button when `l3mon_auth` unconfigured | `test_theme_pages.py::test_login_google_button_hidden_when_unconfigured` | Caught & verified |

---

## 7. Security notes

- **Strict CSP Enforcement**: Zero inline `<script>`, `<style>`, `style=` attributes, or remote hosts (only `challenges.cloudflare.com` allowed when Turnstile is active).
- **Enumeration Prevention**: Reset password endpoints disclose no account existence. Nonce tokens expire in 30 minutes and are single-use.
- **Query Budget**: Ceiling tests ensure zero database statement growth with number of challenges or teams.
- **Fail-open / Fail-closed Stance**: Turnstile CAPTCHA adheres to `l3mon_guard` policies.

---

## 8. Design changes requested from owner

None.

---

## 9. Screenshots list (kept outside the repository)

Screenshots captured from prototype server and verified across all viewport breakpoints (`360px`, `768px`, `1280px`).
Stored in temporary local inspection cache.

---

## 10. Open questions and known gaps

None. All Milestone 1 and Milestone 2 requirements are fulfilled and verified.

---

## 11. Anything suspicious noticed elsewhere

None. All linting, hygiene, and security suites pass cleanly.
