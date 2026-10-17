# Runbook 14: Escalation Directory (Who to Call)

## Purpose
Emergency contact roster and escalation tree for L3m0nCTF 2026 operations. Keep printed copies in the operations control room.

## Incident Escalation Tree

```
                      +-----------------------------+
                      |   Incident Commander (Lead) |
                      +--------------+--------------+
                                     |
             +-----------------------+-----------------------+
             |                                               |
+------------v------------+                     +------------v------------+
|  Infrastructure / Ops   |                     |     Challenge Lead      |
|  (Server, DB, Network)  |                     | (Dynamic Flags, Exploits)|
+------------+------------+                     +------------+------------+
             |                                               |
+------------v------------+                     +------------v------------+
| Cloudflare / DNS / VM   |                     |     Support / Discord   |
|   (GCP Console Access)  |                     |    (Player Comms, Tix)  |
+-------------------------+                     +-------------------------+
```

## Emergency Contact Directory

*Note: Blanks to be populated during event briefing by the event leadership. Do NOT commit real personal phone numbers to git.*

| Role | Name | Phone / Signal | Discord Tag | Primary Responsibility |
|---|---|---|---|---|
| **Incident Commander** | [                      ] | [                      ] | @[             ] | Final decision on downtime, freezes, and schedule adjustments |
| **Infrastructure Lead** | [                      ] | [                      ] | @[             ] | Host VM, Docker Compose, Nginx, MariaDB, backups |
| **Network & Cloudflare** | [                      ] | [                      ] | @[             ] | Cloudflare WAF, DNS, Under Attack mode, Google Cloud |
| **Challenge Author Lead**| [                      ] | [                      ] | @[             ] | Bug fixes, challenge release overrides, voiding solves |
| **Instancer Operator** | [                      ] | [                      ] | @[             ] | Ephemeral container runner, worker VM scaling, instancer API |
| **Communications Lead** | [                      ] | [                      ] | @[             ] | Discord broadcasts, player notifications, social announcements |
| **Faculty Sponsor** | [                      ] | [                      ] | @[             ] | TIFAC-CORE university liaison, institutional escalation |

## Third-Party Provider Emergency Consoles

| Provider | Service | Console URL / Emergency Link | Account Owner |
|---|---|---|---|
| **Google Cloud Platform** | Compute Engine VM | `https://console.cloud.google.com/` | [                      ] |
| **Cloudflare** | DNS & Edge WAF | `https://dash.cloudflare.com/` | [                      ] |
| **CTFtime** | Event Listing & Standings | `https://ctftime.org/event/mail/` | [                      ] |
| **SMTP Mail Provider** | Email Delivery Relay | [                      ] | [                      ] |

---
- [ ] followed once by someone who did not write it
