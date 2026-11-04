# Challenge Host Operator Runbook

This runbook guides operators and infrastructure engineers on provisioning, operating, scaling, and decommissioning L3m0nCTF challenge hosts (Milestone 2 & 3).

---

## 1. What a Challenge Host Is and Why It Is a Separate Machine

A **challenge host** is a dedicated Linux VM (Debian 12 / Ubuntu 22.04+) running in the same private cloud VPC as the CTFd platform VM. It runs:
1. **The Instancer Agent** (`services/instancer` started with `INSTANCER_AGENT_MODE=1`): Manages the container lifecycle of challenge instances.
2. **The Socket Proxy** (`tecnativa/docker-socket-proxy` pinned by digest): Strictly exposes only `containers` and `networks` endpoints; completely disables dangerous Docker endpoints (`exec`, `volumes`, `auth`, `build`).
3. **The Host Router** (`services/instancer/router`): Handles Hop 2 HTTP reverse-proxying into containers and direct TCP port relays (ports 30000–30199).

### Why It Is an Isolated Machine
- **Zero Master Secret Exposure:** The challenge host **never** holds `FLAG_HMAC_SECRET`, database credentials, or CTFd secret keys. Flags are derived dynamically on the platform VM and passed via the start API.
- **Kernel & Container Containment:** Even in the worst-case scenario where an attacker achieves a kernel exploit or container breakout, the attacker lands on an isolated host without access to the CTFd database, scoreboard, or other challenge hosts.
- **Independent Failure Domain:** A crash, resource exhaustion, or network disruption on one challenge host affects only instances on that host. The platform scheduler automatically marks the host down for 30 seconds and fails over to other healthy hosts.

---

## 2. Machine Sizing Guide (From Packing Benchmarks)

Hardware capacity planning must be determined empirically rather than estimated. Challenge instance memory consumption varies widely depending on whether challenges run lightweight compiled binaries (e.g. pwn/rev) or full web runtimes (Python/Node/JVM).

Operators must run the packing benchmark tool on the target machine before sizing production slots:
```bash
python services/instancer/tools/pack_test.py --slug <challenge-slug> --count 20
```

### Capacity Sizing Baseline Guidelines:
- **Host Infrastructure Overhead:** Agent daemon + socket proxy + router + Linux kernel baseline consumes ~200–300 MB RAM.
- **Per-Container Allocation:** Budget RAM based on the declared `mem_limit` in `challenges.toml` (e.g. 128m or 256m) plus 20–30% safety headroom for kernel networking buffers and conntrack table overhead.
- **Port Relays:** Ensure `INSTANCER_TCP_PORT_COUNT` does not exceed the target host's available ephemeral port range and file descriptor limits (`nofile >= 65535`).
- **Slot Ceilings Calculation (Arithmetic):**
  Slot ceilings are calculated arithmetically as:
  `Slot Ceiling = floor((Assignable Host RAM - Host Infrastructure Overhead) / (mem_limit * (1 + safety_headroom)))`
  - 4 GB RAM VM (~3.7 GB assignable): 20–25 concurrent instances (at 128 MB `mem_limit` with headroom).
  - 8 GB RAM VM (~7.7 GB assignable): 45–55 concurrent instances (at 128 MB `mem_limit` with headroom).
  - 16 GB RAM VM (~15.7 GB assignable): 95–110 concurrent instances (at 128 MB `mem_limit` with headroom).
  Verify these bounds under real load with `pack_test.py --count <N>`.

---

## 3. Firewall and Network Rules

### Cloud VPC Firewall Rules:
| Direction | Source | Destination | Protocol / Ports | Purpose |
|---|---|---|---|---|
| **Ingress** | Platform VM Private IP (`10.0.0.1/32`) | Challenge Host Private IP | TCP `8300` | Instancer Agent API (Bearer auth) |
| **Ingress** | Platform VM Private IP (`10.0.0.1/32`) | Challenge Host Private IP | TCP `8080` (or `8090`) | Hop 1 to Hop 2 HTTP proxy traffic |
| **Ingress** | Internet (`0.0.0.0/0`) | Challenge Host Public IP | TCP `30000–30199` | Direct TCP challenge relays for players |
| **Egress** | Challenge Host | Internet | TCP `443` | Pulling challenge images from GHCR |

All other ingress ports (including Docker daemon port 2375 and internal router metrics port 8301) must be blocked at the cloud firewall.

### Host `DOCKER-USER` iptables Rules (Configured by `bootstrap.sh`):
- **Metadata Protection:** Outbound traffic from containers to `169.254.169.254` is dropped.
- **VPC Isolation:** Outbound traffic from the instance pool (`10.200.0.0/16`) to private RFC1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) is dropped.
- **Router Access:** Inbound relay traffic from the local router into the instance pool is accepted.

---

## 4. Provisioning a Fresh Challenge Host

### Step 1: Run Bootstrap
On the target Linux VM (as root):
```bash
# 1. Preview changes (safe, dry-run mode)
./deploy/challenge-host/bootstrap.sh --dry-run

# 2. Apply configuration
INSTANCER_INSTANCE_POOL="10.200.0.0/16" PLATFORM_VM_IP="10.0.0.1" ./deploy/challenge-host/bootstrap.sh --apply
```

### Step 2: Configure Deployment Secrets
Tokens can be placed either in `/etc/l3mon/secrets` or in repository `.secrets/`.
```bash
# Example using system directory /etc/l3mon/secrets
mkdir -p /etc/l3mon/secrets
chmod 0700 /etc/l3mon/secrets

# Generate strong random tokens
openssl rand -hex 32 > /etc/l3mon/secrets/INSTANCER_TOKEN
openssl rand -hex 32 > /etc/l3mon/secrets/ROUTER_TOKEN
chmod 0600 /etc/l3mon/secrets/*
```

### Step 3: Start Challenge Host Services
Define `HOST_PRIVATE_BIND_IP` with the host's private cloud VPC IP address (e.g. `10.0.1.10`) so the platform VM can reach the agent (port 8300) and router (port 8090). Do not leave this at default `127.0.0.1` unless testing locally on the same host.

Ensure `INSTANCER_TRUSTED_FORWARDERS` is configured in `/etc/l3mon/challenge-host.env` (or exported in environment) with the platform VM's private IP address (e.g. `10.0.0.1` or `10.0.1.1`). `bootstrap.sh` sets this automatically from `PLATFORM_VM_IP`. This allows the Hop 2 host router to trust `X-L3mon-Client-IP` forwarded by Hop 1, preserving per-player rate limits and connection tracking.

Point secret file environment variables to the token locations:
```bash
export HOST_PRIVATE_BIND_IP="10.0.1.10"
export INSTANCER_TRUSTED_FORWARDERS="10.0.0.1"
export INSTANCER_TOKEN_FILE_HOST="/etc/l3mon/secrets/INSTANCER_TOKEN"
export ROUTER_TOKEN_FILE_HOST="/etc/l3mon/secrets/ROUTER_TOKEN"

docker compose -f deploy/compose/compose.challenge-host.yml up -d
```

Verify services are running and healthy:
```bash
docker compose -f deploy/compose/compose.challenge-host.yml ps
curl -s -H "Authorization: Bearer $(cat /etc/l3mon/secrets/INSTANCER_TOKEN)" http://${HOST_PRIVATE_BIND_IP}:8300/v1/capacity
```

---

## 5. Adding the Host to the Platform Fleet

On the **platform VM**, edit `/etc/l3mon/hosts.json` (or `config/instancer/hosts.json`):
```json
[
  {
    "name": "h1",
    "url": "http://10.0.1.10:8300",
    "router_url": "http://10.0.1.10:8080",
    "public_tcp_host": "h1.inst.l3m0nctf.xyz",
    "weight": 1.0,
    "max_slots": 150,
    "ca_cert": null
  }
]
```
Add the host's authentication tokens to the platform's `.env` or secrets:
- `INSTANCER_TOKEN_H1`: Token matching `INSTANCER_TOKEN` on host h1.
- `ROUTER_TOKEN_H1`: Token matching `ROUTER_TOKEN` on host h1.

The platform scheduler will automatically probe `GET /v1/capacity` on host `h1`, cache it for 5s, and begin routing new challenge starts to it.

---

## 6. Challenge Image Workflow (GHCR Integration)

1. **Registry:** Images are stored in the private GitHub Container Registry (`ghcr.io/sivaahari/...`).
2. **Fine-Grained Read-Only Token:** Challenge hosts receive a fine-grained GitHub PAT with `read:packages` permissions scoped strictly to the challenge packages. The token is stored at `/etc/l3mon/secrets/ghcr_token` (mode 0600).
3. **Pulling Images:**
   ```bash
   cat /etc/l3mon/secrets/ghcr_token | docker login ghcr.io -u <user> --password-stdin
   docker pull ghcr.io/sivaahari/challenge-pwn-demo:v1@sha256:...
   ```
4. **Zero Baked Flags:** Challenge images **never** contain flags in the image layer. Flags are supplied exclusively at runtime via the `FLAG` environment variable from CTFd.

---

## 7. Operational Procedures

### Kill-All Operations
In case of emergency, tournament conclusion, or infrastructure reset:
- **Via Platform Admin API:** Calling `POST /api/v1/l3mon/admin/instances/kill` broadcasts the kill request across all registered hosts in `hosts.json`, stopping all ephemeral containers simultaneously.
- **Directly on a Challenge Host:**
  ```bash
  curl -X POST -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
    -d '{}' http://127.0.0.1:8300/v1/admin/kill
  ```

### Handling Host Failure or Outage
If a challenge host crashes, loses network connectivity, or becomes unresponsive:
1. **Automatic 30-Second Down Penalty:** The platform capacity tracker automatically marks the host as `DOWN` for 30 seconds upon the first failed probe.
2. **Scheduler Failover:** All subsequent player starts are immediately diverted to the remaining healthy challenge hosts.
3. **Player Status:** Existing instances on the down host report as `state="error"` in the player API.
4. **Recovery:** When the host is brought back online, it recovers automatically once the 30-second penalty expires and the capacity probe succeeds.

### Removing or Decommissioning a Host
1. Stop routing new starts to the host by removing its entry from `/etc/l3mon/hosts.json` on the platform VM.
2. Wait for active instances to expire (or invoke `kill` on that specific host).
3. Shut down the compose services on the host:
   ```bash
   docker compose -f deploy/compose/compose.challenge-host.yml down
   ```
4. Revoke `INSTANCER_TOKEN_<NAME>` and `ROUTER_TOKEN_<NAME>`.

### Token Rotation
To rotate authentication tokens without downtime:
1. Generate new tokens on the challenge host.
2. Update `/etc/l3mon/secrets/INSTANCER_TOKEN` and restart the agent container.
3. Update `INSTANCER_TOKEN_<NAME>` in the platform configuration and restart CTFd.
