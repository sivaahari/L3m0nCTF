#!/usr/bin/env bash
# Switch platform maintenance mode on or off.
#   tools/ops/maintenance.sh on [--until <ISO time>] [--message "<plain text>"]
#   tools/ops/maintenance.sh off
#
# Writes or removes the maintenance flag file atomically.
# In development/Compose: writes to deploy/ops/maintenance/maintenance (mounted into nginx at /etc/l3mon/maintenance).
# On Linux host: uses /etc/l3mon/maintenance if writable.
set -euo pipefail

action="${1:-}"
if [[ "$action" != "on" && "$action" != "off" ]]; then
  echo "Usage: $0 on|off [--until <ISO time>] [--message \"<plain text>\"]" >&2
  exit 1
fi
shift

until_time=""
message="The platform is temporarily offline for scheduled system maintenance."

while [[ $# -gt 0 ]]; do
  case "$1" in
    --until)
      until_time="$2"
      shift 2
      ;;
    --message)
      message="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

root="$(cd "$(dirname "$0")/../.." && pwd)"
dir="${MAINTENANCE_DIR:-}"

if [[ -z "$dir" ]]; then
  if [[ -w "/etc/l3mon" ]]; then
    dir="/etc/l3mon"
  else
    dir="$root/deploy/ops/maintenance"
  fi
fi

mkdir -p "$dir"
target="$dir/maintenance"

if [[ "$action" == "off" ]]; then
  rm -f "$target"
  echo "Maintenance mode: OFF"
  exit 0
fi

stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
tmp="$dir/maintenance.tmp.$$"

# Clean plain text: escape quotes and backslashes
clean_msg="$(echo "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')"

cat > "$tmp" <<EOF
{
  "active": true,
  "until": "${until_time}",
  "message": "${clean_msg}",
  "updated_at": "${stamp}"
}
EOF

mv -f "$tmp" "$target"
echo "Maintenance mode: ON"
if [[ -n "$until_time" ]]; then
  echo "  Until:   $until_time"
fi
echo "  Message: $message"
