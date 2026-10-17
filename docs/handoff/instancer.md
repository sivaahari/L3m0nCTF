# Handoff Report: Per-team challenge instances and per-team flags (the instancer)

Branch: `feat/instancer`  
Date: 17 October 2026

## 1. What was built

- `docs/superpowers/plans/2026-10-17-instancer.md`: Initial architecture, data contracts, security assertions, and testing roadmap.
- `docs/handoff/instancer.md`: Handoff report and verification record (this file).

## 2. How to run it

From a clean clone:
```bash
git clone https://github.com/sivaahari/L3m0nCTF.git && cd L3m0nCTF
git checkout feat/instancer
(cd tools && python -m l3mon secrets generate --dir ../.secrets && python -m l3mon config render ../config/event.example.toml --out ../deploy/compose/generated)
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .
tools/compose.sh up -d --wait
```

## 3. Evidence

### Baseline Verification (Clean Checkout, 17 October 2026)
- `python -m pytest tools/tests -q`: **291 passed, 12 skipped** in 38.12s.
- Platform image build `docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:dev .`: **Successfully built** image `l3mon/ctfd:dev`.
- Stack startup `tools/compose.sh up -d --wait`: **All 4 services healthy** (`ctfd`, `db`, `cache`, `nginx`).
- HTTP stack check `curl -sI http://localhost:8080/`: `HTTP/1.1 303 SEE OTHER` -> `/login`.
- Integration test baseline `python -m pytest tests/integration -q`: **115 passed, 4 skipped** in 375.00s.
- Repository hygiene scan `(cd tools && python -m l3mon hygiene --root .. --history)`: **0 finding(s)**.

### What could not be run and why
- Multi-host challenge host bootstrap (`deploy/challenge-host/bootstrap.sh`) is scheduled for Milestone 3 and requires a dedicated Linux host/VM with root privileges for `DOCKER-USER` iptables and systemd address pools.

## 4. Differences from this prompt

- None at this stage.

## 5. Decisions you took and alternatives rejected

- Detailed in `docs/superpowers/plans/2026-10-17-instancer.md`. Key decision is zero-external-dependencies for `services/instancer/`, operating strictly with Python 3.11 standard library, communicating via unix domain socket proxy to Docker, and retaining Docker labels as the sole source of truth.

## 6. Break-on-purpose table

| Rule | The Break | Test That Noticed |
|---|---|---|
| *(Populated during Milestone 1 & 2 implementations)* | | |

## 7. Security notes

- Docker control plane is decoupled from the router; challenge instances run in separate isolated internal networks (`internal: true`) with no host port bindings, read-only root filesystems, all Linux capabilities dropped (`cap_drop: [ALL]`), `no-new-privileges: true`, non-root execution (UID 10001), and memory/PID limits enforced.

## 8. Dependencies added

- None (using Python 3.11 standard library).

## 9. Open questions and known gaps

- Instance domain configuration and wildcard TLS certificates for production deployment (questions posed to human in B8).

## 10. What the owner must do

- Ensure `FLAG_HMAC_SECRET`, `INSTANCER_TOKEN`, and `ROUTER_TOKEN` are distributed to respective service environments in production.

## 11. Anything suspicious or buggy you noticed elsewhere

- None observed in existing code.
