# Stream D: Studio Page and Settings Page (Implementation Plan)

> Implementation plan for Stream D of L3m0nCTF 2026: The studio page (`/team` -> `teams/private.html`) and the settings page (`/settings` -> `settings.html`). Built test-first, milestone by milestone. All rules adhere to L3m0nCTF 2026 platform standards and prompt rules.

**Goal:** Deliver the approved visual and functional design for the studio page and settings page on CTFd 3.8.8 (theme `l3mon`), including byte-for-byte template equivalence, CTFd schema-validated write endpoints, strict CSRF and permission enforcement, low query budgets, and full integration test coverage.

---

## 1. What was read and re-checked in the codebase

Re-checked each fact in the repository and live `l3mon/ctfd:dev` image as required by prompt D2:

1. **Invite Code Mechanics (Prompt D2, fact 1):**
   - `CTFd/models/__init__.py:808-816`: `Teams.get_invite_code()` uses `URLSafeTimedSerializer(secret_key, salt="team-invite-code")`. Signs `[self.id, self.password]`.
   - Valid for 86,400 seconds (1 day). It does not access the database and cannot be cancelled before expiration.
   - `CTFd/teams.py:91-119`: Route `/teams/invite?code=<code>` verifies token and adds user if `team_size` (4) not exceeded and user is not already in a team.
   - Invite URL is constructed dynamically via `https://<platform_host>/teams/invite?code=<code>` using `l3mon.site_url`.

2. **Captain-Only Permissions (Prompt D2, fact 2):**
   - `CTFd/api/v1/teams.py:270-277` (`TeamPrivate.patch`): Explicitly checks `team.captain_id != session["id"]` and returns 403 `{"errors": {"": ["Only team captains can edit team information"]}}`.
   - `CTFd/api/v1/teams.py:461-468` (`TeamPrivateMembers.post`): Checks `team.captain_id != session["id"]` and returns 403 `{"errors": {"": ["Only team captains can generate invite codes"]}}`.
   - Our write views `POST /team` and `POST /team/members/<id>/captain` must enforce this check before any processing.

3. **Handing Over Captain's Chair (Prompt D2, fact 2 & D3):**
   - `CTFd/schemas/teams.py:249-270`: `TeamSchema(view="self", instance=team, partial=True)` accepts `captain_id`.
   - Core CTFd does not validate whether `captain_id` belongs to the team in `TeamSchema`. Therefore, our endpoint `POST /team/members/<id>/captain` MUST explicitly assert `target_user.team_id == team.id` and that the target is an active, unbanned member. Hand-over attempts for users outside the team must be refused (400/403) with no change made.

4. **No Member Removal or Leave Routes (Prompt D2, fact 3):**
   - `CTFd/api/v1/teams.py:495-515`: Only admin route `DELETE /api/v1/teams/<team_id>/members` exists.
   - The UI specification deliberately displays: "To have someone removed, ask the crew." No player route for leaving or removing is created.

5. **Email Verification on Email Change (Prompt D2, fact 4):**
   - `CTFd/schemas/users.py:126-136`: `UserSchema.validate_email` requires current password in `confirm` field when updating email.
   - `CTFd/api/v1/users.py:175-188`: When email changes and `verify_emails` config is enabled, user is marked `verified = False` and CTFd dispatches a confirmation email. Channels remain inaccessible to unverified accounts.

6. **Password Change and Session Invalidation (Prompt D2, fact 5):**
   - `CTFd/schemas/users.py:141-152`: Changing password requires `confirm` (current password) and valid new password (minimum 10 characters).
   - `CTFd/api/v1/users.py:183`: Calls `update_user(user)` which updates `session["hash"] = hash_password(user.password)`.
   - `CTFd/utils/user/__init__.py:authed()` verifies `session["hash"] == user.password` on every request. Any other active session on other devices is invalidated immediately.

7. **Studio Passwords (Prompt D2, fact 6):**
   - In accordance with Stream B's architecture, studios are assigned secure random passwords. Neither the studio page nor settings page prompts for or shows studio passwords.

8. **Country Lists (Prompt D4):**
   - `CTFd/utils/countries.py:SELECT_COUNTRIES_LIST` contains 254 ISO 3166-1 alpha-2 country tuples + 1 empty option.
   - `platform-ui/server/validate.mjs:107-126` contains the matching 254 country codes and short names.

9. **Score and Standing (Prompt D4):**
   - `plugins/l3mon_board/guide.py:44`: Computes `board.numbers(team, phase)` -> `(score, place, of)`.
   - Score is live TRP; place is `None` when frozen or score <= 0 ("Ranks frozen" / "Not ranked yet").

10. **Guard Policies (Prompt D3):**
    - `plugins/l3mon_guard/policies/team_name.py`: `validate_team_name` handles confusable characters, bidi/zero-width stripping, reserved words, and canonical collision.
    - `plugins/l3mon_guard/policies/disposable.py`: `is_disposable_domain` rejects disposable email providers.
    - `plugins/l3mon_guard/policies/email_norm.py`: `is_email_disguise_used` prevents duplicate/disguised email registration.

---

## 2. Key Decisions and Alternatives Rejected

| Topic | Options | Choice | Rationale |
|---|---|---|---|
| **Form Submission Pattern (D3)** | (a) Client-side JavaScript calling CTFd JSON API<br/>(b) Plain HTML form POST to dedicated server endpoints with redirect + flash | **(b)** Dedicated server POST views | Matches the approved design: forms function 100% without client-side JavaScript, consistent with sign-in and registration pages. Allows server-side flash message handling and prevents form resubmission on refresh. |
| **Validation Architecture** | (a) Write custom validation logic in views<br/>(b) Re-use CTFd's schemas (`TeamSchema`, `UserSchema`) + `l3mon_guard` policies | **(b)** Re-use CTFd schemas + `l3mon_guard` | Rule A3.5 & D3: Reuses CTFd's battle-tested schemas for name uniqueness, password confirmation, and length rules. Integrates `l3mon_guard` for canonical name collision and disposable email checks. |
| **Write Route Names** | (a) Intercept CTFd core `/settings` and `/team`<br/>(b) Register views via `pages/team_settings.py` `install(app)` | **(b)** Clean plugin route registration | `plugins/l3mon_theme/pages/team_settings.py` registers `POST /team`, `POST /team/members/<id>/captain`, `POST /settings` without editing any CTFd core files or shared theme files. |
| **CSRF Handling** | (a) Custom CSRF token<br/>(b) CTFd's standard session `nonce` | **(b)** CTFd `Session.nonce` | Standard CTFd CSRF protection. Refuses requests with missing or invalid `nonce` with HTTP 403. |
| **Identity / Provider Detection** | (a) Check `Users.oauth_id` for CTFtime and `Identity` table for Google | **(b)** Expose provider helper in page view | If `Users.oauth_id` is set, provider is CTFtime. If `Identity.query.filter_by(user_id=user.id, provider="google")` exists, provider is Google. Locked email and disabled password card render appropriately. |

---

## 3. Interfaces and Data Contracts

### 3.1 Studio Page Context (`tm`)
Adapter: `ui/theme/l3mon/templates/teams/private.html` (includes `_team.html`)
Context variables under `tm`:
- `name`: string, team name
- `meta`: string, country name and affiliation (e.g. `India · Amrita CyberNation`)
- `standing`: string, formatted standing (`52nd of 59 · 2875 TRP`, `Ranks frozen`, or `Not ranked yet · 0 TRP`)
- `seats`: string (e.g. `2 of 4`)
- `captain`: string, captain's name
- `size`: string, maximum team size (`4`)
- `isCaptain`: boolean, true if current viewer is team captain
- `canInvite`: boolean, true if viewer is captain and seats are free
- `seatsText`: string (e.g. `2 seats are free`, `1 seat is free`)
- `invite`: string, invite URL `https://<host>/teams/invite?code=<code>`
- `hasOthers`: boolean, true if captain can hand chair to other members
- `others`: list of `{id: str, name: str}` (members excluding captain)
- `members`: list of `{id: str, name: str, ini: str, tag: str, cls: str}`
- `affiliation`: string
- `country`: string (two-letter code)
- `countries`: list of `{code: str, name: str, sel: str}`
- `hasNotice`: boolean
- `notice`: string (one-time flash message)

### 3.2 Settings Page Context (`st`)
Adapter: `ui/theme/l3mon/templates/settings.html` (includes `_settings.html`)
Context variables under `st`:
- `name`: string, username
- `email`: string
- `confirmed`: boolean, true if email verified and no errors
- `pending`: boolean, true if email unconfirmed (`user.verified is False`)
- `viaProvider`: boolean, true if created via Google or CTFtime
- `provider`: string (`Google` or `CTFtime`)
- `saved`: boolean, true if change notice should display
- `hasSummary`: boolean, true if validation errors present
- `summary`: list of `{href: str, text: str}`
- `err`: object `{name: str|None, email: str|None, current: str|None, password: str|None}`

### 3.3 Write Endpoints Specification
1. **`POST /team`**
   - Requires authed user with studio, captain only.
   - Body fields: `nonce`, `name`, `country`, `affiliation`.
   - Validates through `validate_team_name` and `TeamSchema(view="self", instance=team, partial=True)`.
   - On success: updates team, commits, flashes `'team-saved'`, redirects to `/team`.
   - On error: stores error summary and values in session flash, redirects to `/team`.

2. **`POST /team/members/<id>/captain`**
   - Requires authed user with studio, captain only.
   - Validates that target `<id>` is a member of viewer's studio (`target.team_id == team.id`).
   - On success: updates `team.captain_id = target.id`, commits, flashes `'captain-moved'`, redirects to `/team`.

3. **`POST /settings`**
   - Requires authed user.
   - Body fields:
     - `part="details"`: `nonce`, `name`, `email` (if not viaProvider), `confirm` (required if email changes).
     - `part="password"`: `nonce`, `password`, `confirm`.
   - Validates through `UserSchema(view="self", instance=user, partial=True)` and guard email policies.
   - On success: updates user, commits, re-hashes session if password changed, flashes `'settings-saved'`, redirects to `/settings`.
   - On error: stores error summary in session flash, redirects to `/settings`.

---

## 4. Test Plan and Milestones

### Milestone 1 (M1)
- [x] Plan written in `docs/superpowers/plans/2026-10-22-ui-d-team-settings.md`.
- [x] Python test environment configured with Jinja2.
- [ ] Registered `theme-pages/team.json` and `theme-pages/settings.json`.
- [ ] Export `_team.html` and `_settings.html` into `ui/theme/l3mon/templates/`.
- [ ] Equivalence tests in `platform-ui/tests/export-account.test.mjs` verifying prototype vs. Jinja rendering across captain, full, alone, and member contexts.
- [ ] Theme adapter `ui/theme/l3mon/templates/teams/private.html` and provider module `plugins/l3mon_theme/pages/team_settings.py`.
- [ ] Plugin unit tests `plugins/l3mon_theme/tests/test_team_pages.py` for `/team` under CTFd test client (signed out, no team, member, captain, full team).
- [ ] Handoff started in `docs/handoff/ui-d-team-settings.md` and contract in `platform-ui/docs/contract-d-team-settings.md`.

### Milestone 2 (M2)
- [ ] Settings adapter `ui/theme/l3mon/templates/settings.html`.
- [ ] Write views implementation: `POST /team`, `POST /team/members/<id>/captain`, `POST /settings`.
- [ ] Comprehensive security and validation tests:
  - CSRF `nonce` enforcement.
  - Member refusal (403) on captain endpoints.
  - Hand-over target boundary verification (cannot hand over to user outside studio).
  - Rate limiting on write views.
  - Password change session invalidation proof.
  - Name look-alike and collision refusal.
  - Disposable email domain refusal.
- [ ] Query budget and hook cost tests.
- [ ] Scripted browser smoke run on local stack (`smoke-real-account.mjs`).
- [ ] Break-on-purpose verification matrix.

### Milestone 3 (M3)
- [ ] Lead engineer audit findings resolved.
- [ ] All test suites passing with full counts recorded in handoff.
