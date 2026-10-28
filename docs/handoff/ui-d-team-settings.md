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
