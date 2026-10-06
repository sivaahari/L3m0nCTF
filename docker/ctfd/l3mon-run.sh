#!/bin/bash
# Runs any command with the same environment CTFd itself starts with, for administration and for tests:
#   docker compose exec ctfd l3mon-run /opt/venv/bin/python -c "..."
set -euo pipefail
# shellcheck source=docker/ctfd/l3mon-env.sh
source /usr/local/lib/l3mon-env.sh
exec "$@"
