# Sourced by l3mon-entrypoint and l3mon-run. Reads the platform's secrets from files and exports them as environment
# variables. CTFd cannot read a secret from a file itself; this keeps every secret out of the compose file, out of
# `docker inspect`, and out of the image.
#
#   /run/secrets/<NAME>             one secret per file (Docker secrets)
#   /run/config/preset_configs.json CTFd's PRESET_CONFIGS settings, rendered from the event settings file

for name in SECRET_KEY DATABASE_PASSWORD REDIS_PASSWORD PRESET_ADMIN_PASSWORD PRESET_ADMIN_TOKEN; do
  file="/run/secrets/${name}"
  if [[ -f "$file" ]]; then
    value="$(<"$file")"
    export "${name}=${value}"
  fi
done

if [[ -f /run/config/preset_configs.json ]]; then
  PRESET_CONFIGS="$(<"/run/config/preset_configs.json")"
  export PRESET_CONFIGS
fi

if [[ -z "${SECRET_KEY:-}" ]]; then
  echo "[ ERROR ] SECRET_KEY is not set. Refusing to start: sessions would not survive a restart." >&2
  exit 1
fi
