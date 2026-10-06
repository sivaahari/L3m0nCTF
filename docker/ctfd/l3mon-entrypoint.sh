#!/bin/bash
# Starts CTFd with the platform's secrets in its environment (see l3mon-env.sh), through CTFd's own entrypoint.
set -euo pipefail
# shellcheck source=docker/ctfd/l3mon-env.sh
source /usr/local/lib/l3mon-env.sh
exec /opt/CTFd/docker-entrypoint.sh "$@"
