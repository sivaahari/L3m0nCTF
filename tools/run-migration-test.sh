#!/usr/bin/env bash
# Run the tests that need a real MariaDB and a real Redis, in the platform image, against throwaway containers.
#
#   tools/run-migration-test.sh [IMAGE] [-- PYTEST_ARGS...]   IMAGE defaults to l3mon/ctfd:dev (also when only `-- ...` is given); PYTEST_ARGS replace the default three test files
#   L3MON_MOUNT_PLUGINS=1 tools/run-migration-test.sh     test the working-tree plugins instead of the ones baked into the image
#
# What it proves (SQLite, the default test database, cannot): the l3mon_core migrations build the schema the models describe,
# the database refuses what the models refuse, the migrations are safe to run twice and roll back, the tick counter is
# atomic and never expires on a real Redis, and eight workers meeting one scheduled drop show it, announce it and record it once, eight workers revoking or restoring one challenge act once each, a Revoke and a Restore started together end consistent, and a ban corrects a dynamic value in the same commit. The containers and the network are removed at the end, even when a test fails.
set -euo pipefail

if [[ "${1:-}" == "--" ]]; then
  image="l3mon/ctfd:dev"
else
  image="${1:-l3mon/ctfd:dev}"
  shift || true
fi
if [[ "${1:-}" == "--" ]]; then shift; fi
targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then targets=(/l3mon_tests/l3mon_core/test_migration_mariadb.py /l3mon_tests/l3mon_core/test_tick.py /l3mon_tests/l3mon_release/test_scheduler_mariadb.py /l3mon_tests/l3mon_scoring/test_voids_mariadb.py); fi
name="l3mon-realdb-$$"
export MSYS_NO_PATHCONV=1

cleanup() {
  docker rm -f "$name-db" "$name-redis" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$name" >/dev/null
docker run -d --name "$name-db" --network "$name" -e MARIADB_ROOT_PASSWORD=test -e MARIADB_DATABASE=ctfd mariadb:10.11 >/dev/null
docker run -d --name "$name-redis" --network "$name" redis:7-alpine >/dev/null

echo "waiting for MariaDB and Redis..."
for _ in $(seq 1 60); do
  # over TCP, because the image's temporary start-up server answers on the socket only and then goes away
  if docker exec "$name-db" mariadb -uroot -ptest -h127.0.0.1 --protocol=tcp -e 'select 1' >/dev/null 2>&1; then break; fi
  sleep 2
done
docker exec "$name-db" mariadb -uroot -ptest -e 'select version()' >/dev/null
for _ in $(seq 1 30); do
  if [[ "$(docker exec "$name-redis" redis-cli ping 2>/dev/null)" == "PONG" ]]; then break; fi
  sleep 1
done

repo_root="$(cd "$(dirname "$0")/.." && (pwd -W 2>/dev/null || pwd))"
mounts=()
for tests in "$repo_root"/plugins/l3mon_*/tests; do
  [[ -d "$tests" ]] && mounts+=(-v "$tests:/l3mon_tests/$(basename "$(dirname "$tests")"):ro")
done
if [[ "${L3MON_MOUNT_PLUGINS:-}" == "1" ]]; then
  for plugin in "$repo_root"/plugins/l3mon_*; do
    [[ -d "$plugin" ]] && mounts+=(-v "$plugin:/opt/CTFd/CTFd/plugins/$(basename "$plugin"):ro")
  done
fi

docker run --rm --user root --network "$name" "${mounts[@]}" \
  -e TESTING_DATABASE_URL="mysql+pymysql://root:test@$name-db/ctfd_test" \
  -e L3MON_TEST_REDIS_URL="redis://$name-redis:6379/0" \
  --entrypoint bash "$image" -c '
  set -e
  /opt/venv/bin/python -m ensurepip --default-pip >/dev/null
  /opt/venv/bin/pip install --no-cache-dir -q pytest==8.4.2 pytest-xdist==3.8.0 moto==4.1.11 Faker==4.1.0 psycopg2-binary==2.9.6 coverage==7.10.7
  cd /opt/CTFd
  exec /opt/venv/bin/python -m pytest -q -p no:randomly -p no:cacheprovider "$@"
' bash "${targets[@]}"
