# SP5 & SP8: Anti-Abuse and "Continue with Google" Audit Follow-up (Implementation Plan)

> Follow-up to the 18 October 2026 audit findings for `plugins/l3mon_guard` and `plugins/l3mon_auth`. Built test-first, milestone by milestone. All rules adhere to L3m0nCTF 2026 platform standards.

**Goal:** Resolve audit findings F1 through F5 across `l3mon_guard` and `l3mon_auth`:
1. **F1:** Eliminate the $O(N)$ full-table user scan during registration and Google sign-up by introducing an indexed `l3mon_email_norm` table, SQLAlchemy lifecycle listeners (`after_insert`/`after_update` on `Users`), and `claim()`-guarded lazy batch backfilling (500 rows/batch).
2. **F2:** Prevent campus-wide login CAPTCHA lockouts by ignoring IP failure buckets for addresses in `L3MON_GUARD_CAMPUS_NETS` while preserving per-account failure counters.
3. **F3:** Upgrade disposable email domain detection to the full CC0 `disposable-email-domains` open list (downloaded once at dev-time), while ensuring major free providers (Gmail, Outlook, Yahoo) are never blocked.
4. **F4:** Enforce the `MIN_KEY_REFRESH_INTERVAL` cooldown on Google JWKS key refreshes even when an unknown `kid` is presented, unless the cache is empty.
5. **F5:** Security hardening and contract polish:
   - Restrict `TURNSTILE_VERIFY_URL` to Cloudflare challenges or localhost/127.0.0.1 in test mode.
   - Standardize wording from "wrong attempts" to "flag attempts" across code, docs, and UI.
   - Move client IP from free-text `detail` to the dedicated `address` column for `captcha_unverified` evidence.
   - Document theme template contracts and nginx CSP recommendations in `docs/deploy/identity.md`.

---

## 1. What was read and measured in the codebase

1. **Current Full-Table User Scans (Audit finding G-2):**
   - `plugins/l3mon_guard/policies/email_norm.py:75-81`: Iterates over `Users.query.all()` and normalizes every user's email in Python on every single registration attempt.
   - `plugins/l3mon_auth/oauth.py:213-221`: In `_check_email_collision(email)`, iterates over `Users.query.all()` to check for collisions against existing accounts.
   - During opening minutes, hundreds of players register concurrently; loading thousands of ORM objects per request consumes worker CPU and memory.
2. **Campus Network IP Throttling (Audit finding G-3):**
   - `plugins/l3mon_guard/turnstile.py:108-124`: `is_login_captcha_required` triggers when `guard:login_fail:ip:{ip}` >= 3.
   - Behind a campus NAT or dormitory proxy, 3 failed logins across 3 different users force every user at that IP to solve a CAPTCHA.
   - `plugins/l3mon_guard/limits.py:61-73` already provides `is_campus_ip(ip_str)` parsing `L3MON_GUARD_CAMPUS_NETS`.
3. **Google JWKS Key Refresh Cooldown (Audit finding G-5):**
   - `plugins/l3mon_auth/jwt.py:196-197`: Calls `fetch_jwks(force=True)` whenever `kid not in _JWKS_CACHE`.
   - `fetch_jwks` bypasses `MIN_KEY_REFRESH_INTERVAL` (60s) when `force=True`.
4. **Turnstile Configuration & Evidence Recording (Audit finding G-6):**
   - `plugins/l3mon_guard/turnstile.py:57-58`: Reads `TURNSTILE_VERIFY_URL` without host restrictions.
   - `plugins/l3mon_guard/turnstile.py:181`: `record("captcha_unverified", detail=f"action={action}; ip={remote_ip or ''}")` stores IP in `detail` rather than the `address` column.
   - `plugins/l3mon_guard/limits.py:30-36` and related docs reference "wrong attempts", but the counter increments on all submissions.

---

## 2. Key Decisions and Alternatives Rejected

| Topic | Options | Choice | Rationale |
|---|---|---|---|
| **Indexed Email Normalization Storage** | (a) Add `norm` column to `users`<br/>(b) New table `l3mon_email_norm(user_id unique, norm unique)` | **(b)** New table `l3mon_email_norm` | CTFd core `users` table must never be altered (pinned CTFd 3.8.8 rule). A plugin-owned table with unique foreign key provides clean isolation, Alembic migrations, and SQLite/MariaDB portability. |
| **Email Normalization Synchronization** | (a) Intercept register/profile views only<br/>(b) SQLAlchemy `after_insert` and `after_update` event listeners on `Users` | **(b)** Event listeners | Users can be created/updated via CTFd standard registration, Google OIDC, Login with CTFtime, or admin panel. Event listeners automatically capture all paths without editing other plugins or core code. Wrapped with try/except so account creation is never broken. |
| **Existing User Backfilling** | (a) Run migration data script on startup<br/>(b) Lazy batch backfill (500 rows) on demand guarded by `claim()` | **(b)** Lazy batch backfill | Avoids startup delays or lock contention on large databases. `claim("l3mon_email_norm_backfill", 10.0)` guarantees only one worker runs a small 500-row batch at a time. |
| **Shared Collision Logic** | (a) Duplicate SQL logic across guard and auth<br/>(b) Expose `is_email_disguise_used` in guard and import lazily in auth | **(b)** Expose in guard, import lazily in auth | Single source of truth. Both plugins share the identical lookup semantics and query budget. |
| **Campus Login CAPTCHA Policy** | (a) Completely disable CAPTCHA for campus IPs<br/>(b) Ignore IP failure bucket for campus IPs, retain per-account bucket | **(b)** Ignore IP bucket, retain per-account bucket | Protects against credential stuffing on specific accounts even on campus networks, while preventing campus-wide denial-of-service from collective typos. |
| **JWKS Key Refresh Cooldown** | (a) Allow unconditional refresh for any unknown kid<br/>(b) Honor `MIN_KEY_REFRESH_INTERVAL` for unknown kids unless cache is empty | **(b)** Honor cooldown unless cache empty | Prevents outbound flood requests to Google endpoints on malicious or unknown `kid`s. If cache is empty at startup, it fetches immediately; once populated, at most one fetch per 60 seconds. |

---

## 3. Interfaces and Data Contracts

### 3.1 Table Schema: `l3mon_email_norm`
```sql
CREATE TABLE l3mon_email_norm (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, -- or INT AUTO_INCREMENT in MariaDB
    user_id INTEGER NOT NULL,
    norm VARCHAR(255) NOT NULL,
    CONSTRAINT uq_l3mon_email_norm_user_id UNIQUE (user_id),
    CONSTRAINT uq_l3mon_email_norm_norm UNIQUE (norm),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_l3mon_email_norm_norm ON l3mon_email_norm (norm);
```

### 3.2 Function Signature: `is_email_disguise_used`
```python
def is_email_disguise_used(email: str, exclude_user_id: int | None = None) -> bool:
    """Check if any existing registered account has the same normalized email form.
    
    Executes an indexed O(1) query against l3mon_email_norm.
    Executes lazy claim()-guarded backfill if unbackfilled accounts remain.
    """
```

### 3.3 Function Signature: `is_login_captcha_required`
```python
def is_login_captcha_required(username: str = "", ip: str = "") -> bool:
    """Return True if login requires CAPTCHA verification due to recent failures.
    
    Ignores ip bucket if ip is in L3MON_GUARD_CAMPUS_NETS.
    """
```

### 3.4 Verification URL Validation: `_get_verify_url`
```python
def _get_verify_url() -> str:
    """Return validated Turnstile verify URL.
    
    Allows https://challenges.cloudflare.com/...
    Allows http(s)://localhost or 127.0.0.1 ONLY when L3MON_AUTH_TEST_MODE=1.
    Falls back to default Cloudflare endpoint on any disallowed URL.
    """
```

---

## 4. Test Plan and Break-on-Purpose Verification

### 4.1 Test Cases to Implement First
1. `test_guard_email_norm_migration.py`:
   - Runs migration upgrade and downgrade twice on SQLite and MariaDB.
   - Verifies unique constraint on `norm` and `user_id`.
2. `test_guard_email_norm_listener.py`:
   - Account creation via `Users` inserts corresponding `l3mon_email_norm` row.
   - Account email update updates `l3mon_email_norm` row.
   - Account deletion cascades and removes `l3mon_email_norm` row.
   - Exception in listener does not break `Users` insertion.
3. `test_guard_email_query_budget.py`:
   - Populates database with 50 users, measures queries on registration / disguise check.
   - Populates database with 5,000 users, measures queries on registration / disguise check.
   - Asserts query count is bounded and identical regardless of user count ($O(1)$).
4. `test_guard_campus_login_captcha.py`:
   - Campus IP with 10 failures across 10 accounts: 11th account does not require CAPTCHA.
   - Non-campus IP with 10 failures across 10 accounts: 11th account requires CAPTCHA.
   - Single account with 3 failures: requires CAPTCHA on both campus and non-campus IP.
5. `test_auth_google_jwks_refresh_rate_limit.py`:
   - Initial empty cache fetches JWKS immediately.
   - Unknown `kid` within 60s cooldown does NOT trigger network fetch (refuses with unknown kid).
   - Unknown `kid` after 60s cooldown triggers network fetch.
6. `test_guard_turnstile_url_whitelist.py`:
   - `TURNSTILE_VERIFY_URL` with evil host rejected and defaulted to Cloudflare.
   - `TURNSTILE_VERIFY_URL` with localhost accepted only when `L3MON_AUTH_TEST_MODE=1`.
7. `test_guard_disposable_top_providers.py`:
   - Verifies top 20 providers (gmail, outlook, yahoo, hotmail, icloud, proton, etc.) are NOT in disposable list.
   - Verifies subdomains of disposable domains are blocked.

### 4.2 Break-on-Purpose Matrix (for Handoff)
- **F1 Rule:** Replace full table scan with indexed lookup.
  - *Break:* Remove index or revert to `Users.query.all()`.
  - *Caught by:* `test_guard_email_query_budget.py` (query count or latency spike with 5,000 users).
- **F1 Race Rule:** Unique constraint on `norm` prevents duplicate disguised accounts.
  - *Break:* Drop `uq_l3mon_email_norm_norm`.
  - *Caught by:* `test_guard_email_norm_concurrent_disguise`.
- **F2 Campus Rule:** Campus IPs exempt from IP failure bucket.
  - *Break:* Remove `is_campus_ip` check from IP bucket logic.
  - *Caught by:* `test_guard_campus_login_captcha.py` (11th account on campus fails).
- **F4 JWKS Cooldown Rule:** Unknown kid honors refresh interval unless cache empty.
  - *Break:* Revert `jwt.py` to `fetch_jwks(force=True)`.
  - *Caught by:* `test_auth_google_jwks_refresh_rate_limit.py`.
- **F5(a) URL Whitelist Rule:** Disallowed verify URLs fall back to Cloudflare.
  - *Break:* Blindly return `os.environ.get("TURNSTILE_VERIFY_URL")`.
  - *Caught by:* `test_guard_turnstile_url_whitelist.py`.
