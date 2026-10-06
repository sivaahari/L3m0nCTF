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

cd "$root"
exec docker compose --project-directory "$root/deploy/compose" --env-file "$generated/compose.env" "${files[@]}" "$@"
