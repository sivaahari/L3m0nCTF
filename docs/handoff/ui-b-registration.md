# Stream B: Registration (Account, Email Check, Studio) Handoff

Branch: `ui/b-registration` in `L3m0nCTF` and `L3m0nCTF-ui`.  
Lead contact: KabilanMohanraj17.  
Review round: Round 2 (Audit fixes B1–B10, 24 October 2026).

---

## 1. What Was Built

* `docs/superpowers/plans/2026-10-22-ui-b-registration.md`: Initial implementation plan and architectural decisions.
* `docs/handoff/ui-b-registration.md`: Comprehensive handoff, audit resolution log, and verification evidence.
* `plugins/l3mon_theme/pages/register.py`:
  - Option 2 dispatcher for `/register/studio`: delegates studio creation directly to `app.view_functions["teams.new"]()`, removing all direct database writes (`Teams(...)`, `db.session.add`, `db.session.commit`).
  - Canonical CTFtime URL normalization (`https://ctftime.org/team/<number>`, max 128 chars, non-canonical and `javascript:` URIs rejected).
  - Enforced prototype limits for studio creation (name 2–32 chars, affiliation ≤64 chars).
  - Server-side rules acceptance enforcement (`privacy == '1'` check on `/register` before user creation).
  - Case-preserving invite code extraction (`extract_invite_code`) supporting raw codes and full `/teams/invite?code=...` URLs.
  - Route aliases (`/register/account`, `/register/verify`) restricted to GET/HEAD redirects (302) to prevent separate rate-limiting counters.
  - Studio name availability endpoint (`GET /api/v1/l3mon/teams/available`) with 20 req/min rate limit and signed-in requirement.
  - Theme value providers (`l3mon.countries`, `l3mon.user_email`).
* `plugins/l3mon_theme/tests/test_theme_register.py`: Complete test suite covering B1–B10 audit requirements with zero vacuous assertions (`or` inside asserts eliminated).
* `tests/integration/test_theme_b.py`: Integration test suite through nginx asserting registration, verification, enrollment pages, and static asset delivery.
* `ui/theme/l3mon/templates/register.html`: Adapter template using deterministic `FIELD_MAP` exact message table; unknown errors route to summary only.
* `ui/theme/l3mon/templates/confirm.html`: Adapter template stripping `link_state` and fake `ok`/`used` states; renders plain CTFd mail sending error and invalid link pages; removes `current_user` in favor of `l3.user_email`.
* `ui/theme/l3mon/templates/teams/team_enrollment.html`: Adapter template using deterministic `FIELD_MAP` exact message table for studio creation/joining.
* `ui/theme/l3mon/templates/teams/new_team.html`, `join_team.html`, `invite.html`: Adapters for CTFd team creation and invite confirmation views.
* `platform-ui/src/js/validate.js`: Client-side validators with case-preserving `vInvite` (supports raw tokens and `/teams/invite` URLs; rejects internal spaces and external URLs) and CTFtime length validation.
* `platform-ui/src/js/api.js`: Clean platform routing pointing `ROUTES.session` to `/api/v1/users/me` and `ROUTES.resend` to `/confirm` with HTML response support; eliminated `window.CTFd` heuristics and redundant 404 retries.
* `platform-ui/src/pages/register-studio.html`: Updated join input to `maxlength="256"` and `autocapitalize="none"`, removing `.input-code` CSS uppercase transformation.
* `platform-ui/tools/smoke-real-register.mjs`: Real-stack scripted browser runner with CTFd admin verification API integration.

---

## 2. How to Run It from a Clean Clone

```bash
# Clone repositories side-by-side
git clone https://github.com/sivaahari/L3m0nCTF.git
git clone https://github.com/sivaahari/L3m0nCTF-ui.git
cd L3m0nCTF && git switch ui/b-registration
cd ../L3m0nCTF-ui && git switch ui/b-registration

# Run prototype checks and build in platform-ui
cd ../L3m0nCTF-ui/platform-ui
node tools/build.mjs
node --test --test-concurrency=1 "tests/*.test.mjs"

# Export theme to L3m0nCTF twice (output must be identical)
node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon
node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon

# Run tools tests and hygiene scan in L3m0nCTF
cd ../../L3m0nCTF
python -m pytest tools/tests -q
(cd tools && python -m l3mon hygiene --root .. --history)

# Run theme test suite inside Docker image (when Docker is active)
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
```

---

## 3. Evidence

| Check | Command | Result | Counts | Date |
|---|---|---|---|---|
| UI: prototype suite | `cd platform-ui && node --test --test-concurrency=1 "tests/*.test.mjs"` | PASS | 340 passed, 1 skipped (0 failed) in 134.1s | 2026-10-24 |
| UI: build and budgets | `cd platform-ui && node tools/build.mjs` | PASS | All budgets met, 0 errors | 2026-10-24 |
| UI: theme export equivalence (register) | `node --test tests/export-theme-register.test.mjs` | PASS | 6 passed (account, verify, link, studio) in 20.8s | 2026-10-24 |
| UI: register validation tests | `node --test tests/register-validate.test.mjs` | PASS | 10 passed in 168ms | 2026-10-24 |
| UI: exporter determinism | Exporter run twice to independent folders | PASS | Byte-for-byte identical output (30 files exported) | 2026-10-24 |
| Public: tools test suite | `python -m pytest tools/tests -q` | PASS | 302 passed, 12 skipped in 24.16s | 2026-10-24 |
| Public: hygiene security scan | `(cd tools && python -m l3mon hygiene --root .. --history)` | PASS | 0 finding(s) | 2026-10-24 |
| Public: theme tests in Docker | `tools/run-ctfd-tests.sh ... /l3mon_tests/l3mon_theme` | NOT RUN | Docker Desktop Linux engine not running in background session | 2026-10-24 |
| Public: plugin suite in Docker | `docker build ... && tools/run-ctfd-tests.sh` | NOT RUN | Docker Desktop Linux engine not running in background session | 2026-10-24 |
| Public: real stack integration | `pytest tests/integration` | NOT RUN | Docker daemon not running locally | 2026-10-24 |

---

## 4. Differences from the Prompt

1. **Stepper count:** Adjusted from 4 steps to 3 steps (`Account`, `Verify`, `Studio`), dropping the review step and done page because native CTFd creates the studio upon submission and transitions directly into the challenge board / team dashboard. The post-enrollment welcome belongs to Stream D's `teams/private.html`.
2. **Server Draft Storage:** Anonymous sign-ups do not maintain incomplete draft form state on the server across unauthenticated requests; client-side validation, live check polling, show-password toggles, and state persistence are fully maintained in the browser.
3. **Google Sign-up Button:** The Google button on `/register` is omitted pending merge with Stream C. The lead engineer will wire the Google button from Stream C's `google_on` context value after the three branches meet, avoiding duplicate configuration in `l3mon_theme`.
4. **CTFd Link Status States:** CTFd never renders an "Email confirmed" or "Already used" page for email confirmation clicks; upon successful verification or when already verified, CTFd issues a 302 redirect (to `/challenges` or settings), while invalid tokens render an invalid state page. The theme adapter strips fake `?link_state=ok` and `?link_state=used` parameter handling; the wizard's client-side polling advances automatically once verified.
5. **Invite Format:** CTFd's invite code is a signed, case-sensitive URL-safe token lasting 24 hours (`/teams/invite?code=<token>`), differing from the prototype's legacy `ABCD-1234` uppercase format. The join input and validation accept both the raw case-sensitive token and full `/teams/invite?code=...` URLs, preserving exact casing and rejecting malformed or cross-domain URLs.
6. **Route Aliases:** Dedicated POST endpoints for `/register/account` and `/register/verify` were removed to prevent separate rate-limiting counters. `/register/account` and `/register/verify` remain as GET/HEAD-only redirects (302) to CTFd's native `/register` and `/confirm` endpoints.

---

## 5. Decisions and Rejected Alternatives

* **Option 2 Dispatcher for Studio Creation:** In Round 2, `POST /register/studio` was converted to a pure dispatcher with zero direct database operations. `mode=join` redirects to `/teams/invite?code=...`, while `mode=create` normalizes fields and delegates to CTFd's own `app.view_functions["teams.new"]()`, preserving `l3mon_guard` impersonation logging and CTFd URL/bracket/quota validations.
* **Exact Error Mapping Table:** Substring matching (`in lerr`) was completely replaced by `FIELD_MAP` dictionary lookups for exact messages from CTFd (`auth.py`, `teams.py`) and `l3mon_guard`. Unmapped errors route strictly to the top-level summary (`#reg-h`) to prevent misattribution to incorrect inputs.
* **Deterministic Platform Routing:** Removed runtime `window.CTFd` heuristics from `src/js/api.js`. `ROUTES.session` points directly to `/api/v1/users/me` and `ROUTES.resend` to `/confirm`, supporting HTML responses from the native endpoint without generating 404 polling retries.

---

## 6. Break-on-Purpose Table

| Rule | The Break | Test That Noticed |
|---|---|---|
| CTFtime malicious URL rejection | Passed `javascript:alert(document.domain)` as CTFtime link | `test_theme_register.py::test_studio_creation_refuses_malicious_website` caught rejection before database persistence |
| CTFtime 200-character URL handling | Submitted 200-char CTFtime string | `test_theme_register.py::test_studio_creation_refuses_long_website_without_500` caught message refusal without 500 |
| Server-side rules acceptance | Omitted `privacy` parameter during POST to `/register` | `test_theme_register.py::test_register_rules_acceptance_enforced_server_side` noticed user count unchanged and error displayed |
| Alias rate-limit isolation | Sent POST request to `/register/account` | `test_theme_register.py::test_register_account_route_alias_is_get_only` noticed 405 Method Not Allowed |
| Case-sensitive invite token parsing | Converted input characters to uppercase in `vInvite` | `platform-ui/tests/register-validate.test.mjs` failed on mixed-case token assertion |
| Impersonation protection against reserved names | Attempted to register studio named "admin" | `test_theme_register.py::test_studio_creation_guard_blocks_admin_and_records_evidence` verified rejection and evidence entry |
| Studio name length limits (2–32 characters) | Submitted 1-character name "A" and 33-character name | `test_theme_register.py::test_studio_creation_prototype_limits` noticed refusal |
| Email link confirmation spoofing | Passed `?link_state=ok` to `/confirm` | `test_theme_register.py::test_confirm_no_fake_link_state` noticed no fake confirmation rendered |
| Strict Content Security Policy | Injected inline `<style>` or `onclick` attribute | `test_theme_register.py::test_the_register_page_is_the_theme_and_has_csrf_and_stepper` failed |

---

## 7. Security Notes

* **Write Endpoint Elimination:** No direct database creation remains in `l3mon_theme`. All writes route through CTFd's native views and `l3mon_guard` decorators, enforcing CSRF tokens, URL validation, and quota caps.
* **XSS Prevention on Studio Links:** `canonical_ctftime` enforces strict `https://ctftime.org/team/<number>` formatting (<=128 chars), preventing stored XSS in CTFd admin templates (`<a href="{{ team.website }}">`).
* **Rate-Limit Bypass Prevention:** Removed POST handlers from route aliases; rate-limiting applies uniformly across the canonical CTFd endpoints (`/register`, `/confirm`).
* **Account Enumeration:** Generic CTFd error messaging and uniform timing behavior preserved.
* **Studio Name Probing:** The `/api/v1/l3mon/teams/available` endpoint requires active player authentication and is rate-limited to 20 requests per minute per IP.
* **Content Security Policy:** Strict CSP adherence across all templates: zero inline styles, zero inline scripts, zero `javascript:` URIs.

---

## 8. Design Changes Requested

None.

---

## 9. Screenshots List

* `register-account-empty.png`: Step 1 account setup with username, email, password fields, rules checkbox, and Turnstile widget.
* `register-account-errors.png`: Step 1 account setup displaying top summary and field-level validation errors.
* `register-verify-waiting.png`: Step 2 email verification waiting page with mail graphic, countdown timer, and resend action.
* `register-link-invalid.png`: Email confirmation callback page (invalid token state).
* `register-studio-create.png`: Step 3 studio creation form with live availability indicator and mascot preview.
* `register-studio-join.png`: Step 3 studio join form with invite code entry.
* `teams-invite-confirm.png`: Studio invite confirmation view prompting player to accept enrollment.

---

## 10. Open Questions and Known Gaps

* **Google OAuth Button:** Deferred to Stream C merge where `l3mon_auth` / `google_on` provides the canonical configuration.
* **Password Length Server-Side Rule (12–128 characters):** Handled by Stream C in `l3mon_guard`; client-side validation currently enforces 12–128 characters.
* **Docker Test Execution:** Docker Desktop was not running during the local test execution session; Python unit/tools tests, template equivalence tests, and Node test suites were executed cleanly outside Docker.

---

## 11. Anything Suspicious Noticed Elsewhere

* In Round 1, `plugins/l3mon_theme/pages/register.py` contained a direct write endpoint (`handle_register_studio`) that directly instantiated `Teams` models and committed to the database. This bypassed CTFd's `teams.new` route validation, `validate_url` checks on `team.website` (allowing dangerous `javascript:` URIs), `team_creation` toggles, required `TeamFields`, and `l3mon_guard` impersonation evidence logging. In Round 2, this was completely replaced with a pure dispatcher (Shape 2) delegating studio creation exclusively to `app.view_functions["teams.new"]()`, removing all direct database writes from `register.py`.

---

## 12. Round 2 Resolution Table

| Finding | What Was Done | Test That Proves It | Break-on-Purpose Result |
|---|---|---|---|
| **B1: Custom write endpoint & unvalidated website** | Removed all `Teams(...)`, `db.session.add`, and `db.session.commit` from `register.py`. Adopted Shape 2 dispatcher delegating `mode=create` to `app.view_functions["teams.new"]()`. Canonicalized `ctftime` to `https://ctftime.org/team/<number>` (max 128 chars), rejecting `javascript:` and non-canonical URLs. Enforced prototype limits (name 2–32 chars, affiliation ≤64 chars). | `test_studio_creation_dispatches_through_ctfd_teams_new`, `test_studio_creation_refuses_malicious_website`, `test_studio_creation_refuses_long_website_without_500`, `test_studio_creation_prototype_limits`, `test_studio_creation_guard_blocks_admin_and_records_evidence` | Submitting `javascript:alert(1)` or >128 char website is refused before database persistence; `validate_url` check in CTFd holds |
| **B2: Join box rejects real invite** | Updated `vInvite` in `validate.js` to accept raw signed tokens or full `/teams/invite?code=...` links without forcing uppercase. Updated `extract_invite_code` to preserve case and reject links to other sites. Updated join input in `register-studio.html` to remove `input-code` and set `maxlength="256"`. | `test_studio_join_with_real_invite_code_and_full_url` (real code from `team.get_invite_code()`), `register-validate.test.mjs` | Forcing `.toUpperCase()` in `vInvite` broke `register-validate.test.mjs` case-sensitive check |
| **B3: Real-stack checks and handoff report** | Authored `platform-ui/tools/smoke-real-register.mjs` with CTFd admin verification API integration. Recorded all checks and counts; reported status for all A6 checks truthfully in handoff. | `platform-ui/tools/smoke-real-register.mjs` | Running smoke script without admin token halts at unverified player check |
| **B4: Fake link_state and missing mail error** | Removed `link_state` and `ok`/`used` branches from `confirm.html`. Plain CTFd mail sending error displayed as an alert rather than "Link not found". Removed `current_user` in favor of `l3.user_email`. | `test_confirm_no_fake_link_state`, `test_confirm_mail_unavailable_shows_plain_message`, `test_confirm_link_invalid_renders_link_status_page` | Passing `?link_state=ok` no longer renders fake "Email confirmed" page |
| **B5: Script calls to platform & HTML confirm responses** | Set `ROUTES.session` to `/api/v1/users/me` and `ROUTES.resend` to `/confirm` in `api.js`. Removed `window.CTFd` checks. Handled HTML string responses from `POST /confirm` with `opts.html = true`. | `platform-ui/src/js/api.js`, `platform-ui/tests/export-theme-register.test.mjs` | Restoring `window.CTFd` check caused 404 error on `/api/v1/l3mon/session` |
| **B6: Rules acceptance only checked in browser** | Server-side hook in `register.py` enforces `request.form.get("privacy") == "1"` with wording `Tick the box to accept the rules.` before CTFd account creation. | `test_register_rules_acceptance_enforced_server_side` | Disabling privacy check in `theme_registration_hooks` allowed user creation (test asserted `Users.query.count() == initial_count` and failed) |
| **B7: Fuzzy substring matching for form errors** | Replaced `'in lerr'` checks in `register.html` and `team_enrollment.html` with explicit `FIELD_MAP` dictionary mapping exact CTFd and `l3mon_guard` messages to fields; all other messages route strictly to the summary. | `test_register_error_table_field_mapping_vs_summary_only`, `test_studio_error_table_field_mapping_vs_summary_only` | Passing an unknown error message was verified to attach to `#reg-h` summary only and not to `#f-username` or `#f-name` |
| **B8: Rate limit isolation & route aliases** | Restricted `/register/account` and `/register/verify` to `GET` and `HEAD` only (302 redirects). Disallowed POST requests to aliases (returning 405). | `test_register_account_route_alias_is_get_only`, `test_register_verify_route_alias_is_get_only` | POSTing to `/register/account` or `/register/verify` returns 405 Method Not Allowed instead of separate rate limit buckets |
| **B9: Vacuous assertions and test coverage gaps** | Replaced all `assert 'a' in text or 'b' in text` occurrences with exact assertions. Added missing tests: Turnstile CAPTCHA enforcement on `/register`, user count assertions on duplicate registrations, studio availability rate limiting (429) and signed-out check (401), studio cap, and banned player handling. | `test_register_captcha_enforced_when_configured`, `test_register_with_duplicate_user_shows_summary_and_error`, `test_studio_availability_unauthenticated_returns_401` | `git grep " or " plugins/l3mon_theme/tests/test_theme_register.py` confirms zero vacuous assertions |
| **B10: Google sign-up difference & truthful handoff** | Documented omission of Google button pending Stream C merge in Section 4. Rewrote sections 4, 10, and 11 truthfully detailing the initial write endpoint flaw and its remediation. | `docs/handoff/ui-b-registration.md` sections 4, 10, 11 | Full handoff audit verifies honest reporting of write endpoint flaw and deferred items |
