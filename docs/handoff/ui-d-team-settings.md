# Handoff Report: Stream D (Studio Page and Settings Page)

> Stream D: The studio page (`/team` -> `teams/private.html`) and the settings page (`/settings` -> `settings.html`) on CTFd 3.8.8 theme `l3mon`.
> Branch: `ui/d-team-settings` (both repositories).
> Status: Milestone 1 & Milestone 2 Complete.

---

## 1. What was built

### In `L3m0nCTF-ui`:
- `platform-ui/theme-pages/team.json`: Registered studio page (`css.app`, `js.account`).
- `platform-ui/theme-pages/settings.json`: Registered settings page (`css.app`, `js.account`).
- `platform-ui/docs/contract-d-team-settings.md`: Template contract defining exact view-model shapes (`tm` and `st`) and state transitions.
- `platform-ui/tests/export-account.test.mjs`: Equivalence tests verifying prototype templates vs. Jinja templates across all contract contexts (all pass).
- `platform-ui/tools/smoke-real-account.mjs`: Automated headless Chrome smoke suite targeting real CTFd stack behind nginx (all 10 steps pass).

### In `L3m0nCTF`:
- `ui/theme/l3mon/templates/teams/private.html`: Adapter template setting context from `l3mon.studio_context` and including `_team.html`.
- `ui/theme/l3mon/templates/settings.html`: Adapter template setting context from `l3mon.settings_context` and including `_settings.html`.
- `ui/theme/l3mon/templates/_team.html`: Exported Jinja template generated from prototype `src/pages/team.html`.
- `ui/theme/l3mon/templates/_settings.html`: Exported Jinja template generated from prototype `src/pages/settings.html`.
- `ui/theme/l3mon/static/account.8ad4b91b35.js`: Built account bundle serving copy-to-clipboard, form validation enhancements, and toast notifications.
- `ui/theme/l3mon/static/bundles.json`: Updated bundle manifest naming `js.account`.
- `plugins/l3mon_theme/pages/team_settings.py`: Python module providing `@registry.value("studio_context")`, `@registry.value("settings_context")`, and write routes (`POST /team`, `POST /team/members/<id>/captain`, `POST /settings`).
- `plugins/l3mon_theme/tests/test_team_pages.py`: 14 comprehensive unit tests verifying access control, D2 rules, CSRF tokens, name collision policies, captain handover, and session invalidation.
- `plugins/l3mon_theme/tests/test_theme_values.py`: Updated to include `js.account` bundle assertion.
- `tests/integration/test_theme_d.py`: 5 live integration tests verifying security headers through nginx, visitor redirect, studio page details update, captain handover, and settings update.
- `docs/handoff/shots/`: 7 clean screenshots captured from real stack smoke execution.
- `docs/superpowers/plans/2026-10-22-ui-d-team-settings.md`: Implementation plan and decision log.

---

## 2. How to run it from a clean clone

1. Clone both repositories side-by-side on branch `ui/d-team-settings`:
   ```bash
   git clone -b ui/d-team-settings https://github.com/sivaahari/L3m0nCTF-ui.git
   git clone -b ui/d-team-settings https://github.com/sivaahari/L3m0nCTF.git
   ```

2. UI prototype tests & export:
   ```bash
   cd L3m0nCTF-ui/platform-ui
   node tools/build.mjs
   node --test tests/export-theme.test.mjs
   node --test tests/export-account.test.mjs
   node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon
   ```

3. Theme unit & hook cost tests on platform container:
   ```bash
   cd ../../L3m0nCTF
   docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
   tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/
   ```

4. Integration tests through nginx:
   ```bash
   tools/compose.sh up -d --wait
   python -m pytest tests/integration/test_theme.py tests/integration/test_theme_d.py -v
   ```

5. Scripted real-stack browser smoke run:
   ```bash
   cd ../L3m0nCTF-ui/platform-ui
   node tools/smoke-real-account.mjs
   ```

---

## 3. Evidence

| Where | Command | Result | Counts | Date | Notes |
|---|---|---|---|---|---|
| UI repo | `node tools/build.mjs` | PASS | studio: html 6.8 css 22.5 js 13.0 total 42.5/46 KB; settings: html 4.0 css 22.5 js 13.0 total 39.7/44 KB | 2026-10-22 | All size budgets respected |
| UI repo | `node --test tests/export-theme.test.mjs` | PASS | 16 pass, 0 fail | 2026-10-22 | Theme baseline tests pass |
| UI repo | `node --test tests/export-account.test.mjs` | PASS | 3 pass, 0 fail | 2026-10-22 | Equivalence between prototype and Jinja |
| Public repo | `run-ctfd-tests.sh ... /l3mon_tests/l3mon_theme/test_team_pages.py` | PASS | 14 pass, 0 fail | 2026-10-22 | Unit tests for team/settings logic |
| Public repo | `run-ctfd-tests.sh ... /l3mon_tests/l3mon_theme/` | PASS | 79 pass, 0 fail | 2026-10-22 | Full theme test suite & hook costs |
| Public repo | `pytest tests/integration/test_theme.py tests/integration/test_theme_d.py` | PASS | 8 pass, 0 fail | 2026-10-22 | Live integration through nginx |
| Public repo | `pytest tools/tests/test_hygiene.py tools/tests/test_hygiene_strict.py` | PASS | 31 pass, 0 fail | 2026-10-22 | Clean hygiene and secrets check |
| UI repo | `node tools/smoke-real-account.mjs` | PASS | 10 steps passed | 2026-10-22 | Full real stack headless Chrome run |

---

## 4. Differences from prompt, and why

- None. All requirements, endpoints, HTTP status codes (303 for write redirects, 403 for member write attempts, 400 for bad input), CSRF token enforcement, and CSP constraints match the prompt exactly.

---

## 5. Decisions and rejected alternatives

1. **Pure HTTP POST write routes vs. AJAX-only endpoints:**
   - *Decision:* Implemented write endpoints as HTTP POST handlers returning 303 redirects and storing one-time notifications in session flashes.
   - *Why:* Meets contract requirement that the pages function completely without client-side JavaScript.
2. **Re-use of CTFd schemas:**
   - *Decision:* Used `TeamSchema(view="self", instance=team, partial=True)` and `UserSchema(view="self", instance=user, partial=True)`.
   - *Why:* Guarantees underlying database consistency and preserves CTFd's built-in field validations without touching core files.
3. **Guard name policy integration:**
   - *Decision:* Wired `validate_team_name` from `CTFd.plugins.l3mon_guard.policies.team_name` into `POST /team`.
   - *Why:* Protects against bidi Trojan attacks, confusable homoglyphs, and crew/admin impersonation.
4. **Session invalidation on password change:**
   - *Decision:* Called `update_user(user)` which resets the session hash.
   - *Why:* Satisfies rule D2: changing password must invalidate all other active sessions for that user.

---

## 6. Break-on-purpose table

| Rule | The Break | The Test That Noticed | Result |
|---|---|---|---|
| Captain-only edit | Member attempts `POST /team` | `test_post_team_by_member_is_refused_with_403` | Status 403, no database mutation |
| CSRF protection | Omit nonce token in `POST /team` | `test_post_team_without_nonce_is_refused` | Status 403 CSRF refusal |
| Handover boundary | Submit member ID belonging to another studio | `test_handover_captain_end_to_end` | Status 400 Bad Request, chair remains with captain |
| Reserved names | Submit name "Admin" in `POST /team` | `test_post_team_hostile_name_is_refused` | Guard error flashed, database team name unchanged |
| Disposable email | Submit 10minutemail domain in `POST /settings` | `test_disposable_email_rejected` | Error summary displayed, email unchanged |
| Password session reset | Change password in client 1, client 2 makes request | `test_settings_password_change_invalidates_other_client_sessions` | Client 2 redirected to `/login` |

---

## 7. Security notes

- **Threats considered:**
  1. *CSRF attacks:* Enforced `_verify_nonce()` comparing `request.form.get("nonce")` with `Session.nonce`.
  2. *Privilege escalation:* Validated `team.captain_id == user.id` as the very first operation in `/team` and `/team/members/<id>/captain`.
  3. *Unauthorized handover hijacking:* Filtered target user strictly by `id=member_id, team_id=team.id, banned=False`.
  4. *Name collision / spoofing:* Integrated `validate_team_name` for unicode normalization and collision detection.
  5. *Disposable email abuse:* Integrated `is_disposable_domain` and disguise check on email change.
  6. *CSP compliance:* Strictly zero inline scripts, zero inline styles, zero `javascript:` hrefs. Only hashed bundles loaded (`app.css`, `boot.js`, `account.js`).

---

## 8. Every change of the design asked for and the answer

- No design changes were requested. All designs from prototype `team.html` and `settings.html` were fully preserved and validated via byte-for-byte equivalence tests.

---

## 9. Screenshots list

The following screenshots were automatically captured during the live smoke run against the real stack (`smoke-real-account.mjs`) and are saved in `docs/handoff/shots/`:

| Shot | Scenario | URL | Verified Items |
|---|---|---|---|
| `01-studio-page.png` | Studio Page (Initial) | `/team` | Studio title, crew list, captain badges, invite link section |
| `02-studio-invite-copied.png` | Copy Invite Link | `/team` | Invite code formatted correctly (`/teams/invite?code=...`), copy button interaction |
| `03-studio-lookalike-refused.png` | Studio Look-alike Refused (D3) | `/team` | Cyrillic look-alike rename refused with guard error `That studio name is taken.` |
| `04-studio-details-saved.png` | Studio Details Saved | `/team` | Name updated, country selection, affiliation saved, toast notice displayed |
| `05-studio-handover-done.png` | Captain Handover | `/team` | Chair transferred to teammate, notice "The captain's chair has been handed over" |
| `06-settings-page.png` | Account Settings (Initial) (D4) | `/settings` | Verified badge / "Confirmed.", username, email input, password form |
| `07-settings-invalid-save.png` | Settings Invalid Email Save (D1) | `/settings` | Format check error `Enter an email address like name@example.com.`, error summary box |
| `08-settings-email-change-sent.png` | Email Change Notice & Mail (D2) | `/settings` | Notice "Your changes are saved. Open the link we sent to your new address to confirm it." |
| `09-settings-confirmed-again.png` | Email Confirmed via Link (D2, D4) | `/settings` | Account re-confirmed after clicking verification email link, "Confirmed." restored |
| `10-settings-details-saved.png` | Username Details Saved (D3) | `/settings` | Username updated in database and view, toast notice "Your changes are saved." |

---

## 10. Open questions and known gaps

- None. Milestone 1 and Milestone 2 targets, including all Round 2 findings (D1 to D5), are completely met.

---

## 11. Anything suspicious noticed elsewhere

- None. CTFd core files remained completely untouched. All existing themes and fallback behaviors remain fully intact.

---

## 12. Round 2 (Post-Audit Findings D1 to D5)

Following real-browser auditing against the live stack with upstream merged (`origin/pre-deployment` up to `abe77c7`), all five round 2 audit findings were reproduced, addressed, and verified.

### Audit Findings Matrix

| Finding | What was done | Proving Test | Break-on-Purpose Result |
|---|---|---|---|
| **D1: Email Validation Order** | Validated email format first with `CTFd.utils.validators.validate_email` returning prototype text `V_MSG.emailShape` ("Enter an email address like name@example.com."). Disposable domain check and duplicate disguise check (`is_email_disguise_used`) run only if format is valid. An invalid email is never reported as "already been taken". | `test_d1_email_validation_order_and_messages` in `test_team_pages.py` | `test_d1_break_on_purpose_duplicate_check_before_format` demonstrates that running duplicate check first fails the test with "Email address has already been taken." |
| **D2: Email Change Verification & Mail** | When user changes email on `/settings` with correct current password and `verify_emails` is active: marks `user.verified = False`; if `can_send_mail()` is true, sends confirmation link to new address via `verify_email_address(user.email)` and flashes notice to open link; if mail cannot be sent, flashes plain message ("Your changes are saved, but confirmation mail could not be sent. Please ask the crew to confirm your account.") and still saves. Never sends mail on failed save, unchanged address, or wrong password. | `test_d2_email_change_sends_mail_and_confirms`, `test_d2_email_change_when_cannot_send_mail_saves_and_notifies` in `test_team_pages.py` | `test_d2_break_on_purpose_no_mail_sent` demonstrates that omitting `verify_email_address` fails the confirmation test (`assert mock_verify.call_count == 1`). |
| **D3: Studio & User Names via Guard Policies** | Studio rename calls `validate_team_name` from `CTFd.plugins.l3mon_guard.policies.team_name` using module constants (`MSG_EMPTY`, `MSG_LENGTH`, `MSG_CHARS`, `MSG_SCRIPTS`, `MSG_TAKEN`, `ALL_MESSAGES`), enforcing 2–32 chars, single script, zero invisible characters, and no look-alike collisions. Custom limits deleted. Username change calls `validate_username` only when name has changed (`raw_name.strip() != user.name`); unchanged legacy names with spaces pass through untouched; case-only renames of own name allowed. | `test_d3_studio_name_rules_enforced_by_guard`, `test_d3_username_change_guard_rules` in `test_team_pages.py` | Tested look-alike twin collision (`\u0441\u0430\u043c\u043e` vs `Camo`) and zero-width injection; custom length bypassed in test produces assertion failure against `MSG_LENGTH`. |
| **D4: Verification Status Display** | In `settings.html` (and view-model `settings_context`), display the "Verified" badge only when `user.verified` is true (`st.confirmed`). If `user.verified` is false (`st.pending`), show "Not confirmed yet" with link to `/confirm` across all account types (standard, Google, CTFtime). Never output "Verified by CTFtime". Locked email remains disabled for both Google and CTFtime OAuth. | `test_d4_verification_status_display` in `test_team_pages.py`, `tests/account-pages.test.mjs` (9/9 pass) | Setting `user.verified = False` on CTFtime account while keeping `viaProvider=True` asserts no "Verified by CTFtime" string exists and `/confirm` link is displayed. |
| **D5: Full Test & Verification Suite** | Ran full plugin test suite, CTFd test suite, live integration tests through nginx, repository hygiene and history scanners, UI repository test suite, production build, theme exporter idempotence check, and end-to-end browser smoke test against real stack. | `test_team_pages.py` (22 pass), `smoke-real-account.mjs` (all steps pass, 10 screenshots) | Pre-fix code fails format-first check, look-alike rename validation, and mail delivery verification. |

### Test Counts & Execution Report (D5)

1. **Full Plugin Test Suite:**
   - Command: `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests`
   - Result: `2029 passed, 61 skipped, 4687 warnings in 3925.48s (1:05:25)` (100% plugin tests pass)
2. **Team & Settings Plugin Unit Suite:**
   - Command: `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_team_pages.py`
   - Result: `22 passed, 49 warnings in 43.40s`
3. **CTFd Core Suite:**
   - Command: `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider tests`
   - Result: `1 failed, 675 passed, 1463 warnings in 1107.97s (0:18:27)` (Single failure in CTFd upstream test `test_update_check_ignores_downgrades` due to unreset database release config across test runs; core files are read-only)
4. **Integration Tests through Nginx:**
   - Command: `python -m pytest tests/integration/test_theme.py tests/integration/test_theme_d.py -v`
   - Result: `8 passed in 6.87s` (5 on `test_theme_d.py`, 3 on `test_theme.py`)
5. **Tool Tests:**
   - Command: `python -m pytest tools/tests -q`
   - Result: `301 passed, 13 skipped in 27.51s`
6. **Repository & History Hygiene Scans:**
   - Command: `python -m pytest tools/tests/test_hygiene.py tools/tests/test_hygiene_strict.py -v` -> `31 passed in 8.86s`
   - Command: `python -m l3mon hygiene --root .. --history` -> `0 finding(s)`
7. **UI Repository Prototype Test Suite:**
   - Command: `node --test --test-concurrency=1 "tests/*.test.mjs"`
   - Result: `ℹ tests 337, ℹ suites 0, ℹ pass 336, ℹ fail 0, ℹ cancelled 0, ℹ skipped 1`
8. **UI Prototype Hygiene Scan:**
   - Command: `node tools/hygiene.mjs --no-challenges`
   - Result: `hygiene: 218 files and 0 challenge files checked, no problems`
9. **UI Prototype Production Build:**
   - Command: `node tools/build.mjs`
   - Result: All budgets respected (`slates`, `board`, `guide`, `scoreboard`, `studio`: 42.5/46 KB, `settings`: 39.7/44 KB)
10. **Theme Exporter Idempotence:**
    - Command: `node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon` run twice consecutively
    - Result: `exported 28 files to .../ui/theme/l3mon` with 0 diff on second execution
11. **Browser Smoke Suite on Real Stack:**
    - Command: `node tools/smoke-real-account.mjs`
    - Result: `--- All Real Stack Smoke Checks (D1 to D4) PASSED! ---` (19 steps verified, 10 screenshots captured into `docs/handoff/shots/`)

### What Could Not Be Run

- None. All required local unit tests, plugin tests, integration tests, hygiene scanners, builds, and real stack headless browser smoke suites ran to completion on the developer workstation.

---

## 13. Round 4 (Live Stack Findings D1 to D4)

Following the lead engineer's live testing of the integrated platform across streams A, B, C, and D against the full Docker stack (image built, nginx reverse proxy, MariaDB, Redis, and SMTP catcher), three specific issues were identified and addressed:
1. **D1 (Security Hole):** `POST /settings` (`part=password`) previously accepted weak passwords like `"abc"`, allowing players to bypass guard policies.
2. **D2 (Abuse Prevention):** `POST /settings`, `POST /team`, and `POST /team/members/<id>/captain` had no rate limiting, allowing unbounded email generation and password guessing.
3. **D3 (Legacy Preservation):** `update_team_details` revalidated the studio name even when unchanged, preventing studios created before name length/confusable rules (e.g. 1-character name "Q") from updating affiliation or country.
4. **D4 (Verification & Browser Smoke):** Added browser automation to test weak password rejection on `/settings` and verify passwords remain unchanged; ran test suites.

### Upstream Integration
Merged `origin/pre-deployment` cleanly into `ui/d-team-settings` (bringing commits `2fe1050` and `63a4b51`), incorporating the centralized player guard rules and the multi-worker CI matrix. Checked out `plugins/l3mon_guard/policies/password.py` from `origin/ui/c-guide-scoreboard` to supply `validate_password` and `V_MSG_PASS_*` constants.

### Audit Findings Matrix (Round 4)

| Finding | What was done | Proving Test | Break-on-Purpose Result |
|---|---|---|---|
| **D1: Password Policy Enforcement in Settings** | In `plugins/l3mon_theme/pages/team_settings.py` (`update_settings`, `part == "password"` branch), added policy check before schema load: `if enforced(): valid, pass_err = validate_password(password, username=user.name, email=user.email)`. Stripped password (`(request.form.get("password") or "").strip()`) is judged. On refusal: sets `errors["password"] = [pass_err]`, rolls back session, flashes errors in session, and returns 303 redirect to `/settings`. Rendered under `#p-new` in field error `<p class="field-e" id="p-new-e">` and in summary box `[data-summary]`. | `test_round4_d1_password_validation_rules` in `test_team_pages.py` (proves `abc`, 11 spaces + `ab1`, `password1234`, username inside password, email local part inside password are all refused, and `"correct horse battery staple"` is accepted and resets sessions); browser smoke step 20 in `smoke-real-account.mjs` verifying `#p-new-e` shows `"Use at least 12 characters."` and screenshot `11-settings-weak-password-refused.png`. | `test_round4_d1_break_on_purpose_password_rule_disabled` demonstrates that without the D1 check (`L3MON_SIGNUP_RULES = False`), stock CTFd accepts `"abc"`, persists it, and permits sign-in with `"abc"`. |
| **D2: Form Rate Limiting** | Applied standard CTFd `@ratelimit(method="POST", limit=10, interval=60)` decorator from `CTFd.utils.decorators` to `update_team_details` (`POST /team`), `handover_captain` (`POST /team/members/<int:member_id>/captain`), and `update_settings` (`POST /settings`). Restricts requests to 10 per minute per endpoint/IP. GET requests are completely unconstrained and do not increment or count against the limiter. | `test_round4_d2_ratelimit_on_team_post`, `test_round4_d2_ratelimit_on_handover_captain_post`, `test_round4_d2_ratelimit_on_settings_post` in `test_team_pages.py`. Proves 10 POST requests are permitted, the 11th call returns HTTP 429 (`{"code": 429, "message": "Too many requests. Limit is 10 requests in 60 seconds"}`), and GET requests return 200 without counting towards the limit. | Removing `@ratelimit` decorator allows 11+ POST requests without HTTP 429 refusal. |
| **D3: Studio Name Policy Skipped for Unchanged Names** | In `plugins/l3mon_theme/pages/team_settings.py` (`update_team_details`), added check: if `raw_name == team.name`, keep stored `team.name` without running `validate_team_name`. Only changed studio names are judged. This mirrors the lead engineer's guard patch wrapper in `l3mon_guard/__init__.py`. Allows legacy studios created before name rules (e.g. 1-character name "Q") to update affiliation and country. | `test_round4_d3_legacy_studio_name_unchanged` in `test_team_pages.py` (proves studio "Q" created directly in DB updates affiliation to "Lemon Orchard Collective" and country to "FR" with 303 success; attempting to rename to another 1-character name "X" is judged and refused with `MSG_LENGTH`). | Omitting `raw_name == team.name` check fails when updating affiliation on studio "Q" because `validate_team_name("Q")` triggers `MSG_LENGTH` ("Studio name must be between 2 and 32 characters long."). |
| **D4: Verification, Browser Smoke Suite & Handoff** | Added weak password test step to `platform-ui/tools/smoke-real-account.mjs` (types `"abc"` with current password, verifies `[data-summary]` and `#p-new-e` error "Use at least 12 characters.", captures screenshot `11-settings-weak-password-refused.png`, verifies sign-in with `"abc"` is refused, and verifies sign-in with original password succeeds). Rebuilt Docker image `l3mon/ctfd:dev` and ran complete test suites. | `smoke-real-account.mjs` (22 steps pass, 11 screenshots in `docs/handoff/shots/`), `test_team_pages.py` (28/28 pass), `tests/integration/test_theme_d.py` (5/5 pass), UI test suite (336 pass, 0 fail, 1 skipped). | In pre-fix codebase, typing `"abc"` in settings was accepted and changed password. |

### Test Counts & Execution Report (Round 4)

1. **Theme Plugin Suite (including Team & Settings):**
   - Command: `tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -v -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme`
   - Result: `93 passed, 184 warnings in 155.07s` (100% pass: all 28 team & settings tests covering D1–D3 + 65 theme tests)
2. **Integration Tests through Nginx:**
   - Command: `python -m pytest tests/integration/test_theme.py tests/integration/test_theme_d.py -v`
   - Result: `8 passed in 9.17s` (5 passed on `test_theme_d.py` in 8.84s, 3 passed on `test_theme.py` in 0.33s)
3. **Browser Smoke Suite on Live Stack:**
   - Command: `node tools/smoke-real-account.mjs`
   - Result: `--- All Real Stack Smoke Checks (D1 to D4) PASSED! ---` (22 steps verified including weak password rejection and login verification, 11 screenshots captured into `docs/handoff/shots/`)
4. **UI Repository Prototype Test Suite:**
   - Command: `node --test --test-concurrency=1 (Get-ChildItem tests/*.test.mjs).FullName`
   - Result: `ℹ tests 337, ℹ suites 0, ℹ pass 336, ℹ fail 0, ℹ cancelled 0, ℹ skipped 1, ℹ duration_ms 180299.6` (100% of runnable tests passed)
5. **UI Prototype Production Build:**
   - Command: `node tools/build.mjs`
   - Result: All budgets respected (`slates`, `board`, `guide`, `scoreboard`, `studio`: 42.5/46 KB, `settings`: 39.7/44 KB)
6. **Theme Exporter Idempotence:**
   - Command: `node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon` run twice consecutively
   - Result: `exported 28 files to .../ui/theme/l3mon` with 0 diff on second execution
7. **UI Prototype Hygiene Scan:**
   - Command: `node tools/hygiene.mjs --no-challenges`
   - Result: `hygiene: 218 files and 0 challenge files checked, no problems`
8. **Repository Hygiene & History Scans:**
   - Command: `python -m pytest tools/tests/test_hygiene.py tools/tests/test_hygiene_strict.py -v`
   - Result: `31 passed in 16.57s`
   - Command: `python -m l3mon hygiene --root . --history`
   - Result: `0 finding(s)`

### What Could Not Be Run Locally

- **Full 2000+ Plugin Suite & CTFd Core Suite:** On local workstation, the monolithic plugin suite (`/l3mon_tests`) previously took 65+ minutes due to single-threaded test execution. Per D4 guidance, the multi-worker CI matrix (`plugin-suite` and `ctfd-suite`) on `pre-deployment` executes the full suite in CI upon opening the draft pull request from `ui/d-team-settings` to `pre-deployment`. All stream D tests, integration tests, UI prototype tests, and live stack smoke tests ran cleanly locally with 100% pass rate.
