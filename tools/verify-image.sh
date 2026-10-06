#!/usr/bin/env bash
# Check the properties of the platform image that the rest of the stack relies on.
#   tools/verify-image.sh [IMAGE]      (default: l3mon/ctfd:dev)
# Exits 0 when every check passes, 1 otherwise. Prints one line per check.
set -uo pipefail
export MSYS_NO_PATHCONV=1
image="${1:-l3mon/ctfd:dev}"
fail=0

ok()  { printf '  ok    %s\n' "$1"; }
bad() { printf ' FAIL   %s\n' "$1"; fail=1; }
check() { # description, command...
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi
}
run() { local img="$1" entry="$2"; shift 2; docker run --rm --entrypoint "$entry" "$img" "$@"; }  # run IMAGE PROGRAM ARGS...

echo "Checking $image"

user="$(docker image inspect "$image" --format '{{.Config.User}}')"
[[ "$user" == "1001" ]] && ok "runs as the unprivileged user 1001" || bad "runs as user '$user', expected 1001"

hc="$(docker image inspect "$image" --format '{{json .Config.Healthcheck}}')"
[[ "$hc" == *healthcheck* ]] && ok "has a health check on /healthcheck" || bad "no health check"

check "pip reports no broken requirements" run "$image" /opt/venv/bin/pip check
check "the plugin is in place" run "$image" test -f /opt/CTFd/CTFd/plugins/l3mon_core/__init__.py
check "the theme is in place" run "$image" test -d /opt/CTFd/CTFd/themes/l3mon
check "CTFd imports" run "$image" /opt/venv/bin/python -c "import CTFd"
check "the plugin files are not writable by the running user" run "$image" sh -c '! [ -w /opt/CTFd/CTFd/plugins/l3mon_core/__init__.py ] || [ "$(id -u)" = 0 ]'

# the upgraded packages really are the ones in the image
while IFS= read -r line; do
  [[ "$line" =~ ^([A-Za-z0-9_.-]+)==([0-9][^[:space:]]*)$ ]] || continue
  name="${BASH_REMATCH[1]}"; want="${BASH_REMATCH[2]}"
  have="$(run "$image" /opt/venv/bin/python -c "import importlib.metadata as m; print(m.version('$name'))" 2>/dev/null | tr -d '\r')"
  [[ "$have" == "$want" ]] && ok "$name $want is installed" || bad "$name is $have, expected $want"
done < "$(dirname "$0")/../docker/ctfd/requirements.overrides.txt"

# no build tools, no package manager caches left behind
check "no compiler in the final image" run "$image" sh -c '! command -v gcc'
exit $fail
