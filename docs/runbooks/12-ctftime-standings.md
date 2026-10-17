# Runbook 12: Publishing Final CTFtime Standings

## Purpose
Generate and publish the official final results feed for CTFtime (`/ctftime/final-standings.json`) and upload them to CTFtime to grant global rating points.

## Pre-conditions & Rules (from `docs/ctftime-endpoints.md`)
1. **The event has concluded** (after 22:00 IST / 16:30 UTC on 28 November 2026).
2. **All cheating investigations, flag disputes, and team bans are resolved**.
3. **The scoreboard freeze has been lifted** (see Runbook 06).
   - *Crucial*: The final standings endpoint explicitly refuses to generate while a freeze is active, warning: *"wait until the scoreboard freeze is lifted"*.
4. **Final standings are formally published** by an administrator via `l3mon_final_standings_published`.

## Numbered Procedure

1. **Verify scoreboard freeze is lifted**:
   ```bash
   curl -s "http://127.0.0.1:8080/api/v1/configs" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" | jq '.data[] | select(.key=="freeze")'
   ```
   Must be null or empty. If not, follow Runbook 06 to lift it.

2. **Publish the final standings flag**:
   ```bash
   curl -s -X PATCH "http://127.0.0.1:8080/api/v1/configs" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" \
     -H "Content-Type: application/json" \
     -d '{"l3mon_final_standings_published": true}'
   ```

3. **Verify and download the final results JSON**:
   ```bash
   curl -fsS "https://play.l3m0nctf.xyz/ctftime/final-standings.json" -o final-standings.json
   ```
   Inspect the file structure:
   ```bash
   head -n 20 final-standings.json
   ```
   Confirm format:
   ```json
   {
     "standings": [
       { "pos": 1, "team": "Team Name", "score": 4200 },
       { "pos": 2, "team": "Second Team", "score": 3900 }
     ]
   }
   ```
   Validate that:
   - Only teams with score > 0 are listed.
   - Hidden, banned, or staff accounts are excluded.
   - Tied scores share identical positions or match tie-break timestamps.

4. **Upload to CTFtime**:
   - Log into `https://ctftime.org/` using the verified organiser account.
   - Navigate to the event management page for **L3m0nCTF 2026**.
   - Under **Results**, upload `final-standings.json` or paste the URL `https://play.l3m0nctf.xyz/ctftime/final-standings.json`.
   - Submit for CTFtime administrator verification.

## How to know it worked
- `https://play.l3m0nctf.xyz/ctftime/final-standings.json` answers HTTP 200 with the full leaderboard.
- CTFtime event page updates to display the calculated rating point awards for participating teams.

## Who to tell
- **Event Lead & TIFAC-CORE Faculty Sponsor**: Report successful publication of final rankings.
- **Competitors**: Post announcement in Discord and on social channels celebrating the winners.

---
- [ ] followed once by someone who did not write it
