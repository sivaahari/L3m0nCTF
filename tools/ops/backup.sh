#!/usr/bin/env bash
# tools/ops/backup.sh: Off-host encrypted backups with binary logging for L3m0nCTF 2026.
#
# Usage:
#   tools/ops/backup.sh [--full | --binlog] [--sink SINK_URI] [--recipients KEY_FILE] [--heartbeat PROM_FILE]
#
# Sinks supported:
#   file://<path>   (used by tests and local operations)
#   gs://<bucket>   (production off-host sink)
#
# Encrypted with age before leaving the machine.
# Never writes secrets or unencrypted archives to the sink.
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/../.." && pwd)"
compose() { "$root/tools/compose.sh" "$@"; }

mode="full"
sink="${BACKUP_SINK:-file://$root/backups/sink}"
recipients="${RECIPIENTS_FILE:-$root/config/backup/recipients.txt}"
heartbeat="${BACKUP_HEARTBEAT:-$root/backups/backup.prom}"

explicit_recipients=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)
      mode="full"
      shift
      ;;
    --binlog)
      mode="binlog"
      shift
      ;;
    --sink)
      sink="$2"
      shift 2
      ;;
    --recipients)
      recipients="$2"
      explicit_recipients=true
      shift 2
      ;;
    --heartbeat)
      heartbeat="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 [--full|--binlog] [--sink file://...|gs://...] [--recipients <file>] [--heartbeat <prom_file>]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# The recipients file must exist. There is NO fallback to the example file: a backup holds every player's email and API token, and the example key is the
# one printed in age's own documentation, whose private half is printed beside it, so anybody could open a backup made with it (integration audit, 2026-10-18).
if [[ ! -f "$recipients" ]]; then
  echo "Error: recipients file not found: $recipients" >&2
  echo "       Make config/backup/recipients.txt with YOUR public age key (see config/backup/recipients.example.txt for the format). Never use the example key." >&2
  exit 1
fi
if grep -q 'age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p' "$recipients" && [[ "${L3MON_ALLOW_EXAMPLE_BACKUP_KEY:-}" != "1" ]]; then
  echo "Error: $recipients holds the example key from age's documentation, whose private key is public: refusing to encrypt a backup to it." >&2
  echo "       (Only the tests set L3MON_ALLOW_EXAMPLE_BACKUP_KEY=1.)" >&2
  exit 1
fi

# Ensure age is available
if ! command -v age >/dev/null 2>&1; then
  echo "Error: 'age' encryption tool is not in PATH." >&2
  exit 1
fi

start_time="$(date +%s)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work_dir="$(mktemp -d -t l3mon_backup_XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT

ship_file() {
  local src="$1"
  local subfolder="$2"
  local filename
  filename="$(basename "$src")"

  if [[ "$sink" =~ ^file://(.*)$ ]]; then
    local dest_dir="${BASH_REMATCH[1]}/$subfolder"
    mkdir -p "$dest_dir"
    cp "$src" "$dest_dir/$filename"
  elif [[ "$sink" =~ ^gs://(.*)$ ]]; then
    # Production Google Cloud Storage sink
    local target_uri="$sink/$subfolder/$filename"
    if command -v gcloud >/dev/null 2>&1; then
      gcloud storage cp "$src" "$target_uri"
    elif command -v gsutil >/dev/null 2>&1; then
      gsutil cp "$src" "$target_uri"
    else
      echo "Error: neither gcloud nor gsutil found to ship to $sink" >&2
      exit 1
    fi
  else
    echo "Error: unsupported sink scheme: $sink (must be file:// or gs://)" >&2
    exit 1
  fi
}

write_heartbeat() {
  local b_type="$1"
  local b_size="$2"
  local b_duration="$3"
  local b_time="$4"

  local prom_dir
  prom_dir="$(dirname "$heartbeat")"
  mkdir -p "$prom_dir"

  local tmp_prom="${heartbeat}.tmp.$$"
  cat > "$tmp_prom" <<EOF
# HELP l3mon_backup_last_success_timestamp_seconds Timestamp of last successful backup.
# TYPE l3mon_backup_last_success_timestamp_seconds gauge
l3mon_backup_last_success_timestamp_seconds{type="$b_type"} $b_time
# HELP l3mon_backup_last_size_bytes Size of last backup archive in bytes.
# TYPE l3mon_backup_last_size_bytes gauge
l3mon_backup_last_size_bytes{type="$b_type"} $b_size
# HELP l3mon_backup_last_duration_seconds Duration of last backup in seconds.
# TYPE l3mon_backup_last_duration_seconds gauge
l3mon_backup_last_duration_seconds{type="$b_type"} $b_duration
EOF
  mv "$tmp_prom" "$heartbeat"
}

if [[ "$mode" == "full" ]]; then
  echo "Starting full backup ($stamp) ..."
  raw_dir="$work_dir/raw"
  mkdir -p "$raw_dir"

  # 1. Run core tested backup script into raw directory
  "$root/tools/backup.sh" "$raw_dir"

  # Capture current master binlog position
  master_info="$(compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb -N -uroot -e "SHOW MASTER STATUS;"' 2>/dev/null || true)"
  active_binlog="$(echo "$master_info" | awk '{print $1}')"
  active_pos="$(echo "$master_info" | awk '{print $2}')"
  cat > "$raw_dir/binlog_position.json" <<EOF
{
  "file": "${active_binlog:-}",
  "position": "${active_pos:-0}",
  "created_utc": "$stamp"
}
EOF

  # 2. Package into tarball
  tar_file="$work_dir/backup-$stamp.tar"
  tar -cf "$tar_file" -C "$raw_dir" .

  # 3. Encrypt with age using public recipient
  enc_file="$work_dir/backup-$stamp.tar.age"
  age -R "$recipients" -o "$enc_file" "$tar_file"
  sha256sum "$enc_file" | cut -d' ' -f1 > "${enc_file}.sha256"

  # 4. Ship to sink
  ship_file "$enc_file" "full"
  ship_file "${enc_file}.sha256" "full"

  end_time="$(date +%s)"
  duration=$((end_time - start_time))
  size="$(wc -c < "$enc_file" | tr -d ' ')"

  write_heartbeat "full" "$size" "$duration" "$end_time"
  echo "Full backup completed in ${duration}s ($size bytes), shipped to $sink/full"

elif [[ "$mode" == "binlog" ]]; then
  echo "Starting binary log backup ($stamp) ..."
  binlog_dir="$work_dir/binlogs"
  mkdir -p "$binlog_dir"

  # Flush logs inside MariaDB so current transactions are closed into a complete log
  compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb -uroot -e "FLUSH BINARY LOGS;"'

  # Extract binary logs from container
  compose exec -T db sh -c 'cd /var/lib/mysql && tar -cf - mariadb-bin.[0-9]* mariadb-bin.index' > "$work_dir/binlogs-$stamp.tar"

  if [[ ! -s "$work_dir/binlogs-$stamp.tar" ]]; then
    echo "Error: no binary logs captured from database." >&2
    exit 1
  fi

  # Encrypt with age
  enc_file="$work_dir/binlogs-$stamp.tar.age"
  age -R "$recipients" -o "$enc_file" "$work_dir/binlogs-$stamp.tar"
  sha256sum "$enc_file" | cut -d' ' -f1 > "${enc_file}.sha256"

  # Ship to sink
  ship_file "$enc_file" "binlogs"
  ship_file "${enc_file}.sha256" "binlogs"

  end_time="$(date +%s)"
  duration=$((end_time - start_time))
  size="$(wc -c < "$enc_file" | tr -d ' ')"

  write_heartbeat "binlog" "$size" "$duration" "$end_time"
  echo "Binary log backup completed in ${duration}s ($size bytes), shipped to $sink/binlogs"
fi
