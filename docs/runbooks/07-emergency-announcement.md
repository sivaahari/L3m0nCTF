# Runbook 07: Emergency Announcement Broadcast

## Purpose
Send an urgent notification or challenge clarification directly to all connected players via the in-platform notification bell and API.

## Architectural Context
- Notifications reach players through `GET /api/v1/notifications?since_id=N` (polled alongside ticks every 15s).
- Player notifications pass through `CTFd.plugins.l3mon_core.text.plain_text` (all HTML/script tags are strictly stripped or escaped; plain sentences only).
- When a notification is posted, the platform's tick counter (`notif_id`, `notif_ver`) advances, causing player browser tabs to immediately ring the notification bell and pull the message.

## Numbered Procedure

1. **Draft the message**:
   - Keep it short, calm, and plain text. No markdown links, no HTML tags.
   - Example Title: `Challenge Clarification: Gadget-03`
   - Example Content: `The network service port for Gadget-03 has been restored. Download the revised handout from the challenge panel.`

2. **Send via Administrator API**:
   ```bash
   curl -s -X POST "http://127.0.0.1:8080/api/v1/notifications" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "EMERGENCY: Infrastructure Update",
       "content": "A 5-minute maintenance pause is scheduled for 18:00 IST. Submissions will be held briefly.",
       "type": "toast"
     }'
   ```
   *(Alternatively, in the CTFd Admin UI: Notifications -> New Notification -> Submit).*

3. **Verify the notification was recorded**:
   Query the public notification feed:
   ```bash
   curl -s "http://127.0.0.1:8080/api/v1/notifications" | jq '.data[-1]'
   ```
   Confirm the latest notification matches the published title and content.

4. **Verify the tick incremented**:
   ```bash
   curl -s "http://127.0.0.1:8080/api/v1/l3mon/ticks" | jq .
   ```
   Confirm `notif_id` matches the new notification ID.

## How to know it worked
- `curl /api/v1/notifications` lists the new notification.
- Player browser tabs illuminate the notification bell within 15 seconds.

## Who to tell
- **Discord / Support Crew**: Cross-post the exact same message to the `#announcements` channel in Discord so players tabbed away are notified.

---
- [ ] followed once by someone who did not write it
