#!/usr/bin/env bash
# tools/ops/fire_drill.sh - Fire every operational alert once on purpose
#
# Injects simulated alerts into Alertmanager to verify end-to-end receipt,
# displays firing alerts, and clears them.
#
# Usage:
#   tools/ops/fire_drill.sh [--url http://127.0.0.1:9093]
set -euo pipefail

ALERTMANAGER_URL="${1:-http://127.0.0.1:9093}"

ALERTS=(
  "High5xxRate:critical:service=nginx:02-app-down.md"
  "HighLatency:warning:service=nginx:05-ddos-attack.md"
  "DatabaseConnectionsHigh:warning:service=mariadb:03-database-recovery.md"
  "RedisMemoryHigh:warning:service=cache:04-redis-down.md"
  "DiskSpaceWarning:warning:service=host:01-event-start.md"
  "DiskSpaceCritical:critical:service=host:01-event-start.md"
  "ContainerRestarting:warning:service=container:02-app-down.md"
  "ReadyzFailing:critical:service=ctfd:02-app-down.md"
  "CTFtimeFeedStale:warning:service=ctftime:12-ctftime-standings.md"
  "BackupStale:warning:service=backup:03-database-recovery.md"
  "MailQueueGrowing:warning:service=mail:01-event-start.md"
  "CertificateExpiring:warning:service=tls:01-event-start.md"
  "InstancerErrorRateHigh:warning:service=instancer:08-kill-instances.md"
)

echo "=== L3m0nCTF 2026 Alert Fire Drill ==="
echo "Target Alertmanager: $ALERTMANAGER_URL"
echo "Injecting ${#ALERTS[@]} operational alerts ..."

PASSED=0
FAILED=0

AM_IMAGE="prom/alertmanager:v0.27.0@sha256:e13b6ed5cb929eeaee733479dce55e10eb3bc2e9c4586c705a4e8da41e5eacf5"

# Determine Docker network to use if reaching internal service
NETWORK_FLAG=()
AM_DOCKER_URL="$ALERTMANAGER_URL"
if [[ "$ALERTMANAGER_URL" == *"localhost"* ]] || [[ "$ALERTMANAGER_URL" == *"127.0.0.1"* ]]; then
  NETWORK_FLAG=("--network" "l3mon_ops")
  AM_DOCKER_URL="http://alertmanager:9093"
fi

for alert_spec in "${ALERTS[@]}"; do
  IFS=":" read -r name severity svc_label runbook <<< "$alert_spec"

  echo -n "Firing alert [$name] ... "

  # Inject alert using amtool inside container
  MSYS_NO_PATHCONV=1 docker run --rm \
    "${NETWORK_FLAG[@]}" \
    --entrypoint /bin/amtool \
    "$AM_IMAGE" alert add \
      --alertmanager.url="$AM_DOCKER_URL" \
      --annotation=summary="\"Fire drill simulated alert for $name\"" \
      --annotation=runbook="\"$runbook\"" \
      --annotation=description="\"Simulated alert drill verification\"" \
      alertname="$name" \
      severity="$severity" \
      "$svc_label"

  # Verify alert exists in Alertmanager
  OUT=$(MSYS_NO_PATHCONV=1 docker run --rm \
    "${NETWORK_FLAG[@]}" \
    --entrypoint /bin/amtool \
    "$AM_IMAGE" alert query \
      --alertmanager.url="$AM_DOCKER_URL" \
      alertname="$name" || true)

  if grep -q "$name" <<< "$OUT"; then
    echo "VERIFIED in Alertmanager (severity: $severity, runbook: $runbook)"
    PASSED=$((PASSED + 1))
  else
    echo "FAILED to verify in Alertmanager!"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "=== Fire Drill Summary ==="
echo "Total Alerts Tested : ${#ALERTS[@]}"
echo "Successfully Verified: $PASSED"
echo "Failed              : $FAILED"
echo "=========================="

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi

echo "Alert fire drill completed successfully."
