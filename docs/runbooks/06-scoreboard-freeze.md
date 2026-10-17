# Runbook 06: Scoreboard Freeze and Unfreeze

## Purpose
Freeze public standings during the final hours of the contest to preserve suspense, and unfreeze post-contest after cheating reviews are settled.

## Architectural Rules (from `docs/deploy/local.md`)
- The freeze is controlled by CTFd's **Freeze Time** (`freeze` configuration key as Unix epoch seconds).
- **While freeze is active**:
  - The public scoreboard, CTFtime live feed, and competitor views stand completely still.
  - Programme TRP values stop falling (values are held so dynamic decay cannot leak solves).
  - Each studio's own solves and scores stay live for that studio; their rank display shows *"Ranks frozen"*.
  - Bought hints deduct TRP immediately but remain hidden from the public list until unfreeze.
  - Players cannot rate challenges (which would leak late solve counts).
  - Revoke, Restore, bans, and bonuses continue working; actions affecting pre-freeze solves update publicly immediately.
  - Recalculate values is disabled (returns HTTP 409).
- **Setting the freeze retrospectively**: Setting `freeze` to a past timestamp works retroactively; all values recalculate as of that exact moment.

## Numbered Procedure

### To Activate the Scoreboard Freeze (e.g. at 20:00 IST / T-2h)
1. **Calculate the freeze timestamp**:
   Get Unix epoch seconds for the desired freeze moment (e.g. `1764340200` for 20:00 IST):
   ```bash
   date -d "2026-11-28 20:00:00 +0530" +%s
   ```

2. **Apply the freeze via Administrator API**:
   Using the organiser token:
   ```bash
   curl -s -X PATCH "http://127.0.0.1:8080/api/v1/configs" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" \
     -H "Content-Type: application/json" \
     -d "{\"freeze\": 1764340200}"
   ```
   *(Alternatively, in the CTFd Admin UI: Config -> Visibility and Time -> Freeze Time).*

3. **Verify freeze status**:
   - Check public scoreboard: Teams solving new challenges do not change position.
   - Check CTFtime feed:
     ```bash
     curl -s http://127.0.0.1:8080/ctftime/standings.json
     ```
     Standings must remain static.

---

### To Lift the Scoreboard Freeze (Post-Event Review Completed)
1. **Confirm all cheating disputes, bans, and voided challenges are finalized**.

2. **Clear the freeze configuration**:
   ```bash
   curl -s -X PATCH "http://127.0.0.1:8080/api/v1/configs" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" \
     -H "Content-Type: application/json" \
     -d '{"freeze": null}'
   ```

3. **Verify full standings recalculation**:
   The unfreeze request triggers an automatic synchronous recalculation of all dynamic values and ranks:
   ```bash
   curl -s http://127.0.0.1:8080/api/v1/l3mon/scoreboard | jq '.data.rows[0:5]'
   ```
   Ranks are restored and dynamic scores match final solve counts.

## How to know it worked
- On freeze: Competitors see *"Ranks frozen"* on their Guide and public ranks freeze.
- On unfreeze: Public scoreboard displays true final standings and CTFtime feed reflects final unheld values.

## Who to tell
- **Event Lead & Announcer**: Announce freeze or unfreeze to players in Discord and platform notification bell.

---
- [ ] followed once by someone who did not write it
