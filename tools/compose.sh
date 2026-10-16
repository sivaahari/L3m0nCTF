#!/usr/bin/env bash
# Run docker compose for one environment from the repository root, whatever the current folder is.
#   tools/compose.sh up -d --wait        # development (default)
#   L3MON_ENV=staging tools/compose.sh ps
# The generated settings must exist first: python -m l3mon secrets generate; python -m l3mon config render ...
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
env_name="${L3MON_ENV:-dev}"
generated="$root/deploy/compose/generated"

if [[ ! -f "$generated/compose.env" || ! -f "$root/.secrets/SECRET_KEY" ]]; then
  echo "Missing generated settings. From the tools folder run:" >&2
  echo "  python -m l3mon secrets generate --dir ../.secrets" >&2
  echo "  python -m l3mon config render ../config/event.example.toml --out ../deploy/compose/generated" >&2
  exit 1
fi

files=(-f "$root/deploy/compose/compose.base.yml")
[[ -f "$root/deploy/compose/compose.$env_name.yml" ]] && files+=(-f "$root/deploy/compose/compose.$env_name.yml")

# Extra pieces of the platform that add their own services (the instancer, the operations stack, ...) each ship an overlay file, so none of them edits a
# shared one: L3MON_OVERLAYS=instancer,ops adds deploy/compose/compose.instancer.yml and compose.ops.yml after the environment's own file, in that order.
if [[ "${L3MON_OVERLAYS:-}" == *$'\n'* ]]; then # `read` would stop at the first line and quietly drop the rest
  echo "L3MON_OVERLAYS: a list of names separated by commas, on one line." >&2
  exit 1
fi
IFS=',' read -r -a overlays <<< "${L3MON_OVERLAYS:-}"
for overlay in "${overlays[@]+"${overlays[@]}"}"; do
  [[ -z "$overlay" ]] && continue
  if [[ ! "$overlay" =~ ^[a-z][a-z0-9-]{0,30}$ || "$overlay" == "base" || "$overlay" == "dev" || "$overlay" == "staging" || "$overlay" == "production" ]]; then
    echo "L3MON_OVERLAYS: '$overlay' is not an overlay name (a lowercase word of letters, digits and dashes, and not base, dev, staging or production)." >&2
    exit 1
  fi
  if [[ ! -f "$root/deploy/compose/compose.$overlay.yml" ]]; then
    echo "L3MON_OVERLAYS: there is no overlay '$overlay' (deploy/compose/compose.$overlay.yml)." >&2
    exit 1
  fi
  files+=(-f "$root/deploy/compose/compose.$overlay.yml")
done

cd "$root"
exec docker compose --project-directory "$root/deploy/compose" --env-file "$generated/compose.env" "${files[@]}" "$@"
