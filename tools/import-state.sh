#!/usr/bin/env bash
# Bring a platform saved by tools/export-state.sh back on a new machine, exactly as it was.
#   tools/import-state.sh place   STATE_FILE [--force] [--allow-other-commit]
#   tools/import-state.sh restore STATE_FILE
#
# STATE_FILE is the .tar, or the .tar.enc made with --encrypt (the passphrase is asked for, not shown).
#
# `place` (no Docker needed): checks the file's checksums, refuses unless this folder holds the same commit that was running, and puts back the
# secrets, config/event.toml, deploy/compose/generated/, the Cloudflare address list, and the folder outside the repository (default /etc/l3mon:
# production.env, admin-nets.conf, the TLS files) with the same rules as the hosting guide (the TLS files owned by you and group 101, mode 640).
# It refuses to overwrite anything that is already there unless you add --force. It then prints the commands that build and start the platform.
#
# `restore` (the platform must be up and healthy, started with the secrets that `place` put back): replaces the database and the uploaded files
# from the backup inside (tools/restore.sh), refills the story volume, and prints the numbers that prove it (users, studios, uploaded files).
#
# Settings for tests and unusual machines: L3MON_ETC (default /etc/l3mon), L3MON_SUDO (default sudo; empty when you already are root),
# L3MON_TLS_GROUP (default 101, the group the nginx container reads the TLS files with; empty skips the chown), L3MON_NO_MODES=1 (a Windows shell, whose
# files keep no Unix modes: copy without setting them), L3MON_STATE_PASSPHRASE_FILE (a file that holds the passphrase, for a run with nobody at the keyboard).
set -euo pipefail
umask 077
root="$(cd "$(dirname "$0")/.." && pwd)"
# a path such as C:/folder (a Windows shell) would be read by GNU tar as "host:path" without this
tar_help="$(tar --help 2>&1 || true)"; tar_local=(); if [[ "$tar_help" == *--force-local* ]]; then tar_local=(--force-local); fi
tarf() { tar "${tar_local[@]}" "$@"; }
# the file's authenticity: an HMAC-SHA256 over the ciphertext, with a key derived from the passphrase (PBKDF2), kept as the file's last line. The passphrase
# goes in on stdin, never on a command line.
py=""; for candidate in python3 python; do if "$candidate" -c "import hashlib, hmac" >/dev/null 2>&1; then py="$candidate"; break; fi; done   # a Windows shell may hold a store shortcut that only looks like python3
state_mac() {
  "$py" -c '
import hashlib, hmac, sys
key = hashlib.pbkdf2_hmac("sha256", sys.stdin.buffer.read(), b"l3mon-state-mac-v1", 200000)
mac = hmac.new(key, digestmod="sha256")
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        mac.update(chunk)
print(mac.hexdigest())' "$1"
}
etc="${L3MON_ETC:-/etc/l3mon}"
sudo_cmd="${L3MON_SUDO-sudo}"
tls_group="${L3MON_TLS_GROUP-101}"

mode="${1:-}"
file="${2:-}"
if [[ "$mode" != "place" && "$mode" != "restore" ]] || [[ -z "$file" ]]; then
  sed -n '2,/^set -euo/p' "$0" | sed '$d' >&2
  exit 2
fi
shift 2
force=0
other_commit=0
for arg in "$@"; do
  case "$arg" in
    --force) force=1 ;;
    --allow-other-commit) other_commit=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ -f "$file" ]] || { echo "No such file: $file" >&2; exit 1; }

if [[ -f "$etc/production.env" ]]; then set -a; . "$etc/production.env"; set +a; fi
as_root() { if [[ -n "$sudo_cmd" ]]; then "$sudo_cmd" "$@"; else "$@"; fi; }

work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# 1. open the file (decrypt when it ends in .enc), refuse anything that would write outside the work folder
plain="$file"
if [[ "$file" == *.enc ]]; then
  if [[ -n "${L3MON_STATE_PASSPHRASE_FILE:-}" ]]; then pass="$(tr -d '\r\n' < "$L3MON_STATE_PASSPHRASE_FILE")"
  else read -rsp "Passphrase of the file (not shown): " pass || { echo "Could not read a passphrase: run this from a terminal, or set L3MON_STATE_PASSPHRASE_FILE." >&2; exit 1; }; echo; fi
  plain="$work/state.tar"
  [[ -n "$py" ]] || { echo "python3 is needed to check an encrypted file." >&2; exit 1; }
  # the last line is the HMAC (76 bytes: a line break, "L3MON-MAC-", 64 hex digits, a line break); it is checked BEFORE anything is decrypted
  trailer="$(tail -c 76 "$file" | tr -d '\n')"
  if [[ ! "$trailer" =~ ^L3MON-MAC-[0-9a-f]{64}$ ]]; then
    unset pass; echo "The file did not open: it has no authentication line, so it is damaged or not a file made by tools/export-state.sh. Nothing was changed." >&2; exit 1
  fi
  head -c -76 "$file" > "$work/cipher.bin"
  if [[ "$(printf '%s' "$pass" | state_mac "$work/cipher.bin")" != "${trailer#L3MON-MAC-}" ]]; then
    unset pass; echo "The file did not open: the passphrase is wrong, or the file was changed. Nothing was changed." >&2; exit 1
  fi
  if ! printf '%s' "$pass" | openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass stdin -in "$work/cipher.bin" -out "$plain" 2>/dev/null; then
    unset pass; echo "The file did not open: the passphrase is wrong or the file is damaged. Nothing was changed." >&2; exit 1
  fi
  unset pass
else
  echo "Note: this file is not encrypted, so nothing proves who made it. Use only a file you made yourself and kept private." >&2
fi
# Only plain files and folders are allowed in it (no link, no device), and no name may point outside its own folder: the checksums inside prove the
# file is whole, not that it is ours, so what it holds is never trusted beyond that. Read the lists whole first: a pipe into `grep -q` would end tar
# early and, with pipefail, hide the match.
members="$(tarf -tf "$plain")"
kinds="$(tarf -tvf "$plain")"
if grep -Eq '(^/|(^|/)\.\.(/|$))' <<< "$members"; then
  echo "The file lists a path that points outside its own folder. It is refused; nothing was changed." >&2; exit 1
fi
if grep -Eqv '^[-d]' <<< "$kinds"; then
  echo "The file holds something that is not a plain file or folder (a link or a device). It is refused; nothing was changed." >&2; exit 1
fi
tarf -xf "$plain" -C "$work" --no-same-owner
state="$work/state"
[[ -f "$state/STATE.env" && -f "$state/MANIFEST.sha256" ]] || { echo "This is not a file made by tools/export-state.sh (no STATE.env or MANIFEST.sha256). Nothing was changed." >&2; exit 1; }
if ! ( cd "$state" && sha256sum -c MANIFEST.sha256 --quiet ) >/dev/null 2>&1; then
  echo "A checksum inside the file does not match: the file is damaged or was changed. Nothing was changed." >&2; exit 1
fi
# STATE.env is data, never code: it is read line by line (not sourced), only printable characters are kept, and every value is checked before it is used
env_value() { grep -m1 "^$1=" "$state/STATE.env" | cut -d= -f2- | tr -cd '[:print:]' || true; }
want_commit="$(env_value COMMIT)"
if [[ ! "$want_commit" =~ ^([0-9a-f]{40}|unknown)$ ]]; then
  echo "The summary inside the file has no valid commit. Nothing was changed." >&2; exit 1
fi

if [[ "$mode" == "place" ]]; then
  have_commit="$(git -C "$root" rev-parse HEAD 2>/dev/null || echo none)"
  if [[ "$have_commit" != "$want_commit" && "$other_commit" -ne 1 ]]; then
    echo "This folder holds commit ${have_commit:0:12}; the platform that was saved ran commit ${want_commit:0:12} (tag: $(env_value TAG))." >&2
    echo "Check out that release first (git fetch --tags && git checkout $(env_value TAG)), or add --allow-other-commit if you mean to." >&2
    exit 1
  fi
  if [[ "$force" -ne 1 ]]; then
    if [[ -d "$root/.secrets" && -n "$(ls -A "$root/.secrets" 2>/dev/null)" ]]; then echo ".secrets/ is not empty here. Nothing was changed; add --force to replace it." >&2; exit 1; fi
    if [[ -d "$etc" && -n "$(ls -A "$etc" 2>/dev/null)" ]]; then echo "$etc is not empty. Nothing was changed; add --force to replace it." >&2; exit 1; fi
  fi

  echo "Putting back the secrets and the settings ..."
  rm -rf "$root/.secrets"
  cp -a "$state/repo/secrets" "$root/.secrets"
  chmod 700 "$root/.secrets"
  if [[ -f "$state/repo/event.toml" ]]; then mkdir -p "$root/config"; cp -a "$state/repo/event.toml" "$root/config/event.toml"; fi
  mkdir -p "$root/deploy/compose" "$root/deploy/nginx/snippets"
  rm -rf "$root/deploy/compose/generated"
  cp -a "$state/repo/generated" "$root/deploy/compose/generated"
  if [[ -f "$state/repo/cloudflare-realip.conf" ]]; then cp -a "$state/repo/cloudflare-realip.conf" "$root/deploy/nginx/snippets/cloudflare-realip.conf"; fi

  if [[ -n "$(ls -A "$state/etc" 2>/dev/null)" ]]; then
    echo "Putting back $etc ..."
    # inst -d MODE DIR  |  inst MODE SOURCE TARGET.  L3MON_NO_MODES=1 (a Windows shell, whose files keep no Unix modes) copies without setting them.
    inst() {
      if [[ "${L3MON_NO_MODES:-}" == 1 ]]; then
        if [[ "$1" == "-d" ]]; then mkdir -p "$3"; else cp "$2" "$3"; fi
      elif [[ "$1" == "-d" ]]; then as_root install -d -m "$2" "$3"
      else as_root install -m "$1" "$2" "$3"; fi
    }
    inst -d 0755 "$etc"
    for f in "$state/etc"/*; do
      base="$(basename "$f")"
      if [[ "$base" == "tls" ]]; then
        inst -d 0750 "$etc/tls"
        for t in "$f"/*; do
          [[ -e "$t" ]] || continue
          inst 0640 "$t" "$etc/tls/$(basename "$t")"
        done
        if [[ -n "$tls_group" && "${L3MON_NO_MODES:-}" != 1 ]]; then as_root chown "$(id -un)":"$tls_group" "$etc/tls" "$etc/tls"/*; fi
      elif [[ -f "$f" ]]; then
        inst 0644 "$f" "$etc/$base"
      fi
    done
  fi

  echo
  echo "Placed. The same release ($(env_value TAG), ${want_commit:0:12}) is checked out and its secrets and settings are back."
  [[ "$(env_value LOCAL_CHANGES)" == "yes" ]] && echo "NOTE: the machine this came from had local changes to the checked-out files (git/local-changes.patch inside the file). Look at them before going on."
  image="$(env_value L3MON_IMAGE_TAG)"
  echo
  echo "Next, in this order (the same as the hosting guide, parts 5.6 and 5.7):"
  echo "  python3 tools/update_cloudflare_ips.py"
  echo "  docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:${image:-TAG} .  &&  tools/verify-image.sh l3mon/ctfd:${image:-TAG}"
  echo "  set -a; . $etc/production.env; set +a; (cd tools && python3 -m l3mon deploy check)        # must end with 0 blocked"
  echo "  tools/compose.sh up -d --wait"
  echo "  tools/import-state.sh restore $file"
  exit 0
fi

# restore
compose() { "$root/tools/compose.sh" "$@"; }
[[ -z "$(compose ps -q ctfd 2>/dev/null)" ]] && { echo "The platform is not running here. Start it first (tools/compose.sh up -d --wait), then run this again." >&2; exit 1; }
[[ -f "$state/backup/manifest.json" ]] || { echo "The file holds no database backup (it was made with --skip-backup)." >&2; exit 1; }

echo "Replacing the database and the uploaded files ..."
"$root/tools/restore.sh" "$state/backup" --force

story_files="$(env_value STORY_FILES)"
[[ "$story_files" =~ ^[0-9]{1,9}$ ]] || story_files=0   # a number or nothing: [[ -gt ]] would evaluate anything else as an expression
if [[ "$story_files" -gt 0 && -f "$state/story.tar.gz" ]]; then
  echo "Refilling the story volume ($story_files entries) ..."
  volume="$(docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-l3mon}" --filter "label=com.docker.compose.volume=story" | sed -n 1p)"
  [[ -n "$volume" ]] || { echo "No story volume found." >&2; exit 1; }
  image="$(docker inspect --format '{{.Config.Image}}' "$(compose ps -q ctfd | sed -n 1p)")"
  gzip -dc "$state/story.tar.gz" | MSYS_NO_PATHCONV=1 docker run --rm -i --user root -v "$volume":/dest --entrypoint sh "$image" -c 'cd /dest && find . -mindepth 1 -delete && tar -xf -'
fi

count() { compose exec -T db sh -c "MYSQL_PWD=\"\$(cat /run/secrets/DATABASE_ROOT_PASSWORD)\" exec mariadb -N -uroot ctfd -e \"$1\"" | tr -d '[:space:]'; }
echo
echo "Restored. In the database now: users $(count 'select count(*) from users'), studios $(count 'select count(*) from teams'), challenges $(count 'select count(*) from challenges'), uploaded files $(count 'select count(*) from files')."
echo "Everybody has to sign in again (the cache was cleared). Sign-in links, invites and flags made from the saved secrets stay valid."
