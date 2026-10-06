#!/usr/bin/env bash
# Back up the platform: the database, the uploaded files, and a manifest that proves they are intact.
#   tools/backup.sh [OUTPUT_DIR]       (default: backups/<UTC timestamp>)
#
# The backup is NOT encrypted by this script. It holds personal data (emails, team names) AND live credentials (CTFd stores
# every user's API tokens in clear text), so treat it like a password file: it must be encrypted before it leaves the
# machine (SP9 adds off-host encrypted copies). Keep the folder out of git: `backups/` is ignored.
set -euo pipefail
umask 077   # backups hold every user's API token and email address: readable by their owner only
root="$(cd "$(dirname "$0")/.." && pwd)"
compose() { "$root/tools/compose.sh" "$@"; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${1:-$root/backups/$stamp}"
mkdir -p "$out"

echo "Backing up the database ..."
compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb-dump --single-transaction --routines --events --default-character-set=utf8mb4 -uroot ctfd' | gzip -9 > "$out/db.sql.gz"

echo "Backing up the uploaded files ..."
compose exec -T ctfd sh -c 'cd /var/uploads && exec tar -cf - .' | gzip -9 > "$out/uploads.tar.gz"

# a dump that was cut short or failed quietly has no header, or no trailer (mariadb-dump ends a complete one with a comment)
has_header="$(gzip -dc "$out/db.sql.gz" | head -c 2000 | grep -c 'MariaDB dump' || true)"
has_trailer="$(gzip -dc "$out/db.sql.gz" | tail -c 300 | grep -c 'Dump completed' || true)"
if [[ "$has_header" -lt 1 || "$has_trailer" -lt 1 ]]; then
  echo "The database dump does not look complete. Backup failed." >&2
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
