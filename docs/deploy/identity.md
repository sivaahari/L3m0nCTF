# Identity and Anti-Abuse Deployment Guide (`l3mon_guard` and `l3mon_auth`)

This document describes how to configure and deploy the anti-abuse suite (`plugins/l3mon_guard`, SP5) and the "Continue with Google" sign-in integration (`plugins/l3mon_auth`, SP8) for L3m0nCTF 2026.

---

## 1. Architecture & Design Principles

- **Zero Touch to CTFd Core:** All policies, verification steps, and limits are enforced through view wrappers, event listeners, and standard CTFd hooks.
- **Fail Soft:** Outages or degraded performance from Google OIDC or Cloudflare Turnstile never degrade or block traditional email/password login or Login with CTFtime.
- **Off Until Configured:** When secrets or client IDs are omitted or empty, all related routes either answer 404 (`/auth/google`) or bypass CAPTCHA checks (`l3mon_guard`).
- **No Automated Bans:** Rate limits strictly issue temporary HTTP 429 (`Retry-After`) responses. Abuse heuristics record tamper-evident records into `l3mon_evidence` for human review by the crew.

---

## 2. Google Cloud Setup ("Continue with Google")

Organisers must complete the following in the [Google Cloud Console](https://console.cloud.google.com/):

### 2.1 Project & Consent Screen
1. Create a project under an official organiser account (e.g., `l3monctf-production`).
2. Navigate to **APIs & Services > OAuth consent screen**:
   - User Type: **External**.
   - App Name: `L3m0nCTF 2026`.
   - User support email: `security@l3monctf.xyz` or organiser contact email.
   - Authorized domains: `l3monctf.xyz`.
   - Developer contact email: organiser team contact.
3. Scopes:
   - Add `openid`, `.../auth/userinfo.email`, and `.../auth/userinfo.profile`.
4. **Publishing Status (Critical):**
   - Click **Publish App** to switch status to **In production**.
   - *Note:* If left in "Testing", only explicitly whitelisted Google accounts (max 100) can sign in.

### 2.2 Web Application Credentials
1. Navigate to **APIs & Services > Credentials > Create Credentials > OAuth client ID**.
2. Application type: **Web application**.
3. Name: `L3m0nCTF Web Client`.
4. Authorized redirect URIs:
   - Staging: `https://staging.l3m0nctf.xyz/auth/google/callback`
   - Production: `https://play.l3m0nctf.xyz/auth/google/callback`
   - Local dev: `https://localhost:8443/auth/google/callback` or `http://localhost:8080/auth/google/callback`
5. Note the generated **Client ID** and **Client Secret**.

---

## 3. Cloudflare Turnstile Setup

1. In the Cloudflare Dashboard, go to **Turnstile > Add Site**.
2. Site name: `L3m0nCTF 2026`.
3. Domain: `play.l3m0nctf.xyz`, `staging.l3m0nctf.xyz`, `localhost`.
4. Widget Mode: **Managed** (recommended) or **Non-interactive**.
5. Copy the generated **Site Key** and **Secret Key**.

---

## 4. Configuration & Secrets

### 4.1 Secrets
Secrets are mounted inside the container via Docker secrets (read from `/run/secrets/`):

| Secret Name | Secret File Path | Purpose |
|---|---|---|
| `TURNSTILE_SECRET` | `.secrets/TURNSTILE_SECRET` | Cloudflare Turnstile secret key |
| `GOOGLE_CLIENT_SECRET` | `.secrets/GOOGLE_CLIENT_SECRET` | Google OAuth2 Web Client Secret |

*An empty secret file leaves that feature inactive.*

### 4.2 Environment Variables

Set in `compose.env` or the deployment environment:

| Variable | Default | Purpose |
|---|---|---|
| `TURNSTILE_SITEKEY` | `""` | Public Turnstile site key (injected into templates) |
| `GOOGLE_CLIENT_ID` | `""` | Public Google OAuth2 Client ID |
| `L3MON_PLATFORM_HOST` | `play.l3m0nctf.xyz` | Hostname used to build redirect URIs |
| `L3MON_GUARD_CAMPUS_NETS` | `""` | Comma-separated CIDRs (e.g., `192.0.2.0/24`) exempt from address heuristics |
| `L3MON_GUARD_TEAM_LIMIT` | `40` | Max wrong flag attempts per team per minute before 429 Retry-After |
| `L3MON_GUARD_REG_BURST_THRESHOLD` | `5` | Max account registrations from one non-campus IP per 10 min |

---

## 5. Compose Overlay (`compose.identity.yml`)

To start the stack with identity and anti-abuse enabled:

```bash
L3MON_OVERLAYS=identity tools/compose.sh up -d --wait
```

To run in production with all security controls:

```bash
L3MON_ENV=production L3MON_OVERLAYS=identity tools/compose.sh up -d --wait
```

---

## 6. Theme Contract (Template Context Processor)

Both plugins inject structured dictionaries into template contexts for clean frontend rendering:

### 6.1 `l3mon_guard`
Available as `l3mon_guard` in Jinja2 templates:
```json
{
  "captcha": {
    "sitekey": "1x0000000000000000000000AA",
    "register": true,
    "login": false,
    "reset": true
  }
}
```
- `login` is dynamically `true` only when the visitor's IP or requested account has exceeded 3 recent failed sign-in attempts.

### 6.2 `l3mon_auth`
Available as `l3mon_auth` in Jinja2 templates:
```json
{
  "google": {
    "active": true,
    "auth_url": "/auth/google"
  }
}
```
When `l3mon_auth.google.active` is true, the theme renders the official Google sign-in button pointing to `/auth/google`. If JavaScript is disabled, the button functions as a regular hyperlink.

---

## 7. Crew Review & Evidence Retention

Crew review interface: `GET /admin/l3mon/guard` (accessible to administrators only).

### Evidence Cleanup & Data Retention
Adhering to privacy requirements, client IP addresses are recorded only in `l3mon_evidence`. The crew or an automated cron job prunes old evidence:

```bash
curl -X DELETE "https://play.l3m0nctf.xyz/api/v1/l3mon/admin/guard/evidence?older_than_days=30" \
     -H "Authorization: Token <ADMIN_API_TOKEN>" \
     -H "Content-Type: application/json"
```
