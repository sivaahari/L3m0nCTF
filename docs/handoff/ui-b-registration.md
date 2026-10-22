# Stream B: Registration (Account, Email Check, Studio) Handoff

Branch: `ui/b-registration` in `L3m0nCTF` and `L3m0nCTF-ui`.  
Lead contact: KabilanMohanraj17.

## 1. What Was Built

* `docs/superpowers/plans/2026-10-22-ui-b-registration.md`: Implementation plan and architectural decisions for Stream B.
* `docs/handoff/ui-b-registration.md`: Progress and verification handoff document.

## 2. How to Run It from a Clean Clone

```bash
# Clone repositories side-by-side
git clone https://github.com/sivaahari/L3m0nCTF.git
git clone https://github.com/sivaahari/L3m0nCTF-ui.git
cd L3m0nCTF && git switch ui/b-registration
cd ../L3m0nCTF-ui && git switch ui/b-registration

# Run prototype checks
cd ../L3m0nCTF-ui/platform-ui
node tools/build.mjs
node --test --test-concurrency=1 "tests/*.test.mjs"

# Export theme to L3m0nCTF
node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon

# Run CTFd theme tests
cd ../../L3m0nCTF
env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
```

## 3. Evidence

*(Checks in progress for Milestone 1)*

| Check | Command | Result | Counts | Date |
|---|---|---|---|---|
| UI: prototype suite | `node --test --test-concurrency=1 "tests/*.test.mjs"` | PASS | 321 passed, 1 skipped | 2026-10-22 |
| UI: build and budgets | `node tools/build.mjs` | PASS | All budgets met | 2026-10-22 |
| UI: registration suite | `node --test "tests/register-*.test.mjs"` | PASS | 39 passed | 2026-10-22 |

## 4. Differences from the Prompt

1. **Stepper count:** Scaled from 4 steps to 3 steps (`Account`, `Verify`, `Studio`), dropping the review step and done page since CTFd ends the wizard upon studio creation/enrollment. The welcome card moves to Stream D's `teams/private.html`.

## 5. Decisions and Rejected Alternatives

* **Native CTFd flow vs custom registration backend:** Ported directly onto native CTFd auth and team blueprints (`/register`, `/confirm`, `/teams/*`) rather than introducing a separate registration backend or modifying CTFd core.
* **Invite code resolution:** Accept both full URL (`/teams/invite?code=...`) and standalone token code in the join input.
* **Studio password handling:** Hidden random password generation if required by CTFd to maintain pure invite-link exclusivity.
* **Error display:** Full error list displayed in top error summary; individual field error binding is strictly deterministic to prevent misleading field attribution.

## 6. Break-on-Purpose Table

| Rule | The Break | Test That Noticed |
|---|---|---|
| *(Populated during test verification)* | | |

## 7. Security Notes

* **Account Enumeration:** Standard CTFd error responses and timing characteristics maintained; no extraneous disclosures added.
* **Studio Name Probing:** Availability endpoint is rate-limited (20 requests/min/IP) and restricted to authenticated players only.
* **Content Security Policy:** Strict CSP adherence; zero inline scripts/styles; external assets confined to Cloudflare Turnstile if enabled.
* **Input Sanitization:** Team names and user inputs sanitized via `l3mon_guard` look-alike and printable ASCII/Unicode normalization.

## 8. Design Changes Requested

None.

## 9. Screenshots List

*(To be captured on local running stack)*

## 10. Open Questions and Known Gaps

None at present.

## 11. Anything Suspicious Noticed Elsewhere

None at present.
