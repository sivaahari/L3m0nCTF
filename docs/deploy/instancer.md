# Challenge Instancer & Router Deployment Guide (`services/instancer`)

This document describes how to configure, deploy, and operate the challenge instancer service and reverse-proxy router (SP4) for L3m0nCTF 2026.

---

## 1. Architecture & Design Principles

- **Zero Touch to CTFd Core:** Instance management is orchestrated via the `plugins/l3mon_instances` CTFd plugin. CTFd's internal files and core models remain unedited.
- **Strict Host & Container Isolation:**
  - Instanced containers run as unprivileged numeric UIDs (`user: "10001:10001"`).
  - All containers drop all capabilities (`cap_drop: [ALL]`), enforce `no-new-privileges: true`, have strict memory (`mem_limit`) and PID (`pids_limit`) boundaries, and run with read-only root filesystems (`read_only: true`).
  - Network isolation: Every instance lives in its own dedicated Docker bridge network with `internal: true` (no outbound route to the internet, gateway, or metadata services). Only the instancer router and CTFd are bridged into the routing network.
  - Modern bind-mount checking: `safety.assert_safe` verifies that no host mounts (`HostConfig.Mounts` or `Binds`) are permitted into instance containers.
- **Docker Socket Security:** The Docker socket is never mounted into CTFd or player containers. The instancer communicates through `tecnativa/docker-socket-proxy` with minimal privileges (containers and networks CRUD only; no exec, no swarm, no volume mounts, no system reconfigurations).

---

## 2. Environments and Compose Overlays

The instancer provides two overlay configurations. **Never run the development overlay on a production server.**

### 2.1 Development Overlay (`deploy/compose/compose.instancer.yml`)
- **Target:** Local laptops and continuous integration testing.
- **Run command:**
  ```bash
  L3MON_OVERLAYS=instancer tools/compose.sh up -d --wait
  ```
- **Characteristics:**
  - Mounts example manifest from repository (`config/instancer/challenges.example.toml`).
  - Router binds to loopback only (`127.0.0.1:8090`).
  - Allows local unpinned images for fast offline authoring and iteration if `INSTANCER_DEV=1`.
  - Development administrator token (`PRESET_ADMIN_TOKEN`) is available for test scripts.

### 2.2 Production Overlay (`deploy/compose/compose.instancer-production.yml`)
- **Target:** Production cloud servers (Google Cloud Compute Engine).
- **Run command:**
  ```bash
  L3MON_ENV=production L3MON_OVERLAYS=instancer,instancer-production tools/compose.sh up -d --wait
  ```
- **Characteristics:**
  - `INSTANCER_DEV: "0"` is explicitly hardcoded and enforced.
  - Manifest is mounted read-only strictly from outside the git checkout (default `/etc/l3mon/challenges.toml:ro`).
  - All challenge images must be pinned by SHA-256 digest (`image@sha256:...`); unpinned tags are rejected on startup.
  - Instance TTL overrides by players are rejected (`L3MON_INSTANCES_TTL_OVERRIDE` is absent).
  - Router binds to public interface (`${ROUTER_BIND_HOST:-0.0.0.0}:${ROUTER_HTTP_PORT:-8090}`).
  - `INSTANCE_DOMAIN` must be set to the production subdomain (e.g. `inst.l3m0nctf.xyz`).
  - No development administrator token (`PRESET_ADMIN_TOKEN`) is passed to any service.

---

## 3. Configuration & Secrets

### 3.1 Secrets
Secrets are generated using `python -m l3mon secrets generate --dir ../.secrets` and mounted via Docker secrets into `/run/secrets/`:

| Secret Name | Secret File Path | Consumed By | Purpose |
|---|---|---|---|
| `INSTANCER_TOKEN` | `.secrets/INSTANCER_TOKEN` | `ctfd`, `instancer` | Authenticates CTFd plugin requests to instancer daemon |
| `ROUTER_TOKEN` | `.secrets/ROUTER_TOKEN` | `instancer`, `router` | Authenticates router route-polling queries from instancer |
| `FLAG_HMAC_SECRET` | `.secrets/FLAG_HMAC_SECRET` | `ctfd`, `instancer` | Seeds HMAC-SHA256 dynamic per-team flag derivations |

### 3.2 Environment Variables

Set in environment or `deploy/compose/generated/compose.env`:

| Variable | Default (Dev) | Default (Prod) | Purpose |
|---|---|---|---|
| `INSTANCER_DEV` | `0` | `0` (enforced) | Development switch: 1 allows unpinned images |
| `INSTANCER_MANIFEST_FILE` | `../../config/instancer/challenges.example.toml` | `/etc/l3mon/challenges.toml` | Path to challenge manifest file |
| `INSTANCE_DOMAIN` | `localhost:8090` | Required (e.g. `inst.l3m0nctf.xyz`) | Wildcard domain routing suffix |
| `ROUTER_BIND_HOST` | `127.0.0.1` | `0.0.0.0` | IP interface where router listens |
| `ROUTER_HTTP_PORT` | `8090` | `8090` | External TCP port for HTTP router |
| `ROUTER_TLS_DIR` | (none) | `/etc/l3mon/router-tls` | Directory containing optional router TLS certs |

---

## 4. Operational Runbook

### 4.1 Adding / Updating Challenges in Manifest
1. Author challenge container, test locally, build and push to container registry.
2. Obtain the exact image digest:
   ```bash
   docker inspect --format='{{index .RepoDigests 0}}' <image>
   ```
3. Edit `/etc/l3mon/challenges.toml` on the challenge host, defining the challenge stanza:
   ```toml
   [[challenge]]
   slug = "web-sqli-pwn"
   image = "ghcr.io/sivaahari/web-sqli@sha256:abcd1234..."
   protocol = "http"
   port = 80
   cpus = 0.5
   mem_mb = 256
   pids = 64
   ttl_minutes = 45
   max_per_team = 1
   ```
4. Restart the instancer to reload the manifest:
   ```bash
   docker restart l3mon-instancer-1
   ```

### 4.2 Emergency "Kill All" Instances
To immediately terminate all active challenge instances without stopping platform services:
```bash
docker exec -it l3mon-instancer-1 python -c "
from services.instancer.docker_client import DockerClient
client = DockerClient()
containers = client.list_containers(all=True)
for c in containers:
    labels = c.get('Labels', {})
    if labels.get('l3mon.service') == 'instancer':
        cid = c['Id']
        print(f'Terminating {cid[:12]}...')
        client.stop_container(cid, timeout=2)
        client.remove_container(cid)
print('Done.')
"
```

### 4.3 Token Rotation
To rotate `INSTANCER_TOKEN` or `ROUTER_TOKEN`:
1. Generate new token:
   ```bash
   openssl rand -hex 32 > .secrets/INSTANCER_TOKEN
   ```
2. Re-create `ctfd`, `instancer`, and `router`:
   ```bash
   tools/compose.sh up -d --force-recreate ctfd instancer router
   ```

### 4.4 What to Do If a Challenge Host Dies
If the host running instancer fails or reboots:
1. Docker restarts the containers defined in Compose (`restart: unless-stopped`).
2. When the instancer restarts, its startup lifecycle runs `reconcile_from_docker()`:
   - Queries Docker for all existing containers and networks labeled `l3mon.service=instancer`.
   - Recovers active running instances into memory.
   - Cleans up orphan networks left behind by interrupted creations.
3. The router polls `GET /routes` on the instancer every 2 seconds, immediately restoring all HTTP routes.
