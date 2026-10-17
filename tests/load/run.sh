#!/usr/bin/env bash
# tests/load/run.sh - Run k6 load testing suite with safety rails
#
# Runs official pinned grafana/k6 container against allow-listed target.
# Enforces target validation before starting container.
#
# Usage:
#   tests/load/run.sh [--target http://localhost:8080] [--profile laptop|full] [--setup] [--cleanup]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_URL="${TARGET_URL:-http://localhost:8080}"
PROFILE="laptop"
DO_SETUP=false
DO_CLEANUP=false

K6_IMAGE="grafana/k6:0.54.0@sha256:1f40432b1cbe7234e977f96c362c9bc550a2d2b583d014dd8669fe40d3e9e755"

for arg in "$@"; do
  case "$arg" in
    --target=*) TARGET_URL="${arg#*=}" ;;
    --profile=*) PROFILE="${arg#*=}" ;;
    --setup) DO_SETUP=true ;;
    --cleanup) DO_CLEANUP=true ;;
    --help|-h)
      echo "Usage: $0 [--target=<url>] [--profile=laptop|full] [--setup] [--cleanup]"
      exit 0
      ;;
  esac
done

# 1. Target Allow-List Safety Check
ALLOW_LIST=("localhost" "127.0.0.1" "localhost:8080" "127.0.0.1:8080" "ctfd:8000" "nginx:80" "nginx:8080" "play.l3m0nctf.xyz")
TARGET_HOST="$(echo "$TARGET_URL" | sed -e 's|^[^/]*//||' -e 's|/.*$||' | tr '[:upper:]' '[:lower:]')"

ALLOWED=false
for allowed in "${ALLOW_LIST[@]}"; do
  # the whole host name (with or without its port) must be on the list: a name that merely STARTS like an allowed one (localhost.evil.example) is not
  if [[ "$TARGET_HOST" == "$allowed" || "${TARGET_HOST%%:*}" == "$allowed" ]]; then
    ALLOWED=true
    break
  fi
done

if [ "$ALLOWED" != true ]; then
  echo "[SAFETY RAIL REFUSAL] Target '$TARGET_HOST' is NOT on the allow-list!" >&2
  echo "Allowed targets: ${ALLOW_LIST[*]}" >&2
  exit 2
fi

# 2. Optional Setup
if [ "$DO_SETUP" = true ]; then
  echo "Provisioning zzload accounts on $TARGET_URL ..."
  python "$ROOT_DIR/tests/load/setup.py" --base "$TARGET_URL"
fi

# 3. Optional Cleanup
if [ "$DO_CLEANUP" = true ]; then
  echo "Cleaning up zzload accounts from $TARGET_URL ..."
  python "$ROOT_DIR/tests/load/cleanup.py" --base "$TARGET_URL"
  exit 0
fi

# 4. Run k6 container
echo "Starting k6 load test (profile: $PROFILE, target: $TARGET_URL) ..."
NETWORK="l3mon_edge"
if [[ "$TARGET_URL" == *"ctfd:8000"* ]]; then
  NETWORK="l3mon_internal"
fi

LOAD_DIR="$(cd "$ROOT_DIR/tests/load" && pwd -W 2>/dev/null || pwd)"

# If running on host localhost:8080, target nginx directly inside l3mon_edge
DOCKER_TARGET="$TARGET_URL"
HOST_HEADER=""
if [[ "$TARGET_URL" == *"localhost:8080"* ]] || [[ "$TARGET_URL" == *"127.0.0.1:8080"* ]]; then
  DOCKER_TARGET="http://nginx:8080"
  HOST_HEADER="localhost"
elif [[ "$TARGET_URL" == *"localhost"* ]] || [[ "$TARGET_URL" == *"127.0.0.1"* ]]; then
  DOCKER_TARGET="http://nginx:8080"
  HOST_HEADER="localhost"
fi

MSYS_NO_PATHCONV=1 docker run --rm -i \
  --network "$NETWORK" \
  --add-host host.docker.internal:host-gateway \
  -v "${LOAD_DIR}:/scripts" \
  -e "TARGET_URL=${DOCKER_TARGET}" \
  -e "HOST_HEADER=${HOST_HEADER}" \
  -e "PROFILE=${PROFILE}" \
  -e "SUMMARY_PATH=/scripts/summary.json" \
  "$K6_IMAGE" run /scripts/scenarios.js
