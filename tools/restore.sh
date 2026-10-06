#!/usr/bin/env bash
# Restore a backup made by tools/backup.sh into the running stack.
#   tools/restore.sh BACKUP_DIR [--force]
#
# Refuses to run when the database already holds data, unless --force is given (then the database is dropped and
# recreated first). Checks the manifest's checksums before touching anything.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
compose() { "$root/tools/compose.sh" "$@"; }

dir="${1:?usage: tools/restore.sh BACKUP_DIR [--force]}"
force="${2:-}"
[[ -f "$dir/manifest.json" ]] || { echo "No manifest.json in $dir" >&2; exit 1; }

echo "Checking the backup's checksums ..."
for name in db.sql.gz uploads.tar.gz; do
  want="$(grep -o "\"$name\": \"[0-9a-f]*\"" "$dir/manifest.json" | grep -o '[0-9a-f]\{64\}')"
  have="$(sha256sum "$dir/$name" | cut -d' ' -f1)"
  [[ -n "$want" && "$want" == "$have" ]] || { echo "Checksum mismatch for $name. The backup is damaged; nothing was changed." >&2; exit 1; }
done

sql() { compose exec -T db sh -c 'exec mariadb -N -uroot -p"$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" "$@"' sh "$@"; }

tables="$(sql -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ctfd'" | tr -d '\r')"
if [[ "$tables" != "0" ]]; then
  if [[ "$force" != "--force" ]]; then
    echo "The database already holds $tables tables. Re-run with --force to replace it." >&2
    exit 1
  fi
  echo "Replacing the existing database ..."
  sql -e "DROP DATABASE ctfd; CREATE DATABASE ctfd CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
fi

echo "Restoring the database ..."
gzip -dc "$dir/db.sql.gz" | compose exec -T db sh -c 'exec mariadb --default-character-set=utf8mb4 -uroot -p"$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" ctfd'

echo "Restoring the uploaded files ..."
gzip -dc "$dir/uploads.tar.gz" | compose exec -T ctfd sh -c 'cd /var/uploads && exec tar -xf -'

echo "Clearing the cache and restarting the application ..."
compose exec -T cache sh -c 'exec redis-cli --no-auth-warning -a "$(cat /run/secrets/REDIS_PASSWORD)" flushdb' >/dev/null
compose restart ctfd >/dev/null
echo "Restore complete."
