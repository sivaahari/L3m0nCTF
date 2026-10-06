#!/usr/bin/env bash
# Back up the platform: the database, the uploaded files, and a manifest that proves they are intact.
#   tools/backup.sh [OUTPUT_DIR]       (default: backups/<UTC timestamp>)
#
# The backup is NOT encrypted by this script. It holds personal data (emails, team names) and must be encrypted before it
# leaves the machine (SP9 adds off-host encrypted copies). Keep the folder out of git: `backups/` is ignored.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
compose() { "$root/tools/compose.sh" "$@"; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${1:-$root/backups/$stamp}"
mkdir -p "$out"

echo "Backing up the database ..."
compose exec -T db sh -c 'exec mariadb-dump --single-transaction --routines --events --default-character-set=utf8mb4 -uroot -p"$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" ctfd' | gzip -9 > "$out/db.sql.gz"

echo "Backing up the uploaded files ..."
compose exec -T ctfd sh -c 'cd /var/uploads && exec tar -cf - .' | gzip -9 > "$out/uploads.tar.gz"

# a dump that is only a few bytes long means the dump failed quietly
if [[ "$(gzip -dc "$out/db.sql.gz" | head -c 2000 | grep -c 'MariaDB dump')" -lt 1 ]]; then
  echo "The database dump does not look like a dump. Backup failed." >&2
  exit 1
fi

sha() { sha256sum "$1" | cut -d' ' -f1; }
ctfd_image="$(docker inspect --format '{{.Image}}' "$(compose ps -q ctfd)")"
db_version="$(compose exec -T db mariadbd --version | tr -d '\r' | head -1)"

cat > "$out/manifest.json" <<EOF
{
  "format": 1,
  "created_utc": "$stamp",
  "files": {
    "db.sql.gz": "$(sha "$out/db.sql.gz")",
    "uploads.tar.gz": "$(sha "$out/uploads.tar.gz")"
  },
  "ctfd_image": "$ctfd_image",
  "database": "$db_version"
}
EOF
echo "Backup written to $out"
ls -l "$out"
