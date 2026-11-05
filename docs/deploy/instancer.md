# Challenge Instancer & Router Deployment Guide (`services/instancer`)

This document describes how to configure, deploy, and operate the challenge instancer service, reverse-proxy router, and multi-host fleet (SP4 / Stream F) for L3m0nCTF 2026.

---

## 1. Architecture & Design Principles

- **Zero Touch to CTFd Core:** Instance management is orchestrated via the `plugins/l3mon_instances` CTFd plugin. CTFd's internal files and core models remain unedited.
- **Strict Host & Container Isolation:**
  - Instanced containers run as unprivileged numeric UIDs (`user: "10001:10001"`).
  - All containers drop all capabilities (`cap_drop: [ALL]`), enforce `no-new-privileges: true`, have strict memory (`mem_limit`) and PID (`pids_limit`) boundaries, and run with read-only root filesystems (`read_only: true`).
  - Network isolation: Every instance lives in its own dedicated Docker bridge network with `internal: true` (no outbound route to the internet, gateway, or metadata services). Only the instancer router and CTFd are bridged into the routing network.
  - Modern bind-mount checking: `safety.assert_safe` verifies that no host mounts (`HostConfig.Mounts` or `Binds`) are permitted into instance containers.
- **Docker Socket Security:** The Docker socket is never mounted into CTFd or player containers. The instancer communicates through `tecnativa/docker-socket-proxy` with minimal privileges (containers and networks CRUD only; no exec, no swarm, no volume mounts, no system reconfigurations).
- **Two-Hop HTTP Routing & Direct TCP:**
  - HTTP challenges route through the Central Router on the platform VM (Hop 1) to the Host Router (Hop 2), which streams to the local container. Hop 1 takes destination addresses strictly from `hosts.json`, enforcing destination integrity.
  - TCP challenges connect directly to the challenge host's public DNS address (e.g. `h1.inst.l3m0nctf.xyz`) on allocated ports (30000–30199).

---

## 2. Environments and Compose Overlays

The instancer provides three overlay configurations. **Never run the development overlay on a production server.**

### 2.1 Development Overlay (`deploy/compose/compose.instancer.yml`)
- **Target:** Local laptops and continuous integration testing.
- **Run command:**
  ```bash
  L3MON_OVERLAYS=instancer tools/compose.sh up -d --wait
  ```
- **Characteristics:**
  - Mounts example manifest from repository (`config/instancer/challenges.example.toml`).
  - Router binds to loopback only (`127.0.0.1:8090` and `127.0.0.1:30000-30199`).
  - Allows local unpinned images for fast offline authoring and iteration if `INSTANCER_DEV=1`.
  - Development administrator token (`PRESET_ADMIN_TOKEN`) is available for test scripts.

### 2.2 Production Overlay (`deploy/compose/compose.instancer-production.yml`)
- **Target:** Platform VM on Google Cloud Compute Engine.
- **Run command:**
  ```bash
  L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production tools/compose.sh up -d --wait
  ```
- **Characteristics:**
  - `INSTANCER_DEV: "0"` is explicitly hardcoded and enforced.
  - Manifest is mounted read-only strictly from outside the git checkout (default `/etc/l3mon/challenges.toml:ro`).
  - All challenge images must be pinned by SHA-256 digest (`image@sha256:...`); unpinned tags are rejected on startup.
  - Instance TTL overrides by players are rejected (`L3MON_INSTANCES_TTL_OVERRIDE` is absent).
  - Router binds to public interface (`0.0.0.0:8090` and `0.0.0.0:30000-30199`).
  - `INSTANCE_DOMAIN` must be set to the production subdomain (e.g. `inst.l3m0nctf.xyz`).
  - No development administrator token (`PRESET_ADMIN_TOKEN`) is passed to any service.

### 2.3 Standalone Challenge Host (`deploy/compose/compose.challenge-host.yml`)
- **Target:** Dedicated remote challenge host VMs.
- **Run command:**
  ```bash
  docker compose -f deploy/compose/compose.challenge-host.yml up -d
  ```
- **Characteristics:**
  - Runs the agent (`INSTANCER_AGENT_MODE=1`), socket proxy, and router.
  - Exposes port 8300 (agent API) and 8080 (Hop 2 HTTP router) to the private cloud VPC only.
  - Publishes TCP ports 30000–30199 to the public internet (`0.0.0.0`).
  - Explicit container hardening (cap drop, read-only root, no-new-privileges, log rotation).

---

## 3. Configuration & Secrets

### 3.1 Secrets
Secrets are generated using `python -m l3mon secrets generate --dir ../.secrets` and mounted via Docker secrets into `/run/secrets/`:

| Secret Name | Secret File Path | Consumed By | Purpose |
|---|---|---|---|
| `INSTANCER_TOKEN` | `.secrets/INSTANCER_TOKEN` | `ctfd`, `instancer` | Authenticates CTFd plugin requests to instancer daemon (default fallback) |
| `INSTANCER_TOKEN_<HOST>` | `/run/secrets/INSTANCER_TOKEN_<HOST>` | `ctfd` | Dedicated bearer token for remote challenge host (e.g. `INSTANCER_TOKEN_H1`) |
| `ROUTER_TOKEN` | `.secrets/ROUTER_TOKEN` | `instancer`, `router` | Authenticates router route-polling queries from instancer (default fallback) |
| `ROUTER_TOKEN_<HOST>` | `/run/secrets/ROUTER_TOKEN_<HOST>` | `router` | Dedicated route read token for remote challenge host (e.g. `ROUTER_TOKEN_H1`) |
| `FLAG_HMAC_SECRET` | `.secrets/FLAG_HMAC_SECRET` | `ctfd` | Seeds HMAC-SHA256 dynamic per-team flag derivations (never stored on challenge hosts) |

### 3.2 Environment Variables

Set in environment or `deploy/compose/generated/compose.env`:

| Variable | Default (Dev) | Default (Prod) | Purpose |
|---|---|---|---|
| `INSTANCER_DEV` | `0` | `0` (enforced) | Development switch: 1 allows unpinned images |
| `INSTANCER_AGENT_MODE` | `0` | `1` (on challenge hosts) | Enables agent mode on challenge hosts |
| `INSTANCER_HOST_NAME` | `default` | `h1`, `h2`... | Name of this challenge host in the fleet |
| `INSTANCER_MANIFEST_FILE` | `../../config/instancer/challenges.example.toml` | `/etc/l3mon/challenges.toml` | Path to challenge manifest file |
| `INSTANCE_DOMAIN` | `localhost:8090` | Required (e.g. `inst.l3m0nctf.xyz`) | Wildcard domain routing suffix |
| `INSTANCER_INSTANCE_POOL` | `10.200.0.0/16,172.16.0.0/12` | `10.200.0.0/16` | Valid network CIDR pool for instance containers |
| `INSTANCER_TCP_PORT_START` | `30000` | `30000` | Start of TCP relay port range |
| `INSTANCER_TCP_PORT_COUNT` | `200` | `200` | Number of allocated TCP relay ports |
| `INSTANCER_TCP_MAX_CONNS_PER_IP`| `60` | `60` | Max concurrent TCP connections per client IP |
| `INSTANCER_TCP_MAX_TOTAL_CONNS` | `2000` | `2000` | Global TCP relay connection ceiling |
| `INSTANCER_TCP_RATE_LIMIT_BPS` | `2097152` (2 MB/s) | `2097152` | Token bucket rate limit per client connection |
| `INSTANCER_TCP_RATE_LIMIT_BURST`| `4194304` (4 MB) | `4194304` | Token bucket burst allowance |
| `ROUTER_BIND_HOST` | `127.0.0.1` | `0.0.0.0` | IP interface where router listens |
| `ROUTER_HTTP_PORT` | `8090` | `8090` | External TCP port for HTTP router |

---

## 4. Multi-Host Fleet Management (`hosts.json`)

The platform VM distributes challenge starts across multiple challenge hosts using `/etc/l3mon/hosts.json` (example in `config/instancer/hosts.example.json`):

```json
[
  {
    "name": "h1",
    "url": "http://10.200.1.10:8300",
    "router_url": "http://10.200.1.10:8080",
    "public_tcp_host": "h1.inst.l3m0nctf.xyz",
    "weight": 1.0,
    "max_slots": 150,
    "ca_cert": null
  },
  {
    "name": "h2",
    "url": "http://10.200.1.11:8300",
    "router_url": "http://10.200.1.11:8080",
    "public_tcp_host": "h2.inst.l3m0nctf.xyz",
    "weight": 1.0,
    "max_slots": 150,
    "ca_cert": null
  }
]
```

### Compose wiring for a multi-host fleet (`compose.instancer-multihost.yml`, optional)
A platform with ONE instancer (the default; every deployment so far) uses neither this overlay nor any of its files: `compose.instancer-production.yml` starts without a `hosts.json` and without per-host secrets. To switch to several hosts, add the overlay **after** the instancer overlays:

```bash
L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production,instancer-multihost \
  L3MON_HOSTS_FILE=/etc/l3mon/hosts.json \
  L3MON_INSTANCER_TOKENS_DIR=/etc/l3mon/host-secrets/instancer \
  L3MON_ROUTER_TOKENS_DIR=/etc/l3mon/host-secrets/router \
  tools/compose.sh up -d --wait
```
It also works in development (`L3MON_OVERLAYS=instancer,instancer-multihost`, with a hosts file and two folders of your own).

- `hosts.json` is mounted read-only into `ctfd` (`L3MON_HOSTS_PATH`) and into the `router` (`ROUTER_HOSTS_PATH`).
- One folder of `INSTANCER_TOKEN_<NAME>` files (one per listed host, name in capitals) is mounted read-only into `ctfd` only; one folder of `ROUTER_TOKEN_<NAME>` files into the router only. Any number of hosts needs **no edit of a compose file**: a new host is a line in `hosts.json` and two files.
- A listed host without its token is never fed the platform token: CTFd logs an ERROR at start naming the missing variable and does not use the host; the central router refuses to start and names it.
- The host name `default` is reserved for the implicit single-host mode and is refused in `hosts.json`.

> [!IMPORTANT]
> **Domain matching.** The challenge host kit's `INSTANCE_DOMAIN` **must equal the platform's `INSTANCE_DOMAIN`**. The central router forwards `Host: <id>.<domain>` unchanged to the host's router; with different domains the host answers HTTP 404.

> [!IMPORTANT]
> **The host-down mark is shared.** When a host fails a probe, the mark "down for 30 seconds" is kept in CTFd's cache (Redis), so every CTFd worker agrees. A player's list also probes the hosts of the listed instances (through the 5-second capacity cache), so a dead host is found without waiting for anyone to start an instance, and the player sees `error` rather than `running`.

### Scheduling & Failover Invariants:
1. **5-Second Capacity Cache:** `GET /v1/capacity` queries from each host are cached for 5 seconds to reduce inter-machine overhead.
2. **Optimistic Local Counter:** When a host is selected for a start, its local in-memory `slots_used` is immediately incremented (`_optimistic_slots += 1`). This eliminates the "thundering herd" problem where rapid concurrent starts pile onto one host during the 5s cache window.
3. **30-Second Down Penalty:** If a host fails to respond (timeout or connection refused), the scheduler marks it `DOWN` for 30 seconds. No new starts are routed to it during this window.
4. **Instance Stickiness:** An instance remains on its assigned host for all query, renew, and stop operations.
5. **Down Host Instance Status:** If an instance's host is down, the player API returns `state="error"`, never `"running"`.
6. **Hop 1 Destination Integrity:** The central router looks up the instance's host and forwards HTTP traffic strictly to that host's configured `router_url`. Untrusted route targets from the agent are completely ignored.

---

## 5. Author Challenge Image Workflow (GHCR Integration)

Authors build and deliver containerized challenges following Decision 4 (GitHub Container Registry):

1. **Automated CI Build & Push:**
   - Challenges are authored in the private challenges repository.
   - GitHub Actions workflow builds and pushes images to GHCR (`ghcr.io/sivaahari/...`).
   - Authors never hold direct write credentials to the registry; CI enforces image immutability.
2. **Private Packages & Read-Only Host PATs:**
   - Every package in GHCR is marked **private**.
   - Challenge hosts use a root-only, read-only fine-grained personal access token (`read:packages`) stored at `/etc/l3mon/secrets/ghcr_token` (mode 0600).
3. **Zero Baked Flags Rule:**
   - **No flag is ever baked into a Docker image layer.**
   - If a host or image is compromised, no participant flags are leaked. Flags are supplied exclusively at runtime via the `FLAG` environment variable derived dynamically by CTFd.
4. **Offline Fallback (`docker load`):**
   - For offline rehearsals or air-gapped operations, images can be loaded from `.tar` archives via `docker load`. The manifest must specify `image = "<repository>@sha256:<digest>"` referencing the locally tagged digest.

---

## 6. Manifest Format Specification & Field Limits

The manifest (`challenges.toml`) defines challenge specs loaded by the instancer:

```toml
[[challenge]]
slug = "pwn-lemon-kernel"
image = "ghcr.io/sivaahari/pwn-kernel@sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069"
protocol = "tcp"          # Allowed: "http" or "tcp"
port = 1337               # Target internal port inside container (1..65535)
cpus = 1.0                # CPU quota (0.1 .. 4.0)
mem_mb = 128              # Memory limit in MB (32 .. 2048)
pids = 64                 # Process limit (16 .. 256)
ttl_minutes = 45          # Base instance lifetime (5 .. 120)
max_per_team = 1          # Active instances per team (default 1)
max_body_bytes = 10485760 # Max HTTP request size in bytes (HTTP only, default 10MB)
response_timeout_seconds = 120 # HTTP backend response timeout
```

---

## 7. Operational Runbook

### 7.1 Emergency "Kill All" Instances
Terminates all active ephemeral instances across all configured hosts without touching core platform containers:
```bash
# Call platform admin kill endpoint
curl -X POST -H "Authorization: Bearer <CREW_ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{}' https://ctfd.l3m0nctf.xyz/api/v1/l3mon/admin/instances/kill
```

### 7.2 Token Rotation
Tokens can be rotated without interruption:
1. Update `/etc/l3mon/secrets/INSTANCER_TOKEN` and `ROUTER_TOKEN` on the challenge host.
2. Restart the challenge host agent and router.
3. Update `INSTANCER_TOKEN_<HOST>` and `ROUTER_TOKEN_<HOST>` in the platform configuration and reload CTFd.
