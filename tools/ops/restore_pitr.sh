#!/usr/bin/env bash
# tools/ops/restore_pitr.sh: Point-in-time restore from off-host encrypted backups and binary logs.
#
# Usage:
#   tools/ops/restore_pitr.sh --sink SINK_URI --key PRIVATE_KEY_FILE [--target-time ISO_OR_DATETIME] --force
#
# Examples:
#   tools/ops/restore_pitr.sh --sink file://backups/sink --key .secrets/age.key --force
#   tools/ops/restore_pitr.sh --sink file://backups/sink --key .secrets/age.key --target-time "2026-10-17 18:30:00" --force
#
# Replaces the entire database and uploads, replays binlogs up to target-time, clears Redis, and restarts web services.
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/../.." && pwd)"
compose() { "$root/tools/compose.sh" "$@"; }

sink=""
key_file=""
target_time=""
force=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sink)
      sink="$2"
      shift 2
      ;;
    --key)
      key_file="$2"
      shift 2
      ;;
    --target-time)
      target_time="$2"
      shift 2
      ;;
    --force)
      force="--force"
      shift
      ;;
    -h|--help)
      echo "Usage: $0 --sink <SINK_URI> --key <KEY_FILE> [--target-time <DATETIME>] --force"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$force" != "--force" ]]; then
  echo "Error: PITR restore drops and replaces the entire database and uploads." >&2
  echo "Re-run with --force to proceed." >&2
  exit 1
fi

if [[ -z "$sink" ]]; then
  echo "Error: --sink is required." >&2
  exit 1
fi

if [[ -z "$key_file" || ! -f "$key_file" ]]; then
  echo "Error: valid private key file is required with --key." >&2
  exit 1
fi

if ! command -v age >/dev/null 2>&1; then
  echo "Error: 'age' tool is required for decryption." >&2
  exit 1
fi

start_time="$(date +%s)"
work_dir="$(mktemp -d -t l3mon_restore_XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT

fetch_files() {
  local subfolder="$1"
  local dest_local="$2"
  mkdir -p "$dest_local"

  if [[ "$sink" =~ ^file://(.*)$ ]]; then
    local src_dir="${BASH_REMATCH[1]}/$subfolder"
    if [[ -d "$src_dir" ]]; then
      cp "$src_dir"/* "$dest_local/" 2>/dev/null || true
    fi
  elif [[ "$sink" =~ ^gs://(.*)$ ]]; then
    if command -v gcloud >/dev/null 2>&1; then
      gcloud storage cp "$sink/$subfolder/*" "$dest_local/" 2>/dev/null || true
    elif command -v gsutil >/dev/null 2>&1; then
      gsutil cp "$sink/$subfolder/*" "$dest_local/" 2>/dev/null || true
    fi
  else
    echo "Error: unsupported sink: $sink" >&2
    exit 1
  fi
}

echo "Retrieving backup archives from $sink ..."
full_pool="$work_dir/sink_full"
binlog_pool="$work_dir/sink_binlogs"
fetch_files "full" "$full_pool"
fetch_files "binlogs" "$binlog_pool"

# Find available full backups
# Format: backup-YYYYMMDDTHHMMSSZ.tar.age
full_backups=( $(ls -1 "$full_pool"/backup-*.tar.age 2>/dev/null | sort || true) )
if [[ ${#full_backups[@]} -eq 0 ]]; then
  echo "Error: no full backup archives found in $sink/full" >&2
  exit 1
fi

# Pick newest full backup that is <= target_time if specified
chosen_full=""
if [[ -z "$target_time" ]]; then
  chosen_full="${full_backups[-1]}"
else
  # Normalize target timestamp
  target_stamp="$(date -u -d "$target_time" +%Y%m%dT%H%M%SZ 2>/dev/null || echo "$target_time")"
  for b in "${full_backups[@]}"; do
    b_name="$(basename "$b")"
    b_stamp="${b_name#backup-}"
    b_stamp="${b_stamp%.tar.age}"
    if [[ "$b_stamp" < "$target_stamp" || "$b_stamp" == "$target_stamp" ]]; then
      chosen_full="$b"
    fi
  done
  if [[ -z "$chosen_full" ]]; then
    echo "Notice: no full backup older than target time; using earliest available full backup."
    chosen_full="${full_backups[0]}"
  fi
fi

echo "Selected base backup: $(basename "$chosen_full")"
chosen_name="$(basename "$chosen_full")"
base_stamp="${chosen_name#backup-}"
base_stamp="${base_stamp%.tar.age}"

# Decrypt full backup
echo "Decrypting base backup ..."
base_tar="$work_dir/base.tar"
age -d -i "$key_file" -o "$base_tar" "$chosen_full"

unpacked_base="$work_dir/unpacked_base"
mkdir -p "$unpacked_base"
tar -xf "$base_tar" -C "$unpacked_base"

# Verify manifest checksums
if [[ ! -f "$unpacked_base/manifest.json" ]]; then
  echo "Error: base backup missing manifest.json" >&2
  exit 1
fi

for name in db.sql.gz uploads.tar.gz; do
  want="$(grep -o "\"$name\": \"[0-9a-f]*\"" "$unpacked_base/manifest.json" | grep -o '[0-9a-f]\{64\}')"
  have="$(sha256sum "$unpacked_base/$name" | cut -d' ' -f1)"
  if [[ -z "$want" || "$want" != "$have" ]]; then
    echo "Error: Checksum mismatch for $name in base backup. Aborting." >&2
    exit 1
  fi
done

echo "Stopping web services (nginx, ctfd) ..."
compose stop nginx ctfd >/dev/null

echo "Restoring base database ..."
sql() { compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb -N -uroot "$@"' sh "$@"; }
sql -e "DROP DATABASE IF EXISTS ctfd; CREATE DATABASE ctfd CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
gzip -dc "$unpacked_base/db.sql.gz" | compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb --default-character-set=utf8mb4 -uroot ctfd'

# Replay binary logs
binlog_archives=( $(ls -1 "$binlog_pool"/binlogs-*.tar.age 2>/dev/null | sort || true) )
binlogs_dir="$work_dir/binlog_files"
mkdir -p "$binlogs_dir"

replayed_count=0
if [[ ${#binlog_archives[@]} -gt 0 ]]; then
  echo "Decrypting and replaying binary logs ..."
  for archive in "${binlog_archives[@]}"; do
    arc_name="$(basename "$archive")"
    arc_stamp="${arc_name#binlogs-}"
    arc_stamp="${arc_stamp%.tar.age}"

    # Only process binlog archives recorded after base backup
    if [[ "$arc_stamp" > "$base_stamp" || "$arc_stamp" == "$base_stamp" ]]; then
      temp_tar="$work_dir/temp_binlog.tar"
      age -d -i "$key_file" -o "$temp_tar" "$archive"
      tar -xf "$temp_tar" -C "$binlogs_dir"
      rm -f "$temp_tar"
      replayed_count=$((replayed_count + 1))
    fi
  done

  # Check if we have extracted binlog files
  binlog_files=( $(ls -1 "$binlogs_dir"/mariadb-bin.[0-9]* 2>/dev/null | sort || true) )
  if [[ ${#binlog_files[@]} -gt 0 ]]; then
    base_binlog_file=""
    base_binlog_pos=""
    if [[ -f "$unpacked_base/binlog_position.json" ]]; then
      base_binlog_file="$(grep -o '"file": "[^"]*"' "$unpacked_base/binlog_position.json" | cut -d'"' -f4 || true)"
      base_binlog_pos="$(grep -o '"position": "[^"]*"' "$unpacked_base/binlog_position.json" | cut -d'"' -f4 || true)"
    fi

    # Discard any binlog files strictly older than base_binlog_file
    for f in "${binlog_files[@]}"; do
      fname="$(basename "$f")"
      if [[ -n "$base_binlog_file" && "$fname" < "$base_binlog_file" ]]; then
        rm -f "$f"
      fi
    done

    # Re-evaluate remaining files to replay
    remaining_files=( $(ls -1 "$binlogs_dir"/mariadb-bin.[0-9]* 2>/dev/null | sort || true) )
    if [[ ${#remaining_files[@]} -gt 0 ]]; then
      echo "Replaying ${#remaining_files[@]} binary log files into database ..."
      stop_arg=""
      start_arg=""
      if [[ -n "$target_time" ]]; then
        formatted_datetime="$(date -u -d "$target_time" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$target_time")"
        stop_arg="--stop-datetime=\"$formatted_datetime\""
      fi

      first_file="$(basename "${remaining_files[0]}")"
      if [[ -n "$base_binlog_file" && "$first_file" == "$base_binlog_file" && -n "$base_binlog_pos" && "$base_binlog_pos" -gt 0 ]]; then
        start_arg="--start-position=$base_binlog_pos"
      fi

      # Stream binlogs into container tmpfs and run mariadb-binlog piped to mariadb --force
      tar -cf - -C "$binlogs_dir" . | compose exec -T db sh -c '
        set -e
        mkdir -p /tmp/pitr_binlogs
        cd /tmp/pitr_binlogs
        tar -xf -
        MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" mariadb-binlog --no-defaults --database=ctfd '"$start_arg"' '"$stop_arg"' mariadb-bin.[0-9]* | \
        MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" mariadb --force --default-character-set=utf8mb4 -uroot ctfd
        rm -rf /tmp/pitr_binlogs
      '
      echo "Binary logs successfully replayed."
    else
      echo "No binary logs newer than base dump to replay."
    fi
  fi
fi

echo "Restoring uploaded files ..."
gzip -dc "$unpacked_base/uploads.tar.gz" | compose run --rm --no-deps -T --entrypoint sh ctfd -c 'cd /var/uploads && find . -mindepth 1 -delete && tar -xf -'

echo "Flushing Redis cache and restarting web services ..."
compose exec -T cache sh -c 'REDISCLI_AUTH="$(cat /run/secrets/REDIS_PASSWORD)" exec redis-cli flushdb' >/dev/null
compose up -d --wait ctfd nginx >/dev/null

end_time="$(date +%s)"
elapsed=$((end_time - start_time))
echo "PITR restore completed successfully in ${elapsed}s."
