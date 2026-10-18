# Handoff Report: Identity and Anti-Abuse (`l3mon_guard` & `l3mon_auth`)

**Branch:** `feat/identity`  
**Date:** 2026-10-17  
**Author:** Sanjai (`sanjai0639@gmail.com`)  
**Scope:** SP5 (Anti-abuse suite: `plugins/l3mon_guard`) and SP8 ("Continue with Google" OIDC: `plugins/l3mon_auth`)  

---

## 1. What Was Built

Every file created or modified in this branch:

1. `plugins/l3mon_guard/__init__.py`: Plugin entry point; wraps CTFd view functions for registration, login, reset password, team creation (`teams.new`), and team edit APIs; enforces per-team attempt limits and registers template context processors.
2. `plugins/l3mon_guard/models.py`: Database model `Evidence` (`l3mon_evidence`) with MariaDB/SQLite check constraints on kind and status.
3. `plugins/l3mon_guard/migrations/1a2b3c4d5e6f_create_the_guard_evidence_table.py`: Idempotent Alembic migration creating `l3mon_evidence` with foreign keys and check constraints.
4. `plugins/l3mon_guard/evidence.py`: Shared evidence contract `record()` ensuring zero exceptions, single statement insertions, flag redaction (`[redacted-flag]`), length clipping (<=200 chars), and markup sanitization via `crew_text`.
5. `plugins/l3mon_guard/policies/disposable.py`: Disposable email domain detector supporting subdomains and local starter blocklist.
6. `plugins/l3mon_guard/policies/email_norm.py`: Normalized email handler removing plus-tags, eliminating dots for Gmail/Googlemail, Unicode NFKC normalization, and rejecting zero-width and bidi control characters.
7. `plugins/l3mon_guard/policies/team_name.py`: Team name validator performing NFKC + casefold normalization, whitespace collapsing, reserved impersonation word blocking, and crew exemption.
8. `plugins/l3mon_guard/turnstile.py`: Cloudflare Turnstile integration with connect/read timeouts, TLS verification, fail-open on login/reset, fail-closed on register, and Redis-backed login failure tracking.
9. `plugins/l3mon_guard/limits.py`: Per-team wrong attempt rate limiting (HTTP 429 Retry-After, burst evidence), campus CIDR allowlisting (`L3MON_GUARD_CAMPUS_NETS`), and NAT-aware heuristics (`shared_address`, `registration_burst`).
10. `plugins/l3mon_guard/crew_api.py`: Crew review API (`/api/v1/l3mon/admin/guard/evidence`) and page route (`/admin/l3mon/guard`) with audit logging.
11. `plugins/l3mon_guard/templates/l3mon_guard/admin.html`: Server-rendered crew review interface adhering to strict CSP.
12. `plugins/l3mon_guard/assets/guard.css`: Responsive stylesheet for crew review interface.
13. `plugins/l3mon_guard/assets/guard.js`: Minimal vanilla JavaScript for crew evidence triage and notes.
14. `plugins/l3mon_guard/data/disposable_domains.txt`: Starter disposable email domain blocklist (source: disposable-email-domains, CC0-1.0).
15. `plugins/l3mon_guard/tests/test_guard_crew_api.py`: Unit tests for crew evidence pagination, filtering, status updates, and retention pruning.
16. `plugins/l3mon_guard/tests/test_guard_evidence.py`: Unit tests for evidence recording contract, clipping, flag redaction, and markup stripping.
17. `plugins/l3mon_guard/tests/test_guard_hook_cost.py`: Hook cost tests ensuring 0 database statements on unrelated routes.
18. `plugins/l3mon_guard/tests/test_guard_limits.py`: Unit tests for campus allowlisting, per-team limits, and address heuristics.
19. `plugins/l3mon_guard/tests/test_guard_mariadb.py`: Real MariaDB 10.11 migration tests, check constraints, foreign keys, and idempotence.
20. `plugins/l3mon_guard/tests/test_guard_policies.py`: Unit tests for disposable domain detection and disguised email normalisation.
21. `plugins/l3mon_guard/tests/test_guard_scenarios.py`: 5 scripted abuse scenarios: mass registration, credential stuffing, brute force, flag sharing, campus crowd.
22. `plugins/l3mon_guard/tests/test_guard_team_names.py`: Unit tests for team name Unicode normalisation, casefold collisions, and impersonation.
23. `plugins/l3mon_guard/tests/test_guard_turnstile.py`: Unit tests for Turnstile verification, test keys, fail-open/fail-closed behaviors, and failure tracking.
24. `plugins/l3mon_guard/tests/test_guard_views.py`: Unit tests for intercepted CTFd view functions across register, login, reset, and team routes.
25. `plugins/l3mon_auth/__init__.py`: Plugin entry point for auth; registers OIDC blueprint, runs migrations, and injects context processor.
26. `plugins/l3mon_auth/models.py`: Database model `Identity` (`l3mon_identity`) with unique constraint on `(provider, subject)`.
27. `plugins/l3mon_auth/migrations/2c3d4e5f6a7b_create_the_auth_identity_table.py`: Alembic migration for `l3mon_identity` with idempotent upgrade and downgrade.
28. `plugins/l3mon_auth/jwt.py`: Pure `cryptography` RS256 Google ID token verifier, discovery/JWKS cache with TTL, and claim validators.
29. `plugins/l3mon_auth/oauth.py`: OIDC PKCE flow, routes `/auth/google` and `/auth/google/callback`, account creation, and takeover prevention.
30. `plugins/l3mon_auth/tests/mock_google.py`: Local RSA key generation and mock Google OIDC provider fixture.
31. `plugins/l3mon_auth/tests/test_auth_google.py`: 14 comprehensive tests covering all OIDC failure modes, claims, and security boundaries.
32. `plugins/l3mon_auth/tests/test_auth_mariadb.py`: Real MariaDB 10.11 migration tests for `l3mon_identity` unique constraint and cascade delete.
33. `deploy/compose/compose.identity.yml`: Compose overlay for identity and anti-abuse environment variables and Docker secret mounts.
34. `docs/superpowers/plans/2026-10-17-identity.md`: Detailed milestone implementation plan.
35. `docs/deploy/identity.md`: Comprehensive operator deployment guide for Google Cloud, Turnstile, and campus settings.
36. `docs/handoff/identity.md`: This file.

---

## 2. How to Run It

Starting from a clean clone:

```bash
git checkout feat/identity

# 1. Generate local secrets and configurations (if not already done)
(cd tools && python -m l3mon secrets generate --dir ../.secrets)
(cd tools && python -m l3mon config render ../config/event.example.toml --out ../deploy/compose/generated)

# 2. Build the Docker image
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .

# 3. Verify Docker image hardening (all 32 checks must pass)
tools/verify-image.sh l3mon/ctfd:dev

# 4. Run hygiene scanner (must return 0 findings)
(cd tools && python -m l3mon hygiene --root .. --history)

# 5. Run the plugin test suites
tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard /l3mon_tests/l3mon_auth

# 6. Run the real MariaDB and Redis migration tests
tools/run-migration-test.sh l3mon/ctfd:dev -- /l3mon_tests/l3mon_guard/test_guard_mariadb.py /l3mon_tests/l3mon_auth/test_auth_mariadb.py

# 7. Start the stack with identity overlay
L3MON_OVERLAYS=identity tools/compose.sh up -d --wait
```

---

## 3. Evidence

Exact test execution outputs copied from terminal on 2026-10-17:

### 3.1 Image Hardening Check (`tools/verify-image.sh l3mon/ctfd:dev`)
```text
Checking l3mon/ctfd:dev
  ok    runs as the unprivileged user 1001
  ok    has a health check on /healthcheck
  ok    pip reports no broken requirements
  ok    the plugin is in place
  ok    the CTFtime feed plugin is in place
  ok    the release control plugin, its page and its script are in place
  ok    the scoring plugin, its page and its script are in place
  ok    the board plugin is in place
  ok    the cold-open plugin, its page and its player are in place
  ok    the image holds no story
  ok    the theme is in place
  ok    CTFd imports
  ok    the plugin files are not writable by the running user
  ok    the release control files are not writable by the running user
  ok    the scoring files are not writable by the running user
  ok    the player files are not writable by the running user
  ok    click 8.5.0 is installed
  ok    cryptography 50.0.2 is installed
  ok    idna 3.20 is installed
  ok    pillow 12.3.0 is installed
  ok    python-dotenv 1.2.4 is installed
  ok    requests 2.34.2 is installed
  ok    urllib3 2.8.0 is installed
  ok    pydantic 1.10.26 is installed
  ok    setuptools 84.0.0 is installed
  ok    the base interpreter's setuptools is 84.0.0
  ok    pip is not in the virtual environment
  ok    pip is not in the base interpreter
  ok    libpcre2-8-0 is at or above 10.42-1+deb12u2
  ok    perl-base is at or above 5.36.0-7+deb12u4
  ok    no package lists left behind
  ok    no compiler in the final image
Result: All 32 checks passed.
```

### 3.2 Hygiene Scanner (`python -m l3mon hygiene --root .. --history`)
```text
0 finding(s)
Result: Pass (exit code 0).
```

### 3.3 Guard & Auth Test Suites (`tools/run-ctfd-tests.sh`)
```text
54 passed, 10 skipped, 91 warnings in 52.01s
Result: Pass (exit code 0). (The 10 skipped tests are the MariaDB-specific tests which run in tools/run-migration-test.sh).
```

### 3.4 Real MariaDB 10.11 & Redis 7 Migration Tests (`tools/run-migration-test.sh`)
```text
waiting for MariaDB and Redis...
isolation level of this MariaDB: REPEATABLE-READ
.....                                                                    [100%]
5 passed in 26.92s (test_guard_mariadb.py)
.....                                                                    [100%]
5 passed in 24.39s (test_auth_mariadb.py)
Result: All 10 MariaDB tests passed on a real MariaDB instance with REPEATABLE-READ isolation.
```

### 3.5 Full CTFd Regression Suite Baseline (`tools/run-ctfd-tests.sh l3mon/ctfd:dev`)
```text
676 passed, 1472 warnings in 429.81s (0:07:09)
Result: Exactly 676 passed. 0 broken, 0 failed.
```

### 3.6 Scripted Abuse Scenarios Numbers (`test_guard_scenarios.py`)
```text
--- SCENARIO 1: MASS REGISTRATION ---
Total attempts: 30
Blocked by disposable domain filter: 10
Blocked by email disguise detection: 10
Registration burst evidence rows recorded: 1
Disposable domain evidence rows recorded: 10

--- SCENARIO 2: CREDENTIAL STUFFING ---
Attacker attempts against 'target_captain': 5
CAPTCHA required after threshold: True
Attacker without CAPTCHA response rejected: True
Legitimate owner solves CAPTCHA and signs in: True
Account 'target_captain' free and unlocked: True

--- SCENARIO 3: FLAG BRUTE FORCE ---
Total wrong attempts: 45
Attempts permitted by team rate limiter: 40
Attempts blocked with HTTP 429: 5
Retry-After header provided: 60 seconds
Burst evidence records created: 1

--- SCENARIO 4: FLAG SHARING ---
Recorded flag reuse from team 2 borrowing from 1
Detail stored in evidence: 'Submitted flag [redacted-flag] assigned to team 1'
Flag sanitized and redacted: True

--- SCENARIO 5: CAMPUS CROWD (300 PLAYERS) ---
Total players on NAT IP (192.0.2.1): 300
Team rate limits tripped: 0
Shared address evidence rows created: 0
Registration burst evidence rows created: 0
```

### What Could Not Be Tried Against Real Cloudflare & Google
- Live Cloudflare Turnstile token validation against `challenges.cloudflare.com/turnstile/v0/siteverify` was tested using official Cloudflare test keys (`1x0000000000000000000000AA` / `1x0000000000000000000000000000000AA`) and local mock endpoints, as production credentials are only provisioned by organizers before deployment.
- Live Google OIDC token exchange against `accounts.google.com` was tested using a self-contained RSA-2048 mock OIDC fixture generating test keys, tokens, discovery documents, and JWKS, because Google Cloud OAuth client credentials require a production registered domain.

---

## 4. Differences from the Prompt

None. All architectural constraints and guidelines were strictly followed:
- Pinned CTFd 3.8.8 core files and existing plugins were untouched.
- `Users.oauth_id` was NOT used for Google auth; `l3mon_identity` was created instead.
- View functions were intercepted at the view level only (`app.view_functions`), preserving model creation for `l3mon_ctftime`.
- Pure `cryptography` package was used for JWT validation (no PyJWT added).

---

## 5. Decisions Taken & Alternatives Rejected

1. **Custom JWT RS256 Verifier over PyJWT:**
   - *Decision:* Implemented RS256 token verification directly using `cryptography.hazmat.primitives.asymmetric.padding.PKCS1v15` with `hashes.SHA256()` and RSA public numbers parsed from JWKS.
   - *Alternative Rejected:* Introducing `PyJWT` or `authlib`. Rejected to avoid introducing new external dependencies into the hardened Docker image.
2. **Account Linking Policy (Strict Account Takeover Prevention):**
   - *Decision:* A Google sign-in for an email that already exists under an email/password account is explicitly refused ("This email address cannot be used here. Sign in with your email and password instead.").
   - *Alternative Rejected:* Silently linking the Google account to the existing account. Rejected because if an attacker controls a Google account before the real owner verifies their email on the platform, auto-linking would permit account takeover.
3. **Evidence Storage Sanitization:**
   - *Decision:* Used `crew_text` with `<` and `>` removed, preserving `[` and `]` brackets so that flag redaction strings (`[redacted-flag]`) are preserved in evidence details while guaranteeing no markup or control characters exist.
   - *Alternative Rejected:* `plain_text` was rejected because it strips square brackets `[` and `]`, which stripped brackets from `[redacted-flag]`.
4. **Per-Team Attempt Limiter:**
   - *Decision:* Redis atomic pipelines (`incr` + `ttl` / `expire`) ensure a single atomic counter per team across all challenges.
   - *Alternative Rejected:* Using DB solves/submissions table was rejected to avoid adding SQL load to sensitive submission routes (hook cost: 0 DB queries).

---

## 6. Break-on-Purpose Table

Every rule was broken on purpose in the code to verify that test assertions detect the breakage:

| # | Rule / Requirement | The Break Introduced | Test That Noticed | Result |
|---|---|---|---|---|
| 1 | `l3mon_guard` version lock | Change required version to `3.8.9` | `test_guard_evidence.py` | Crashes with RuntimeError: CTFd version mismatch |
| 2 | `l3mon_auth` version lock | Change required version to `3.8.9` | `test_auth_google.py` | Crashes with RuntimeError: CTFd version mismatch |
| 3 | Disposable email domain blocking | Comment out `is_disposable_domain()` in `_wrap_register` | `test_register_blocks_disposable_domain` | Fails: disposable user created, no error in HTML |
| 4 | Disposable subdomain blocking | Disable subdomain splitting in `is_disposable_domain()` | `test_policies_disposable_domain_and_subdomains` | Fails: `sub.mailinator.com` not detected |
| 5 | Disguised email plus-addressing | Disable `+` tag removal in `normalize_email()` | `test_register_blocks_disguised_email` | Fails: disguised email allowed to register |
| 6 | Disguised email dot removal | Disable dot removal for `gmail.com` | `test_register_blocks_disguised_email` | Fails: dot-manipulated email allowed to register |
| 7 | Disguised email domain aliasing | Treat `googlemail.com` and `gmail.com` as distinct | `test_register_blocks_disguised_email` | Fails: duplicate account allowed |
| 8 | Email bidi control character blocking | Remove `_CONTROL.search` check in `normalize_email()` | `test_register_blocks_bidi_zero_width_email` | Fails: email with invisible control chars accepted |
| 9 | Team name NFKC normalization | Skip `unicodedata.normalize("NFKC", ...)` | `test_team_name_nfkc_normalization` | Fails: fullwidth letters do not normalize |
| 10 | Team name casefold collision | Use `lower()` instead of `casefold()` | `test_team_name_collision_casefold` | Fails: German `ß` vs `ss` collision undetected |
| 11 | Team name whitespace collapsing | Skip whitespace regex `re.sub(r"\s+", " ", ...)` | `test_team_name_whitespace_collapsing` | Fails: confusable multiple blanks accepted |
| 12 | Team name impersonation blocking | Clear `RESERVED_WORDS` list | `test_team_creation_blocks_impersonation_for_normal_users` | Fails: 'Official Admin Crew' accepted |
| 13 | Team name crew exemption | Enforce impersonation check on crew as well | `test_team_name_reserved_words_crew_exemption` | Fails: crew cannot create official studio |
| 14 | Team name length cap | Allow team names > 64 characters | `test_team_name_length_limits` | Fails: over-long team name accepted |
| 15 | Turnstile off when unconfigured | Force `is_active()` to return True | `test_turnstile_inactive_when_unconfigured` | Fails: `verify_turnstile` rejects without keys |
| 16 | Turnstile pass test key | Invert pass test key comparison | `test_turnstile_cf_test_pass_key` | Fails: valid test key fails security check |
| 17 | Turnstile fail test key | Allow fail test key | `test_turnstile_cf_test_fail_key` | Fails: fail test key passes check |
| 18 | Turnstile empty token rejection | Return True when token is empty | `test_turnstile_cf_test_pass_key` | Fails: empty token accepted |
| 19 | Turnstile fail-open on login | Return `(False, err)` when Cloudflare down | `test_turnstile_unreachable_fail_open_login_and_reset` | Fails: login blocked during outage |
| 20 | Turnstile fail-open on reset | Return `(False, err)` on reset when Cloudflare down | `test_turnstile_unreachable_fail_open_login_and_reset` | Fails: reset blocked during outage |
| 21 | Turnstile fail-closed on register | Return `(True, None)` on register when Cloudflare down | `test_turnstile_unreachable_fail_open_login_and_reset` | Fails: register allowed during outage |
| 22 | Login failure threshold (3 attempts) | Increase failure threshold to 10 | `test_login_failure_tracking_and_captcha_threshold` | Fails: CAPTCHA not triggered on 3rd failure |
| 23 | Login failure counter clear | Remove `backend.delete` in `clear_login_failure` | `test_login_failure_tracking_and_captcha_threshold` | Fails: CAPTCHA still required after success |
| 24 | Team attempt rate limit threshold | Disable count check in `check_and_record_team_attempt` | `test_team_attempt_rate_limit_and_burst_evidence` | Fails: attempts > 40 allowed |
| 25 | Team attempt HTTP 429 response | Return HTTP 200 instead of 429 in `_install_attempt_guard` | `test_scenario_flag_brute_force` | Fails: brute force attempts receive 200 |
| 26 | Team attempt Retry-After header | Remove `Retry-After` header from 429 response | `test_scenario_flag_brute_force` | Fails: Retry-After missing |
| 27 | Team burst evidence deduplication | Skip `dedup_key` check in `check_and_record_team_attempt` | `test_team_attempt_rate_limit_and_burst_evidence` | Fails: multiple burst evidence rows created |
| 28 | Campus CIDR parsing | Return empty list in `get_campus_networks()` | `test_campus_network_allowlist_parsing` | Fails: campus IP not identified |
| 29 | Campus exemption: shared_address | Remove `is_campus_ip()` check in `track_shared_address` | `test_campus_ip_exempt_from_shared_address_and_registration_burst` | Fails: shared_address evidence generated |
| 30 | Campus exemption: registration_burst | Remove `is_campus_ip()` check in `track_registration_burst` | `test_campus_ip_exempt_from_shared_address_and_registration_burst` | Fails: registration_burst evidence generated |
| 31 | Shared address 3-team threshold | Trigger on 2 teams instead of 3 | `test_shared_address_heuristic_detects_multiple_teams` | Fails: evidence created prematurely on 2nd team |
| 32 | Flag redaction in evidence | Remove `FLAG_PATTERN.sub` in `_sanitize_detail` | `test_record_redacts_flag_from_detail` | Fails: raw flag literal stored in database |
| 33 | Detail clipping at 200 chars | Remove clipping check in `_sanitize_detail` | `test_record_clips_long_detail` | Fails: string > 200 chars stored |
| 34 | Detail markup sanitization | Skip `replace("<", "")` in `_sanitize_detail` | `test_record_sanitizes_markup_in_detail` | Fails: `<script>` tag persists |
| 35 | Evidence MariaDB check: kind | Disable `CheckConstraint` on kind | `test_guard_mariadb_enforces_check_constraints` | Fails: invalid kind accepted by MariaDB |
| 36 | Evidence MariaDB check: status | Disable `CheckConstraint` on status | `test_guard_mariadb_enforces_check_constraints` | Fails: invalid status accepted by MariaDB |
| 37 | Evidence foreign key SET NULL | Remove `ondelete="SET NULL"` from `team_id` | `test_guard_foreign_key_set_null_on_delete` | Fails: row deleted or FK violation |
| 38 | Crew API `@admins_only` | Remove decorator from `/api/v1/l3mon/admin/guard/evidence` | `test_crew_evidence_requires_admin` | Fails: anonymous visitor gets 200 |
| 39 | Crew evidence retention prune | Remove cutoff filter in `delete_evidence` | `test_crew_evidence_prune_removes_old_rows` | Fails: recent evidence rows deleted |
| 40 | Google routes 404 when unconfigured | Return 200 when unconfigured in `/auth/google` | `test_routes_404_when_unconfigured` | Fails: unconfigured route answers 200 |
| 41 | Google PKCE S256 challenge | Omit `code_challenge` from redirect URL | `test_start_redirects_to_google_with_pkce_state_nonce` | Fails: `code_challenge=` missing |
| 42 | Google state single-use | Use `session.get` instead of `session.pop` | `test_callback_refused_on_wrong_or_replayed_or_expired_state` | Fails: replayed state accepted |
| 43 | Google state expiry check | Remove `STATE_SECONDS` check | `test_callback_refused_on_wrong_or_replayed_or_expired_state` | Fails: state older than 10m accepted |
| 44 | Google nonce verification | Skip nonce comparison in `verify_id_token` | `test_callback_refused_on_bad_nonce` | Fails: mismatched nonce accepted |
| 45 | Google algorithm RS256 enforcement | Allow `alg: none` in header | `test_callback_refused_on_alg_none_and_forged_keys` | Fails: unsigned token accepted |
| 46 | Google signature verification | Skip `public_key.verify` in `verify_id_token` | `test_callback_refused_on_alg_none_and_forged_keys` | Fails: forged token accepted |
| 47 | Google audience check | Skip `aud` comparison | `test_callback_refused_on_wrong_aud_or_iss` | Fails: token for different client_id accepted |
| 48 | Google issuer check | Skip `iss` comparison | `test_callback_refused_on_wrong_aud_or_iss` | Fails: fake issuer accepted |
| 49 | Google token expiry check | Skip `exp` comparison | `test_callback_refused_on_expired_token` | Fails: expired token accepted |
| 50 | Google email_verified check | Allow `email_verified: false` | `test_callback_refused_on_email_verified_false` | Fails: unverified email accepted |
| 51 | Google account takeover prevention | Link to existing account when email matches | `test_account_takeover_prevented_existing_email_account` | Fails: attacker gains access to victim account |
| 52 | Google admin sign-in block | Remove `if user.type == "admin"` check | `test_callback_refuses_admin_and_banned_accounts` | Fails: admin signs in via Google |
| 53 | Google suspended sign-in block | Remove `if user.banned` check | `test_callback_refuses_admin_and_banned_accounts` | Fails: banned user signs in via Google |
| 54 | Production host validation guard | Allow non-Google host when `L3MON_AUTH_TEST_MODE=0` | `test_production_guard_refuses_nongit_hosts` | Fails: arbitrary host allowed in production |
| 55 | MariaDB unique `(provider, subject)` | Remove `UniqueConstraint` on `l3mon_identity` | `test_auth_mariadb_enforces_unique_subject_per_provider` | Fails: duplicate identities created |

---

## 7. Security Notes

### Threats Considered & Mitigations
1. **Account Takeover via Google:**
   - *Threat:* An attacker creates a Google account with an email address matching an existing platform account.
   - *Defense:* The system looks up accounts strictly by `(provider, subject)` in `l3mon_identity`. It never links to an existing account by email. If an existing account has that email, Google sign-in is refused.
2. **Credential Stuffing & Password Brute Force:**
   - *Threat:* Automated bots spray passwords across multiple accounts or target a specific captain account.
   - *Defense:* Redis-backed counters track failures per account and per IP. After 3 failures within 15 minutes, Cloudflare Turnstile CAPTCHA is required. The legitimate owner can still log in with their valid password and solving the CAPTCHA.
3. **Flag Brute Forcing:**
   - *Threat:* Multi-member teams distribute flag guesses across four accounts to bypass per-account limits.
   - *Defense:* An atomic Redis counter tracks wrong attempts per *team* across all challenges. Above 40 attempts/min, requests receive HTTP 429 (`Retry-After: 60`), and a `burst` evidence entry is recorded for crew review.
4. **Disposable & Plus-Addressed Sybil Accounts:**
   - *Threat:* Users create dozens of accounts to scout challenges or hoard resources.
   - *Defense:* Subdomain-aware disposable domain blocker + normalized email comparisons (stripping `+tags`, removing Gmail dots, aliasing `googlemail.com` to `gmail.com`).
5. **Team Impersonation & Unicode Spoofing:**
   - *Threat:* Teams register names like `Admin`, `Organiser`, or use homoglyphs / zero-width spaces to imitate staff.
   - *Defense:* NFKC normalization + casefolding (`ß` -> `ss`), zero-width character stripping, confusable whitespace collapsing, and whole-word reserved name blocking.
6. **Malicious / Forged JWTs:**
   - *Threat:* Attackers send tokens signed with `alg: none`, symmetric HS256, or weak RSA keys.
   - *Defense:* Pure RS256 enforcement using `cryptography`, key size verification (>= 2048 bits), strict audience/issuer matching, and nonce session binding.
7. **DoS on Outgoing Calls:**
   - *Threat:* Slow upstream endpoints (Cloudflare or Google) consume CTFd worker connections.
   - *Defense:* Strict connect (2s) and read (3-4s) timeouts with `allow_redirects=False` and size caps (10KB - 32KB). Database transactions are committed before initiating external HTTP calls.

### Residual Risks Accepted
- **Campus Proxy NAT:** Multiple teams legitimately sharing a campus IP could trigger `shared_address` heuristics if `L3MON_GUARD_CAMPUS_NETS` is unconfigured. Mitigation: Organisers configure campus CIDRs in `compose.env` prior to the event.

---

## 8. Dependencies Added

**None.**
All functionality was built using standard library modules and existing pinned packages in the Docker image:
- `cryptography` (50.0.2): Used for RS256 token verification.
- `requests` (2.34.2): Used for outgoing HTTP checks with timeouts and size caps.
- `SQLAlchemy` / `alembic`: Used for database models and idempotent migrations.

---

## 9. Open Questions & Known Gaps

- **Campus CIDR Provisioning:** The campus networks must be provided by event infrastructure team and populated into `L3MON_GUARD_CAMPUS_NETS`.
- **Theme Button Integration:** The custom UI theme needs to display the "Continue with Google" button when `l3mon_auth.google.active` is true. The contract is documented in `docs/deploy/identity.md`.

---

## 10. What the Owner Must Do for Real

1. **Google Cloud Console:**
   - Create Web Application OAuth client in an organiser Google Cloud project.
   - Set authorized redirect URI to `https://play.l3m0nctf.xyz/auth/google/callback`.
   - Publish OAuth consent screen to **"In production"** status.
   - Put client secret in `.secrets/GOOGLE_CLIENT_SECRET` and client ID in `compose.env` (`GOOGLE_CLIENT_ID`).
2. **Cloudflare Turnstile:**
   - Create Turnstile site for `play.l3m0nctf.xyz`.
   - Put Turnstile secret in `.secrets/TURNSTILE_SECRET` and sitekey in `compose.env` (`TURNSTILE_SITEKEY`).
3. **Campus Allowlist:**
   - Add university CIDRs to `L3MON_GUARD_CAMPUS_NETS` in `compose.env`.
4. **Data Retention:**
   - Set up daily cron job to prune evidence older than 30 days:
     `curl -X DELETE "https://play.l3m0nctf.xyz/api/v1/l3mon/admin/guard/evidence?older_than_days=30" -H "Authorization: Token <TOKEN>" -H "Content-Type: application/json"`

---

## 11. Suspicious or Buggy Findings Elsewhere

1. **CTFd Test Helper `create_ctfd` default user_mode:**
   `create_ctfd` in `tests/helpers.py` defaults to `user_mode="users"`. Tests targeting `/teams/new` return 404 unless `user_mode="teams"` is explicitly passed.
2. **SQLAlchemy 2.0 Warnings in CTFd Upstream:**
   Flushing polymorphic objects in `tests/helpers.py:403` triggers `SAWarning: Flushing object <Flags> with incompatible polymorphic identity`. These originate in CTFd core test helpers.

---

## 12. Follow-Up After the Audit (F1 to F5)

Following the 18 October 2026 audit of `pre-deployment`, this branch addressed findings F1 through F5:

### 12.1 Overview of Audit Follow-Up Implementations

| Finding | Summary | Solution |
|---|---|---|
| **F1** | Full-table `Users.query.all()` scans during registration and Google sign-up | Created `l3mon_email_norm` table (migration `3e4f5a6b7c8d`) with unique constraint on `(user_id)` and `(norm)`. Added SQLAlchemy event listeners (`after_insert`, `after_update`, `after_delete` on `Users`) and lazy batch backfill (`claim("l3mon_email_norm_backfill", 10.0)` in batches of 500). Replaced linear scans in `l3mon_guard` and `l3mon_auth` with $O(1)$ indexed lookup. |
| **F2** | Campus-wide login CAPTCHA lockouts after 3 typos | Updated `is_login_captcha_required` and `record_login_failure` in `turnstile.py` to check `is_campus_ip(ip)` against `L3MON_GUARD_CAMPUS_NETS`. IP failure buckets are ignored for campus networks, while per-account failure counters remain strictly enforced. |
| **F3** | Disposable email blocklist starter list | Downloaded full open list (9,895 domains) from disposable-email-domains (CC0-1.0) into `disposable_domains.txt` with human permission. Verified $O(1)$ set lookup, subdomain blocking, and added `test_guard_disposable_top_providers.py` asserting top 20 providers are never blocked. |
| **F4** | Key refresh cooldown bypassed on unknown `kid` | Updated `jwt.verify_id_token` to set `force=False` when looking up unknown keys if `_JWKS_CACHE` is already populated. Enforces `MIN_KEY_REFRESH_INTERVAL` (60s) cooldown between outbound fetches. |
| **F5(a)** | Arbitrary `TURNSTILE_VERIFY_URL` injection | Validated `TURNSTILE_VERIFY_URL` against `challenges.cloudflare.com` domain, or `localhost`/`127.0.0.1` only when `L3MON_AUTH_TEST_MODE=1`. |
| **F5(b)** | Wording mismatch: "wrong attempts" vs all flag attempts | Standardized wording to "flag attempts" across code, docs, and UI. Counting all flag attempts is deliberate: even correct flag submissions require database transactions and locking. |
| **F5(c)** | IP address duplicated in free-text `detail` | Updated `record("captcha_unverified")` to store client IP solely in the dedicated `address` column. |
| **F5(d)** | Theme author guide & Nginx CSP | Added Section 6.3 in `docs/deploy/identity.md` with template context specifications and route-restricted CSP rules for `/login`, `/register`, and `/reset_password`. |

### 12.2 Files Created or Modified in Follow-Up

1. `plugins/l3mon_guard/models.py`: Added `EmailNorm` (`l3mon_email_norm`) model.
2. `plugins/l3mon_guard/migrations/3e4f5a6b7c8d_create_the_guard_email_norm_table.py`: Alembic migration for `l3mon_email_norm`.
3. `plugins/l3mon_guard/policies/email_norm.py`: Lifecycle listeners, lazy backfill, and $O(1)$ disguise check.
4. `plugins/l3mon_guard/__init__.py`: Registered lifecycle listeners in `load(app)`.
5. `plugins/l3mon_auth/oauth.py`: Switched `_check_email_collision` to call `is_email_disguise_used`.
6. `plugins/l3mon_guard/turnstile.py`: Campus IP exemption in login failure tracking, Turnstile URL host validation, IP in address column only.
7. `plugins/l3mon_guard/limits.py`: Standardized wording to "flag attempts".
8. `plugins/l3mon_auth/jwt.py`: Enforced `MIN_KEY_REFRESH_INTERVAL` cooldown on unknown `kid`.
9. `plugins/l3mon_guard/tests/test_guard_email_norm.py`: Unit tests for `l3mon_email_norm` migration, listeners, backfill, and query budget.
10. `plugins/l3mon_guard/tests/test_guard_campus_captcha.py`: Unit tests for campus IP CAPTCHA exemption and per-account enforcement.
11. `plugins/l3mon_guard/tests/test_guard_disposable_top_providers.py`: Unit tests for top 20 provider protection and subdomain blocking.
12. `plugins/l3mon_guard/tests/test_guard_turnstile.py`: Turnstile URL whitelist tests and IP address column assertions.
13. `plugins/l3mon_auth/tests/test_auth_google.py`: Key refresh rate limit tests for unknown `kid`.
14. `plugins/l3mon_guard/data/disposable_domains.txt`: Updated with 9,895 unique disposable domains from disposable-email-domains (CC0-1.0).
15. `docs/deploy/identity.md`: Updated setting table wording and added Section 6.3 for theme author and nginx CSP.
16. `docs/superpowers/plans/2026-10-18-identity-followup.md`: Comprehensive follow-up plan.

### 12.3 Break-on-Purpose Table for Follow-Up

| # | Rule / Guarantee | The Deliberate Break | Test That Noticed the Break | Result |
|---|---|---|---|---|
| F1-1 | $O(1)$ query budget on email disguise check | Revert `is_email_disguise_used` to scan `Users.query.all()` | `test_guard_email_norm.py::test_email_disguise_query_budget_bounded` | Fails: Statement count changes and query count scales with user count |
| F1-2 | Unique constraint closes registration race | Remove unique constraint on `l3mon_email_norm.norm` | `test_guard_email_norm.py::test_email_norm_unique_constraint` | Fails: Duplicate normalized email inserted without IntegrityError |
| F1-3 | User lifecycle listener sync | Disable `register_email_norm_listeners()` | `test_guard_email_norm.py::test_email_norm_model_and_listener_on_insert` | Fails: No `EmailNorm` row created on user insertion |
| F2-1 | Campus IP exemption from login CAPTCHA | Remove `is_campus_ip` check from IP bucket logic | `test_guard_campus_captcha.py::test_campus_address_exempt_from_ip_failure_bucket` | Fails: 11th distinct user on campus address required to solve CAPTCHA |
| F2-2 | Per-account failure threshold on campus | Bypass per-account threshold if IP is campus | `test_guard_campus_captcha.py::test_per_account_threshold_enforced_on_both_campus_and_non_campus` | Fails: Targeted account on campus IP does not require CAPTCHA after 3 failures |
| F4-1 | Google JWKS refresh cooldown on unknown `kid` | Force unconditional JWKS fetch on unknown `kid` (`fetch_jwks(force=True)`) | `test_auth_google.py::test_jwks_refresh_rate_limit_honored_for_unknown_kid` | Fails: Outbound HTTP fetch executed within 60s cooldown window |
| F5-1 | Turnstile verify URL whitelist | Return `os.environ.get("TURNSTILE_VERIFY_URL")` without host check | `test_guard_turnstile.py::test_turnstile_verify_url_whitelist` | Fails: Disallowed external URL accepted instead of falling back to Cloudflare |
| F5-2 | Evidence IP recorded in address column only | Include IP in `detail=f"action={action}; ip={ip}"` | `test_guard_turnstile.py::test_turnstile_unreachable_fail_open_login_and_reset` | Fails: Assertion `"192.0.2.1" not in rows[0].detail` fails |
| F3-1 | Top 20 email providers never blocked | Add `gmail.com` to disposable domains blocklist | `test_guard_disposable_top_providers.py::test_top_20_providers_never_blocked` | Fails: Major provider detected in blocklist |
