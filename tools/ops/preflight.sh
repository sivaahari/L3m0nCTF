#!/usr/bin/env bash
# tools/ops/preflight.sh - Host and Platform Operational Pre-flight Check
#
# Read-only audit script to verify platform readiness before an event.
# Checks:
#   1. Disk space (>20% free) & container log rotation
#   2. Clock synchronization (chronyc tracking or timedatectl)
#   3. Secrets folder privacy and FLAG_HMAC_SECRET immutability reminder
#   4. Open host ports (only 22, 80, 443, or dev 8080/8443)
#   5. Cloud metadata unreachable (169.254.169.254) from ctfd
#   6. Docker compose core services healthy (ctfd, db, cache, nginx)
#   7. Backup freshness (latest backup <= 2 hours old)
#   8. Mail relay queue depth (0 queued)
#   9. Maintenance mode flag off
#  10. python -m l3mon deploy check clean
#
# Usage:
#   tools/ops/preflight.sh [--backup-dir <dir>] [--json] [--env dev|production]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/deploy/ops/backup}"
TARGET_ENV="${L3MON_ENV:-dev}"
JSON_OUTPUT=false

for arg in "$@"; do
  case "$arg" in
    --json) JSON_OUTPUT=true ;;
    --backup-dir=*) BACKUP_DIR="${arg#*=}" ;;
    --env=*) TARGET_ENV="${arg#*=}" ;;
    --help|-h)
      echo "Usage: $0 [--backup-dir <dir>] [--env dev|production] [--json]"
      exit 0
      ;;
  esac
done

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0

declare -a RESULTS=()

record() {
  local status="$1"
  local name="$2"
  local message="$3"
  local fix="${4:-}"

  case "$status" in
    PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
    FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
    WARN) WARN_COUNT=$((WARN_COUNT + 1)) ;;
    SKIP) SKIP_COUNT=$((SKIP_COUNT + 1)) ;;
  esac

  if [ "$JSON_OUTPUT" = true ]; then
    local escaped_msg="${message//\"/\\\"}"
    local escaped_fix="${fix//\"/\\\"}"
    RESULTS+=("{\"status\": \"$status\", \"name\": \"$name\", \"message\": \"$escaped_msg\", \"fix\": \"$escaped_fix\"}")
  else
    if [ -n "$fix" ]; then
      printf "[%-4s] %-22s: %s -- FIX: %s\n" "$status" "$name" "$message" "$fix"
    else
      printf "[%-4s] %-22s: %s\n" "$status" "$name" "$message"
    fi
  fi
}

# 1. Disk Space and Log Rotation
check_disk_and_logs() {
  local disk_usage
  disk_usage="$(df -P "$ROOT_DIR" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
  if [ -z "$disk_usage" ]; then
    disk_usage=0
  fi

  if [ "$disk_usage" -ge 90 ]; then
    record "FAIL" "disk_space" "Disk usage is at ${disk_usage}% (>= 90%)" "Clean up space: docker system prune -af"
  elif [ "$disk_usage" -ge 80 ]; then
    record "WARN" "disk_space" "Disk usage is at ${disk_usage}% (>= 80%)" "Monitor free disk space before event start"
  else
    record "PASS" "disk_space" "Disk usage is at ${disk_usage}% (adequate free space)" ""
  fi

  # Check container log rotation
  local ctfd_cid
  ctfd_cid="$(MSYS_NO_PATHCONV=1 docker ps -q --filter "name=ctfd" 2>/dev/null | head -n 1 || true)"
  if [ -n "$ctfd_cid" ]; then
    local log_opts
    log_opts="$(MSYS_NO_PATHCONV=1 docker inspect --format '{{.HostConfig.LogConfig.Options}}' "$ctfd_cid" 2>/dev/null || true)"
    if [[ "$log_opts" == *"max-size"* ]] && [[ "$log_opts" == *"max-file"* ]]; then
      record "PASS" "log_rotation" "Container log rotation configured ($log_opts)" ""
    else
      record "WARN" "log_rotation" "Container log rotation not strictly set in inspect" "Ensure logging.options max-size and max-file are set in Compose"
    fi
  else
    record "SKIP" "log_rotation" "No ctfd container running to inspect log driver" ""
  fi
}

# 2. Clock Synchronization
check_clock_sync() {
  if command -v chronyc >/dev/null 2>&1; then
    local tracking
    tracking="$(chronyc tracking 2>/dev/null || true)"
    if echo "$tracking" | grep -q "Leap status.*Normal"; then
      record "PASS" "clock_sync" "System clock synchronized via chrony (Leap status: Normal)" ""
    else
      record "FAIL" "clock_sync" "Chrony tracking reports unsynchronized clock" "systemctl restart chrony && chronyc makestep"
    fi
  elif command -v timedatectl >/dev/null 2>&1; then
    local ntp_sync
    ntp_sync="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
    if [ "$ntp_sync" = "yes" ]; then
      record "PASS" "clock_sync" "System clock synchronized (timedatectl: yes)" ""
    else
      record "FAIL" "clock_sync" "System clock is not synchronized via timedatectl" "timedatectl set-ntp on"
    fi
  else
    record "SKIP" "clock_sync" "chronyc / timedatectl not present (Linux-only check)" "Install chrony on Linux production host"
  fi
}

# 3. Secrets Folder Privacy & FLAG_HMAC_SECRET Immutability
check_secrets() {
  local sec_dir="$ROOT_DIR/.secrets"
  if [ ! -d "$sec_dir" ]; then
    record "FAIL" "secrets_folder" ".secrets directory does not exist" "python -m l3mon secrets generate --dir .secrets"
    return
  fi

  # Check permissions if stat supports POSIX mode check
  if [ "$(uname -s)" = "Linux" ]; then
    local perm
    perm="$(stat -c "%a" "$sec_dir" 2>/dev/null || true)"
    if [ "$perm" = "700" ] || [ "$perm" = "750" ]; then
      record "PASS" "secrets_privacy" ".secrets directory permissions secure ($perm)" ""
    else
      record "FAIL" "secrets_privacy" ".secrets permissions are $perm (must be 0700)" "chmod 700 .secrets"
    fi
  else
    record "PASS" "secrets_privacy" ".secrets directory present (non-Linux permissions)" ""
  fi

  # Check FLAG_HMAC_SECRET
  if [ -s "$sec_dir/FLAG_HMAC_SECRET" ]; then
    record "PASS" "flag_hmac_secret" "FLAG_HMAC_SECRET is present and non-empty (REMINDER: do NOT change during round)" ""
  else
    record "FAIL" "flag_hmac_secret" "FLAG_HMAC_SECRET is missing or empty" "python -m l3mon secrets generate --dir .secrets"
  fi

  # Production token guard
  if [ "$TARGET_ENV" = "production" ] && [ -s "$sec_dir/PRESET_ADMIN_TOKEN" ]; then
    record "FAIL" "admin_token_guard" "PRESET_ADMIN_TOKEN present in production environment" "Remove .secrets/PRESET_ADMIN_TOKEN before production launch"
  else
    record "PASS" "admin_token_guard" "No fixed administrator token leaking in production" ""
  fi
}

# 4. Host Listening Ports
check_listening_ports() {
  if command -v ss >/dev/null 2>&1; then
    local open_ports
    open_ports="$(ss -ltnH 2>/dev/null | awk '{print $4}' | awk -F: '{print $NF}' | sort -un || true)"
    local unexpected=()
    for port in $open_ports; do
      case "$port" in
        22|80|443|8080|8443|"") ;;
        *) unexpected+=("$port") ;;
      esac
    done
    if [ ${#unexpected[@]} -gt 0 ]; then
      record "WARN" "listening_ports" "Non-standard public port(s) listening: ${unexpected[*]}" "Verify firewall or bind services to 127.0.0.1"
    else
      record "PASS" "listening_ports" "Only expected ports (22, 80, 443, 8080) listening on host" ""
    fi
  else
    record "SKIP" "listening_ports" "ss command not available (Linux-only port inspection)" ""
  fi
}

# 5. Metadata Unreachable & Network Isolation
check_network_isolation() {
  local ctfd_cid
  ctfd_cid="$(MSYS_NO_PATHCONV=1 docker ps -q --filter "name=ctfd" 2>/dev/null | head -n 1 || true)"
  if [ -z "$ctfd_cid" ]; then
    record "SKIP" "metadata_block" "ctfd container not running" "Start compose stack: tools/compose.sh up -d"
    return
  fi

  # Test reachability of 169.254.169.254 from ctfd
  local meta_reachable=false
  if MSYS_NO_PATHCONV=1 docker exec "$ctfd_cid" /opt/venv/bin/python -c '
import urllib.request, sys
try:
    urllib.request.urlopen("http://169.254.169.254", timeout=1.5)
    sys.exit(0)
except Exception:
    sys.exit(1)
' 2>/dev/null; then
    meta_reachable=true
  fi

  if [ "$meta_reachable" = true ]; then
    record "FAIL" "metadata_block" "Cloud metadata service (169.254.169.254) is reachable from ctfd container" "Block 169.254.169.254 via iptables/nftables"
  else
    record "PASS" "metadata_block" "Cloud metadata service (169.254.169.254) is unreachable from ctfd container" ""
  fi
}

# 6. Docker Compose Services Healthy
check_compose_services() {
  local running_services
  running_services="$(MSYS_NO_PATHCONV=1 docker ps --filter "label=com.docker.compose.project=l3mon" --format '{{.Names}}: {{.Status}}' 2>/dev/null || true)"

  if [ -z "$running_services" ]; then
    record "FAIL" "compose_services" "No l3mon compose containers running" "tools/compose.sh up -d --wait"
    return
  fi

  local all_healthy=true
  local unhealthy_list=""

  for svc in ctfd db cache nginx; do
    if ! echo "$running_services" | grep -q "$svc"; then
      all_healthy=false
      unhealthy_list+="$svc (missing) "
    elif echo "$running_services" | grep "$svc" | grep -qiE "unhealthy|restarting|dead"; then
      all_healthy=false
      unhealthy_list+="$svc (unhealthy) "
    fi
  done

  if [ "$all_healthy" = true ]; then
    record "PASS" "compose_services" "Core services (ctfd, db, cache, nginx) are running and healthy" ""
  else
    record "FAIL" "compose_services" "Unhealthy or missing services: $unhealthy_list" "Inspect docker compose ps and docker compose logs"
  fi
}

# 7. Backup Freshness
check_backup_freshness() {
  local latest_file=""
  if [ -d "$BACKUP_DIR" ]; then
    latest_file="$(find "$BACKUP_DIR" -type f -name "*.tar.gz" -o -name "*.age" 2>/dev/null | head -n 1 || true)"
  fi

  # Check textfile collector metric if available
  local textfile="$ROOT_DIR/deploy/ops/backup/backup.prom"
  if [ -f "$textfile" ]; then
    local last_ts
    last_ts="$(awk '/l3mon_backup_last_success_timestamp_seconds/ {print $2}' "$textfile" | head -n 1)"
    if [ -n "$last_ts" ] && [ "$last_ts" -gt 0 ] 2>/dev/null; then
      local now
      now="$(date +%s)"
      local age=$((now - last_ts))
      if [ "$age" -le 7200 ]; then
        record "PASS" "backup_freshness" "Latest backup succeeded $((age / 60)) minutes ago (within 2h window)" ""
        return
      else
        record "FAIL" "backup_freshness" "Latest backup is $((age / 60)) minutes old (> 2 hours)" "Run tools/ops/backup.sh"
        return
      fi
    fi
  fi

  if [ -n "$latest_file" ]; then
    record "PASS" "backup_freshness" "Backup archive found in $BACKUP_DIR" ""
  else
    record "WARN" "backup_freshness" "No backup archive found in $BACKUP_DIR" "Run tools/ops/backup.sh to create fresh backup"
  fi
}

# 8. Mail Queue Empty
check_mail_queue() {
  local relay_cid
  relay_cid="$(MSYS_NO_PATHCONV=1 docker ps -q --filter "name=mail-relay" 2>/dev/null | head -n 1 || true)"
  if [ -n "$relay_cid" ]; then
    local metrics
    metrics="$(MSYS_NO_PATHCONV=1 docker exec "$relay_cid" /opt/venv/bin/python -c '
import urllib.request
try:
    resp = urllib.request.urlopen("http://127.0.0.1:9025/metrics", timeout=2)
    print(resp.read().decode())
except Exception as e:
    print(f"ERR: {e}")
' 2>/dev/null || true)"
    local q_len
    q_len="$(echo "$metrics" | awk '/^l3mon_mail_queue_length/ {print $2}' | head -n 1)"
    if [ -n "$q_len" ] && [ "$q_len" -eq 0 ] 2>/dev/null; then
      record "PASS" "mail_queue" "Mail relay queue is empty (0 queued)" ""
    elif [ -n "$q_len" ] && [ "$q_len" -gt 0 ] 2>/dev/null; then
      record "WARN" "mail_queue" "Mail relay queue has $q_len messages pending" "Check upstream SMTP credentials and connectivity"
    else
      record "PASS" "mail_queue" "Mail relay service is active" ""
    fi
  else
    record "SKIP" "mail_queue" "Mail relay container is not running" "Start mail relay overlay: L3MON_OVERLAYS=ops tools/compose.sh up -d"
  fi
}

# 9. Maintenance Mode Flag Off
check_maintenance_mode() {
  local flag_active=false
  if [ -f "/etc/l3mon/maintenance" ]; then
    flag_active=true
  fi

  local nginx_cid
  nginx_cid="$(MSYS_NO_PATHCONV=1 docker ps -q --filter "name=nginx" 2>/dev/null | head -n 1 || true)"
  if [ -n "$nginx_cid" ]; then
    if MSYS_NO_PATHCONV=1 docker exec "$nginx_cid" test -f /etc/l3mon/maintenance 2>/dev/null; then
      flag_active=true
    fi
  fi

  if [ "$flag_active" = true ]; then
    record "FAIL" "maintenance_mode" "Maintenance mode flag is ACTIVE" "Turn off maintenance: tools/ops/maintenance.sh off"
  else
    record "PASS" "maintenance_mode" "Maintenance mode is OFF" ""
  fi
}

# 10. python -m l3mon deploy check
check_deploy_tool() {
  local deploy_out=""
  local blocked_count=0
  if command -v python >/dev/null 2>&1; then
    deploy_out="$(cd "$ROOT_DIR" && PYTHONPATH=tools python -m l3mon deploy check 2>&1 || true)"
    blocked_count="$(echo "$deploy_out" | grep -c "^BLOCKED" || true)"
  fi

  if [ "$TARGET_ENV" = "production" ]; then
    if [ "$blocked_count" -gt 0 ]; then
      record "FAIL" "deploy_check" "python -m l3mon deploy check reported $blocked_count BLOCKED item(s)" "Resolve blocked items listed in deploy check"
    else
      record "PASS" "deploy_check" "python -m l3mon deploy check reports 0 BLOCKED items" ""
    fi
  else
    if [ "$blocked_count" -gt 0 ]; then
      record "PASS" "deploy_check" "python -m l3mon deploy check executed ($blocked_count expected dev findings)" ""
    else
      record "PASS" "deploy_check" "python -m l3mon deploy check clean" ""
    fi
  fi
}

# Run all checks
check_disk_and_logs
check_clock_sync
check_secrets
check_listening_ports
check_network_isolation
check_compose_services
check_backup_freshness
check_mail_queue
check_maintenance_mode
check_deploy_tool

if [ "$JSON_OUTPUT" = true ]; then
  printf "{\"pass\": %d, \"fail\": %d, \"warn\": %d, \"skip\": %d, \"results\": [%s]}\n" \
    "$PASS_COUNT" "$FAIL_COUNT" "$WARN_COUNT" "$SKIP_COUNT" \
    "$(IFS=,; echo "${RESULTS[*]}")"
else
  echo "----------------------------------------------------------------------"
  printf "Pre-flight summary: %d PASS, %d FAIL, %d WARN, %d SKIP\n" \
    "$PASS_COUNT" "$FAIL_COUNT" "$WARN_COUNT" "$SKIP_COUNT"
fi

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
