#!/usr/bin/env bash
# Restore a backup made by tools/backup.sh. This REPLACES the whole database and the uploaded files.
#   tools/restore.sh BACKUP_DIR --force
#
# --force is always required: a restore drops the database and loads the backup in its place, and a platform that has just
# started is not empty (CTFd's own tables and the preset administrator exist from the first minute).
# Order: check the manifest's checksums (nothing is touched when they do not match), stop the web side (nginx and CTFd) so
# nobody writes while the database is replaced, restore the database and the uploads, clear the cache, start the web side again.
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
compose() { "$root/tools/compose.sh" "$@"; }

dir="${1:?usage: tools/restore.sh BACKUP_DIR --force}"
force="${2:-}"
[[ -f "$dir/manifest.json" ]] || { echo "No manifest.json in $dir" >&2; exit 1; }
if [[ "$force" != "--force" ]]; then
  echo "A restore replaces the whole database and the uploaded files. Re-run with --force to go ahead." >&2
  exit 1
fi

echo "Checking the backup's checksums ..."
for name in db.sql.gz uploads.tar.gz; do
  want="$(grep -o "\"$name\": \"[0-9a-f]*\"" "$dir/manifest.json" | grep -o '[0-9a-f]\{64\}')"
  have="$(sha256sum "$dir/$name" | cut -d' ' -f1)"
  [[ -n "$want" && "$want" == "$have" ]] || { echo "Checksum mismatch for $name. The backup is damaged; nothing was changed." >&2; exit 1; }
done

echo "Stopping the web side (nginx and CTFd) ..."
compose stop nginx ctfd >/dev/null

echo "Replacing the database ..."
sql() { compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb -N -uroot "$@"' sh "$@"; }
sql -e "DROP DATABASE IF EXISTS ctfd; CREATE DATABASE ctfd CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
gzip -dc "$dir/db.sql.gz" | compose exec -T db sh -c 'MYSQL_PWD="$(cat /run/secrets/DATABASE_ROOT_PASSWORD)" exec mariadb --default-character-set=utf8mb4 -uroot ctfd'

echo "Restoring the uploaded files ..."
# CTFd is stopped, so a one-off container with the same volume does the unpacking (as the same unprivileged user)
gzip -dc "$dir/uploads.tar.gz" | compose run --rm --no-deps -T --entrypoint sh ctfd -c 'cd /var/uploads && find . -mindepth 1 -delete && tar -xf -'

echo "Clearing the cache and starting the web side again ..."
compose exec -T cache sh -c 'REDISCLI_AUTH="$(cat /run/secrets/REDIS_PASSWORD)" exec redis-cli flushdb' >/dev/null
compose up -d --wait ctfd nginx >/dev/null
echo "Restore complete."
