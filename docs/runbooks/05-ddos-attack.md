# Runbook 05: DDoS Mitigation & Flood Protection

## Purpose
Respond to high-volume distributed denial-of-service (DDoS) attacks, automated crawler floods, or application-layer request spikes.

## Defense Layers & What Each Costs

| Defense Layer | Where Applied | Mechanism | Cost / Player Impact |
|---|---|---|---|
| **Cloudflare "Under Attack" Mode** | Cloudflare Edge | Managed JavaScript challenge on every incoming browser request | Adds 3–5s delay on initial page load; headless bots without full JS engine are dropped immediately. |
| **Cloudflare Rate Limiting Rules** | Cloudflare Edge | Block or challenge IP addresses generating > 100 req/10s | May challenge players behind large university CGNATs if thresholds are too tight. |
| **Nginx Level 1 Busy Mode** | Origin Nginx | Concurrency cap (`app_conn`) sheds excess requests with HTTP 503 and static busy slate | Uncached page views wait for retry bar; **crucially, flag submissions use an isolated pool and are NOT starved**. |
| **Nginx Per-IP Rate Limiting** | Origin Nginx | `limit_req` zones: `100r/s` general, `120r/m` login, `1200r/m` flags | Dropped bots receive HTTP 429. Campus IP addresses are protected by high burst allowances. |

## Numbered Procedure

1. **Assess the nature of the flood**:
   Check Nginx access logs and top client IPs / User-Agents:
   ```bash
   tools/compose.sh logs --tail 500 nginx | awk '{print $1}' | sort | uniq -c | sort -nr | head -n 20
   ```
   Check if the upstream application is saturating:
   ```bash
   curl -I http://127.0.0.1:8080/readyz
   ```

2. **Enable Cloudflare "Under Attack" Mode**:
   - Log into the Cloudflare Dashboard for `l3m0nctf.xyz`.
   - Under **Security** -> **Settings**, set **Security Level** to **"I'm Under Attack!"**.
   - Alternatively, via Cloudflare API:
     ```bash
     curl -s -X PATCH "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/settings/security_level" \
       -H "Authorization: Bearer $CF_API_TOKEN" \
       -H "Content-Type: application/json" \
       --data '{"value": "under_attack"}'
     ```

3. **Verify origin Level 1 Busy Mode load-shedding**:
   If requests still exceed upstream capacity, Nginx automatically sheds load by serving `/etc/nginx/snippets/slates/busy.html` with HTTP 503 and `Retry-After: 15`.
   Verify that flag submissions remain operational:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/api/v1/challenges/attempt
   ```
   Must return 400 or 403 (expected API rejection), NOT 503.

4. **Block attacking CIDR or ASN in Cloudflare**:
   If attack originates from specific cloud hosting ASNs (e.g., OVH, Hetzner, DigitalOcean) with no legitimate players:
   Create a WAF Custom Rule in Cloudflare: `(ip.geoip.asnum in {16276 24940}) -> Block`.

5. **De-escalate after attack subsides**:
   Once traffic stabilizes, return Cloudflare Security Level to **Medium**:
   ```bash
   curl -s -X PATCH "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/settings/security_level" \
     -H "Authorization: Bearer $CF_API_TOKEN" \
     -H "Content-Type: application/json" \
     --data '{"value": "medium"}'
   ```

## How to know it worked
- Origin CPU utilization drops back below 70%.
- Latency on `/api/v1/l3mon/ticks` returns under 100ms.
- Legitimate players successfully solve challenges and load the board.

## Who to tell
- **Event Lead & Infrastructure Owner**: Report attack size (RPS, bandwidth), origin ASNs, and actions taken.

---
- [ ] followed once by someone who did not write it
