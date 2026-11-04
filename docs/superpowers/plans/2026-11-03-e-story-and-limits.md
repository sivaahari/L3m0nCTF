# L3m0nCTF 2026: Plan for Stream E: The Story Pieces and the Rate-Limit Page

> Implementation plan for Stream E (3 November 2026).
> Branch: `build/e-story-and-limits` (in both `L3m0nCTF` and `L3m0nCTF-ui`).
> Handoff: `docs/handoff/e-story-and-limits.md` (public) and `platform-ui/docs/contract-e-story-and-limits.md` (UI).

---

## 1. Facts Read in the Codebase (File and Line References)

### E1. A story line for each programme
1. **Frontend templates & scripts:**
   - `platform-ui/src/partials/tpl-panel.html:14`: `<h3 id="pn-story-h">In the story</h3>` already exists inside `<section class="pn-story" id="pn-story-sec" hidden>`.
   - `platform-ui/src/partials/noscript-programme.html:42`: `<div class="pn-story" ...><p>{{pn.story}}</p></div>` already handles `pn.story`.
   - `platform-ui/src/js/panel.js:246`: `pnK('story').textContent = l3.story || '';` and `storySec.hidden = !l3.story;`.
   - `platform-ui/src/js/platform.js:43`: Reads `story: typeof l3.story === 'string' ? l3.story : ''` from `data.l3mon`.
2. **Backend views and adapter:**
   - `plugins/l3mon_theme/pages/board.py:277`: Reads `detail.get("story") or ""` in `programme_view`.
   - `plugins/l3mon_theme/pages/board.py:381`: `programme_detail` passes `"story": ""` (hardcoded empty string), which must be updated to `block.get("story") or ""`.
3. **Database model and migrations:**
   - `plugins/l3mon_core/models.py:49-67`: Model `Programme` (`__tablename__ = "l3mon_programme"`). Needs text column `story` (max 280 chars, NOT NULL, default `""`, server default `""`).
   - `plugins/l3mon_core/migrations/c5e2a7b9d104_add_difficulty_and_delivery_to_programmes.py`: The third migration, which added `difficulty` and `delivery`. Our fourth migration (`d8f3a1b4e205_add_story_to_programmes.py`) revises `c5e2a7b9d104`.
4. **Plan loading and validation:**
   - `plugins/l3mon_release/plan.py:36`: `PROGRAMME_FIELDS = {"challenge_id", "challenge_name", "channel", "cell", "number", "slug", "difficulty", "delivery"}`. Needs `"story"`.
   - `plugins/l3mon_core/text.py:32-48`: `crew_text(value, limit, required=True)` provides the exact validation required: trims, enforces max length (280), rejects control characters / direction-changing / invisible marks, rejects `<` and `>`, allows empty string when `required=False`.
   - `plugins/l3mon_release/plan.py:343-365`: `apply_plan` handles upsert. Must preserve existing `row.story` if `story` key is omitted in request; updates when present (clears when `""`).
   - `plugins/l3mon_release/plan.py:391-395`: `release_view(t)` exposes programme fields to crew. Must include `"story": p.story`.
5. **Challenge l3mon block and security isolation:**
   - `plugins/l3mon_board/panel.py:54-75`: `block()` constructs `l3mon` dictionary. Adds `"story": programme.story or ""`.
   - Zero extra query cost: `programme` is already fetched via `Programme.query.filter_by(challenge_id=challenge.id)`.
   - Security isolation: Board JSON (`plugins/l3mon_board/board.py`) and Guide JSON (`plugins/l3mon_board/guide.py`) never include `story`.

### E2. The way into the stories (cold opens and finale)
1. **Existing story capabilities:**
   - `GET /api/v1/l3mon/story`: Lists unlocked stories for the current viewer (`plugins/l3mon_story/api.py:75`). Returns `{"stories": [{"slug", "title", "kicker", "panels"}]}`.
   - `GET /story/<slug>`: Renders the comic viewer (`plugins/l3mon_story/api.py:142`, `plugins/l3mon_story/templates/comic.html`, `assets/comic.js`).
   - `plugins/l3mon_board/board.py:99`: Channel JSON already carries `"cold_open": {"panels": n}` or `null`.
2. **Current gaps:**
   - No approved page currently has links or entry points to open the cold-open comics or finale.
   - Guide has a story meter ("Reels toward the Director's Cut"), but nothing to click.
3. **Required action:**
   - Present two options on `/__dev` (Director's desk) in `platform-ui`:
     - **Option 1:** Buttons on the board under channel captions + finale banner.
     - **Option 2 (Recommended):** Dedicated `/stories` page linked from the Guide.
   - Await owner decision before building the theme adapter and route.

### E3. An automatic time for the finale
1. **Existing finale logic:**
   - `plugins/l3mon_story/finale.py:45-53`: `is_open(who)` checks `current_phase().state == "before"`, `finale_open()`, and `_meter_is_full(who)`.
   - `plugins/l3mon_core/settings.py:13-54`: Helper functions parse settings with defensive bounds (`show_coming_count`, `story_meter`, `finale_open`, `story_air_target`).
   - `plugins/l3mon_core/clock.py:40`: `now()` returns `time.time()`.
2. **New setting and behavior:**
   - Setting key: `l3mon_finale_at`.
   - Whole number of UTC epoch seconds (between 2020-01-01 and year 2049, i.e., `1577836800 <= t <= 2500000000`). Bad values, booleans, negative numbers, or out-of-range timestamps evaluate to `None`.
   - `finale.is_open(who)` checks `auto_at = finale_at()`; if `auto_at is not None and clock.now() >= auto_at`, returns `True` immediately (after ensuring `current_phase().state != "before"`).
   - Because `auto_at` check precedes `_meter_is_full(who)`, the 5-second cache never delays opening once the timestamp is reached.

### E4. The rate-limit page
1. **CTFd's default 429 response:**
   - CTFd `ratelimit` decorator (`CTFd/utils/decorators/__init__.py`) emits JSON `{"code": 429, "message": "Too many requests. Limit is %s requests in %s seconds"}` with status 429.
   - On browser HTML form submissions (login, register, password reset, team join), users receive raw JSON.
2. **Existing template assets:**
   - `ui/theme/l3mon/templates/errors/429.html` and `_slate.html` exist.
   - `platform-ui/server/slates.mjs:16`: Prototype slate `ratelimited` (Status 429, "PLEASE STAND BY", "Too many requests", "Slow down a little.").
3. **New module:**
   - `plugins/l3mon_theme/pages/errors.py` with `install(app)` registered automatically via `plugins/l3mon_theme/registry.py:discover(app)`.
   - Registers an `after_request` handler active only when `ctf_theme == "l3mon"`.
   - Filters for page requests (GET or POST, non-API/asset path, client did not request JSON, response is 429 JSON).
   - Renders `errors/429.html` with status 429, `Retry-After: <seconds>`, `Cache-Control: no-store`.

---

## 2. Decisions and Alternatives Rejected

| Topic | Options | Selected | Rationale |
|---|---|---|---|
| **E1: Plan `story` validation** | (a) Strict regex `UNSAFE_TEXT`<br/>(b) Free text with HTML escape<br/>(c) `crew_text` | **(c) `crew_text`** | Storylines contain apostrophes, punctuation, and hyphens which `UNSAFE_TEXT` would reject. `crew_text` properly rejects `< >` and control characters while trimming and capping at 280 chars. |
| **E1: Upsert omission semantics** | (a) Missing key clears line<br/>(b) Missing key preserves line | **(b) Preserve line** | Prompt E1 explicitly requires safe upsert: partial plan syncs must not erase existing story lines. `""` explicitly clears. |
| **E2: Way into stories design** | (a) Option 1: Board channel buttons + finale banner<br/>(b) Option 2: Dedicated `/stories` page linked from Guide | **(b) Option 2 (Recommended)** | Option 2 keeps board templates, view builders, and golden snapshots untouched; provides a clean single place for cold opens and the finale. Both options will be prototyped on `/__dev` for owner confirmation. |
| **E3: Finale timestamp check** | (a) Check inside `_meter_is_full`<br/>(b) Check directly in `is_open` before meter | **(b) Direct check in `is_open`** | Checking before `_meter_is_full` ensures that the 5-second studio cache never delays opening once the time arrives (0-second delay), while avoiding meter calculation overhead. |
| **E4: 429 detection & Retry-After** | (a) Parse CTFd JSON message for window seconds<br/>(b) Read Redis key TTL | **(a) Parse message with safe fallback** | Cache keys vary across backends (in-memory SimpleCache in tests vs Redis in prod) and IP resolution behind proxies. The CTFd message `Limit is X requests in Y seconds` guarantees a deterministic, safe integer without backend-specific coupling. |

---

## 3. Interfaces and Data Contracts

### 3.1 Programme Story Line (E1)
- **Model:** `Programme.story`: `db.Column(db.String(280), nullable=False, default="", server_default="")`
- **Migration:** `d8f3a1b4e205_add_story_to_programmes.py` (`down_revision = "c5e2a7b9d104"`)
- **Admin Plan API (`PUT /api/v1/l3mon/admin/programmes`):**
  - Accepts optional `story` string per programme.
  - Returns `{"channels": ..., "programmes": ...}`.
- **Admin Release View (`GET /api/v1/l3mon/admin/release`):**
  - Includes `story` in each programme record.
- **Challenge Detail API (`GET /api/v1/challenges/<id>`):**
  - `response.data.l3mon.story`: string (empty string when unset).

### 3.2 Stories Page / Entrance (E2)
- **Route:** `GET /stories` (under Option 2)
  - Registered in `plugins/l3mon_theme/pages/story.py`.
  - Gated by `signed_in`, `verified`, `in_studio` (same as Guide).
  - Context `st`: `{stories: list, hasFinale: bool, empty: bool}`.
- **Guide Page Link:** Link to `/stories` in `platform-ui/src/pages/guide.html` and `_guide.html`.

### 3.3 Finale Setting (E3)
- **Setting:** `l3mon_finale_at`
- **API:** `PATCH /api/v1/configs` with `{"l3mon_finale_at": 1795876200}`
- **Function:** `CTFd.plugins.l3mon_core.settings.finale_at() -> int | None`

### 3.4 Rate-Limit Interceptor (E4)
- **Module:** `plugins/l3mon_theme/pages/errors.py`
- **Hook:** `app.after_request`
- **Output:** Status 429 HTML slate with headers:
  - `Retry-After: <seconds>`
  - `Cache-Control: no-store`
  - `Content-Type: text/html; charset=utf-8`

---

## 4. Test Strategy and Test Names

### 4.1 E4 Rate-Limit Tests (`plugins/l3mon_theme/tests/test_theme_rate_limit.py`)
- `test_rate_limit_browser_request_returns_themed_slate()`
- `test_rate_limit_api_request_keeps_ctfd_json()`
- `test_rate_limit_json_accept_header_keeps_json()`
- `test_rate_limit_xmlhttprequest_header_keeps_json()`
- `test_rate_limit_json_content_type_keeps_json()`
- `test_rate_limit_headers_retry_after_and_cache_control()`
- `test_rate_limit_other_theme_leaves_ctfd_json()`
- `test_rate_limit_non_429_untouched()`
- `test_rate_limit_head_and_options_untouched()`
- `test_rate_limit_hook_cost_on_normal_requests()`

### 4.2 E3 Finale Timestamp Tests (`plugins/l3mon_story/tests/test_finale_automatic_time.py`)
- `test_finale_at_setting_parsing_valid_and_invalid()`
- `test_finale_at_never_opens_before_event_start()`
- `test_finale_at_before_timestamp_remains_closed()`
- `test_finale_at_at_or_after_timestamp_opens_for_all()`
- `test_finale_at_does_not_break_crew_trigger_or_meter()`
- `test_finale_at_five_second_cache_does_not_hold_stale_closed()`
- `test_finale_at_query_budget_zero_extra_statements()`

### 4.3 E1 Programme Story Tests
- `plugins/l3mon_core/tests/test_story_migration.py`:
  - `test_story_migration_upgrades_and_downgrades()`
  - `test_story_migration_idempotent_run()`
  - `test_story_migration_preserves_existing_rows()`
- `plugins/l3mon_release/tests/test_plan_story.py`:
  - `test_plan_accepts_valid_story_line()`
  - `test_plan_trims_story_line()`
  - `test_plan_rejects_long_or_unsafe_story_line()`
  - `test_plan_omitted_story_preserves_existing_line()`
  - `test_plan_empty_string_clears_story_line()`
  - `test_release_view_includes_story_line()`
- `plugins/l3mon_board/tests/test_board_story_isolation.py`:
  - `test_challenge_detail_includes_story_in_l3mon_block()`
  - `test_board_json_never_contains_story()`
  - `test_guide_json_never_contains_story()`
  - `test_withheld_programme_detail_is_404()`
  - `test_panel_block_query_budget_unchanged()`

### 4.4 E2 Prototype and Theme Tests
- `platform-ui/tests/dev-stories.test.mjs`
- `plugins/l3mon_theme/tests/test_stories_page.py`
- Integration: `tests/integration/test_story_pieces.py`

---

## 5. Break-on-Purpose Verification Matrix

| Rule | Intentional Break | Catching Test |
|---|---|---|
| E4: Page request only | Invert `request.path.startswith("/api/")` | `test_rate_limit_api_request_keeps_ctfd_json` |
| E4: Respect Accept header | Ignore `request.accept_mimetypes` | `test_rate_limit_json_accept_header_keeps_json` |
| E4: Retry-After header | Omit `Retry-After` header | `test_rate_limit_headers_retry_after_and_cache_control` |
| E3: Before start block | Remove `current_phase().state == "before"` check | `test_finale_at_never_opens_before_event_start` |
| E3: Valid timestamp bound | Accept negative or string timestamp | `test_finale_at_setting_parsing_valid_and_invalid` |
| E1: Safe upsert | Overwrite `row.story = ""` when key omitted | `test_plan_omitted_story_preserves_existing_line` |
| E1: Data isolation | Leak `story` field in `board.build()` | `test_board_json_never_contains_story` |
