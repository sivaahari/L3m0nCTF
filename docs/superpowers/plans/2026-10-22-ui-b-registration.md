# Stream B: Registration (Account, Email Check, Studio) Implementation Plan

> Built test-first, milestone by milestone, with independent verification. Measure, then decide; nothing here is changed on a feeling. All rules adhere to L3m0nCTF 2026 platform standards.

**Goal:** Put the prototype's approved visual design for the player registration flow (account setup, email verification, and studio enrollment) onto CTFd's native auth and team flows (`/register`, `/confirm`, `/teams/*`) page by page without altering CTFd's core backend logic or compromising strict CSP, query budgets, or anti-abuse guarantees.

**Branch:** `ui/b-registration` in both `L3m0nCTF` and `L3m0nCTF-ui`.  
**Handoff:** `docs/handoff/ui-b-registration.md` and `platform-ui/docs/contract-b-registration.md`.  
**Milestones:** M1 (29 October), M2 (5 November), M3 (10 November).

---

## 1. Facts Read and Measured in the Codebase

1. **CTFd Version & Invariants:**
   - Platform pinned to CTFd 3.8.8 (`docker/ctfd/Dockerfile#L5`). CTFd core files are never modified (`PROMPT-B-registration.md#A3.5`).
   - Upstream registration view: `CTFd/auth.py#L234-L350` (`/register`). Handles `POST` with `name`, `email`, `password`, rate-limited to 10 per 5s. Errors returned as flat list `errors`.
   - Upstream confirm view: `CTFd/auth.py#L35-L115` (`/confirm` and `/confirm/<data>`). GET confirms token; POST triggers verification email resend.
   - Upstream team enrollment: `CTFd/teams.py` (`/team`, `/teams/new`, `/teams/join`, `/teams/invite`).

2. **Theme Pipeline and Registry (`docs/deploy/theme-slices.md`):**
   - Theme exporter (`platform-ui/tools/export-theme.mjs`) reads `platform-ui/theme-pages/<page>.json` containing `page`, `css`, and `js`.
   - Compiles prototype templates into Jinja equivalents (`ui/theme/l3mon/templates/_<page>.html`) and extracts hashed bundles to `ui/theme/l3mon/static/`.
   - Strict CSP enforced (`export-theme.mjs#L74-L86`): no inline `<script>`, no inline `<style>`, no `style=` or `on...=` attributes, no outside hosts (except Cloudflare Turnstile if configured).
   - Bundles defined in `platform-ui/tools/build.mjs#L37`: `register: 'entry-register.js'`.

3. **Plumbing & Extension Points:**
   - Plugin `plugins/l3mon_theme/` exposes `registry.py` (`@value(...)`) and imports all modules in `plugins/l3mon_theme/pages/` executing `install(app)` (`registry.py#L50-L61`).
   - Anti-abuse guard integration: `l3mon_guard` provides Turnstile context (`values.py#L77-L88`). Widget partial already provided in `platform-ui/src/partials/captcha.html` and conditioned on `captcha.sitekey`.

4. **Prototype Pages and Contracts (`platform-ui/src/pages/`):**
   - `register-account.html`: Account creation (username, email, password, privacy).
   - `register-verify.html`: Email confirmation wait and resend with cool-down.
   - `register-studio.html`: Studio choice (create with name/country/affiliation/CTFtime or join with invite).
   - `register-link.html`: Email token verification landing page (ok, used, expired, invalid).
   - `register-golive.html` and `register-done.html`: Step 4 review and completion.

---

## 2. Key Decisions (Part B2 Alignment)

| Area | Decision | Rationale & Mechanics |
|---|---|---|
| **1. The Stepper** | 3-step wizard on CTFd | The prototype has 4 steps (Account, Verify, Studio, Review). CTFd's native flow terminates enrollment upon studio creation/joining. The review step and done page are dropped; Stream D's `teams/private.html` will handle the studio welcome. Stepper displays 3 steps ("Step n of 3"). |
| **2. Joining & Passwords** | Dual link/code input; random team password | The join field accepts either the complete `/teams/invite?code=...` link or the raw code token. If CTFd requires a team password during studio creation, a strong random password is generated to prevent unauthorized join by password guessing, keeping joins strictly invite-based. Studio names are checked against `l3mon_guard` look-alike and uniqueness rules. |
| **3. Error Handling** | Flat list summary + safe deterministic mapping | CTFd returns a flat list `errors`. Display the entire list in the top error summary (`reg.summary` / `reg.alert`). Attach errors to specific form fields only when pattern matching is 100% deterministic (e.g. standard CTFd message templates or `l3mon_guard` strings). Never guess field attribution. |
| **4. Drafts & Auth Providers** | Drop server draft; retain browser live checks | Plain CTFd registrations do not persist drafts in server sessions across reloads. Retain all browser-side validation, live availability checks, password show toggles, and pending labels. Users entering via Google (`l3mon_auth`) or CTFtime arrive authenticated and verified, bypassing Step 2 directly to Step 3. No account linking by email. |
| **5. CAPTCHA** | Cloudflare Turnstile inside form | Embed `<!--include:captcha-->` in `register-account.html` inside the form before submission actions. Activated only when `captcha.sitekey` is present and `l3.captcha.register` is true, submitting `cf-turnstile-response`. |

---

## 3. Interfaces and Modules

### 3.1 Backend Adapter Module (`plugins/l3mon_theme/pages/register.py`)
- **Studio Name Availability Endpoint:**
  - `GET /api/v1/l3mon/teams/available?name=<name>`
  - Response: `{"success": true, "data": {"available": bool, "reason": "taken" | "invalid" | null}}`
  - Signed-in users only (401 if unauthenticated).
  - Rate-limited to 20 requests per minute per IP.
  - Normalization: trims whitespace, collapses inner spaces, case-insensitive, look-alike check via `l3mon_guard.normalise`.
  - Single database lookup with query-budget assertion (0 or 1 DB query).
- **Form Interception and Route Mapping:**
  - Map form field `username` -> `name` for CTFd's `/register` handler if `name` is omitted.
  - Route `/register/account` to `/register` so prototype action targets resolve cleanly.
  - Handle verify resend via CTFd's `/confirm` POST endpoint.

### 3.2 UI Repository Artifacts (`L3m0nCTF-ui`)
- `platform-ui/theme-pages/register-account.json` -> `{"page": "register-account", "css": "css.app", "js": "js.register"}`
- `platform-ui/theme-pages/register-verify.json` -> `{"page": "register-verify", "css": "css.app", "js": "js.register"}`
- `platform-ui/theme-pages/register-studio.json` -> `{"page": "register-studio", "css": "css.app", "js": "js.register"}`
- `platform-ui/theme-pages/register-link.json` -> `{"page": "register-link", "css": "css.app", "js": "js.register"}`
- `platform-ui/src/pages/register-account.html`: Update with captcha partial inclusion.
- `platform-ui/docs/contract-b-registration.md`: Contract definition for context keys.

### 3.3 Theme Adapters (`L3m0nCTF/ui/theme/l3mon/templates/`)
- `register.html`: Adapter for `/register` invoking `{% include "_register-account.html" %}`.
- `confirm.html`: Adapter for `/confirm` and `/confirm/<data>` invoking `_register-verify.html` and `_register-link.html`.
- `teams/team_enrollment.html`: Adapter for `/team` choice page.
- `teams/new_team.html`: Adapter for `/teams/new` invoking `_register-studio.html` (mode: create).
- `teams/join_team.html` & `teams/invite.html`: Adapters for joining studios.

---

## 4. Test Strategy

1. **Equivalence Tests (`platform-ui/tests/export-theme-register.test.mjs`):**
   - Render every context of `register-account`, `register-verify`, `register-studio`, `register-link` through prototype renderer and Python Jinja.
   - Assert output matches byte for byte after whitespace collapse.
   - Verify CSP compliance (no inline scripts, styles, or event handlers).
   - Verify no prototype/mock wording leaks onto production pages without dev flag.

2. **Plugin Unit Tests (`plugins/l3mon_theme/tests/test_theme_register.py`):**
   - App fixture on SQLite with `user_mode="teams"` and `ctf_theme="l3mon"`.
   - `/register` GET: renders theme template, contains CSRF nonce, correct OSD/step headers.
   - `/register` POST with error conditions: duplicate username, duplicate email, short password, missing required field, captcha check required.
   - `/confirm` and `/confirm/<data>` states: valid, expired, invalid tokens.
   - `/teams/new` and `/teams/join` flows.
   - Studio name availability API: shape, validation, rate limiting, query budget.

3. **Integration Tests (`tests/integration/test_theme_b.py`):**
   - End-to-end user registration and team enrollment through the live nginx stack.
