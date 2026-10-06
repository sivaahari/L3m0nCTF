#!/usr/bin/env bash
# Run CTFd's own test suite inside an image, as a throwaway container.
#
#   tools/run-ctfd-tests.sh IMAGE [OVERRIDES_FILE] [-- PYTEST_ARGS...]
#
# IMAGE           the image whose CTFd source and installed packages are tested
# OVERRIDES_FILE  optional pip requirements file applied on top (the dependency upgrades being tried)
# PYTEST_ARGS     passed to pytest (default: the whole suite on two workers)
#
# The test-only packages (pytest and friends) are installed inside the throwaway container and never end up in an image.
set -euo pipefail

image="${1:?usage: run-ctfd-tests.sh IMAGE [OVERRIDES_FILE] [-- PYTEST_ARGS...]}"
shift
overrides=""
if [[ $# -gt 0 && "$1" != "--" ]]; then
  overrides="$1"
  shift
fi
if [[ $# -gt 0 && "$1" == "--" ]]; then shift; fi
pytest_args=("$@")
if [[ ${#pytest_args[@]} -eq 0 ]]; then pytest_args=(-q -n 2 -p no:randomly tests); fi

mounts=()
pip_overrides=""
if [[ -n "$overrides" ]]; then
  mounts+=(-v "$(cd "$(dirname "$overrides")" && pwd -W 2>/dev/null || pwd):/over:ro")
  pip_overrides="/opt/venv/bin/pip install --no-cache-dir -q --upgrade -r /over/$(basename "$overrides") && /opt/venv/bin/pip check &&"
fi

# our own plugin tests are mounted read-only at /l3mon_tests, so `-- -q /l3mon_tests` runs only them
repo_root="$(cd "$(dirname "$0")/.." && (pwd -W 2>/dev/null || pwd))"
if [[ -d "$repo_root/plugins/l3mon_core/tests" ]]; then mounts+=(-v "$repo_root/plugins/l3mon_core/tests:/l3mon_tests:ro"); fi

export MSYS_NO_PATHCONV=1
docker run --rm --user root "${mounts[@]}" --entrypoint bash "$image" -c "
  set -e
  # the platform image has no pip (it is removed on purpose); this throwaway container gets the bundled one back
  /opt/venv/bin/python -m ensurepip --default-pip >/dev/null
  ${pip_overrides}
  /opt/venv/bin/pip install --no-cache-dir -q pytest==8.4.2 pytest-xdist==3.8.0 moto==4.1.11 Faker==4.1.0 psycopg2-binary==2.9.6 coverage==7.10.7
  cd /opt/CTFd
  exec /opt/venv/bin/python -m pytest ${pytest_args[*]}
"
