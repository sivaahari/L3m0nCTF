# Production Player Traffic Routing Design: Instance Router (SP4)

**Document:** `docs/deploy/instancer-traffic.md`  
**Author:** Instancer Teammate (`venu-21`)  
**Date:** 19 October 2026  
**Status:** Design Proposal for Owner Decision  

---

## 1. Context & Problem Statement

In L3m0nCTF 2026, dynamic per-team challenge instances are accessed by players via dedicated hostnames structured as:
```
<32-hex-instance-id>.<instance_domain>
```
(For example: `0123456789abcdef0123456789abcdef.<instance_domain>`)

The instancer router (`services/instancer/router/`) currently listens for plain HTTP connections on port `8090` (`0.0.0.0:8090`). In production, this setup encounters three fundamental networking constraints:

1. **Cloudflare Proxied Port Restrictions:**
   Cloudflare proxies traffic only on a strict, pre-defined set of ports:
   - **HTTPS:** `443`, `2053`, `2083`, `2087`, `2096`, `8443`
   - **HTTP:** `80`, `8080`, `8880`, `2052`, `2082`, `2086`, `2095`
   Port `8090` is **not** on Cloudflare's supported list and cannot be proxied by Cloudflare's CDN/DDoS edge.
2. **Universal SSL Certificate Depth Limit:**
   Cloudflare's free plan provides Universal SSL covering only the zone apex and immediate first-level subdomains:
   - `l3m0nctf.xyz`
   - `*.l3m0nctf.xyz` (single-level wildcard only)
   A two-level hostname such as `<instance-id>.instances.l3m0nctf.xyz` will **fail SSL validation in all modern browsers** (`ERR_CERT_COMMON_NAME_INVALID`) unless a paid Cloudflare Advanced Certificate Manager subscription ($10/month) is purchased.
3. **TLS Termination Architecture:**
   While `deploy/compose/compose.instancer-production.yml` mounts `/etc/l3mon/router-tls`, the router container runs Python async HTTP with no internal TLS handling. Exposing TLS directly in pure Python would add unnecessary cryptographic overhead and latency to the event loop.

This document analyzes three viable options for routing player traffic to the router in production, evaluates their operational steps, financial cost, failure modes, and security impact, and presents a concrete recommendation.

---

## 2. Evaluation of Options

### Option (a): Wildcard One Level Under Zone (`<id>.l3m0nctf.xyz`) on Port 8443 with Origin TLS

#### Architectural Blueprint
- **Public Domain & Hostname:** `<32-hex-id>.l3m0nctf.xyz:8443`
- **DNS:** Wildcard DNS record `*.l3m0nctf.xyz` pointing to origin VM IP, proxied through Cloudflare (orange-clouded). Cloudflare's edge terminates public HTTPS on port 8443 using the free Universal SSL certificate (`*.l3m0nctf.xyz`).
- **Origin Port & Protocol:** Origin listens on port 8443 speaking HTTPS to Cloudflare edge.
- **Where TLS is Terminated on Origin:**
  - **In Nginx in front of the router:** Nginx terminates TLS on `8443`, validates Cloudflare Authenticated Origin Pulls (AOP) client certificates, and forwards plain HTTP over Docker's internal network to `router:8090`.
  - Terminating TLS in Nginx reuses the existing, hardened OpenSSL infrastructure and client-cert verification already built in `deploy/nginx/templates-production`. The Python router remains a pure, fast HTTP reverse proxy.

#### Deployment Steps
1. In Cloudflare DNS dashboard, create record:
   - Type: `A` (or `CNAME`)
   - Name: `*`
   - Target: `<Production GCE VM External IP>`
   - Proxy status: **Proxied (Orange Cloud)**
2. In `deploy/nginx/templates-production/default.conf.template`, add an upstream block and server block listening on `8443 ssl`:
   - Bind: `listen 8443 ssl;`
   - Server name pattern: `~^[0-9a-f]{32}\.l3m0nctf\.xyz$;`
   - SSL certificates: Reuses `${TLS_DIR}/origin.crt` and `${TLS_DIR}/origin.key` (issued by Cloudflare Origin CA).
   - Client Verification: Includes `snippets/authenticated-origin-pulls.conf` (`ssl_client_certificate /etc/nginx/tls/cloudflare-aop.crt; ssl_verify_client on;`).
   - Reverse proxy: `proxy_pass http://router:8090;`
3. In `deploy/compose/compose.instancer-production.yml`:
   - Set `INSTANCE_DOMAIN=l3m0nctf.xyz`
   - Keep router internal on `edge` network (or publish only to internal/loopback).
   - In `nginx` service, publish port `"8443:8443"`.

#### Financial Cost
- **$0.00** (Free). Completely covered by existing domain, free Cloudflare Universal SSL, and free Cloudflare Origin CA.

#### Failure Modes & Operational Risks
- **Subdomain Name Collision:** A player might attempt to claim a challenge instance ID matching an official subdomain. However, official subdomains are named words (`play.l3m0nctf.xyz`, `crew.l3m0nctf.xyz`, `auth.l3m0nctf.xyz`), whereas instance hostnames are strictly 32 hexadecimal lowercase characters (`^[0-9a-f]{32}$`). Furthermore, explicit Cloudflare DNS records always override wildcard records.
- **Port Visibility:** Players access challenges on `https://<id>.l3m0nctf.xyz:8443/`. While non-standard, port 8443 is widely open on standard educational, corporate, and campus Wi-Fi networks (standard HTTPS alt port).
- **Misconfigured Nginx Routing:** If the regex `~^[0-9a-f]{32}\.l3m0nctf\.xyz$` is omitted, Nginx might catch and route unexpected traffic to the instancer router.

#### Security Impact
- **Platform Session Isolation (`__Host-session`):**
  The platform CTFd session cookie is issued with the RFC 6265bis prefix `__Host-session`, with flags `Secure; HttpOnly; SameSite=Lax`. By browser specification:
  - Cookies with `__Host-` prefix **cannot** set a `Domain` attribute (they are strictly bound to the exact origin `play.l3m0nctf.xyz`).
  - Sibling subdomains (such as `<id>.l3m0nctf.xyz`) **cannot overwrite, forge, or delete** `__Host-session`.
- **Cross-Origin Storage & DOM Isolation:**
  - Origin for player instance: `https://<id1>.l3m0nctf.xyz:8443`
  - Origin for CTFd platform: `https://play.l3m0nctf.xyz`
  The Same-Origin Policy (SOP) considers scheme, host, and port. Since both host and port differ, challenge web instances have zero access to `localStorage`, `sessionStorage`, `IndexedDB`, or cookies of CTFd.
  - Furthermore, instance `<id1>` and instance `<id2>` have distinct hostnames, strictly isolating different challenges from each other.
- **Exposure to Non-Cloudflare Visitors:**
  With Cloudflare Authenticated Origin Pulls (AOP) enabled in Nginx on port 8443, any direct connection from an attacker bypassing Cloudflare directly to the origin IP is rejected at the TLS handshake with `400 Bad Request: No required SSL certificate was sent`.

---

### Option (b): Dedicated Secondary Domain (e.g. `l3m0ninstances.xyz`)

#### Architectural Blueprint
- **Public Domain & Hostname:** `https://<32-hex-id>.l3m0ninstances.xyz` (standard port 443)
- **DNS:** Dedicated zone `l3m0ninstances.xyz` on Cloudflare with wildcard `*` proxied to origin IP.
- **Origin Port & Protocol:** Origin listens on port 443 (or standard HTTPS port) with Cloudflare Origin Certificate for `*.l3m0ninstances.xyz`.

#### Deployment Steps
1. Purchase a cheap domain (e.g., `.xyz` or `.top` via Namecheap/Porkbun for ~$1.50–$3.00/year).
2. Delegate nameservers to Cloudflare (Free tier).
3. In Cloudflare, add wildcard `*` pointing to origin VM IP (Proxied).
4. Issue Cloudflare Origin CA certificate for `l3m0ninstances.xyz` and `*.l3m0ninstances.xyz`.
5. Configure Nginx to terminate TLS on port 443 (or share 443 with SNI) and proxy to `http://router:8090`.
6. Set `INSTANCE_DOMAIN=l3m0ninstances.xyz`.

#### Financial Cost
- **~$1.50 – $3.00** one-time annual domain registration.

#### Failure Modes & Operational Risks
- **Domain Renewal / Expiry:** Another domain credential and renewal lifecycle to track.
- **DNS Propagation:** Setting up and testing a second zone during event setup.
- **SNI Complexity:** If origin shares port 443 between CTFd (`l3m0nctf.xyz`) and the instances (`l3m0ninstances.xyz`), Nginx must dispatch via SNI to distinct certificate bundles.

#### Security Impact
- **Total Domain Isolation:** The attack surface is completely decoupled from `l3m0nctf.xyz`.
- **Zero Risk of Sibling Leakage:** Sibling cookies, HTTP Strict Transport Security (HSTS) with `includeSubDomains`, and CORS headers on `l3m0nctf.xyz` cannot inadvertently affect or be affected by instance subdomains.
- Standard port 443 eliminates any campus firewall issues that block non-443 ports.

---

### Option (c): Cloudflare Tunnel (`cloudflared`)

#### Architectural Blueprint
- **Public Domain & Hostname:** `<id>.l3m0nctf.xyz` or `<id>.instances.l3m0nctf.xyz`
- **Networking:** Outbound-only tunnel. The `cloudflared` daemon runs in a container alongside the stack and establishes a multiplexed QUIC/HTTP2 tunnel to Cloudflare Edge. No inbound ports (8090, 8443, etc.) need to be open in the GCE firewall.
- **Routing:** Cloudflare edge terminates TLS and funnels traffic through the tunnel directly to `http://router:8090`.

#### Deployment Steps
1. Create a Cloudflare Tunnel via Cloudflare Zero Trust dashboard or CLI.
2. Obtain tunnel token and store as Docker secret `.secrets/TUNNEL_TOKEN`.
3. Add `cloudflared` service in `deploy/compose/compose.instancer-production.yml` running `cloudflare/cloudflared:latest` with ingress rule:
   ```yaml
   ingress:
     - hostname: "*.l3m0nctf.xyz"
       service: http://router:8090
   ```
4. Point DNS in Cloudflare to the Tunnel UUID CNAME.

#### Financial Cost
- **$0.00** (Free on Cloudflare Zero Trust tier).
- **Caveat:** If two-level subdomain `<id>.instances.l3m0nctf.xyz` is desired, Cloudflare Advanced Certificate Manager ($10/month) is **still required** because Cloudflare Edge terminates the TLS handshake before routing traffic into the tunnel.

#### Failure Modes & Operational Risks
- **Multi-Level SSL Fallacy:** Tunnels do *not* bypass edge certificate limits. A tunnel on `*.instances.l3m0nctf.xyz` will still yield browser SSL errors on the free plan.
- **Tunnel Throughput Bottlenecks:** Web challenges with large binary asset downloads or automated scripts can exhaust `cloudflared` container resources or hit Cloudflare tunnel egress rate limits.
- **Single Point of Failure / Daemon Drops:** If the tunnel connection drops or reconnects under load, debugging tunnel state and QUIC keep-alives during the 12-hour live contest adds operational friction compared to direct Nginx reverse-proxying.

#### Security Impact
- **Superior Origin Invisibility:** No open ports on the host VM for player instances (firewall can deny all inbound traffic except port 443 for CTFd).
- Bypassing Cloudflare is completely impossible since the origin does not listen on any public interface.

---

## 3. Comparison Matrix

| Criteria | Option (a): Wildcard under `l3m0nctf.xyz` on Port 8443 | Option (b): Dedicated Domain (`l3m0ninstances.xyz`) on 443 | Option (c): Cloudflare Tunnel (`cloudflared`) |
|---|---|---|---|
| **URL Format** | `https://<id>.l3m0nctf.xyz:8443/` | `https://<id>.l3m0ninstances.xyz/` | `https://<id>.l3m0nctf.xyz/` |
| **Edge SSL Support** | **Free (Universal SSL)** (1 level deep) | **Free (Universal SSL)** (1 level deep) | **Free** (if 1 level) / **$10/mo** (if multi-level) |
| **Financial Cost** | **$0.00** | **~$2.00 / year** | **$0.00** (or $10/mo) |
| **Host Ports Exposed** | Port 8443 (Cloudflare AOP protected) | Port 443 (Cloudflare AOP protected) | **None** (Outbound tunnel only) |
| **Origin TLS Termination** | Nginx (`snippets/authenticated-origin-pulls.conf`) | Nginx with dedicated cert | Cloudflare Edge (plain HTTP to origin) |
| **Cookie Isolation** | Full (`__Host-session` is immune) | Absolute (different top-level domain) | Full (`__Host-session` is immune) |
| **Operational Complexity** | Low (uses existing Nginx + compose overlay) | Low (requires domain purchase & DNS setup) | Medium (requires tunnel daemon management) |
| **Risk of Proxy Port Block** | Very low (8443 is standard HTTPS alt) | None (standard port 443) | None (standard port 443) |

---

## 4. Recommendation for Owner Decision

### **Primary Recommendation: Option (a) — Wildcard Subdomain on Port 8443 with Nginx Origin Pulls**

**Why Option (a) is the recommended path:**
1. **Zero Financial Cost ($0):** Fits within our free-tier budget while remaining strictly compliant with Cloudflare's 1-level wildcard certificate limit (`*.l3m0nctf.xyz`).
2. **Proven Security Stance:**
   - Nginx handles TLS termination and validates Cloudflare Authenticated Origin Pulls (AOP). Strangers scanning the public IP cannot reach port 8443 without presenting Cloudflare's client certificate.
   - The platform session cookie is `__Host-session`, meaning sibling subdomains (`<id>.l3m0nctf.xyz`) cannot inject or overwrite cookies on `play.l3m0nctf.xyz`.
   - Distinct 32-hex instance IDs ensure full browser Same-Origin Policy (SOP) isolation between competing challenge instances.
3. **Simplicity & Operational Reliability:** Reuses the battle-tested Nginx template structure already established for CTFd; keeps the Python router free of TLS handling complexity.

### **Secondary Alternative: Option (b)**
If the team desires standard port 443 (omitting the `:8443` suffix in URLs) or prefers 100% domain decoupling, spending ~$2 for `l3moninstances.xyz` is clean, robust, and requires no code changes.

**Decision:** The infrastructure lead / project owner will choose between Option (a) and Option (b). No code changes are required until the decision is finalized.
