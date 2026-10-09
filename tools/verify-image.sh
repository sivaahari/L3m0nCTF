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

check "pip reports no broken requirements" docker run --rm --user root --entrypoint sh "$image" -c "/opt/venv/bin/python -m ensurepip --default-pip >/dev/null && /opt/venv/bin/pip check"
check "the plugin is in place" run "$image" test -f /opt/CTFd/CTFd/plugins/l3mon_core/__init__.py
check "the CTFtime feed plugin is in place" run "$image" test -f /opt/CTFd/CTFd/plugins/l3mon_ctftime/__init__.py
check "the release control plugin, its page and its script are in place" run "$image" sh -c 'test -f /opt/CTFd/CTFd/plugins/l3mon_release/__init__.py && test -f /opt/CTFd/CTFd/plugins/l3mon_release/templates/l3mon_release/release.html && test -f /opt/CTFd/CTFd/plugins/l3mon_release/assets/release.js'
check "the theme is in place" run "$image" test -d /opt/CTFd/CTFd/themes/l3mon
check "CTFd imports" run "$image" /opt/venv/bin/python -c "import CTFd"
check "the plugin files are not writable by the running user" run "$image" sh -c '! [ -w /opt/CTFd/CTFd/plugins/l3mon_core/__init__.py ] || [ "$(id -u)" = 0 ]'
check "the release control files are not writable by the running user" run "$image" sh -c '! [ -w /opt/CTFd/CTFd/plugins/l3mon_release/assets/release.js ] || [ "$(id -u)" = 0 ]'

# the upgraded packages really are the ones in the image
while IFS= read -r line; do
  [[ "$line" =~ ^([A-Za-z0-9_.-]+)==([0-9][^[:space:]]*)$ ]] || continue
  name="${BASH_REMATCH[1]}"; want="${BASH_REMATCH[2]}"
  have="$(run "$image" /opt/venv/bin/python -c "import importlib.metadata as m; print(m.version('$name'))" 2>/dev/null | tr -d '\r')"
  [[ "$have" == "$want" ]] && ok "$name $want is installed" || bad "$name is $have, expected $want"
done < "$(dirname "$0")/../docker/ctfd/requirements.overrides.txt"

# the base interpreter (outside the virtual environment) carries the same fixed setuptools
want="$(sed -n 's/^setuptools==//p' "$(dirname "$0")/../docker/ctfd/requirements.overrides.txt")"
have="$(run "$image" /usr/local/bin/python -c "import importlib.metadata as m; print(m.version('setuptools'))" 2>/dev/null | tr -d '\r')"
[[ -n "$want" && "$have" == "$want" ]] && ok "the base interpreter's setuptools is $want" || bad "the base interpreter's setuptools is '$have', expected '$want'"

# no package manager in the image: pip is gone from both interpreters (a throwaway container can bring it back with ensurepip)
check "pip is not in the virtual environment" run "$image" sh -c '! test -e /opt/venv/bin/pip && ! /opt/venv/bin/python -c "import pip"'
check "pip is not in the base interpreter" run "$image" sh -c '! command -v pip && ! /usr/local/bin/python -c "import pip"'

# operating-system fixes the upstream image lacks (Trivy, 2026-10-06)
check "libpcre2-8-0 is at or above 10.42-1+deb12u2" run "$image" sh -c 'dpkg --compare-versions "$(dpkg-query -W -f="\${Version}" libpcre2-8-0)" ge 10.42-1+deb12u2'
check "perl-base is at or above 5.36.0-7+deb12u4" run "$image" sh -c 'dpkg --compare-versions "$(dpkg-query -W -f="\${Version}" perl-base)" ge 5.36.0-7+deb12u4'
check "no package lists left behind" run "$image" sh -c '[ -z "$(ls /var/lib/apt/lists 2>/dev/null | grep -v "^partial$")" ]'

# no build tools, no package manager caches left behind
check "no compiler in the final image" run "$image" sh -c '! command -v gcc'
exit $fail
