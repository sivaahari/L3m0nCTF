# Handoff: Stream C (Guide, Scoreboard, Sign-in Finish, Password Reset, Account Gates)

**Stream:** C  
**Branch:** `ui/c-guide-scoreboard` in both repositories (`L3m0nCTF` and `L3m0nCTF-ui`)  
**Engineer:** sanjai063928  
**Current Milestone:** M1 (Plan, baseline checks, initial Guide implementation)  
**Date:** 2026-10-22  

---

## 1. What was built (every file one line)

- `docs/superpowers/plans/2026-10-22-ui-c-guide-scoreboard.md`: Implementation plan and gates table for Stream C.
- `docs/handoff/ui-c-guide-scoreboard.md`: This handoff document tracking implementation, checks, and decisions.

---

## 2. How to run it from a clean clone

```bash
# 1. UI repository baseline checks
cd L3m0nCTF-ui/platform-ui
node --test --test-concurrency=1 "tests/*.test.mjs"
node tools/build.mjs
node tools/hygiene.mjs --no-challenges

# 2. Platform repository baseline checks
cd ../../L3m0nCTF
(cd tools && python -m l3mon hygiene --root .. --history)
python -m pytest tools/tests -q
env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
```

---

## 3. Evidence (A6 checks: command, result, counts, date)

| Check | Command | Result & Counts | Date |
|---|---|---|---|
| UI prototype test suite | `cd platform-ui && node --test --test-concurrency=1 "tests/*.test.mjs"` | `tests 334, pass 333, fail 0, skipped 1` (56.2s) | 2026-10-22 |
| UI build & budgets | `cd platform-ui && node tools/build.mjs` | All 21 page budgets pass | 2026-10-22 |
| UI hygiene scan | `cd platform-ui && node tools/hygiene.mjs --no-challenges` | `215 files checked, no problems` | 2026-10-22 |
| Platform theme tests (mounted) | `env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme` | `65 passed, 128 warnings in 41.63s` | 2026-10-22 |
| Platform tool tests | `python -m pytest tools/tests -q` | `302 passed, 12 skipped in 28.04s` | 2026-10-22 |
| Platform hygiene scan | `(cd tools && python -m l3mon hygiene --root .. --history)` | `0 finding(s)` | 2026-10-22 |

---

## 4. Differences from this prompt, and why

None so far.

---

## 5. Decisions and rejected alternatives

1. **View Model Computation Strategy**: Kept view-model builders pure and strictly separated from database queries; `guide.py` and `scoreboard.py` in `plugins/l3mon_theme/pages/` consume pre-aggregated payloads from `l3mon_board.guide.build()` and `l3mon_board.scoreboard.read()` and format them into template context matching `template-contract.md`.
2. **Dynamic Value Registrations**: Registered values with `@registry.value` rather than modifying `values.py`, preserving ownership rules.

---

## 6. Break-on-purpose table

| Rule | The Break | The Test That Noticed | Status |
|---|---|---|---|
| *Will be populated after implementing page slices in M1 & M2* | | | Pending |

---

## 7. Security notes

- **Strict CSP Enforcement**: No inline `<script>`, `<style>`, `style=` attributes, or remote hosts.
- **Enumeration Prevention**: Reset and authentication endpoints disclose no account existence.
- **Query Budget**: Ceiling tests ensure zero database statement growth with number of challenges or teams.
- **Fail-open / Fail-closed Stance**: Turnstile CAPTCHA adheres to `l3mon_guard` policies.

---

## 8. Design changes requested from owner

None.

---

## 9. Screenshots list (kept outside the repository)

*Pending browser runs in M1 and M2.*

---

## 10. Open questions and known gaps

None at this time.

---

## 11. Anything suspicious noticed elsewhere

None.
