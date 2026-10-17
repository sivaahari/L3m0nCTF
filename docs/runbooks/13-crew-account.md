# Runbook 13: Adding Crew and Administrator Accounts

## Purpose
Provision a new organiser or challenge author account with administrative privileges, ensuring IP allow-listing and secure credential delivery.

## Architectural Requirements
1. **Admin Surface Protection**: Nginx gates `/admin` and `/api/v1/l3mon/admin/...` strictly behind the IP allow-list defined in `ADMIN_NETS_FILE` (`deploy/nginx/snippets/admin-nets.conf`).
   - *An admin user attempting to sign in from an unlisted IP will receive HTTP 403 Forbidden on all administrative routes*.
2. **Account Properties**: Crew accounts must be marked `type: "admin"`, `verified: true`, and `hidden: true` so they do not appear on player scoreboards.

## Numbered Procedure

1. **Verify or update the crew member's IP address in Nginx**:
   If the organiser is connecting from a new static IP or VPN CIDR:
   - Add their subnet to the host's `ADMIN_NETS_FILE`:
     ```text
     203.0.113.45/32 1; # Organiser: Alice
     ```
   - Reload Nginx configuration without dropping connections:
     ```bash
     docker exec $(tools/compose.sh ps -q nginx) nginx -s reload
     ```

2. **Create the Administrator Account via API**:
   Generate a strong random temporary password and provision the account:
   ```bash
   CREW_PASS="$(python -c "import secrets; print(secrets.token_urlsafe(16))")"
   curl -s -X POST "http://127.0.0.1:8080/api/v1/users" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" \
     -H "Content-Type: application/json" \
     -d "{
       \"name\": \"crew_bob\",
       \"email\": \"bob@tifac.amrita.edu\",
       \"password\": \"$CREW_PASS\",
       \"type\": \"admin\",
       \"verified\": true,
       \"hidden\": true
     }"
   ```

3. **Verify the user record**:
   ```bash
   curl -s "http://127.0.0.1:8080/api/v1/users?field=name&q=crew_bob" \
     -H "Authorization: Token $(cat .secrets/PRESET_ADMIN_TOKEN 2>/dev/null || echo $CREW_TOKEN)" | jq .
   ```
   Confirm `type: "admin"` and `hidden: true`.

4. **Securely transmit credentials**:
   - Send the temporary password to the crew member via an encrypted channel (Signal, PGP, or in person).
   - Require the user to immediately change their password upon first login under `/settings`.

## How to know it worked
- The crew member can sign in at `https://play.l3m0nctf.xyz/login` and navigate to `/admin`.
- The user does not appear in `/api/v1/users` public queries or on the public scoreboard.

## Who to tell
- **Lead Organiser**: Confirm the new crew member has been onboarded and their IP whitelisted.

---
- [ ] followed once by someone who did not write it
