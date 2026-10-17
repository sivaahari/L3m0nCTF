#!/usr/bin/env bash
# tools/ops/init_exporter_user.sh - Create MariaDB read-only user for mysqld-exporter
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRETS_DIR="${SECRETS_DIR:-$ROOT_DIR/.secrets}"

if [ ! -f "$SECRETS_DIR/DATABASE_ROOT_PASSWORD" ] || [ ! -f "$SECRETS_DIR/DB_EXPORTER_PASSWORD" ]; then
  echo "Error: Required secret files missing from $SECRETS_DIR" >&2
  exit 1
fi

ROOT_PW="$(cat "$SECRETS_DIR/DATABASE_ROOT_PASSWORD")"
EXPORTER_PW="$(cat "$SECRETS_DIR/DB_EXPORTER_PASSWORD")"

echo "Configuring l3mon_exporter read-only user in MariaDB ..."
MSYS_NO_PATHCONV=1 docker exec -i l3mon-db-1 mariadb -u root -p"$ROOT_PW" <<EOSQL
CREATE USER IF NOT EXISTS 'l3mon_exporter'@'%' IDENTIFIED BY '${EXPORTER_PW}';
ALTER USER 'l3mon_exporter'@'%' IDENTIFIED BY '${EXPORTER_PW}';
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'l3mon_exporter'@'%';
FLUSH PRIVILEGES;
EOSQL

echo "[SUCCESS] l3mon_exporter user provisioned."
