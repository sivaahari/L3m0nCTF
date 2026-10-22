# Stream C: Guide, Scoreboard, Sign-in Finish, Password Reset and Account Gates (Implementation Plan)

**Stream:** C (about 20 percent of the visual design rollout)  
**Branch (both repos):** `ui/c-guide-scoreboard`  
**Handoff files:** `docs/handoff/ui-c-guide-scoreboard.md` (public) and `platform-ui/docs/contract-c-guide-scoreboard.md` (UI)  
**Date:** 2026-10-22  

---

## 1. Goal

Port the approved visual design from `L3m0nCTF-ui` onto the real platform (`L3m0nCTF` / CTFd 3.8.8) for Stream C:
1. **The Guide (`/guide`)**: registered new route, player/crew gate, Python port of Node view-model builder (`server/api-guide.mjs`) on top of `plugins/l3mon_board/guide.py` & `epg.py`, adapter `guide.html` including `_guide.html`.
2. **The Scoreboard (`/scoreboard`)**: adapter `scoreboard.html` including `_scoreboard.html`, Python port of Node view-model builder (`server/scoreboard.mjs`) on top of `plugins/l3mon_board/scoreboard.py`, 100 rows server-rendered, polling via ticks.
3. **Sign-in Finished (`/login`)**: take ownership of adapter `ui/theme/l3mon/templates/login.html`, turn on Google auth button when `oauth.configured()`, review Turnstile CAPTCHA and error alerts.
4. **Password Reset (`/reset_password`, `/reset_password/<data>`)**: adapter `reset_password.html` including `_register-reset.html`, Turnstile CAPTCHA widget partial when `l3.captcha.reset`, mapping CTFd modes (request, sent, set, expired, invalid).
5. **Account Gate Pages**: adapters for missing errors (`errors/401.html`, `errors/502.html`), suspended-account slate, before-start and after-end slates, verified against the prototype gates gallery.

---

## 2. Facts Read from Codebase (with File and Line)

### 2.1 Contracts and Prototype Implementations (`L3m0nCTF-ui`)
- `platform-ui/docs/template-contract.md`:
  - lines 83-95: `guide.*` flat context definition (ver, hasEpg, epg, epgSig, hasMembers, members, hasBonus, bonus, hasNotes, notes, hasTeam, studio, team, chans, hasStory, story).
  - lines 96-99: `sb.*` flat context definition (ver, top, total, hasRows, rows, sig, hasMe, me, more).
  - lines 73-76: `login.*` context (alert, summary, errors, name, next, remember, human, retry, providers, showRemember, showHuman, showGoogle).
  - lines 77-82: `slate.*` context (id, status, inShell, cls, srStatus, code, h1, line, retry, retryLead, retryUrl, ways, requestId, note, site).
  - lines 150-160: `register-reset.html` context (`reset.*`: ok, token, username, ask, retry, email, err, dev).
- `platform-ui/server/api-guide.mjs`:
  - lines 24-28: `ordinal(n)` logic (1st, 2nd, 3rd, 4th... 11th, 12th, 13th, 21st).
  - lines 38-56: `memberRows(world, team, user)` (solves, trp, pct, earlier solves id 0, sort by -trp, captain, name).
  - lines 107-119: `blockLabel` and `rowContext` for `guide-row.html`.
  - lines 132-156: `guideData` JSON payload and strong ETag generation.
  - lines 159-195: `guideView` generating flat template dictionary for `guide.html`.
- `platform-ui/server/scoreboard.mjs`:
  - lines 18-28: `scoreboardRows` ranking calculation (pos, teamId, name, solves, score).
  - lines 37-43: `meBlock` (name, score, pos, of; pos null if frozen or not ranked).
  - lines 62-72: `rowContext` and `scoreboardHtml` rendering `scoreboard-row.html`.
  - lines 75-84: `scoreboardView` generating flat template dictionary for `scoreboard.html`.
- `platform-ui/server/dev-rows.mjs`:
  - lines 4-56: `GATE_ROWS` defining all 17 gate scenarios.
- `platform-ui/tools/build.mjs`:
  - lines 37: `ENTRIES` mapping logical bundles (`login`, `register`, `guide`, `scoreboard`, `slate`).
  - lines 48-70: First-load budgets in KB gzip.
- `platform-ui/tools/export-theme.mjs`:
  - lines 26-45: `readRegistry` validating `theme-pages/<page>.json`.
  - lines 64-100: template conversion to Jinja, strict CSP and markup refusals.

### 2.2 Existing Platform Plumbing (`L3m0nCTF`)
- `plugins/l3mon_board/guide.py`:
  - lines 24-39: `_members(team, viewer, credited)` computing member contributions.
  - lines 42-78: `_team_block(viewer, phase, view)` computing team metrics, bonus, notes.
  - lines 81-94: `_story(viewer, view)` computing meter reels and on_air.
  - lines 102-113: `build(viewer)` returning `(data, etag)` with `signature()`.
- `plugins/l3mon_board/epg.py`:
  - lines 27-48: `model(plan, viewer)` grid representation.
  - lines 51-57: `signature(grid)` sha256 16-char ETag.
  - lines 66-89: `html(grid)` rendering `epg_row.html`.
- `plugins/l3mon_board/scoreboard.py`:
  - lines 40-62: `read(team)` returning `SimpleNamespace` (phase, total, rows, me, sig, mine, ver).
  - lines 64-70: `answer(view)` returning `(data, etag)`.
  - lines 77-92: `html(view)` rendering `scoreboard_row.html`.
- `plugins/l3mon_board/viewer.py`:
  - lines 25-60: `current()` resolving viewer and gates (signed in, team, crew, unverified, banned).
- `plugins/l3mon_auth/oauth.py`:
  - lines 95-97: `configured()` returning `bool(client_id() and client_secret() and platform_host())`.
  - lines 170-180: `@oauth.route("/auth/google")` start endpoint with PKCE and state.
- `plugins/l3mon_theme/registry.py`:
  - lines 23-38: `@value(name, default)` registration decorator.
  - lines 50-61: `discover(app)` importing all modules in `pages/` in alphabetical order.
- `plugins/l3mon_theme/pages/routes.py`:
  - lines 16-24: `ALIASES = {"/board": "channels", "/guide": "guide"}`: steps aside as soon as `app.url_map` has `/guide`.
- `plugins/l3mon_theme/values.py`:
  - lines 112-121: `Values.__getattr__` looking up registered dynamic values.
  - lines 179-183: `captcha` state exposing `{sitekey, register, login, reset}`.

---

## 3. The Gates Table

Every gate state from `server/dev-rows.mjs` mapped to the CTFd platform implementation:

| Row ID | Title / State | Prototype Behavior | CTFd Platform Status / Handling | Implementation Mechanism |
|---|---|---|---|---|
| `signed-out` | Not signed in | 303 to `/login?next=...`, API: 401 | 302/303 to `/login?next=...`, API: 401 | CTFd `@authed_only` & `l3mon_board.viewer.current()` |
| `unverified` | Email not verified | 303 to `/register/verify`, API: 403 | CTFd redirects unconfirmed email to `/confirm`, API: 403 `unverified` | `l3mon_guard` & `viewer.py` |
| `no-team` | No studio yet | 303 to `/register/studio`, API: 403 | CTFd redirects to team setup, API: 403 `no_team` | `viewer.py` checks `user.team_id is None and not is_admin()` |
| `banned` | Banned account / studio | 403 OFF AIR slate, API: 403 `banned` | CTFd throws 403; theme adapter renders `sl-banned` (OFF AIR) | `errors/403.html` checks viewer banned state or `viewer.banned` slate |
| `before` | Contest not started | 200 NOT ON AIR YET in-shell slate, attempts 403 `phase_closed` | `/challenges` shows `_slate.html` in shell (`sl-before`), `/guide` shows empty grid with note, `/scoreboard` shows empty list | `pages/guide.py` sets `hasEpg=False`, `scoreboard.py` returns empty rows, `phase.state == 'before'` |
| `ended` | Contest ended | 200 THAT'S A WRAP banner (`wrap`), read-only | `banner.for_phase()` returns `wrap`, attempts 403 | `l3mon_board.banner` already renders `data-banner="wrap"` |
| `paused` | Contest paused | 200 PAUSED banner, attempts 403 `paused` | `banner.for_phase()` returns `paused`, attempts 403 | `l3mon_board.banner` renders `data-banner="paused"` |
| `frozen` | Scoreboard frozen | Banner `frozen`, `place` hidden ("Ranks frozen") | `banner.for_phase()` returns `frozen`, `place` None | `l3mon_core.clock.current_phase().frozen`, `board.numbers()` place None |
| `busy` | Shed level 1 | 503 busy slate, Retry-After | 503 via nginx snippet `deploy/nginx/snippets/slates/busy.html` | Handled at nginx edge |
| `busy-flags` | Shed all | 503 busy slate, attempts 503 | 503 via nginx snippet | Handled at nginx edge |
| `waiting` | Waiting room | 503 YOU ARE IN LINE | 503 via nginx snippet / cloudflare queue | Handled at nginx edge |
| `maintenance` | Maintenance | 503 maintenance slate | 503 via nginx snippet `maintenance.html` | Handled at nginx edge |
| `restarting` | Restarting | 502 restarting slate | 502 adapter `errors/502.html` (including `_slate.html`) | **Stream C creates `errors/502.html` adapter** |
| `401` | Unauthorized | 401 slate / redirect | 401 adapter `errors/401.html` (including `_slate.html`) | **Stream C creates `errors/401.html` adapter** |
| `403` | Forbidden | 403 RESTRICTED CHANNEL slate | Handled by `ui/theme/l3mon/templates/errors/403.html` | Already created by lead |
| `404` | Not found | 404 NO SIGNAL slate | Handled by `ui/theme/l3mon/templates/errors/404.html` | Already created by lead |
| `429` | Rate limited | 429 slate with countdown | Handled by `ui/theme/l3mon/templates/errors/429.html` | Already created by lead |
| `500` | Server error | 500 TECHNICAL DIFFICULTY | Handled by `ui/theme/l3mon/templates/errors/500.html` | Already created by lead |

---

## 4. Key Architectural Decisions

1. **Guide Route (`/guide`) & View Model**:
   - Register route `/guide` in `plugins/l3mon_theme/pages/guide.py`.
   - Gate access using `l3mon_board.viewer.current()`: unauthed visitors redirected to `/login?next=/guide`; unverified redirected to `/register/verify` (or `/confirm`); unteamed redirected to `/register/studio` (or `/teams/join`); banned rendered with OFF AIR slate (status 403).
   - Python view-model builder `build_guide_view(viewer, data, epg_html)` replicates `server/api-guide.mjs:guideView` producing the exact dictionary required by `guide.html`.
   - Adapter template `ui/theme/l3mon/templates/guide.html` sets shell context (`l3`, `page`, `asset`, `nav`, `me`, `csrf`, `event`, `build`, `banner`, `guide`) and includes `_guide.html`.
2. **Scoreboard (`/scoreboard`)**:
   - CTFd's core route `/scoreboard` renders theme template `scoreboard.html`.
   - Register dynamic value `@value("scoreboard_view")` or provide helper in `plugins/l3mon_theme/pages/scoreboard.py`.
   - Python view-model builder `build_scoreboard_view(viewer, data, rows_html)` replicates `server/scoreboard.mjs:scoreboardView`.
   - Adapter `ui/theme/l3mon/templates/scoreboard.html` sets shell context and `sb` dictionary, then includes `_scoreboard.html`.
3. **Sign-in Finish (`/login`)**:
   - Register `@value("google_on")` in `plugins/l3mon_theme/pages/signin.py` returning `bool(l3mon_auth.oauth.configured())`.
   - Update `ui/theme/l3mon/templates/login.html`: set `'showGoogle': l3.google_on`, set `'providers': {'google': google_href if l3.google_on else none, ...}`.
4. **Password Reset (`/reset_password`, `/reset_password/<data>`)**:
   - Register `theme-pages/register-reset.json` with bundles `css.app` and `js.register`.
   - Export produces `templates/_register-reset.html`.
   - Write adapter `ui/theme/l3mon/templates/reset_password.html`: handles CTFd's modes (`mode="set"`, sent, expired, invalid).
   - Turnstile CAPTCHA included when `l3.captcha.reset` is true (`{% if captcha.sitekey and l3.captcha.reset %}`).
5. **Gate Pages & Adapters**:
   - Create `ui/theme/l3mon/templates/errors/401.html`: includes `_slate.html` with status 401, mood 'sad', code 'AUTH REQUIRED', ways leading to sign-in.
   - Create `ui/theme/l3mon/templates/errors/502.html`: includes `_slate.html` with status 502, mood 'sad', code 'RESTARTING'.
6. **Golden Test Pairs**:
   - Create node script in `platform-ui/tools/dump-golden-c.mjs` exporting canonical scenarios for Guide and Scoreboard (`live_one_studio`, `before_start`, `frozen`, `crew`, `multiple_members`).
   - Store fixtures in `plugins/l3mon_theme/tests/fixtures/golden_guide.json` and `golden_scoreboard.json`.
   - Python test in `plugins/l3mon_theme/tests/test_golden_builders.py` verifies Python builders match Node outputs byte-for-byte or key-for-key.

---

## 5. Milestone Breakdown & Delivery Plan

### Milestone 1 (Target: 29 October, Executing Now)
- [x] Pre-flight checks: baseline prototype tests (333 pass), theme tests (65 pass), tool tests (302 pass), hygiene clean.
- [x] Check out branch `ui/c-guide-scoreboard` in both repos.
- [ ] Implementation plan in `docs/superpowers/plans/2026-10-22-ui-c-guide-scoreboard.md` committed.
- [ ] Initial handoff documents:
  - `docs/handoff/ui-c-guide-scoreboard.md` in `L3m0nCTF`
  - `platform-ui/docs/contract-c-guide-scoreboard.md` in `L3m0nCTF-ui`
- [ ] Guide Page Slice:
  - Register `platform-ui/theme-pages/guide.json`.
  - Write equivalence test in `platform-ui/tests/export-guide.test.mjs`.
  - Write golden dump tool `platform-ui/tools/dump-golden-c.mjs`.
  - Export theme (`tools/export-theme.mjs`) to generate `ui/theme/l3mon/templates/_guide.html` and static bundles.
  - Implement Python builder in `plugins/l3mon_theme/pages/guide.py`.
  - Write adapter `ui/theme/l3mon/templates/guide.html`.
  - Write plugin tests in `plugins/l3mon_theme/tests/test_theme_guide.py` (signed out, no studio, banned, crew, player, before/live/paused/ended).
  - Verify golden test pairs in `plugins/l3mon_theme/tests/test_golden_guide.py`.
  - Verify query budgets in `plugins/l3mon_theme/tests/test_guide_query_budget.py`.

### Milestone 2 (Target: 5 November)
- [ ] Scoreboard Page Slice:
  - Register `platform-ui/theme-pages/scoreboard.json`.
  - Write equivalence test in `platform-ui/tests/export-scoreboard.test.mjs`.
  - Export theme to generate `ui/theme/l3mon/templates/_scoreboard.html`.
  - Implement Python builder in `plugins/l3mon_theme/pages/scoreboard.py`.
  - Write adapter `ui/theme/l3mon/templates/scoreboard.html`.
  - Write plugin tests in `plugins/l3mon_theme/tests/test_theme_scoreboard.py`.
  - Query budget tests for scoreboard.
- [ ] Sign-in Finish:
  - Implement `plugins/l3mon_theme/pages/signin.py` with `@value("google_on")`.
  - Update `ui/theme/l3mon/templates/login.html` with Google provider and button display.
  - Write plugin tests for Google button conditional rendering and CSRF/next safety.
- [ ] Password Reset:
  - Register `platform-ui/theme-pages/register-reset.json`.
  - Write equivalence test in `platform-ui/tests/export-reset.test.mjs`.
  - Export theme to generate `ui/theme/l3mon/templates/_register-reset.html`.
  - Write adapter `ui/theme/l3mon/templates/reset_password.html`.
  - Plugin tests for password reset states (request, sent, invalid token, set password, Turnstile widget, timing/enumeration safety).
- [ ] Gate Pages:
  - Create `ui/theme/l3mon/templates/errors/401.html` and `502.html`.
  - Plugin tests for 401, 502, banned slate, before-start, after-end.
- [ ] Integration & End-to-End:
  - `tests/integration/test_theme_c.py`.
  - Browser verification script `platform-ui/tools/smoke-real-guide.mjs`.
  - Full test suites of A6 passing with complete counts in handoff.

### Milestone 3 (Target: 10 November)
- [ ] Lead audit review and resolution of findings.
- [ ] Rebase on `origin/pre-deployment`, regenerate generated theme bundles.
- [ ] Final verification of security stance and hygiene scans.
