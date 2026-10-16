#!/usr/bin/env bash
# Run tools/load_probe.py against the compose stack that is up, from a container on its internal network (so nginx's limit of 100 requests a second for
# one address does not stop the probe), using the image the stack runs for the Python. Arguments are passed on to the probe.
#
#   tools/load_probe_run.sh --players 300 --seconds 60          the event-sized smoke run
#   tools/load_probe_run.sh --poll 3 --flags 60                  five times the polling and three times the flags
#   tools/load_probe_run.sh --cleanup                            remove what an earlier run made (no window change)
#
# Like the integration tests it renders an event window around the present moment into the generated settings and restarts CTFd (the platform takes the
# window from there, and flags are refused before the start), and it puts the settings back at the end. It makes players, challenges and a plan, and it
# replaces the plan that is there. So it checks FIRST, before it changes anything, that the running stack is the development one: the container has the
# development-only administrator token mounted (compose.dev.yml; production has none, on purpose) and trusts "localhost" as a host name. The probe itself
# then refuses an address that is not on this machine, a plan with channels it did not make, and a platform with many other accounts.
# Never point it at a real stack. If this script is killed with SIGKILL it cannot put the settings back: restore deploy/compose/generated/preset_configs.json
# from `python -m l3mon config render` and run `tools/compose.sh up -d --force-recreate --no-deps ctfd`.
set -euo pipefail
cd "$(dirname "$0")/.."
here="$(pwd -W 2>/dev/null || pwd)"
network="${L3MON_NETWORK:-l3mon_internal}"
image="${L3MON_IMAGE:-l3mon/ctfd:dev}"
preset="deploy/compose/generated/preset_configs.json"
die() { echo "load_probe_run: refusing: $*" >&2; exit 2; }
compose() { env -u MSYS_NO_PATHCONV bash tools/compose.sh "$@"; }

[[ "${L3MON_ENV:-dev}" == "dev" ]] || die "L3MON_ENV is ${L3MON_ENV}; the probe runs only against the development stack"
[[ -f .secrets/PRESET_ADMIN_TOKEN ]] || die "there is no development administrator token in .secrets (a real stack has none)"
ctfd_id="$(compose ps -q ctfd | head -n 1)"
[[ -n "$ctfd_id" ]] || die "the stack is not up"
container_env="$(MSYS_NO_PATHCONV=1 docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$ctfd_id")"
grep -q '^TRUSTED_HOSTS=.*,localhost$' <<<"$container_env" || die "the running CTFd does not trust localhost: it is not the development stack"
MSYS_NO_PATHCONV=1 docker inspect --format '{{json .Mounts}}' "$ctfd_id" | grep -q 'PRESET_ADMIN_TOKEN' || die "the running CTFd has no development administrator token mounted: it is not the development stack"

run_probe() {
  MSYS_NO_PATHCONV=1 docker run --rm --network "$network" --entrypoint /opt/venv/bin/python \
    -v "$here/tools/load_probe.py:/probe.py:ro" -v "$here/.secrets/PRESET_ADMIN_TOKEN:/token:ro" \
    "$image" /probe.py --base http://ctfd:8000 --token-file /token "$@"
}

if [[ " $* " == *" --cleanup "* ]]; then run_probe "$@"; exit $?; fi

original="$(mktemp)"
cp "$preset" "$original"
restore() { cp "$original" "$preset"; rm -f "$original"; compose up -d --wait --force-recreate --no-deps ctfd >/dev/null 2>&1 || true; }
trap restore EXIT
python - "$preset" <<'PY'
import json, sys, time
path = sys.argv[1]
window = json.load(open(path, encoding="utf-8"))
now = int(time.time())
window["start"], window["end"] = now - 3600, now + 6 * 3600
open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(window, separators=(",", ":")) + "\n")
PY
compose up -d --wait --force-recreate --no-deps ctfd >/dev/null
run_probe "$@"
