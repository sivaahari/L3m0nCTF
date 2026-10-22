# Stream B: Registration (Account, Email Check, Studio) Handoff

Branch: `ui/b-registration` in `L3m0nCTF` and `L3m0nCTF-ui`.  
Lead contact: KabilanMohanraj17.

## 1. What Was Built

* `docs/superpowers/plans/2026-10-22-ui-b-registration.md`: Implementation plan and architectural decisions for Stream B.
* `docs/handoff/ui-b-registration.md`: Full verification, testing evidence, and handoff report.
* `plugins/l3mon_theme/pages/register.py`: Studio availability endpoint (`/api/v1/l3mon/teams/available`), routing aliases (`/register/account`, `/register/verify`, `/register/studio`, `/register/go-live`, `/register/done`), theme value providers (`l3mon.countries`, `l3mon.user_email`), and request hooks for form field mapping and random secret password injection.
* `plugins/l3mon_theme/tests/test_theme_register.py`: Complete test suite covering registration, email verify and link status pages, studio enrollment, team creation with random secret passphrase, invite token join flow, availability query budget, and guard validation rules.
* `tests/integration/test_theme_b.py`: Integration tests through nginx asserting registration, verification, enrollment pages and static asset delivery.
* `ui/theme/l3mon/templates/register.html`: Adapter template for CTFd `/register`.
* `ui/theme/l3mon/templates/confirm.html`: Adapter template for CTFd `/confirm` (email verification waiting) and `/confirm/<data>` (link status: ok, used, expired, invalid).
* `ui/theme/l3mon/templates/teams/team_enrollment.html`: Adapter template for CTFd `/team` when not enrolled.
* `ui/theme/l3mon/templates/teams/new_team.html`: Adapter template for CTFd `/teams/new` (studio creation).
* `ui/theme/l3mon/templates/teams/join_team.html`: Adapter template for CTFd `/teams/join` (studio join).
* `ui/theme/l3mon/templates/teams/invite.html`: Adapter template for CTFd `/teams/invite?code=...` (invite confirmation).
* `ui/theme/l3mon/templates/_register-account.html`: Exported Jinja template from prototype `register-account.html`.
* `ui/theme/l3mon/templates/_register-verify.html`: Exported Jinja template from prototype `register-verify.html`.
* `ui/theme/l3mon/templates/_register-link.html`: Exported Jinja template from prototype `register-link.html`.
* `ui/theme/l3mon/templates/_register-studio.html`: Exported Jinja template from prototype `register-studio.html`.
* `ui/theme/l3mon/static/bundles.json`: Theme static assets manifest including `js.register`.

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

# Export theme to L3m0nCTF
node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon

# Rebuild Docker dev image and run theme test suite
cd ../../L3m0nCTF
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme

# Run tools tests and hygiene scan
python -m pytest tools/tests -q
(cd tools && python -m l3mon hygiene --root .. --history)
```

## 3. Evidence

| Check | Command | Result | Counts | Date |
|---|---|---|---|---|
| UI: prototype suite | `cd platform-ui && node --test --test-concurrency=1 "tests/*.test.mjs"` | PASS | 339 passed, 1 skipped (0 failed) in 153.4s | 2026-10-22 |
| UI: build and budgets | `cd platform-ui && node tools/build.mjs` | PASS | All budgets met, 0 errors | 2026-10-22 |
| UI: theme export equivalence (register) | `node --test tests/export-theme-register.test.mjs` | PASS | 6 passed (account, verify, link, studio) | 2026-10-22 |
| UI: theme export core suite | `node --test tests/export-theme.test.mjs` | PASS | 16 passed | 2026-10-22 |
| UI: browser smoke tests | `node tools/smoke.mjs` | NOT RUN | Headless Chrome socket blocked in local Windows container environment (documented per prompt A6) | 2026-10-22 |
| UI: exporter determinism | Exporter run twice to independent folders | PASS | Byte-for-byte identical output | 2026-10-22 |
| Public: full theme suite on fresh image | `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme` | PASS | 79 passed, 156 warnings in 90.31s | 2026-10-22 |
| Public: tools test suite | `python -m pytest tools/tests -q` | PASS | 302 passed, 12 skipped in 37.67s | 2026-10-22 |
| Public: hygiene security scan | `(cd tools && python -m l3mon hygiene --root .. --history)` | PASS | 0 finding(s) | 2026-10-22 |

## 4. Differences from the Prompt

1. **Stepper count:** Adjusted from 4 steps to 3 steps (`Account`, `Verify`, `Studio`), dropping the review step and done page because native CTFd creates the studio upon submission and transitions directly into the challenge board / team dashboard. The post-enrollment welcome belongs to Stream D's `teams/private.html`.
2. **Server Draft Storage:** Anonymous sign-ups do not maintain incomplete draft form state on the server across unauthenticated requests; client-side validation, live check polling, show-password toggles, and state persistence are fully maintained in the browser.

## 5. Decisions and Rejected Alternatives

* **Native CTFd flow vs custom registration backend:** Ported directly onto native CTFd auth and team blueprints (`/register`, `/confirm`, `/teams/*`) rather than introducing a separate registration backend or modifying CTFd core.
* **Invite code & link dual handling:** The join input accepts both raw invite token strings (`ABCD-1234`) and complete invite URLs (`https://play.l3m0nctf.xyz/teams/invite?code=ABCD-1234`), normalizing automatically to the native invite endpoint.
* **Random secret studio passphrase:** Studios created via the wizard are automatically provisioned with a secure random passphrase (`secrets.token_hex(16)`), preventing brute-force team name/password guessing and enforcing an invite-only model.
* **Deterministic error display:** Full error messages are displayed unconditionally in the top error summary; individual field error binding is strictly deterministic to prevent misleading field attribution.

## 6. Break-on-Purpose Table

| Rule | The Break | Test That Noticed |
|---|---|---|
| Username form field mapping (`username` -> `name`) | Disabled `theme_registration_hooks` in `register.py` | `test_theme_register.py::test_register_successful_with_username_form_field` failed (400 validation error) |
| Studio availability query budget (max 1 query) | Added duplicate team lookup in `check_studio_availability()` | `test_theme_register.py::test_studio_availability_query_budget` failed (`executed 2 team queries, budget is 1`) |
| Impersonation protection against reserved names | Bypassed `check_impersonation` in `validate_team_name` | `test_theme_register.py::test_studio_creation_guard_rejections` failed (name "admin" accepted) |
| Studio name length limits (2 to 32 characters) | Removed min length check in `handle_register_studio()` | `test_theme_register.py::test_studio_creation_guard_rejections` failed (name "A" accepted) |
| Random secret password on studio creation | Allowed empty/null password on team creation | `test_theme_register.py::test_studio_creation_and_invite_joining_lifecycle` failed (`assert team.password is not None and len(team.password) > 0`) |
| Country code ISO-3166 validation | Skipped `validate_country_code` validation | `test_theme_register.py::test_studio_creation_and_invite_joining_lifecycle` noticed country case sensitivity |
| Turnstile captcha integration | Omitted `<!--include:captcha-->` from `register-account.html` | `test_theme_register.py::test_register_captcha_when_configured` failed (widget not in output) |
| Strict Content Security Policy | Injected inline `<style>` or `onclick` attribute | `test_theme_register.py::test_the_register_page_is_the_theme_and_has_csrf_and_stepper` and `test_confirm_verify_page_rendering_and_stepper` failed |

## 7. Security Notes

* **Account Enumeration:** Generic CTFd error messaging and uniform timing behavior preserved.
* **Studio Name Probing:** The `/api/v1/l3mon/teams/available` endpoint requires active player authentication and is rate-limited to 20 requests per minute per IP.
* **Content Security Policy:** Strict CSP adherence across all templates: zero inline styles, zero inline scripts, zero `javascript:` URIs, and external scripts restricted to Cloudflare Turnstile when explicitly configured.
* **Input Sanitization:** Team names, user inputs, and affiliation strings sanitized via `l3mon_guard` Unicode NFKC normalization and invisible/bidi control character removal.

## 8. Design Changes Requested

None.

## 9. Screenshots List

* `register-account-empty.png`: Step 1 account setup with username, email, password fields, rules checkbox, and Turnstile widget.
* `register-account-errors.png`: Step 1 account setup displaying top summary and field-level validation errors.
* `register-verify-waiting.png`: Step 2 email verification waiting page with mail graphic, countdown timer, and resend action.
* `register-link-status.png`: Email confirmation callback page (valid, used, expired, and invalid token states).
* `register-studio-create.png`: Step 3 studio creation form with live availability indicator and mascot preview.
* `register-studio-join.png`: Step 3 studio join form with invite code entry.
* `teams-invite-confirm.png`: Studio invite confirmation view prompting player to accept enrollment.

## 10. Open Questions and Known Gaps

None at present.

## 11. Anything Suspicious Noticed Elsewhere

None observed.
